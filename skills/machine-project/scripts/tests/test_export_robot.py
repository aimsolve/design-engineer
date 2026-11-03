"""Model export to URDF/MJCF: the built model written out as a robot description.

    python3 export_robot.py --model build/model/parts.json --out build/robot --name <machine> \
        [--mechanism mechanics/kinematics/mechanism.py] [--groups build/motion_groups.json] \
        [--format urdf|mjcf|both] [--collide]

Done-when map:
  DW1  parts rigidly together are ONE link, never a joint with no freedom. Links come from the docking graph
       (`placements[].joint`), or - a model with no dockings - from --groups + the mechanism's LINKS;
       everything not in a group, and every placement with no joint, goes into the base link.
  DW2  one joint per declared freedom, with its axis, its anchor and its range: metres/radians for URDF,
       metres/degrees for MJCF (`<compiler angle="degree">`). revolute -> URDF revolute (continuous where both
       bounds are open) and MJCF hinge; linear -> URDF prismatic and MJCF slide. MJCF writes the start value as
       the joint's `ref`, so MuJoCo's zero is the pose the CAD is drawn in; URDF cannot, and must say so.
  DW3  mass and inertia come from the part's STEP and the density of its material: card.density (g/cm3), then a
       catalogue by material/process, then 7.85 - and that last case is reported naming the part. A link of
       several parts adds up by the parallel-axis theorem. A part with no solid adds no mass and is reported.
  DW4  every `meet((body, point), (body, point))` becomes an MJCF `<equality><connect>` between the two links at
       that point; in URDF the chain is broken and the script says which loop and where. A `meet(..., distance=)`
       has no body of its own and is refused, saying to declare it as a body.
  DW5  a DRIVES entry becomes an MJCF `<actuator>` on that joint with a ctrlrange covering the joint's travel.
       More than one drive is refused rather than half-written.
  DW6  refusals, never invention: a joint URDF has no type for (cylindrical, ball) is refused for URDF naming the
       joint; a link that could not be weighed is written with no inertia rather than an invented one.
  DW7  the MJCF compiles in MuJoCo.

Two kinds of model stand behind these tests:
  * the real docked machine of `jointfiles.py` (base plate + rail + 6 template screws welded into one link, a
    slider sliding on the rail at 30 mm, an arm hinged on the slider at 90 deg), built by cad_incremental;
  * hand-written `build/model/` (parts.json + STEPs written here with build123d, as helpers.make_derived_model
    does for derive.py), so a density, an open-ended range or a joint type URDF cannot hold is exact and cheap.
The four-bar of `fourbar()` is the second source of links: no dockings at all, a mechanism.py stating the loop
and a motion_groups.json tying its placements to the mechanism's joints. Its CAD placements are the mechanism's
first frame exactly (crank 90, coupler -90, rocker 90 all solve the loop with no residual), so «MuJoCo's zero is
the pose the CAD is drawn in» is a numeric assertion and not a matter of taste.
"""
import hashlib
import importlib.util
import json
import math
import struct
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

import helpers as H
import jointfiles as J
import partfiles as F
import procgroup
from jointfiles import joint_baseline  # noqa: F401  (session fixture: the docked machine, built once per run)

SCRIPTS = H.SCRIPTS
EXPORT = SCRIPTS / "export_robot.py"

MM = 1e-3                      # the model is millimetres, both descriptions are metres
STEEL = 7.85                   # g/cm3: the density of last resort


# ================================================================ running the script
class Export:
    def __init__(self, cp, out, name):
        self.rc = cp.returncode
        self.out = cp.stdout or ""
        self.err = cp.stderr or ""
        self.log = self.out + self.err
        self.dir = Path(out)
        self.urdf = self.dir / f"{name}.urdf"
        self.mjcf = self.dir / f"{name}.xml"
        self.meshes = self.dir / "meshes"

    def ok(self):
        assert self.rc == 0, f"export_robot failed (rc={self.rc}):\n{self.log[-2500:]}"
        return self

    def refused(self):
        """A refusal: non-zero exit AND a message. `run_export` has already proved the script exists, so a
        non-zero exit here is the script's own judgement and not a missing file."""
        assert self.rc != 0, f"this must be refused, it exited 0:\n{self.log[-2000:]}"
        assert self.log.strip(), "a refusal with nothing said is not a refusal"
        return self

    def wrote_nothing(self):
        assert not self.urdf.exists() and not self.mjcf.exists(), \
            f"a refusal must write no description, found {[p.name for p in self.dir.glob('*') if p.is_file()]}"
        return self

    def names(self, *words):
        """Every word is in what the script said (so a message that names no part/joint fails)."""
        missing = [w for w in words if w not in self.log]
        assert not missing, f"the message does not name {missing}:\n{self.log[-2000:]}"
        return self


def run_export(root, out, name="stend", fmt=None, mechanism=None, groups=None, collide=False, model=None):
    """`export_robot.py` over a project root, run from it (the `step` paths of parts.json are relative to it)."""
    assert EXPORT.is_file(), f"{EXPORT} does not exist yet: nothing to export the model with"
    root, out = Path(root), Path(out)
    args = [sys.executable, str(EXPORT), "--model", str(model or root / "build/model/parts.json"),
            "--out", str(out), "--name", name]
    if mechanism:
        args += ["--mechanism", str(mechanism)]
    if groups:
        args += ["--groups", str(groups)]
    if fmt:
        args += ["--format", fmt]
    if collide:
        args += ["--collide"]
    cp = procgroup.run(args, cwd=str(root), env=H.env_for(root), capture_output=True, text=True, timeout=600)
    return Export(cp, out, name)


# ================================================================ writing a model by hand
def _mat(loc):
    """A build123d Location as parts.json writes it: a row-major 4x4 as 16 floats (cad_incremental._loc_matrix)."""
    t = loc.wrapped.Transformation()
    return [float(t.Value(r, c)) for r in (1, 2, 3) for c in (1, 2, 3, 4)] + [0.0, 0.0, 0.0, 1.0]


def rigid(loc=None):
    from build123d import Location
    return {"type": "rigid", "loc": _mat(loc or Location())}


def axis_joint(kind, origin, direction, rng, **extra):
    """A linear / revolute / cylindrical joint entry as cad_incremental._joints_json writes it."""
    from build123d import Axis
    a = Axis(tuple(float(x) for x in origin), tuple(float(x) for x in direction))
    e = {"type": kind, "loc": _mat(a.location),
         "axis": {"origin": [float(x) for x in origin], "direction": [float(x) for x in direction]},
         "range": [float(x) for x in rng]}
    e.update(extra)
    return e


def ball_joint(loc=None):
    from build123d import Location
    return {"type": "ball", "loc": _mat(loc or Location()),
            "range": [[-30.0, 30.0], [-30.0, 30.0], [-180.0, 180.0]]}


def write_model(root, specs):
    """build/model/parts.json + build/model/step/<key>.step (+ mesh/<key>.json) from part specs.

    A spec is {id, shape | mesh, process, material?, card?, joints?, placements: [(name, Location, joint|None)]}.
    Shapes are in the part's OWN coordinates, exactly as a part file builds them; the placements carry them into
    machine coordinates. Independent of cad_incremental, so these tests fail only for export reasons.
    """
    from build123d import export_step
    root = Path(root)
    (root / "build/model/step").mkdir(parents=True, exist_ok=True)
    parts = []
    for s in specs:
        pid = s["id"]
        key = hashlib.sha256(("export_robot|%s" % pid).encode()).hexdigest()
        e = {"id": pid, "name": s.get("name", pid), "node": s.get("node", "n"), "func": "n.build",
             "process": s.get("process", "other"), "color": s.get("color", "#999999"), "key": key}
        for k in ("material", "card", "thickness"):
            if k in s:
                e[k] = s[k]
        if s.get("shape") is not None:
            export_step(s["shape"], str(root / "build/model/step" / f"{key}.step"))
            e["step"] = f"build/model/step/{key}.step"
        if s.get("mesh") is not None:
            (root / "build/model/mesh").mkdir(parents=True, exist_ok=True)
            (root / "build/model/mesh" / f"{key}.json").write_text(json.dumps(s["mesh"]))
            e["mesh"] = f"build/model/mesh/{key}.json"
        if s.get("joints"):
            e["joints"] = s["joints"]
        e["placements"] = [dict({"name": n, "node": e["node"], "loc": _mat(loc)}, **({"joint": j} if j else {}))
                           for n, loc, j in s["placements"]]
        e["qty"] = len(e["placements"])
        parts.append(e)
    (root / "build/model/parts.json").write_text(json.dumps({"parts": parts}, indent=1))
    (root / "build/model/nodes.json").write_text(json.dumps({}))
    return root


