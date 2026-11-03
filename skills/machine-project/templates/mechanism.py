#!/usr/bin/env python3
"""Template: a machine's closed loops and its cycle, stated once.

Copy to `mechanics/kinematics/mechanism.py` of a machine that has a loop one docking cannot hold - a
cylinder driving a crank through a rod, a four-bar, a slider on a swinging lever - and rewrite the loop
below as the machine's own. The model, the checks and the viewer all read this file, so the machine's
kinematics exists once: NEVER write the loop out again as `atan2`/`asin` anywhere else.

The example is a cylinder swinging a lever: the lever turns on a shaft, the cylinder sits on a trunnion
and its rod eye takes the pin of the lever's short leg. The rod is what drives it.

`cycle_from_mechanism.py` reads PERIOD_S, RUNS, FRAMES, poses() and groups(); everything else here is the
machine's own. Write the cycle with:

    python3 <framework>/skills/machine-project/scripts/cycle_from_mechanism.py \
        --mechanism mechanics/kinematics/mechanism.py --out build/motion.json
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mechanics" / "cad"))
import params as P  # noqa: E402
from linkage import Mechanism  # noqa: E402

PERIOD_S = 3.0                                   # seconds for one cycle
RUNS = [(0.4, 1.2, True), (1.7, 2.5, False)]     # when the drive runs over the period, and which way
FRAMES = 24                                      # segments per run


def mechanism():
    """The loop: the joints it is made of, and which points stay together.

    `turn(name, on, at, start)` - `name` turns on `on` about the point `at` of it, `start` degrees.
    `slide(name, on, offset, start)` - `name` slides along `on`'s X, `offset` across it, from `start` mm.
    `meet((body, point), (body, point))` - the two points stay together; `distance=` instead makes it a
    link with no body of its own. A third item in a side, `(body, point, turned)`, puts the point on an
    arm `turned` degrees off that body's X axis.
    `start` values are a guess: `assemble()` closes the loop from them, so they only have to pick the
    right assembly - the one the machine is really built in, not its mirror image.
    """
    m = Mechanism()
    m.turn("lever", on="ground", at=(0.0, P.PIVOT_Z_MM), start=P.PICK_TIP_DEG)
    m.turn("cyl_body", on="ground", at=(P.CYL_PIVOT_X_MM, P.CYL_PIVOT_Z_MM), start=0.0)
    m.slide("cyl_rod", on="cyl_body", start=P.CYL_RETRACTED_MM)
    m.meet(("cyl_rod", (0.0, 0.0)), ("lever", (P.CYL_ARM_R_MM, 0.0), -P.L_LEG_ANGLE_DEG))
    return m


def poses(frames=FRAMES * 10 + 1):
    """The whole stroke: one solved frame each, every frame starting at the one before it.

    Name the driven joint and the two ends of its travel. Reading a joint the other way round - the
    stroke a swing needs rather than the swing a stroke gives - is the same call with another joint, so
    nothing here is ever bisected by hand.

    THE FIRST FRAME HAS TO BE THE POSE THE CAD IS DRAWN IN. The viewer moves every part from where the
    model put it, so frame nought is its nought; a sweep that starts anywhere else leaves the whole
    machine out of place at t = 0 and nothing says so.
    """
    m = mechanism()
    retracted = m.assemble("lever")["cyl_rod"]
    return m.sweep("cyl_rod", retracted, retracted + P.CYL_STROKE_MM, frames - 1)


def groups(solved):
    """Which viewer group follows which joint value, and about what.

    `read` is the group's own value in a frame; the travel and every per-frame fraction are worked out
    from it. The signs are the viewer's convention, not the loop's: in the CAD frame a positive turn
    about +Y lowers the angle measured from +X toward +Z. `parent` = the group rides on that one.
    """
    return {
        "lever": {"kind": "rotary", "axis": "y", "pivot_mm": [0.0, 0.0, P.PIVOT_Z_MM],
                  "read": lambda frame: -frame["lever"]},
        "cylbody": {"kind": "rotary", "axis": "y", "pivot_mm": [P.CYL_PIVOT_X_MM, 0.0, P.CYL_PIVOT_Z_MM],
                    "read": lambda frame: -frame["cyl_body"]},
        "cylrod": {"kind": "linear", "parent": "cylbody",
                   "axis": [math.cos(math.radians(solved[0]["cyl_body"])), 0.0,
                            math.sin(math.radians(solved[0]["cyl_body"]))],
                   "read": lambda frame: frame["cyl_rod"]},
    }


#: Which group of parts is which link of the mechanism, read by the robot export alone (#20,
#: `scripts/export_robot.py`): a group is a part's `motion_group` field, or an entry of the
#: `build/motion_groups.json` a machine's own code writes. A group named in no entry is welded to the
#: base, where a part nothing moves belongs. A group whose own freedom the loop does not state - a
#: drive of its own, on its own timing - names the link it rides on, and its stroke is not written.
LINKS = {"lever": "lever", "cylbody": "cyl_body", "cylrod": "cyl_rod"}

#: What drives the loop, declared beside the joint it drives. A cylinder states its bore, its rod and
#: the regulated pressure (the pulling side is the one that counts); a motor states `torque_nm`, and
#: anything states `force_n` outright. One drive per machine is written for now.
DRIVES = [{"joint": "cyl_rod", "kind": "cylinder", "bore_mm": 50.0, "rod_mm": 20.0,
           "pressure_bar": 6.0, "note": "the catalogue cylinder, with its wiki decision"}]


# `linkage.py` is the framework's, and `cycle_from_mechanism.py` puts it on the path for you. To run
# this file by itself - which is the quickest way to see what the loop does - say where it is:
#   PYTHONPATH=<framework>/skills/machine-project/scripts python3 mechanics/kinematics/mechanism.py
if __name__ == "__main__":
    solved = poses()
    for name in sorted(solved[0]):
        print("%-10s %10.3f -> %10.3f" % (name, solved[0][name], solved[-1][name]))
