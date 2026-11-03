#!/usr/bin/env python3
"""Real-browser check of the bot's links and the model-rebuild banner on the assembly site (done-when 9).

Builds a fresh site from importer output with THIS template (build.py, --kit auto), serves it under /<project>/ like the stack does
(viewer_stack_server), and intercepts the router: /kit/** (status + downloads) and /<project>/model.json (the router writes
public/model.json = {"v", "building", "since", "est_secs"} whenever the model version / build state changes).

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_model_link.py \
      [--in <importer output>] [--part <id>] [--swiftshader] [--shots <dir>]
(first run: `... --with playwright playwright install chromium`)
The model: --in, else $VIEWER_TEST_MODEL, else the demo machine (examples/nema-bracket, built once in a temp dir); the part with files
is the biggest distinct part (or --part). The page is in its default language (ru); the texts checked come from i18n.mjs.

Contract under test:
  [9a] /<project>/#sel=<id>&dl=part selects the part and starts that part's download by itself (the same flow as the card button,
       including the 202 wait), no click.
  [9b] /<project>/#dl=print starts the print kit download by itself.
  [9c] while model.json says building: a banner with the text `banner` is visible; a download button that waits on a
       202 with "model": true says kit.building.
  [9d] when model.json `v` grows and building is false, the page reloads by itself onto the new version, keeping the hash (sel=<id>);
       it does not reload while the model is still building, nor while v stays the same.
  [9e] with text typed in a feedback textarea (or focus in a text field), a newer model.json version with building:false does not
       reload the page; once the text is cleared and focus leaves, it reloads.
Exit code 1 on any failed check.
"""
import argparse, json, re, subprocess, sys, tempfile, time, urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import model_dir, parts_of, serve_stack, ui_texts  # noqa: E402
ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="src", default=None, help="importer output dir (default: $VIEWER_TEST_MODEL, else the demo machine)")
ap.add_argument("--part", default=None, help="a part id that has files in the kit (default: the biggest distinct part)")
ap.add_argument("--build", default=str(HERE / "build.py"))
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
a.src = str(model_dir(a.src))
if not a.part:
    a.part = max((p for p in parts_of(a.src) if not p.get("part_id")), key=lambda p: p.get("volume_mm3") or 0)["id"]
T = ui_texts("ru")
BANNER, BUILDING, PRINT = T["banner"], T["kit"]["building"], T["kit"]["scopes"]["print"]
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="model_link_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
rows, ok_all = [], True
PROJ = "demo-proj"
PART_FILE = (f"Pièce {a.part}.zip", b"PK\x03\x04 part-zip")   # non-ASCII names on purpose (Content-Disposition filename*)
PRINT_FILE = ("Impression 3D – pièces.zip", b"PK\x03\x04 print-zip")
WAIT_SECONDS = 6


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


root = Path(tempfile.mkdtemp(prefix="model_link_site_"))
b_kit = subprocess.run([sys.executable, a.build, "--in", a.src, "--out", str(root / PROJ), "--flash", "none"], capture_output=True, text=True)
if b_kit.returncode:
    sys.exit("build.py failed:\n" + b_kit.stderr[-2000:])
srv, BASE = serve_stack(root)


def fake_router(page, st):
    """/kit/** and model.json. st: {model: {...}, slow: {scope: model-wait seconds}, ready_at: {scope: t}, calls: [paths]}."""
    def kit(route):
        path = urllib.parse.unquote(urllib.parse.urlparse(route.request.url).path)
        st["calls"].append(path)
        m = re.match(r"^/kit/([^/]+)/(.+)$", path)
        scope, t = (m[2] if m else ""), time.time()
        if scope == "status":
            body = {"enabled": True, "fresh": True, "building": False, "since": None, "parts": [a.part], "nodes": [],
                    "scopes": ["print", "laser", "bought", "step"], "off": {}}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        if scope in st["slow"]:
            st["ready_at"].setdefault(scope, t + st["slow"][scope])
            if t < st["ready_at"][scope]:
                return route.fulfill(status=202, content_type="application/json",
                                     body=json.dumps({"building": True, "model": True, "since": t - 3, "est_secs": 40, "queue_pos": 0}))
        files = {f"part/{a.part}": (*PART_FILE, "application/zip"), "print": (*PRINT_FILE, "application/zip")}
        if scope not in files:
            return route.fulfill(status=404, content_type="application/json", body=json.dumps({"error": "no such file"}))
        name, data, ctype = files[scope]
        route.fulfill(status=200, body=data, headers={"Content-Type": ctype,
                      "Content-Disposition": f"attachment; filename=\"kit.bin\"; filename*=UTF-8''{urllib.parse.quote(name)}"})

    def model(route):
        st["model_calls"] += 1
        route.fulfill(status=200, content_type="application/json", headers={"Cache-Control": "no-store"}, body=json.dumps(st["model"]))
    page.route("**/kit/**", kit)
    page.route(f"**/{PROJ}/model.json*", model)


