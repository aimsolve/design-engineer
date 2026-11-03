#!/usr/bin/env python3
"""Real-browser check of part notes + voice dictation on an assembly site (the platform's model page), and that the pencil notes are unchanged.

Builds a fresh site from importer output with THIS template (build.py, default --feedback auto), serves it under /<project>/ like the stack does,
and intercepts the two endpoints the page talks to (no router, no messenger login, no speech recogniser):
  POST /feedback/<project>  JSON {comment, image: JPEG data URL, view: {..., part, node}}     (card note; pencil note has view.parts, shapes, camera)
  POST /stt/<project>       raw audio body, Content-Type audio/webm or audio/mp4 -> {"text"}
The microphone is faked (getUserMedia + MediaRecorder replaced by an init script that yields known bytes).

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_part_notes.py \
      [--in <importer output>] [--part <part id>] [--swiftshader] [--shots <dir>]
(first run: `... --with playwright playwright install chromium`)

The model: --in, else $VIEWER_TEST_MODEL, else the demo machine (examples/nema-bracket, built once in a temp dir); the part clicked in
3D: --part, else the biggest distinct part a click can reach on screen. The page is in its default language (ru); every UI text the test looks for comes from i18n.mjs.

UI the test relies on (from the spec): the part card is #info; its note field is a <textarea> inside #info with a placeholder;
its send button is a button named fb.send inside #info; the pencil popup is #fbPop (textarea + fb.send, fb.cancel);
the mic button in either place is a <button> whose accessible name (aria-label / title / text) is fb.mic (fb.micStop while recording).
Exit code 1 on any failed check.
"""
import argparse, base64, functools, json, re, subprocess, sys, tempfile, threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import model_dir, parts_of, serve_stack, ui_texts  # noqa: E402
ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="src", default=None, help="importer output dir (model.glb, parts.json, parts/); default: $VIEWER_TEST_MODEL, else the demo machine")
ap.add_argument("--part", default=None, help="a part id to click in 3D (default: the biggest distinct part that is on screen)")
ap.add_argument("--project", default="demo-proj", help="project id = first path segment (the /feedback/<id> and /stt/<id> endpoints)")
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="notes_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
rows, ok_all = [], True
F = ui_texts("ru")["fb"]
MIC = re.compile("|".join(re.escape(F[k]) for k in ("mic", "micStop")), re.I)
SEND = re.compile(r"^\s*" + re.escape(F["send"]) + r"\s*$")
STT_TEXT = "make the wall thicker"
LOGIN_ERROR = "log in to send a note"        # what the fake router answers on 401: the page shows the router's error as it is
AUDIO = [0x1A, 0x45, 0xDF, 0xA3] + [ord(c) for c in "fake-opus-audio"] * 64     # what the fake recorder "records"


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- site: build with this template, serve under /<project>/
src = model_dir(a.src)
# tried in this order on the first page until a click reaches one (a big part may sit inside a housing); later pages click that one
CANDIDATES = [a.part] if a.part else [p["id"] for p in sorted((p for p in parts_of(src) if not p.get("part_id")),
                                                              key=lambda p: -(p.get("volume_mm3") or 0))][:8]
root = Path(tempfile.mkdtemp(prefix="notes_site_"))
subprocess.run([sys.executable, str(HERE / "build.py"), "--in", str(src), "--out", str(root / a.project), "--flash", "none"], check=True, capture_output=True)   # flash is on by default: its router calls would 404 here
srv, _ = serve_stack(root)   # the stack's rules: /_viewer/ = this template, /<id>/ = the site with the shared index.html
URL = f"http://127.0.0.1:{srv.server_port}/{a.project}/?app=0"

