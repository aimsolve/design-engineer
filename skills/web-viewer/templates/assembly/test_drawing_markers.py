#!/usr/bin/env python3
"""Real-browser check of the ready-made marks (pin, move, face, dim) on the model page, and understood.py
rendering them in a chosen view.

Same synthetic two-box model and harness as test_drawing_3d.py (build.py --up z --kit none --flash none, viewer_stack_server,
POST /feedback/** answered 200 by Playwright). Everything the client does goes through his door: #fbOpen, the tools in #fbBar
(pin, move, dim, face, select), real mouse clicks and drags, a typed comment, the send button (i18n fb.send; the page is in its default language, ru). Camera presets (__viewer.setView / setCam),
the page's picker and camInfo() are used only for setup: finding the boxes on screen, and putting the camera back where the client
turned it after the note is sent, so a picture without the marks can be compared pixel by pixel with the one with them.

  env -u VIRTUAL_ENV uv run --isolated --no-project --python 3.12 --with playwright --with numpy python test_drawing_markers.py [--swiftshader] [--shots DIR]

The model (assembly mm, Z up):  A «Left support»   10.01.001  x 0..40,   y 0..40, z 0..20   (top face 40 × 40 mm, 1600 mm², z = 20)
                                B «Right bracket»  10.02.001  x 80..120, y 0..40, z 0..30

Checked:
  [P] top view; pin tool: a click on A (pin 1), a click on empty space (no mark), a click on B (pin 2); move tool: a drag from A
      to B; dim tool: a drag from A to B -> the page shows «N,N mm» (the page language's decimal sign and mm, from i18n.mjs); select tool, a mouse drag turns the model; send ->
      shapes = [pin, pin, move, dim] with roles pin / from,to / from,to, parts A, B, A->B, A->B, 3D paths (1, 2, 2 points) on the parts' tops,
      dim.length = the distance between its two 3D points (1 decimal) and = the number on the page's label; in the turned view the marks
      are drawn at their 3D places (pixels changed against the same view without the note); the sent pictures (the turned view and the top
      view) show them.
  [F] top view; face tool: a click on empty space (no mark), a click on A's top -> the WHOLE top face of A is tinted (its four inset
      corners and >= 80 % of a 5x5 grid on it change colour), B's top is not; select tool turns the model; send -> shapes = [face] with the
      key point on A and face = {part A, normal +Z, centre (20, 20, 20), area 1600, size [40, 40], plane z = 20}; the tint is on A's top
      in the turned view and in both sent pictures.
  [U] understood.py renders a note with all four marks (the page's own when [P] and [F] worked, else the same marks built from the
      known geometry) in view 2: pins and the face tint where they are in 3D (against the same note with no shapes), the move differs
      from a plain line drawn along the same path (its head) and the dim too (its label / ticks).
Exit code 1 on any failed check.
"""
import argparse, atexit, json, math, re, shutil, subprocess, sys, tempfile
from pathlib import Path

import numpy as np
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SKILLS / "step-import" / "scripts"))
from viewer_stack_server import png_pixels, serve_stack, ui_texts  # noqa: E402
import step_import  # noqa: E402

UNDERSTOOD = SKILLS / "web-viewer" / "scripts" / "understood.py"

ap = argparse.ArgumentParser()
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="drawing_marks_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
PROJECT = "marks-proj"
VIEWPORT = {"width": 1280, "height": 800}
A, B = "10.01.001", "10.02.001"
BOXES = {A: ("Left support", (0, 0, 0), (40, 40, 20)), B: ("Right bracket", (80, 0, 0), (120, 40, 30))}
TOP_Z = {A: 20, B: 30}
MSG_ID = 6201
GRID = (4, 12, 20, 28, 36)        # a 5x5 grid of points on a 40 mm top face, 4 mm in from its edges
T = ui_texts("ru")
LABEL = re.compile(r"(\d+)" + re.escape(T["decimal"]) + r"(\d)[\s\u00a0\u202f\u2009]*" + re.escape(T["mm"]))
rows, ok_all = [], True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- synthetic model (as test_drawing_3d.py)
def box_mesh(lo, hi):
    v, t, qn = [], [], []
    for ax in range(3):
        u, w = (ax + 1) % 3, (ax + 2) % 3
        for s in (-1, 1):
            k = len(v)
            for cu, cw in ((lo[u], lo[w]), (hi[u], lo[w]), (hi[u], hi[w]), (lo[u], hi[w])):
                p = [0.0, 0.0, 0.0]; p[ax] = hi[ax] if s > 0 else lo[ax]; p[u] = cu; p[w] = cw
                n = [0, 0, 0]; n[ax] = 127 * s
                v.append(p); qn.append(n)
            t += [[k, k + 1, k + 2], [k, k + 2, k + 3]] if s > 0 else [[k, k + 2, k + 1], [k, k + 3, k + 2]]
    return np.array(v, dtype=float), np.array(t, dtype=np.int64), np.array(qn, dtype=np.int8)


