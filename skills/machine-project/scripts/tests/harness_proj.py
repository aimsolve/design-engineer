"""The tiny code-model project of the harness tests (issue #113 «Wires in 3D», stage 1), built by the real
`cad_incremental.py --model assemble.py`. Shared by test_harness_e2e.py and web-viewer's test_wires_view.py.

Machine frame, mm, Z up. Every part is a build123d Box with Align.MIN in its own coordinates, placed by Pos(min corner),
so own coordinates = world minus the placement corner. One node `rig`:

    floor     mill    300 x 200 x 5     x[0,300]   y[0,200]   z[-5,0]
    wall      mill      5 x 200 x 100   x[150,155] y[0,200]   z[0,100]   a SLOT 5 x 12 x 12 through it at y[94,106] z[44,56]
    standoff  mill     30 x 30 x 49     x[40,70]   y[85,115]  z[0,49]   (10 mm short of the board's edge: clear of its ports)
    board     bought electrical  40 x 30 x 2   x[40,80] y[85,115] z[49,51]
                ports_at (own -> world):  D3  (40,15,1) +X -> (80,100,50)    D4  (40,19,1) +X -> (80,104,50)
                                          VCC (40, 5,1) +X -> (80, 90,50)    D2  (5,15,2) point only (default +Z) -> (45,100,51)
                no ports_at for GND
    motor     bought motors  42 x 42 x 42  x[220,262] y[79,121] z[29,71]
                ports_at:  A+ (0,21,21) -X -> (220,100,50)   A- (0,25,21) -X -> (220,104,50)   VM (0,11,21) -X -> (220,90,50)
    carriage  other  30 x 30 x 10   x[0,30]  y[20,50] z[0,10]   motion group `slide`: +X 100 mm and back over 2 s
    sensor    bought electrical  10 x 10 x 10  x[10,20] y[30,40] z[10,20]   group `slide` too
                ports_at:  OUT (5,5,10) +Z -> (15,35,20)      no ports_at for GND

Waypoints (connections.py):
    in    (130,100,50) machine frame        slot  (2.5,100,50) on `wall` -> (152.5,100,50), in the middle of the slot
    out   (175,100,50) machine frame        drop  (5,5,80) on `sensor` -> (15,35,90) at rest, rides with the carriage
    hop   (45,100,90) machine frame, straight above board.D2
    k1    (100,100,50), k2 (100,103,50)    only the BEND variant: two 90 deg turns 3 mm apart

The GOOD wiring (every rule passes):
    W1 board.D3  -> motor.A+  0.5 mm²  via in, slot, out    (straight line y=100 z=50 through the slot)
    W2 board.D4  -> motor.A-  0.5 mm²  via in, slot, out
    W3 board.VCC -> motor.VM  1.0 mm²  via in, slot, out    -> W1..W3 share in->slot->out: one bundle through the 12 mm slot
    W4 sensor.OUT -> board.D2 0.5 mm²  via drop, hop, loop 40: up 70, across 71.6 at z=90, down 39 into D2.
       Over the cycle drop moves +X 100: drop->hop grows from 71.6 to 95.5 mm (+23.9) - inside a 40 mm loop
    W5 sensor.GND -> board.GND 0.5 mm²  (no ports_at on either end: not routed, still W5)

The BAD variants, one rule each (everything else as GOOD):
    THROUGH  W3 with no waypoints: the straight line y=90 z=50 runs through the solid wall (the slot is y 94..106)
    GAP      W1..W3 with od 8 mm: one fits the 12 mm slot, a bundle of three (>= 8*sqrt(3) = 13.9 mm) does not
    BEND     W1 via k1, k2, in, slot, out: two 90 deg turns 3 mm apart, no room for any radius >= 6 x its diameter
    STRETCH  W4 with loop 10: over the cycle it needs 23.9 mm more than at rest
    BADREF   W1 via an unknown waypoint `nowhere`
    PLAIN    (not a rule) the same WIRES with from/to/net/spec only and no WAYPOINTS: a project from before #113
"""
import json
import os
import shutil
import sys
from pathlib import Path

import helpers as H

