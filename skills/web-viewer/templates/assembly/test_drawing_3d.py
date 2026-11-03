#!/usr/bin/env python3
"""Real-browser check: drawings stick to the model in 3D, the camera turns freely, the drawing plane tool,
and understood.py renders the note's 3D shapes in a chosen view with the agent's --path sketch.

Same synthetic two-box model and harness as test_drawing_coords.py (build.py --up z --kit none --flash none, viewer_stack_server,
POST /feedback/** answered 200 by Playwright). Everything the client does goes through his door: #fbOpen, the tools in #fbBar
(select, arrow, line, ellipse, plane), real mouse drags on the page, a typed comment, the send button (i18n fb.send; the page is in its default language, ru). Camera presets (__viewer.setView)
and the page's picker are used only for setup (finding where the boxes are on screen, putting the camera on a known side).

  env -u VIRTUAL_ENV uv run --isolated --no-project --python 3.12 --with playwright --with numpy python test_drawing_3d.py [--swiftshader] [--shots DIR]

The model (assembly mm, Z up):  A «Left support»   10.01.001  x 0..40,   y 0..40, z 0..20
                                B «Right bracket»  10.02.001  x 80..120, y 0..40, z 0..30

Checked:
  [A] two shapes drawn without moving the camera -> view.views has ONE camera, both shapes have view 0, view.camera = views[0].
  [1] arrow from A to B (top view); select tool; a mouse drag on the model turns the camera; the arrow is drawn at its 3D place
      in the new view (red pixels where its 3D ends project, the tail inside A's screen area, the tip inside B's; not left at its old 2D
      place); a line drawn on B in the new view; send -> one POST, 2 shapes, views = [view 1, view 2], shape.view = 0 / 1,
      camera = views[1], each shape has its 3D `path` (arrow: [tail.at, tip.at] within 1 mm), every view's matrices map points to pixels,
      the JPEG `image` shows the arrow in view 2; no `selected`.
  [1h] hidden behind a part: an arrow from A's top to B's top, seen low (0.1 rad) from +X and from -X (a short marker line drawn on the near
      box in each side view, so the sent note carries both side cameras); the arrow's 3D points (tail .. past its middle) projected through
      each side camera: from B's side no red at any of them (B is in front), from A's side red at most of them. Points under page UI skipped.
  [2] the plane tool, a click on A's top face -> a visible translucent patch over the face (>= half of A's screen area changes
      colour); two lines drawn from A, one to
      over B, one into empty space beyond A's edge -> both carry plane {part A, normal +Z, at on A's top}, every path point and key point
      at z = 20 (A's top plane, NOT B's top at 30), the start on A, the end `off` with no part. The plane tool pressed again: the next line
      sticks to the surface again (its end on B at z = 30, no plane).
  [4] understood.py <note with views> --view 1 --parts none --points none --path "...": PNG of view 1 with both shapes in 3D
      (the arrow drawn in view 1 and the line drawn in view 2, both where their 3D paths project in view 1) and the green --path polyline;
      --view 2: the arrow where it projects in view 2, no green without --path.
Exit code 1 on any failed check.
"""
import argparse, atexit, json, math, shutil, subprocess, sys, tempfile
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
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="drawing3d_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
PROJECT = "draw3d-proj"
VIEWPORT = {"width": 1280, "height": 800}
A, B = "10.01.001", "10.02.001"
BOXES = {A: ("Left support", (0, 0, 0), (40, 40, 20)), B: ("Right bracket", (80, 0, 0), (120, 40, 30))}
T = ui_texts("ru")
MSG_ID = 6101
SKETCH = [(-15, -15, 0), (140, -15, 0), (140, 55, 0)]      # the agent's --path: round the boxes on the ground, nowhere near a part
CAM_KEYS = ("w", "h", "fov", "near", "far", "position", "target", "up", "view", "proj")
rows, ok_all = [], True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- synthetic model (as test_drawing_coords.py)
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


root = Path(tempfile.mkdtemp(prefix="drawing3d_e2e_"))
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


# ---------------------------------------------------------------- geometry and pixels
def project(cam, at):
    """Assembly mm -> CSS px through a camera's row-major view and proj (as the page sends them)."""
    P, V = np.array(cam["proj"], dtype=float).reshape(4, 4), np.array(cam["view"], dtype=float).reshape(4, 4)
    c = P @ V @ np.array([*at, 1.0])
    if c[3] <= 1e-9:
        return None
    return ((c[0] / c[3] + 1) / 2 * cam["w"], (1 - c[1] / c[3]) / 2 * cam["h"])


def red(p):       # the drawing's red (#e03131, possibly half transparent over a grey part); not orange, not magenta
    r, g, b = p[0], p[1], p[2]
    return r >= 120 and r - g >= 60 and r - b >= 60 and abs(int(g) - int(b)) <= 40


