"""Done-when 3, through the user's door: open the URL `de serve` printed in a real browser (Playwright chromium), see the part,
draw a note on the model and send it -> a file in .engineer/inbox/; the page's kit endpoint hands out a STEP file.

Selectors are the viewer's own (feedback.mjs, as agent/skills/web-viewer/templates/assembly/test_drawing_markers.py drives it):
#fbOpen opens the bar, #fbBar [data-tool=pin] picks the pin, a click on the model places it, #fbPop textarea + #fbPop .send send it.
GPU: SwiftShader by default (headless CI); DE_TEST_GPU=metal for the Mac's GPU.
"""
import io
import json
import os
import time

import pytest
from PIL import Image

from conftest import inbox, need_server

pytestmark = [pytest.mark.slow, pytest.mark.e2e]
GPU_ARGS = (["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"] if os.environ.get("DE_TEST_GPU") == "metal"
            else ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])


@pytest.fixture(scope="module")
def browser():
    sp = pytest.importorskip("playwright.sync_api")
    with sp.sync_playwright() as p:
        b = p.chromium.launch(args=GPU_ARGS)
        yield b
        b.close()


@pytest.fixture
def viewer(browser, served):
    """-> open(): the URL `de serve` printed, in a fresh browser context, once the viewer is ready (checks run in the test body)."""
    ctxs = []

    def open_():
        server = need_server(served)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
        ctxs.append(ctx)
        return load(ctx.new_page(), server)
    yield open_
    for c in ctxs:
        c.close()


def load(pg, server):
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.goto(f"http://127.0.0.1:{server['port']}/{server['slug']}/", wait_until="load")
    pg.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=180000)
    assert not pg.evaluate("window.__viewer.error"), f"the viewer failed: {pg.evaluate('String(window.__viewer.error)')}"
    pg.wait_for_function("!window.__viewer.state().flying", timeout=30000)
    pg.wait_for_timeout(800)
    return pg


def model_points(pg):
    """Screen points where the model is (the viewer's picker), coarse grid."""
    return pg.evaluate("""() => { const v = window.__viewer, out = [];
      for (let y = 40; y < innerHeight - 40; y += 16) for (let x = 40; x < innerWidth - 40; x += 16) { const h = v.pick(x, y); if (h && h.part) out.push([x, y, h.part]); }
      return out; }""")


def test_page_renders_the_part(viewer):
    page = viewer()
    pts = model_points(page)
    assert pts, "the viewer picks no part anywhere on the screen: the model is not shown"
    img = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    bg = img.getpixel((5, img.height - 5))
    x, y, _ = pts[len(pts) // 2]
    px = img.getpixel((int(x), int(y)))
    assert sum(abs(a - b) for a, b in zip(px, bg)) > 30, f"the pixel on the part {px} looks like the background {bg}: the canvas is blank"
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]   # the whole part on screen: faces, shading and contour lines
    colours = len(set(img.crop((min(xs) - 16, min(ys) - 16, max(xs) + 16, max(ys) + 16)).getdata()))
    assert colours > 3, f"{colours} colours over the part: nothing is drawn"


def test_draw_a_note_on_the_model_and_send_it(viewer, served):
    page = viewer()
    server = need_server(served)
    page.wait_for_function("window.__viewer.feedback || window.__viewer.feedbackError", timeout=30000)
    assert page.evaluate("!!window.__viewer.feedback"), "the page has no «draw a note» (config.json `feedback` missing or the module failed)"
    if page.locator("#pFold").count() and not page.evaluate("window.__viewer.state().panelCollapsed"):
        page.click("#pFold")
        page.wait_for_timeout(400)
    page.click("#fbOpen", timeout=5000)
    page.click('#fbBar [data-tool="pin"]', timeout=5000)
    free = [p for p in model_points(page)
            if page.evaluate("([x, y]) => { const e = document.elementFromPoint(x, y); return e && (e.id === 'fbDraw' || !!e.closest('#fbDraw')); }", p[:2])]
    assert free, "no point of the model is on the drawing layer"
    x, y, part = free[len(free) // 2]
    before = inbox(server["dir"])
    page.mouse.click(x, y)
    page.wait_for_timeout(400)
    page.wait_for_selector("#fbPop textarea", state="visible", timeout=5000)
    page.fill("#fbPop textarea", "M3 screw here, łącznik")
    page.wait_for_selector("#fbPop .send:not([disabled])", timeout=5000)
    page.click("#fbPop .send")

    deadline, new = time.time() + 30, set()
    while time.time() < deadline:
        new = {p for p in inbox(server["dir"]) - before if p.suffix == ".json"}
        if new:
            break
        time.sleep(0.5)
    assert new, "sending the note from the page wrote nothing into .engineer/inbox/"
    note = json.loads((server["dir"] / ".engineer" / "inbox" / sorted(new)[0]).read_text(encoding="utf-8"))
    assert note.get("comment") == "M3 screw here, łącznik", note
    assert part in json.dumps(note.get("view", {}), ensure_ascii=False), f"the note does not say which part was marked ({part}): {note.get('view')}"
    jpgs = [p for p in inbox(server["dir"]) - before if p.suffix.lower() in (".jpg", ".jpeg")]
    assert jpgs, "the note's picture was not saved"
    assert not page.errors, page.errors[:3]


def test_step_download_from_the_page_kit_endpoint(viewer):
    page = viewer()
    cfg = page.evaluate("window.__viewer.config")
    assert cfg.get("kit"), f"config.json has no `kit`: the page shows no downloads ({cfg})"
    res = page.evaluate("""async () => {
      const cfg = window.__viewer.config, seg = location.pathname.split("/")[1];
      const base = cfg.kit === "auto" ? "/kit/" + seg : String(cfg.kit).replace(/\\/$/, "");
      const st = await fetch(base + "/status").then((r) => r.json());
      for (let i = 0; i < 450; i++) {
        const r = await fetch(base + "/step");
        if (r.status === 200) { const t = await r.text(); return { status: st, code: 200, head: t.slice(0, 40) }; }
        if (r.status !== 202) return { status: st, code: r.status, head: (await r.text()).slice(0, 200) };
        await new Promise((ok) => setTimeout(ok, 2000));
      }
      return { status: st, code: 202, head: "still 202 after 15 min" };
    }""")
    assert res["status"].get("enabled") is True and "step" in res["status"].get("scopes", []), res["status"]
    assert res["code"] == 200, res
    assert res["head"].lstrip().startswith("ISO-10303-21"), res
