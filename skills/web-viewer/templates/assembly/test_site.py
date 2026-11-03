#!/usr/bin/env python3
"""Real-browser check of an assembly-mode site: screenshots on desktop and phone, click -> card, downloads, console, frame time.

With --url: an already served site. Without it: the test builds a site with THIS build.py from an importer output (--in, else
$VIEWER_TEST_MODEL, else the demo machine examples/nema-bracket, built once in a temp dir) and serves it like the stack
(viewer_stack_server.py, /<id>/). The part to click defaults to the biggest distinct part of parts.json, the module to isolate to the
module holding the most distinct parts (but not all of them); a model with no such module (the demo: one group) has the isolation
check skipped with an [info] line. The page is in its default language (ru); every text the checks expect comes from i18n.mjs.
  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_site.py [--url http://localhost:8765/ | --in <importer output>] \
      [--shots <dir>] [--part <part id>] [--module <module prefix or group node name>]
(first run: `... --with playwright playwright install chromium` inside the same uv invocation, or run `playwright install chromium` once)
LOOK at every PNG. Exit code 1 on any failed check.
"""
import argparse, json, shutil, tempfile, sys, urllib.request
from collections import Counter
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import build, model_dir, serve_stack, ui_texts  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--url", default=None, help="a served site (default: build one from --in and serve it)")
ap.add_argument("--in", dest="src", default=None, help="importer output dir (default: $VIEWER_TEST_MODEL, else the demo machine)")
ap.add_argument("--shots", default=None, help="keep screenshots here (default: a temp dir; screenshots never go in the project)")
ap.add_argument("--part", default=None, help="a part id to click (default: the biggest distinct part)")
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--module", default=None, help="module prefix or group node name to isolate (default: the module with the most distinct parts)")
a = ap.parse_args()
T = ui_texts("ru")
srv = tmp = None
if a.url:
    PARTS = json.loads(urllib.request.urlopen(a.url.rstrip("/") + "/parts.json").read())["parts"]
else:
    src = model_dir(a.src)
    tmp = Path(tempfile.mkdtemp(prefix="site_test_"))
    r = build(src, tmp / "demo-site", "--flash", "none", "--kit", "none", "--feedback", "none")
    if r.returncode:
        sys.exit("build.py failed:\n" + (r.stderr or r.stdout)[-2000:])
    srv, base = serve_stack(tmp)
    a.url = base + "/demo-site/"
    PARTS = json.loads((tmp / "demo-site" / "parts.json").read_text(encoding="utf-8"))["parts"]
a.url = a.url.rstrip("/") + "/"
DISTINCT = [p for p in PARTS if not p.get("part_id")]
if not a.part:
    a.part = max(DISTINCT, key=lambda p: p.get("volume_mm3") or 0)["id"]
PART = next(p for p in PARTS if p["id"] == a.part)
if not a.module:
    mods = Counter(p.get("module") for p in DISTINCT if p.get("module"))
    a.module = next((m for m, n in mods.most_common() if n < len(DISTINCT)), None)
HAS_DRAW = any(p.get("drawing") for p in PARTS)
HAS_DENSITY = any(p.get("density") for p in PARTS)
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="site_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
rows, ok_all = [], True
# real GPU (Metal on a Mac) so frame times mean something; --swiftshader for CI boxes without one (then fps is not judged)
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if "--swiftshader" in sys.argv else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)


def head(url):
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req) as r:
            return r.status, int(r.headers.get("content-length") or 0)
    except Exception as e:  # noqa
        return getattr(e, "code", 0), 0