def write_model(out: Path):
    items = []
    for pid, (name, lo, hi) in BOXES.items():
        v, t, qn = box_mesh(lo, hi)
        items.append({"id": pid, "name": name, "node": "Bench", "color": None, "v": v, "t": t, "qn": qn,
                      "volume_mm3": float(np.prod(np.subtract(hi, lo))), "bbox_mm": [list(lo), list(hi)], "step_src": None, "stl_src": None})
    step_import.write_model_dir(str(out), items, "Bench")


root = Path(tempfile.mkdtemp(prefix="drawing_marks_e2e_"))
atexit.register(lambda: shutil.rmtree(root, ignore_errors=True))
model_dir, proj_dir, served = root / "model", root / "project", root / "served"
write_model(model_dir)
b_site = subprocess.run([sys.executable, str(HERE / "build.py"), "--in", str(model_dir), "--out", str(proj_dir / "public"), "--up", "z",
                         "--kit", "none", "--flash", "none"], capture_output=True, text=True)
if b_site.returncode:
    sys.exit("build.py failed:\n" + b_site.stderr[-2000:])
served.mkdir()
(served / PROJECT).symlink_to(proj_dir / "public")
srv, BASE = serve_stack(served)
atexit.register(srv.shutdown)


# ---------------------------------------------------------------- geometry
def project(cam, at):
    """Assembly mm -> CSS px through a camera's row-major view and proj (as the page sends them)."""
    P, V = np.array(cam["proj"], dtype=float).reshape(4, 4), np.array(cam["view"], dtype=float).reshape(4, 4)
    c = P @ V @ np.array([*at, 1.0])
    if c[3] <= 1e-9:
        return None
    return ((c[0] / c[3] + 1) / 2 * cam["w"], (1 - c[1] / c[3]) / 2 * cam["h"])


def visible(cam, at):
    """No box between the camera and the point (a point on a box's own face is entered at t = 1, not before)."""
    o, d = np.array(cam["position"], dtype=float), np.subtract(at, cam["position"])
    for _, lo, hi in BOXES.values():
        t0, t1 = -math.inf, math.inf
        for i in range(3):
            if abs(d[i]) < 1e-12:
                if not lo[i] <= o[i] <= hi[i]:
                    t0, t1 = 1, 0
                    break
                continue
            ta, tb = sorted(((lo[i] - o[i]) / d[i], (hi[i] - o[i]) / d[i]))
            t0, t1 = max(t0, ta), min(t1, tb)
        if t0 <= t1 and 0 < t0 < 1 - 1e-3:
            return False
    return True


def lerp(p, q, t):
    return [p[i] + t * (q[i] - p[i]) for i in range(3)]


def seg_dist(q, a, b):
    ab, aq = np.subtract(b, a), np.subtract(q, a)
    t = max(0.0, min(1.0, float(np.dot(aq, ab) / max(np.dot(ab, ab), 1e-9))))
    return float(np.linalg.norm(aq - t * ab))


def finite3(p):
    return isinstance(p, list) and len(p) == 3 and all(isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) for c in p)


def top_grid(part, inset=GRID):
    lo, hi = BOXES[part][1], BOXES[part][2]
    return [[lo[0] + x, lo[1] + y, TOP_Z[part]] for x in inset for y in inset]


def inset_corners(part, m=4):
    lo, hi = BOXES[part][1], BOXES[part][2]
    return [[x, y, TOP_Z[part]] for x in (lo[0] + m, hi[0] - m) for y in (lo[1] + m, hi[1] - m)]


# ---------------------------------------------------------------- pixels
class Img:
    def __init__(self, png: bytes = None, jpeg: dict = None):
        if png is not None:
            self.w, self.h, self.ch, self.rows = png_pixels(png)
            self.get = lambda x, y: self.rows[y][x * self.ch:x * self.ch + 3]
        else:
            self.w, self.h, rgb = jpeg["w"], jpeg["h"], jpeg["rgb"]
            self.get = lambda x, y: rgb[(y * self.w + x) * 3:(y * self.w + x) * 3 + 3]
        self.ui = []

    def win(self, x, y, r):
        return [(xx, yy) for yy in range(max(0, int(round(y)) - r), min(self.h, int(round(y)) + r + 1))
                for xx in range(max(0, int(round(x)) - r), min(self.w, int(round(x)) + r + 1))]


def diff_px(p, q):
    return sum(abs(int(p[i]) - int(q[i])) for i in range(3)) / 3


def changed_count(im, base, x, y, r=10, thr=40, k=1.0, kb=1.0):
    """Pixels near (x, y) css px whose colour moved by >= thr between base and im (k, kb: css px -> image px of each)."""
    n = 0
    for xx, yy in im.win(x * k, y * k, max(1, int(r * k))):
        bx, by = int(round(xx / k * kb)), int(round(yy / k * kb))
        if 0 <= bx < base.w and 0 <= by < base.h:
            n += diff_px(im.get(xx, yy), base.get(bx, by)) >= thr
    return n


def tinted(im, base, x, y, thr=6, k=1.0, kb=1.0):
    """Mean colour over a 5x5 px window moved by >= thr."""
    ws = im.win(x * k, y * k, 2)
    tot = 0.0
    for xx, yy in ws:
        bx, by = min(base.w - 1, int(round(xx / k * kb))), min(base.h - 1, int(round(yy / k * kb)))
        tot += diff_px(im.get(xx, yy), base.get(bx, by))
    return tot / max(1, len(ws)) >= thr


