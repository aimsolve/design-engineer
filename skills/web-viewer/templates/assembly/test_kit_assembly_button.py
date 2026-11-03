#!/usr/bin/env python3
"""Real-browser check of the fifth download button, assembly drawings (kit scope assembly). Sibling of test_kit_downloads.py and
test_panel_downloads_fit.py, same style.

Builds a fresh site with THIS template (build.py --flash, plus --sim <sim> when a sim is given), serves it under /<project>/ like the
stack does, and fakes the router. The model: --in, else $VIEWER_TEST_MODEL, else the demo machine (examples/nema-bracket, built once in
a temp dir); the sim: --sim, else $VIEWER_TEST_SIM, else none (then the panel has no simulation section to open in [fit]).
  GET /kit/<project>/status   -> {enabled: true, fresh: true, scopes: [print, laser, bought, step, assembly], off: {}}
  GET /kit/<project>/assembly -> 200 a zip, Content-Disposition «<project title> – <assembly label>.zip»
  /hw/** -> 404 (no boards).

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_kit_assembly_button.py \
      [--in <importer output>] [--sim <machine sim dir>] \
      [--build <build.py to test>] [--swiftshader] [--shots <dir>]
(first run: `... --with playwright playwright install chromium`)

UI the test relies on (the spec): #panel, the downloads section #secDl, its buttons named exactly as the kit scopes print, laser,
bought, step, assembly (texts from i18n.mjs; the page is in its default language, ru). Check names carry the done-when number:
[1] the client presses the assembly drawings button on the model page and gets the zip (the page fetches /kit/<project>/assembly);
[fit] with every left-panel section open, all five buttons are fully visible inside #secDl at 1500x950, 1280x800 and on a phone.
LOOK at every PNG. Exit code 1 on any failed check.
"""
import argparse, atexit, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.parse
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
a.sim = a.sim or os.environ.get(SIM_ENV) or None   # optional here: without it the panel just has no simulation section
T = ui_texts("ru")
K = T["kit"]
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="assybtn_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
rows, ok_all = [], True

ASSY = K["scopes"]["assembly"]
DL_BUTTONS = [K["scopes"][k] for k in ("print", "laser", "bought", "step")] + [ASSY]
DL_HEAD = T["sec"]["dl"]
SECTIONS = {   # left-panel section head -> a selector of something visible only while it is open
    T["sec"]["view"]: "#bColMat",
    **({T["sim"]["machine"]: "#vmBoards"} if a.sim else {}),
    T["sec"]["hw"]: f"#panel :is(button, [role=button], a):has-text({json.dumps(T['hw']['connect'], ensure_ascii=False)})",
    DL_HEAD: f"#panel :is(button, [role=button], a):has-text({json.dumps(K['scopes']['print'], ensure_ascii=False)})",
    T["sec"]["parts"]: "#tree",
}
ZIP_NAME = f"Demo – {ASSY}.zip"
ZIP_BYTES = b"PK\x03\x04 assembly-zip"


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


root = Path(tempfile.mkdtemp(prefix="assybtn_site_"))
atexit.register(shutil.rmtree, root, True)   # the built site is large: never leave it behind
bld = subprocess.run([sys.executable, a.build, "--in", a.src, "--out", str(root / a.project), *(["--sim", a.sim] if a.sim else []), "--flash"],
                     capture_output=True, text=True)
if bld.returncode:
    sys.exit("build.py failed:\n" + (bld.stderr or bld.stdout)[-3000:])
srv, _ = serve_stack(root)
URL = f"http://127.0.0.1:{srv.server_port}/{a.project}/?app=0"
print("site:", root / a.project, "served at", URL)


