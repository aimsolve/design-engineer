#!/usr/bin/env python3
"""Real-browser check on the shared viewer: the project page offers the machine's released Android app.

Builds two sites from importer output with THIS template (build.py, --flash none): one with the default --kit auto, one with --kit none.
Each gets a public apk/ folder like the router's release publish leaves it (tests/test_apk_page.py covers that side):
  apk/latest.json = {"version": "1.2.3", "file": "app-1.2.3.apk", "released_at": <unix int>}   and   apk/app-1.2.3.apk
Their «no APK» twins are the same sites without apk/ (symlinked entries), so GET <project>/apk/latest.json answers 204 —
the stack answers the probe of a project without a release that way, never 404 (a 404 is an error in the client's console).
Served under /<project>/ like the stack does (viewer_stack_server.py); the router's /kit/<project>/status and model.json are faked, /hw/** answers 404.

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_apk_button.py \
      [--in <importer output>] [--swiftshader] [--shots <dir>]
(first run: `... --with playwright playwright install chromium`)
The model: --in, else $VIEWER_TEST_MODEL, else the demo machine (examples/nema-bracket, built once in a temp dir).

UI the test relies on (the spec): the panel is #panel, its downloads section is #secDl (hidden attribute while it has nothing to
offer). The APK control is a button (or a link) in #secDl whose accessible name starts with apk.button; the version «1.2.3» is visible
in #secDl next to it. Clicking it downloads <project>/apk/app-1.2.3.apk (a Playwright download event). The four kit buttons are the kit
scopes print, laser, bought, step. Texts from i18n.mjs; the page is in its default language (ru).
Check names start with the done-when number:
  [1] APK released -> the downloads section visible, the button there, version 1.2.3 next to it, a click downloads apk/app-1.2.3.apk
  [2] the same with and without `kit` in config.json; with kit the four kit buttons stay, and the APK button survives the kit row being
      rebuilt (the router's status changes its scopes; the page is told to re-read it via window.__viewer.kit.status())
  [3] no apk/latest.json (204) -> no APK button, no console error; with no kit either the downloads section stays hidden
  [4] phone 390x844 -> the button inside the panel, nothing scrolls sideways
Exit code 1 on any failed check.
"""
import argparse, json, re, subprocess, sys, tempfile, time, urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import model_dir, serve_stack, ui_texts  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="src", default=None, help="importer output dir (default: $VIEWER_TEST_MODEL, else the demo machine)")
ap.add_argument("--build", default=str(HERE / "build.py"), help="build.py to test (default: this template)")
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
a.src = str(model_dir(a.src))
T = ui_texts("ru")
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="apk_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
rows, ok_all = [], True

VER, FILE = "1.2.3", "app-1.2.3.apk"
APK_BYTES = b"PK\x03\x04 fake released apk " + VER.encode()
APK_LABEL = T["apk"]["button"]
APK_NAME = re.compile(r"^\s*" + re.escape(APK_LABEL))
KIT_BUTTONS = [T["kit"]["scopes"][k] for k in ("print", "laser", "bought", "step")]
STEP_LABEL = T["kit"]["scopes"]["step"]
DL_HEAD = T["sec"]["dl"]
KIT_SITE, NOKIT_SITE, KIT_NOAPK, NOKIT_NOAPK = "apk-kit", "apk-nokit", "noapk-kit", "noapk-nokit"   # project ids: /<a>-<b>/ like the stack


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- sites
root = Path(tempfile.mkdtemp(prefix="apk_site_"))
import atexit, shutil
atexit.register(shutil.rmtree, root, True)
for pid, extra in ((KIT_SITE, []), (NOKIT_SITE, ["--kit", "none"])):
    r = subprocess.run([sys.executable, a.build, "--in", a.src, "--out", str(root / pid), "--flash", "none", *extra], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"build.py ({pid}) failed:\n" + (r.stderr or r.stdout)[-2000:])
for src_pid, dst_pid in ((KIT_SITE, KIT_NOAPK), (NOKIT_SITE, NOKIT_NOAPK)):   # same site, no apk/ folder
    d = root / dst_pid; d.mkdir()
    for e in (root / src_pid).iterdir():
        (d / e.name).symlink_to(e)
