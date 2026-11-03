#!/usr/bin/env python3
"""Write the `cycle` block of motion.json from a machine's solved mechanism: nobody writes the motion.

  python3 cycle_from_mechanism.py --mechanism mechanics/kinematics/mechanism.py --out build/motion.json

The machine's `mechanism.py` is where the closed loops are stated (see `linkage.py`) and is the only
place its kinematics lives. This script asks it for the solved cycle and turns it into what the viewer
reads, so the animation cannot disagree with the model: both come out of the same solve.

The contract, five names in `mechanism.py`:

  PERIOD_S          seconds per cycle
  RUNS              [(t0, t1, forward), ...] - when the drive runs over one period, and which way;
                    a group holds between runs, which is what the viewer does between segments.
                    Each run lies inside the period and no two of them overlap
  FRAMES            how many segments each run is cut into. It is not how finely the loop is solved:
                    `poses()` picks that for itself, and the segments are read off those poses
  poses()           the solved cycle: a list of frames, each a dict of joint values (linkage.sweep).
                    ITS FIRST FRAME IS THE POSE THE CAD IS DRAWN IN, because the viewer moves every
                    part from its home matrix: fraction nought is frame nought, and a machine whose
                    solve starts anywhere else draws itself out of place at t = 0 with nothing to say so
  groups(poses)     {viewer group: {"kind": "rotary"|"linear", "axis": "x"|"y"|"z"|[x, y, z],
                    "pivot_mm": [x, y, z] (rotary only), "parent"?, "read": f(frame) -> that group's
                    own value} - the only machine-specific part, because which way a viewer group
                    turns is the viewer's convention, not the loop's

Everything else - travels, per-frame fractions, the segments - is arithmetic on the solve, done here.
Existing keys of --out are kept, only `cycle` is replaced, as `cycle_from_params.py` does.

Unlike `cycle_from_params.py`, which reads a machine's `params.py` in a separate interpreter and brings
back only JSON, this script imports `mechanism.py` into its own process: `groups()` hands back functions,
which no subprocess can return. The framework's own directory therefore goes on `sys.path` ahead of the
machine's, so a file a machine happens to keep beside its `mechanism.py` cannot stand in for `linkage.py`.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: a group whose largest excursion is below this, in degrees or millimetres, does not move
STILL = 1e-6
#: the two kinds of motion the viewer's cycle knows
KINDS = ("rotary", "linear")
#: the axes the viewer names; anything else has to be given as three numbers
AXES = ("x", "y", "z")


def _three_numbers(value) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value))


def load(path: Path):
    was = list(sys.path)
    sys.path.insert(0, str(path.resolve().parent))
    sys.path.insert(0, str(HERE))                 # ahead of the machine's own: linkage.py is the framework's
    try:
        spec = importlib.util.spec_from_file_location("machine_mechanism", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = was
    for name in ("PERIOD_S", "RUNS", "FRAMES", "poses", "groups"):
        if not hasattr(module, name):
            sys.exit("%s states no '%s'; see the contract in %s" % (path, name, Path(__file__).name))
    if not isinstance(module.FRAMES, int) or module.FRAMES < 1:
        sys.exit("%s states FRAMES = %r; a run is cut into at least one segment" % (path, module.FRAMES))
    return module


def check_runs(machine) -> list:
    """Every run inside the period, going forwards, and no two of them over the same time."""
    wrong = []
    if not isinstance(machine.PERIOD_S, (int, float)) or isinstance(machine.PERIOD_S, bool) or machine.PERIOD_S <= 0:
        return ["PERIOD_S is %r; a cycle lasts some number of seconds" % (machine.PERIOD_S,)]
    runs = list(machine.RUNS)
    if not runs:
        return ["RUNS names no run; the drive never moves and nothing in the cycle would ever go anywhere"]
    for run in runs:
        if len(run) != 3:
            wrong.append("RUNS takes (t0, t1, forward), not %r" % (run,))
            continue
        start, end, forward = run
        if not isinstance(forward, bool):
            wrong.append("run %s says %r for which way it goes; that is True or False" % ((run,), forward))
        if not 0 <= start < end <= machine.PERIOD_S + 1e-9:
            wrong.append("run %s is not inside 0..%s, or does not go forwards" % ((run,), machine.PERIOD_S))
    ordered = sorted((r for r in runs if len(r) == 3), key=lambda r: r[0])
    for was, now in zip(ordered, ordered[1:]):
        if now[0] < was[1] - 1e-9:
            wrong.append("runs %s and %s are over the same time; a group would have two fractions at once"
                         % ((was,), (now,)))
    return wrong


def check_groups(declared) -> list:
    """What a group has to state before anything can be worked out from it."""
    wrong = []
    if not declared:
        wrong.append("groups() named no group; a cycle with nothing moving in it is not a cycle")
    for name, spec in declared.items():
        if not isinstance(spec, dict):
            wrong.append("group '%s' is %r, not a section" % (name, spec))
            continue
        for key in ("kind", "read", "axis"):
            if key not in spec:
                wrong.append("group '%s' states no '%s'" % (name, key))
        if "kind" in spec and spec["kind"] not in KINDS:
            wrong.append("group '%s' is of kind '%s'; it is one of %s" % (name, spec["kind"], ", ".join(KINDS)))
        # The viewer turns or slides about the axis and takes no default: a group without one, or
        # with an axis it cannot name, throws as it mounts and the whole page then stands still.
        axis = spec.get("axis")
        if axis is not None and not (isinstance(axis, str) and axis.lower() in AXES) and not _three_numbers(axis):
            wrong.append("group '%s' turns about '%s'; that is %s, or three numbers"
                         % (name, axis, " or ".join(AXES)))
        if spec.get("kind") == "rotary" and not _three_numbers(spec.get("pivot_mm")):
            wrong.append("group '%s' turns, and states no 'pivot_mm' of three numbers (it states %r); "
                         "without one the viewer would turn it about the model's origin"
                         % (name, spec.get("pivot_mm")))
    return wrong


def build_cycle(machine) -> dict:
    timing = check_runs(machine)          # before the solve: a typo in RUNS should not cost a sweep
    if timing:
        sys.exit("\n".join(timing))
    poses = list(machine.poses())
    if len(poses) < 2:
        sys.exit("poses() gave %d frames; a cycle needs at least two" % len(poses))
    declared = machine.groups(poses)
    wrong = check_groups(declared)
    if wrong:
        sys.exit("\n".join(wrong))

    smooth = lambda x: x * x * (3 - 2 * x)
    last = len(poses) - 1

    out = {}
    for name, spec in declared.items():
        read = spec["read"]
        rest = read(poses[0])
        # The travel is the group's furthest reach from where the model is drawn, not its last
        # frame minus its first: a group that comes back towards its start over the cycle - a
        # rocker on a crank driven a whole turn - would otherwise have a travel of nearly nought
        # and fractions of nearly anything. Measured this way every fraction stays within
        # -1..1, frame zero is still 0, and travel x fraction is the same motion either way.
        travel = max((read(pose) - rest for pose in poses), key=abs)
        if abs(travel) < STILL:
            sys.exit("group '%s' stays within %g of where it starts over the whole solve: it is not "
                     "moving, so leave it out or drive it" % (name, abs(travel)))

        def at(fraction, read=read, rest=rest, travel=travel):
            """How far through its own travel the group is, `fraction` of the way through the stroke."""
            place = min(max(fraction, 0.0), 1.0) * last
            lower = min(int(place), last - 1)
            between = place - lower
            value = read(poses[lower]) * (1 - between) + read(poses[lower + 1]) * between
            return (value - rest) / travel

        group = {"kind": spec["kind"], **{k: v for k, v in spec.items() if k not in ("read", "kind")}}
        group["travel_deg" if spec["kind"] == "rotary" else "travel_mm"] = round(travel, 4)
        segments = []
        for start, end, forward in machine.RUNS:
            for step in range(machine.FRAMES):
                was, now = step / machine.FRAMES, (step + 1) / machine.FRAMES
                first, second = smooth(was), smooth(now)
                if not forward:
                    first, second = 1 - first, 1 - second
                segments.append([round(start + (end - start) * was, 4), round(start + (end - start) * now, 4),
                                 # + 0.0 so that a rounded -1e-18 reads as 0.0 rather than -0.0
                                 round(at(first), 5) + 0.0, round(at(second), 5) + 0.0])
        group["segments"] = segments
        out[name] = group

    riding = ["group '%s' rides on '%s', which is not a group of this cycle" % (name, group["parent"])
              for name, group in out.items() if group.get("parent") and group["parent"] not in out]
    if riding:
        sys.exit("\n".join(riding))
    return {"period_s": machine.PERIOD_S, "loop": True, "groups": out}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mechanism", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    cycle = build_cycle(load(args.mechanism))
    whole = json.loads(args.out.read_text(encoding="utf-8")) if args.out.is_file() else {}
    whole["cycle"] = cycle
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(whole, indent=1, ensure_ascii=False), encoding="utf-8")
    print("%d groups, %d segments each -> %s"
          % (len(cycle["groups"]), len(next(iter(cycle["groups"].values()))["segments"]), args.out))


if __name__ == "__main__":
    main()