def install_routes(ctx, calls):
    def kit(route):
        path = urllib.parse.unquote(urllib.parse.urlparse(route.request.url).path)
        calls.append(path)
        if path.rstrip("/").endswith("/status"):
            body = {"enabled": True, "fresh": True, "building": False, "since": None, "parts": [], "nodes": [],
                    "scopes": ["print", "laser", "bought", "step", "assembly"], "off": {}}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        if path == f"/kit/{a.project}/assembly":
            return route.fulfill(status=200, body=ZIP_BYTES, headers={
                "Content-Type": "application/zip",
                "Content-Disposition": f"attachment; filename=\"download.zip\"; filename*=UTF-8''{urllib.parse.quote(ZIP_NAME)}"})
        route.fulfill(status=404, content_type="application/json", body='{"error": "no file in this test"}')
    ctx.route(re.compile(r"^https?://[^/]+/kit/.*"), kit)
    ctx.route(re.compile(r"^https?://[^/]+/hw/.*"), lambda r: r.fulfill(status=404, content_type="application/json", body='{"error": "no router"}'))
    ctx.route(re.compile(r"^https?://[^/]+/[^/]+/model\.json.*"),
              lambda r: r.fulfill(status=200, content_type="application/json", body='{"v": 0, "building": false}'))


# ---------------------------------------------------------------- panel sections (same lookup as test_panel_layout.py)
FIND_HEAD_JS = r"""([label, mark]) => {
  const panel = document.getElementById('panel'); if (!panel) return null;
  const re = new RegExp('^\\s*' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?!\\p{L})', 'u');
  const own = el => Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  for (const el of panel.querySelectorAll('*')) {
    if (!vis(el)) continue;
    if (re.test(own(el)) || (el.children.length === 0 && re.test(el.textContent))) {
      document.querySelectorAll('[data-test-head="' + mark + '"]').forEach(x => x.removeAttribute('data-test-head'));
      const h = el.closest('button, summary, [role=button], h1, h2, h3, h4, h5, h6, [class*=head], [class*=Head]') || el;
      h.setAttribute('data-test-head', mark); return true;
    }
  }
  return null; }"""

def body_open(page, label):
    loc = page.locator(SECTIONS[label]).first
    return loc.count() > 0 and loc.evaluate("e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; }")


def open_section(page, label):
    mark = "h" + str(list(SECTIONS).index(label))
    if not page.evaluate(FIND_HEAD_JS, [label, mark]):
        return False
    if not body_open(page, label):
        page.locator(f'[data-test-head="{mark}"]').click(); page.wait_for_timeout(350)
    return body_open(page, label)


# Is the button fully visible: its rect inside every clipping ancestor, the viewport and #secDl? Scrolls the panel to it first, the way
# a person would (the panel may scroll as a whole); a button cut inside a tiny box of #secDl still fails.
FULLY_JS = r"""(label) => {
  const sec = document.getElementById('secDl'), panel = document.getElementById('panel');
  const b = Array.from((panel || document).querySelectorAll('button, [role=button], a')).find(x => x.textContent.trim() === label);
  if (!b) return {fully: false, why: 'missing'};
  b.scrollIntoView({block: 'nearest'});
  const r = b.getBoundingClientRect(); let c = {left: 0, top: 0, right: innerWidth, bottom: innerHeight}; const chain = [];
  for (let e = b.parentElement; e && e !== document.documentElement; e = e.parentElement) { const s = getComputedStyle(e);
    if (s.overflowX !== 'visible' || s.overflowY !== 'visible') { const q = e.getBoundingClientRect();
      c = {left: Math.max(c.left, q.left), top: Math.max(c.top, q.top), right: Math.min(c.right, q.right), bottom: Math.min(c.bottom, q.bottom)};
      chain.push((e.id ? '#' + e.id : e.tagName.toLowerCase()) + ' ' + Math.round(q.height) + 'px'); } }
  const w = sec && sec.getBoundingClientRect();
  const fully = r.width > 0 && r.height > 0 && r.left >= c.left - 1 && r.right <= c.right + 1 && r.top >= c.top - 1 && r.bottom <= c.bottom + 1
    && !!w && r.top >= w.top - 1 && r.bottom <= w.bottom + 1 && r.left >= w.left - 1 && r.right <= w.right + 1;
  const hit = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
  return {fully: fully && !!hit && (hit === b || b.contains(hit)), rect: [r.left, r.top, r.right, r.bottom].map(Math.round), clippedBy: chain,
          disabled: !!b.disabled, inSecDl: !!sec && sec.contains(b)}; }"""