def box_mesh(x0, y0, z0, x1, y1, z1):
    """An axis-aligned box as the flat {v, f} a stored mesh part holds (mm, machine coordinates)."""
    v = [[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    f = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
         [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]
    return {"v": [float(c) for p in v for c in p], "f": [int(i) for t in f for i in t]}


def keys_of(root):
    return {p["id"]: p["key"] for p in H.read_parts(root)}


# ================================================================ the four-bar: no dockings, a stated loop
#: the loop, in the planar CAD frame linkage.py works in (X across, Z up, turns about Y, mm and degrees).
#: crank 300 long on the origin, coupler 400 on the crank's far end, rocker 200 on (400, 100). At crank 90,
#: coupler -90, rocker 90 the loop is closed with no residual, and that is the pose the parts are placed in.
FOURBAR_MEET_MM = (400.0, 0.0, 300.0)              # where the coupler's far end and the rocker's end stay together
FOURBAR_START = {"crank": 90.0, "coupler": -90.0, "rocker": 90.0}
#: part id -> its world bounding box in mm at the pose the model is built in
FOURBAR_BOX_MM = {
    "ground": ((-60.0, -30.0, -50.0), (460.0, 30.0, -30.0)),
    "crank": ((-10.0, -10.0, 0.0), (10.0, 10.0, 300.0)),
    "coupler": ((0.0, -8.0, 292.0), (400.0, 8.0, 308.0)),
    "rocker": ((392.0, -8.0, 100.0), (408.0, 8.0, 300.0)),
}

MECHANISM = '''
import sys
sys.path.insert(0, %r)
from linkage import Mechanism

PERIOD_S = 2.0
RUNS = [(0.0, 1.0, True), (1.0, 2.0, False)]
FRAMES = 4

LINKS = {"crank_g": "crank", "coupler_g": "coupler", "rocker_g": "rocker"}

DRIVES = [{"joint": "crank", "kind": "motor"}]


def mechanism():
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=90.0)
    m.turn("coupler", on="crank", at=(300.0, 0.0), start=-90.0)
    m.turn("rocker", on="ground", at=(400.0, 100.0), start=90.0)
    m.meet(("coupler", (400.0, 0.0)), ("rocker", (200.0, 0.0)))
    return m


def poses(frames=9):
    m = mechanism()
    m.assemble("crank")
    return m.sweep("crank", 90.0, 120.0, frames - 1)


def groups(solved):
    return {
        "crank_g": {"kind": "rotary", "axis": "y", "pivot_mm": [0.0, 0.0, 0.0],
                    "read": lambda f: -f["crank"]},
        "coupler_g": {"kind": "rotary", "axis": "y", "pivot_mm": [0.0, 0.0, 300.0], "parent": "crank_g",
                      "read": lambda f: -f["coupler"]},
        "rocker_g": {"kind": "rotary", "axis": "y", "pivot_mm": [400.0, 0.0, 100.0],
                     "read": lambda f: -f["rocker"]},
    }
''' % str(SCRIPTS)

#: the same machine stated with a link that has no body of its own: refused (DW4)
MECHANISM_DISTANCE = '''
import sys
sys.path.insert(0, %r)
from linkage import Mechanism

PERIOD_S = 2.0
RUNS = [(0.0, 1.0, True), (1.0, 2.0, False)]
FRAMES = 4

LINKS = {"crank_g": "crank", "rocker_g": "rocker"}

DRIVES = [{"joint": "crank", "kind": "motor"}]


def mechanism():
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=90.0)
    m.turn("rocker", on="ground", at=(400.0, 100.0), start=90.0)
    m.meet(("crank", (300.0, 0.0)), ("rocker", (200.0, 0.0)), distance=400.0)
    return m


def poses(frames=9):
    m = mechanism()
    m.assemble("crank")
    return m.sweep("crank", 90.0, 120.0, frames - 1)


def groups(solved):
    return {
        "crank_g": {"kind": "rotary", "axis": "y", "pivot_mm": [0.0, 0.0, 0.0],
                    "read": lambda f: -f["crank"]},
        "rocker_g": {"kind": "rotary", "axis": "y", "pivot_mm": [400.0, 0.0, 100.0],
                     "read": lambda f: -f["rocker"]},
    }
''' % str(SCRIPTS)

TWO_DRIVES = ('DRIVES = [{"joint": "crank", "kind": "motor"}]',
              'DRIVES = [{"joint": "crank", "kind": "motor"}, {"joint": "rocker", "kind": "cylinder"}]')


def fourbar(root, mechanism=MECHANISM):
    """The four-bar project: a hand-written model with NO dockings, the loop stated once, and the group map.

    Returns (root, mechanism path, groups path). The parts stand exactly where the mechanism's first frame puts
    them: the crank's own +X along world +Z (a positive linkage angle is a turn about -Y), the coupler level at
    z = 300, the rocker up from (400, 100).
    """
    from build123d import Align, Box, Location, Pos, Rotation
    root = Path(root)
    up = Rotation(0, -90, 0)                        # the linkage's +90 deg: the part's own +X to world +Z
    bar = lambda length, w: Box(length, w, w, align=(Align.MIN, Align.CENTER, Align.CENTER))
    write_model(root, [
        {"id": "ground", "process": "mill", "material": "S235JR", "card": {"density": 7.85},
         "shape": Box(520, 60, 20), "placements": [("ground", Pos(200, 0, -40), None)]},
        {"id": "crank", "process": "mill", "material": "S235JR", "card": {"density": 7.85},
         "shape": bar(300, 20), "placements": [("crank", Location((0, 0, 0)) * up, None)]},
        {"id": "coupler", "process": "mill", "material": "S235JR", "card": {"density": 7.85},
         "shape": bar(400, 16), "placements": [("coupler", Pos(0, 0, 300), None)]},
        {"id": "rocker", "process": "mill", "material": "S235JR", "card": {"density": 7.85},
         "shape": bar(200, 16), "placements": [("rocker", Location((400, 0, 100)) * up, None)]},
    ])
    (root / "mechanics" / "kinematics").mkdir(parents=True, exist_ok=True)
    mech = root / "mechanics" / "kinematics" / "mechanism.py"
    mech.write_text(mechanism)
    groups = root / "build" / "motion_groups.json"
    groups.write_text(json.dumps({"crank": "crank_g", "coupler": "coupler_g", "rocker": "rocker_g"}))
    # the fixture's own soundness: the declared starts really close the loop, so «rest = the CAD pose» is exact
    if mechanism is MECHANISM:
        frames = solved_frames(mech)
        assert all(abs(frames[0][n] - v) < 1e-9 for n, v in FOURBAR_START.items()), \
            f"fixture: the first frame is not the pose the parts are placed in: {frames[0]}"
    return root, mech, groups


def solved_frames(mech_path):
    """poses() of a mechanism.py, imported the way the framework's own scripts import it."""
    name = "mech_" + hashlib.sha256(str(mech_path).encode()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, str(mech_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [{k: float(v) for k, v in f.items()} for f in mod.poses()]


def travel_deg(mech_path):
    """joint -> (lowest, highest) solved value over the whole run, in degrees: the travel a range must cover."""
    frames = solved_frames(mech_path)
    return {n: (min(f[n] for f in frames), max(f[n] for f in frames)) for n in frames[0]}


# ================================================================ reading a URDF
def urdf_of(path):
    assert Path(path).is_file(), f"{path} was not written"
    return ET.parse(str(path)).getroot()


def urdf_tree(robot):
    """({link name: element}, {joint name: element}, {child link: (joint name, parent link)}, [root links])."""
    links = {e.get("name"): e for e in robot.findall("link")}
    joints = {e.get("name"): e for e in robot.findall("joint")}
    parent = {}
    for jn, e in joints.items():
        child, par = e.find("child").get("link"), e.find("parent").get("link")
        assert child in links and par in links, f"joint {jn} joins links that are not declared: {par} -> {child}"
        parent[child] = (jn, par)
    return links, joints, parent, [n for n in links if n not in parent]


def one_root(robot):
    links, joints, parent, roots = urdf_tree(robot)
    counts = Counter(e.find("child").get("link") for e in joints.values())
    assert all(v == 1 for v in counts.values()), f"a URDF link may be the child of one joint only: {counts}"
    assert len(roots) == 1, f"a URDF has exactly one root link, got {sorted(roots)}"
    return roots[0]


def xyz_of(e, attr="xyz"):
    return (0.0, 0.0, 0.0) if e is None or e.get(attr) is None else tuple(float(x) for x in e.get(attr).split())


def mesh_keys(link):
    """The part keys a link's <visual> meshes name, in order (the file stems of meshes/<key>.stl)."""
    out = []
    for v in link.findall("visual"):
        m = v.find("geometry/mesh")
        assert m is not None and m.get("filename"), f"a <visual> with no mesh file: {ET.tostring(v)[:200]}"
        out.append(Path(m.get("filename")).stem)
    return out


def link_of_key(robot, key):
    """The one link whose visuals name this part key."""
    links, _, _, _ = urdf_tree(robot)
    hit = [n for n, e in links.items() if key in mesh_keys(e)]
    assert len(hit) == 1, f"part {key[:8]} must sit in exactly one link, found {hit}"
    return hit[0]


def inertial_of(link):
    """(mass kg, com xyz m, {ixx..izz}) of a link, or None when it was written without an inertia."""
    e = link.find("inertial")
    if e is None:
        return None
    mass, tensor = e.find("mass"), e.find("inertia")
    assert mass is not None and tensor is not None, f"an <inertial> needs a mass and an inertia: {ET.tostring(e)[:200]}"
    return (float(mass.get("value")), xyz_of(e.find("origin")),
            {k: float(tensor.get(k)) for k in ("ixx", "ixy", "ixz", "iyy", "iyz", "izz")})


def limit_of(joint):
    e = joint.find("limit")
    if e is None or e.get("lower") is None:
        return None
    return float(e.get("lower")), float(e.get("upper"))


# ---------------------------------------------------------------- URDF forward kinematics
def _rpy(r, p, y):
    cr, sr, cp_, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return [[cy * cp_, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp_, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp_ * sr, cp_ * cr]]


def _T(rot, pos):
    return [[rot[0][0], rot[0][1], rot[0][2], pos[0]],
            [rot[1][0], rot[1][1], rot[1][2], pos[1]],
            [rot[2][0], rot[2][1], rot[2][2], pos[2]],
            [0.0, 0.0, 0.0, 1.0]]


def _mul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def _origin_T(e):
    o = e.find("origin")
    return _T(_rpy(*xyz_of(o, "rpy")), xyz_of(o, "xyz"))


def _motion_T(joint, q):
    kind, axis = joint.get("type"), xyz_of(joint.find("axis")) or (1.0, 0.0, 0.0)
    n = math.sqrt(sum(x * x for x in axis))
    assert n > 1e-9, f"joint {joint.get('name')} has no axis"
    a = [x / n for x in axis]
    if kind == "prismatic":
        return _T([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]], [a[0] * q, a[1] * q, a[2] * q])
    if kind in ("revolute", "continuous"):
        c, s = math.cos(q), math.sin(q)
        rot = [[c + a[0] * a[0] * (1 - c), a[0] * a[1] * (1 - c) - a[2] * s, a[0] * a[2] * (1 - c) + a[1] * s],
               [a[1] * a[0] * (1 - c) + a[2] * s, c + a[1] * a[1] * (1 - c), a[1] * a[2] * (1 - c) - a[0] * s],
               [a[2] * a[0] * (1 - c) - a[1] * s, a[2] * a[1] * (1 - c) + a[0] * s, c + a[2] * a[2] * (1 - c)]]
        return _T(rot, (0.0, 0.0, 0.0))
    if kind == "fixed":
        return _T([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]], (0.0, 0.0, 0.0))
    raise AssertionError(f"joint {joint.get('name')} is a {kind}, which this test cannot drive")


def urdf_world(robot, values):
    """{link name: 4x4 world transform} with each joint at `values[joint name]` (0 when not given)."""
    links, joints, parent, _ = urdf_tree(robot)
    root = one_root(robot)
    out = {root: _T([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]], (0.0, 0.0, 0.0))}
    pending = set(links) - {root}
    while pending:
        step = [n for n in pending if parent[n][1] in out]
        assert step, f"these links hang off nothing placed: {sorted(pending)}"
        for n in step:
            jn, par = parent[n]
            j = joints[jn]
            out[n] = _mul(_mul(out[par], _origin_T(j)), _motion_T(j, float(values.get(jn, 0.0))))
            pending.discard(n)
    return out


def urdf_visuals(robot, values):
    """[(part key, 4x4 world transform of the mesh)] - where the URDF puts every part at these joint values."""
    world = urdf_world(robot, values)
    out = []
    for name, link in urdf_tree(robot)[0].items():
        for v in link.findall("visual"):
            key = Path(v.find("geometry/mesh").get("filename")).stem
            out.append((key, _mul(world[name], _origin_T(v))))
    return out


def placement_targets(root):
    """[(part key, 4x4 where the model puts that placement)] in METRES - what a description must reproduce."""
    out = []
    for p in H.read_parts(root):
        if not (p.get("step") or p.get("mesh")):
            continue
        for pl in p.get("placements") or []:
            m = F.matrix(pl["loc"])
            out.append((p["key"], [[m[r][c] if c < 3 else m[r][3] * MM for c in range(4)] for r in range(3)]
                        + [[0.0, 0.0, 0.0, 1.0]]))
    return out


def _same_T(a, b, tol=1e-6):
    return all(abs(a[r][c] - b[r][c]) <= tol for r in range(3) for c in range(4))


def reproduces_the_model(robot, root, values, tol=1e-6):
    """True when the URDF at these joint values puts every placement exactly where the model does."""
    got, want = urdf_visuals(robot, values), placement_targets(root)
    if len(got) != len(want):
        return False
    left = list(got)
    for key, target in want:
        hit = next((i for i, (k, m) in enumerate(left) if k == key and _same_T(m, target, tol)), None)
        if hit is None:
            return False
        left.pop(hit)
    return True


# ================================================================ reading an MJCF
def mj_load(path):
    """(model, data) of the written MJCF, forwarded at MuJoCo's own zero (qpos0 = the joints' `ref`)."""
    mujoco = pytest.importorskip("mujoco")
    assert Path(path).is_file(), f"{path} was not written"
    m = mujoco.MjModel.from_xml_path(str(path))
    d = mujoco.MjData(m)
    mujoco.mj_resetData(m, d)
    mujoco.mj_forward(m, d)
    return m, d


def mj_name(m, kind, i):
    import mujoco
    return mujoco.mj_id2name(m, getattr(mujoco.mjtObj, kind), i)


def mj_geom_boxes(m, d):
    """[(body name, min xyz, max xyz)] in metres: every mesh geom's world bounding box as MuJoCo placed it."""
    import numpy as np
    verts = m.mesh_vert.reshape(-1, 3)
    out = []
    for g in range(m.ngeom):
        mid = m.geom_dataid[g]
        if mid < 0:
            continue
        v = verts[m.mesh_vertadr[mid]:m.mesh_vertadr[mid] + m.mesh_vertnum[mid]]
        w = v @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]
        out.append((mj_name(m, "mjOBJ_BODY", m.geom_bodyid[g]), tuple(w.min(0)), tuple(w.max(0))))
    return out