NODE_INIT = '''from build123d import Pos
from geom import place
from . import floor, wall, standoff, board, motor, carriage, sensor


def build():
    return [
        place(floor.build(), Pos(0, 0, -5)),
        place(wall.build(), Pos(150, 0, 0)),
        place(standoff.build(), Pos(40, 85, 0)),
        place(board.build(), Pos(40, 85, 49)),
        place(motor.build(), Pos(220, 79, 29)),
        place(carriage.build(), Pos(0, 20, 0)),
        place(sensor.build(), Pos(10, 30, 10)),
    ]
'''

HEAD = "from build123d import Align, Box, Pos\nfrom geom import P\nMIN = (Align.MIN, Align.MIN, Align.MIN)\n\n\n"

PART_FILES = {
    "floor": HEAD + '''def build():
    return P("floor", Box(300, 200, 5, align=MIN), "#888888", process="mill", material="S235JR")
''',
    "wall": HEAD + '''def build():
    s = Box(5, 200, 100, align=MIN) - Pos(-1, 94, 44) * Box(7, 12, 12, align=MIN)
    return P("wall", s, "#999999", process="mill", material="S235JR")
''',
    "standoff": HEAD + '''def build():
    return P("standoff", Box(30, 30, 49, align=MIN), "#777777", process="mill", material="S235JR")
''',
    "board": HEAD + '''def build():
    return P("board", Box(40, 30, 2, align=MIN), "#1b7f3a", process="bought", kind="electrical", code="BRD-1",
             desc="Controller board",
             ports={"VCC": "+12 V", "GND": "0 V", "D2": "IN", "D3": "STEP", "D4": "DIR"},
             ports_at={"D3": ((40, 15, 1), (1, 0, 0)), "D4": ((40, 19, 1), (1, 0, 0)),
                       "VCC": ((40, 5, 1), (1, 0, 0)), "D2": (5, 15, 2)})
''',
    "motor": HEAD + '''def build():
    return P("motor", Box(42, 42, 42, align=MIN), "#3a3f44", process="bought", kind="motors", code="17HS4401",
             desc="Stepper motor NEMA 17",
             ports={"A+": "", "A-": "", "VM": "+12 V"},
             ports_at={"A+": ((0, 21, 21), (-1, 0, 0)), "A-": ((0, 25, 21), (-1, 0, 0)), "VM": ((0, 11, 21), (-1, 0, 0))})
''',
    "carriage": HEAD + '''def build():
    return P("carriage", Box(30, 30, 10, align=MIN), "#cc8800", process="other", motion_group="slide")
''',
    "sensor": HEAD + '''def build():
    return P("sensor", Box(10, 10, 10, align=MIN), "#2255cc", process="bought", kind="electrical", code="SNS-1",
             desc="Proximity sensor", motion_group="slide",
             ports={"OUT": "OUT", "GND": "0 V"},
             ports_at={"OUT": ((5, 5, 10), (0, 0, 1))})
''',
}

ASSEMBLE = '''import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parts import rig

items = rig.build()
print(f"{len(items)} placed parts")
'''

MOVING = ("carriage", "sensor")
CYCLE = {"cycle": {"period_s": 2.0, "loop": True,
                   "groups": {"slide": {"kind": "linear", "axis": "x", "travel_mm": 100.0,
                                        "segments": [[0, 1, 0, 1], [1, 2, 1, 0]], "parts": list(MOVING)}},
                   "node_groups": {n: "slide" for n in MOVING}}}

WAYPOINTS = {
    "in": (130, 100, 50),
    "slot": {"at": (2.5, 100, 50), "on": "wall"},
    "out": (175, 100, 50),
    "drop": {"at": (5, 5, 80), "on": "sensor"},
    "hop": (45, 100, 90),
    "k1": (100, 100, 50),
    "k2": (100, 103, 50),
}
THRU = ["in", "slot", "out"]


