#!/usr/bin/env python3
"""Real-browser check of issue #113 «Wires in 3D», stage 1: the page draws every routed wire of the project as a tube.

Written before the implementation, from the spec. The wires come from the cwd project: the harness test rig of machine-project
(scripts/tests/harness_proj.py: a board, a motor and a moving sensor, wires W1..W4 routed through waypoints, W5 not routed),
built here by the real `cad_incremental.py --model`. The picture comes from an importer output (--in, else $VIEWER_TEST_MODEL,
else the demo machine examples/nema-bracket, built once in a temp dir): build.py draws the model it is given and computes the
wires from the project it runs in, so the count of drawn wires does not depend on which model is shown.

  <python with build123d 0.13.0 + python-fcl + playwright> test_wires_view.py [--in <importer output>] [--swiftshader]
(first run: `python -m playwright install chromium`)

Contract under test (done-when, client side):
  [w1] build.py run in the project writes <site>/wires.json and names it in config.json "wires"
  [w2] the page loads it and draws the routed wires: window.__viewer.wires reports what was drawn, at least the count
       (a number, an array of the drawn wires, or an object with `count` / `n`): 4 here (W1..W4; W5 has no port locations)
  [w3] with the wires on, the page has no errors
Out of scope (not asserted): tube radius, colours per net (asserted only loosely: wires on different nets are not all one
colour, when the page reports colours).
Exit code 1 on any failed check.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import link_input, model_dir, serve_stack  # noqa: E402

TESTS = HERE.parents[2] / "machine-project" / "scripts" / "tests"
sys.path[:0] = [str(TESTS), str(TESTS.parent)]
os.environ.setdefault("PROCGROUP_TIMEOUT", "900")
import harness_proj as HP  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--in", dest="src", default=None, help="importer output dir (default: $VIEWER_TEST_MODEL, else the demo machine)")
ap.add_argument("--swiftshader", action="store_true")
a = ap.parse_args()
GPU_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
NOROUTER = ["--flash", "none", "--kit", "none", "--feedback", "none"]
PROJ = "wires-demo"
ROUTED = 4
rows, ok_all = [], True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


def count_of(w):
    if isinstance(w, bool) or w is None:
        return None
    if isinstance(w, (int, float)):
        return int(w)
    if isinstance(w, list):
        return len(w)
    if isinstance(w, dict):
        for k in ("count", "n", "drawn"):
            if isinstance(w.get(k), (int, float)) and not isinstance(w.get(k), bool):
                return int(w[k])
        for k in ("wires", "list", "items"):
            if isinstance(w.get(k), list):
                return len(w[k])
    return None


src = model_dir(a.src)
tmp = Path(tempfile.mkdtemp(prefix="wires_view_"))
try:
    rig = HP.make_project(tmp / "rig")
    run = HP.build_model(rig)
    if run.rc:
        sys.exit("the rig's model build failed:\n" + run.log[-2000:])
    projects = tmp / "projects"
    projects.mkdir()
    site = projects / PROJ
    r = subprocess.run([sys.executable, str(HERE / "build.py"), "--in", str(link_input(src, tmp / "in")), "--out", str(site),
                        "--motion", str(rig / "motion.json"), *NOROUTER], cwd=str(rig), env=HP.env(rig),
                       capture_output=True, text=True, timeout=900)
    if r.returncode:
        sys.exit("build.py failed:\n" + (r.stdout + r.stderr)[-2000:])
    cfg = json.loads((site / "config.json").read_text(encoding="utf-8"))
    check("[w1] config.json names the wires layer", cfg.get("wires") == "wires.json", json.dumps({k: cfg.get(k) for k in ("wires",)}))
    check("[w1] the site carries wires.json", (site / "wires.json").is_file())

    srv, BASE = serve_stack(projects)
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(args=GPU_ARGS)
            page = b.new_page(viewport={"width": 1280, "height": 800})
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(f"{BASE}/{PROJ}/?app=0", wait_until="load")
            page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
            try:
                page.wait_for_function("window.__viewer && window.__viewer.wires !== undefined", timeout=30000)
            except Exception:  # noqa: BLE001 - reported by the check below
                pass
            got = page.evaluate("""() => { const w = window.__viewer && window.__viewer.wires;
                if (w === undefined) return {missing: true};
                try { return {v: JSON.parse(JSON.stringify(w, (k, x) => (x && x.isObject3D) ? '[3d]' : x))}; }
                catch (e) { return {v: typeof w === 'number' ? w : String(w)}; } }""")
            n = None if got.get("missing") else count_of(got.get("v"))
            check("[w2] window.__viewer.wires reports the drawn wires", not got.get("missing") and n is not None,
                  json.dumps(got)[:300])
            check(f"[w2] it drew the {ROUTED} routed wires (W1..W4; W5 has no port locations)", n == ROUTED, f"count {n}")
            v = got.get("v")
            colours = {str(x.get("color")) for x in (v if isinstance(v, list) else (v or {}).get("wires") or [])
                       if isinstance(x, dict) and x.get("color") is not None} if isinstance(v, (list, dict)) else set()
            if colours:
                check("[w2] wires on different nets are not all one colour", len(colours) > 1, str(sorted(colours)))
            check("[w3] no page errors with the wires on", not errs, "; ".join(errs[:3]))
            b.close()
    finally:
        srv.shutdown()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n| check | result | detail |\n|---|---|---|")
for x in rows:
    print("| " + " | ".join(str(c).replace("|", "/") for c in x) + " |")
print(f"\n{sum(x[1] == 'FAIL' for x in rows)} failing, {sum(x[1] == 'PASS' for x in rows)} passing")
sys.exit(0 if ok_all else 1)