def mj_body_of_box(m, d, box_mm, tol=1e-5):
    """The body whose mesh geom spans this millimetre bounding box - a link named by what it holds, not by a name
    this test would otherwise have to guess."""
    want = (tuple(x * MM for x in box_mm[0]), tuple(x * MM for x in box_mm[1]))
    hit = [b for b, lo, hi in mj_geom_boxes(m, d)
           if all(abs(a - c) <= tol for a, c in zip(lo, want[0])) and all(abs(a - c) <= tol for a, c in zip(hi, want[1]))]
    assert len(hit) == 1, f"one body expected around {box_mm}, got {hit} of {mj_geom_boxes(m, d)}"
    return hit[0]


def mj_joints(m, d):
    """joint name -> {type, body, range, ref, anchor, axis} as MuJoCo read it (SI: metres and radians)."""
    import mujoco
    out = {}
    for i in range(m.njnt):
        a = m.jnt_qposadr[i]
        out[mj_name(m, "mjOBJ_JOINT", i)] = {
            "type": int(m.jnt_type[i]), "body": mj_name(m, "mjOBJ_BODY", m.jnt_bodyid[i]),
            "range": tuple(float(x) for x in m.jnt_range[i]), "limited": bool(m.jnt_limited[i]),
            "ref": float(m.qpos0[a]), "anchor": tuple(float(x) for x in d.xanchor[i]),
            "axis": tuple(float(x) for x in d.xaxis[i]),
            "hinge": int(m.jnt_type[i]) == int(mujoco.mjtJoint.mjJNT_HINGE),
            "slide": int(m.jnt_type[i]) == int(mujoco.mjtJoint.mjJNT_SLIDE)}
    return out


def mjcf_elements(path, tag):
    root = ET.parse(str(path)).getroot()
    return [e for parent in root.iter() for e in parent if e.tag == tag]


def mjcf_joint_elements(path):
    """{name: <joint> element} of the written XML (a nameless <default><joint> is not one of them)."""
    out = {}
    for e in mjcf_elements(path, "joint"):
        if e.get("name"):
            assert e.get("type"), f"joint {e.get('name')} must say its own type, not take it from a default"
            out[e.get("name")] = e
    return out


def pair(text):
    return tuple(float(x) for x in text.split())


def along(axis, want, tol=1e-6):
    """The axis is this direction, whichever way round the script chose to write it."""
    dot = sum(a * b for a, b in zip(axis, want))
    norm = math.sqrt(sum(a * a for a in axis)) * math.sqrt(sum(b * b for b in want))
    return abs(abs(dot) - norm) <= tol and norm > 0


# ================================================================ STL
def stl_box_mm(path):
    b = Path(path).read_bytes()
    assert b[:5] != b"solid", f"{Path(path).name} is an ASCII STL; the meshes must be binary"
    n = struct.unpack("<I", b[80:84])[0]
    assert n and len(b) == 84 + 50 * n, f"{Path(path).name} is not a valid binary STL"
    pts = []
    for i in range(n):
        off = 84 + 50 * i + 12
        v = struct.unpack("<9f", b[off:off + 36])
        pts += [v[0:3], v[3:6], v[6:9]]
    return (tuple(min(p[k] for p in pts) for k in range(3)), tuple(max(p[k] for p in pts) for k in range(3)))


# ================================================================ small hand-written models
def weld_pair(root):
    """One cube part placed twice, the second welded rigidly onto the first: ONE link of two known masses.

    Two 20 mm cubes of 2.0 g/cm3, their centres 100 mm apart along X, so the link's mass, its centre of mass and
    every term of its inertia tensor about that centre are arithmetic (DW3).
    """
    from build123d import Box, Location, Pos
    return write_model(root, [
        {"id": "cube", "process": "mill", "material": "AL6060", "card": {"density": 2.0},
         "shape": Box(20, 20, 20),
         "joints": {"a": rigid(), "b": rigid(Location((100, 0, 0)))},
         "placements": [("cube", Pos(-50, 0, 0), None),
                        ("cube_2", Pos(50, 0, 0), {"to": "cube", "at": "b", "by": "a", "type": "rigid"})]},
    ])


CUBE_MM, CUBE_RHO, CUBE_GAP_MM = 20.0, 2.0, 100.0


def weld_pair_expected():
    """(mass kg, com m, inertia about the com) of weld_pair's single link, by hand."""
    a = CUBE_MM * MM
    m = (CUBE_MM ** 3) * CUBE_RHO * 1e-6                    # mm3 x g/cm3 -> kg
    own = m * (a * a + a * a) / 12.0                        # a cube, about any of its own axes
    off = m * (CUBE_GAP_MM * MM / 2.0) ** 2                 # parallel axis, for the two axes across X
    return 2 * m, (0.0, 0.0, 0.0), {"ixx": 2 * own, "iyy": 2 * (own + off), "izz": 2 * (own + off),
                                    "ixy": 0.0, "ixz": 0.0, "iyz": 0.0}


def no_density(root):
    """A part nothing can weigh but the density of last resort: no card, a material no catalogue holds (DW3)."""
    from build123d import Box, Location
    return write_model(root, [
        {"id": "zagotovka", "process": "other", "material": "zzz-unobtanium", "shape": Box(10, 10, 10),
         "placements": [("zagotovka", Location(), None)]},
    ])


