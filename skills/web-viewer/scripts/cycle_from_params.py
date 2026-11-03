#!/usr/bin/env python3
"""Write the `cycle` block of motion.json (and the part -> motion-group map) from a code-CAD machine's params.py.

  python3 cycle_from_params.py --params <cad>/params.py --out motion.json [--bom build/cad/bom.json --groups-out motion_groups.json]

params.py contract:
  GROUPS       {group: None | (axis, travel)}   axis 'x'|'y'|'z' (or a 3-vector), travel in mm (linear)
  GROUP_PARENT {child: parent}                  a child group rides on its parent's motion
  MOTION       {group: [(t0, t1, frac0, frac1), ...]}   over one CYCLE_T; frac = fraction of the travel, holds between segments
  CYCLE_T      seconds per cycle
  optional GROUP_ROTARY {group: pivot_mm [x,y,z]}: that group turns instead of sliding; its GROUPS travel is then DEGREES about the axis through the pivot.
params.py is imported in a separate python process with cwd = a temp dir (a params.py that needs only the standard library runs anywhere; one that
imports build123d needs that interpreter) and only the JSON of the five names above comes back. Existing keys of --out are kept, only `cycle` is replaced.
--bom: bom.json [{name, node, group}] -> --groups-out {name: group} for `step_import.py --motion-groups`.
"""
import argparse, json, subprocess, sys, tempfile
from pathlib import Path

PROBE = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("params", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
g = lambda n, d: getattr(m, n, d)
print(json.dumps({"GROUPS": g("GROUPS", {}), "GROUP_PARENT": g("GROUP_PARENT", {}), "MOTION": g("MOTION", {}), "CYCLE_T": g("CYCLE_T", None), "GROUP_ROTARY": g("GROUP_ROTARY", {})}))
"""


def read_params(path: Path) -> dict:
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run([sys.executable, "-c", PROBE, str(path.resolve())], cwd=td, capture_output=True, text=True, timeout=60)
    if r.returncode:
        sys.exit(f"cannot import {path}:\n{r.stderr[-1500:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def build_cycle(p: dict) -> dict:
    if not p["CYCLE_T"]:
        sys.exit("params.py has no CYCLE_T")
    groups, errs = {}, []
    for name, ax in p["GROUPS"].items():
        if ax is None:
            continue                                   # static
        axis, travel = ax
        g = {"kind": "linear", "axis": axis if isinstance(axis, str) else list(axis), "travel_mm": travel}
        if name in p["GROUP_ROTARY"]:
            g = {"kind": "rotary", "axis": g["axis"], "travel_deg": travel, "pivot_mm": list(p["GROUP_ROTARY"][name])}
        if name in p["GROUP_PARENT"]:
            g["parent"] = p["GROUP_PARENT"][name]
        segs = [[float(a), float(b), float(f0), float(f1)] for a, b, f0, f1 in p["MOTION"].get(name, [])]
        for s in segs:
            if not (0 <= s[0] < s[1] <= p["CYCLE_T"] + 1e-9):
                errs.append(f"{name}: segment {s} outside 0..{p['CYCLE_T']}")
        g["segments"] = segs
        groups[name] = g
    for n, g in groups.items():
        if g.get("parent") and g["parent"] not in groups and p["GROUPS"].get(g["parent"]) is not None:
            errs.append(f"{n}: unknown parent {g['parent']}")
    for n in p["MOTION"]:
        if n not in groups:
            errs.append(f"MOTION has '{n}' that is not a moving group of GROUPS")
    if errs:
        sys.exit("\n".join(errs))
    return {"period_s": float(p["CYCLE_T"]), "loop": True, "groups": groups}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--params", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--bom"); ap.add_argument("--groups-out")
    a = ap.parse_args()
    cyc = build_cycle(read_params(Path(a.params)))
    out = Path(a.out); doc = json.loads(out.read_text()) if out.is_file() else {}
    doc["cycle"] = cyc
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    print(f"{out}: period {cyc['period_s']} s, " + ", ".join(f"{n} {g['kind'] == 'linear' and g['axis'] + ' ' + str(g['travel_mm']) + ' mm' or 'rot'}{' <' + g['parent'] if g.get('parent') else ''} ({len(g['segments'])} seg)" for n, g in cyc["groups"].items()))
    if a.bom:
        bom = json.loads(Path(a.bom).read_text())
        m = {b["name"]: b["group"] for b in bom if b.get("group")}
        bad = sorted({g for g in m.values() if g not in cyc["groups"] and g != "static"})
        if bad:
            sys.exit(f"bom.json groups not in params GROUPS: {bad}")
        Path(a.groups_out or "motion_groups.json").write_text(json.dumps(m, indent=0))
        print(f"{a.groups_out or 'motion_groups.json'}: {len(m)} parts, {sum(1 for g in m.values() if g in cyc['groups'])} in moving groups")


if __name__ == "__main__":
    main()
