"""Screenshot a built viewer page frozen at cycle time t, from a given camera, with parts hidden. For reviewers.

One frame:
  shoot.py model.html out.png [--t 1.0] [--cam YAW,PITCH,DIST,TX,TY,TZ] [--hide node/a,node/b] [--mobile]
Many frames (one browser; every frame is a fresh page load, so no state leaks between frames):
  shoot.py model.html --jobs jobs.json      # [{"out": "a.png", "t": 1.0, "cam": "...", "hide": "...", "mobile": true}]

Camera: orbit around (TX,TY,TZ); yaw 0 looks from +Z, pitch 0 = horizontal, dist in model units. --mobile = 375x812 phone
(touch, DPR 2). Prints the fraction of the frame covered by parts: near 0 means a blank frame (camera inside a solid).
Run: env -u VIRTUAL_ENV uv run --quiet --isolated --no-project --with playwright python shoot.py ...
First time on a machine: ... --with playwright python -m playwright install chromium
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

DESKTOP = {"viewport": {"width": 1400, "height": 900}}
MOBILE = {"viewport": {"width": 375, "height": 812}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}
BLANK = 0.03


def url_for(page: Path, job: dict) -> str:
    parts = []
    if job.get("t") is not None:
        parts.append(f"t={job['t']}")
    if job.get("cam"):
        parts.append(f"cam={job['cam']}")
    if job.get("hide"):
        parts.append("hide=" + quote(job["hide"], safe=","))
    return page.resolve().as_uri() + ("#" + "&".join(parts) if parts else "")


def shoot(browser, page: Path, job: dict) -> float:
    ctx = browser.new_context(**(MOBILE if job.get("mobile") else DESKTOP))
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(url_for(page, job))
    pg.wait_for_function("window.__viewer && window.__viewer.frames > 10 && !window.__viewer.state().flying", timeout=30000)
    cover = pg.evaluate("window.__viewer.coverage()")
    pg.screenshot(path=job["out"])
    ctx.close()
    warn = "  WARNING: looks blank" if cover < BLANK else ""
    print(f"saved {job['out']}  coverage {cover:.2f}{warn}" + (f"  PAGE ERRORS: {errors}" if errors else ""))
    return cover


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--t", type=float)
    ap.add_argument("--cam")
    ap.add_argument("--hide")
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--jobs")
    a = ap.parse_args()
    if a.jobs:
        jobs = json.loads(Path(a.jobs).read_text())
    elif a.out:
        jobs = [{"out": a.out, "t": a.t, "cam": a.cam, "hide": a.hide, "mobile": a.mobile}]
    else:
        sys.exit("need OUT.png or --jobs")
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        covers = [shoot(b, Path(a.page), j) for j in jobs]
        b.close()
    if any(c < BLANK for c in covers):
        sys.exit(1)


if __name__ == "__main__":
    main()