def run(p, tag, viewport, mobile):
    b = p.chromium.launch(args=GPU_ARGS)
    ctx = b.new_context(viewport=viewport, device_scale_factor=1, is_mobile=mobile, has_touch=mobile)
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(a.url.rstrip("/") + "/?app=0", wait_until="load")   # ?app=0: no app iframe (a machine's 40 MB app would only slow a model test down)
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    err = page.evaluate("window.__viewer.error")
    check(f"{tag}: model loads", not err, str(err or page.inner_text('#load')))
    page.wait_for_timeout(1500)
    page.screenshot(path=str(shots / f"{tag}_1_whole.png"))
    st = page.evaluate("window.__viewer.state()")
    check(f"{tag}: camera fitted (dist>0, upright)", st["dist"] > 100 and abs(st["pitch"]) < 1.4, f"dist={st['dist']:.0f} target={[round(x) for x in st['target']]}")

    # CAD look: baked edges load after the model (non-blocking), toggles work and are remembered
    has_edges = page.evaluate("!!(window.__viewer.config && window.__viewer.config.edges)")
    if has_edges:
        page.wait_for_function("window.__viewer.edges && (window.__viewer.edges.loaded || window.__viewer.edges.error)", timeout=120000)
        ed = page.evaluate("window.__viewer.edges")
        check(f"{tag}: edges loaded (line objects share per-part geometry)", ed.get("loaded") and ed["lines"] > 0 and ed["geos"] <= ed["lines"], json.dumps(ed))
    if mobile and page.evaluate("window.__viewer.state().panelCollapsed"):
        page.click("#pHead")
    for bid, key in (("#bEdges", "edges"), ("#bAO", "ao"), ("#bHC", "hc")):
        before = page.evaluate("window.__viewer.view.get()")[key]
        page.click(bid); page.wait_for_timeout(150)
        after = page.evaluate("window.__viewer.view.get()")[key]
        check(f"{tag}: toggle {key} flips and is stored", after != before and json.loads(page.evaluate("localStorage.getItem('assembly.view')"))[key] == after, f"{before} -> {after}")
        page.click(bid); page.wait_for_timeout(150)
    page.screenshot(path=str(shots / f"{tag}_0_look.png"))
    # module isolated through the real UI: tree row -> card -> "only this"
    if mobile and page.evaluate("window.__viewer.state().panelCollapsed"):
        page.click("#pHead")
    n = a.module and (page.evaluate(f"window.__viewer.nodeByDrawing({json.dumps(a.module + '.00.000')})")
                      or page.evaluate(f"window.__viewer.nodeByDrawing({json.dumps(a.module)})"))   # drawing-numbered module or a named group
    if not n:
        print(f"[info] {tag}: the model has no module that holds only part of the machine: isolation not checked", flush=True)
    else:
        page.click(f'#tree .tr[data-n="{n[0]}"] .lbl')
        page.wait_for_timeout(300)
        if mobile and page.evaluate("window.__viewer.state().infoCollapsed"):
            page.click("#infoHead")
        page.click("#infoOnly")
        page.wait_for_timeout(1200)
        check(f"{tag}: module {a.module} isolated", page.evaluate("window.__viewer.state().hidden.length") > 0)
        page.screenshot(path=str(shots / f"{tag}_2_module.png"))

    # real click on a part -> card
    page.evaluate("window.__viewer.showAll()")
    page.evaluate("window.__viewer.select(null)")
    part = a.part
    page.evaluate(f"window.__viewer.select('{part}', true)")
    page.wait_for_timeout(500)
    page.wait_for_function("!window.__viewer.state().flying", timeout=30000)   # slow software GL: let the camera fly finish before scanning
    page.evaluate("window.__viewer.select(null)")
    page.wait_for_timeout(200)
    pos = page.evaluate(f"window.__viewer.partScreen('{part}')")
    pick = page.evaluate(f"window.__viewer.pick({pos['x']}, {pos['y']})")
    # the centre of a part can be covered by a neighbour; scan a few offsets for a point that hits it
    hit = page.evaluate("""(id) => { const v = window.__viewer;
      for (let y = 20; y < innerHeight - 20; y += 12) for (let x = 20; x < innerWidth - 20; x += 12) { const r = v.pick(x, y); if (r && r.part === id && document.elementFromPoint(x, y) === v.canvas) return [x, y]; }
      return null; }""", part)
    check(f"{tag}: part visible on screen at framed view", hit is not None, f"pick centre -> {pick and pick['part']}")
    if hit:
        if mobile:
            page.touchscreen.tap(*hit)
        else:
            page.mouse.click(*hit)
        page.wait_for_timeout(500)
        st = page.evaluate("window.__viewer.state()")
        check(f"{tag}: click opens card of the clicked part", st["infoOpen"] and st["selected"] and st["selected"]["part"] == part, json.dumps(st["selected"], ensure_ascii=False))
        if mobile and st["infoCollapsed"]:
            page.click("#infoHead"); page.wait_for_timeout(300)
        txt = page.inner_text("#info")
        want = [T["size"], " " + T["mm"]] + ([T["drawing"], PART["drawing"], T["kind"]] if HAS_DRAW and PART.get("drawing") else []) \
            + ([T["mass"]] if PART.get("volume_mm3") and (PART.get("density") or not HAS_DENSITY) else [])
        check(f"{tag}: card shows drawing, kind (when the model has drawings), size, mass", all(w in txt for w in want),
              f"missing {[w for w in want if w not in txt]}: " + txt.replace("\n", " | ")[:200])
        step, stl = page.get_attribute("#dlStep", "href"), page.get_attribute("#dlStl", "href")
        s1, s2 = head(a.url + step), head(a.url + stl)
        check(f"{tag}: STEP/STL links 200", s1[0] == 200 and s2[0] == 200 and s1[1] > 0 and s2[1] > 0, f"{step} {s1}, {stl} {s2}")
        # hide part / hide all such / hash
        n_hidden0 = len(page.evaluate("window.__viewer.state().hidden"))
        page.click("#infoHide"); page.wait_for_timeout(200)
        n_hidden1 = len(page.evaluate("window.__viewer.state().hidden"))
        page.click("#infoHide"); page.wait_for_timeout(200)
        check(f"{tag}: hide / unhide part", n_hidden1 == n_hidden0 + 1 and len(page.evaluate("window.__viewer.state().hidden")) == n_hidden0)
        page.wait_for_timeout(700)
        page.screenshot(path=str(shots / f"{tag}_3_card.png"))
        h = page.evaluate("location.hash")
        check(f"{tag}: hash carries cam and sel", "cam=" in h and ("sel=" + part) in h, h[:120])
    # double click / tap re-centres pivot and never hides
    if hit and not mobile:
        page.evaluate("window.__viewer.setCam({target: [0, 0, 0]})"); page.wait_for_timeout(600)
        hit = page.evaluate("""(id) => { const v = window.__viewer;
          for (let y = 20; y < innerHeight - 20; y += 12) for (let x = 20; x < innerWidth - 20; x += 12) { const r = v.pick(x, y); if (r && r.part === id && document.elementFromPoint(x, y) === v.canvas) return [x, y]; }
          return null; }""", part) or hit
        before = page.evaluate("window.__viewer.state()")
        page.mouse.dblclick(*hit); page.wait_for_timeout(900)
        after = page.evaluate("window.__viewer.state()")
        moved = sum(abs(x - y) for x, y in zip(before["target"], after["target"]))
        check(f"{tag}: double-click re-centres, hides nothing", moved > 1 and len(after["hidden"]) == 0, f"target moved {moved:.0f} mm")
    # hash round trip
    page.goto(a.url.rstrip("/") + "/?app=0#hide=" + part, wait_until="load")
    page.wait_for_function("window.__viewer && window.__viewer.ready", timeout=120000)
    hid = page.evaluate("window.__viewer.state().hidden.length")
    check(f"{tag}: #hide=<part id> hides its instances", hid >= 1, f"{hid} nodes hidden")
    # frame time
    page.evaluate("window.__viewer.showAll()"); page.evaluate("window.__viewer.setCam({yaw: -0.6, pitch: 0.4})")
    page.wait_for_timeout(500)
    fps = page.evaluate("""async () => { const v = window.__viewer, a = v.state(), n = 60, t0 = performance.now();
      for (let i = 0; i < n; i++) { v.setCam({ yaw: a.yaw + i * 0.02 }); await new Promise((r) => requestAnimationFrame(r)); }
      return { msPerFrame: (performance.now() - t0) / n }; }""")
    stats = page.evaluate("window.__viewer.stats")
    gpu = page.evaluate("""() => { const gl = document.createElement('canvas').getContext('webgl2'), e = gl.getExtension('WEBGL_debug_renderer_info'); return gl.getParameter(e.UNMASKED_RENDERER_WEBGL); }""")
    bench = fps
    print(f"[info] {tag}: {gpu}: {fps['msPerFrame']:.1f} ms/frame while orbiting the whole machine, {stats}", flush=True)
    if "SwiftShader" not in gpu:
        meshes = page.evaluate("window.__viewer.nodes.filter((o) => o.isMesh).length")
        check(f"{tag}: >= 30 fps orbiting all {meshes} meshes ({gpu.split(',')[1].strip() if ',' in gpu else gpu})", fps["msPerFrame"] < 33.4, f"{fps['msPerFrame']:.1f} ms/frame (vsync-capped at 16.7)")
    if page.evaluate("!!(window.__viewer.config && window.__viewer.config.cycle)"):   # cycle machine (build.py --motion): bar, scrub moves parts, #t= freezes, play advances
        page.wait_for_function("window.__viewer.cycle", timeout=15000)
        cy = page.evaluate("window.__viewer.cycle.state()")
        check(f"{tag}: cycle bar + groups bound", page.evaluate("!!document.getElementById('cyclebar')") and len(cy["groups"]) > 0 and all(g["parts"] > 0 for g in cy["groups"]), json.dumps([(g["name"], g["parts"]) for g in cy["groups"]]))
        w = "JSON.stringify(window.__viewer.nodes.filter(o=>o.isMesh).map(o=>{o.updateWorldMatrix(true,false);return Array.from(o.matrixWorld.elements)}))"
        page.evaluate("window.__viewer.cycle.setTime(0.0)"); a0 = page.evaluate(w); page.evaluate(f"window.__viewer.cycle.setTime({cy['period'] * 0.5})"); a1 = page.evaluate(w)
        check(f"{tag}: scrubbing moves the model", a0 != a1)
        page.evaluate("window.__viewer.cycle.setTime(0.0)"); page.evaluate(f"window.__viewer.cycle.setTime({cy['period']})"); a2 = page.evaluate(w)
        check(f"{tag}: cycle loops (t = period == t = 0)", a0 == a2)
        page.evaluate("window.__viewer.cycle.play()"); page.wait_for_timeout(600); t1 = page.evaluate("window.__viewer.cycle.state().t")
        check(f"{tag}: play advances t", t1 != 0 and page.evaluate("window.__viewer.cycle.state().playing"), f"t={t1:.2f}")
        page.evaluate("window.__viewer.cycle.pause()"); page.wait_for_timeout(700)
        check(f"{tag}: paused time is in the hash (#t=)", "t=" in page.evaluate("location.hash") and not page.evaluate("window.__viewer.cycle.state().playing"), page.evaluate("location.hash")[:80])
    check(f"{tag}: console has no errors", not errs, "; ".join(errs[:3]))
    b.close()
    return bench


try:
    with sync_playwright() as p:
        bd = run(p, "desktop", {"width": 1440, "height": 900}, False)
        bm = run(p, "phone", {"width": 390, "height": 844}, True)
finally:
    if srv:
        srv.shutdown()
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(x.replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
