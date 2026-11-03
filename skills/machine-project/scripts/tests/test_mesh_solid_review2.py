"""Issue #78 «mesh parts», second review round: behaviours still uncovered by test_mesh_solid*.py.

  S1  print_rot about two axes ([90, 0, 90], [30, 45, 60]): the mesh part's print STL equals the one derive.py's STEP path
      makes of its B-rep twin (same box, same vertices: same extents and orientation)
  S2  countersunk and counterbored seats in a mesh host, each against its STEP twin: a 90 deg countersink over a 4.2 tapped
      hole (M5: passes), the same over a 4.6 hole (FAIL, hole_d about 4.6), a counterbore over a 5.2 hole (passes) and over a
      4.6 hole (FAIL, hole_d about 4.6, not the counterbore's 9)
  S3  a screw joint 0.03 mm off the axis of a mesh hole: the hole is found, the verdict is the STEP twin's
  S4  assembly drawings with a CURVED mesh part (SDF ellipsoid) next to STEP parts: derive.py assembly succeeds; hlr over every
      view, three times over, draws the mesh part (non-empty segments); a STEP block behind it loses the lines it hides
  S5  a Mesh returned with process "laser" or "bent" fails the build naming the part ("print" builds: the control)
  S6  Mesh.bounding_box() is the triangles' box where the shape's location puts them; Mesh.vertices() still works

One project (node `frame`, base = STEP Box 300 x 150 x 10 at the origin, every part docked on a seat of its top face):
    mplate / splate  the same plate 130 x 40 x 10 as Mesh / STEP, at (10, 10) / (150, 10); own-coordinate holes, joints Z up:
        cs_42   (15, 20)  4.2 through, 90 deg countersink Ø11 at the top          joint on the top face   M5x8 DIN 912
        cs_46   (40, 20)  4.6 through, the same countersink                        joint on the top face   M5x8
        cb_52   (65, 20)  5.2 through, counterbore Ø9 x 5 deep                     joint on the cb floor   M5x5
        cb_46   (90, 20)  4.6 through, counterbore Ø9 x 5 deep                     joint on the cb floor   M5x5
        off_46  (115, 20) 4.6 through                          joint at (115.03, 20, 10), 0.03 off axis    M5x8
    prm_m / prm_s    L-block (Box 10 x 20 x 30 less a 5 x 10 x 15 corner) Mesh / B-rep, print_rot [90, 0, 90]
    prr_m / prr_s    the same L-block, print_rot [30, 45, 60]
    egg              SDF ellipsoid 15 x 15 x 12 (edge 1.5), mesh, at (230, 100); blk_a / blk_b STEP Box 20 x 20 x 30 at
                     (208, 122) / (252, 78): one of them is behind the egg in the first isometric view, the other in front

    python -m pytest skills/machine-project/scripts/tests/test_mesh_solid_review2.py -n 8 -q -W ignore     (from agent/)
    (S4 needs SHOP_DRAW_VENV, default /tmp/shopdrawvenv)
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest
import trimesh

import helpers as H
import meshsolid_proj as MP
from meshsolid_proj import load_mesh, placement_map

VENV = Path(os.environ.get("SHOP_DRAW_VENV", "/tmp/shopdrawvenv"))
SHOP = H.SCRIPTS.parent.parent / "shop-drawings" / "scripts"

PLATE_HELPER = '''
from build123d import Align, Box, Cone, Cylinder, Pos

T = 10.0
UP = (Align.CENTER, Align.CENTER, Align.MIN)
CSK = {"cs_42": (15, 4.2), "cs_46": (40, 4.6)}
CB = {"cb_52": (65, 5.2), "cb_46": (90, 4.6)}
JOINTS = {"cs_42": (15, 20, T), "cs_46": (40, 20, T), "cb_52": (65, 20, T - 5), "cb_46": (90, 20, T - 5),
          "off_46": (115.03, 20, T), "bottom": (0, 0, 0)}


def solid():
    s = Box(130, 40, T, align=(Align.MIN, Align.MIN, Align.MIN))
    for x, d in CSK.values():
        s -= Pos(x, 20, -1) * Cylinder(d / 2, T + 2, align=UP)
        s -= Pos(x, 20, T - (11 - d) / 2) * Cone(d / 2, 11 / 2 + 1, (11 - d) / 2 + 1, align=UP)   # 90 deg, 1 mm past the top
    for x, d in CB.values():
        s -= Pos(x, 20, -1) * Cylinder(d / 2, T + 2, align=UP)
        s -= Pos(x, 20, T - 5) * Cylinder(4.5, 6, align=UP)                                # Ø9 x 5 counterbore
    s -= Pos(115, 20, -1) * Cylinder(2.3, T + 2, align=UP)
    return s
'''

PLATE = '''
import meshpart
from build123d import Location, RigidJoint
from geom import P
from . import _plate


def build():
    s = _plate.solid()
    h = {wrap}
    for label, xyz in _plate.JOINTS.items():
        RigidJoint(label, h, Location(xyz))
    return P("{name}", h, "#cccccc", process="{process}", material="PETG")
'''

LBLOCK = '''
import meshpart
from build123d import Align, Box, Location, Pos, RigidJoint
from geom import P

MN = (Align.MIN, Align.MIN, Align.MIN)


def build():
    s = Box(10, 20, 30, align=MN) - Pos(5, 10, 15) * Box(5, 10, 15, align=MN)
    m = {wrap}
    RigidJoint("bottom", m, Location((0, 0, 0)))
    return P("{name}", m, "#dd8800", process="print", material="PETG", print_rot={rot})
'''

EGG = '''
import math
import meshpart as mp
from build123d import Location, RigidJoint
from geom import P


def blob(a, b, c):
    def f(x, y, z):
        return (math.sqrt((x / a) ** 2 + (y / b) ** 2 + (z / c) ** 2) - 1.0) * min(a, b, c)
    return f


def build():
    m = mp.Mesh(mp.sdf(blob(15.0, 15.0, 12.0), (-17.3, -17.3, -14.3, 17.3, 17.3, 14.3), edge=1.5))
    RigidJoint("bottom", m, Location((0, 0, -12)))
    return P("egg", m, "#cc3333", process="print", material="PETG")
'''

BLOCK = '''
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    s = Box(20, 20, 30, align=(Align.CENTER, Align.CENTER, Align.MIN))
    RigidJoint("bottom", s, Location((0, 0, 0)))
    return P("blk", s, "#4444aa", process="mill", material="AL6060")
'''

BASE = '''
from build123d import Align, Box, Location, RigidJoint
from geom import P

SEATS = {"mplate": (10, 10), "splate": (150, 10), "prm_m": (20, 90), "prm_s": (60, 90), "prr_m": (100, 90),
         "prr_s": (140, 90), "egg": (230, 100), "blk_a": (208, 122), "blk_b": (252, 78)}


def build():
    s = Box(300, 150, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    for name, (x, y) in SEATS.items():
        RigidJoint(name + "_seat", s, Location((x, y, 10)))
    return P("base", s, "#888888", process="mill", material="S235JR")
'''

FRAME = '''
from build123d import Pos
from geom import place
from . import base, blk, egg, mplate, prm_m, prm_s, prr_m, prr_s, screw, splate

SCREWS = [("cs_42", 8), ("cs_46", 8), ("cb_52", 5), ("cb_46", 5), ("off_46", 8)]


def build():
    b = base.build()
    out = [place(b, Pos(0, 0, 0))]

    def dock(host, at, part, by="bottom", name=None):
        host["shape"].joints[at].connect_to(part["shape"].joints[by])
        out.append(place(part, name=name))
        return part

    for tag, plate in (("m", mplate), ("s", splate)):
        p = dock(b, ("mplate" if tag == "m" else "splate") + "_seat", plate.build())
        for hole, length in SCREWS:
            dock(p, hole, screw.build("M5", length, "DIN 912"), by="head", name=f"scr_{tag}_{hole}")
    for mod, name in ((prm_m, "prm_m"), (prm_s, "prm_s"), (prr_m, "prr_m"), (prr_s, "prr_s"), (egg, "egg")):
        dock(b, name + "_seat", mod.build())
    dock(b, "blk_a_seat", blk.build(), name="blk_a")
    dock(b, "blk_b_seat", blk.build(), name="blk_b")
    return out
'''


def _twin(src, name, mesh, **kw):
    out = src.replace("{wrap}", "meshpart.Mesh(meshpart.solid(s))" if mesh else "s").replace("{name}", name)
    out = out.replace("{process}", "print" if mesh else "mill")
    for k, v in kw.items():
        out = out.replace("{" + k + "}", v)
    return out


FILES = {"frame/__init__.py": FRAME, "frame/base.py": BASE, "frame/_plate.py": PLATE_HELPER, "frame/egg.py": EGG,
         "frame/blk.py": BLOCK,
         "frame/mplate.py": _twin(PLATE, "mplate", True), "frame/splate.py": _twin(PLATE, "splate", False),
         "frame/prm_m.py": _twin(LBLOCK, "prm_m", True, rot="[90, 0, 90]"),
         "frame/prm_s.py": _twin(LBLOCK, "prm_s", False, rot="[90, 0, 90]"),
         "frame/prr_m.py": _twin(LBLOCK, "prr_m", True, rot="[30, 45, 60]"),
         "frame/prr_s.py": _twin(LBLOCK, "prr_s", False, rot="[30, 45, 60]")}


def _build(shared):
    root = MP.write_project(shared / "proj", FILES, screw=True)
    r = H.run_model(root)
    (shared / "cold.json").write_text(json.dumps({"rc": r.rc, "log": r.log}))


@pytest.fixture(scope="module")
def proj(tmp_path_factory):
    """(private copy of the once-per-run build or None, build log)."""
    import conftest
    shared = conftest._shared(tmp_path_factory, "meshsolid_review2", _build)
    cold = json.loads((shared / "cold.json").read_text())
    if cold["rc"] != 0:
        return None, cold["log"]
    return conftest._clean_copy(shared / "proj", tmp_path_factory.mktemp("mr2") / "proj"), cold["log"]


def _ok(proj):
    root, log = proj
    assert root is not None, f"the --model build of the review-2 project failed:\n{log[-2500:]}"
    return root


# ================================================================ S1: print_rot about two axes, mesh vs B-rep twin
@pytest.fixture(scope="module")
def printed(proj):
    root = _ok(proj)
    return root, H.run_derive(root, "print", manifest=root / "build/kit/print_r2.json")


@pytest.mark.parametrize("pair", [("prm_m", "prm_s"), ("prr_m", "prr_s")], ids=["90_0_90", "30_45_60"])
def test_s1_print_rot_about_two_axes_gives_the_brep_twins_stl(printed, pair):
    from scipy.spatial import cKDTree
    root, d = printed
    d.ok()
    stl = {}
    for pid in pair:
        files = [f for f in H.file_list(H.entry(d, pid), root) if f.suffix.lower() == ".stl"]
        assert len(files) == 1, f"{pid}: one STL expected: {files}"
        stl[pid] = trimesh.load(str(files[0]), force="mesh")
    m, s = stl[pair[0]], stl[pair[1]]
    np.testing.assert_allclose(m.bounds, s.bounds, atol=0.01, err_msg=f"{pair}: STL boxes differ (extents / placement on the bed)")
    assert abs(m.volume) == pytest.approx(abs(s.volume), rel=0.005)
    # same orientation: every corner of the B-rep twin's STL is a vertex of the mesh twin's, and back
    dm, _ = cKDTree(np.asarray(m.vertices)).query(np.unique(np.round(s.vertices, 4), axis=0))
    ds, _ = cKDTree(np.asarray(s.vertices)).query(np.unique(np.round(m.vertices, 4), axis=0))
    assert max(dm.max(), ds.max()) < 0.01, f"{pair}: the two STLs are turned differently (max vertex gap {max(dm.max(), ds.max()):.3f} mm)"


# ================================================================ S2 + S3: seats under a countersink / counterbore, off-axis joint
@pytest.fixture(scope="module")
def fit(proj):
    root = _ok(proj)
    if str(H.SCRIPTS) not in sys.path:
        sys.path.insert(0, str(H.SCRIPTS))
    import fit_checks
    return fit_checks.fit_report(root)


def _items(rep, name):
    return [it for it in rep["fastener"] + rep["clash"] if name in it["parts"]]


SEATS = [("cs_42", None), ("cs_46", 4.6), ("cb_52", None), ("cb_46", 4.6), ("off_46", 4.6)]


@pytest.mark.parametrize("hole,want_d", SEATS, ids=[h for h, _ in SEATS])
@pytest.mark.parametrize("tag", ["m", "s"])
def test_s2_s3_seat_verdict_of_a_screw(fit, tag, hole, want_d):
    """None: nothing reported (a thread under a countersink, a clearance hole under a counterbore). A number: one FAIL whose
    hole_d is that diameter (the narrowest hole the shaft reaches, not the countersink or counterbore over it; found 0.03 mm
    off the joint's axis too)."""
    got = _items(fit, f"scr_{tag}_{hole}")
    if want_d is None:
        assert got == [], f"scr_{tag}_{hole} must pass: {got}"
    else:
        fails = [it for it in got if it["level"] == "FAIL"]
        assert fails and fails[0].get("hole_d") == pytest.approx(want_d, abs=0.05), f"scr_{tag}_{hole}: {got}"
        assert [it for it in got if it["level"] == "WARN"] == [], f"scr_{tag}_{hole}: no WARN expected: {got}"


@pytest.mark.parametrize("hole", [h for h, _ in SEATS])
def test_s2_s3_mesh_host_gets_the_step_twins_verdict(fit, hole):
    def verdict(tag):
        return sorted((it["level"], round(it.get("hole_d", 0) or 0, 1)) for it in _items(fit, f"scr_{tag}_{hole}"))
    assert verdict("m") == verdict("s"), f"{hole}: mesh {_items(fit, f'scr_m_{hole}')} vs STEP {_items(fit, f'scr_s_{hole}')}"


# ================================================================ S4: assembly drawings with a curved mesh part
needs_venv = pytest.mark.skipif(not (VENV / "bin" / "python").exists(), reason=f"drawings venv not available at {VENV}")

HLR_DRIVER = '''
import json, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, sys.argv[1])
import assembly_drawing as A

items = json.loads(Path(sys.argv[2]).read_text())
shapes = {k: A.load(it) for k, it in items.items()}
out = []
for rep in range(3):
    for vi, look in enumerate(A.VIEWS):
        row = {}
        for keys in (("egg", "blk_a", "blk_b"), ("blk_a", "blk_b")):
            segs = A.hlr({k: shapes[k] for k in keys}, look)
            row["+".join(keys)] = {k: float(sum(np.linalg.norm(np.asarray(q, float) - np.asarray(p, float)) for p, q in v))
                                   for k, v in segs.items()}
        out.append({"rep": rep, "view": vi, "len": row})
print("HLR_JSON " + json.dumps(out))
'''


def _drawing_items(root):
    pm = placement_map(root)
    items = {}
    for name in ("egg", "blk_a", "blk_b"):
        p, pl = pm[name]
        it = dict(p, loc=pl["loc"], name=name)
        for k in ("mesh", "step"):
            if p.get(k):
                it[k] = str(root / p[k])
        items[name] = it
    return items


@needs_venv
def test_s4_hlr_draws_the_curved_mesh_part_and_hides_the_block_behind_it(proj, tmp_path):
    root = _ok(proj)
    (tmp_path / "items.json").write_text(json.dumps(_drawing_items(root)))
    (tmp_path / "drv.py").write_text(HLR_DRIVER)
    cp = subprocess.run([str(VENV / "bin" / "python"), str(tmp_path / "drv.py"), str(SHOP), str(tmp_path / "items.json")],
                        capture_output=True, text=True, timeout=900, env=dict(os.environ, MPLCONFIGDIR=str(tmp_path / "mpl")))
    line = [l for l in cp.stdout.splitlines() if l.startswith("HLR_JSON ")]
    assert cp.returncode == 0 and line, f"hlr over 4 views x 3 runs must succeed (rc={cp.returncode}):\n{(cp.stdout + cp.stderr)[-2500:]}"
    runs = json.loads(line[0][len("HLR_JSON "):])
    assert len(runs) == 12
    for r in runs:
        assert r["len"]["egg+blk_a+blk_b"].get("egg", 0) > 50, f"the egg must be drawn (outline): view {r['view']} run {r['rep']}: {r['len']}"
    first = [r for r in runs if r["view"] == 0]
    assert all(r["len"] == first[0]["len"] for r in first), f"hlr must give the same lines on every run: {first}"
    with_egg, alone = first[0]["len"]["egg+blk_a+blk_b"], first[0]["len"]["blk_a+blk_b"]
    loss = {k: 1 - with_egg[k] / alone[k] for k in ("blk_a", "blk_b")}
    hidden, front = max(loss, key=loss.get), min(loss, key=loss.get)
    assert loss[hidden] > 0.05, f"the block behind the egg must lose the lines the egg hides (view 0): loss {loss}"
    assert abs(loss[front]) < 0.01, f"the block in front of the egg must keep its lines: loss {loss}"


@needs_venv
def test_s4_derive_assembly_draws_the_node_with_the_curved_mesh_part(proj, tmp_path):
    import conftest
    from test_assembly_drawings import run_assembly
    root = conftest._clean_copy(_ok(proj), tmp_path / "proj")
    import shutil
    for i in range(3):
        shutil.rmtree(root / "build/derived", ignore_errors=True)      # nothing cached: every run draws the node again
        r = run_assembly(root).ok()
        assert r.counts[2] >= 1, f"run {i + 1} must draw the node, not reuse it: {r.counts}"
        pdf = r.pdf("frame")
        assert pdf.is_file() and pdf.stat().st_size > 0, f"run {i + 1}: no PDF for node frame"


# ================================================================ S5: a Mesh as laser / bent fails the build
FLAT = '''
import meshpart
from build123d import Box
from geom import P

NAME = "flat_mesh"
PROC = "{proc}"


def build():
    m = meshpart.Mesh(meshpart.solid(Box(40, 20, 2)))
    return P(NAME, m, "#aaaaaa", process=PROC, material="S235JR", thickness=2.0)
'''
FLAT_NODE = '''
from build123d import Pos
from geom import place
from . import flat_mesh


def build():
    return [place(flat_mesh.build(), Pos(0, 0, 0))]
'''


@pytest.mark.parametrize("proc", ["laser", "bent", "print"])
def test_s5_a_mesh_part_cannot_be_laser_or_bent(tmp_path, proc):
    import re
    root = MP.write_project(tmp_path / "p", {"sheet/__init__.py": FLAT_NODE, "sheet/flat_mesh.py": FLAT.replace("{proc}", proc)},
                            assemble="from parts import sheet\nout = sheet.build()\n")
    r = H.run_model(root)
    if proc == "print":
        r.ok()                                   # the control: the same Mesh as a print part builds
        return
    assert r.rc != 0, f"a mesh part with process {proc!r} must fail the build, it exited 0:\n{r.log[-1500:]}"
    said = [ln for ln in r.log.splitlines() if "flat_mesh" in ln and re.search(proc, ln)]
    assert said, f"the build must name the part 'flat_mesh' and its process {proc!r}; log:\n{r.log[-2000:]}"


# ================================================================ S6: Mesh.bounding_box / vertices
def _meshpart():
    if str(H.SCRIPTS) not in sys.path:
        sys.path.insert(0, str(H.SCRIPTS))
    import meshpart
    return meshpart


def _bb(b):
    return np.array([[b.min.X, b.min.Y, b.min.Z], [b.max.X, b.max.Y, b.max.Z]])


def test_s6_bounding_box_is_the_triangles_box_where_the_location_puts_them():
    mp = _meshpart()
    from build123d import Align, Box, Pos, Rot
    m = mp.Mesh(mp.solid(Box(10, 20, 30, align=(Align.MIN, Align.MIN, Align.MIN))))
    np.testing.assert_allclose(_bb(m.bounding_box()), [[0, 0, 0], [10, 20, 30]], atol=1e-3)
    np.testing.assert_allclose(_bb((Pos(5, 0, 50) * m).bounding_box()), [[5, 0, 50], [15, 20, 80]], atol=1e-3)
    np.testing.assert_allclose(_bb((Rot(0, 0, 90) * m).bounding_box()), [[-20, 0, 0], [0, 10, 30]], atol=1e-3)


def test_s6_vertices_still_works_as_build123ds_method():
    mp = _meshpart()
    from build123d import Box
    m = mp.Mesh(mp.solid(Box(10, 10, 10)))
    vs = m.vertices()                             # a TypeError here means the build123d method was shadowed
    assert hasattr(vs, "__len__"), type(vs)