for pid in (KIT_SITE, NOKIT_SITE):   # what the router's release publish leaves in the project's public folder
    apk = root / pid / "apk"; apk.mkdir()
    (apk / FILE).write_bytes(APK_BYTES)
    (apk / "latest.json").write_text(json.dumps({"version": VER, "file": FILE, "released_at": int(time.time()) - 3600}), encoding="utf-8")
srv, BASE = serve_stack(root)


def kit_router(ctx, st):
    """Fake router: /kit/<p>/status from `st` (scopes/off can change mid-test), /kit/<p>/<scope> 404, /hw/** 404."""
    def kit(route):
        path = urllib.parse.unquote(urllib.parse.urlparse(route.request.url).path)
        st["calls"].append(path)
        if path.rstrip("/").endswith("/status"):
            body = {"enabled": True, "fresh": True, "building": False, "since": None, "parts": [], "nodes": [],
                    "scopes": st["scopes"], "off": st["off"]}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        route.fulfill(status=404, content_type="application/json", body='{"error": "no kit file in this test"}')
    ctx.route(re.compile(r"^https?://[^/]+/kit/.*"), kit)
    ctx.route(re.compile(r"^https?://[^/]+/hw/.*"), lambda r: r.fulfill(status=404, content_type="application/json", body='{"error": "no router"}'))
    ctx.route("**/model.json", lambda r: r.fulfill(status=200, content_type="application/json", body='{"v": 0, "building": false}'))   # the router writes it; nothing serves it here