def on_screen(pt, w, h, m=8):
    return pt is not None and m <= pt[0] <= w - m and m <= pt[1] <= h - m


def covered(pt, rects, m=8):
    return any(r[0] - m <= pt[0] <= r[2] + m and r[1] - m <= pt[1] <= r[3] + m for r in rects)


# ---------------------------------------------------------------- page helpers
TEXT_HOOK = """(() => { const seen = (window.__fbTexts = []);
  for (const C of [window.CanvasRenderingContext2D, window.OffscreenCanvasRenderingContext2D]) { if (!C) continue;
    for (const m of ["fillText", "strokeText"]) { const f = C.prototype[m];
      C.prototype[m] = function (t, ...r) { try { seen.push(String(t)); if (seen.length > 20000) seen.splice(0, 5000); } catch (e) {} return f.call(this, t, ...r); }; } } })();"""


def page_texts(page):
    """Every string the page drew into a 2D canvas (sprites, labels) and every DOM leaf text: where the client could read a label."""
    return page.evaluate("""() => { const dom = [];
      for (const e of document.querySelectorAll('body *')) { if (e.children.length) continue; const cs = getComputedStyle(e);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue; const t = (e.textContent || '').trim(); if (t) dom.push(t); }
      return { canvas: (window.__fbTexts || []).slice(), dom }; }""")


def screen_boxes(page):
    return page.evaluate("""() => { const v = window.__viewer, out = {};
      for (let y = 2; y < innerHeight; y += 4) for (let x = 2; x < innerWidth; x += 4) { const h = v.pick(x, y); if (!h || !h.part) continue;
        const b = out[h.part] || (out[h.part] = [1e9, 1e9, -1e9, -1e9]); b[0] = Math.min(b[0], x); b[1] = Math.min(b[1], y); b[2] = Math.max(b[2], x); b[3] = Math.max(b[3], y); }
      return out; }""")


def part_at(page, pt):
    return page.evaluate("([x, y]) => { const h = window.__viewer.pick(x, y); return h ? h.part : null; }", list(pt))


def free_for_drawing(page, pts):
    return page.evaluate("(pts) => pts.map(([x, y]) => { const e = document.elementFromPoint(x, y); return e ? (e.id || e.tagName) : null; })", [list(p) for p in pts])


def drag(page, p0, p1, steps=14):
    page.mouse.move(*p0); page.mouse.down(); page.mouse.move(*p1, steps=steps); page.mouse.up(); page.wait_for_timeout(300)


def click(page, p):
    page.mouse.click(round(p[0]), round(p[1])); page.wait_for_timeout(350)


def settle(page):
    page.wait_for_timeout(300)
    page.wait_for_function("!window.__viewer.state().flying", timeout=30000)
    page.wait_for_timeout(400)


def top_view(page):
    page.evaluate("window.__viewer.setView('top')")
    settle(page)
    page.evaluate("window.__viewer.setCam({dist: window.__viewer.state().dist * 1.4})")
    page.wait_for_timeout(600)


def tool(page, name):
    page.click(f'#fbBar [data-tool="{name}"]', timeout=5000)


def send(page, text):
    page.fill("#fbPop textarea", text)
    page.locator("#fbPop").get_by_role("button", name=T["fb"]["send"]).click()
    page.wait_for_timeout(2600)


def open_bar(page):
    if page.locator("#fbOpen").is_hidden():
        page.click("#fbBar [data-close]", timeout=5000)
        page.wait_for_timeout(300)
    page.click("#fbOpen", timeout=5000)


def close_bar(page):
    if page.locator("#fbOpen").is_hidden():
        page.click("#fbBar [data-close]", timeout=5000)
        page.wait_for_timeout(400)


def ui_rects(page):
    return page.evaluate("""() => { const out = [], W = innerWidth, H = innerHeight;
      for (const e of document.querySelectorAll('body *')) {
        if (e.tagName === 'CANVAS' || e.id === 'fbDraw' || e.closest('#fbDraw')) continue;
        const cs = getComputedStyle(e); if (cs.position !== 'fixed' && cs.position !== 'absolute') continue;
        if (cs.display === 'none' || +cs.opacity === 0) continue;
        const r = e.getBoundingClientRect(); if (r.width < 2 || r.height < 2 || r.width * r.height > 0.5 * W * H) continue;
        out.push([r.left, r.top, r.right, r.bottom]); }
      return out; }""")


def shot(page, name):
    page.mouse.move(3, 3)
    page.wait_for_timeout(250)
    im = Img(png=page.screenshot(path=str(shots / name)))
    im.ui = ui_rects(page)
    return im


def decode_jpeg(page, data_url):
    return Img(jpeg=page.evaluate("""async (src) => { const im = new Image(); im.src = src; await im.decode();
      const c = document.createElement('canvas'); c.width = im.width; c.height = im.height; const g = c.getContext('2d'); g.drawImage(im, 0, 0);
      const d = g.getImageData(0, 0, im.width, im.height).data, rgb = new Array(im.width * im.height * 3);
      for (let i = 0, j = 0; i < d.length; i += 4) { rgb[j++] = d[i]; rgb[j++] = d[i + 1]; rgb[j++] = d[i + 2]; }
      return { w: im.width, h: im.height, rgb }; }""", data_url))


