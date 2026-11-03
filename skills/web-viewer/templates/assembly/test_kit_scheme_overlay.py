#!/usr/bin/env python3
"""Real-browser check of the scheme DRAWING overlay (step 2b of the scheme buttons). Sibling of test_kit_schemes_buttons.py, same style.

Builds a fresh site with THIS template (build.py --flash, plus --sim <sim> when a sim is given), serves it under /<project>/ like the
stack does, and fakes the router. The model: --in, else $VIEWER_TEST_MODEL, else the demo machine (examples/nema-bracket, built once in
a temp dir); the sim: --sim, else $VIEWER_TEST_SIM, else none (the overlay does not need one).
  "declared": GET /kit/<project>/status -> {enabled, fresh, scopes: [print, laser, bought, step, assembly, electric],
              off: {pneumatic: "none"}}
              GET /kit/<project>/svg/electric -> 200 image/svg+xml, a drawing with the text «Power supply 24 V»
              GET /kit/<project>/electric     -> 200 a PDF, Content-Disposition «Demo – <electric label>.pdf»
  "none":     scopes: [print, laser, bought, step, assembly], off: {electric: "none", pneumatic: "none"}
  /hw/** -> 404 (no boards).

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_kit_scheme_overlay.py \
      [--in <importer output>] [--sim <machine sim dir>] \
      [--build <build.py to test>] [--swiftshader] [--shots <dir>]
(first run: `... --with playwright playwright install chromium`)

UI the test relies on (the spec): #secDl's electric scheme button (kit.scopes.electric); the overlay `#kitScheme` holding the drawing
as an <img>, a PDF button (kit.pdf) and a close button (kit.close); texts from i18n.mjs, the page in its default language (ru).
Check names carry the done-when:
[6] pressing the electric scheme button fetches /kit/<project>/svg/electric and opens #kitScheme showing the drawing as an <img> (no
    download yet); the PDF button fetches /kit/<project>/electric and saves the PDF under the Content-Disposition name; the close
    button and Escape remove the overlay; a greyed-out scheme button fetches nothing and opens no overlay.
LOOK at every PNG. Exit code 1 on any failed check.
"""
import argparse, atexit, json, os, re, shutil, subprocess, sys, tempfile, urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import SIM_ENV, model_dir, serve_stack, ui_texts  # noqa: E402
ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="src", default=None, help="importer output dir (default: $VIEWER_TEST_MODEL, else the demo machine)")
ap.add_argument("--sim", default=None, help="machine sim dir with site.json (default: $VIEWER_TEST_SIM, else none)")
ap.add_argument("--build", default=str(HERE / "build.py"), help="build.py to test (default: this template)")
ap.add_argument("--project", default="demo-machine", help="URL segment the site is served under (/kit/<project>/...)")
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
a.src = str(model_dir(a.src))
a.sim = a.sim or os.environ.get(SIM_ENV) or None   # optional here
K = ui_texts("ru")["kit"]
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="schemeovl_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
rows, ok_all = [], True

EL, PN = K["scopes"]["electric"], K["scopes"]["pneumatic"]
PDF_LABEL, CLOSE_LABEL = K["pdf"], K["close"]
PDF_NAME = f"Demo – {EL}.pdf"
PDF_BYTES = b"%PDF-1.4\n% merged electric scheme, 3 sheets\n%%EOF\n"
SVG_BYTES = ('<?xml version="1.0" encoding="utf-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="840" height="594" '
             'viewBox="0 0 840 594"><rect x="40" y="40" width="200" height="120" fill="none" stroke="#000"/>'
             '<line x1="240" y1="100" x2="600" y2="100" stroke="#000"/>'
             '<text x="50" y="80" font-size="16">Power supply 24 V</text></svg>\n').encode()
BASE = ["print", "laser", "bought", "step", "assembly"]
STATES = {
    "declared": {"scopes": BASE + ["electric"], "off": {"pneumatic": "none"}},
    "none": {"scopes": BASE, "off": {"electric": "none", "pneumatic": "none"}},
}
SVG_PATH, PDF_PATH = f"/kit/{a.project}/svg/electric", f"/kit/{a.project}/electric"


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