def green(p):
    r, g, b = p[0], p[1], p[2]
    return g >= 110 and g - r >= 50 and g - b >= 30


class Img:
    def __init__(self, png: bytes):
        self.w, self.h, self.ch, self.rows = png_pixels(png)

    def px(self, x, y):
        return self.rows[y][x * self.ch:x * self.ch + 3]

    def count(self, pred, x=None, y=None, r=5):
        if x is None:
            xs, ys = range(self.w), range(self.h)
        else:
            xs = range(max(0, int(round(x)) - r), min(self.w, int(round(x)) + r + 1))
            ys = range(max(0, int(round(y)) - r), min(self.h, int(round(y)) + r + 1))
        return sum(1 for yy in ys for xx in xs if pred(self.px(xx, yy)))

    def changed(self, other, box, thr=6, step=2):
        """Share of pixels in box whose colour moved by >= thr (mean of |dr|, |dg|, |db|) between self and other."""
        n = c = 0
        for yy in range(max(0, int(box[1])), min(self.h, int(box[3]) + 1), step):
            for xx in range(max(0, int(box[0])), min(self.w, int(box[2]) + 1), step):
                p, q = self.px(xx, yy), other.px(xx, yy)
                n += 1
                c += sum(abs(int(p[i]) - int(q[i])) for i in range(3)) / 3 >= thr
        return c / max(n, 1)

    def mean(self, x, y, r=4):
        ps = [self.px(xx, yy) for yy in range(int(y) - r, int(y) + r + 1) for xx in range(int(x) - r, int(x) + r + 1)]
        return [sum(p[i] for p in ps) / len(ps) for i in range(3)]


def inside(pt, box, pad=3):
    return pt is not None and box[0] - pad <= pt[0] <= box[2] + pad and box[1] - pad <= pt[1] <= box[3] + pad


def on_screen(pt, w, h, m=6):
    return pt is not None and m <= pt[0] <= w - m and m <= pt[1] <= h - m


def seg_dist(q, a, b):
    ab, aq = np.subtract(b, a), np.subtract(q, a)
    t = max(0.0, min(1.0, float(np.dot(aq, ab) / max(np.dot(ab, ab), 1e-9))))
    return float(np.linalg.norm(aq - t * ab))


def mid(p, q):
    return [(p[i] + q[i]) / 2 for i in range(3)]


def finite3(p):
    return isinstance(p, list) and len(p) == 3 and all(isinstance(c, (int, float)) and math.isfinite(c) for c in p)


# ---------------------------------------------------------------- page helpers
def screen_boxes(page):
    """Each box's screen rectangle, from the page's own picker over a 4 px grid (setup only)."""
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


def settle(page):
    page.wait_for_timeout(300)
    page.wait_for_function("!window.__viewer.state().flying", timeout=30000)
    page.wait_for_timeout(400)


def top_view(page):
    page.evaluate("window.__viewer.setView('top')")
    settle(page)
    page.evaluate("window.__viewer.setCam({dist: window.__viewer.state().dist * 1.4})")
    page.wait_for_timeout(600)


def side_view(page, name):
    page.evaluate(f"window.__viewer.setView('{name}')")
    settle(page)


def tool(page, name):
    page.click(f'#fbBar [data-tool="{name}"]', timeout=5000)


def send(page, text):
    page.fill("#fbPop textarea", text)
    page.locator("#fbPop").get_by_role("button", name=T["fb"]["send"]).click()
    page.wait_for_timeout(2600)          # the page shows «sent» and clears the drawing after ~2 s


def open_bar(page):
    """Start a fresh note: close a bar left open by the previous one, then the pen button."""
    if page.locator("#fbOpen").is_hidden():
        page.click("#fbBar [data-close]", timeout=5000)
        page.wait_for_timeout(300)
    page.click("#fbOpen", timeout=5000)


def ui_rects(page):
    """Screen rectangles of the page UI drawn over the model (popup, tool bar, hint, info card): pixels there are not the 3D view."""
    return page.evaluate("""() => { const out = [], W = innerWidth, H = innerHeight;
      for (const e of document.querySelectorAll('body *')) {
        if (e.tagName === 'CANVAS' || e.id === 'fbDraw' || e.closest('#fbDraw')) continue;
        const cs = getComputedStyle(e); if (cs.position !== 'fixed' && cs.position !== 'absolute') continue;
        if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) continue;
        const r = e.getBoundingClientRect(); if (r.width < 2 || r.height < 2 || r.width * r.height > 0.5 * W * H) continue;
        out.push([r.left, r.top, r.right, r.bottom]); }
      return out; }""")