def same_cam(c1, c2):
    return c1 and c2 and all(abs(x - y) <= 1e-3 * (1 + abs(y)) for x, y in zip(c1["view"], c2["view"]))


def restore(page, st, want_cam, name):
    """Setup: the view the client turned to, without the note (bar closed: the marks are gone), for a pixel-by-pixel comparison."""
    close_bar(page)
    page.evaluate("(s) => window.__viewer.setCam({yaw: s.yaw, pitch: s.pitch, dist: s.dist, target: s.target})", st)
    page.wait_for_timeout(700)
    ci = page.evaluate("window.__viewer.camInfo()")
    if not check(f"{name} setup: the same camera again without the note", same_cam(ci, want_cam)):
        return None
    return shot(page, f"{name.strip('[]')}_baseline_turned.png")


def turn(page, grab, name):
    """The select tool and a real mouse drag on the model (B): the camera turns."""
    tool(page, "select")
    st0 = page.evaluate("window.__viewer.state()")
    check(f"{name} setup: the turning drag starts on the model", part_at(page, grab) is not None, str(grab))
    drag(page, grab, (grab[0] + 160, grab[1] - 70), steps=24)   # a turn that keeps the marks clear of the toolbar hint and the popup
    settle(page)
    st1 = page.evaluate("window.__viewer.state()")
    turned = abs(st1["yaw"] - st0["yaw"]) + abs(st1["pitch"] - st0["pitch"])
    check(f"{name} with marks on the model, a select-tool drag turns the camera", turned > 0.15, f"{turned:.3f} rad")
    return st1


def mark_points(shapes):
    """[(label, kind, [3D points])] of the marks, for pixel checks: a pin = its point, move/dim = points along the path."""
    out = []
    for s in shapes:
        p = s.get("path") or []
        if s.get("type") == "pin" and p:
            out.append(("pin", [p[0]]))
        elif s.get("type") in ("move", "dim") and len(p) == 2:
            out.append((s["type"], [lerp(p[0], p[1], t) for t in (0.3, 0.4, 0.5, 0.6, 0.7)]))
    return out


def marks_in(im, base, cam, shapes, name, thr=40, r=10, need=3, rects=()):
    """Each mark drawn where its 3D points project through cam (pixels changed against the picture without the note)."""
    k = im.w / cam["w"]
    for kind, pts in mark_points(shapes):
        seen = [q for q in ((project(cam, p), p) for p in pts) if on_screen(q[0], cam["w"], cam["h"]) and not covered(q[0], rects) and visible(cam, q[1])]
        why = [(p, project(cam, p), on_screen(project(cam, p), cam["w"], cam["h"]), covered(project(cam, p), rects) if project(cam, p) else None, visible(cam, p)) for p in pts]
        if not check(f"{name} setup: the {kind} is in view and not hidden", seen, json.dumps(why, default=str)[:300]):
            continue
        hits = [changed_count(im, base, q[0][0], q[0][1], r=r, thr=thr, k=k, kb=base.w / cam["w"]) for q in seen]
        good = sum(1 for n in hits if n >= need)
        check(f"{name} the {kind} is drawn at its 3D place", good >= max(1, math.ceil(0.6 * len(hits))), f"changed px near its points: {hits}")


def face_in(im, base, cam, name, thr=6, rects=(), want=0.8, avoid=()):
    """A's top tinted (>= want of the grid, and every inset corner that is in view); B's top not (<= 10 %).
    avoid: other marks' 3D paths; grid points within 20 px of them on screen are not judged."""
    k, kb = im.w / cam["w"], base.w / cam["w"]
    segs = []
    for path in avoid:
        qs = [project(cam, p) for p in path]
        segs += [(qs[i], qs[min(i + 1, len(qs) - 1)]) for i in range(len(qs)) if qs[i] and qs[min(i + 1, len(qs) - 1)]]
    def share(pts):
        qs = [project(cam, p) for p in pts if visible(cam, p)]
        qs = [q for q in qs if on_screen(q, cam["w"], cam["h"]) and not covered(q, rects) and all(seg_dist(q, s0, s1) >= 20 for s0, s1 in segs)]
        return [tinted(im, base, q[0], q[1], thr=thr, k=k, kb=kb) for q in qs]
    ga, ca, gb = share(top_grid(A)), share(inset_corners(A)), share(top_grid(B))
    if not check(f"{name} setup: A's top is in view", len(ga) >= 10, f"{len(ga)} grid points"):
        return
    check(f"{name} A's whole top face is tinted (grid)", sum(ga) >= want * len(ga), f"{sum(ga)} of {len(ga)}")
    check(f"{name} A's top is tinted up to its corners", ca and all(ca), json.dumps(ca))
    if gb:
        check(f"{name} B's top is not tinted", sum(gb) <= 0.1 * len(gb), f"{sum(gb)} of {len(gb)}")


