#!/usr/bin/env python3
"""Real-browser check of a virtual DEVICE on the model page: the device runs its real firmware in the
assembly viewer, its display is drawn on the display part in 3D and enlarged in the "device" window, its buttons are pressed by
holding the mouse on their 3D part / on their button in the window / their keyboard key, the encoder turns with the wheel over it.

Written from the spec before the implementation. Self-contained: it builds the test's own firmware (virtual-controllers
tests/fixtures/demo.ino, cached; needs emscripten, skips with the reason otherwise; never on the VPS), writes a tiny importer output
(a box per part in model.glb, extras.part = part id; parts.json), a sim dir with site.json {"device": "device.json", "fw": "fw"}
(no "entry"), builds the site with THIS template's build.py --sim, serves it like the stack (viewer_stack_server.py) and drives
Chromium.

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_device.py [--swiftshader] [--shots <dir>]
(first run: `... --with playwright playwright install chromium`)

The device (virtual-controllers tests/fixtures/demo.device.json): board "mcu" (part esp32_devkit, fw "demo"); button "btn_play"
(GPIO27 to GND, key Space, label Play); encoder "enc" (A 32, B 33, push SW 25, keys ArrowLeft / ArrowRight); ST7789 "tft" 240x320
with a glass on its front face. The firmware paints the screen BLUE with a RED 40x40 square at (100..139, 140..179) and prints
"PRESS 27 <ms>" per debounced press of btn_play, "ENC <clicks>" when the encoder moved, "PUSH" per encoder push.
API under test: __viewer.sim.device = {elements(), down(id), up(id), press(id, ms), turn(id, n), screen(id) -> {w, h, on, frames,
pixels}, shot(id) -> PNG data URL}; the window registered with win.mjs as id "device", titled with the i18n text `device`
(the page is in its default language, ru; every text the checks expect comes from i18n.mjs).
Check names carry the "done when" they serve: [2] the page, [6] the scripting API, [7] nothing else broke (no console errors).
Exit code 1 on any failed check.
"""
import argparse, base64, json, re, shutil, struct, subprocess, sys, tempfile, time
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import serve_stack, png_pixels, ui_texts  # noqa: E402

VC = HERE.parents[2] / "virtual-controllers"
FIX = VC / "tests" / "fixtures"
ap = argparse.ArgumentParser()
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="device_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
rows, ok_all = [], True
T = ui_texts("ru")


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- firmware (the virtual-controllers test helper: emsdk, cache)
NODE_BUILD = ("import { emEnv, buildFixture } from %s; const e = emEnv(); "
              "if (e.skip) console.log('SKIP ' + e.skip); else console.log('FW ' + buildFixture('demo'));") % json.dumps((VC / "tests" / "helpers.mjs").as_uri())
r = subprocess.run(["node", "--input-type=module", "-e", NODE_BUILD], capture_output=True, text=True, timeout=1800)
last = (r.stdout.strip().splitlines() or [""])[-1]
if last.startswith("SKIP "):
    print("SKIP:", last[5:]); sys.exit(0)
if r.returncode or not last.startswith("FW "):
    sys.exit("firmware build failed:\n" + (r.stderr or r.stdout)[-3000:])
FW = Path(last[3:])

# ---------------------------------------------------------------- the model: a box per part (metres, Y up), extras.part = part id
BOXES = {   # part id: (size m, centre m)
    "esp32_devkit": ((0.050, 0.028, 0.004), (-0.045, 0.0, 0.0)),
    "tft": ((0.034, 0.044, 0.004), (0.010, 0.0, 0.0)),
    "btn_play": ((0.012, 0.012, 0.008), (0.055, 0.012, 0.0)),
    "enc": ((0.014, 0.014, 0.010), (0.055, -0.014, 0.0)),
}