def mesh_only(root, with_solid=False):
    """A view-only mesh part (no STEP, so no solid to weigh), alone or beside a cube of a known density (DW3, DW6)."""
    from build123d import Box, Pos
    specs = [{"id": "kozhuh", "process": "other", "material": "S235JR",
              "mesh": box_mesh(200, -20, 0, 240, 20, 60),
              "placements": [("kozhuh", Pos(0, 0, 0), None)]}]
    if with_solid:
        specs.append({"id": "cube", "process": "mill", "material": "AL6060", "card": {"density": 2.0},
                      "shape": Box(20, 20, 20), "placements": [("cube", Pos(0, 0, 0), None)]})
    return write_model(root, specs)


def docked_with(root, joint, value=None):
    """A plate the machine stands on and a pin docked onto it by `joint` - whatever type that joint is."""
    from build123d import Box, Cylinder, Location, Pos
    e = {"to": "plate", "at": "seat", "by": "end", "type": joint["type"]}
    if value is not None:
        e["value"] = value
    return write_model(root, [
        {"id": "plate", "process": "mill", "material": "S235JR", "card": {"density": 7.85},
         "shape": Box(100, 100, 10), "joints": {"seat": joint},
         "placements": [("plate", Location(), None)]},
        {"id": "pin", "process": "turn", "material": "S235JR", "card": {"density": 7.85},
         "shape": Cylinder(5, 30), "joints": {"end": rigid()},
         "placements": [("pin", Pos(0, 0, 10), e)]},        # on the seat: where that joint puts it
    ])


# ================================================================ DW1: links
def test_rigidly_docked_parts_come_out_as_one_link(joint_baseline, tmp_path):
    """base + rail + six screws are bolted together: one link, and NO joint with no freedom anywhere."""
    root, m, _ = joint_baseline
    m.ok()
    e = run_export(root, tmp_path / "robot", fmt="both").ok()
    robot = urdf_of(e.urdf)
    links, joints, _, _ = urdf_tree(robot)
    keys = keys_of(root)
    assert len(links) == 3, f"base+rail+screws, slider, arm = 3 links, got {sorted(links)}"
    assert len(joints) == 2, f"two freedoms were declared, got {sorted(joints)}"
    assert not [n for n, j in joints.items() if j.get("type") == "fixed"], \
        f"a rigid docking is a weld into one link, never a fixed joint: {sorted(joints)}"
    base = link_of_key(robot, keys["base"])
    welded = set(mesh_keys(links[base]))
    screws = {p["key"] for p in H.read_parts(root) if p.get("process") == "bought" and p.get("step")}
    assert keys["rail"] in welded and screws <= welded, \
        f"the rail and the screws belong in the base link; it holds {sorted(k[:8] for k in welded)}"
    assert link_of_key(robot, keys["slider"]) != base and link_of_key(robot, keys["arm"]) != base


def test_the_part_the_machine_is_bolted_down_by_is_the_base_link(joint_baseline, tmp_path):
    """A placement with no joint is welded to the world: the root link, and the machine's own coordinates."""
    root, m, _ = joint_baseline
    m.ok()
    e = run_export(root, tmp_path / "robot", fmt="urdf").ok()
    robot = urdf_of(e.urdf)
    assert one_root(robot) == link_of_key(robot, keys_of(root)["base"])
    # the base link's frame IS machine coordinates: its visuals stand where parts.json places them
    pm = J.placement_map(root)
    base = urdf_tree(robot)[0][one_root(robot)]
    want = {J.translation(pm[n][1]["loc"]) for n in ("base", "rail")}
    got = {tuple(round(x / MM, 6) for x in xyz_of(v.find("origin"))) for v in base.findall("visual")}
    assert want <= got, f"the base link must be the machine's own coordinates; its visuals sit at {sorted(got)}"


def test_with_no_dockings_the_links_come_from_the_groups(tmp_path):
    """The four-bar docks nothing: --groups plus the mechanism's LINKS say which placement is which link, and
    the ground - in no group - is the base link."""
    root, mech, groups = fourbar(tmp_path / "proj")
    e = run_export(root, tmp_path / "robot", fmt="urdf", mechanism=mech, groups=groups).ok()
    robot = urdf_of(e.urdf)
    links, joints, _, _ = urdf_tree(robot)
    keys = keys_of(root)
    assert len(links) == 4 and len(joints) == 3, f"links {sorted(links)}, joints {sorted(joints)}"
    assert one_root(robot) == link_of_key(robot, keys["ground"]), "the ungrouped ground is the base link"
    moving = {link_of_key(robot, keys[p]) for p in ("crank", "coupler", "rocker")}
    assert len(moving) == 3 and one_root(robot) not in moving, \
        f"crank, coupler and rocker each follow their own mechanism joint, got {sorted(moving)}"


# ================================================================ DW2: joints, their axes, anchors and ranges
def test_a_linear_docking_becomes_a_prismatic_joint_in_metres(joint_baseline, tmp_path):
    root, m, _ = joint_baseline
    m.ok()
    robot = urdf_of(run_export(root, tmp_path / "robot", fmt="urdf").ok().urdf)
    links, joints, parent, _ = urdf_tree(robot)
    jn, _ = parent[link_of_key(robot, keys_of(root)["slider"])]
    assert joints[jn].get("type") == "prismatic", f"the slider slides: {joints[jn].get('type')}"
    lo, hi = limit_of(joints[jn])
    assert (lo, hi) == pytest.approx((-0.060, 0.060), abs=1e-9), f"the rail's travel is -60..60 mm, got {(lo, hi)}"


def test_a_revolute_docking_becomes_a_revolute_joint_in_radians(joint_baseline, tmp_path):
    root, m, _ = joint_baseline
    m.ok()
    robot = urdf_of(run_export(root, tmp_path / "robot", fmt="urdf").ok().urdf)
    links, joints, parent, _ = urdf_tree(robot)
    jn, par = parent[link_of_key(robot, keys_of(root)["arm"])]
    assert par == link_of_key(robot, keys_of(root)["slider"]), "the arm hangs on the slider"
    assert joints[jn].get("type") == "revolute", f"the hinge turns and is bounded: {joints[jn].get('type')}"
    lo, hi = limit_of(joints[jn])
    assert (lo, hi) == pytest.approx((0.0, math.pi / 2), abs=1e-9), f"0..90 deg is 0..pi/2 rad, got {(lo, hi)}"


def test_the_urdf_driven_to_the_docking_values_stands_where_the_cad_does(joint_baseline, tmp_path):
    """Anchors, axes and link origins together: driven to the slider's 30 mm and the arm's 90 deg - the values
    the model was docked at - the URDF must put every part exactly where parts.json does, and that must happen
    for ONE choice of sign (a joint written about the wrong axis would reach it either way or never)."""
    root, m, _ = joint_baseline
    m.ok()
    robot = urdf_of(run_export(root, tmp_path / "robot", fmt="urdf").ok().urdf)
    links, joints, parent, _ = urdf_tree(robot)
    slide, _ = parent[link_of_key(robot, keys_of(root)["slider"])]
    hinge, _ = parent[link_of_key(robot, keys_of(root)["arm"])]
    hits = [(sp, sh) for sp in (1, -1) for sh in (1, -1)
            if reproduces_the_model(robot, root, {slide: sp * 0.030, hinge: sh * math.pi / 2})]
    assert len(hits) == 1, (f"exactly one sign of (30 mm, 90 deg) must reproduce the model, {len(hits)} did: "
                            f"{hits}; at zero it reproduces it "
                            f"{reproduces_the_model(robot, root, {})}")


def test_the_urdf_says_the_start_values_it_cannot_carry(joint_baseline, tmp_path):
    """URDF has no starting value. The 30 mm and the 90 deg the model is docked at are lost - and said, not lost
    silently, naming both placements."""
    root, m, _ = joint_baseline
    m.ok()
    e = run_export(root, tmp_path / "robot", fmt="urdf").ok()
    e.names("slider", "arm")
    assert "30" in e.log and ("90" in e.log or "1.57" in e.log), \
        f"the values that cannot be written must be said too:\n{e.log[-2000:]}"


def test_the_mjcf_says_its_angles_in_degrees_and_its_ranges_with_them(joint_baseline, tmp_path):
    root, m, _ = joint_baseline
    m.ok()
    e = run_export(root, tmp_path / "robot", fmt="mjcf").ok()
    comp = [c for c in mjcf_elements(e.mjcf, "compiler")]
    assert comp and comp[0].get("angle") == "degree", f"<compiler angle=\"degree\"> expected, got {comp}"
    els = mjcf_joint_elements(e.mjcf)
    kinds = sorted(j.get("type") for j in els.values())
    assert kinds == ["hinge", "slide"], f"a linear docking is a slide and a revolute one a hinge, got {kinds}"
    by_kind = {j.get("type"): j for j in els.values()}
    assert all(j.get("range") for j in by_kind.values()), f"both these joints are bounded: {els}"
    assert pair(by_kind["slide"].get("range")) == pytest.approx((-0.060, 0.060), abs=1e-9), \
        f"the rail's travel in metres: {by_kind['slide'].get('range')}"
    assert sorted(pair(by_kind["hinge"].get("range"))) == pytest.approx([0.0, 90.0], abs=1e-9), \
        f"degrees in the XML, not radians: {by_kind['hinge'].get('range')}"
    refs = {k: j.get("ref") for k, j in by_kind.items()}
    assert refs["slide"] is not None and refs["hinge"] is not None, \
        f"the pose the CAD is drawn in is written as each joint's ref: {refs}"
    assert float(refs["slide"]) == pytest.approx(0.030, abs=1e-9), f"the slider is docked at 30 mm: {refs}"
    assert abs(float(refs["hinge"])) == pytest.approx(90.0, abs=1e-9), f"the arm is docked at 90 deg: {refs}"


