"""Synthetic project for issue #16 «Ports on parts and assembly by joints» (stock build123d joints, no own wrappers).

Every part file builds its part in its OWN coordinates and declares stock build123d joints on its shape; nodes dock parts with
`a["shape"].joints[...].connect_to(b["shape"].joints[...], **kw)` and return `place(part)` WITHOUT a Location. Only the root
(frame/base) is placed with an explicit Location.

    mechanics/cad/parts/__init__.py   build(): the machine root: builds frame + head, docks head's slider onto the rail
    mechanics/cad/assemble.py         calls parts.build()
    parts/frame/base.py   Box 200 x 100 x T (T = 10.0, a constant in the file), corner at the origin, mill.
                          RigidJoint "rail" at (100, 50, T); RigidJoint "hole_1".."hole_6" on the top face (Z up, out of the plate)
    parts/frame/rail.py   Box 160 x 20 x 20, centred in X/Y, bottom at z=0. RigidJoint "bottom" at the origin;
                          LinearJoint "carriage" on Axis((0, 0, 20), (1, 0, 0)), linear_range (-60, 60)
    parts/frame/screw.py  the FRAMEWORK TEMPLATE templates/screw.py copied in unchanged: build(size, length, std), a bought part with
                          a RigidJoint "head" under the head whose Z points from the shaft to the head (so it docks onto a hole joint
                          whose Z points out of the surface: the shaft goes into the hole, `length` = from under the head down)
    parts/frame/__init__  base placed at Pos(0, 0, 0) (the root); rail docked base.rail <- rail.bottom; screw("M5", 12, "DIN 912")
                          docked into hole_1..hole_4, screw("M4", 8, "DIN 912") into hole_5, hole_6
    parts/head/slider.py  Box 30 x 30 x 10, centred in X/Y, bottom at z=0. RigidJoint "mount" at the origin;
                          RevoluteJoint "hinge" on Axis((0, 0, 10), (0, 0, 1)), angular_range (0, 90)
    parts/head/arm.py     Box 50 x 10 x 5, from x=0 along +X, centred in Y, bottom at z=0. RigidJoint "pivot" at the origin
    parts/head/__init__   sub-assembly in its own coordinates: slider (place(slider), never docked inside head),
                          arm docked slider.hinge <- arm.pivot at angle=90
    parts/__init__        rail.carriage <- slider.mount at position=30; returns frame's list + place(head's list)

Where everything must end up (world, mm; T = 10):
    base   [0, 0, 0] .. [200, 100, 10]
    rail   [20, 40, 10] .. [180, 60, 30]            (origin (100, 50, 10))
    slider [115, 35, 30] .. [145, 65, 40]           (origin (130, 50, 30))
    arm    [125, 50, 40] .. [135, 100, 45]          (origin (130, 50, 40), turned 90 deg about Z)
    screws: under-head point at (hole x, hole y, T), shaft down to T - length
Joint graph: rail -> base.rail (rigid), slider -> rail.carriage (linear, 30), arm -> slider.hinge (revolute, 90),
each screw -> base.hole_i (rigid); base is the root (no joint).
Part files tick their stem (base, rail, slider, arm); the template screw does not tick (it is framework code).
"""
import json
import shutil
import textwrap
from pathlib import Path

import pytest

import helpers as H
import partfiles as F

TEMPLATES = H.SCRIPTS.parent / "templates"
SCREW_TEMPLATE = TEMPLATES / "screw.py"

T = 10.0
HOLES_M5 = {"hole_1": (20, 20), "hole_2": (180, 20), "hole_3": (20, 80), "hole_4": (180, 80)}
HOLES_M4 = {"hole_5": (60, 50), "hole_6": (140, 50)}
HOLES = dict(HOLES_M5, **HOLES_M4)

WORLD_BBOX = {
    "base": ((0, 0, 0), (200, 100, 10)),
    "rail": ((20, 40, 10), (180, 60, 30)),
    "slider": ((115, 35, 30), (145, 65, 40)),
    "arm": ((125, 50, 40), (135, 100, 45)),
}
ORIGIN = {"base": (0, 0, 0), "rail": (100, 50, 10), "slider": (130, 50, 30), "arm": (130, 50, 40)}