# ---------------------------------------------------------------- [P] pins, move, dim
def phase_marks(page, fb):
    top_view(page)
    sb = screen_boxes(page)
    if not check("[P] setup: both boxes on screen", A in sb and B in sb, json.dumps(sb)):
        return None
    ax0, ay0, ax1, ay1 = sb[A]; bx0, by0, bx1, by1 = sb[B]
    at = lambda b, fx, fy: (round(b[0] + fx * (b[2] - b[0])), round(b[1] + fy * (b[3] - b[1])))
    pin1, pin2 = at(sb[A], 0.3, 0.15), at(sb[B], 0.7, 0.15)
    mv0, mv1 = at(sb[A], 0.5, 0.45), at(sb[B], 0.5, 0.45)
    dm0, dm1 = at(sb[A], 0.5, 0.88), at(sb[B], 0.5, 0.88)
    air = (round(ax0 - 0.6 * (ax1 - ax0)), round(ay0 + 0.3 * (ay1 - ay0)))
    open_bar(page)
    check("[P] setup: every click / drag point is on the drawing layer", set(free_for_drawing(page, [pin1, pin2, mv0, mv1, dm0, dm1, air])) == {"fbDraw"})
    check("[P] setup: the points are on A / B / empty space", [part_at(page, p) for p in (pin1, pin2, mv0, mv1, dm0, dm1, air)] == [A, B, A, B, A, B, None])
    base_top = shot(page, "P0_top_before.png")
    texts0 = page_texts(page)
    tool(page, "pin")
    click(page, pin1)
    click(page, air)                       # empty space: no mark
    click(page, pin2)
    texts_pins = page_texts(page)
    tool(page, "move")
    drag(page, mv0, mv1)
    tool(page, "dim")
    drag(page, dm0, dm1)
    page.wait_for_timeout(300)
    texts_dim = page_texts(page)
    top_marked = shot(page, "P1_top_marked.png")
    cam_top = page.evaluate("window.__viewer.camInfo()")
    st = turn(page, at(sb[B], 0.9, 0.65), "[P]")
    turned_img = shot(page, "P2_turned.png")
    n0 = len(fb)
    send(page, "pins, a move and a size")
    if not check("[P] one POST", len(fb) == n0 + 1, f"{len(fb) - n0} POST(s)"):
        return None
    body = fb[-1]["body"]
    v = body.get("view") or {}
    shapes, views = v.get("shapes") or [], v.get("views") or []
    if not check("[P] the note has [pin, pin, move, dim]: the click on empty space made no mark",
                 [s.get("type") for s in shapes] == ["pin", "pin", "move", "dim"], json.dumps([s.get("type") for s in shapes])):
        return None
    p1, p2, mv, dm = shapes
    # the pin on A
    for s, part, nm in ((p1, A, "pin 1"), (p2, B, "pin 2")):
        pts = s.get("pts") or []
        check(f"[P] {nm}: one key point, role pin, on {part}", [p.get("role") for p in pts] == ["pin"] and pts[0].get("part") == part, json.dumps(pts))
        path = s.get("path")
        ok = isinstance(path, list) and len(path) == 1 and finite3(path[0]) and abs(path[0][2] - TOP_Z[part]) <= 1.01
        check(f"[P] {nm}: its 3D path is 1 point on {part}'s top", ok, json.dumps(path))
        if ok and pts and finite3(pts[0].get("at")):
            check(f"[P] {nm}: path = the key point's mm point", math.dist(path[0], pts[0]["at"]) <= 1.01)
    # move and dim from A to B
    for s, nm in ((mv, "move"), (dm, "dim")):
        pts = s.get("pts") or []
        check(f"[P] {nm}: key points from (A) and to (B)", [p.get("role") for p in pts] == ["from", "to"] and [p.get("part") for p in pts] == [A, B], json.dumps(pts)[:300])
        path = s.get("path")
        check(f"[P] {nm}: its 3D path is 2 points, on A's top then B's top", isinstance(path, list) and len(path) == 2 and all(finite3(p) for p in path)
              and abs(path[0][2] - 20) <= 1.01 and abs(path[1][2] - 30) <= 1.01, json.dumps(path))
    ln = dm.get("length")
    dpath = dm.get("path") if isinstance(dm.get("path"), list) and len(dm.get("path")) == 2 and all(finite3(p) for p in dm["path"]) else None
    if check("[P] dim.length is a number with 1 decimal", isinstance(ln, (int, float)) and math.isfinite(ln) and abs(ln * 10 - round(ln * 10)) < 1e-6, repr(ln)) and dpath:
        check("[P] dim.length = the distance between its two 3D points", abs(ln - math.dist(*dpath)) <= 0.11, f"{ln} vs {math.dist(*dpath):.2f}")
        labels = [float(f"{m[0]}.{m[1]}") for t in texts_dim["canvas"] + texts_dim["dom"] for m in LABEL.findall(t)]
        check("[P] the page shows the dim's label «N,N mm» with the payload's length", any(abs(x - ln) < 0.05 for x in labels), f"labels {labels}, length {ln}")
    new = lambda before, after: [t for t in after["canvas"][len(before["canvas"]):]] + [t for t in after["dom"] if t not in before["dom"]]
    nums = [t.strip() for t in new(texts0, texts_pins)]
    check("[P] the pins are numbered 1 and 2 on the page", "1" in nums and "2" in nums, json.dumps(nums[:30], ensure_ascii=False))
    check("[P] every mark names its view (0: drawn in the top view)", [s.get("view") for s in shapes] == [0, 0, 0, 0], json.dumps([s.get("view") for s in shapes]))
    if not check("[P] views = [top, turned] (the picture's view added at send)", len(views) == 2 and same_cam(views[0], cam_top), f"{len(views)} views"):
        return body
    # in the turned view the marks are at their 3D places, in the top view too; and in the pictures
    marks_in(top_marked, base_top, views[0], shapes, "[P] top view:", rects=top_marked.ui + base_top.ui)
    base_turned = restore(page, st, views[1], "[P]")
    if base_turned:
        marks_in(turned_img, base_turned, views[1], shapes, "[P] turned view:", rects=turned_img.ui + base_turned.ui)
        for old, nm in ((pin1, "pin 1"), (pin2, "pin 2")):
            far = all(math.dist(old, q) >= 30 for _, pts in mark_points(shapes) for q in (project(views[1], p) for p in pts) if q)
            if far and not covered(old, turned_img.ui + base_turned.ui):
                n = changed_count(turned_img, base_turned, *old, r=4)
                check(f"[P] turned view: {nm} is not left at its old 2D place", n == 0, f"{n} changed px at {old}")
        img = body.get("image")
        if check("[P] the note's picture is a JPEG", isinstance(img, str) and img.startswith("data:image/jpeg")):
            marks_in(decode_jpeg(page, img), base_turned, views[1], shapes, "[P] the sent picture (turned view):", thr=45, rects=base_turned.ui)
    imgs = body.get("images") or []
    if check("[P] the earlier view's picture is sent too", len(imgs) == 1 and str(imgs[0]).startswith("data:image/jpeg")):
        marks_in(decode_jpeg(page, imgs[0]), base_top, views[0], shapes, "[P] the sent picture (top view):", thr=45, rects=base_top.ui)
    return body