def box_mesh(sx, sy, sz):
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    faces = [((1, 0, 0), [(hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz), (hx, -hy, hz)]),
             ((-1, 0, 0), [(-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz), (-hx, -hy, -hz)]),
             ((0, 1, 0), [(-hx, hy, -hz), (-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz)]),
             ((0, -1, 0), [(-hx, -hy, hz), (-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz)]),
             ((0, 0, 1), [(-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]),
             ((0, 0, -1), [(hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz)])]
    pos, nrm, idx = [], [], []
    for n, quad in faces:
        b = len(pos)
        pos += quad; nrm += [n] * 4; idx += [b, b + 1, b + 2, b, b + 2, b + 3]
    return pos, nrm, idx


def write_glb(path):
    blob, views, accs, meshes, nodes = bytearray(), [], [], [], []

    def add(data, target, ctype, count, typ, mn=None, mx=None):
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target})
        blob.extend(data)
        acc = {"bufferView": len(views) - 1, "componentType": ctype, "count": count, "type": typ}
        if mn is not None:
            acc["min"], acc["max"] = mn, mx
        accs.append(acc)
        return len(accs) - 1
    for pid, (size, centre) in BOXES.items():
        pos, nrm, idx = box_mesh(*size)
        pa = add(b"".join(struct.pack("<3f", *p) for p in pos), 34962, 5126, len(pos), "VEC3",
                 [min(p[i] for p in pos) for i in range(3)], [max(p[i] for p in pos) for i in range(3)])
        na = add(b"".join(struct.pack("<3f", *n) for n in nrm), 34962, 5126, len(nrm), "VEC3")
        ia = add(b"".join(struct.pack("<H", i) for i in idx), 34963, 5123, len(idx), "SCALAR")
        meshes.append({"primitives": [{"attributes": {"POSITION": pa, "NORMAL": na}, "indices": ia, "material": 0}]})
        nodes.append({"name": pid, "mesh": len(meshes) - 1, "translation": list(centre), "extras": {"part": pid}})
    g = {"asset": {"version": "2.0", "generator": "test_device.py"}, "scene": 0, "scenes": [{"nodes": [0]}],
         "nodes": [{"name": "Player", "children": list(range(1, len(nodes) + 1))}] + nodes, "meshes": meshes, "accessors": accs,
         "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
         "materials": [{"name": "default", "pbrMetallicRoughness": {"baseColorFactor": [0.62, 0.64, 0.67, 1], "metallicFactor": 0.2, "roughnessFactor": 0.6}}]}
    js = json.dumps(g, separators=(",", ":")).encode(); js += b" " * (-len(js) % 4)
    while len(blob) % 4:
        blob.append(0)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob)))
        f.write(struct.pack("<II", len(js), 0x4E4F534A)); f.write(js)
        f.write(struct.pack("<II", len(blob), 0x004E4942)); f.write(blob)


work = Path(tempfile.mkdtemp(prefix="device_site_"))
src = work / "build"; (src / "parts").mkdir(parents=True)
write_glb(src / "model.glb")
names = {"esp32_devkit": "ESP32 DevKit", "tft": "TFT 2\" 240x320", "btn_play": "Play button", "enc": "EC11 encoder"}
(src / "parts.json").write_text(json.dumps({"assembly": {"root": "Player"}, "parts": [
    {"id": pid, "name": names[pid], "drawing": None, "kind": "electrical", "material": "other", "instances": 1, "files": {},
     "bbox": [[-s[0] * 500, -s[1] * 500, -s[2] * 500], [s[0] * 500, s[1] * 500, s[2] * 500]]} for pid, (s, _) in BOXES.items()]},
    ensure_ascii=False), encoding="utf-8")
sim = work / "sim"; sim.mkdir()
shutil.copyfile(FIX / "demo.device.json", sim / "device.json")
shutil.copytree(FW, sim / "fw", ignore=shutil.ignore_patterns("build"))
(sim / "site.json").write_text(json.dumps({"device": "device.json", "fw": "fw"}), encoding="utf-8")
root = work / "sites"
b = subprocess.run([sys.executable, str(HERE / "build.py"), "--in", str(src), "--out", str(root / "dev-proj"), "--sim", str(sim),
                    "--flash", "none", "--kit", "none", "--feedback", "none"], capture_output=True, text=True)
if not check("[2] build.py --sim builds a site from a device site.json (\"device\", no \"entry\")", b.returncode == 0, (b.stderr or b.stdout)[-1500:]):
    sys.exit(1)
srv, BASE = serve_stack(root)

V = "window.__viewer"
DEV = f"{V}.sim.device"


def serial(page):
    return page.evaluate(f"({V}.sim.logs('mcu') || []).map((l) => l.line)")


def lines_since(page, n0, pat):
    return [m for m in (re.search(pat, l) for l in serial(page)[n0:]) if m]


def level(page, pin):
    return page.evaluate(f"{V}.sim.vm.m.sim.board('mcu').level({pin})")