def shot(page, name):
    png = page.screenshot(path=str(shots / name))
    im = Img(png)
    im.ui = ui_rects(page)
    return im


def covered(img, pt, m=7):
    return pt is None or not on_screen(pt, img.w, img.h, m) or any(r[0] - m <= pt[0] <= r[2] + m and r[1] - m <= pt[1] <= r[3] + m for r in getattr(img, "ui", []))


def decode_jpeg(page, data_url):
    """The note's JPEG `image` decoded by the browser: {w, h, rgb: flat list}."""
    return page.evaluate("""async (src) => { const im = new Image(); im.src = src; await im.decode();
      const c = document.createElement('canvas'); c.width = im.width; c.height = im.height; const g = c.getContext('2d'); g.drawImage(im, 0, 0);
      const d = g.getImageData(0, 0, im.width, im.height).data, rgb = new Array(im.width * im.height * 3);
      for (let i = 0, j = 0; i < d.length; i += 4) { rgb[j++] = d[i]; rgb[j++] = d[i + 1]; rgb[j++] = d[i + 2]; }
      return { w: im.width, h: im.height, rgb }; }""", data_url)


def jpeg_count(img, pred, x, y, r=6):
    n = 0
    for yy in range(max(0, int(y) - r), min(img["h"], int(y) + r + 1)):
        for xx in range(max(0, int(x) - r), min(img["w"], int(x) + r + 1)):
            i = (yy * img["w"] + xx) * 3
            n += pred(img["rgb"][i:i + 3])
    return n


# ---------------------------------------------------------------- the client
def phase_one_view(page, fb):
    """[A] Two shapes without moving the camera: one view."""
    top_view(page)
    sb = screen_boxes(page)
    if not check("[A] setup: both boxes on screen", A in sb and B in sb, json.dumps(sb)):
        return
    c = {k: ((v[0] + v[2]) / 2, (v[1] + v[3]) / 2) for k, v in sb.items()}
    open_bar(page)
    tool(page, "arrow")
    drag(page, c[A], c[B])
    tool(page, "ellipse")
    bx0, by0, bx1, by1 = sb[B]
    drag(page, (bx0 + 0.2 * (bx1 - bx0), by0 + 0.2 * (by1 - by0)), (bx1 - 0.2 * (bx1 - bx0), by1 - 0.2 * (by1 - by0)))
    n0 = len(fb)
    send(page, "two shapes, one view")
    if not check("[A] one POST", len(fb) == n0 + 1, f"{len(fb) - n0} POST(s)"):
        return
    v = fb[-1]["body"].get("view") or {}
    views, shapes = v.get("views"), v.get("shapes") or []
    check("[A] drawing again without moving the camera adds no view: views has one camera", isinstance(views, list) and len(views) == 1,
          f"{len(views) if isinstance(views, list) else views!r} views")
    check("[A] both shapes have view 0", [s.get('view') for s in shapes] == [0, 0], json.dumps([s.get('view') for s in shapes]))
    if isinstance(views, list) and views:
        check("[A] view.camera = the last view", v.get("camera") == views[-1])


def phase_turn_and_draw(page, fb):
    """[1] Done-when 1: draw, turn the camera with the select tool, draw again, send."""
    top_view(page)
    sb1 = screen_boxes(page)
    if not check("[1] setup: both boxes on screen in the top view", A in sb1 and B in sb1, json.dumps(sb1)):
        return None
    ax0, ay0, ax1, ay1 = sb1[A]; bx0, by0, bx1, by1 = sb1[B]
    tail = (round((ax0 + ax1) / 2), round(ay0 + 0.7 * (ay1 - ay0)))
    tip = (round((bx0 + bx1) / 2), round(by0 + 0.7 * (by1 - by0)))
    open_bar(page)
    tool(page, "arrow")
    check("[1] setup: the arrow's ends are on the drawing layer", free_for_drawing(page, [tail, tip]) == ["fbDraw", "fbDraw"])
    drag(page, tail, tip)
    shot(page, "1a_arrow_top.png")

    tool(page, "select")
    st0 = page.evaluate("window.__viewer.state()")
    grab = (round(bx0 + 0.85 * (bx1 - bx0)), round(by0 + 0.2 * (by1 - by0)))       # on B, away from the arrow
    check("[1] setup: the drag starts on the model (B)", part_at(page, grab) == B, str(grab))
    drag(page, grab, (grab[0] - 170, grab[1] + 90), steps=24)
    settle(page)
    st1 = page.evaluate("window.__viewer.state()")
    turned = abs(st1["yaw"] - st0["yaw"]) + abs(st1["pitch"] - st0["pitch"])
    if not check("[1] with shapes on the model, a select-tool drag on the model turns the camera", turned > 0.15,
                 f"yaw {st0['yaw']:.3f} -> {st1['yaw']:.3f}, pitch {st0['pitch']:.3f} -> {st1['pitch']:.3f}"):
        return None
    sb2 = screen_boxes(page)
    if not check("[1] setup: both boxes on screen in the turned view", A in sb2 and B in sb2, json.dumps(sb2)):
        return None
    turned_img = shot(page, "1b_turned.png")

    cbx, cby = (sb2[B][0] + sb2[B][2]) / 2, (sb2[B][1] + sb2[B][3]) / 2
    hw = 0.25 * (sb2[B][2] - sb2[B][0])
    l0, l1 = (round(cbx - hw), round(cby)), (round(cbx + hw), round(cby))
    check("[1] setup: the second line lies on B", part_at(page, l0) == B and part_at(page, l1) == B, f"{l0} {l1}")
    tool(page, "line")
    drag(page, l0, l1)
    shot(page, "1c_second_shape.png")
    n0 = len(fb)
    send(page, "an arrow and a line from two sides")
    if not check("[1] one POST to /feedback/<project>", len(fb) == n0 + 1 and fb[-1]["url"].split("?")[0].endswith(f"/feedback/{PROJECT}"), f"{len(fb) - n0} POST(s)"):
        return None
    body = fb[-1]["body"]
    return body, sb1, sb2, tail, tip, turned_img