root = Path(tempfile.mkdtemp(prefix="schemeovl_site_"))
atexit.register(shutil.rmtree, root, True)   # the built site is large: never leave it behind
bld = subprocess.run([sys.executable, a.build, "--in", a.src, "--out", str(root / a.project), *(["--sim", a.sim] if a.sim else []), "--flash"],
                     capture_output=True, text=True)
if bld.returncode:
    sys.exit("build.py failed:\n" + (bld.stderr or bld.stdout)[-3000:])
srv, _ = serve_stack(root)
URL = f"http://127.0.0.1:{srv.server_port}/{a.project}/?app=0"
print("site:", root / a.project, "served at", URL)


def install_routes(ctx, calls, state):
    def kit(route):
        path = urllib.parse.unquote(urllib.parse.urlparse(route.request.url).path)
        calls.append(path)
        offered = "electric" in STATES[state]["scopes"]
        if path.rstrip("/").endswith("/status"):
            body = dict({"enabled": True, "fresh": True, "building": False, "since": None, "parts": [], "nodes": []}, **STATES[state])
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        if path == SVG_PATH and offered:
            return route.fulfill(status=200, body=SVG_BYTES, headers={"Content-Type": "image/svg+xml"})
        if path == PDF_PATH and offered:
            return route.fulfill(status=200, body=PDF_BYTES, headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": f"attachment; filename=\"download.pdf\"; filename*=UTF-8''{urllib.parse.quote(PDF_NAME)}"})
        route.fulfill(status=404, content_type="application/json", body='{"error": "no file in this test"}')
    ctx.route(re.compile(r"^https?://[^/]+/kit/.*"), kit)
    ctx.route(re.compile(r"^https?://[^/]+/hw/.*"), lambda r: r.fulfill(status=404, content_type="application/json", body='{"error": "no router"}'))
    ctx.route(re.compile(r"^https?://[^/]+/[^/]+/model\.json.*"),
              lambda r: r.fulfill(status=200, content_type="application/json", body='{"v": 0, "building": false}'))


# The overlay as the user sees it: present, visible, its <img> loaded (a decoded picture, not a broken icon), its buttons.
OVERLAY_JS = r"""([pdfLabel, closeLabel]) => {
  const o = document.getElementById('kitScheme');
  if (!o) return {present: false};
  const r = o.getBoundingClientRect(), s = getComputedStyle(o);
  const img = o.querySelector('img');
  const name = b => [b.textContent, b.getAttribute('aria-label'), b.title].map(x => (x || '').trim());   // text or accessible name
  const btn = t => Array.from(o.querySelectorAll('button, [role=button], a')).some(b => name(b).includes(t));
  return {present: true, visible: r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none',
          img: !!img, imgSrc: img ? img.currentSrc || img.src : null, imgLoaded: !!img && img.complete && img.naturalWidth > 0,
          imgShown: !!img && img.getBoundingClientRect().width > 0 && img.getBoundingClientRect().height > 0,
          download: btn(pdfLabel), close: btn(closeLabel)}; }"""