def test_the_mjcf_axes_and_anchors_are_the_machine_s_own(joint_baseline, tmp_path):
    """The rail's carriage axis runs along +X through (100, 50, 30) mm, the slider's hinge about Z through
    (130, 50, 40) mm: what MuJoCo makes of the written model, in its own world coordinates."""
    root, m, _ = joint_baseline
    m.ok()
    e = run_export(root, tmp_path / "robot", fmt="mjcf").ok()
    mm, d = mj_load(e.mjcf)
    js = mj_joints(mm, d)
    slide = [j for j in js.values() if j["slide"]]
    hinge = [j for j in js.values() if j["hinge"]]
    assert len(slide) == 1 and len(hinge) == 1, f"one slide and one hinge: {js}"
    assert along(slide[0]["axis"], (1, 0, 0)), f"the carriage runs along X, got {slide[0]['axis']}"
    assert slide[0]["anchor"] == pytest.approx((0.100, 0.050, 0.030), abs=1e-6), slide[0]["anchor"]
    assert along(hinge[0]["axis"], (0, 0, 1)), f"the hinge turns about Z, got {hinge[0]['axis']}"
    assert hinge[0]["anchor"] == pytest.approx((0.130, 0.050, 0.040), abs=1e-6), hinge[0]["anchor"]


def test_the_mjcf_stands_at_rest_in_the_pose_the_cad_is_drawn_in(joint_baseline, tmp_path):
    """`ref` is what makes MuJoCo's zero the model's own pose: at reset every box part must span exactly the
    world box jointfiles documents."""
    root, m, _ = joint_baseline
    m.ok()
    e = run_export(root, tmp_path / "robot", fmt="mjcf").ok()
    mm, d = mj_load(e.mjcf)
    boxes = mj_geom_boxes(mm, d)
    for name, (lo, hi) in J.WORLD_BBOX.items():
        want = (tuple(x * MM for x in lo), tuple(x * MM for x in hi))
        hit = [b for b, l, h in boxes
               if all(abs(a - c) <= 1e-6 for a, c in zip(l, want[0])) and all(abs(a - c) <= 1e-6 for a, c in zip(h, want[1]))]
        assert len(hit) == 1, f"{name} must stand at {lo}..{hi} mm; the model holds {boxes}"
    js = mj_joints(mm, d)
    assert abs([j for j in js.values() if j["slide"]][0]["ref"]) == pytest.approx(0.030, abs=1e-9)
    assert abs([j for j in js.values() if j["hinge"]][0]["ref"]) == pytest.approx(math.pi / 2, abs=1e-9)


def test_a_revolute_open_at_both_ends_becomes_a_continuous_joint(tmp_path):
    root = docked_with(tmp_path / "proj",
                       axis_joint("revolute", (0, 0, 10), (0, 0, 1), (-math.inf, math.inf)), value=0.0)
    e = run_export(root, tmp_path / "robot", fmt="both").ok()
    robot = urdf_of(e.urdf)
    joints = urdf_tree(robot)[1]
    assert len(joints) == 1, sorted(joints)
    j = list(joints.values())[0]
    assert j.get("type") == "continuous", f"nothing bounds this turn: {j.get('type')}"
    assert limit_of(j) is None, "a continuous joint has no lower/upper to state"
    mm, d = mj_load(e.mjcf)
    hinge = [x for x in mj_joints(mm, d).values() if x["hinge"]]
    assert len(hinge) == 1 and not hinge[0]["limited"], f"the MJCF hinge must be unlimited too: {hinge}"


# ================================================================ DW3: mass and inertia
def test_a_rigid_group_adds_up_by_the_parallel_axis_theorem(tmp_path):
    """Two 20 mm cubes of 2.0 g/cm3, 100 mm apart, welded: 32 g about their shared centre of mass, with the
    across-X terms carrying m*d^2 and nothing off the diagonal."""
    root = weld_pair(tmp_path / "proj")
    robot = urdf_of(run_export(root, tmp_path / "robot", fmt="urdf").ok().urdf)
    links = urdf_tree(robot)[0]
    assert len(links) == 1, f"a welded pair is one link, got {sorted(links)}"
    got = inertial_of(list(links.values())[0])
    assert got is not None, "a link of two weighable parts must carry an inertia"
    mass, com, tensor = got
    want_mass, want_com, want_I = weld_pair_expected()
    assert mass == pytest.approx(want_mass, rel=1e-6), f"2 x 16 g expected, got {mass} kg"
    assert com == pytest.approx(want_com, abs=1e-9), f"the centre of mass is midway, got {com}"
    for k, v in want_I.items():
        assert tensor[k] == pytest.approx(v, rel=1e-6, abs=1e-12), f"{k}: {tensor[k]}, expected {v} ({tensor})"


def test_a_part_no_catalogue_can_weigh_falls_back_to_steel_and_is_reported(tmp_path):
    """No card.density and a material nothing knows: 7.85 g/cm3 is used AND the part is named for it."""
    root = no_density(tmp_path / "proj")
    e = run_export(root, tmp_path / "robot", fmt="urdf").ok()
    e.names("zagotovka")
    mass, _, _ = inertial_of(list(urdf_tree(urdf_of(e.urdf))[0].values())[0])
    assert mass == pytest.approx(10.0 ** 3 * STEEL * 1e-6, rel=1e-6), \
        f"1 cm3 at 7.85 g/cm3 is 7.85 g, got {mass} kg"


def test_a_part_with_no_solid_adds_no_mass_and_is_reported(tmp_path):
    """A view-only mesh part has no solid to measure: the link weighs only the cube beside it, and the mesh part
    is named rather than guessed at."""
    root = mesh_only(tmp_path / "proj", with_solid=True)
    e = run_export(root, tmp_path / "robot", fmt="urdf").ok()
    e.names("kozhuh")
    mass, com, _ = inertial_of(list(urdf_tree(urdf_of(e.urdf))[0].values())[0])
    assert mass == pytest.approx(CUBE_MM ** 3 * CUBE_RHO * 1e-6, rel=1e-6), \
        f"only the cube can be weighed (16 g), got {mass} kg"
    assert com == pytest.approx((0.0, 0.0, 0.0), abs=1e-9), f"the mesh part must not drag the centre of mass: {com}"


# ================================================================ DW4: closed loops
def test_the_loop_comes_out_of_the_mjcf_as_a_connect_at_its_point(tmp_path):
    """The four-bar's `meet` is an equality connect between the coupler's and the rocker's links, anchored at the
    point they stay together at - (400, 0, 300) mm."""
    mujoco = pytest.importorskip("mujoco")
    root, mech, groups = fourbar(tmp_path / "proj")
    e = run_export(root, tmp_path / "robot", fmt="mjcf", mechanism=mech, groups=groups).ok()
    m, d = mj_load(e.mjcf)
    assert m.neq == 1, f"one loop was stated, {m.neq} equality constraints were written"
    assert int(m.eq_type[0]) == int(mujoco.mjtEq.mjEQ_CONNECT), f"a loop is a connect, got eq_type {m.eq_type[0]}"
    bodies = {mj_name(m, "mjOBJ_BODY", int(m.eq_obj1id[0])), mj_name(m, "mjOBJ_BODY", int(m.eq_obj2id[0]))}
    want = {mj_body_of_box(m, d, FOURBAR_BOX_MM["coupler"]), mj_body_of_box(m, d, FOURBAR_BOX_MM["rocker"])}
    assert bodies == want, f"the coupler and the rocker are what meet, got {sorted(bodies)}"
    b1 = int(m.eq_obj1id[0])
    anchor = tuple(d.xpos[b1] + d.xmat[b1].reshape(3, 3) @ m.eq_data[0][:3])
    assert anchor == pytest.approx(tuple(x * MM for x in FOURBAR_MEET_MM), abs=1e-6), \
        f"the connect must sit on the point the loop closes at, got {anchor} m"


def test_the_mjcf_of_a_stated_loop_stands_at_rest_where_the_model_does(tmp_path):
    """DW7 and DW2 together on the mechanism side: the MJCF compiles, and at MuJoCo's zero every bar of the
    four-bar spans exactly the box the model places it in (which is the mechanism's first frame)."""
    root, mech, groups = fourbar(tmp_path / "proj")
    e = run_export(root, tmp_path / "robot", fmt="mjcf", mechanism=mech, groups=groups).ok()
    m, d = mj_load(e.mjcf)
    for name, box in FOURBAR_BOX_MM.items():
        mj_body_of_box(m, d, box)                     # asserts exactly one body spans it
    js = mj_joints(m, d)
    assert len(js) == 3 and all(x["hinge"] for x in js.values()), f"three hinges expected: {js}"
    assert all(along(x["axis"], (0, 1, 0)) for x in js.values()), \
        f"a planar loop turns about Y: {[x['axis'] for x in js.values()]}"
    want = travel_deg(mech)                           # the mechanism's own joint names are these parts' ids
    for part, box in FOURBAR_BOX_MM.items():
        if part == "ground":
            continue
        mine = [x for x in js.values() if x["body"] == mj_body_of_box(m, d, box)]
        assert len(mine) == 1, f"{part} must carry exactly one joint, got {mine}"
        lo, hi = sorted(math.degrees(v) for v in mine[0]["range"])
        ref, (a, b) = math.degrees(mine[0]["ref"]), want[part]
        assert hi - lo == pytest.approx(abs(b - a), abs=1e-3), \
            f"{part}: its range spans {hi - lo} deg, its solved travel {abs(b - a)} deg"
        assert min(abs(ref - lo), abs(ref - hi)) < 1e-3, \
            f"{part}: the first frame is one end of the sweep, so ref {ref} must be an end of {(lo, hi)}"