def check_turned_note(page, res):
    body, sb1, sb2, tail_px, tip_px, img2 = res
    v = body.get("view") or {}
    shapes, views = v.get("shapes") or [], v.get("views")
    if not check("[1] the note has both shapes: [arrow, line]", [s.get('type') for s in shapes] == ["arrow", "line"], json.dumps([s.get('type') for s in shapes])):
        return None
    if not check("[1] views = 2 cameras", isinstance(views, list) and len(views) == 2, f"{len(views) if isinstance(views, list) else views!r}"):
        return None
    for i, cam in enumerate(views):
        check(f"[1] views[{i}] has {', '.join(CAM_KEYS)}", isinstance(cam, dict) and all(k in cam for k in CAM_KEYS), ",".join(sorted(cam)) if isinstance(cam, dict) else repr(cam))
        check(f"[1] views[{i}] view/proj are 16 numbers", isinstance(cam, dict) and all(isinstance(cam.get(k), list) and len(cam[k]) == 16 for k in ("view", "proj")))
        check(f"[1] views[{i}] is the viewport size", isinstance(cam, dict) and (cam.get("w"), cam.get("h")) == (VIEWPORT["width"], VIEWPORT["height"]))
    check("[1] the two views differ (the camera was turned)", views[0].get("position") != views[1].get("position"))
    check("[1] view.camera = the last view", v.get("camera") == views[1])
    # the album: the earlier view's picture rides along, and the whole body stays under the host nginx's 1 MB cap
    imgs = body.get("images") or []
    check("[1] images = 1 picture (the first view), image = the view at send", len(imgs) == 1 and str(imgs[0]).startswith("data:image/jpeg;base64,")
          and str(body.get("image", "")).startswith("data:image/jpeg;base64,"), f"{len(imgs)} earlier picture(s)")
    check("[1] the whole POST body is under 1 MB", len(json.dumps(body)) < 1_000_000, f"{len(json.dumps(body))} bytes")
    if imgs:
        nred = page.evaluate("""async (u) => { const im = new Image(); im.src = u; await im.decode(); const c = document.createElement('canvas');
            c.width = im.width; c.height = im.height; const x = c.getContext('2d'); x.drawImage(im, 0, 0); const d = x.getImageData(0, 0, c.width, c.height).data;
            let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] > 180 && d[i + 1] < 90 && d[i + 2] < 90) n++; return n; }""", imgs[0])
        check("[1] the first view's picture shows the red arrow", nred > 30, f"{nred} red px")
    check("[1] shape.view: the arrow in view 0, the line in view 1", [s.get('view') for s in shapes] == [0, 1], json.dumps([s.get('view') for s in shapes]))
    check("[1] no `selected`", "selected" not in v, ",".join(sorted(v)))
    ar, ln = shapes
    t, h = (ar.get("pts") or [{}, {}])[:2]
    check("[1] arrow tail on A's top, tip on B's top", t.get("part") == A and h.get("part") == B and abs((t.get("at") or [0, 0, 0])[2] - 20) <= 1
          and abs((h.get("at") or [0, 0, 0])[2] - 30) <= 1, json.dumps([t, h]))
    check("[1] the line drawn in view 2 is on B", all(p.get("part") == B for p in ln.get("pts") or [{}]), json.dumps(ln.get("pts")))
    path = ar.get("path")
    if not check("[1] the arrow has a 3D path of 2 points", isinstance(path, list) and len(path) == 2 and all(finite3(p) for p in path), json.dumps(path)):
        return None
    check("[1] arrow path = [tail.at, tip.at] within 1 mm", math.dist(path[0], t["at"]) <= 1.01 and math.dist(path[1], h["at"]) <= 1.01, json.dumps([path, t.get("at"), h.get("at")]))
    lpath = ln.get("path")
    check("[1] the line has a 3D path of 2 points on B's top", isinstance(lpath, list) and len(lpath) == 2 and all(finite3(p) and abs(p[2] - 30) <= 1.01 for p in lpath), json.dumps(lpath))
    d1 = math.dist(project(views[0], t["at"]) or (1e9, 1e9), t["px"])
    check("[1] views[0] maps the tail's mm point onto its pixel (+-3 px)", d1 <= 3, f"{d1:.1f} px")
    lp = (ln.get("pts") or [{}])[0]
    if lp.get("at") and lp.get("px"):
        d2 = math.dist(project(views[1], lp["at"]) or (1e9, 1e9), lp["px"])
        check("[1] views[1] maps the line's mm point onto its pixel (+-3 px)", d2 <= 3, f"{d2:.1f} px")

    # pixels in the turned view (screenshot taken after the drag, before the second line)
    W, H = img2.w, img2.h
    tv, hv, mv = project(views[1], path[0]), project(views[1], path[1]), project(views[1], mid(path[0], path[1]))
    if not check("[1] setup: the arrow's ends project on screen in view 2", on_screen(tv, W, H) and on_screen(hv, W, H), f"{tv} {hv}"):
        return views, shapes
    check("[1] setup: the turn moved the arrow's tail on screen by >= 25 px", math.dist(tv, tail_px) >= 25, f"{tv} vs {tail_px}")
    check("[1] after turning, the arrow's tail still sits on A (inside A's screen area)", inside(tv, sb2[A]), f"{tv} in {sb2[A]}")
    check("[1] after turning, the arrow's tip still sits on B (inside B's screen area)", inside(hv, sb2[B]), f"{hv} in {sb2[B]}")
    if not covered(img2, tv):
        check("[1] the arrow is drawn at its tail's 3D place in view 2 (red near it)", img2.count(red, *tv) >= 2, f"{img2.count(red, *tv)} red px near {tv}")
    along = [project(views[1], [path[0][i] + t * (path[1][i] - path[0][i]) for i in range(3)]) for t in (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8)]
    free = [q for q in along if not covered(img2, q)]
    if check("[1] setup: some of the arrow's middle is not under the page UI in view 2", free, f"{along}"):
        q = free[0]
        check("[1] the arrow is drawn along its 3D place in view 2 (red near its middle)", img2.count(red, *q) >= 2, f"{img2.count(red, *q)} red px near {q}")
    old_mid = ((tail_px[0] + tip_px[0]) / 2, (tail_px[1] + tip_px[1]) / 2)
    if seg_dist(old_mid, tv, hv) >= 25 and not covered(img2, old_mid):
        check("[1] the arrow is not left at its old 2D place", img2.count(red, *old_mid) == 0, f"{img2.count(red, *old_mid)} red px at the old middle {old_mid}")

    img = body.get("image")
    if check("[1] the note's image is a JPEG", isinstance(img, str) and img.startswith("data:image/jpeg"), (img or "")[:30]):
        j = decode_jpeg(page, img)
        k = j["w"] / views[1]["w"]
        n = jpeg_count(j, red, mv[0] * k, mv[1] * k, r=max(4, int(6 * k)))
        check("[1] the image is the last view with the 3D arrow in it (red at the arrow's middle in view 2)", n >= 2, f"{n} red px, image {j['w']}x{j['h']}")
    return views, shapes


