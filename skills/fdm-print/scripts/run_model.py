#!/usr/bin/env python3
"""Run a CAD build script, find the meshes it wrote, check + preview each, and print ONE JSON result.

  python run_model.py print/<piece>/build.py [--preview] [--strict] [--timeout 1800]

JSON: {success, returncode, seconds, stdout_tail, stderr_tail, outputs: [{path, watertight, bodies, bbox_mm,
volume_cm3, solid_weight_g, sheet}]}. "outputs" = .stl/.3mf/.step files under the script's folder created or
changed by this run. success = the script exited 0, wrote at least one mesh, and (--strict) every mesh is watertight
with one body. On failure read stderr_tail, fix the script, run again — don't paper over it (no try/except around a
failing fillet, no silently shrunk radius). Runs with the current python (build123d, trimesh in the worker image).
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MESH = {".stl", ".3mf", ".obj"}


def snapshot(root):
    out = {}
    for p in root.rglob("*"):
        if p.suffix.lower() in MESH | {".step", ".stp"}:
            try:
                out[p] = p.stat().st_mtime
            except OSError:  # a temp file deleted between the walk and the stat
                pass
    return out


def check(p, a):
    """Facts (and the six-view sheet with --preview) for one mesh, in a subprocess so a crash can't lose the JSON."""
    if a.preview:
        cmd = [sys.executable, str(HERE / "preview_sheet.py"), str(p), str(p.with_name(p.stem + "_sheet.png")),
               "--density", str(a.density)]
    else:
        cmd = [sys.executable, "-c", "import sys,json,trimesh;sys.path.insert(0,sys.argv[1]);import preview_sheet as s;"
               "print(json.dumps(s.facts(trimesh.load(sys.argv[2],force='mesh'),float(sys.argv[3]))))",
               str(HERE), str(p), str(a.density)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=900)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--preview", action="store_true", help="six-view sheet next to each mesh (<name>_sheet.png)")
    ap.add_argument("--strict", action="store_true", help="fail on non-watertight or multi-body meshes")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--density", type=float, default=1.27)
    a = ap.parse_args()

    script = Path(a.script).resolve()
    root = script.parent
    before = snapshot(root)
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, str(script)], cwd=root,
                           capture_output=True, text=True, timeout=a.timeout)
        rc, out, err = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = 124, "", f"timeout after {a.timeout} s"
    res = {"success": False, "returncode": rc, "seconds": round(time.time() - t0, 1),
           "stdout_tail": out[-3000:], "stderr_tail": err[-4000:], "outputs": []}
    changed = [p for p, t in snapshot(root).items() if before.get(p) != t]
    for p in sorted(changed):
        item = {"path": str(p)}
        if p.suffix.lower() in MESH:
            try:
                c = check(p, a)
            except subprocess.TimeoutExpired:
                item["check_error"] = "mesh check timed out after 900 s"
                res["outputs"].append(item)
                continue
            try:
                item.update(json.loads(c.stdout.strip().splitlines()[-1]))
            except (ValueError, IndexError):
                item["check_error"] = c.stderr[-500:]
        res["outputs"].append(item)
    meshes = [o for o in res["outputs"] if Path(o["path"]).suffix.lower() in MESH]
    res["success"] = rc == 0 and bool(meshes) and all(
        "check_error" not in o and (not a.strict or (o.get("watertight") and o.get("bodies") == 1)) for o in meshes)
    if rc == 0 and not meshes:
        res["stderr_tail"] += "\n[run_model] the script exited 0 but wrote no .stl/.3mf/.obj under its folder"
    print(json.dumps(res, ensure_ascii=False, indent=1))
    sys.exit(0 if res["success"] else 1)


if __name__ == "__main__":
    main()