def new_page(b, hash_="", slow=None, model=None):
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1, accept_downloads=True)
    ctx.add_init_script("try { localStorage.setItem('assembly.sec.%s.dl', '0') } catch (e) {}" % PROJ)
    page = ctx.new_page()
    errs, downloads, loads = [], [], []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" and not str((m.location or {}).get("url", "")).endswith(
        "/apk/latest.json") else None)   # the APK link probes apk/latest.json; a project without an APK answers 404 (not ours)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("download", lambda d: downloads.append(d))
    page.on("load", lambda: loads.append(time.time()))
    st = {"model": model or {"v": 1, "building": False, "since": None, "est_secs": None}, "slow": slow or {}, "ready_at": {},
          "calls": [], "model_calls": 0}
    fake_router(page, st)
    page.goto(f"{BASE}/{PROJ}/?app=0{hash_}", wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    return ctx, page, errs, downloads, loads, st


def wait_for(page, cond, timeout):
    """Sync Playwright dispatches events only inside its own calls: wait with page.wait_for_timeout, not time.sleep."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if cond():
                return True
        except Exception:  # noqa: BLE001  (the page may be navigating)
            pass
        page.wait_for_timeout(250)
    try:
        return bool(cond())
    except Exception:  # noqa: BLE001
        return False


def same_download(d, name, data):
    try:
        got = Path(d.path()).read_bytes()
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    return d.suggested_filename == name and got == data, f"{d.suggested_filename!r}, {len(got)} bytes"


def selected_part(page):
    return page.evaluate("decodeURIComponent(location.hash)")


def banner_visible(page):
    loc = page.get_by_text(re.compile(re.escape(BANNER)))
    return any(loc.nth(i).is_visible() for i in range(loc.count()))


def run(p):
    b = p.chromium.launch(args=GPU_ARGS)
    enc = urllib.parse.quote(a.part, safe="")

    # ---- 9a: #sel=<id>&dl=part -> the part is selected and its download starts by itself, through the 202 model wait
    ctx, page, errs, downloads, loads, st = new_page(b, f"#sel={enc}&dl=part", slow={f"part/{a.part}": WAIT_SECONDS})
    check("[9a] model loads", not page.evaluate("window.__viewer.error"))
    check("[9a] the part is selected", wait_for(page, lambda: page.locator("#info").is_visible() and f"sel={a.part}" in selected_part(page), 15),
          selected_part(page))
    check("[9a] the page asked for the part's file without a click", wait_for(page, lambda: f"/kit/{PROJ}/part/{a.part}" in st["calls"], 15),
          json.dumps(st["calls"][:6], ensure_ascii=False))
    waiting = wait_for(page, lambda: BUILDING in page.inner_text("#info"), 5)
    page.screenshot(path=str(shots / "9a_waiting.png"))
    check(f"[9a] while the router answers 202 model:true the card button says «{BUILDING}»", waiting, page.inner_text("#info")[:200])
    got = wait_for(page, lambda: len(downloads) >= 1, WAIT_SECONDS + 30)
    ok, det = same_download(downloads[0], *PART_FILE) if got else (False, "no download")
    check("[9a] when ready the part file downloads by itself", got and ok, det)
    page.wait_for_timeout(1500)
    check("[9a] exactly one download", len(downloads) == 1, str(len(downloads)))
    check("[9a] console has no errors", not errs, "; ".join(errs[:3]))
    ctx.close()

    # ---- 9b: #dl=print -> the print kit downloads by itself
    ctx, page, errs, downloads, loads, st = new_page(b, "#dl=print")
    got = wait_for(page, lambda: len(downloads) >= 1, 20)
    ok, det = same_download(downloads[0], *PRINT_FILE) if got else (False, "no download")
    check("[9b] #dl=print downloads the print kit without a click", got and ok and f"/kit/{PROJ}/print" in st["calls"], det)
    check("[9b] console has no errors", not errs, "; ".join(errs[:3]))
    ctx.close()

    # ---- 9c + 9d: the banner while the model builds; a reload onto the new version, keeping the hash
    ctx, page, errs, downloads, loads, st = new_page(b, f"#sel={enc}", slow={"print": 600})
    check("[9c] the page reads model.json", wait_for(page, lambda: st["model_calls"] >= 1, 20), str(st["model_calls"]))
    page.wait_for_timeout(6000)
    check("[9d] no reload while v stays the same", len(loads) == 1, str(len(loads)))
    check("[9c] no banner while the model is not building", not banner_visible(page))
    st["model"] = {"v": 2, "building": True, "since": time.time() - 3, "est_secs": 30}
    shown = wait_for(page, lambda: banner_visible(page), 30)
    page.screenshot(path=str(shots / "9c_banner.png"))
    check(f"[9c] model.json building -> a banner «{BANNER}» is visible", shown)
    pb = page.locator("#panel").get_by_role("button", name=PRINT, exact=True)
    if check("[9c] the print button is there", pb.count() == 1):
        h = pb.element_handle()
        h.click()
        check(f"[9c] a download waiting on the model rebuild says «{BUILDING}»", wait_for(page, lambda: BUILDING in h.inner_text(), 5),
              repr(h.inner_text()))
    page.wait_for_timeout(4000)
    check("[9d] no reload while the model is still building", len(loads) == 1, str(len(loads)))
    st["model"] = {"v": 2, "building": False, "since": None, "est_secs": None}
    st["slow"] = {}
    reloaded = wait_for(page, lambda: len(loads) >= 2, 40)
    check("[9d] v grew and building is false -> the page reloads by itself", reloaded, f"loads={len(loads)}")
    if reloaded:
        page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
        check("[9d] the reload keeps the hash (the selected part)", f"sel={a.part}" in selected_part(page), selected_part(page))
        page.wait_for_timeout(3000)
        check("[9d] after the reload: no banner", not banner_visible(page))
        check("[9d] and no reload loop", len(loads) == 2, str(len(loads)))
    page.screenshot(path=str(shots / "9d_after_reload.png"))
    check("[9c/9d] console has no errors", not errs, "; ".join(errs[:3]))
    ctx.close()

    # ---- 9e: a comment being written is never thrown away by the reload onto a new version
    for mode in ("text", "focus"):
        ctx, page, errs, downloads, loads, st = new_page(b, f"#sel={enc}")
        check(f"[9e/{mode}] the page reads model.json", wait_for(page, lambda: st["model_calls"] >= 1, 20), str(st["model_calls"]))
        # a feedback textarea if the page shows one, else a text field of the page (the viewer must spare any of them)
        page.evaluate("""() => { let t = [...document.querySelectorAll('#fbPop textarea, #fbPart textarea')].find((x) => x.offsetParent);
            if (!t) { t = document.createElement('textarea'); t.id = 't9e'; t.style.cssText = 'position:fixed;bottom:8px;left:8px;z-index:99';
                      document.body.appendChild(t); }
            t.setAttribute('data-9e', '1'); }""")
        ta = page.locator("[data-9e]")
        ta.focus()
        if mode == "text":
            ta.type("this part is too thin")
        n0 = len(loads)
        st["model"] = {"v": 2, "building": False, "since": None, "est_secs": None}
        page.wait_for_timeout(14000)       # the page polls model.json every few seconds: several polls see v=2
        check(f"[9e/{mode}] a newer version does not reload while " + ("text is typed in a text field" if mode == "text" else "a text field has focus"),
              len(loads) == n0 and st["model_calls"] >= 3, f"loads={len(loads)} polls={st['model_calls']}")
        if mode == "text":
            check("[9e/text] the typed text is still there", page.locator("[data-9e]").input_value() == "this part is too thin")
            ta.fill("")
        page.evaluate("() => { document.activeElement && document.activeElement.blur(); }")
        reloaded = wait_for(page, lambda: len(loads) > n0, 40)
        check(f"[9e/{mode}] once the field is empty and focus leaves, the page reloads onto the new version", reloaded, f"loads={len(loads)}")
        ctx.close()
    b.close()


with sync_playwright() as p:
    run(p)
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