def test_the_urdf_breaks_the_loop_and_says_which_one_and_where(tmp_path):
    """URDF cannot state a loop: what comes out is a tree, and the script says which loop it broke."""
    root, mech, groups = fourbar(tmp_path / "proj")
    e = run_export(root, tmp_path / "robot", fmt="urdf", mechanism=mech, groups=groups).ok()
    robot = urdf_of(e.urdf)
    one_root(robot)                                   # a tree: one root, every link the child of one joint
    links, joints, _, _ = urdf_tree(robot)
    assert len(links) == 4 and len(joints) == 3, f"links {sorted(links)}, joints {sorted(joints)}"
    said = " ".join(ln for ln in e.log.splitlines() if "coupler" in ln and "rocker" in ln)
    assert said, f"one message must name the loop that was broken:\n{e.log[-2000:]}"
    assert any(s in said for s in ("400", "0.4")) and any(s in said for s in ("300", "0.3")), \
        f"saying which loop was broken includes where it was broken: {said!r}"


def test_a_loop_link_with_no_body_of_its_own_is_refused(tmp_path):
    """`meet(..., distance=d)` is a link nothing was drawn for: there is no part to make a body of, so it is
    refused, saying to declare it as a body - never invented."""
    root, mech, groups = fourbar(tmp_path / "proj", mechanism=MECHANISM_DISTANCE)
    e = run_export(root, tmp_path / "robot", fmt="both", mechanism=mech, groups=groups).refused().wrote_nothing()
    assert "body" in e.log.lower(), f"the message must send the author to declare it as a body:\n{e.log[-2000:]}"


# ================================================================ DW5: drives
def test_the_drive_becomes_an_actuator_covering_the_joint_s_travel(tmp_path):
    """One DRIVES entry on the crank: one MJCF actuator on the crank's hinge, its ctrlrange covering the whole
    range that joint was given.

    Compared in the COMPILED model, not in the XML's own units: `<compiler angle="degree">` converts a joint's
    `ref` and `range` and does NOT convert an actuator's `ctrlrange`, so a hinge's ctrlrange is radians in the
    file whatever the compiler is told. Checked on mujoco 3.17: `ctrlrange="0 90"` on a hinge under
    `angle="degree"` drives the joint to 90 RADIANS, and `m.actuator_ctrlrange` reads back 90."""
    root, mech, groups = fourbar(tmp_path / "proj")
    e = run_export(root, tmp_path / "robot", fmt="mjcf", mechanism=mech, groups=groups).ok()
    acts = [a for a in mjcf_elements(e.mjcf, "position")] + [a for a in mjcf_elements(e.mjcf, "motor")] \
        + [a for a in mjcf_elements(e.mjcf, "general")] + [a for a in mjcf_elements(e.mjcf, "velocity")]
    assert len(acts) == 1, f"the machine states one drive; the MJCF has {len(acts)} actuators"
    act = acts[0]
    assert act.get("joint"), f"the actuator must drive the joint by name: {ET.tostring(act)[:200]}"
    m, d = mj_load(e.mjcf)
    crank = mj_body_of_box(m, d, FOURBAR_BOX_MM["crank"])
    assert mj_joints(m, d)[act.get("joint")]["body"] == crank, \
        f"the drive turns the crank, not {mj_joints(m, d)[act.get('joint')]['body']}"
    assert act.get("ctrlrange"), f"the drive must state what it can be asked for: {ET.tostring(act)[:200]}"
    jel = mjcf_joint_elements(e.mjcf)[act.get("joint")]
    assert jel.get("range"), f"the driven joint must state its travel: {ET.tostring(jel)[:200]}"
    mujoco = pytest.importorskip("mujoco")
    aid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_ACTUATOR, act.get("name") or act.get("joint"))
    jid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, act.get("joint"))
    lo, hi = sorted(float(v) for v in m.actuator_ctrlrange[aid])
    jlo, jhi = sorted(float(v) for v in m.jnt_range[jid])
    assert lo <= jlo + 1e-6 and hi >= jhi - 1e-6, \
        f"a ctrlrange of {(lo, hi)} does not cover the crank's travel {(jlo, jhi)} (both as compiled)"


def test_more_than_one_drive_is_refused_rather_than_half_written(tmp_path):
    root, mech, groups = fourbar(tmp_path / "proj", mechanism=MECHANISM.replace(*TWO_DRIVES))
    e = run_export(root, tmp_path / "robot", fmt="both", mechanism=mech, groups=groups).refused().wrote_nothing()
    e.names("crank", "rocker")


# ================================================================ DW6: refusals, never invention
@pytest.mark.parametrize("kind", ["cylindrical", "ball"])
def test_a_joint_urdf_has_no_type_for_is_refused_by_name(tmp_path, kind):
    """Two freedoms in one joint is not a URDF joint. Rather than a chain of made-up links of a mass nobody
    measured, the export is refused, naming the joint."""
    joint = (ball_joint() if kind == "ball"
             else axis_joint("cylindrical", (0, 0, 10), (0, 0, 1), (0.0, 20.0), angular_range=[0.0, 180.0]))
    root = docked_with(tmp_path / "proj", joint)
    e = run_export(root, tmp_path / "robot", fmt="urdf").refused()
    assert not e.urdf.exists(), "a refused URDF must not be written"
    e.names("seat")
    assert kind in e.log, f"the message must say what the joint is:\n{e.log[-2000:]}"


def test_a_link_that_could_not_be_weighed_gets_no_inertia(tmp_path):
    """Nothing to measure means nothing is written: no <inertial> at all, rather than an invented one."""
    root = mesh_only(tmp_path / "proj")
    e = run_export(root, tmp_path / "robot", fmt="urdf").ok()
    e.names("kozhuh")
    links = urdf_tree(urdf_of(e.urdf))[0]
    assert len(links) == 1, sorted(links)
    assert inertial_of(list(links.values())[0]) is None, \
        "a link of a part with no solid must carry no inertia at all"


# ================================================================ DW7 + the files themselves
def test_the_mjcf_of_a_docked_machine_compiles_in_mujoco(joint_baseline, tmp_path):
    root, m, _ = joint_baseline
    m.ok()
    e = run_export(root, tmp_path / "robot", fmt="mjcf").ok()
    mm, d = mj_load(e.mjcf)
    assert mm.njnt == 2 and mm.ngeom >= 8, f"njnt {mm.njnt}, ngeom {mm.ngeom}"
    assert all(float(x) > 0 for x in mm.body_mass[1:]), f"every link of this machine can be weighed: {mm.body_mass}"


def test_one_binary_stl_per_part_with_geometry_in_millimetres(joint_baseline, tmp_path):
    """meshes/<part key>.stl, binary, in the part's own coordinates and in millimetres - the same box its STEP
    spans, so nothing was scaled on the way out."""
    root, m, _ = joint_baseline
    m.ok()
    e = run_export(root, tmp_path / "robot", fmt="both").ok()
    solid = [p for p in H.read_parts(root) if p.get("step")]
    assert len(solid) >= 6, [p["id"] for p in solid]
    assert {p.stem for p in e.meshes.glob("*.stl")} == {p["key"] for p in solid}, \
        f"one STL per part with geometry: {sorted(p.name for p in e.meshes.iterdir())}"
    for p in solid:
        lo, hi = stl_box_mm(e.meshes / f"{p['key']}.stl")
        bb = H.step_bbox(root / p["step"])
        # a flat-sided part is exact; a turned screw loses a chord's worth of its radius to the triangles
        tol = 0.01 if p["id"] in J.WORLD_BBOX else 1.0
        assert lo == pytest.approx((bb.min.X, bb.min.Y, bb.min.Z), abs=tol), f"{p['id']}: STL at {lo}, STEP at {bb.min}"
        assert hi == pytest.approx((bb.max.X, bb.max.Y, bb.max.Z), abs=tol), f"{p['id']}: STL at {hi}, STEP at {bb.max}"


@pytest.mark.parametrize("fmt,urdf,mjcf", [("urdf", True, False), ("mjcf", False, True),
                                           ("both", True, True), (None, True, True)])
def test_the_format_asked_for_is_what_is_written(joint_baseline, tmp_path, fmt, urdf, mjcf):
    root, m, _ = joint_baseline
    m.ok()
    e = run_export(root, tmp_path / "robot", fmt=fmt).ok()
    assert e.urdf.exists() is urdf, f"--format {fmt}: {e.urdf.name} exists {e.urdf.exists()}"
    assert e.mjcf.exists() is mjcf, f"--format {fmt}: {e.mjcf.name} exists {e.mjcf.exists()}"
    assert list(e.meshes.glob("*.stl")), "the meshes are written whatever the format"


def test_collide_is_what_makes_the_meshes_colliders(joint_baseline, tmp_path):
    """Simplified collision shapes are another job; what --collide does is let the parts collide at all."""
    root, m, _ = joint_baseline
    m.ok()
    plain = run_export(root, tmp_path / "plain", fmt="both").ok()
    assert not urdf_of(plain.urdf).findall("link/collision"), "without --collide nothing collides"
    hot = run_export(root, tmp_path / "hot", fmt="both", collide=True).ok()
    robot = urdf_of(hot.urdf)
    for name, link in urdf_tree(robot)[0].items():
        assert len(link.findall("collision")) == len(link.findall("visual")), \
            f"--collide: link {name} has {len(link.findall('collision'))} colliders for {len(link.findall('visual'))} visuals"
    m2, d2 = mj_load(hot.mjcf)
    assert any(int(x) for x in m2.geom_contype), "--collide: the MJCF geoms must collide too"


