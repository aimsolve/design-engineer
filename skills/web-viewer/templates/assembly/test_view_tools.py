#!/usr/bin/env python3
"""Real-browser check of the three viewer tools of the view section: standard views, the measuring grid, the ruler.

Builds a site from importer output with THIS template (build.py --flash none), serves it under /<project>/ the way the stack does
(viewer_stack_server.serve_stack) and intercepts /kit/** and /model.json so no router is needed, then drives the page through the
same door a client uses: the buttons in #panel and real mouse clicks on the canvas. The model: --in, else $VIEWER_TEST_MODEL, else the
demo machine (examples/nema-bracket, built once in a temp dir); the part measured on defaults to its biggest distinct part with one
instance. The page is in its default language (ru); every UI text the checks expect comes from i18n.mjs (views.*, grid.*, meas.*,
kit.scopes.*, reset, hc).

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_view_tools.py \
      [--in <importer output>] [--part <part id>] [--swiftshader] [--shots <dir>]
(first run: `... --with playwright playwright install chromium`)

What is checked, by tool:
  [views] #panel > #secView has a row #viewsRow with seven buttons (bViewFront «views.front» … bViewIso «views.iso»). Pressing one puts
       the camera on that world axis (front +Z, back −Z, right +X, left −X, top +Y, bottom −Y, iso front-left-above) AND refits the
       distance: the machine is not blank, covers a sensible share of the canvas, is wholly inside the viewport, and its projected
       silhouette has the aspect of the model's own extents for that direction (front/back X×Y, left/right Z×Y, top/bottom X×Z, so
       the top view also shows it the right way up). Front and back are not the same picture. __viewer.setView(name) does the same.
       The button of the view the camera is at carries class "on"; after orbiting away no button is "on".
  [grid] A switch #bGrid «grid.on», off by default. On: the picture gains a grid, #gridRow appears with #gridStep (auto/1/5/…/1000 mm)
       and #gridSize with the machine's overall size; __viewer.grid = {on, step, size}, __viewer.setGrid(on, step). A finer step puts
       visibly more lines on the screen (counted from the rendered pixels, not from an internal counter). Off removes it completely —
       the picture goes back to the pixels it had. On/off and step survive a reload. A click on the machine still opens its card, a
       click on the grid does not.
  [ruler] A switch #bMeasure «meas.on», off by default, with #measRow (#measOut, #measClear «meas.clear»). Two clicks on the model give
       the distance between the two points: a line in the 3D view and the distance in mm plus how much of it is horizontal and how much
       is height, in #measOut (NOT world ΔX/ΔY/ΔZ: the viewer's Y is up, which is the CAD model's Z for a --up z project, so axis labels
       there would name the wrong axes). The expected value is derived
       independently, from the world points the (already working) picker reports for the very same two pixels — the readout must match
       to within 0.5 mm, and the two points must lie in the clicked part's own bounding box (__viewer.box). A third click starts a new
       measurement; the clear button and switching off clear the line and the readout; while the ruler is on clicks do not select
       parts, and after switching it off they select again.
  [reg] Nothing that works may break: the four download buttons (kit.scopes), a tree click still opens the card, the reset-view
        button still works, and the console stays clean through every flow above.
  [phone] 390x844: the seven view buttons, the grid and the ruler switches are all reachable in the panel and the page does not
        scroll sideways.
Exit code 1 on any failed check.
"""
import argparse, atexit, json, math, re, shutil, subprocess, sys, tempfile, urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import model_dir, non_background_share, parts_of, png_pixels, serve_stack, ui_texts  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="src", default=None, help="importer output dir (model.glb, parts.json, parts/); default: $VIEWER_TEST_MODEL, else the demo machine")
ap.add_argument("--part", default=None, help="a part to measure on (two points on its surface); default: the biggest distinct part with one instance")
ap.add_argument("--build", default=str(HERE / "build.py"), help="build.py to test (default: this template)")
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
a.src = str(model_dir(a.src))
if not a.part:
    # the biggest distinct part with ONE instance: selecting it frames exactly that part (with several the camera fits all of them, and
    # one instance is seen end-on, where the 12 px snap budget below, computed at the camera's distance, no longer holds)
    one = [q for q in parts_of(a.src) if not q.get("part_id") and (q.get("instances") or 1) == 1] or [q for q in parts_of(a.src) if not q.get("part_id")]
    a.part = max(one, key=lambda q: q.get("volume_mm3") or 0)["id"]
T = ui_texts("ru")
MM = T["mm"]
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="views_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
PROJECT = "views-proj"
rows, ok_all = [], True

# name -> (button id, exact label, camera position relative to the model centre in world axes)
VIEWS = [("bViewFront", T["views"]["front"], "front", (0, 0, 1)),
         ("bViewBack", T["views"]["back"], "back", (0, 0, -1)),
         ("bViewLeft", T["views"]["left"], "left", (-1, 0, 0)),
         ("bViewRight", T["views"]["right"], "right", (1, 0, 0)),
         ("bViewTop", T["views"]["top"], "top", (0, 1, 0)),
         ("bViewBottom", T["views"]["bottom"], "bottom", (0, -1, 0)),
         ("bViewIso", T["views"]["iso"], "iso", None)]
VIEW_IDS = [v[0] for v in VIEWS]
# which world extent is the screen width / the screen height for an orthogonal view (0=X, 1=Y, 2=Z)
SILHOUETTE = {"front": (0, 1), "back": (0, 1), "left": (2, 1), "right": (2, 1), "top": (0, 2), "bottom": (0, 2)}
GRID_OPTIONS = [("auto", T["grid"]["auto"])] + [(n, T["grid"]["cell"].replace("{n}", n)) for n in ("1", "5", "10", "25", "50", "100", "250", "500", "1000")]
PANEL_DL = [T["kit"]["scopes"][k] for k in ("print", "laser", "bought", "step")]
GRID, RULER, CLEAR, RESET, HC = T["grid"]["on"], T["meas"]["on"], T["meas"]["clear"], T["reset"], T["hc"]
SIZE_WORD = T["grid"]["size"].split("{s}")[0].strip().rstrip(":")            # the size line's label
_split = T["meas"]["split"]                                                      # "<horizontal word> {h} · <height word> {v} mm"
SPLIT_WORDS = (_split[:_split.index("{h}")].strip(), _split[_split.index("{h}") + 3:_split.index("{v}")].strip(" ·"))
DEC = T["decimal"]
TOL_MM = 0.5


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- site: built with this template, served under /<project>/
root = Path(tempfile.mkdtemp(prefix="views_site_"))
atexit.register(lambda: shutil.rmtree(root, ignore_errors=True))   # a viewer test once filled the disk with left-over sites
b_site = subprocess.run([sys.executable, a.build, "--in", a.src, "--out", str(root / PROJECT), "--flash", "none"], capture_output=True, text=True)
if b_site.returncode:
    sys.exit("build.py failed:\n" + b_site.stderr[-2000:])