def open_page(p, w, h, state):
    b = p.chromium.launch(args=GPU)
    ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, accept_downloads=True)
    ctx.add_init_script("try { localStorage.setItem('assembly.sec.%s.dl', '0') } catch (e) {}" % a.project)   # the downloads section open
    calls, downloads = [], []
    install_routes(ctx, calls, state)
    ctx.set_default_timeout(8000); ctx.set_default_navigation_timeout(120000)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("download", lambda d: downloads.append(d))
    page.goto(URL, wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    try:
        page.get_by_role("button", name=EL, exact=True).wait_for(timeout=15000)
    except Exception:  # noqa
        pass
    page.wait_for_timeout(800)
    return b, page, calls, downloads, errs


def overlay(page):
    return page.evaluate(OVERLAY_JS, [PDF_LABEL, CLOSE_LABEL])


def press_scheme(page):
    page.locator("#secDl").get_by_role("button", name=EL, exact=True).click()
    try:
        page.wait_for_function("() => { const i = document.querySelector('#kitScheme img'); return i && i.complete && i.naturalWidth > 0; }",
                               timeout=8000)
    except Exception:  # noqa
        pass


def run_overlay(p):
    """[6] press -> SVG fetched, overlay with the drawing; the PDF button -> the router's PDF; the close button / Escape -> gone."""
    b, page, calls, downloads, errs = open_page(p, 1500, 950, "declared")
    n = len(calls)
    press_scheme(page)
    asked = calls[n:]
    check(f"[6] «{EL}» fetches {SVG_PATH}", SVG_PATH in asked, json.dumps(asked, ensure_ascii=False))
    o = overlay(page)
    check("[6] the press opens a visible #kitScheme overlay", o.get("present") and o.get("visible"), json.dumps(o, ensure_ascii=False))
    check("[6] the overlay shows the drawing as a loaded <img>", o.get("img") and o.get("imgLoaded") and o.get("imgShown"),
          json.dumps(o, ensure_ascii=False))
    check("[6] the press alone downloads nothing and does not fetch the PDF", not downloads and PDF_PATH not in asked,
          f"downloads {len(downloads)}; fetched {json.dumps(asked, ensure_ascii=False)}")
    check(f"[6] the overlay has «{PDF_LABEL}» and «{CLOSE_LABEL}»", o.get("download") and o.get("close"), json.dumps(o, ensure_ascii=False))
    page.screenshot(path=str(shots / "scheme_overlay_open.png"))
    if not o.get("present"):
        b.close(); return

    if o.get("download"):
        n = len(calls)
        with page.expect_download(timeout=15000) as dl:
            page.locator("#kitScheme").get_by_role("button", name=PDF_LABEL, exact=True).click()
        d = dl.value
        data = Path(d.path()).read_bytes()
        check(f"[6] «{PDF_LABEL}» fetches {PDF_PATH}", PDF_PATH in calls[n:], json.dumps(calls[n:], ensure_ascii=False))
        check("[6] the download is the router's PDF under its Content-Disposition name", d.suggested_filename == PDF_NAME and data == PDF_BYTES,
              f"{d.suggested_filename!r}, {len(data)} bytes")

    if o.get("close"):
        page.locator("#kitScheme").get_by_role("button", name=CLOSE_LABEL, exact=True).click()
        page.wait_for_timeout(400)
        check(f"[6] «{CLOSE_LABEL}» removes #kitScheme", page.locator("#kitScheme").count() == 0, json.dumps(overlay(page), ensure_ascii=False))

    if page.locator("#kitScheme").count() == 0:     # closed by the close button: open it again for Escape
        press_scheme(page)
        reopened = overlay(page)
        check("[6] pressing again reopens the overlay", reopened.get("present") and reopened.get("visible"), json.dumps(reopened, ensure_ascii=False))
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    check("[6] Escape removes #kitScheme", page.locator("#kitScheme").count() == 0, json.dumps(overlay(page), ensure_ascii=False))
    page.screenshot(path=str(shots / "scheme_overlay_closed.png"))
    check("[6] no page errors", not errs, json.dumps(errs[:3], ensure_ascii=False))
    b.close()


def run_grey(p):
    """[6] a greyed-out scheme button fetches nothing and opens no overlay."""
    b, page, calls, downloads, errs = open_page(p, 1500, 950, "none")
    n, pressed = len(calls), 0
    for label in (EL, PN):
        loc = page.locator("#secDl").get_by_role("button", name=label, exact=True)
        if loc.count() == 1:
            loc.click(force=True); page.wait_for_timeout(500); pressed += 1
    asked = [c for c in calls[n:] if c.endswith(("/electric", "/pneumatic"))]
    check("[6] pressing a grey scheme button fetches nothing (no SVG, no PDF)", pressed == 2 and not asked,
          f"pressed {pressed} of 2; fetched {json.dumps(asked, ensure_ascii=False)}")
    check("[6] a grey scheme button opens no overlay", page.locator("#kitScheme").count() == 0 and not downloads,
          json.dumps(overlay(page), ensure_ascii=False))
    check("[6] no page errors", not errs, json.dumps(errs[:3], ensure_ascii=False))
    b.close()


with sync_playwright() as p:
    for name, fn in (("overlay", lambda: run_overlay(p)), ("grey", lambda: run_grey(p))):
        try:
            fn()
        except Exception as e:  # noqa
            check(f"scenario {name} ran to the end", False, f"{type(e).__name__}: {str(e)[:300]}")
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
