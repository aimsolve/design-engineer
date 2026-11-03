"""Drive a built viewer page with REAL input (mouse via Playwright, touch via CDP) and assert the controls work:
desktop: click selects, double click sets the orbit pivot, right-drag / Shift-drag pan, wheel zooms, card hide button,
reset view; phone (375x812): panel + card collapsed by default, tap selects, double tap sets the pivot, one-finger
orbit, two-finger pan, pinch zoom, card never covers more than half the screen.

  env -u VIRTUAL_ENV uv run --quiet --isolated --no-project --with playwright python test_controls.py model.html [--shots DIR]
Exit 1 on the first failed check. --shots saves the phone frames (default, card collapsed, card open, after pivot).
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

FLAGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
FAILS = []


def check(ok: bool, what: str) -> None:
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


def ready(pg) -> None:
    pg.wait_for_function("window.__viewer && window.__viewer.frames > 10", timeout=30000)


def settle(pg) -> None:
    pg.wait_for_timeout(150)
    pg.wait_for_function("!window.__viewer.state().flying", timeout=5000)


def state(pg) -> dict:
    return pg.evaluate("window.__viewer.state()")


def find_part(pg, exclude=None, near=(0.5, 0.5)):
    """A screen point over a visible part, on the canvas (not under a panel), nearest `near` (fractions of the screen)."""
    return pg.evaluate("""([ex, fx, fy]) => {
      const w = innerWidth, h = innerHeight, c = document.querySelector('#stage canvas'); let best = null;
      for (let i = 1; i < 30; i++) for (let j = 1; j < 30; j++) {
        const x = w * i / 30, y = h * j / 30;
        if (document.elementFromPoint(x, y) !== c) continue;
        const p = window.__viewer.pick(x, y); if (!p || p.name === ex) continue;
        const d = Math.hypot(x - w * fx, y - h * fy); if (!best || d < best.d) best = {x, y, d, name: p.name, point: p.point};
      }
      return best; }""", [exclude, near[0], near[1]])


def moved(a, b, eps=0.5) -> bool:
    return max(abs(x - y) for x, y in zip(a, b)) > eps


def desktop(b, page: str) -> None:
    ctx = b.new_context(viewport={"width": 1400, "height": 900})
    pg = ctx.new_page()
    pg.goto(page + "#t=1.0")
    ready(pg)
    s0 = state(pg)
    check(not s0["panelCollapsed"], "desktop: panel open by default")
    hit = find_part(pg)
    check(hit is not None, "desktop: found a part on screen")
    pg.mouse.click(hit["x"], hit["y"])
    settle(pg)
    s = state(pg)
    check(s["selected"] == hit["name"] and s["infoOpen"], f"desktop: click selects {hit['name']} and opens the card")
    pg.wait_for_timeout(400)
    pg.mouse.dblclick(hit["x"], hit["y"])
    settle(pg)
    s = state(pg)
    err = max(abs(x - y) for x, y in zip(s["target"], hit["point"]))
    check(err < 1.0, f"desktop: double click moves the pivot onto the hit point (err {err:.2f})")
    check(not s["hidden"], "desktop: double click does not hide anything")
    t0 = s["target"]
    pg.mouse.move(700, 450)
    pg.mouse.down(button="right")
    pg.mouse.move(800, 500, steps=5)
    pg.mouse.up(button="right")
    t1 = state(pg)["target"]
    check(moved(t0, t1), "desktop: right-drag pans the target")
    pg.keyboard.down("Shift")
    pg.mouse.move(700, 450)
    pg.mouse.down()
    pg.mouse.move(640, 420, steps=5)
    pg.mouse.up()
    pg.keyboard.up("Shift")
    t2 = state(pg)["target"]
    check(moved(t1, t2), "desktop: Shift-drag pans the target")
    y0 = state(pg)["yaw"]
    pg.mouse.move(700, 450)
    pg.mouse.down()
    pg.mouse.move(780, 450, steps=5)
    pg.mouse.up()
    check(abs(state(pg)["yaw"] - y0) > 0.1, "desktop: left-drag orbits")
    d0 = state(pg)["dist"]
    pg.mouse.wheel(0, 300)
    pg.wait_for_timeout(100)
    check(state(pg)["dist"] > d0, "desktop: wheel zooms")
    name = state(pg)["selected"]
    if name:
        pg.click("#infoHide")
        check(name in state(pg)["hidden"], "desktop: card button hides the part")
        pg.click("#bShowAll")
        check(not state(pg)["hidden"], "desktop: show-all brings it back")
    pg.click("#bReset")
    settle(pg)
    check(not moved(state(pg)["target"], s0["target"], 0.01), "desktop: reset view returns to the home view")
    ctx.close()


def touch(cdp, kind: str, pts) -> None:
    cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [{"x": x, "y": y, "id": i} for i, (x, y) in enumerate(pts)]})


def phone(b, page: str, shots: Path | None) -> None:
    # DPR 1: under software GL every CDP touch call waits for a frame; at DPR 2 two taps land > 400 ms apart
    ctx = b.new_context(viewport={"width": 375, "height": 812}, device_scale_factor=1, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.goto(page + "#t=1.0")
    ready(pg)
    cdp = ctx.new_cdp_session(pg)
    s = state(pg)
    check(s["panelCollapsed"], "phone: panel collapsed by default")
    panel = pg.eval_on_selector("#panel", "e => e.getBoundingClientRect().height")
    check(panel < 812 * 0.25, f"phone: collapsed panel is short ({panel:.0f} px)")
    if shots:
        pg.screenshot(path=str(shots / "mobile_default.png"))
    hit = find_part(pg)
    check(hit is not None, "phone: found a part on screen")
    touch(cdp, "touchStart", [(hit["x"], hit["y"])])
    touch(cdp, "touchEnd", [])
    settle(pg)
    s = state(pg)
    check(s["selected"] == hit["name"], f"phone: tap selects {hit['name']}")
    check(s["infoOpen"] and s["infoCollapsed"], "phone: card opens collapsed")
    card = pg.eval_on_selector("#info", "e => e.getBoundingClientRect().height")
    check(card < 70, f"phone: collapsed card is a strip ({card:.0f} px)")
    if shots:
        pg.screenshot(path=str(shots / "mobile_card_collapsed.png"))
    pg.tap("#infoT")
    pg.wait_for_timeout(100)
    card = pg.eval_on_selector("#info", "e => e.getBoundingClientRect().height")
    check(not state(pg)["infoCollapsed"] and card <= 812 * 0.46, f"phone: tapping the strip opens the card, <= 45% height ({card:.0f} px)")
    if shots:
        pg.screenshot(path=str(shots / "mobile_card_open.png"))
    pg.tap("#infoT")                                   # collapse again so the model is free
    pg.wait_for_timeout(400)
    hit = find_part(pg, near=(0.3, 0.62)) or hit       # off-centre, so the re-centring is visible
    t0 = state(pg)["target"]
    # under software GL a CDP touch call can wait a whole frame; if the two taps land further apart than the page's
    # double-tap window (event time), that is the rig, not the page: retry, and say so
    pg.evaluate("""() => { window.__ups = []; document.querySelector('#stage canvas')
      .addEventListener('pointerup', e => window.__ups.push(e.timeStamp)); }""")
    for attempt in range(3):
        pg.evaluate("window.__ups.length = 0")
        for _ in range(2):
            touch(cdp, "touchStart", [(hit["x"], hit["y"])])
            touch(cdp, "touchEnd", [])
        settle(pg)
        s = state(pg)
        ups = pg.evaluate("window.__ups")
        gap = ups[1] - ups[0] if len(ups) == 2 else -1
        if moved(t0, s["target"]) or gap < 400:
            break
        print(f"  (rig slow: taps {gap:.0f} ms apart, retrying)")
        pg.wait_for_timeout(500)
    err = max(abs(x - y) for x, y in zip(s["target"], hit["point"]))
    check(moved(t0, s["target"]) and err < 1.0, f"phone: double tap moves the pivot onto the part (err {err:.2f})")
    check(not s["hidden"], "phone: double tap does not hide anything")
    if shots:
        pg.screenshot(path=str(shots / "mobile_after_pivot.png"))
    y0 = s["yaw"]
    touch(cdp, "touchStart", [(180, 400)])
    for i in range(1, 6):
        touch(cdp, "touchMove", [(180 + 15 * i, 400)])
    touch(cdp, "touchEnd", [])
    check(abs(state(pg)["yaw"] - y0) > 0.1, "phone: one-finger drag orbits")
    s = state(pg)
    touch(cdp, "touchStart", [(140, 400), (240, 400)])
    for i in range(1, 6):
        touch(cdp, "touchMove", [(140 - 12 * i, 400), (240 + 12 * i, 400)])
    touch(cdp, "touchEnd", [])
    s2 = state(pg)
    check(s2["dist"] < s["dist"] * 0.8, f"phone: pinch out zooms in ({s['dist']:.0f} -> {s2['dist']:.0f})")
    touch(cdp, "touchStart", [(140, 400), (240, 400)])
    for i in range(1, 6):
        touch(cdp, "touchMove", [(140 + 10 * i, 400 + 8 * i), (240 + 10 * i, 400 + 8 * i)])
    touch(cdp, "touchEnd", [])
    s3 = state(pg)
    check(moved(s2["target"], s3["target"]), "phone: two-finger drag pans")
    check(abs(s3["dist"] - s2["dist"]) < 0.05 * s2["dist"], "phone: two-finger drag does not zoom")
    check(s3["selected"] == s2["selected"], "phone: gestures do not change the selection")
    ctx.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--shots")
    a = ap.parse_args()
    page = Path(a.page).resolve().as_uri()
    shots = Path(a.shots) if a.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(args=FLAGS)
        desktop(b, page)
        phone(b, page, shots)
        b.close()
    print(f"{len(FAILS)} failed" if FAILS else "all controls checks passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