def open_page(p, w, h, mobile=False):
    b = p.chromium.launch(args=GPU)
    ctx = b.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, is_mobile=mobile, has_touch=mobile, accept_downloads=True)
    ctx.add_init_script("try { localStorage.setItem('assembly.sec.%s.dl', '0') } catch (e) {}" % a.project)   # the downloads section open
    calls = []
    install_routes(ctx, calls)
    ctx.set_default_timeout(8000); ctx.set_default_navigation_timeout(120000)
    page = ctx.new_page()
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(URL, wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    try:
        page.get_by_role("button", name=K["scopes"]["print"], exact=True).wait_for(timeout=15000)
    except Exception:  # noqa
        pass
    page.wait_for_timeout(800)
    return b, page, calls, errs


def run_press(p):
    """[1] the fifth button is there, in #secDl, enabled, and one press downloads /kit/<project>/assembly."""
    b, page, calls, errs = open_page(p, 1500, 950)
    btn = page.locator("#panel").get_by_role("button", name=ASSY, exact=True)
    check(f"[1] #panel has exactly one «{ASSY}» button", btn.count() == 1, f"{btn.count()} found")
    if btn.count() != 1:
        page.screenshot(path=str(shots / "assy_missing.png")); b.close(); return
    check(f"[1] «{ASSY}» sits in «{DL_HEAD}» (#secDl)", page.evaluate("() => { const s = document.getElementById('secDl'); "
          f"return !!s && Array.from(s.querySelectorAll('button')).some(x => x.textContent.trim() === {json.dumps(ASSY)}); }}"))
    check(f"[1] «{ASSY}» is enabled when the router offers «assembly»", btn.is_enabled())
    page.screenshot(path=str(shots / "assy_panel.png"))
    with page.expect_download(timeout=15000) as dl:
        btn.click()
    d = dl.value
    data = Path(d.path()).read_bytes()
    check(f"[1] «{ASSY}» fetches /kit/{a.project}/assembly", f"/kit/{a.project}/assembly" in calls, json.dumps(calls[-4:], ensure_ascii=False))
    check(f"[1] the download is the router's zip under its name", d.suggested_filename == ZIP_NAME and data == ZIP_BYTES,
          f"{d.suggested_filename!r}, {len(data)} bytes")
    check("[1] no page errors", not errs, json.dumps(errs[:3], ensure_ascii=False))
    b.close()


def run_fit(p, w, h, mobile=False):
    """[fit] every section open: the five buttons all fully visible inside #secDl."""
    tag = f"{w}x{h}"
    b, page, _, _ = open_page(p, w, h, mobile)
    if mobile and page.evaluate("() => document.getElementById('panel').classList.contains('collapsed')"):
        page.click("#pFold"); page.wait_for_timeout(400)
    opened = {s: open_section(page, s) for s in SECTIONS}
    page.wait_for_timeout(400)
    check(f"[fit] {tag}: «{DL_HEAD}» open", opened.get(DL_HEAD), json.dumps(opened, ensure_ascii=False))
    for label in DL_BUTTONS:
        r = page.evaluate(FULLY_JS, label)
        check(f"[fit] {tag}: «{label}» fully visible inside #secDl", r.get("fully") and r.get("inSecDl"), json.dumps(r, ensure_ascii=False))
    page.evaluate("() => { const s = document.getElementById('secDl'); s && s.scrollIntoView({block: 'nearest'}); }")
    page.screenshot(path=str(shots / f"assy_fit_{tag}.png"))
    b.close()


with sync_playwright() as p:
    for name, fn in (("press", lambda: run_press(p)), ("fit 1500x950", lambda: run_fit(p, 1500, 950)),
                     ("fit 1280x800", lambda: run_fit(p, 1280, 800)), ("fit phone 390x844", lambda: run_fit(p, 390, 844, True))):
        try:
            fn()
        except Exception as e:  # noqa
            check(f"scenario {name} ran to the end", False, f"{type(e).__name__}: {str(e)[:300]}")
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