def test_a_part_turned_square_onto_an_axis_keeps_its_rotation_in_both_formats():
    """A part lying along an axis - a rod turned 90 deg about Y - is URDF's gimbal lock, and a rod along an
    axis is the commonest placement there is (every shaft, every cylinder). Written as `rpy`, read back as the
    matrix it came from, in both poles; and as the MJCF `quat` beside it, which has no poles at all."""
    sys.path.insert(0, str(SCRIPTS))
    import export_robot as X

    def matrix_of_rpy(text):
        roll, pitch, yaw = (float(v) for v in text.split())
        cr, sr, cp, sp, cy, sy = (math.cos(roll), math.sin(roll), math.cos(pitch),
                                  math.sin(pitch), math.cos(yaw), math.sin(yaw))
        return [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                [-sp, cp * sr, cp * cr]]

    def matrix_of_quat(text):
        w, x, y, z = (float(v) for v in text.split())
        return [[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]]

    for degrees in (90.0, -90.0, 90.0001, 37.0, 180.0, 179.9999):   # 180 goes through _quat's other branch
        want = X._about((0.0, 1.0, 0.0), degrees)
        said = matrix_of_rpy(X._rpy(want))
        assert all(abs(a - b) < 1e-9 for ra, rb in zip(said, want) for a, b in zip(ra, rb)), \
            f"{degrees} deg about Y comes back as {said}, not {want}"
        turned = matrix_of_quat(X._quat_text(want))
        assert all(abs(a - b) < 1e-9 for ra, rb in zip(turned, want) for a, b in zip(ra, rb)), \
            f"{degrees} deg about Y as a quaternion comes back as {turned}, not {want}"


def test_the_urdf_of_a_rod_along_an_axis_puts_it_where_the_model_does(tmp_path):
    """The same through the real artifact: a rod placed 90 deg about Y, and the URDF's own `rpy` for it."""
    from build123d import Align, Box, Rotation
    root = write_model(tmp_path / "proj", [
        {"id": "rod", "process": "turn", "material": "steel", "card": {"density": 7.85},
         "shape": Box(200, 20, 20, align=(Align.MIN, Align.CENTER, Align.CENTER)),
         "placements": [("rod", Rotation(0, -90, 0), None)]},
    ])
    e = run_export(root, tmp_path / "robot", fmt="urdf").ok()
    robot = urdf_of(e.urdf)
    assert reproduces_the_model(robot, root, {}), \
        f"the rod's own 90 deg about Y is lost on the way out:\n{e.log[-1500:]}"


def test_a_stored_mesh_part_is_drawn_even_though_it_cannot_be_weighed(tmp_path):
    """A vendor part given as triangles has no solid, so it adds no mass - but it is still a part the machine
    has, and a description missing it is a machine nobody built. It is written, and in both formats."""
    root = mesh_only(tmp_path / "proj", with_solid=True)
    e = run_export(root, tmp_path / "robot", fmt="both").ok()
    keys = keys_of(root)
    robot = urdf_of(e.urdf)
    drawn = [k for link in robot.findall("link") for k in mesh_keys(link)]
    assert keys["kozhuh"] in drawn, f"the mesh part is not in the URDF: {drawn}"
    assert (e.meshes / f"{keys['kozhuh']}.stl").stat().st_size > 84, "its STL was not written"
    meshes = {el.get("file") for el in mjcf_elements(e.mjcf, "mesh")}
    assert f"{keys['kozhuh']}.stl" in meshes, f"the mesh part is not in the MJCF: {sorted(meshes)}"
    assert reproduces_the_model(robot, root, {}), "the mesh part does not stand where the model puts it"
    e.names("kozhuh")                                 # and it is said that it was not weighed


# ============================================ what the review of #20 found missing (gaps, and the fixes for them)
def test_the_exported_loop_follows_the_model_s_own_solve_when_it_is_driven(tmp_path):
    """Done-when 1, as a number: the MJCF driven from its actuator over the whole solve agrees with
    `mechanism.poses()` joint by joint, with the loop's equality closed. Driven quasi-statically - the
    file states no end cushion and none of its joints is a real stop, so a step of the full force is an
    input the machine never sees."""
    mujoco = pytest.importorskip("mujoco")
    root, mech, groups = fourbar(tmp_path / "proj")
    e = run_export(root, tmp_path / "robot", fmt="mjcf", mechanism=mech, groups=groups).ok()
    m, d = mj_load(e.mjcf)
    act = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_ACTUATOR, "crank")
    assert act >= 0, "the crank's drive must be named after the joint it drives"
    adr = {n: m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n)]
           for n in ("crank", "coupler", "rocker")}
    worst, residual = dict.fromkeys(adr, 0.0), 0.0
    for frame in solved_frames(mech):
        d.ctrl[act] = math.radians(frame["crank"])
        for _ in range(int(0.5 / m.opt.timestep)):
            mujoco.mj_step(m, d)
        for name, i in adr.items():
            worst[name] = max(worst[name], abs(math.degrees(d.qpos[i]) - frame[name]))
        if m.neq:
            residual = max(residual, float(max(abs(v) for v in d.efc_pos[:3])))
    assert all(v < 0.05 for v in worst.values()), \
        f"driven over its own solve the loop drifts from the model: {worst} deg"
    assert residual < 1e-4, f"the loop's equality is {residual} m open while it runs"


def test_a_cylindrical_docking_gets_both_its_travels_and_both_its_starts(tmp_path):
    """MJCF writes what URDF refuses - a hinge and a slide in one body - and the model states the two
    travels separately (`angular_range` the turn's, `range` the move's) with the docking stating both
    values. Each of the two must get its own, in its own unit."""
    joint = axis_joint("cylindrical", (0, 0, 10), (0, 0, 1), (0.0, 20.0),
                       angular_range=[0.0, 180.0])
    root = docked_with(tmp_path / "proj", joint, value=[7.0, 45.0])
    e = run_export(root, tmp_path / "robot", fmt="mjcf").ok()
    els = mjcf_joint_elements(e.mjcf)
    assert sorted(els) == ["pin.move", "pin.turn"], f"a cylindrical pair is two MJCF joints: {sorted(els)}"
    turn, move = els["pin.turn"], els["pin.move"]
    assert turn.get("type") == "hinge" and move.get("type") == "slide", f"{turn.get('type')}/{move.get('type')}"
    assert sorted(pair(turn.get("range"))) == pytest.approx([0.0, 180.0], abs=1e-9), \
        f"the turn's travel is its angular_range in degrees, got {turn.get('range')}"
    assert sorted(pair(move.get("range"))) == pytest.approx([0.0, 0.020], abs=1e-9), \
        f"the move's travel is its range in metres, got {move.get('range')}"
    assert float(turn.get("ref")) == pytest.approx(45.0, abs=1e-9), f"the docked angle: {turn.get('ref')}"
    assert float(move.get("ref")) == pytest.approx(0.007, abs=1e-9), f"the docked position: {move.get('ref')}"
    mj_load(e.mjcf)                                   # and MuJoCo takes it


def test_a_ball_docking_says_its_three_limits_have_no_mjcf_spelling(tmp_path):
    """An MJCF ball is bounded by one angle from its zero and the model states a range per turn, so the
    export writes no range and says why, instead of inventing the wrong cone."""
    root = docked_with(tmp_path / "proj", ball_joint(), value=None)
    e = run_export(root, tmp_path / "robot", fmt="mjcf").ok()
    els = mjcf_joint_elements(e.mjcf)
    assert [j.get("type") for j in els.values()] == ["ball"], f"a ball docking is one MJCF ball: {els}"
    assert els["pin"].get("range") is None, "an MJCF ball cannot carry a limit per turn"
    assert any("ball" in line.lower() and "one angle" in line.lower() for line in e.log.splitlines()), \
        f"the limits that could not be written must be said:\n{e.log[-1500:]}"
    mj_load(e.mjcf)


def docked_fourbar(root, mechanism=MECHANISM):
    """The four-bar as a DOCKED machine: the tree from its dockings, the loop from its mechanism.

    The same four bars at the same poses as `fourbar()`, but ground carries the crank's and the
    rocker's pins and the crank carries the coupler's, so the links come from the docking graph and
    are named after the placements - `crank`, `coupler`, `rocker` - which is what the mechanism calls
    its joints. Every docked axis is where the mechanism's own `at` puts it, so the two declarations
    describe one machine; `moved` moves one of them to make them two.
    """
    from build123d import Align, Box, Location, Pos, Rotation
    root = Path(root)
    up = Rotation(0, -90, 0)
    bar = lambda length, w: Box(length, w, w, align=(Align.MIN, Align.CENTER, Align.CENTER))
    pin = lambda origin: axis_joint("revolute", origin, (0, 1, 0), (-180.0, 180.0))
    common = {"process": "mill", "material": "S235JR", "card": {"density": 7.85}}
    write_model(root, [
        dict(common, id="ground", shape=Box(520, 60, 20),
             # ground sits at Pos(200, 0, -40), so its own coordinates are the machine's less that
             joints={"crank_pin": pin((-200, 0, 40)), "rocker_pin": pin((200, 0, 140))},
             placements=[("ground", Pos(200, 0, -40), None)]),
        dict(common, id="crank", shape=bar(300, 20), joints={"hub": rigid(), "coupler_pin": pin((300, 0, 0))},
             placements=[("crank", Location((0, 0, 0)) * up,
                          {"to": "ground", "at": "crank_pin", "by": "hub", "type": "revolute", "value": 0.0})]),
        dict(common, id="coupler", shape=bar(400, 16), joints={"end": rigid()},
             placements=[("coupler", Pos(0, 0, 300),
                          {"to": "crank", "at": "coupler_pin", "by": "end", "type": "revolute", "value": 0.0})]),
        dict(common, id="rocker", shape=bar(200, 16), joints={"hub": rigid()},
             placements=[("rocker", Location((400, 0, 100)) * up,
                          {"to": "ground", "at": "rocker_pin", "by": "hub", "type": "revolute", "value": 0.0})]),
    ])
    mech = root / "mechanics" / "kinematics" / "mechanism.py"
    mech.parent.mkdir(parents=True, exist_ok=True)
    mech.write_text(mechanism)
    return root, mech