def side_cam(page, yaw):
    """Setup: the whole model from one side, 0.1 rad above the horizon (yaw +-pi/2 = from +X / -X)."""
    top_view(page)
    page.evaluate(f"window.__viewer.setCam({{yaw: {yaw}, pitch: 0.1}})")
    page.wait_for_timeout(800)


def phase_hidden(page, fb):
    """[1h] An arrow from A's top to B's top (z 20 -> 30) seen low from +X and from -X. From B's side B is in front of it: hidden.
    From A's side it runs above A: seen. A short line drawn low on the near box in each side view makes the note carry that view's camera,
    so the arrow's 3D points are projected exactly into each screenshot."""
    top_view(page)
    sb = screen_boxes(page)
    if not check("[1h] setup: both boxes on screen", A in sb and B in sb, json.dumps(sb)):
        return
    c = {k: ((v[0] + v[2]) / 2, (v[1] + v[3]) / 2) for k, v in sb.items()}
    open_bar(page)
    tool(page, "arrow")
    drag(page, c[A], c[B])
    imgs, marks = [], []
    for i, yaw in enumerate((math.pi / 2, -math.pi / 2)):
        tool(page, "select")
        side_cam(page, yaw)
        sbs = screen_boxes(page)
        if not check(f"[1h] setup: side view {i + 1} shows the model", sbs, json.dumps(sbs)):
            return
        near = max(sbs.values(), key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        w, h = near[2] - near[0], near[3] - near[1]
        p0, p1 = (round(near[0] + 0.7 * w), round(near[1] + 0.88 * h)), (round(near[0] + 0.85 * w), round(near[1] + 0.88 * h))
        check(f"[1h] setup: the marker line in side view {i + 1} is on a part", part_at(page, p0) and part_at(page, p1), f"{p0} {p1}")
        tool(page, "line")
        drag(page, p0, p1)
        tool(page, "select")
        page.mouse.move(3, 3)
        imgs.append(shot(page, f"2_side{i + 1}.png"))
        marks.append((p0, p1))
    n0 = len(fb)
    send(page, "from the side")
    if not check("[1h] one POST", len(fb) == n0 + 1, f"{len(fb) - n0} POST(s)"):
        return
    v = fb[-1]["body"].get("view") or {}
    views, shapes = v.get("views") or [], v.get("shapes") or []
    if not check("[1h] the note has the arrow and 3 views (top, side 1, side 2)", len(views) == 3 and shapes and shapes[0].get("type") == "arrow"
                 and isinstance(shapes[0].get("path"), list) and len(shapes[0]["path"]) == 2, f"{len(views)} views, {[s.get('type') for s in shapes]}"):
        return
    p, q = shapes[0]["path"]
    seen = []
    for i, (img, mk) in enumerate(zip(imgs, marks), 1):
        pts = [project(views[i], [p[k] + t * (q[k] - p[k]) for k in range(3)]) for t in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)]
        pts = [x for x in pts if not covered(img, x) and seg_dist(x, mk[0], mk[1]) >= 14]
        seen.append([img.count(red, *x, r=3) for x in pts])
    print("[1h] red px near the arrow's points (tail .. past its middle), per side view:", seen)
    if not check("[1h] setup: at least 3 of the arrow's points are clear of the page UI in each side view", all(len(x) >= 3 for x in seen), json.dumps(seen)):
        return
    hid = min(range(2), key=lambda i: sum(1 for n in seen[i] if n))
    vis = 1 - hid
    shown = sum(1 for n in seen[vis] if n) / len(seen[vis])
    check("[1h] from A's side the arrow shows where it runs above A (drawn in 3D in a side view)", shown >= 0.6, json.dumps(seen[vis]))
    check("[1h] from B's side B is in front of it: hidden", all(n == 0 for n in seen[hid]), json.dumps(seen[hid]))