def good_wires():
    return [
        {"from": "board.D3", "to": "motor.A+", "net": "STEP", "spec": "0.5 mm²", "via": list(THRU)},
        {"from": "board.D4", "to": "motor.A-", "net": "DIR", "spec": "0.5 mm²", "via": list(THRU)},
        {"from": "board.VCC", "to": "motor.VM", "net": "+12 V", "spec": "1.0 mm²", "via": list(THRU)},
        {"from": "sensor.OUT", "to": "board.D2", "net": "IN", "spec": "0.5 mm²", "via": ["drop", "hop"], "loop": 40},
        {"from": "sensor.GND", "to": "board.GND", "net": "0 V", "spec": "0.5 mm²"},
    ]


def variant(name):
    """WIRES of one variant (see the module doc)."""
    w = good_wires()
    if name == "good":
        pass
    elif name == "through":
        w[2]["via"] = []
    elif name == "gap":
        for i in (0, 1, 2):
            w[i]["od"] = 8.0
    elif name == "bend":
        w[0]["via"] = ["k1", "k2"] + list(THRU)
    elif name == "stretch":
        w[3]["loop"] = 10
    elif name == "badref":
        w[0]["via"] = ["in", "nowhere", "slot", "out"]
    elif name == "plain":                      # the wiring of a project from before #113: no routes at all
        w = [{k: v for k, v in x.items() if k in ("from", "to", "net", "spec")} for x in w]
    else:
        raise ValueError(name)
    return w


def connections_src(wires, waypoints=WAYPOINTS, harness=None):
    lines = ["# connections of the harness test rig (issue #113)", f"WIRES = {wires!r}"]
    if any("via" in w for w in wires):
        lines.append(f"WAYPOINTS = {waypoints!r}")
    if harness is not None:
        lines.append(f"HARNESS = {harness!r}")
    return "\n".join(lines) + "\n"


def strip_ports_at(root):
    """parts.json without any ports_at (as the model of a project from before #113)."""
    f = Path(root) / "build/model/parts.json"
    model = json.loads(f.read_text())
    for p in model["parts"]:
        p.pop("ports_at", None)
    f.write_text(json.dumps(model))


def write_connections(root, name="good", harness=None):
    """connections.py of a variant, dated just before the built model (as if the model had been rebuilt after the edit, which
    the router does after every turn), so no staleness rule fires on it."""
    f = Path(root) / "mechanics" / "cad" / "connections.py"
    f.write_text(connections_src(variant(name), harness=harness), encoding="utf-8")
    model = Path(root) / "build/model/parts.json"
    if model.exists():
        t = model.stat().st_mtime - 1
        os.utime(f, (t, t))
    return f


def make_project(root):
    """The project's sources (no build yet): mechanics/cad (params, geom, node rig, assemble.py, connections.py GOOD), motion.json,
    plus the files check.py's layout rules look for."""
    root = Path(root)
    cad = H.write_base(root, params="X = 1.0\n")
    d = cad / "parts" / "rig"
    d.mkdir(parents=True)
    for pid, src in PART_FILES.items():
        (d / f"{pid}.py").write_text(src)
    (d / "__init__.py").write_text(NODE_INIT)
    (cad / "assemble.py").write_text(ASSEMBLE)
    (cad / "connections.py").write_text(connections_src(variant("good")), encoding="utf-8")
    (root / "motion.json").write_text(json.dumps(CYCLE, indent=1))
    (root / "README.md").write_text("# harness rig\n")
    (root / ".gitignore").write_text("build/\n.engineer/\n.home/\njobs/\ninbox/\noutbox/\npublic/\n.pio/\n")
    (root / "aas").mkdir()
    (root / "mechanics" / "README.md").write_text("mechanics\n")
    return root


def build_model(root):
    """cad_incremental.py --model assemble.py; returns helpers.Run."""
    return H.run_model(root)


def copy_project(src, dst):
    shutil.copytree(src, dst)          # copy2 keeps mtimes: the incremental build and the staleness checks key on them
    for p in Path(dst).rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    return Path(dst)


def env(root):
    e = H.env_for(root)
    e.pop("VIRTUAL_ENV", None)
    return e


def bump(path, after):
    """Give `path` an mtime strictly after file `after` (staleness checks compare mtimes)."""
    t = Path(after).stat().st_mtime + 2
    os.utime(path, (t, t))