def colour_share(png, region, want):
    """share of pixels in region (x0, y0, x1, y1) of a PNG screenshot that pass want(r, g, b)"""
    w, h, ch, px = png_pixels(png)
    x0, y0, x1, y1 = [int(v) for v in region]
    n = hit = 0
    for y in range(max(0, y0), min(h, y1), 2):
        row = px[y]
        for x in range(max(0, x0), min(w, x1), 2):
            r_, g_, b_ = row[x * ch:x * ch + 3]
            n += 1; hit += bool(want(r_, g_, b_))
    return hit / max(1, n)


def aim(page, pid):
    """Centre the camera on a part (same direction and distance), so no floating window covers it; -> its screen point, or None
    when something other than the 3D canvas is on top of it there."""
    page.evaluate(f"""(() => {{ const v = {V}, s = v.partScreen('{pid}'); if (!s) return; const b = v.box(s.n);
        v.setCam({{ target: [0, 1, 2].map((i) => (b[0][i] + b[1][i]) / 2) }}); }})()""")
    page.wait_for_timeout(400)
    pt = page.evaluate(f"{V}.partScreen('{pid}')")
    if not pt:
        return None
    top = page.evaluate(f"(() => {{ const e = document.elementFromPoint({pt['x']}, {pt['y']}); return e === {V}.canvas ? null : (e && (e.id || e.className || e.tagName)); }})()")
    if top:
        check(f"[2] the 3D part {pid} is not covered by the page UI at the centre of the view", False, f"covered by {top}")
        return None
    return pt


BLUE = lambda r, g, b: b > 140 and b > r + 60 and b > g + 60  # noqa: E731
RED = lambda r, g, b: r > 140 and r > g + 60 and r > b + 60  # noqa: E731