def phase_plane(page, fb):
    """[2] Done-when 2: the plane tool on A's top face, lines past A's edge."""
    top_view(page)
    sb = screen_boxes(page)
    if not check("[2] setup: both boxes on screen", A in sb and B in sb, json.dumps(sb)):
        return None
    ax0, ay0, ax1, ay1 = sb[A]; bx0, by0, bx1, by1 = sb[B]
    ca, cb = ((ax0 + ax1) / 2, (ay0 + ay1) / 2), ((bx0 + bx1) / 2, (by0 + by1) / 2)
    gap = ((ax1 + bx0) / 2, ca[1])
    away = (round(ax0 - 0.6 * (ax1 - ax0)), round(ay0 + 0.3 * (ay1 - ay0)))       # left of A: empty space
    open_bar(page)
    check("[2] setup: the spot beyond A is empty space on the drawing layer", part_at(page, away) is None and free_for_drawing(page, [away]) == ["fbDraw"],
          f"{away}: {part_at(page, away)} {free_for_drawing(page, [away])}")
    tool(page, "plane")
    page.mouse.move(*gap)
    before = shot(page, "3_plane_before.png")
    page.mouse.click(round(ca[0]), round(ca[1]))
    page.wait_for_timeout(300)
    page.mouse.move(*gap)
    after = shot(page, "3_plane_after.png")
    share = after.changed(before, sb[A])
    m0, m1 = before.mean(*ca), after.mean(*ca)
    check("[2] a click on A's face with the plane tool shows a translucent patch over the face", share >= 0.5,
          f"{share:.0%} of A's screen area changed colour; centre {[round(x) for x in m0]} -> {[round(x) for x in m1]}")
    start1 = (round(ax0 + 0.3 * (ax1 - ax0)), round(ay0 + 0.45 * (ay1 - ay0)))
    start2 = (round(ax0 + 0.3 * (ax1 - ax0)), round(ay0 + 0.3 * (ay1 - ay0)))
    start3 = (round(ax0 + 0.7 * (ax1 - ax0)), round(ay0 + 0.75 * (ay1 - ay0)))
    end3 = (round(bx0 + 0.5 * (bx1 - bx0)), round(by0 + 0.75 * (by1 - by0)))
    tool(page, "line")
    drag(page, start1, (round(cb[0]), round(cb[1])))        # over B: in the plane it is z = 20, under B's top
    drag(page, start2, away)                                   # into empty space beyond A's edge
    shot(page, "3_plane_lines.png")
    tool(page, "plane")                                        # pressed again: the plane is gone
    tool(page, "line")
    drag(page, start3, end3)                                   # sticks to the surfaces again
    n0 = len(fb)
    send(page, "extend the face")
    if not check("[2] one POST", len(fb) == n0 + 1, f"{len(fb) - n0} POST(s)"):
        return None
    v = fb[-1]["body"].get("view") or {}
    shapes = v.get("shapes") or []
    if not check("[2] three lines in the note", [s.get('type') for s in shapes] == ["line", "line", "line"], json.dumps([s.get('type') for s in shapes])):
        return None
    for i, (s, what) in enumerate(zip(shapes[:2], ("over B", "into empty space")), 1):
        pl = s.get("plane") or {}
        check(f"[2] line {i} ({what}) carries the plane of A's top face", pl.get("part") == A and finite3(pl.get("at")) and abs(pl["at"][2] - 20) <= 0.5
              and finite3(pl.get("normal")) and pl["normal"][2] > 0.99, json.dumps(pl))
        path = s.get("path")
        check(f"[2] line {i}: its 3D path lies in A's top plane (z = 20)", isinstance(path, list) and len(path) == 2 and all(finite3(p) and abs(p[2] - 20) <= 0.05 for p in path), json.dumps(path))
        pts = s.get("pts") or []
        check(f"[2] line {i}: every key point is on the plane (plane: true, z = 20, normal +Z)",
              pts and all(p.get("plane") is True and finite3(p.get("at")) and abs(p["at"][2] - 20) <= 0.05 and finite3(p.get("normal")) and p["normal"][2] > 0.99 for p in pts),
              json.dumps(pts)[:400])
        if pts:
            check(f"[2] line {i}: the start is on A", pts[0].get("part") == A and not pts[0].get("off"), json.dumps(pts[0]))
            e = pts[-1]
            check(f"[2] line {i}: the end is beyond A's edge (off, no part)", e.get("off") is True and not e.get("part"), json.dumps(e))
            if finite3(e.get("at")):
                lo, hi = BOXES[A][1], BOXES[A][2]
                outside_a = not (lo[0] - 2 <= e["at"][0] <= hi[0] + 2 and lo[1] - 2 <= e["at"][1] <= hi[1] + 2)
                check(f"[2] line {i}: the end's mm point is outside A's footprint", outside_a, json.dumps(e["at"]))
                if i == 1:
                    blo, bhi = BOXES[B][1], BOXES[B][2]
                    check("[2] line 1: the end lies under B's top, on A's plane (not on B at z = 30)",
                          blo[0] - 8 <= e["at"][0] <= bhi[0] + 8 and abs(e["at"][2] - 20) <= 0.05, json.dumps(e["at"]))
    s3 = shapes[2]
    check("[2] after pressing the plane tool again the next line has no plane", not s3.get("plane"), json.dumps(s3.get("plane")))
    e3 = (s3.get("pts") or [{}])[-1]
    check("[2] ... and its end sticks to B's top (z = 30)", e3.get("part") == B and finite3(e3.get("at")) and abs(e3["at"][2] - 30) <= 1.01 and not e3.get("plane"), json.dumps(e3))
    return v