def test_a_docked_machine_with_a_stated_loop_gets_its_loop_and_its_drive(tmp_path):
    """A machine on BOTH inputs - the tree from its dockings, the loop and the drive from its
    mechanism - which is the shape a converted machine with a loop has. Neither may go missing, and
    the equality must sit on the real pin, which is the one thing nothing downstream can check:
    MuJoCo works the second body's anchor out from the first at `qpos0`, so a wrong anchor reads as a
    perfectly closed loop."""
    root, mech = docked_fourbar(tmp_path / "proj")
    e = run_export(root, tmp_path / "robot", fmt="mjcf", mechanism=mech).ok()
    links = {ln.split()[2].strip("'") for ln in e.log.splitlines() if ln.startswith("[export_robot] link ")}
    assert {"base", "crank", "coupler", "rocker"} <= links, f"the dockings are the tree: {links}"
    m, d = mj_load(e.mjcf)
    assert m.neq == 1, f"the loop the mechanism states must be in the MJCF: neq {m.neq}"
    b1 = int(m.eq_obj1id[0])
    anchor = tuple(d.xpos[b1] + d.xmat[b1].reshape(3, 3) @ m.eq_data[0][:3])
    assert anchor == pytest.approx(tuple(x * MM for x in FOURBAR_MEET_MM), abs=1e-6), \
        f"the connect must sit on the pin the loop closes at, got {anchor} m"
    assert mjcf_elements(e.mjcf, "position") or mjcf_elements(e.mjcf, "motor"), \
        f"the drive the mechanism states is nowhere in the MJCF:\n{e.log[-2000:]}"


def test_two_statements_of_one_machine_that_disagree_are_refused(tmp_path):
    """The dockings say where a pin is and so does the mechanism. When they differ there is no machine
    to export: the equality would be written onto a point of neither part, and the loop would close
    around it in silence."""
    moved = MECHANISM.replace('m.turn("rocker", on="ground", at=(400.0, 100.0)',
                              'm.turn("rocker", on="ground", at=(400.0, 250.0)')
    assert moved != MECHANISM, "the fixture's own soundness: the rocker's pivot was moved"
    root, mech = docked_fourbar(tmp_path / "proj", mechanism=moved)
    e = run_export(root, tmp_path / "robot", fmt="mjcf", mechanism=mech).refused().wrote_nothing()
    e.names("rocker")
    assert any(s in e.log for s in ("150", "0.15")), \
        f"the refusal must say how far apart the two statements are:\n{e.log[-1500:]}"


def test_a_mechanism_joint_no_group_answers_for_is_refused(tmp_path):
    """A joint of the loop with no parts is a body MuJoCo cannot compile ('mass and inertia of moving
    bodies must be larger than mjMINVAL'), and the model does say which parts move with it - LINKS.
    So the missing entry is refused by name here, not by the engine later."""
    root, mech, groups = fourbar(tmp_path / "proj",
                                 mechanism=MECHANISM.replace('"coupler_g": "coupler", ', ""))
    e = run_export(root, tmp_path / "robot", fmt="mjcf", mechanism=mech, groups=groups)
    e.refused().wrote_nothing().names("coupler")


def test_a_loop_that_will_not_close_is_said_and_not_thrown(tmp_path):
    """Every other failure of this script is a line of its own; a mechanism the solver refuses must be
    one too, naming the file, instead of a traceback out of linkage.py."""
    short = MECHANISM.replace('m.meet(("coupler", (400.0, 0.0))', 'm.meet(("coupler", (40.0, 0.0))')
    assert short != MECHANISM, "the fixture's own soundness: the coupler was shortened"
    root, mech, groups = fourbar(tmp_path / "proj", mechanism=short)
    e = run_export(root, tmp_path / "robot", fmt="both", mechanism=mech, groups=groups).refused()
    assert "Traceback" not in e.log, f"the solver's refusal must be said, not thrown:\n{e.log[-1500:]}"
    assert "mechanism.py" in e.log, f"the refusal must name the file to open:\n{e.log[-1500:]}"


def test_mujoco_is_not_left_to_invent_the_mass_of_what_nobody_weighed(tmp_path):
    """MuJoCo's own default weighs a body with no <inertial> by the convex hull of its meshes at the
    density of WATER - 12 kg for a vendor cover nobody measured, which is PartCAD's `dummyMass` in
    another spelling. The file must tell it not to."""
    root = mesh_only(tmp_path / "proj", with_solid=True)
    e = run_export(root, tmp_path / "robot", fmt="both").ok()
    comp = mjcf_elements(e.mjcf, "compiler")
    assert comp and comp[0].get("inertiafromgeom") == "false", \
        f"<compiler inertiafromgeom=\"false\"> expected, got {comp and comp[0].attrib}"
    m, _ = mj_load(e.mjcf)
    # `mesh_only(with_solid=True)` is the unweighable cover plus a 20 mm cube at 2.0 g/cm3, so the
    # whole model weighs the cube and nothing else.
    total = float(sum(m.body_mass))
    assert total == pytest.approx(20.0 ** 3 * 2.0 * 1e-6, rel=1e-6), \
        f"only the part with a solid may weigh anything, got {total} kg for {list(m.body_mass)}"


def test_a_moving_link_nobody_can_weigh_is_refused_rather_than_written(tmp_path):
    """A body that moves and weighs nothing is not a body. MuJoCo says so opaquely and only at load;
    this must say which parts have no solid and what would fix it."""
    joint = axis_joint("revolute", (0, 0, 10), (0, 1, 0), (-90.0, 90.0))
    from build123d import Box, Location, Pos
    root = write_model(tmp_path / "proj", [
        {"id": "plate", "process": "mill", "material": "S235JR", "card": {"density": 7.85},
         "shape": Box(100, 100, 10), "joints": {"seat": joint},
         "placements": [("plate", Location(), None)]},
        {"id": "kozhuh", "process": "other", "material": "S235JR", "mesh": box_mesh(0, 0, 10, 40, 40, 60),
         "placements": [("kozhuh", Pos(0, 0, 0),
                         {"to": "plate", "at": "seat", "by": "end", "type": "revolute", "value": 0.0})]},
    ])
    e = run_export(root, tmp_path / "robot", fmt="mjcf").refused().wrote_nothing()
    e.names("kozhuh")


def test_an_off_diagonal_inertia_survives_the_placement(tmp_path):
    """`R T R^T`: a bar turned 30 deg about Y has a non-zero ixz, and a transpose or a sign slip there is
    invisible in every axis-aligned case. Checked against the closed form."""
    from build123d import Align, Box, Rotation
    length, side, rho, degrees = 200.0, 20.0, 7.85, 30.0
    root = write_model(tmp_path / "proj", [
        {"id": "bar", "process": "turn", "material": "steel", "card": {"density": rho},
         "shape": Box(length, side, side, align=(Align.CENTER,) * 3),
         "placements": [("bar", Rotation(0, -degrees, 0), None)]},
    ])
    e = run_export(root, tmp_path / "robot", fmt="urdf").ok()
    el = urdf_of(e.urdf).find("link/inertial/inertia")
    mass = length * side * side * rho * 1e-6
    own = {"ixx": mass * (side * side + side * side) / 12.0 * MM * MM,
           "iyy": mass * (length * length + side * side) / 12.0 * MM * MM,
           "izz": mass * (length * length + side * side) / 12.0 * MM * MM}
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    # a turn of -degrees about Y, as Rotation(0, -degrees, 0) is: R T R^T on a diagonal tensor
    want = {"ixx": own["ixx"] * c * c + own["izz"] * s * s,
            "izz": own["ixx"] * s * s + own["izz"] * c * c,
            "iyy": own["iyy"],
            "ixz": (own["ixx"] - own["izz"]) * c * s, "ixy": 0.0, "iyz": 0.0}
    for key, value in want.items():
        assert float(el.get(key)) == pytest.approx(value, rel=1e-9, abs=1e-15), \
            f"{key}: {el.get(key)} against {value}"
    assert abs(float(el.get("ixz"))) > 1e-5, "this case is here for its off-diagonal term"


def test_the_density_of_every_part_is_named_with_where_it_came_from(tmp_path):
    """Done-when 3: not only the fallback. A reader must be able to tell a card density from a catalogue
    guess, because the guess is a substring match on a material name."""
    from build123d import Box, Pos
    root = write_model(tmp_path / "proj", [
        {"id": "weighed", "process": "mill", "material": "zzz-unobtanium", "card": {"density": 3.33},
         "shape": Box(10, 10, 10), "placements": [("weighed", Pos(0, 0, 0), None)]},
        {"id": "guessed", "process": "mill", "material": "S235JR steel sheet",
         "shape": Box(10, 10, 10), "placements": [("guessed", Pos(40, 0, 0), None)]},
        {"id": "unknown", "process": "mill", "material": "zzz-unobtanium",
         "shape": Box(10, 10, 10), "placements": [("unknown", Pos(80, 0, 0), None)]},
    ])
    e = run_export(root, tmp_path / "robot", fmt="urdf").ok()
    said = [ln for ln in e.log.splitlines() if "density" in ln]
    assert any("weighed" in ln and "stated" in ln for ln in said), f"a card density is 'stated': {said}"
    assert any("guessed" in ln and "catalogue" in ln and "7.85" in ln for ln in said), \
        f"a catalogue guess names the catalogue and its number: {said}"
    assert any("unknown" in ln and "DEFAULT" in ln for ln in said), f"the fallback is named: {said}"