# ---------------------------------------------------------------- fake microphone
FAKE_MIC = """
(() => {
  const TYPES = window.__MIC_TYPES || ["audio/webm"];
  window.__mic = { gum: 0, started: 0, stopped: 0 };
  if (!navigator.mediaDevices) Object.defineProperty(navigator, "mediaDevices", { value: {}, configurable: true });
  navigator.mediaDevices.getUserMedia = async () => { window.__mic.gum++; return new MediaStream(); };
  class FakeRecorder extends EventTarget {
    constructor(stream, opts) { super(); this.stream = stream; const want = opts && opts.mimeType;
      this.mimeType = want && FakeRecorder.isTypeSupported(want) ? want : TYPES[0]; this.state = "inactive"; }
    static isTypeSupported(t) { return TYPES.some((x) => String(t).startsWith(x)); }
    _fire(n, e) { this.dispatchEvent(e); const h = this["on" + n]; if (h) h.call(this, e); }
    start() { this.state = "recording"; window.__mic.started++; this._fire("start", new Event("start")); }
    requestData() {} pause() {} resume() {}
    stop() {
      if (this.state === "inactive") return;
      this.state = "inactive"; window.__mic.stopped++;
      const e = new Event("dataavailable"); e.data = new Blob([new Uint8Array(__AUDIO__)], { type: this.mimeType });
      this._fire("dataavailable", e);
      setTimeout(() => this._fire("stop", new Event("stop")), 0);
    }
  }
  window.MediaRecorder = FakeRecorder;
})();
""".replace("__AUDIO__", json.dumps(AUDIO))