def run_understood(args, out_png):
    cmd = [sys.executable, str(UNDERSTOOD), *map(str, args), "--out", str(out_png), *(["--swiftshader"] if a.swiftshader else [])]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(proj_dir))
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, 124, e.stdout or "", "timeout")


def check_understood(view, views, shapes):
    """[4] Done-when 4: understood.py --view N renders the 3D shapes in that view, --path draws the agent's sketch in green."""
    note = {"msg_id": MSG_ID, "site": "public", "url": view.get("url"), "hash": view.get("hash"), "cycle_t": view.get("cycle_t"),
            "shapes": shapes, "camera": view.get("camera"), "views": views}
    (proj_dir / "build" / "notes").mkdir(parents=True, exist_ok=True)
    nf = proj_dir / "build" / "notes" / f"{MSG_ID}.json"
    nf.write_text(json.dumps(note, ensure_ascii=False), encoding="utf-8")
    arrow, line = shapes
    sketch = ";".join(",".join(str(c) for c in p) for p in SKETCH) + ":gusset"
    sketch_mids = [mid(SKETCH[i], SKETCH[i + 1]) for i in range(len(SKETCH) - 1)]

    out1 = root / "u_view1.png"
    r = run_understood([MSG_ID, "--project", proj_dir, "--view", 1, "--parts", "none", "--points", "none", "--path", sketch], out1)
    if check("[4] understood.py <id> --view 1 --path ... exits 0 and writes the PNG", r.returncode == 0 and out1.is_file(), (r.stderr or "")[-400:]):
        shutil.copy(out1, shots / "4_understood_view1.png")
        im = Img(out1.read_bytes())
        cam = views[0]
        check("[4] --view 1: the PNG is view 1's size", (im.w, im.h) == (cam["w"], cam["h"]), f"{im.w}x{im.h}")
        am = project(cam, mid(*arrow["path"]))
        check("[4] --view 1: the arrow is drawn where its 3D path projects in view 1", am is not None and im.count(red, *am) >= 2, f"{im.count(red, *am) if am else '-'} red px near {am}")
        lm = project(cam, mid(*line["path"]))
        check("[4] --view 1: the line drawn in view 2 is drawn where its 3D path projects in view 1", lm is not None and im.count(red, *lm) >= 2,
              f"{im.count(red, *lm) if lm else '-'} red px near {lm}")
        for i, m in enumerate(sketch_mids, 1):
            q = project(cam, m)
            check(f"[4] --view 1: the agent's --path segment {i} is green where it projects", q is not None and on_screen(q, im.w, im.h) and im.count(green, *q) >= 2,
                  f"{im.count(green, *q) if q else '-'} green px near {q}")

    out2 = root / "u_view2.png"
    r = run_understood([nf, "--project", proj_dir, "--view", 2, "--parts", "none", "--points", "none"], out2)
    if check("[4] understood.py NOTE.json --view 2 exits 0 and writes the PNG", r.returncode == 0 and out2.is_file(), (r.stderr or "")[-400:]):
        shutil.copy(out2, shots / "4_understood_view2.png")
        im = Img(out2.read_bytes())
        am = project(views[1], mid(*arrow["path"]))
        check("[4] --view 2: the arrow is drawn where its 3D path projects in view 2", am is not None and im.count(red, *am) >= 2, f"{im.count(red, *am) if am else '-'} red px near {am}")
        for i, m in enumerate(sketch_mids, 1):
            q = project(views[1], m)
            if q is not None and on_screen(q, im.w, im.h):
                check(f"[4] --view 2 without --path: no green at sketch segment {i}", im.count(green, *q) == 0, f"{im.count(green, *q)} green px")