with sync_playwright() as p:
    br = p.chromium.launch(args=GPU)
    ctx = br.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(f"{BASE}/dev-proj/?app=0", wait_until="load")
    page.wait_for_function(f"{V} && ({V}.ready || {V}.error)", timeout=120000)
    try:
        page.wait_for_function(f"{V}.sim || {V}.simError", timeout=60000)
        page.wait_for_function(f"({V}.sim && {V}.sim.device) || {V}.simError", timeout=30000)
    except Exception:  # noqa: BLE001 - reported by the next check
        pass
    has = page.evaluate(f"!!({V}.sim && {V}.sim.device)")
    check("[2] the device layer mounts (no sim error)", has and not page.evaluate(f"{V}.error || {V}.simError"),
          str(page.evaluate(f"{V}.simError || {V}.error || null"))[:400])
    if not has:
        page.screenshot(path=str(shots / "no_device.png"))
        for r_ in rows:
            print(r_)
        sys.exit(1)

    api = page.evaluate(f"(() => {{ const d = {DEV}; return Object.fromEntries(['elements','down','up','press','turn','screen','shot'].map((k) => [k, typeof d[k]])); }})()")
    check("[6] __viewer.sim.device has elements/down/up/press/turn/screen/shot", all(v == "function" for v in api.values()), json.dumps(api))
    els = page.evaluate(f"{DEV}.elements().map((e) => e.id || e)")
    check("[6] elements() lists the device elements", {"btn_play", "enc", "tft"} <= set(els), json.dumps(els))

    page.evaluate(f"{V}.setView('front')")
    page.wait_for_function(f"({V}.sim.logs('mcu') || []).some((l) => /DRAWN/.test(l.line))", timeout=30000)
    page.wait_for_timeout(800)

    # ---- the screen: scripting API
    scr = page.evaluate(f"""(() => {{ const s = {DEV}.screen('tft'); const at = (x, y) => Array.from(s.pixels.slice((y * s.w + x) * 4, (y * s.w + x) * 4 + 4));
        return {{ w: s.w, h: s.h, on: s.on, frames: s.frames, len: s.pixels.length, bg: at(5, 300), sq: at(120, 160) }}; }})()""")
    check("[6] screen('tft') = {w 240, h 320, on, frames > 0, pixels w*h*4}",
          scr["w"] == 240 and scr["h"] == 320 and scr["on"] is True and scr["frames"] > 0 and scr["len"] == 240 * 320 * 4, json.dumps({k: scr[k] for k in ("w", "h", "on", "frames", "len")}))
    check("[2] the firmware's picture: blue background, red square", BLUE(*scr["bg"][:3]) and RED(*scr["sq"][:3]), f"bg {scr['bg']} square {scr['sq']}")
    url = page.evaluate(f"{DEV}.shot('tft')")
    ok_url = isinstance(url, str) and url.startswith("data:image/png;base64,")
    detail = ""
    if ok_url:
        w, h, ch, px = png_pixels(base64.b64decode(url.split(",", 1)[1]))
        bg, sq = px[300][5 * ch:5 * ch + 3], px[160][120 * ch:120 * ch + 3]
        ok_url = (w, h) == (240, 320) and BLUE(*bg) and RED(*sq)
        detail = f"{w}x{h} bg {list(bg)} square {list(sq)}"
    check("[6] shot('tft') is a PNG data URL of the screen", ok_url, detail or str(url)[:80])

    # ---- the device window
    win = page.evaluate("""(title) => { const cands = [...document.querySelectorAll('body *')].filter((e) => e.querySelector('canvas') && !e.contains(window.__viewer.canvas)
          && (e.textContent || '').includes(title) && e.getBoundingClientRect().width > 0);
        cands.sort((a, b) => a.getBoundingClientRect().width * a.getBoundingClientRect().height - b.getBoundingClientRect().width * b.getBoundingClientRect().height);
        const w = cands[0]; if (!w) return null; w.setAttribute('data-test-device-win', '1');
        const c = w.querySelector('canvas'); c.setAttribute('data-test-device-canvas', '1'); const r = c.getBoundingClientRect();
        return { w: r.width, h: r.height, buttons: [...w.querySelectorAll('button')].map((b) => b.textContent.trim()) }; }""", T["device"])
    check(f"[2] a window «{T['device']}» with the enlarged screen is on the page", bool(win) and win["w"] >= 120 and win["h"] >= 160, json.dumps(win, ensure_ascii=False))
    if win:
        png = page.locator("[data-test-device-canvas]").screenshot(path=str(shots / "device_window.png"))
        check("[2] the window's screen shows the firmware's picture (mostly blue, some red)",
              colour_share(png, (0, 0, win["w"], win["h"]), BLUE) > 0.5 and colour_share(png, (0, 0, win["w"], win["h"]), RED) > 0.01, "")

    # ---- a display without w / h (an SSD1306 the firmware leaves dark): its canvas has the chip's own size, 128x64 (review fix)
    osz = page.evaluate("(() => { const c = document.querySelector('canvas[data-el=\"oled\"]'); return c && [c.width, c.height]; })()")
    check("[2] a display without w/h gets the chip's own canvas size (SSD1306 128x64)", osz == [128, 64], str(osz))

    # ---- the picture on the display part in 3D
    tp = aim(page, "tft")
    full = page.screenshot(path=str(shots / "model_front.png"))
    if tp:
        share = colour_share(full, (tp["x"] - 40, tp["y"] - 50, tp["x"] + 40, tp["y"] + 50), BLUE)
        check("[2] the display part in 3D shows the screen (blue texture on its glass)", share > 0.3, f"blue share {share:.2f} around {tp}")
    else:
        check("[2] the display part is visible in 3D", False, "partScreen('tft') = null")

    # ---- mouse held on the button's 3D part
    bp = aim(page, "btn_play") or {"x": -1, "y": -1}
    n0 = len(serial(page))
    page.mouse.move(bp["x"], bp["y"]); page.mouse.down()
    page.wait_for_timeout(400)
    held = level(page, 27)
    page.wait_for_timeout(300)
    held2 = level(page, 27)
    page.mouse.up()
    page.wait_for_timeout(400)
    rel = level(page, 27)
    pr = lines_since(page, n0, r"PRESS 27 (\d+)")
    check("[2] mouse held on the button's 3D part presses it (pin LOW) until the mouse is released", (held, held2, rel) == (0, 0, 1), f"levels {held}/{held2}/{rel}")
    check("[2] the firmware reports exactly one press of ~0.7 s", len(pr) == 1 and 500 <= int(pr[0][1]) <= 1000, str([m[0] for m in pr]))
    shot2 = page.evaluate(f"""(() => {{ const s = {DEV}.screen('tft'); const i = (25 * s.w + 25) * 4; return Array.from(s.pixels.slice(i, i + 3)); }})()""")
    check("[2] the press changed the picture (the firmware repaints a square)", not BLUE(*shot2), str(shot2))

    # ---- its button in the window
    if win:
        btn = page.locator("[data-test-device-win] button", has_text="Play")
        if btn.count():
            n0 = len(serial(page))
            bb = btn.first.bounding_box()
            page.mouse.move(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2); page.mouse.down()
            page.wait_for_timeout(300)
            held = level(page, 27)
            page.mouse.up(); page.wait_for_timeout(400)
            check("[2] holding «Play» in the window presses btn_play", held == 0 and level(page, 27) == 1 and len(lines_since(page, n0, r"PRESS 27")) == 1,
                  f"held level {held}, presses {len(lines_since(page, n0, r'PRESS 27'))}")
        else:
            check("[2] the window has a button «Play» (the element's label)", False, json.dumps(win["buttons"], ensure_ascii=False))

    # ---- the keyboard key: hold Space 1 s (auto-repeat keydowns included) = ONE long press
    page.mouse.move(700, 120)
    n0 = len(serial(page))
    page.keyboard.down("Space")
    for _ in range(9):
        page.wait_for_timeout(110)
        page.keyboard.down("Space")      # a held key auto-repeats: keydown with repeat = true
    held = level(page, 27)
    page.keyboard.up("Space")
    page.wait_for_timeout(500)
    pr = lines_since(page, n0, r"PRESS 27 (\d+)")
    check("[2] holding Space for ~1 s is one long press (pressed while held)", held == 0 and len(pr) == 1 and 800 <= int(pr[0][1]) <= 1400,
          f"held level {held}, presses {[m[0] for m in pr]}")

    # ---- the encoder: wheel over its 3D part turns it, the camera does not zoom
    ep = aim(page, "enc") or {"x": -1, "y": -1}
    dist0 = page.evaluate(f"{V}.state().dist")
    n0 = len(serial(page))
    page.mouse.move(ep["x"], ep["y"])
    for _ in range(3):
        page.mouse.wheel(0, 100); page.wait_for_timeout(150)
    page.wait_for_timeout(500)
    enc = lines_since(page, n0, r"ENC (-?\d+)")
    dist1 = page.evaluate(f"{V}.state().dist")
    check("[2] the mouse wheel over the encoder turns it", bool(enc) and int(enc[-1][1]) != 0, str([m[0] for m in enc]))
    check("[2] ... and does not zoom the camera", abs(dist1 - dist0) < 1e-6, f"dist {dist0} -> {dist1}")
    before = int(enc[-1][1]) if enc else 0

    # its keys: ArrowRight twice = +2 clicks
    page.mouse.move(700, 120)
    n0 = len(serial(page))
    page.keyboard.press("ArrowRight"); page.wait_for_timeout(150); page.keyboard.press("ArrowRight")
    page.wait_for_timeout(500)
    enc = lines_since(page, n0, r"ENC (-?\d+)")
    check("[2] the encoder's keys turn it (ArrowRight x2 = +2)", bool(enc) and int(enc[-1][1]) == before + 2, f"{before} -> {[m[0] for m in enc]}")

    # its push: the push_key (Enter), like a button
    n0 = len(serial(page))
    page.keyboard.down("Enter"); page.wait_for_timeout(200); page.keyboard.up("Enter"); page.wait_for_timeout(400)
    check("[2] the encoder's push works like a button (push_key)", len(lines_since(page, n0, r"\bPUSH\b")) == 1, "")

    # ---- the scripting API drives the same elements
    n0 = len(serial(page))
    page.evaluate(f"{DEV}.press('btn_play', 200)"); page.wait_for_timeout(700)
    pr = lines_since(page, n0, r"PRESS 27 (\d+)")
    check("[6] device.press(id, ms)", len(pr) == 1 and 150 <= int(pr[0][1]) <= 260, str([m[0] for m in pr]))
    n0 = len(serial(page))
    page.evaluate(f"{DEV}.down('btn_play')"); page.wait_for_timeout(300)
    d_lvl = level(page, 27)
    page.evaluate(f"{DEV}.up('btn_play')"); page.wait_for_timeout(400)
    check("[6] device.down(id) / up(id)", d_lvl == 0 and level(page, 27) == 1 and len(lines_since(page, n0, r"PRESS 27")) == 1, f"level while down {d_lvl}")
    n0 = len(serial(page))
    page.evaluate(f"{DEV}.turn('enc', -3)"); page.wait_for_timeout(600)
    enc_all = [int(m[1]) for m in (re.search(r"ENC (-?\d+)", l) for l in serial(page)) if m]
    check("[6] device.turn(id, n)", len(enc_all) >= 2 and enc_all[-1] == before + 2 - 3, str(enc_all[-4:]))

    page.screenshot(path=str(shots / "model_end.png"))
    errs = [e for e in errs if "favicon" not in e]
    check("[7] no console errors", not errs, " | ".join(errs[:5])[:400])
    br.close()
srv.shutdown()

print()
for n, s, d in rows:
    print(f"{s}  {n}")
print("\nALL PASS" if ok_all else "\nFAILURES")
sys.exit(0 if ok_all else 1)