def open_page(b, pid, w=1440, h=900, mobile=False):
    ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, is_mobile=mobile, has_touch=mobile, accept_downloads=True)
    ctx.add_init_script("try { localStorage.setItem('assembly.sec.%s.dl', '0') } catch (e) {}" % pid)   # the downloads section folds by default: open it
    st = {"scopes": ["print", "laser", "bought", "step"], "off": {}, "calls": []}
    kit_router(ctx, st)
    page = ctx.new_page()
    errs, downloads, reqs, codes = [], [], [], []
    faked = lambda m: "404" in m.text and any(s in ((m.location or {}).get("url") or "") for s in ("/kit/", "/hw/"))   # only those two routes are faked as 404
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" and not faked(m) else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("download", lambda d: downloads.append(d))
    page.on("request", lambda r: reqs.append(urllib.parse.urlparse(r.url).path))
    page.on("response", lambda r: codes.append((urllib.parse.urlparse(r.url).path, r.status)))
    page.goto(f"{BASE}/{pid}/?app=0", wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    page.wait_for_timeout(2500)   # latest.json + kit status settle
    return ctx, page, errs, downloads, reqs, st, codes


def apk_btn(page, scope="#secDl"):
    s = page.locator(scope)
    return s.get_by_role("button", name=APK_NAME).or_(s.get_by_role("link", name=APK_NAME))


def sec_visible(page):
    return page.evaluate("() => { const s = document.getElementById('secDl'); if (!s || s.hidden) return false;"
                         " const r = s.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(s).visibility !== 'hidden'; }")


VERSION_NEAR_JS = r"""([ver, btn]) => {   // a visible element in #secDl showing the version, and how far it is from the APK button
  const sec = document.getElementById('secDl'); if (!sec || !btn) return {found: false};
  const b = btn.getBoundingClientRect(); let best = null;
  const els = [btn, ...sec.querySelectorAll('*')];
  for (const e of els) {
    const own = Array.from(e.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('');
    const txt = (e === btn || e.children.length === 0) ? e.textContent : own;
    if (!txt.includes(ver)) continue;
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    if (!(r.width > 0 && r.height > 0) || s.visibility === 'hidden' || s.display === 'none') continue;
    const dy = Math.max(0, r.top - b.bottom, b.top - r.bottom), dx = Math.max(0, r.left - b.right, b.left - r.right);
    const d = Math.hypot(dx, dy);
    if (!best || d < best.d) best = {d: Math.round(d), text: txt.trim().slice(0, 60)};
  }
  return best ? {found: true, ...best} : {found: false}; }"""


def check_apk_offered(page, downloads, tag, n_tag="[1]"):
    sv = sec_visible(page)
    check(f"{n_tag} {tag}: «{DL_HEAD}» (#secDl) is visible", sv)
    bt = apk_btn(page)
    if not check(f"{n_tag} {tag}: #secDl has «{APK_LABEL}»", bt.count() == 1 and bt.first.is_visible(), f"{bt.count()} found"):
        return False
    near = page.evaluate(f"([ver]) => ({VERSION_NEAR_JS})([ver, document.querySelector('[data-test-apk]')])", [VER]) \
        if bt.first.evaluate("e => (e.setAttribute('data-test-apk', '1'), true)") else {"found": False}
    check(f"{n_tag} {tag}: version {VER} is visible next to the button (<= 60 px)", near.get("found") and near["d"] <= 60, json.dumps(near, ensure_ascii=False))
    n0 = len(downloads)
    bt.first.click()
    t0 = time.time()
    while len(downloads) <= n0 and time.time() - t0 < 10:
        page.wait_for_timeout(200)
    if not check(f"{n_tag} {tag}: clicking it starts a download", len(downloads) > n0):
        return False
    d = downloads[-1]
    url_path = urllib.parse.urlparse(d.url).path
    check(f"{n_tag} {tag}: the download URL ends with apk/{FILE}", url_path.endswith(f"apk/{FILE}"), d.url)
    try:
        got = Path(d.path()).read_bytes()
    except Exception as e:  # noqa
        got = repr(e).encode()
    check(f"{n_tag} {tag}: the downloaded file is the released APK", got == APK_BYTES, f"{d.suggested_filename!r}, {len(got)} bytes")
    return True


def kit_buttons_present(page):
    return {l: page.locator("#secDl").get_by_role("button", name=l, exact=True).count() == 1 for l in KIT_BUTTONS}


def run(p):
    b = p.chromium.launch(args=GPU_ARGS)
    conf_k = json.loads((root / KIT_SITE / "config.json").read_text(encoding="utf-8"))
    conf_n = json.loads((root / NOKIT_SITE / "config.json").read_text(encoding="utf-8"))
    check("setup: default build has `kit` in config.json, --kit none has not", "kit" in conf_k and "kit" not in conf_n, f"{list(conf_k)} / {list(conf_n)}")

    # ---- [1] + [2] without kit
    ctx, page, errs, downloads, reqs, st, codes = open_page(b, NOKIT_SITE)
    check("[2] no kit: model loads", not page.evaluate("window.__viewer.error"))
    page.screenshot(path=str(shots / "1_nokit.png"))
    check_apk_offered(page, downloads, "no kit")
    check("[2] no kit: no kit buttons appear", not any(kit_buttons_present(page).values()))
    check("[2] no kit: console has no errors", not errs, "; ".join(errs[:3]))
    ctx.close()

    # ---- [1] + [2] with kit, then the kit row is rebuilt
    ctx, page, errs, downloads, reqs, st, codes = open_page(b, KIT_SITE)
    check("[2] kit: model loads", not page.evaluate("window.__viewer.error"))
    page.screenshot(path=str(shots / "1_kit.png"))
    kb = kit_buttons_present(page)
    check(f"[2] kit: the four kit buttons are in «{DL_HEAD}»", all(kb.values()), json.dumps(kb, ensure_ascii=False))
    check_apk_offered(page, downloads, "kit")
    st["scopes"], st["off"] = ["laser", "bought"], {"print": "none", "step": "setup"}   # the router's scopes change -> the kit row is rebuilt
    has_api = page.evaluate("!!(window.__viewer.kit && window.__viewer.kit.status)")
    if check("[2] kit: the page exposes window.__viewer.kit.status (re-read of the router status)", has_api):
        page.evaluate("window.__viewer.kit.status()")
        page.wait_for_timeout(800)
        step = page.locator("#secDl").get_by_role("button", name=STEP_LABEL, exact=True)
        check(f"[2] kit: the kit row was rebuilt («{STEP_LABEL}» now greyed out)", step.count() == 1 and step.get_attribute("aria-disabled") == "true")
        kb = kit_buttons_present(page)
        check("[2] kit: after the rebuild the four kit buttons are still there", all(kb.values()), json.dumps(kb, ensure_ascii=False))
        page.screenshot(path=str(shots / "2_kit_rebuilt.png"))
        check_apk_offered(page, downloads, "kit after the kit row was rebuilt", "[2]")
    check("[2] kit: console has no errors", not errs, "; ".join(errs[:3]))
    ctx.close()

    # ---- [3] no apk/latest.json
    ctx, page, errs, downloads, reqs, st, codes = open_page(b, NOKIT_NOAPK)
    probe = [s for pth, s in codes if pth.endswith("apk/latest.json")]
    check("[3] no APK, no kit: the page asked for apk/latest.json and got 204, not 404", probe == [204], str(probe))
    check(f"[3] no APK, no kit: no «{APK_LABEL}» anywhere", apk_btn(page, "body").count() == 0)
    check(f"[3] no APK, no kit: «{DL_HEAD}» stays hidden", not sec_visible(page))
    check("[3] no APK, no kit: console has no errors", not errs, "; ".join(errs[:3]))
    page.screenshot(path=str(shots / "3_noapk_nokit.png"))
    ctx.close()

    ctx, page, errs, downloads, reqs, st, codes = open_page(b, KIT_NOAPK)
    check(f"[3] no APK, kit: no «{APK_LABEL}» anywhere", apk_btn(page, "body").count() == 0)
    kb = kit_buttons_present(page)
    check(f"[3] no APK, kit: «{DL_HEAD}» visible with the four kit buttons", sec_visible(page) and all(kb.values()), json.dumps(kb, ensure_ascii=False))
    check("[3] no APK, kit: console has no errors", not errs, "; ".join(errs[:3]))
    ctx.close()

    # ---- [4] phone
    for pid in (KIT_SITE, NOKIT_SITE):
        tag = f"390x844 {'kit' if pid == KIT_SITE else 'no kit'}"
        ctx, page, errs, downloads, reqs, st, codes = open_page(b, pid, 390, 844, mobile=True)
        if page.evaluate("() => document.getElementById('panel').classList.contains('collapsed')"):   # on a phone the panel starts folded
            page.click("#pFold"); page.wait_for_timeout(400)
        bt = apk_btn(page)
        if check(f"[4] {tag}: the APK button is present", bt.count() == 1):
            bt.first.scroll_into_view_if_needed(); page.wait_for_timeout(300)
            geo = bt.first.evaluate("""e => { const r = e.getBoundingClientRect(), p = document.getElementById('panel').getBoundingClientRect(),
                s = document.getElementById('secDl'), d = document.documentElement;
                return {btn: [Math.round(r.left), Math.round(r.right), Math.round(r.top), Math.round(r.bottom)],
                        panel: [Math.round(p.left), Math.round(p.right), Math.round(p.top), Math.round(p.bottom)], vw: innerWidth, vh: innerHeight,
                        docScrollW: d.scrollWidth, bodyScrollW: document.body.scrollWidth,
                        secOverflowX: s.scrollWidth - s.clientWidth, panelOverflowX: document.getElementById('panel').scrollWidth - document.getElementById('panel').clientWidth}; }""")
            bx, pn = geo["btn"], geo["panel"]
            check(f"[4] {tag}: the button is visible", bt.first.is_visible())
            check(f"[4] {tag}: the button lies inside the panel horizontally and on screen",
                  bx[0] >= pn[0] - 1 and bx[1] <= pn[1] + 1 and bx[0] >= -1 and bx[1] <= geo["vw"] + 1 and bx[2] >= pn[2] - 1, json.dumps(geo))
            check(f"[4] {tag}: no horizontal overflow (page, #panel, #secDl)",
                  geo["docScrollW"] <= geo["vw"] + 1 and geo["bodyScrollW"] <= geo["vw"] + 1 and geo["secOverflowX"] <= 1 and geo["panelOverflowX"] <= 1, json.dumps(geo))
            page.screenshot(path=str(shots / f"4_phone_{pid}.png"))
        ctx.close()
    b.close()


with sync_playwright() as p:
    try:
        run(p)
    except Exception as e:  # noqa
        check("scenario ran to the end", False, f"{type(e).__name__}: {str(e)[:300]}")
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