# ---------------------------------------------------------------- [F] face
def phase_face(page, fb):
    top_view(page)
    sb = screen_boxes(page)
    if not check("[F] setup: both boxes on screen", A in sb and B in sb, json.dumps(sb)):
        return None
    ax0, ay0, ax1, ay1 = sb[A]
    ca = (round((ax0 + ax1) / 2), round((ay0 + ay1) / 2))
    air = (round(ax0 - 0.6 * (ax1 - ax0)), round(ay0 + 0.3 * (ay1 - ay0)))
    open_bar(page)
    check("[F] setup: A's centre is on A, the other spot is empty space", part_at(page, ca) == A and part_at(page, air) is None)
    tool(page, "face")
    before = shot(page, "F0_top_before.png")
    cam_top = page.evaluate("window.__viewer.camInfo()")
    click(page, air)
    click(page, ca)
    page.wait_for_timeout(300)
    after = shot(page, "F1_top_face.png")
    face_in(after, before, cam_top, "[F] top view:", rects=after.ui + before.ui)
    st = turn(page, (round((sb[B][0] + sb[B][2]) / 2), round((sb[B][1] + sb[B][3]) / 2)), "[F]")
    turned_img = shot(page, "F2_turned.png")
    n0 = len(fb)
    send(page, "this face")
    if not check("[F] one POST", len(fb) == n0 + 1, f"{len(fb) - n0} POST(s)"):
        return None
    body = fb[-1]["body"]
    v = body.get("view") or {}
    shapes, views = v.get("shapes") or [], v.get("views") or []
    if not check("[F] the note has one face mark: the click on empty space made none", [s.get("type") for s in shapes] == ["face"], json.dumps([s.get("type") for s in shapes])):
        return None
    s = shapes[0]
    pts = s.get("pts") or []
    check("[F] its key point: role face, on A's top", [p.get("role") for p in pts] == ["face"] and pts[0].get("part") == A and finite3(pts[0].get("at"))
          and abs(pts[0]["at"][2] - 20) <= 1.01 and finite3(pts[0].get("normal")) and pts[0]["normal"][2] > 0.99, json.dumps(pts))
    check("[F] it has a view and a 3D path", s.get("view") == 0 and isinstance(s.get("path"), list) and s["path"] and all(finite3(p) for p in s["path"]), json.dumps({k: s.get(k) for k in ("view", "path")}))
    f = s.get("face") if isinstance(s.get("face"), dict) else {}
    check("[F] face.part = A", f.get("part") == A, json.dumps(f))
    check("[F] face.normal = +Z", finite3(f.get("normal")) and math.dist(f["normal"], [0, 0, 1]) <= 0.01, json.dumps(f.get("normal")))
    check("[F] face.centre = (20, 20, 20) ± 0.5", finite3(f.get("centre")) and math.dist(f["centre"], [20, 20, 20]) <= 0.5, json.dumps(f.get("centre")))
    check("[F] face.area = 1600 mm² (± 1 %)", isinstance(f.get("area"), (int, float)) and abs(f["area"] - 1600) <= 16, json.dumps(f.get("area")))
    sz = f.get("size")
    check("[F] face.size = [40, 40] ± 0.5 (the box top, not its diagonal)", isinstance(sz, list) and len(sz) == 2 and all(isinstance(x, (int, float)) and abs(x - 40) <= 0.5 for x in sz), json.dumps(sz))
    pl = f.get("plane") if isinstance(f.get("plane"), dict) else {}
    check("[F] face.plane = {normal +Z, d 20}", finite3(pl.get("normal")) and math.dist(pl["normal"], [0, 0, 1]) <= 0.01 and isinstance(pl.get("d"), (int, float)) and abs(pl["d"] - 20) <= 0.5, json.dumps(pl))
    if not check("[F] views = [top, turned]", len(views) == 2 and same_cam(views[0], cam_top), f"{len(views)} views"):
        return body
    base_turned = restore(page, st, views[1], "[F]")
    if base_turned:
        face_in(turned_img, base_turned, views[1], "[F] turned view:", rects=turned_img.ui + base_turned.ui, want=0.75)
        img = body.get("image")
        if check("[F] the note's picture is a JPEG", isinstance(img, str) and img.startswith("data:image/jpeg")):
            face_in(decode_jpeg(page, img), base_turned, views[1], "[F] the sent picture (turned view):", thr=8, rects=base_turned.ui, want=0.75)
    imgs = body.get("images") or []
    if check("[F] the earlier view's picture is sent too", len(imgs) == 1 and str(imgs[0]).startswith("data:image/jpeg")):
        face_in(decode_jpeg(page, imgs[0]), before, views[0], "[F] the sent picture (top view):", thr=8, rects=before.ui)
    return body


