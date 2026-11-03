#!/usr/bin/env python3
"""Real-browser check: a drawing lands on the model's surface, and understood.py shows how the bot understood it.

A SYNTHETIC model with known geometry (two boxes, CAD frame Z up, mm), written with step-import's write_model_dir, built with THIS template
(build.py --up z --kit none --flash none, default --feedback auto) and served under /<project>/ the way the stack does (viewer_stack_server).
POST /feedback/** is intercepted by Playwright and answered 200, so no router is needed. The client's door is used: the pen button #fbOpen,
the ellipse and arrow tools in #fbBar, real mouse drags on #fbDraw, a typed comment, the send button (i18n fb.send; the page is in its default language, ru).

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright --with numpy python test_drawing_coords.py [--swiftshader] [--shots DIR]
(first run: `... --with playwright playwright install chromium`)

The model (assembly mm, Z up):  A «Left support»   10.01.001  x 0..40,   y 0..40, z 0..20
                                B «Right bracket»  10.02.001  x 80..120, y 0..40, z 0..30
The camera is put on the standard top view (__viewer.setView("top")): every hit is on a box's top face, normal +Z of the CAD frame
(with --up z the viewer's world is rotated: the world normal would be +Y, so this also checks "at"/"normal" are in assembly coordinates).

Checked:
  [1] the drawn note's view.shapes: ellipse around B (shape 1), arrow from A to B (shape 2): arrow pts = [tail on A, tip on B], `at` inside the
      boxes' known extents (+-1 mm) on their top faces (z = 20 / 30), normal ~ (0, 0, 1); the ellipse's centre is on B, its `inside` counts
      have B and not A; view.parts = [B, A] (first appearance); view.camera {w, h = the viewport, fov, position above the target, view/proj
      of 16 numbers that project the tail's `at` back onto the tail's pixel}.
  [2] the drawn note has NO `selected` key.
  [4] understood.py on a note made from that view ({msg_id, site, url, hash, cycle_t, shapes, camera} in <project>/build/notes/<msg_id>.json,
      called with the message id): exit 0, prints the PNG path (default <project>/build/understood/<msg_id>.png), PNG is w x h,
      A's centre is magenta (A is hit by the arrow's tail), the gap between the boxes is not. With --parts B --points 2.tip: B magenta, A not.
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
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="drawing_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
PROJECT = "draw-proj"
VIEWPORT = {"width": 1280, "height": 800}
TOL = 1.0
A, B = "10.01.001", "10.02.001"
BOXES = {A: ("Left support", (0, 0, 0), (40, 40, 20)), B: ("Right bracket", (80, 0, 0), (120, 40, 30))}
T = ui_texts("ru")
MSG_ID = 6001
rows, ok_all = [], True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- synthetic model: two boxes, flat faces, outward winding
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


root = Path(tempfile.mkdtemp(prefix="drawing_e2e_"))
atexit.register(lambda: shutil.rmtree(root, ignore_errors=True))
model_dir, proj_dir, served = root / "model", root / "project", root / "served"
write_model(model_dir)
b_site = subprocess.run([sys.executable, str(HERE / "build.py"), "--in", str(model_dir), "--out", str(proj_dir / "public"), "--up", "z",
                         "--kit", "none", "--flash", "none"], capture_output=True, text=True)
if b_site.returncode:
    sys.exit("build.py failed:\n" + b_site.stderr[-2000:])
served.mkdir()
(served / PROJECT).symlink_to(proj_dir / "public")     # /<project>/ = the project's public/ dir, as on the stack
srv, BASE = serve_stack(served)
atexit.register(srv.shutdown)


# ---------------------------------------------------------------- helpers
def screen_boxes(page):
    """Each box's screen rectangle, from the page's own picker over a 4 px grid (setup only, not what is checked)."""
    return page.evaluate("""() => { const v = window.__viewer, out = {};
      for (let y = 2; y < innerHeight; y += 4) for (let x = 2; x < innerWidth; x += 4) { const h = v.pick(x, y); if (!h || !h.part) continue;
        const b = out[h.part] || (out[h.part] = [1e9, 1e9, -1e9, -1e9]); b[0] = Math.min(b[0], x); b[1] = Math.min(b[1], y); b[2] = Math.max(b[2], x); b[3] = Math.max(b[3], y); }
      return out; }""")


def free_for_drawing(page, pts):
    return page.evaluate("(pts) => pts.map(([x, y]) => { const e = document.elementFromPoint(x, y); return e ? e.id : null; })", pts)


def drag(page, p0, p1, steps=12):
    page.mouse.move(*p0); page.mouse.down(); page.mouse.move(*p1, steps=steps); page.mouse.up(); page.wait_for_timeout(250)


def within(at, lo, hi, tol=TOL):
    return isinstance(at, list) and len(at) == 3 and all(lo[i] - tol <= at[i] <= hi[i] + tol for i in range(3))


def unit_z(n):
    return isinstance(n, list) and len(n) == 3 and abs(math.hypot(*n) - 1) < 0.02 and n[2] > 0.99


def project(cam, at, col_major):
    def m(arr):
        M = np.array(arr, dtype=float).reshape(4, 4)
        return M.T if col_major else M
    c = m(cam["proj"]) @ m(cam["view"]) @ np.array([*at, 1.0])
    if abs(c[3]) < 1e-9:
        return None
    return ((c[0] / c[3] + 1) / 2 * cam["w"], (1 - c[1] / c[3]) / 2 * cam["h"])


def magenta(px):
    r, g, b = px[0], px[1], px[2]
    return r >= 140 and b >= 140 and g <= 0.65 * min(r, b)


def patch(png_rows, ch, x, y, r=3):
    return [png_rows[yy][xx * ch:xx * ch + 3] for yy in range(int(y) - r, int(y) + r + 1) for xx in range(int(x) - r, int(x) + r + 1)]


def mostly_magenta(png_rows, ch, x, y):
    px = patch(png_rows, ch, x, y)
    return sum(1 for p in px if magenta(p)) >= 0.7 * len(px), px[len(px) // 2]


def run_understood(args, out_png=None):
    cmd = [sys.executable, str(UNDERSTOOD), *map(str, args), *(["--swiftshader"] if a.swiftshader else [])]
    if out_png:
        cmd += ["--out", str(out_png)]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(proj_dir))
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, 124, e.stdout or "", "timeout")


# ---------------------------------------------------------------- the client draws
def run(p):
    b = p.chromium.launch(args=GPU_ARGS)
    ctx = b.new_context(viewport=VIEWPORT, device_scale_factor=1)
    page = ctx.new_page()
    errs, fb = [], []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))

    def on_fb(route):
        fb.append({"url": route.request.url, "method": route.request.method, "body": json.loads(route.request.post_data or "{}")})
        route.fulfill(status=200, content_type="application/json", body='{"ok": true}')
    page.route("**/feedback/**", on_fb)
    page.route("**/model.json", lambda r: r.fulfill(status=200, content_type="application/json", body='{"v": 0, "building": false}'))
    page.goto(f"{BASE}/{PROJECT}/?app=0", wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=180000)
    page.wait_for_function("window.__viewer.feedback || window.__viewer.feedbackError", timeout=30000)
    page.click("#pFold")                                    # fold the panel, as a client does: it would cover box A
    page.wait_for_function("window.__viewer.state().panelCollapsed", timeout=5000)
    page.evaluate("window.__viewer.setView('top')")
    page.wait_for_timeout(300)
    page.wait_for_function("!window.__viewer.state().flying", timeout=30000)
    page.evaluate("window.__viewer.setCam({dist: window.__viewer.state().dist * 1.4})")   # room around the model for the ellipse
    page.wait_for_timeout(600)
    sb = screen_boxes(page)
    if not check("setup: both boxes are on screen in the top view", A in sb and B in sb, json.dumps(sb)):
        return None
    ax0, ay0, ax1, ay1 = sb[A]; bx0, by0, bx1, by1 = sb[B]
    centre = {k: ((v[0] + v[2]) / 2, (v[1] + v[3]) / 2) for k, v in sb.items()}
    gap = ((ax1 + bx0) / 2, (centre[A][1] + centre[B][1]) / 2)          # between the boxes: no part, no stroke
    mx, my = 0.12 * (bx1 - bx0), 0.12 * (by1 - by0)
    ell = ((bx0 - mx, by0 - my), (bx1 + mx, by1 + my))
    tail = (round(centre[A][0]), round(ay0 + 0.75 * (ay1 - ay0)))       # off A's centre: the centre pixel stays clear in the render
    tip = (round(centre[B][0]), round(by0 + 0.75 * (by1 - by0)))
    page.screenshot(path=str(shots / "0_top_view.png"))

    page.click("#fbOpen")
    page.click("#fbBar [data-tool=ellipse]")
    over = free_for_drawing(page, [ell[0], ell[1]])
    check("setup: the ellipse's corners are on the drawing layer", over == ["fbDraw", "fbDraw"], json.dumps(over))
    drag(page, ell[0], ell[1])
    page.click("#fbBar [data-tool=arrow]")
    over = free_for_drawing(page, [tail, tip])
    check("setup: the arrow's ends are on the drawing layer", over == ["fbDraw", "fbDraw"], json.dumps(over))
    drag(page, tail, tip)
    page.fill("#fbPop textarea", "move this support to the bracket")
    page.screenshot(path=str(shots / "1_drawn.png"))
    page.locator("#fbPop").get_by_role("button", name=T["fb"]["send"]).click()
    page.wait_for_timeout(1500)
    if not check("one POST to /feedback/<project>", len(fb) == 1 and fb[0]["url"].split("?")[0].endswith(f"/feedback/{PROJECT}"), f"{len(fb)} POST(s)"):
        return None
    v = fb[0]["body"].get("view") or {}
    check("console clean", not errs, "; ".join(errs[:3]))
    ctx.close(); b.close()
    return v, centre, gap, tail, tip


def check_view(v, tail_px, tip_px):
    shapes = v.get("shapes")
    good = check("[1] view.shapes = [ellipse, arrow]", isinstance(shapes, list) and [s.get("type") for s in shapes] == ["ellipse", "arrow"],
                 json.dumps([s.get("type") for s in shapes]) if isinstance(shapes, list) else repr(shapes))
    e, ar = shapes if good else ({}, {})
    pts = ar.get("pts") or []
    check("[1] arrow has exactly [tail, tip]", [q.get("role") for q in pts] == ["tail", "tip"], json.dumps([q.get("role") for q in pts]))
    if len(pts) == 2 and all(isinstance(q.get("px"), list) for q in pts):
        t, h = pts
        check("[1] tail is on A", t.get("part") == A, json.dumps(t))
        check("[1] tip is on B", h.get("part") == B, json.dumps(h))
        check("[1] tail px = where the drag started", all(abs(t["px"][i] - tail_px[i]) <= 1 for i in range(2)), f"{t.get('px')} vs {tail_px}")
        check("[1] tip px = where the drag ended", all(abs(h["px"][i] - tip_px[i]) <= 1 for i in range(2)), f"{h.get('px')} vs {tip_px}")
        check("[1] tail.at is on A's top face (assembly mm)", within(t.get("at"), BOXES[A][1], BOXES[A][2]) and abs(t["at"][2] - 20) <= TOL, json.dumps(t.get("at")))
        check("[1] tip.at is on B's top face (assembly mm)", within(h.get("at"), BOXES[B][1], BOXES[B][2]) and abs(h["at"][2] - 30) <= TOL, json.dumps(h.get("at")))
        check("[1] tail.normal is +Z of the assembly", unit_z(t.get("normal")), json.dumps(t.get("normal")))
        check("[1] tip.normal is +Z of the assembly", unit_z(h.get("normal")), json.dumps(h.get("normal")))
        check("[1] hits carry the node index n", isinstance(t.get("n"), int) and isinstance(h.get("n"), int))
    c = [q for q in e.get("pts") or [] if q.get("role") == "centre"]
    check("[1] the ellipse's centre is on B", len(c) == 1 and c[0].get("part") == B and within(c[0].get("at"), BOXES[B][1], BOXES[B][2]), json.dumps(c))
    ins = e.get("inside") or {}
    check("[1] the ellipse has B inside, not A", ins.get(B, 0) > 0 and not ins.get(A), json.dumps(ins))
    check("[1] view.parts = [B, A] (first appearance)", v.get("parts") == [B, A], json.dumps(v.get("parts")))
    check("[2] drawn note has no `selected`", "selected" not in v, ",".join(sorted(v)))
    cam = v.get("camera") or {}
    check("[1] camera w x h = the viewport", cam.get("w") == VIEWPORT["width"] and cam.get("h") == VIEWPORT["height"], f"{cam.get('w')}x{cam.get('h')}")
    check("[1] camera has fov, position, target, up", all(k in cam for k in ("fov", "position", "target", "up")), ",".join(sorted(cam)))
    okm = isinstance(cam.get("view"), list) and len(cam["view"]) == 16 and isinstance(cam.get("proj"), list) and len(cam["proj"]) == 16
    check("[1] camera view/proj are 16 numbers each", okm)
    if all(k in cam for k in ("position", "target")):
        check("[1] top view: the camera is above its target in assembly Z", cam["position"][2] - cam["target"][2] > 50, f"{cam['position']} -> {cam['target']}")
    if okm and len(pts) == 2 and isinstance(pts[0].get("at"), list) and isinstance(pts[0].get("px"), list):
        got = [project(cam, pts[0]["at"], cm) for cm in (True, False)]
        d = [math.dist(g, pts[0]["px"]) if g else 1e9 for g in got]
        check("[1] camera matrices map the tail's assembly-mm point onto its pixel (+-3 px)", min(d) <= 3, f"column-major {d[0]:.1f} px, row-major {d[1]:.1f} px")


def check_render(v, centre, gap):
    if not UNDERSTOOD.is_file():
        check("[4] agent/skills/web-viewer/scripts/understood.py exists", False, str(UNDERSTOOD))
        return
    note = {"msg_id": MSG_ID, "site": "public", "url": v.get("url"), "hash": v.get("hash"), "cycle_t": v.get("cycle_t"),
            "shapes": v.get("shapes"), "camera": v.get("camera")}
    (proj_dir / "build" / "notes").mkdir(parents=True, exist_ok=True)
    (proj_dir / "build" / "notes" / f"{MSG_ID}.json").write_text(json.dumps(note, ensure_ascii=False), encoding="utf-8")
    cam = v.get("camera") or {}
    W, H = cam.get("w"), cam.get("h")

    r = run_understood([MSG_ID, "--project", proj_dir])
    want = proj_dir / "build" / "understood" / f"{MSG_ID}.png"
    check("[4] understood.py <message id> --project DIR exits 0", r.returncode == 0, (r.stderr or "")[-400:])
    check("[4] it prints the PNG path (default DIR/build/understood/<id>.png)", str(want) in (r.stdout or "") or want.name in (r.stdout or ""), (r.stdout or "")[-200:])
    if not check("[4] the PNG is written", want.is_file()):
        return
    shutil.copy(want, shots / "2_understood_default.png")
    w, h, ch, px = png_pixels(want.read_bytes())
    check("[4] PNG is the note's camera w x h", (w, h) == (W, H), f"{w}x{h} vs {W}x{H}")
    if (w, h) != (W, H):
        return
    okA, pa = mostly_magenta(px, ch, *centre[A])
    check("[4] default: A (hit by the tail) is highlighted magenta at its centre", okA, f"centre px {tuple(pa)}")
    okG, pg = mostly_magenta(px, ch, *gap)
    check("[4] default: the gap between the boxes is not magenta", not okG and not magenta(pg), f"gap px {tuple(pg)}")

    out2 = root / "only_b.png"
    r = run_understood([proj_dir / "build" / "notes" / f"{MSG_ID}.json", "--project", proj_dir, "--parts", B, "--points", "2.tip"], out2)
    check("[4] understood.py NOTE.json --parts B --points 2.tip --out PNG exits 0", r.returncode == 0 and out2.is_file(), (r.stderr or "")[-400:])
    if out2.is_file():
        shutil.copy(out2, shots / "3_understood_only_b.png")
        w, h, ch, px = png_pixels(out2.read_bytes())
        okB, pb = mostly_magenta(px, ch, *centre[B])
        okA, pa = mostly_magenta(px, ch, *centre[A])
        check("[4] --parts B: B is magenta", okB, f"B centre px {tuple(pb)}")
        check("[4] --parts B: A is not magenta", not okA and not magenta(pa), f"A centre px {tuple(pa)}")


with sync_playwright() as p:
    res = run(p)
if res:
    view, centre, gap, tail_px, tip_px = res
    (shots / "view.json").write_text(json.dumps(view, ensure_ascii=False, indent=1), encoding="utf-8")
    check_view(view, tail_px, tip_px)
    check_render(view, centre, gap)
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