# ---------------------------------------------------------------- run
def main(p):
    b = p.chromium.launch(args=GPU_ARGS)
    ctx = b.new_context(viewport=VIEWPORT, device_scale_factor=1)
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
    page.goto(f"{BASE}/{PROJECT}/?app=0", wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=180000)
    page.wait_for_function("window.__viewer.feedback || window.__viewer.feedbackError", timeout=30000)
    page.click("#pFold")
    page.wait_for_function("window.__viewer.state().panelCollapsed", timeout=5000)

    turned = None
    for name, fn in (("[A]", lambda: phase_one_view(page, fb)), ("[1]", lambda: phase_turn_and_draw(page, fb)),
                     ("[1h]", lambda: phase_hidden(page, fb)), ("[2]", lambda: phase_plane(page, fb))):
        try:
            out = fn()
            if name == "[1]" and out:
                checked = check_turned_note(page, out)
                if checked:
                    turned = (out[0].get("view") or {}, *checked)
        except Exception as e:   # a missing tool or button: the phase fails, the others still run
            check(f"{name} phase ran through", False, f"{type(e).__name__}: {str(e)[:300]}")
            try:
                page.keyboard.press("Escape")
                page.reload(wait_until="load")
                page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=180000)
                page.wait_for_function("window.__viewer.feedback || window.__viewer.feedbackError", timeout=30000)
                if not page.evaluate("window.__viewer.state().panelCollapsed"):
                    page.click("#pFold")
            except Exception:
                pass
    check("console clean", not errs, "; ".join(errs[:3]))
    ctx.close(); b.close()
    return turned


with sync_playwright() as p:
    turned = main(p)
if turned:
    view, views, shapes = turned
    (shots / "view_turned.json").write_text(json.dumps(view, ensure_ascii=False, indent=1), encoding="utf-8")
    if all(isinstance(s.get("path"), list) and len(s["path"]) >= 2 for s in shapes):
        check_understood(view, views, shapes)
    else:
        check("[4] setup: the note's shapes have 3D paths for understood.py", False)
else:
    check("[4] understood.py: skipped, no 2-view note from [1]", False)
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