# ---------------------------------------------------------------- [U] understood.py
def synthetic_marks(cams, ns):
    """The four marks as the contract says the page sends them, at known mm points (when the page could not make them)."""
    top = cams[0]
    px = lambda p: [round(c) for c in project(top, p)]
    hit = lambda p, role, part, normal=(0, 0, 1): {"px": px(p), "role": role, "part": part, "n": ns[part], "at": p, "normal": list(normal)}
    p1, p2 = [10.0, 30.0, 20.0], [110.0, 10.0, 30.0]
    m0, m1, d0, d1 = [20.0, 15.0, 20.0], [100.0, 15.0, 30.0], [20.0, 35.0, 20.0], [100.0, 35.0, 30.0]
    fc = [20.0, 20.0, 20.0]
    return [
        {"type": "pin", "px": [px(p1)], "view": 0, "path": [p1], "pts": [hit(p1, "pin", A)]},
        {"type": "pin", "px": [px(p2)], "view": 0, "path": [p2], "pts": [hit(p2, "pin", B)]},
        {"type": "move", "px": [px(m0), px(m1)], "view": 0, "path": [m0, m1], "pts": [hit(m0, "from", A), hit(m1, "to", B)]},
        {"type": "dim", "px": [px(d0), px(d1)], "view": 0, "path": [d0, d1], "length": round(math.dist(d0, d1), 1), "pts": [hit(d0, "from", A), hit(d1, "to", B)]},
        {"type": "face", "px": [px(fc)], "view": 0, "path": [fc], "pts": [hit(fc, "face", A)],
         "face": {"part": A, "normal": [0, 0, 1], "centre": fc, "area": 1600, "size": [40, 40], "plane": {"normal": [0, 0, 1], "d": 20}}},
    ]


def run_understood(note, name):
    nf = proj_dir / "build" / "notes" / f"{name}.json"
    nf.parent.mkdir(parents=True, exist_ok=True)
    nf.write_text(json.dumps(note, ensure_ascii=False), encoding="utf-8")
    out = root / f"u_{name}.png"
    cmd = [sys.executable, str(UNDERSTOOD), str(nf), "--project", str(proj_dir), "--view", "2", "--parts", "none", "--points", "none",
           "--out", str(out), *(["--swiftshader"] if a.swiftshader else [])]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(proj_dir))
    except subprocess.TimeoutExpired:
        r = subprocess.CompletedProcess(cmd, 124, "", "timeout")
    if not check(f"[U] understood.py {name} --view 2 exits 0 and writes the PNG", r.returncode == 0 and out.is_file(), (r.stderr or "")[-400:]):
        return None
    shutil.copy(out, shots / f"U_{name}.png")
    return Img(png=out.read_bytes())