FILES = {
    "__init__.py": '''
        from geom import place
        from . import frame, head


        def build():
            fr = frame.build()
            hd = head.build()
            rail = next(p for p in fr if p["name"] == "rail")
            slider = next(p for p in hd if p["name"] == "slider")
            rail["shape"].joints["carriage"].connect_to(slider["shape"].joints["mount"], position=30)
            return fr + place(hd)
        ''',
    "frame/__init__.py": '''
        from build123d import Pos
        from geom import place
        from . import base, rail, screw
        {TICK}
        M5 = ["hole_1", "hole_2", "hole_3", "hole_4"]
        M4 = ["hole_5", "hole_6"]


        def build():
            _tick("node_frame")
            b = base.build()
            r = rail.build()
            b["shape"].joints["rail"].connect_to(r["shape"].joints["bottom"])
            out = [place(b, Pos(0, 0, 0)), place(r)]
            for h in M5:
                s = screw.build("M5", 12, "DIN 912")
                b["shape"].joints[h].connect_to(s["shape"].joints["head"])
                out.append(place(s))
            for h in M4:
                s = screw.build("M4", 8, "DIN 912")
                b["shape"].joints[h].connect_to(s["shape"].joints["head"])
                out.append(place(s))
            return out
        ''',
    "frame/base.py": '''
        from build123d import Align, Box, Location, RigidJoint
        from geom import P
        {TICK}
        T = 10.0
        HOLES = {HOLES!r}


        def build():
            _tick("base")
            s = Box(200, 100, T, align=(Align.MIN, Align.MIN, Align.MIN))
            RigidJoint("rail", s, Location((100, 50, T)))
            for label, (x, y) in HOLES.items():
                RigidJoint(label, s, Location((x, y, T)))
            return P("base", s, "#888888", process="mill", material="S235JR")
        ''',
    "frame/rail.py": '''
        from build123d import Align, Axis, Box, LinearJoint, Location, RigidJoint
        from geom import P
        {TICK}


        def build():
            _tick("rail")
            s = Box(160, 20, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
            RigidJoint("bottom", s, Location((0, 0, 0)))
            LinearJoint("carriage", s, axis=Axis((0, 0, 20), (1, 0, 0)), linear_range=(-60, 60))
            return P("rail", s, "#aaaaaa", process="mill", material="AL6060")
        ''',
    "head/__init__.py": '''
        from geom import place
        from . import arm, slider
        {TICK}


        def build():
            _tick("node_head")
            s = slider.build()
            a = arm.build()
            s["shape"].joints["hinge"].connect_to(a["shape"].joints["pivot"], angle=90)
            return [place(s), place(a)]
        ''',
    "head/slider.py": '''
        from build123d import Align, Axis, Box, Location, RevoluteJoint, RigidJoint
        from geom import P
        {TICK}


        def build():
            _tick("slider")
            s = Box(30, 30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
            RigidJoint("mount", s, Location((0, 0, 0)))
            RevoluteJoint("hinge", s, axis=Axis((0, 0, 10), (0, 0, 1)), angular_range=(0, 90))
            return P("slider", s, "#4444aa", process="print", material="PETG")
        ''',
    "head/arm.py": '''
        from build123d import Align, Box, Location, RigidJoint
        from geom import P
        {TICK}


        def build():
            _tick("arm")
            s = Box(50, 10, 5, align=(Align.MIN, Align.CENTER, Align.MIN))
            RigidJoint("pivot", s, Location((0, 0, 0)))
            return P("arm", s, "#ff8800", process="print", material="PLA")
        ''',
}

# an unjointed extra part for the check.py FAIL case: placed with a Location although it is not the root
BRACKET = '''
from build123d import Box
from geom import P


def build():
    return P("bracket", Box(10, 10, 10), "#00aa00", process="mill", material="S235JR")
'''

ASSEMBLE = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import parts

items = parts.build()
print(len(items), "items")
"""

# assemble.py variant that records what node code sees on every placed shape (joint labels, joint types, joint world locations,
# shape location), so a cold and a no-change run can be compared
PROBE_ASSEMBLE = """import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import parts

def _loc(l):
    return [round(float(x), 6) for x in (*tuple(l.position), *tuple(l.orientation))]

items = parts.build()
out = {}
for i, p in enumerate(items):
    sh = p["shape"]
    out["%02d_%s" % (i, p["name"])] = {"loc": _loc(sh.location),
                                       "joints": {k: [type(j).__name__, _loc(j.location)] for k, j in sorted(sh.joints.items())}}