srv, BASE = serve_stack(root)
atexit.register(srv.shutdown)


def fake_router(page):
    """/kit/** and /model.json, so the page's own probes do not 404 into the console (no router behind this server)."""
    def kit(route):
        path = urllib.parse.unquote(urllib.parse.urlparse(route.request.url).path)
        if path.endswith("/status"):
            body = {"enabled": True, "fresh": True, "building": False, "since": None, "parts": [], "nodes": [],
                    "scopes": ["print", "laser", "bought", "step"], "off": {}}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))
        route.fulfill(status=200, body=b"PK\x03\x04 stub", headers={"Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="kit.zip"'})
    page.route("**/kit/**", kit)
    page.route("**/model.json", lambda r: r.fulfill(status=200, content_type="application/json", body='{"v": 0, "building": false}'))


def open_page(b, viewport, mobile=False):
    ctx = b.new_context(viewport=viewport, device_scale_factor=1, is_mobile=mobile, has_touch=mobile, accept_downloads=True)
    ctx.add_init_script("try { localStorage.setItem('assembly.sec.%s.dl', '0') } catch (e) {}" % PROJECT)   # the downloads section is folded by default
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    fake_router(page)
    page.goto(f"{BASE}/{PROJECT}/?app=0", wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=180000)
    page.wait_for_timeout(1200)
    return ctx, page, errs


def wait_still(page, ms=450):
    try:
        page.wait_for_function("!window.__viewer.state().flying", timeout=30000)
    except Exception:  # noqa
        pass
    page.wait_for_timeout(ms)


# ---------------------------------------------------------------- pixels (the client's own eyes)
def region_png(page, clip, name=None):
    """The part of the 3D view no card covers, exactly as the client sees it (no overlay is hidden: the ruler may draw its line on top)."""
    png = page.screenshot(clip=clip)
    if name:
        (shots / name).write_bytes(png)
    return png


def canvas_png(page, name=None):
    """A screenshot of the 3D canvas alone: every overlay (panel, cards, bars) hidden for the shot."""
    page.evaluate("document.querySelectorAll('body > *:not(#stage)').forEach((e) => { e.dataset.vis = e.style.visibility; e.style.visibility = 'hidden'; })")
    page.wait_for_timeout(260)
    png = page.screenshot()
    page.evaluate("document.querySelectorAll('body > *:not(#stage)').forEach((e) => { e.style.visibility = e.dataset.vis || ''; })")
    if name:
        (shots / name).write_bytes(png)
    return png


def px_change(x, y, step=4):
    """Share of sampled pixels that differ between two screenshots of the same size."""
    wx, hx, cx, rx = png_pixels(x)
    wy, hy, cy, ry = png_pixels(y)
    if (wx, hx) != (wy, hy):
        return 1.0
    n = d = 0
    for v in range(0, hx, step):
        ra, rb = rx[v], ry[v]
        for u in range(0, wx, step):
            i, j = u * cx, u * cy
            n += 1
            if max(abs(ra[i + k] - rb[j + k]) for k in range(3)) > 24:
                d += 1
    return d / max(1, n)


def px_count(x, y, step=2):
    """How many sampled pixels differ between two screenshots (a thin line changes few: a share would drown it)."""
    wx, hx, cx, rx = png_pixels(x)
    wy, hy, cy, ry = png_pixels(y)
    if (wx, hx) != (wy, hy):
        return 10 ** 9
    d = 0
    for v in range(0, hx, step):
        ra, rb = rx[v], ry[v]
        for u in range(0, wx, step):
            i, j = u * cx, u * cy
            if max(abs(ra[i + k] - rb[j + k]) for k in range(3)) > 24:
                d += 1
    return d


def changed_px(x, y, box, step=6):
    """Pixels (client px) inside `box` = (x0, y0, x1, y1) that differ between the two shots."""
    wx, hx, cx, rx = png_pixels(x)
    wy, hy, cy, ry = png_pixels(y)
    out = []
    if (wx, hx) != (wy, hy):
        return out
    x0, y0, x1, y1 = box
    for v in range(max(0, int(y0)), min(hx, int(y1)), step):
        ra, rb = rx[v], ry[v]
        for u in range(max(0, int(x0)), min(wx, int(x1)), step):
            i, j = u * cx, u * cy
            if max(abs(ra[i + k] - rb[j + k]) for k in range(3)) > 24:
                out.append((u, v))
    return out


def ink_box(png, thresh=24, minrun=3):
    """Bounding box of the pixels the machine actually paints (not of its bounding box, which overshoots a long thin machine)."""
    from collections import Counter
    w, h, ch, rws = png_pixels(png)
    samp = Counter(bytes(rws[v][u * ch:u * ch + 3]) for v in range(0, h, 4) for u in range(0, w, 4))
    bg = samp.most_common(1)[0][0]
    cols, rwct = [0] * w, [0] * h
    for v in range(0, h, 2):
        r = rws[v]
        for u in range(0, w, 2):
            i = u * ch
            if max(abs(r[i + k] - bg[k]) for k in range(3)) > thresh:
                cols[u] += 1
                rwct[v] += 1
    xs = [u for u in range(w) if cols[u] >= minrun]
    ys = [v for v in range(h) if rwct[v] >= minrun]
    if not xs or not ys:
        return None
    return {"x0": xs[0], "x1": xs[-1], "y0": ys[0], "y1": ys[-1], "w": w, "h": h}


def line_density(png, step=3):
    """Vertical edges per sampled row: a finer grid draws more lines, so this number grows. Read from the picture only."""
    w, h, ch, rws = png_pixels(png)
    ys = range(0, h, step)
    t = 0
    for v in ys:
        r = rws[v]
        prev = r[0:3]
        for u in range(1, w):
            cur = r[u * ch:u * ch + 3]
            if max(abs(cur[k] - prev[k]) for k in range(3)) > 10:
                t += 1
            prev = cur
    return t / max(1, len(list(ys)))


# ---------------------------------------------------------------- viewer helpers (never throw: a missing API is a failed check)
SILH_JS = """() => { const v = window.__viewer, c = v.camera; const b = v.box(0); if (!b || !c) return null;
  const mn = b[0], mx = b[1], pts = [];
  for (let i = 0; i < 8; i++) pts.push([i & 1 ? mx[0] : mn[0], i & 2 ? mx[1] : mn[1], i & 4 ? mx[2] : mn[2]]);
  let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
  for (const p of pts) { const q = c.position.clone().set(p[0], p[1], p[2]).project(c);
    const sx = ((q.x + 1) / 2) * innerWidth, sy = ((1 - q.y) / 2) * innerHeight;
    x0 = Math.min(x0, sx); x1 = Math.max(x1, sx); y0 = Math.min(y0, sy); y1 = Math.max(y1, sy); }
  const fwd = c.position.clone(); c.getWorldDirection(fwd);
  return { x0, y0, x1, y1, w: innerWidth, h: innerHeight, fwd: fwd.toArray(),
           size: [mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]],
           centre: [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2] }; }"""


def silhouette(page):
    return page.evaluate(SILH_JS)


def set_view(page, name):
    r = page.evaluate("""(n) => { const v = window.__viewer; if (!v || typeof v.setView !== "function") return { err: "no __viewer.setView" };
      try { v.setView(n); } catch (e) { return { err: String(e) }; } return { ok: true }; }""", name)
    if r.get("ok"):
        wait_still(page)
    return r


def grid_state(page):
    return page.evaluate("""() => { const g = window.__viewer && window.__viewer.grid; return g == null ? { err: "no __viewer.grid" } : { g }; }""")


def set_grid(page, on, step=None):
    r = page.evaluate("""([on, st]) => { const v = window.__viewer; if (!v || typeof v.setGrid !== "function") return { err: "no __viewer.setGrid" };
      try { st === null ? v.setGrid(on) : v.setGrid(on, st); } catch (e) { return { err: String(e) }; } return { ok: true }; }""", [on, step])
    page.wait_for_timeout(500)
    return r


def meas_state(page):
    return page.evaluate("""() => { const m = window.__viewer && window.__viewer.measure; return m == null ? { err: "no __viewer.measure" } : { m }; }""")


def on_classes(page):
    return page.evaluate("""(ids) => ids.filter((i) => { const e = document.getElementById(i); return e && e.classList.contains("on"); })""", VIEW_IDS)


def here(page, *sel):
    """Every selector is in the DOM (a negative check about an element that does not exist asserts nothing)."""
    return all(page.locator(x).count() == 1 for x in sel)


def vis(page, sel):
    loc = page.locator(sel)
    return loc.count() > 0 and loc.first.is_visible()


def btn_label(page, bid):
    return page.evaluate("""(i) => { const e = document.getElementById(i); return e ? { tag: e.tagName, txt: (e.textContent || "").trim(), inView: !!e.closest("#secView"), inRow: !!e.closest("#viewsRow") } : null; }""", bid)


def part_hits(page, node, xmin):
    """Two pixels on ONE instance (node index) of a part, as far apart as possible, with the world point the viewer's own picker reports."""
    return page.evaluate("""([n, xmin]) => { const v = window.__viewer, got = [];
      for (let y = 40; y < innerHeight - 40; y += 8) for (let x = xmin; x < innerWidth - 30; x += 8) {
        const r = v.pick(x, y); if (r && r.n === n) got.push([x, y, r.point]); }
      if (got.length < 3) return null;
      let best = null;
      for (const p of got) for (const q of got) { const d = Math.hypot(p[0] - q[0], p[1] - q[1]); if (!best || d > best.d) best = { d, a: p, b: q }; }
      const mid = got[Math.floor(got.length / 2)];
      return { a: best.a, b: best.b, c: mid, n: got.length }; }""", [node, int(xmin)])


SNAP_PX = 12   # index.html snapToVertex: a corner of the hit triangle this close to the cursor wins


def snap_facts(page, x, y, point):
    """For the pixel (x, y): how far the nearest vertex of the mesh under it projects from the cursor, whether `point` IS one of that
    mesh's vertices, and the raw ray/surface intersection the picker reports. All derived in the page from the geometry itself."""
    return page.evaluate("""([x, y, point]) => { const v = window.__viewer, r = v.pick(x, y); if (!r) return null;
      const o = v.nodes[r.n], pos = o.geometry.attributes.position, V = v.camera.position.constructor;
      let nd = 1e9, exact = false;
      for (let i = 0; i < pos.count; i++) {
        const w = new V(pos.getX(i), pos.getY(i), pos.getZ(i)).applyMatrix4(o.matrixWorld), s = w.clone().project(v.camera);
        nd = Math.min(nd, Math.hypot(((s.x + 1) / 2) * innerWidth - x, ((1 - s.y) / 2) * innerHeight - y));
        if (point && Math.abs(w.x - point[0]) < 1e-6 && Math.abs(w.y - point[1]) < 1e-6 && Math.abs(w.z - point[2]) < 1e-6) exact = true;
      }
      return { nearestPx: nd, exact, raw: r.point }; }""", [int(x), int(y), point])


def visible_vertex(page, node, xmin):
    """A vertex of `node` that is the frontmost surface at its own pixel, so a click there is a click on that corner of the part.
    Candidates are taken across the whole mesh, not only its first triangles. Returns {v: world point, s: [px, py]} or None."""
    return page.evaluate("""([n, xmin]) => { const v = window.__viewer, o = v.nodes[n], pos = o.geometry.attributes.position;
      const V = v.camera.position.constructor, W = innerWidth, H = innerHeight, cand = [];
      for (let i = 0; i < pos.count; i++) {
        const w = new V(pos.getX(i), pos.getY(i), pos.getZ(i)).applyMatrix4(o.matrixWorld), s = w.clone().project(v.camera);
        const x = ((s.x + 1) / 2) * W, y = ((1 - s.y) / 2) * H;
        if (s.z < -1 || s.z > 1 || x < xmin + 40 || x > W - 40 || y < 40 || y > H - 40) continue;
        cand.push({ w: w.toArray(), x: x, y: y });
      }
      const step = Math.max(1, Math.floor(cand.length / 80));
      for (let i = 0; i < cand.length; i += step) {
        const c = cand[i], px = Math.round(c.x), py = Math.round(c.y), r = v.pick(px, py);
        if (!r || r.n !== n) continue;
        if (Math.hypot(r.point[0] - c.w[0], r.point[1] - c.w[1], r.point[2] - c.w[2]) > 2) continue;   // something nearer covers it
        return { v: c.w, s: [px, py] };
      }
      return null; }""", [int(node), int(xmin)])


def px_mm(page):
    """Millimetres per screen pixel at the camera's distance (perspective, fov 35): the mm a SNAP_PX move can be worth."""
    return page.evaluate("""() => { const v = window.__viewer; return (2 * v.state().dist * Math.tan((35 * Math.PI) / 360)) / innerHeight; }""")


def bg_share(png, rgb=(0xee, 0xf2, 0xf5), near=10, step=3):
    """Share of sampled pixels within `near` of one colour — the viewer's normal page background, which high contrast (#bHC) replaces."""
    w, h, ch, rows = png_pixels(png)
    px = [rows[y][x * ch:x * ch + 3] for y in range(0, h, step) for x in range(0, w, step)]
    hit = sum(1 for q in px if max(abs(q[i] - rgb[i]) for i in range(3)) <= near)
    return hit / max(1, len(px))


def empty_px(page, xmin):
    return page.evaluate("""(xmin) => { const v = window.__viewer;
      for (let y = 40; y < innerHeight - 40; y += 11) for (let x = xmin; x < innerWidth - 30; x += 11) if (!v.pick(x, y)) return [x, y];
      return null; }""", int(xmin))


def dist(p, q):
    return math.dist(p, q)


def num_page(s):
    return s.replace("−", "-").replace("–", "-").replace(DEC, ".")


def read_out(page):
    txt = page.inner_text("#measOut") if page.locator("#measOut").count() else ""
    n = num_page(txt)
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*" + re.escape(MM), n)
    d = float(m[1]) if m else None
    split = []        # meas.split "horizontal … · height …": the viewer splits the distance that way, not into world ΔX/ΔY/ΔZ (see the header)
    for word in SPLIT_WORDS:
        mm = re.search(re.escape(word) + r"[^-\d]*(-?\d+(?:\.\d+)?)", n)
        split.append(float(mm[1]) if mm else None)
    return txt, d, split


# ---------------------------------------------------------------- the run
def desktop(b):
    ctx, page, errs = open_page(b, {"width": 1440, "height": 900})
    check("model loads", not page.evaluate("window.__viewer.error"), str(page.evaluate("window.__viewer.error") or ""))
    panel = page.evaluate("(() => { const r = document.getElementById('panel').getBoundingClientRect(); return { right: r.right }; })()")
    xmin = int(panel["right"] + 14)
    clip = {"x": xmin, "y": 60, "width": 1440 - xmin - 20, "height": 780}

    # ---- [reg] what already works, before anything new is touched
    for label in PANEL_DL:
        loc = page.locator("#panel").get_by_role("button", name=label, exact=True)
        try:
            loc.first.wait_for(timeout=15000)
        except Exception:  # noqa
            pass
        check(f"[reg] downloads: «{label}» in #panel", loc.count() == 1 and loc.first.is_visible(), f"{loc.count()} found")
    page.locator("#tree .tr .lbl").first.click()
    page.wait_for_timeout(600)
    check("[reg] a tree click opens the card", page.evaluate("window.__viewer.state().infoOpen") and page.locator("#info").is_visible())
    page.click("#infoX"); page.wait_for_timeout(200)
    page.evaluate("window.__viewer.setCam({ yaw: 1.9, pitch: -0.8, dist: 30000 })"); page.wait_for_timeout(300)
    page.click("#bReset"); wait_still(page)
    st = page.evaluate("window.__viewer.state()")
    check(f"[reg] «{RESET}» flies back to the fitted home view", abs(st["yaw"] + 0.6) < 0.05 and abs(st["pitch"] - 0.4) < 0.05 and st["dist"] > 100,
          f"yaw={st['yaw']:.2f} pitch={st['pitch']:.2f} dist={st['dist']:.0f}")
    home_png = canvas_png(page, "reg_home.png")
    check("[reg] the home view draws the machine", 0.08 <= non_background_share(home_png) <= 0.95, f"{non_background_share(home_png):.3f} of the canvas")

    # ---- [views] the row and its seven buttons
    check("[views] #secView has a row #viewsRow", page.evaluate("!!document.querySelector('#panel #secView #viewsRow')"))
    ids = page.evaluate("""() => { const r = document.getElementById("viewsRow"); return r ? Array.from(r.querySelectorAll("button")).map((e) => [e.id, (e.textContent || "").trim()]) : null; }""")
    check("[views] #viewsRow holds exactly the seven view buttons in order",
          ids is not None and [i[0] for i in ids] == VIEW_IDS, json.dumps(ids, ensure_ascii=False))
    for bid, label, name, _ in VIEWS:
        d = btn_label(page, bid)
        check(f"[views] «{label}» is a button in #viewsRow (#{bid})",
              bool(d) and d["tag"] == "BUTTON" and d["txt"] == label and d["inRow"] and d["inView"], json.dumps(d, ensure_ascii=False))

    # ---- [views] pressing a button: the camera goes to that axis and the whole machine is in view
    pngs = {}
    for bid, label, name, direc in VIEWS:
        if page.locator("#" + bid).count() != 1:
            for what in ("camera looks along the axis", "the button is the only one «on»", "the machine is in the frame, not blank",
                         "the machine is wholly inside the viewport", "the view is refitted (the machine fills the frame)"):
                check(f"[views] «{label}»: {what}", False, f"no #{bid}")
            if name in SILHOUETTE:
                check(f"[views] «{label}»: the silhouette has the model's own proportions", False, f"no #{bid}")
            continue
        page.click("#" + bid); wait_still(page)
        s = silhouette(page)
        png = canvas_png(page, f"views_{name}.png"); pngs[name] = png
        if not s:
            check(f"[views] «{label}»: camera looks along the axis", False, "no __viewer.box(0)/camera")
            continue
        f = s["fwd"]
        if direc:
            want = [-direc[0], -direc[1], -direc[2]]
            dot = sum(f[i] * want[i] for i in range(3)) / max(1e-9, math.dist(f, (0, 0, 0)))
            check(f"[views] «{label}»: camera looks along the axis (position {direc} of the centre)", dot >= 0.999,
                  f"forward {[round(x, 3) for x in f]}, want {want}, cos={dot:.4f}")
        else:
            check(f"[views] «{T['views']['iso']}»: camera is front-left-above", f[0] > 0.25 and f[1] < -0.25 and f[2] < -0.25, f"forward {[round(x, 3) for x in f]}")
        on = on_classes(page)
        check(f"[views] «{label}»: its button is the only one with class «on»", on == [bid], json.dumps(on))
        share = non_background_share(png)
        check(f"[views] «{label}»: the machine is in the frame, not blank", 0.08 <= share <= 0.95, f"{share:.3f} of the canvas")
        ink = ink_box(png)
        inside = bool(ink) and ink["x0"] >= 2 and ink["y0"] >= 2 and ink["x1"] <= ink["w"] - 3 and ink["y1"] <= ink["h"] - 3
        check(f"[views] «{label}»: the machine is wholly inside the viewport, nothing cut off at an edge", inside,
              f"drawn x {ink['x0']}..{ink['x1']} of {ink['w']}, y {ink['y0']}..{ink['y1']} of {ink['h']}" if ink else "nothing drawn")
        fill = max((ink["x1"] - ink["x0"]) / ink["w"], (ink["y1"] - ink["y0"]) / ink["h"]) if ink else 0
        check(f"[views] «{label}»: the view is refitted (the machine fills the frame)", fill >= 0.4, f"fills {fill:.2f} of the canvas")
        if name in SILHOUETTE:
            iw, ih = SILHOUETTE[name]
            want = s["size"][iw] / max(1e-9, s["size"][ih])
            got = (s["x1"] - s["x0"]) / max(1e-9, s["y1"] - s["y0"])
            axis = "XYZ"[iw] + "×" + "XYZ"[ih]
            check(f"[views] «{label}»: the silhouette is {axis} of the model (right way up)", abs(got / want - 1) <= 0.08,
                  f"screen {got:.3f} vs model {want:.3f} ({axis} = {s['size'][iw]:.0f}×{s['size'][ih]:.0f} mm)")
    if "front" in pngs and "back" in pngs:
        ch = px_change(pngs["front"], pngs["back"])
        check(f"[views] «{T['views']['front']}» and «{T['views']['back']}» are not the same picture", ch > 0.02, f"{ch:.3f} of the pixels differ")

    # ---- [views] the API, and losing «on» when the client orbits away
    for bid, label, name, direc in VIEWS:
        r = set_view(page, name)
        if r.get("err"):
            check(f"[views] __viewer.setView(\"{name}\") puts the camera on the axis", False, r["err"])
            continue
        s = silhouette(page)
        f = s["fwd"] if s else [0, 0, 0]
        if direc:
            want = [-direc[0], -direc[1], -direc[2]]
            dot = sum(f[i] * want[i] for i in range(3)) / max(1e-9, math.dist(f, (0, 0, 0)))
            ok = dot >= 0.999
        else:
            ok = f[0] > 0.25 and f[1] < -0.25 and f[2] < -0.25
        check(f"[views] __viewer.setView(\"{name}\") puts the camera on the axis", ok, f"forward {[round(x, 3) for x in f]}")
        check(f"[views] setView(\"{name}\") marks #{bid} «on»", on_classes(page) == [bid], json.dumps(on_classes(page)))
    page.evaluate("window.__viewer.setCam({ yaw: window.__viewer.state().yaw + 0.5, pitch: 0.3 })"); page.wait_for_timeout(500)
    check("[views] after orbiting away no view button is «on»", here(page, *("#" + i for i in VIEW_IDS)) and on_classes(page) == [],
          json.dumps(on_classes(page)) if here(page, *("#" + i for i in VIEW_IDS)) else "the view buttons are not there")

    # the camera's yaw adds up while orbiting, so a view button must take the SHORT way, whatever number the yaw has reached
    page.evaluate("window.__viewer.setCam({ yaw: window.__viewer.state().yaw + 7, pitch: 0.35 })"); page.wait_for_timeout(400)
    travel = page.evaluate("""() => new Promise((res) => { const v = window.__viewer;
      document.getElementById("bViewFront").click();
      let prev = v.state().yaw, tot = 0, n = 0;
      const t = setInterval(() => { const s = v.state(); tot += Math.abs(s.yaw - prev); prev = s.yaw; n++;
        if ((!s.flying && n > 3) || n > 300) { clearInterval(t); res(tot); } }, 16); })""")
    wait_still(page)
    check(f"[views] «{T['views']['front']}» takes the short way round even after the client has orbited several turns",
          travel is not None and travel <= math.pi + 0.3, f"the camera turned {travel:.2f} rad on the way (at most π is sensible)")

    # ---- [grid] the grid
    page.click("#bReset"); wait_still(page)
    base_png = canvas_png(page, "grid_grid_off.png")
    g = grid_state(page)
    check(f"[grid] #bGrid «{GRID}» is a switch in #secView", page.evaluate("""() => { const e = document.getElementById("bGrid"); return !!e && !!e.closest("#secView"); }""")
          and (page.inner_text("#bGrid").strip() if page.locator("#bGrid").count() else "") == GRID,
          repr(page.inner_text("#bGrid").strip() if page.locator("#bGrid").count() else None))
    check("[grid] __viewer.grid says it is off by default", not g.get("err") and g["g"].get("on") is False, g.get("err") or json.dumps(g.get("g")))
    check("[grid] #gridRow is not there while the grid is off", here(page, "#bGrid") and not vis(page, "#gridRow"),
          "" if here(page, "#bGrid") else "no #bGrid")
    if page.locator("#bGrid").count() == 1:
        page.click("#bGrid"); page.wait_for_timeout(700)
    g = grid_state(page)
    check(f"[grid] «{GRID}» on -> __viewer.grid.on", not g.get("err") and g["g"].get("on") is True, g.get("err") or json.dumps(g.get("g")))
    on_png = canvas_png(page, "grid_grid_on.png")
    ch = px_change(base_png, on_png)
    check("[grid] a grid appears around the model (the picture gains lines)", ch > 0.02, f"{ch:.3f} of the pixels changed")
    check("[grid] #gridRow appears with the step select and the size line", vis(page, "#gridRow") and vis(page, "#gridStep") and vis(page, "#gridSize"),
          f"gridRow={vis(page, '#gridRow')} gridStep={vis(page, '#gridStep')} gridSize={vis(page, '#gridSize')}")
    opts = page.evaluate("""() => { const s = document.getElementById("gridStep"); return s ? Array.from(s.options).map((o) => [o.value, (o.textContent || "").trim()]) : null; }""")
    check("[grid] #gridStep offers " + " / ".join(x[1] for x in GRID_OPTIONS),
          opts == [list(x) for x in GRID_OPTIONS], json.dumps(opts, ensure_ascii=False))
    box = page.evaluate("window.__viewer.box(0)")
    size = [box[1][i] - box[0][i] for i in range(3)] if box else [0, 0, 0]
    gs = (g.get("g") or {}).get("size")
    check("[grid] __viewer.grid.size is the machine's overall size in mm",
          isinstance(gs, list) and len(gs) == 3 and all(abs(gs[i] - size[i]) <= 1 for i in range(3)),
          f"{gs} vs box(0) {[round(x, 1) for x in size]}")
    txt = page.inner_text("#gridSize") if page.locator("#gridSize").count() else ""
    nums = [int(x) for x in re.findall(r"\d+", txt)]
    check(f"[grid] #gridSize shows «{T['grid']['size'].replace('{s}', 'X × Y × Z ' + MM)}»",
          SIZE_WORD in txt and MM in txt and all(any(abs(n - round(s)) <= 1 for n in nums) for s in size), repr(txt))
    hc_on = page.evaluate("() => document.documentElement.classList.contains('hc')")
    if page.locator("#bHC").count() == 1 and not hc_on:
        page.click("#bHC"); page.wait_for_timeout(700)
        hc_png = canvas_png(page, "grid_grid_hc.png")
        left, was = bg_share(hc_png), bg_share(on_png)
        check(f"[grid] under «{HC}» the grid panes take the new page colour instead of staying the old grey",
              left <= max(0.05, was * 0.3), f"{left:.3f} of the view is still the viewer's normal grey, {was:.3f} of it was before the switch")
        page.click("#bHC"); page.wait_for_timeout(700)
    step_auto = (g.get("g") or {}).get("step")
    check(f"[grid] with «{T['grid']['auto']}» __viewer.grid.step is still a resolved number in mm",
          isinstance(step_auto, (int, float)) and not isinstance(step_auto, bool) and step_auto > 0,
          f"step={step_auto!r}, select={page.input_value('#gridStep') if page.locator('#gridStep').count() else None}")

    dens = {}
    for v in ("250", "10"):
        if page.locator("#gridStep").count():
            page.select_option("#gridStep", v); page.wait_for_timeout(700)
        gg = grid_state(page)
        check(f"[grid] choosing «{v} {MM}» sets __viewer.grid.step = {v}", not gg.get("err") and gg["g"].get("step") == int(v), gg.get("err") or json.dumps(gg.get("g")))
        dens[v] = line_density(canvas_png(page, f"grid_step_{v}.png"))
    check("[grid] a smaller step draws visibly more lines on the screen", dens.get("10", 0) > dens.get("250", 0) * 1.2,
          f"10 mm: {dens.get('10', 0):.1f} edges/row, 250 mm: {dens.get('250', 0):.1f}")
    r = set_grid(page, True, 25)
    gg = grid_state(page)
    check("[grid] __viewer.setGrid(true, 25) sets it from the API and the select follows",
          not r.get("err") and not gg.get("err") and gg["g"].get("on") is True and gg["g"].get("step") == 25
          and page.locator("#gridStep").count() == 1 and page.input_value("#gridStep") == "25",
          r.get("err") or gg.get("err") or json.dumps(gg.get("g")))

    # a click on the machine still opens its card; a click on the grid does not
    page.evaluate("window.__viewer.select(null)"); page.wait_for_timeout(200)
    cand = [list(q) for q in changed_px(base_png, on_png, (xmin, 60, 1440 - 340, 840))][:400]
    gpx = page.evaluate("""(ps) => { const v = window.__viewer; for (const p of ps) if (!v.pick(p[0], p[1])) return p; return null; }""", cand) if cand else None
    if check("[grid] a pixel that only the grid draws was found", bool(gpx), f"{gpx} of {len(cand)} candidates"):
        page.mouse.click(gpx[0], gpx[1]); page.wait_for_timeout(600)
        stg = page.evaluate("window.__viewer.state()")
        check("[grid] a click on the grid opens no card", not stg["infoOpen"] and stg["selected"] is None, json.dumps(stg["selected"], ensure_ascii=False))
    hit = page.evaluate("""([xmin]) => { const v = window.__viewer;
      for (let y = 40; y < innerHeight - 40; y += 9) for (let x = xmin; x < innerWidth - 30; x += 9) { const r = v.pick(x, y); if (r && r.part) return [x, y, r.part]; }
      return null; }""", [xmin])
    if hit:
        page.mouse.click(hit[0], hit[1]); page.wait_for_timeout(700)
        stg = page.evaluate("window.__viewer.state()")
        gon = (grid_state(page).get("g") or {}).get("on") is True
        check("[grid] with the grid on a click on the machine still opens its card",
              gon and stg["infoOpen"] and stg["selected"] and stg["selected"]["part"] == hit[2],
              json.dumps(stg["selected"], ensure_ascii=False) + ("" if gon else " (the grid is not on)"))
        page.click("#infoX"); page.wait_for_timeout(200)

    # the state survives a reload
    if page.locator("#gridStep").count():
        page.select_option("#gridStep", "50"); page.wait_for_timeout(500)
    page.reload(wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=180000)
    page.wait_for_timeout(1500)
    gg = grid_state(page)
    check("[grid] after a reload the grid is still on with the same step",
          not gg.get("err") and gg["g"].get("on") is True and gg["g"].get("step") == 50, gg.get("err") or json.dumps(gg.get("g")))
    check(f"[grid] after a reload #gridStep still says «50 {MM}»",
          page.locator("#gridStep").count() == 1 and page.input_value("#gridStep") == "50",
          page.input_value("#gridStep") if page.locator("#gridStep").count() else "no #gridStep")

    # off removes it completely: the picture goes back to what it was
    page.click("#bReset"); wait_still(page)
    with_grid = canvas_png(page, "grid_before_off.png")
    if page.locator("#bGrid").count() == 1:
        page.click("#bGrid"); page.wait_for_timeout(700)
    off_png = canvas_png(page, "grid_after_off.png")
    gg = grid_state(page)
    check(f"[grid] «{GRID}» off -> __viewer.grid.on false", not gg.get("err") and gg["g"].get("on") is False, gg.get("err") or json.dumps(gg.get("g")))
    check(f"[grid] «{GRID}» off -> #gridRow is gone", here(page, "#bGrid") and not vis(page, "#gridRow"),
          "" if here(page, "#bGrid") else "no #bGrid")
    back, gone = px_count(base_png, off_png), px_count(with_grid, off_png)
    check("[grid] switching it off removes the grid completely (the picture is the one from before)", gone > 400 and back < gone * 0.25,
          f"{back} px differ from the no-grid picture, {gone} px from the with-grid one")

    # ---- [ruler] the ruler
    page.click("#bReset"); wait_still(page)
    page.evaluate("window.__viewer.select(null)"); page.wait_for_timeout(200)
    check(f"[ruler] #bMeasure «{RULER}» is a switch in #secView",
          page.evaluate("""() => { const e = document.getElementById("bMeasure"); return !!e && !!e.closest("#secView"); }""")
          and (page.inner_text("#bMeasure").strip() if page.locator("#bMeasure").count() else "") == RULER,
          repr(page.inner_text("#bMeasure").strip() if page.locator("#bMeasure").count() else None))
    m = meas_state(page)
    check("[ruler] __viewer.measure says it is off by default, with no points",
          not m.get("err") and m["m"].get("on") is False and m["m"].get("points") == [] and m["m"].get("distance") is None,
          m.get("err") or json.dumps(m.get("m")))
    check("[ruler] while the ruler is off #measOut holds no measurement",
          here(page, "#measOut") and not re.search(r"\d", page.inner_text("#measOut") if page.locator("#measOut").count() else ""),
          repr(page.inner_text("#measOut") if page.locator("#measOut").count() else None))

    # frame the part so two pixels on it are far apart, then measure on it
    page.evaluate("window.__viewer.select(%s, true)" % json.dumps(a.part)); wait_still(page)
    page.evaluate("window.__viewer.select(null)"); page.wait_for_timeout(300)
    if page.locator("#info").count() and page.locator("#info").is_visible():
        page.click("#infoX"); page.wait_for_timeout(200)
    pn = page.evaluate("(id) => { const r = window.__viewer.parts().find(([p]) => p === id); return r ? r[1][0] : null; }", a.part)
    pbox = page.evaluate("(n) => window.__viewer.box(n)", pn) if pn is not None else None
    hits = part_hits(page, pn, xmin) if pn is not None else None
    if not check(f"[ruler] two pixels on «{a.part}» found, with the world point the picker reports", bool(hits), json.dumps(hits and hits["n"])):
        check("[reg] console has no errors through all of it", not errs, "; ".join(errs[:3]))
        ctx.close()
        return errs
    p1, p2, p3 = hits["a"][2], hits["b"][2], hits["c"][2]
    want = dist(p1, p2)

    if page.locator("#bMeasure").count() == 1:
        page.click("#bMeasure"); page.wait_for_timeout(600)
    m = meas_state(page)
    check(f"[ruler] «{RULER}» on -> __viewer.measure.on", not m.get("err") and m["m"].get("on") is True, m.get("err") or json.dumps(m.get("m")))
    check(f"[ruler] #measRow is in #secView with the readout #measOut and a «{CLEAR}» button #measClear",
          page.evaluate("""(clear) => { const r = document.getElementById("measRow"), o = document.getElementById("measOut"), c = document.getElementById("measClear");
            return !!r && !!r.closest("#secView") && !!o && r.contains(o) && !!c && r.contains(c) && c.tagName === "BUTTON" && (c.textContent || "").trim() === clear; }""", CLEAR),
          f"measRow={vis(page, '#measRow')} measOut={vis(page, '#measOut')} measClear={repr(page.inner_text('#measClear').strip() if page.locator('#measClear').count() else None)}")

    clean_png = region_png(page, clip, "ruler_clean.png")
    page.mouse.click(hits["a"][0], hits["a"][1]); page.wait_for_timeout(500)
    m1 = meas_state(page)
    pts1 = (m1.get("m") or {}).get("points") or []
    check("[ruler] the first click adds one point and no distance yet",
          len(pts1) == 1 and (m1.get("m") or {}).get("distance") is None, m1.get("err") or json.dumps(m1.get("m")))
    one_png = region_png(page, clip, "ruler_one.png")
    page.mouse.click(hits["b"][0], hits["b"][1]); page.wait_for_timeout(600)
    m2 = meas_state(page)
    pts = (m2.get("m") or {}).get("points") or []
    got_d = (m2.get("m") or {}).get("distance")
    check("[ruler] the second click gives two points and a distance",
          len(pts) == 2 and isinstance(got_d, (int, float)) and not isinstance(got_d, bool), m2.get("err") or json.dumps(m2.get("m")))
    if len(pts) == 2:
        mm = px_mm(page)
        snap_mm = SNAP_PX * mm * 1.5      # what a 12 px snap can be worth in mm at this distance (1.5: the snap may go along the view)
        off = max(dist(pts[0], p1), dist(pts[1], p2))
        check("[ruler] each measured point is the clicked pixel's own point, or a vertex within 12 px of it",
              off <= max(TOL_MM, snap_mm), f"{off:.2f} mm from the picker's point, a 12 px snap is worth up to {snap_mm:.2f} mm")
        for i, (xy, pt) in enumerate(((hits["a"], pts[0]), (hits["b"], pts[1]))):
            f = snap_facts(page, xy[0], xy[1], pt)
            if not check(f"[ruler] the page can say what is around click {i + 1}", bool(f), json.dumps(f)):
                continue
            check(f"[ruler] click {i + 1} landed either on a real vertex of that part or exactly on the surface under the cursor",
                  f["exact"] or dist(pt, f["raw"]) <= 0.01,
                  f"point {['%.2f' % c for c in pt]} is neither a vertex of the mesh nor its ray hit "
                  f"({dist(pt, f['raw']):.3f} mm away, nearest vertex {f['nearestPx']:.1f} px)")
        check("[ruler] __viewer.measure.distance is the distance between its own two points",
              abs(got_d - dist(pts[0], pts[1])) <= TOL_MM, f"{got_d:.2f} vs {dist(pts[0], pts[1]):.2f} mm")
        check("[ruler] the distance matches the independently derived one, within what a 12 px snap can move it",
              abs(got_d - want) <= max(TOL_MM, 2 * snap_mm), f"{got_d:.2f} vs {want:.2f} mm (snap budget {2 * snap_mm:.2f})")
        if pbox:
            diag = dist(pbox[0], pbox[1])
            inb = all(pbox[0][i] - 1 <= p[i] <= pbox[1][i] + 1 for p in pts for i in range(3))
            check("[ruler] both points lie in the clicked part's own bounding box, so the distance is within its diagonal",
                  inb and got_d <= diag + TOL_MM, f"distance {got_d:.1f} mm, part box diagonal {diag:.1f} mm, in box={inb}")
    two_png = region_png(page, clip, "ruler_two.png")
    drew = px_count(one_png, two_png)
    check("[ruler] a line is drawn between the two points in the 3D view", drew > 60, f"{drew} px of the 3D view changed")
    out, out_d, split = read_out(page)
    check("[ruler] #measOut shows the distance in mm, matching to 0.5 mm",
          out_d is not None and isinstance(got_d, (int, float)) and abs(out_d - got_d) <= TOL_MM, repr(out))
    other = "." if DEC == "," else ","
    check(f"[ruler] #measOut writes the number with the page language's decimal separator «{DEC}» (no «{other}»)",
          bool(re.search(r"\d" + re.escape(DEC) + r"\d", out)) and not re.search(r"\d" + re.escape(other) + r"\d", out), repr(out))
    if len(pts) == 2:
        dx, dy, dz = (pts[1][i] - pts[0][i] for i in range(3))
        want = [math.hypot(dx, dz), abs(dy)]
        ok = all(v is not None and abs(v - want[i]) <= TOL_MM for i, v in enumerate(split))
        check("[ruler] #measOut splits it into the horizontal and the vertical part, each matching to 0.5 mm", ok,
              f"read {split} vs {[round(x, 2) for x in want]} — {out!r}")
    check(f"[ruler] with a measurement taken «{CLEAR}» is there to press", vis(page, "#measClear"))
    stm = page.evaluate("window.__viewer.state()")
    check("[ruler] while the ruler is on a click does not select a part", not stm["infoOpen"] and stm["selected"] is None, json.dumps(stm["selected"], ensure_ascii=False))

    # measureAt: by screen coordinates, null off the model
    e = empty_px(page, xmin)
    if check("[ruler] a pixel off the model was found", bool(e), str(e)):
        r = page.evaluate("""([x, y]) => { const v = window.__viewer; if (typeof v.measureAt !== "function") return { err: "no __viewer.measureAt" };
          try { return { r: v.measureAt(x, y) }; } catch (ex) { return { err: String(ex) }; } }""", e)
        after = ((meas_state(page).get("m")) or {}).get("points") or []
        check("[ruler] measureAt off the model returns null and adds no point",
              not r.get("err") and r.get("r") is None and len(after) == 2, r.get("err") or f"{r.get('r')}, {len(after)} points")
    r = page.evaluate("""([x, y]) => { const v = window.__viewer; if (typeof v.measureAt !== "function") return { err: "no __viewer.measureAt" };
      try { return { r: v.measureAt(x, y) }; } catch (ex) { return { err: String(ex) }; } }""", [hits["c"][0], hits["c"][1]])
    m3 = meas_state(page)
    pts3 = (m3.get("m") or {}).get("points") or []
    got3 = r.get("r")
    check("[ruler] a third point starts a new measurement (measureAt returns the point)",
          not r.get("err") and isinstance(got3, list) and len(got3) == 3 and len(pts3) == 1
          and dist(got3, p3) <= TOL_MM and (m3.get("m") or {}).get("distance") is None,
          r.get("err") or f"{got3} -> {len(pts3)} points, distance {(m3.get('m') or {}).get('distance')}")

    # the clear button clears line and readout
    page.mouse.click(hits["b"][0], hits["b"][1]); page.wait_for_timeout(500)
    if page.locator("#measClear").count() == 1:
        page.click("#measClear"); page.wait_for_timeout(600)
    m4 = meas_state(page)
    out4, d4, _ = read_out(page)
    check(f"[ruler] «{CLEAR}» clears the points and the distance",
          not m4.get("err") and (m4["m"].get("points") or []) == [] and m4["m"].get("distance") is None and m4["m"].get("on") is True,
          m4.get("err") or json.dumps(m4.get("m")))
    check(f"[ruler] «{CLEAR}» empties the readout", here(page, "#measOut") and d4 is None and not re.search(r"\d", out4),
          repr(out4) if here(page, "#measOut") else "no #measOut")
    left = px_count(clean_png, region_png(page, clip, "ruler_cleared.png"))
    check(f"[ruler] «{CLEAR}» removes the line from the 3D view", left < max(60, drew) * 0.25, f"{left} px still differ from the picture before measuring (the line drew {drew})")

    # a click on a vertex of the part must land exactly on that vertex (the snap is what makes a real size come out)
    vv = visible_vertex(page, pn, xmin)
    if check("[ruler] a vertex of the part visible on its own pixel was found to click on", bool(vv), json.dumps(vv)):
        page.mouse.click(vv["s"][0], vv["s"][1]); page.wait_for_timeout(500)
        snapped = ((meas_state(page).get("m") or {}).get("points") or [None])[0]
        f = snap_facts(page, vv["s"][0], vv["s"][1], snapped)
        check("[ruler] a click aimed at a vertex of the part lands on a vertex, not on the surface between them",
              snapped is not None and bool(f) and f["exact"],
              f"{snapped} vs the vertex {[round(x, 3) for x in vv['v']]}"
              + (f", {dist(snapped, vv['v']):.3f} mm from it, {dist(snapped, f['raw']):.3f} mm from the ray hit" if snapped and f else ""))
        if page.locator("#measClear").count() == 1:
            page.click("#measClear"); page.wait_for_timeout(400)

    # switching off clears, and clicks select parts again
    page.mouse.click(hits["a"][0], hits["a"][1]); page.wait_for_timeout(300)
    page.mouse.click(hits["b"][0], hits["b"][1]); page.wait_for_timeout(500)
    if page.locator("#bMeasure").count() == 1:
        page.click("#bMeasure"); page.wait_for_timeout(600)
    m5 = meas_state(page)
    out5, d5, _ = read_out(page)
    check("[ruler] switching the ruler off clears the line and the readout",
          not m5.get("err") and m5["m"].get("on") is False and (m5["m"].get("points") or []) == [] and m5["m"].get("distance") is None and d5 is None,
          m5.get("err") or json.dumps(m5.get("m")) + " " + repr(out5))
    check(f"[ruler] the ruler off -> «{CLEAR}» has nothing to clear any more", here(page, "#measClear") and not vis(page, "#measClear"),
          "" if here(page, "#measClear") else "no #measClear")
    left = px_count(clean_png, region_png(page, clip, "ruler_off.png"))
    check("[ruler] the ruler off -> the 3D view is back to the picture before measuring",
          left < max(60, drew) * 0.25, f"{left} px still differ from the picture before measuring (the line drew {drew})")
    page.mouse.click(hits["a"][0], hits["a"][1]); page.wait_for_timeout(700)
    stm = page.evaluate("window.__viewer.state()")
    check("[ruler] after switching the ruler off a click selects the part again",
          here(page, "#bMeasure") and stm["infoOpen"] and stm["selected"] and stm["selected"]["part"] == a.part,
          json.dumps(stm["selected"], ensure_ascii=False) + ("" if here(page, "#bMeasure") else " (no #bMeasure: the ruler was never on)"))

    check("[reg] console has no errors through all of it", not errs, "; ".join(errs[:3]))
    ctx.close()
    return errs


def phone(b):
    ctx, page, errs = open_page(b, {"width": 390, "height": 844}, mobile=True)
    if page.evaluate("window.__viewer.state().panelCollapsed"):
        page.click("#pHead"); page.wait_for_timeout(500)
    reach = []
    for bid, label, _, _ in VIEWS + [("bGrid", GRID, None, None), ("bMeasure", RULER, None, None)]:
        loc = page.locator("#" + bid)
        if loc.count() != 1:
            check(f"[phone] «{label}» is reachable in the panel", False, f"no #{bid}")
            continue
        try:
            loc.scroll_into_view_if_needed(timeout=5000)
        except Exception as ex:  # noqa
            check(f"[phone] «{label}» is reachable in the panel", False, str(ex).splitlines()[0])
            continue
        page.wait_for_timeout(120)
        bb = loc.bounding_box()
        ok = loc.is_visible() and bb and bb["x"] >= -1 and bb["x"] + bb["width"] <= 391 and bb["height"] >= 16
        check(f"[phone] «{label}» is reachable in the panel", bool(ok), json.dumps({k: round(bb[k]) for k in ("x", "y", "width", "height")} if bb else None))
        reach.append(bid)
    wrapped = page.evaluate("""(ids) => ids.map((i) => { const e = document.getElementById(i); if (!e) return null;
      const lh = parseFloat(getComputedStyle(e).fontSize) * 1.6;
      return e.getBoundingClientRect().height > lh + 9 ? [i, Math.round(e.getBoundingClientRect().height), Math.round(lh)] : null; }).filter(Boolean)""",
      [i for i, _, _, _ in VIEWS] + ["bGrid", "bMeasure"])
    check("[phone] 390x844: no label of a view or a tool wraps onto a second line", wrapped == [], json.dumps(wrapped, ensure_ascii=False))
    page.screenshot(path=str(shots / "phone_panel.png"))
    ov = page.evaluate("[document.scrollingElement.scrollWidth, innerWidth]")
    check("[phone] 390x844: the page does not scroll sideways", ov[0] <= ov[1] + 1, f"scrollWidth {ov[0]} vs innerWidth {ov[1]}")
    check("[phone] console has no errors", not errs, "; ".join(errs[:3]))
    ctx.close()


with sync_playwright() as p:
    b = p.chromium.launch(args=GPU_ARGS)
    try:
        desktop(b)
        phone(b)
    finally:
        b.close()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
print(f"\n{sum(1 for r in rows if r[1] == 'FAIL')} failing, {sum(1 for r in rows if r[1] == 'PASS')} passing")
sys.exit(0 if ok_all else 1)