def new_page(b, mic_types=None):
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    if mic_types:
        ctx.add_init_script(f"window.__MIC_TYPES = {json.dumps(mic_types)};")
    ctx.add_init_script(FAKE_MIC)
    page = ctx.new_page()
    errs, fb, stt = [], [], []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))

    def on_fb(route):
        fb.append({"url": route.request.url, "method": route.request.method, "body": json.loads(route.request.post_data or "{}")})
        st = page.__dict__.get("_fb_status", 200)
        route.fulfill(status=st, content_type="application/json",
                      body=json.dumps({"ok": True} if st == 200 else {"error": LOGIN_ERROR}, ensure_ascii=False))

    def on_stt(route):
        stt.append({"url": route.request.url, "method": route.request.method, "ctype": route.request.headers.get("content-type", ""),
                    "body": route.request.post_data_buffer or b""})
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"text": STT_TEXT}, ensure_ascii=False))

    page.route("**/feedback/**", on_fb)
    page.route("**/stt/**", on_stt)
    page.route("**/kit/**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"enabled": false}'))   # the stack's router: no kit.json here
    page.route("**/model.json*", lambda route: route.fulfill(status=200, content_type="application/json",   # the router's model version poll
                                                             body='{"v": 0, "building": false}'))
    page.goto(URL, wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    page.wait_for_function("window.__viewer.feedback || window.__viewer.feedbackError", timeout=30000)
    return ctx, page, errs, fb, stt


def click_part_in_3d(page, part):
    """Real mouse click on a visible instance of `part` (scan for a pixel that hits it, like test_site.py)."""
    page.evaluate("window.__viewer.select(null)")
    page.evaluate(f"window.__viewer.select({json.dumps(part)}, true)")
    page.wait_for_timeout(400)
    page.wait_for_function("!window.__viewer.state().flying", timeout=30000)
    page.evaluate("window.__viewer.select(null)")
    page.wait_for_timeout(200)
    hit = page.evaluate("""(id) => { const v = window.__viewer;
      for (let y = 20; y < innerHeight - 20; y += 12) for (let x = 360; x < innerWidth - 380; x += 12) { const r = v.pick(x, y); if (r && r.part === id) return [x, y]; }
      return null; }""", part)
    if not hit:
        return False
    page.mouse.click(*hit)
    page.wait_for_timeout(400)
    sel = page.evaluate("window.__viewer.state().selected")
    return bool(sel and sel.get("part") == part)


def card_field(page):
    return page.locator("#info textarea")


def card_send(page):
    return page.locator("#info").get_by_role("button", name=SEND)


def mic_in(page, scope):
    return page.locator(scope).get_by_role("button", name=MIC)


def press_twice(m, page, gap=300):
    """Record -> stop on the SAME button (its label may change to «stop» while recording, so hold the element, not the name)."""
    h = m.element_handle()
    h.click(); page.wait_for_timeout(gap); h.click()


def jpeg_ok(data_url):
    if not isinstance(data_url, str) or not data_url.startswith("data:image/jpeg;base64,"):
        return False
    return base64.b64decode(data_url.split(",", 1)[1])[:2] == b"\xff\xd8"


def run(p):
    b = p.chromium.launch(args=GPU_ARGS)

    # ---- 1 + 2: click a part in 3D -> card shows the note field; empty -> Send disabled; Send -> one POST with view.part/node
    ctx, page, errs, fb, stt = new_page(b)
    check("model loads", not page.evaluate("window.__viewer.error"), str(page.evaluate("window.__viewer.error") or ""))
    a.part = next((pid for pid in CANDIDATES if click_part_in_3d(page, pid)), None)
    clicked = check("click on a part selects it", a.part is not None, f"{a.part} (tried {', '.join(CANDIDATES)})")
    if not clicked:
        ctx.close(); b.close()
        return
    field_n = card_field(page).count()
    check("[1] part card has a note field", field_n == 1 and card_field(page).is_visible(), f"{field_n} textarea(s) in #info")
    if field_n == 1:
        ph = card_field(page).get_attribute("placeholder") or ""
        check("[1] the field asks what to change", len(ph.strip()) > 0, ph)
        check("[1] part card has a Send button", card_send(page).count() == 1)
        check("[2] empty text -> Send disabled", card_send(page).count() == 1 and card_send(page).is_disabled())
        card_field(page).fill("   ")
        check("[2] whitespace-only text -> Send still disabled", card_send(page).count() == 1 and card_send(page).is_disabled())
        text = "make this leg 5 mm longer"
        card_field(page).fill(text)
        check("[2] text typed -> Send enabled", card_send(page).count() == 1 and card_send(page).is_enabled())
        page.screenshot(path=str(shots / "1_card_field.png"))
        if card_send(page).count() == 1:
            card_send(page).click()
            page.wait_for_timeout(1500)
        sel = page.evaluate("window.__viewer.state().selected") or {}
        check("[2] Send -> exactly one POST", len(fb) == 1, f"{len(fb)} POST(s)")
        if fb:
            r, body = fb[0], fb[0]["body"]
            v = body.get("view") or {}
            check("[2] POST goes to /feedback/<project>", r["method"] == "POST" and r["url"].split("?")[0].endswith(f"/feedback/{a.project}"), r["url"])
            check("[2] comment is the typed text", body.get("comment") == text, repr(body.get("comment")))
            check("[2] image is a JPEG data URL", jpeg_ok(body.get("image")), str(body.get("image"))[:40])
            check("[2] view.part = the clicked part id", v.get("part") == a.part, repr(v.get("part")))
            check("[2] view.node = the part's node", v.get("node") not in (None, "", []), repr(v.get("node")))
            check("[2] usual view fields kept (url, hash)", bool(v.get("url")) and "hash" in v, json.dumps({k: v.get(k) for k in ("url", "hash")})[:120])
            check("[2] the part stays selected while the frame is taken", sel.get("part") == a.part, json.dumps(sel, ensure_ascii=False)[:120])
    check("no /stt call without the mic", not stt)
    check("console has no errors (card flow)", not errs, "; ".join(errs[:3]))
    ctx.close()

    # ---- 1 + 2: pick a part in the left tree -> same field; Send carries that part
    ctx, page, errs, fb, stt = new_page(b)
    if click_part_in_3d(page, a.part):
        n2 = page.evaluate("""(id) => { const v = window.__viewer;
          for (const el of document.querySelectorAll('#tree .tr[data-n]:not([data-grp])')) {
            const o = v.nodes[+el.dataset.n]; if (o && o.isMesh && o.userData.part && o.userData.part !== id) return { n: +el.dataset.n, part: o.userData.part }; }
          return null; }""", a.part)
        if check("tree shows another part row next to the selected one", n2 is not None):
            page.click(f'#tree .tr[data-n="{n2["n"]}"] .lbl')
            page.wait_for_timeout(400)
            sel = page.evaluate("window.__viewer.state().selected") or {}
            check("tree click selects that part", sel.get("part") == n2["part"], json.dumps(sel, ensure_ascii=False)[:120])
            if check("[1] tree pick -> card has the note field", card_field(page).count() == 1 and card_field(page).is_visible()):
                card_field(page).fill("change the material to stainless steel")
                if card_send(page).count() == 1:
                    card_send(page).click(); page.wait_for_timeout(1500)
                check("[2] tree pick: one POST with view.part = the tree part", len(fb) == 1 and (fb[0]["body"].get("view") or {}).get("part") == n2["part"],
                      json.dumps([(x["body"].get("view") or {}).get("part") for x in fb]))
    ctx.close()

    # ---- 2: router says 401 (not logged in) -> the note is not lost
    ctx, page, errs, fb, stt = new_page(b)
    page.__dict__["_fb_status"] = 401
    if click_part_in_3d(page, a.part) and card_field(page).count() == 1:
        card_field(page).fill("a text that must not get lost")
        card_send(page).click(); page.wait_for_timeout(1500)
        check("[2] 401 -> the typed text stays in the field", card_field(page).input_value() == "a text that must not get lost", repr(card_field(page).input_value()))
        check("[2] 401 -> the router's error is shown in the card", LOGIN_ERROR in page.inner_text("#info").lower(), page.inner_text("#info").replace("\n", " | ")[-160:])
    else:
        check("[2] 401 case reached the card field", False)
    ctx.close()

    # ---- 3: mic in the card: record -> stop -> recognised text appended, editable, nothing sent until Send
    ctx, page, errs, fb, stt = new_page(b)
    if click_part_in_3d(page, a.part) and card_field(page).count() == 1:
        card_field(page).fill("Leg:")
        m = mic_in(page, "#info")
        if check("[3] card has a mic button", m.count() == 1 and m.is_visible(), f"{m.count()} found"):
            h = m.element_handle()
            h.click(); page.wait_for_timeout(300)
            mic = page.evaluate("window.__mic")
            check("[3] mic press asks for the microphone and starts recording", mic["gum"] == 1 and mic["started"] == 1, json.dumps(mic))
            check("[3] nothing goes to /stt while still recording", not stt)
            h.click()
            try:
                page.wait_for_function(f"document.querySelector('#info textarea').value.includes({json.dumps(STT_TEXT)})", timeout=10000)
            except Exception:  # noqa
                pass
            check("[3] second press stops recording", page.evaluate("window.__mic.stopped") == 1)
            check("[3] audio POSTed once to /stt/<project>", len(stt) == 1 and stt[0]["method"] == "POST" and stt[0]["url"].split("?")[0].endswith(f"/stt/{a.project}"),
                  json.dumps([s["url"] for s in stt]))
            if stt:
                check("[3] raw body = the recorded audio, Content-Type audio/webm", stt[0]["body"] == bytes(AUDIO) and stt[0]["ctype"].startswith("audio/webm"),
                      f"{len(stt[0]['body'])} bytes, {stt[0]['ctype']}")
            val = card_field(page).input_value()
            check("[3] recognised text is appended after what was typed", val.startswith("Leg:") and STT_TEXT in val, repr(val))
            check("[3] nothing sent to /feedback by dictation alone", not fb, f"{len(fb)} POST(s)")
            card_field(page).fill(val + " and paint it")
            check("[3] field stays editable after dictation", card_field(page).input_value().endswith(" and paint it"))
            page.screenshot(path=str(shots / "3_card_mic.png"))
            card_send(page).click(); page.wait_for_timeout(1500)
            check("[3] Send posts the dictated + edited text", len(fb) == 1 and STT_TEXT in fb[0]["body"].get("comment", "") and fb[0]["body"]["comment"].endswith("and paint it"),
                  repr(fb[0]["body"].get("comment") if fb else None))
    else:
        check("[3] mic case reached the card field", False)
    ctx.close()

    # ---- 3: Safari-like browser (MediaRecorder only does audio/mp4) -> POST with Content-Type audio/mp4
    ctx, page, errs, fb, stt = new_page(b, mic_types=["audio/mp4"])
    if click_part_in_3d(page, a.part) and mic_in(page, "#info").count() == 1:
        m = mic_in(page, "#info")
        press_twice(m, page); page.wait_for_timeout(1500)
        check("[3] mp4-only recorder -> /stt gets Content-Type audio/mp4", len(stt) == 1 and stt[0]["ctype"].startswith("audio/mp4"), json.dumps([s["ctype"] for s in stt]))
        check("[3] mp4: recognised text lands in the field", STT_TEXT in card_field(page).input_value())
    else:
        check("[3] mp4 case reached the card mic", False)
    ctx.close()

    # ---- review: text about one part never goes out labelled as another; cancelling a note while recording sends nothing
    ctx, page, errs, fb, stt = new_page(b)
    if click_part_in_3d(page, a.part):
        card_field(page).fill("this is about the first part")
        other = page.evaluate("""(id) => { const v = window.__viewer; const r = v.parts().find(([pid]) => pid !== id); return r ? r[0] : null; }""", a.part)
        page.evaluate(f"window.__viewer.select({json.dumps(other)})"); page.wait_for_timeout(300)
        check("[review] another part selected -> the box starts empty", card_field(page).input_value() == "", repr(card_field(page).input_value()))
    page.evaluate("window.__viewer.select(null)")
    page.evaluate("window.__viewer.feedback.open()"); page.evaluate("window.__viewer.feedback.tool('rect')"); page.wait_for_timeout(200)
    W, H = page.viewport_size["width"], page.viewport_size["height"]
    page.mouse.move(W * 0.4, H * 0.35); page.mouse.down(); page.mouse.move(W * 0.6, H * 0.6, steps=8); page.mouse.up(); page.wait_for_timeout(300)
    pm = mic_in(page, "#fbPop")
    if pm.count() == 1:
        pm.click(); page.wait_for_timeout(300)
        page.locator("#fbPop").get_by_role("button", name=F["cancel"]).click(); page.wait_for_timeout(800)
        check("[review] cancel while recording -> recorder stopped, nothing sent to /stt", page.evaluate("window.__mic.stopped") >= 1 and not stt,
              f"stopped={page.evaluate('window.__mic.stopped')}, stt={len(stt)}")
    else:
        check("[review] cancel-while-recording case reached the popup mic", False)
    ctx.close()

    # ---- 3 + 4: pencil note: draw -> popup (with a mic) -> dictate -> send = JPEG + view.parts, as before
    ctx, page, errs, fb, stt = new_page(b)
    page.evaluate("window.__viewer.select(null)")
    page.evaluate("window.__viewer.feedback.open()")
    page.evaluate("window.__viewer.feedback.tool('rect')")
    page.wait_for_timeout(200)
    W, H = page.viewport_size["width"], page.viewport_size["height"]
    page.mouse.move(W * 0.4, H * 0.35); page.mouse.down(); page.mouse.move(W * 0.55, H * 0.5, steps=8); page.mouse.move(W * 0.6, H * 0.6, steps=8); page.mouse.up()
    page.wait_for_timeout(400)
    pop_open = page.evaluate("window.__viewer.feedback.state().popup")
    check("[4] drawing a rectangle opens the pencil popup", pop_open and page.locator("#fbPop").is_visible())
    pm = mic_in(page, "#fbPop")
    if check("[3] pencil popup has a mic button", pm.count() == 1 and pm.is_visible(), f"{pm.count()} found"):
        page.fill("#fbPop textarea", "a gap here")
        press_twice(pm, page)
        try:
            page.wait_for_function(f"document.querySelector('#fbPop textarea').value.includes({json.dumps(STT_TEXT)})", timeout=10000)
        except Exception:  # noqa
            pass
        v = page.input_value("#fbPop textarea")
        check("[3] popup: recognised text appended, nothing sent yet", v.startswith("a gap here") and STT_TEXT in v and not fb, f"{v!r}, {len(fb)} POST(s)")
        check("[3] popup: audio went to /stt/<project>", len(stt) == 1 and stt[0]["url"].split("?")[0].endswith(f"/stt/{a.project}") and stt[0]["body"] == bytes(AUDIO))
    else:
        page.fill("#fbPop textarea", "a gap here")
    page.screenshot(path=str(shots / "4_pencil_popup.png"))
    page.locator("#fbPop").get_by_role("button", name=SEND).click()
    page.wait_for_timeout(1500)
    check("[4] pencil Send -> exactly one POST to /feedback/<project>", len(fb) == 1 and fb[0]["url"].split("?")[0].endswith(f"/feedback/{a.project}"), f"{len(fb)} POST(s)")
    if fb:
        body = fb[0]["body"]; v = body.get("view") or {}
        check("[4] pencil: comment starts with the typed text", str(body.get("comment", "")).startswith("a gap here"), repr(body.get("comment")))
        check("[4] pencil: JPEG image", jpeg_ok(body.get("image")))
        check("[4] pencil: view.parts = part ids under the drawing", isinstance(v.get("parts"), list) and len(v["parts"]) > 0 and all(isinstance(x, str) for x in v["parts"]), json.dumps(v.get("parts")))
        check("[4] pencil: view keeps url, hash, has shapes + camera and no selected",
              all(k in v for k in ("url", "hash", "shapes", "camera")) and "selected" not in v, ",".join(sorted(v)))
    check("console has no errors (pencil flow)", not errs, "; ".join(errs[:3]))
    ctx.close()
    b.close()


with sync_playwright() as p:
    run(p)
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