def phase_understood(pbody, fbody, cams, ns):
    pv = (pbody or {}).get("view") or {}
    fv = (fbody or {}).get("view") or {}
    ps, fs = pv.get("shapes") or [], fv.get("shapes") or []
    real = [s.get("type") for s in ps] == ["pin", "pin", "move", "dim"] and [s.get("type") for s in fs] == ["face"] and len(pv.get("views") or []) == 2
    check("[U] setup: the page's own marks are rendered (else the same marks built from the geometry)", real)
    if real:
        views, shapes = pv["views"], ps + [{**fs[0], "view": 0}]
    else:
        views, shapes = cams, synthetic_marks(cams, ns)
    cam = views[1]
    note = lambda sh: {"msg_id": MSG_ID, "site": "public", "url": pv.get("url"), "hash": "", "cycle_t": None, "shapes": sh,
                       "camera": cam, "views": views}
    full = run_understood(note(shapes), "full")
    base = run_understood(note([]), "none")
    lines = run_understood(note([{**s, "type": "line"} if s.get("type") in ("move", "dim") else s for s in shapes]), "lines")
    if not (full and base):
        return
    pins = [s for s in shapes if s.get("type") == "pin"]
    marks_in(full, base, cam, pins, "[U] view 2:")
    face_in(full, base, cam, "[U] view 2: the face mark:", want=0.75, avoid=[s.get("path") or [] for s in shapes if s.get("type") != "face"])
    if not lines:
        return
    k = full.w / cam["w"]
    mv = next(s for s in shapes if s.get("type") == "move")
    dm = next(s for s in shapes if s.get("type") == "dim")
    tip = project(cam, mv["path"][1])
    if check("[U] setup: the move's head is in view", on_screen(tip, cam["w"], cam["h"]) and visible(cam, mv["path"][1]), str(tip)):
        n = changed_count(full, lines, *tip, r=14, thr=40, k=k, kb=k)
        check("[U] view 2: the move is drawn as an arrow, not a plain line (its head)", n >= 5, f"{n} px differ from a line near its end")
    d0, d1 = project(cam, dm["path"][0]), project(cam, dm["path"][1])
    m0, m1 = project(cam, mv["path"][0]), project(cam, mv["path"][1])
    if check("[U] setup: the dim is in view", d0 and d1 and on_screen(d0, cam["w"], cam["h"]) and on_screen(d1, cam["w"], cam["h"]), f"{d0} {d1}"):
        x0, x1 = int(min(d0[0], d1[0]) - 45), int(max(d0[0], d1[0]) + 45)
        y0, y1 = int(min(d0[1], d1[1]) - 45), int(max(d0[1], d1[1]) + 45)
        n = 0
        for y in range(max(0, y0), min(cam["h"], y1), 1):
            for x in range(max(0, x0), min(cam["w"], x1), 1):
                if seg_dist((x, y), d0, d1) > 40 or (m0 and m1 and seg_dist((x, y), m0, m1) < 25):
                    continue
                if any(math.dist((x, y), project(cam, p["path"][0]) or (-1e9, -1e9)) < 25 for p in pins):
                    continue
                n += diff_px(full.get(int(x * k), int(y * k)), lines.get(int(x * k), int(y * k))) >= 40
        check("[U] view 2: the dim is drawn as a dimension, not a plain line (its label / ticks)", n >= 20, f"{n} px differ from a line around it")


# ---------------------------------------------------------------- run
def main(p):
    b = p.chromium.launch(args=GPU_ARGS)
    ctx = b.new_context(viewport=VIEWPORT, device_scale_factor=1)
    ctx.add_init_script(TEXT_HOOK)
    page = ctx.new_page()
    errs, fb = [], []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.on("dialog", lambda d: d.accept())

    def on_fb(route):
        fb.append({"url": route.request.url, "body": json.loads(route.request.post_data or "{}")})
        route.fulfill(status=200, content_type="application/json", body='{"ok": true}')
    page.route("**/feedback/**", on_fb)
    page.route("**/model.json", lambda r: r.fulfill(status=200, content_type="application/json", body='{"v": 0, "building": false}'))

    def load():
        page.goto(f"{BASE}/{PROJECT}/?app=0", wait_until="load")
        page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=180000)
        page.wait_for_function("window.__viewer.feedback || window.__viewer.feedbackError", timeout=30000)
        if not page.evaluate("window.__viewer.state().panelCollapsed"):
            page.click("#pFold")
            page.wait_for_function("window.__viewer.state().panelCollapsed", timeout=5000)
    load()
    # setup for [U] when the page cannot make the marks: two known cameras and each part's mesh index
    top_view(page)
    cams = [page.evaluate("window.__viewer.camInfo()")]
    st = page.evaluate("window.__viewer.state()")
    page.evaluate("(s) => window.__viewer.setCam({yaw: s.yaw + 0.6, pitch: s.pitch - 0.5})", st)
    page.wait_for_timeout(700)
    cams.append(page.evaluate("window.__viewer.camInfo()"))
    ns = {pid: nl[0] for pid, nl in page.evaluate("window.__viewer.parts()") if nl}
    tool_buttons = page.evaluate("""() => ['pin', 'move', 'face', 'dim'].map((t) => !!document.querySelector(`#fbBar [data-tool="${t}"]`))""")
    check("the bar has the four new tools: pin, move, face, dim", all(tool_buttons), json.dumps(tool_buttons))

    out = {}
    for name, fn in (("[P]", lambda: phase_marks(page, fb)), ("[F]", lambda: phase_face(page, fb))):
        try:
            out[name] = fn()
        except Exception as e:
            check(f"{name} phase ran through", False, f"{type(e).__name__}: {str(e)[:300]}")
            try:
                page.keyboard.press("Escape")
                load()
            except Exception:
                pass
    check("console clean", not errs, "; ".join(errs[:3]))
    ctx.close(); b.close()
    return out.get("[P]"), out.get("[F]"), cams, ns


with sync_playwright() as p:
    pbody, fbody, cams, ns = main(p)
try:
    phase_understood(pbody, fbody, cams, ns)
except Exception as e:
    check("[U] phase ran through", False, f"{type(e).__name__}: {str(e)[:300]}")
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