Path(__file__).resolve().parents[2].joinpath("joints_probe.json").write_text(json.dumps(out, sort_keys=True))
"""

PART_FILES = ("base", "rail", "slider", "arm")
DOCKED = ("rail", "slider", "arm")                  # transitively docked to base, besides the screws


def _src(s):
    return textwrap.dedent(s).replace("{TICK}", H.TICK).replace("{HOLES!r}", repr(HOLES)).lstrip()


def screw_src():
    """The framework's screw template as a project gets it (placeholders filled like geom.py). None while it does not exist."""
    if not SCREW_TEMPLATE.is_file():
        return None
    return SCREW_TEMPLATE.read_text().replace("{machine}", "m")


def write_parts(cad, files=None, assemble=ASSEMBLE):
    """Write the joint project's parts/ tree and assemble.py into an existing mechanics/cad dir."""
    cad = Path(cad)
    for rel, src in dict(FILES, **(files or {})).items():
        if src is None:
            continue
        p = cad / "parts" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_src(src))
    scr = screw_src()
    if scr is not None:            # missing template: the build fails on the import and every test says why via the build log
        (cad / "parts" / "frame" / "screw.py").write_text(scr)
    (cad / "assemble.py").write_text(assemble)


def write_project(root, files=None, assemble=ASSEMBLE):
    root = Path(root)
    cad = root / "mechanics" / "cad"
    (cad / "parts").mkdir(parents=True)
    (cad / "params.py").write_text("UNUSED = 1.0\n")
    (cad / "geom.py").write_text(F.geom_src())
    write_parts(cad, files, assemble)
    return root


def scaffolded(root, files=None):
    """A scaffolded project (passes check.py as is) whose model is the joint project instead of the example node."""
    root = F.scaffold(root)
    cad = H.cad_dir(root)
    shutil.rmtree(cad / "parts" / "example")
    write_parts(cad, files)
    return root


# ---------------------------------------------------------------- reading
def placement_map(root):
    """placement name -> (part entry, placement) over the whole model."""
    out = {}
    for p in H.read_parts(root):
        for pl in p.get("placements") or []:
            out[pl["name"]] = (p, pl)
    return out


def screw_entries(root, spelled):
    """parts.json entries of the template screw whose code/desc spell `spelled` (e.g. "M5x12")."""
    return [p for p in H.read_parts(root) if p.get("process") == "bought"
            and (spelled in str(p.get("code", "")) or spelled in str(p.get("desc", "")))]


def translation(loc):
    m = F.matrix(loc)
    return tuple(round(m[r][3], 6) for r in range(3))


def rotation(loc):
    m = F.matrix(loc)
    return tuple(tuple(round(m[r][c], 6) for c in range(3)) for r in range(3))


def axis_dir(j):
    """The joint's axis direction from parts.json, accepting {"direction"|"dir": [..]}, [[origin], [dir]] or 6 numbers."""
    a = j.get("axis")
    assert a is not None, f"joint has no axis: {j}"
    if isinstance(a, dict):
        d = a.get("direction", a.get("dir"))
    elif a and isinstance(a[0], list):
        d = a[1]
    else:
        d = a[3:6] if len(a) == 6 else a
    n = sum(float(x) ** 2 for x in d) ** 0.5
    return tuple(round(float(x) / n, 6) for x in d)


def axis_origin(j):
    a = j.get("axis")
    if isinstance(a, dict):
        o = a.get("origin", a.get("position"))
    elif a and isinstance(a[0], list):
        o = a[0]
    else:
        o = a[0:3] if len(a) == 6 else None
    return None if o is None else tuple(round(float(x), 6) for x in o)


# ---------------------------------------------------------------- shared cold build (once per pytest run)
def _build_baseline(shared):
    root = write_project(shared / "proj")
    m = F.run_model(root)
    (shared / "cold.json").write_text(json.dumps({"rc": m.rc, "out": m.out, "err": m.err, "ticks": H.ticks(root)}))


@pytest.fixture(scope="session")
def joint_baseline(tmp_path_factory):
    """(root, Model, cold ticks): the joint project after one clean --model build, built once per run. Read only."""
    import conftest
    shared = conftest._shared(tmp_path_factory, "joints", _build_baseline)
    d = json.loads((shared / "cold.json").read_text())

    class _CP:
        returncode, stdout, stderr = d["rc"], d["out"], d["err"]
    return shared / "proj", F.Model(_CP, shared / "proj"), d["ticks"]


@pytest.fixture
def jproj(joint_baseline, tmp_path):
    """A private copy of the built joint project (a failed cold build shows up as each test's first failing assertion)."""
    root, _, _ = joint_baseline
    return Path(F._clean_copy(root, tmp_path / "proj"))
