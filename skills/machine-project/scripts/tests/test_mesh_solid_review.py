"""Issue #78 «mesh parts», four behaviours an independent review found uncovered by test_mesh_solid*.py.

  R1  a part file that MOVES or TURNS its Mesh (`Pos(0, 0, 50) * m`, `Rot(0, 90, 0) * m`) before declaring joints: the stored
      triangles, the joints and the placements agree with a B-rep part built by the same code (export_step bakes the
      location into a STEP part; the mesh's triangles must stand where that same location puts them)
  R2  a screw docked into a boss inside a SMALL closed housing (Ø24 cylinder, 2 mm walls, Ø6 boss on the axis, Ø1.7 hole
      6 deep): the same fit verdicts as its STEP twin (M2x6: nothing reported; M2x8: FAIL bottoming out; no spurious WARN)
  R3  a real --model build of a part whose field goes through meshpart.sdf with a closure over params values (as the SKILL's
      «Smooth housings» example): it builds, and a param read only to be passed into the field (used nowhere else in build())
      rebuilds the part and changes its triangles
  R4  a print mesh part with no real flat face (an ellipsoid from meshpart.sdf): derive.py print keeps it as it stands at its
      first placement (no tilt), only moved so its box starts at the origin

Project A (R1, R3, R4), node `frame`, base = STEP Box 300 x 100 x 10 at the origin (the root), seats on its top face:
    mv_m / mv_s   the SAME code, Mesh vs B-rep: Box(10, 20, 30) (min corner at 0) moved by Pos(0, 0, 50), joint `bottom`
                  declared after the move at Location((5, 10, 50)); docked on mv_m_seat (20, 20) / mv_s_seat (120, 20)
    tn_m / tn_s   the same box turned by Rot(0, 90, 0), joint `bottom` at Location((15, 10, -10)); seats (20, 70) / (120, 70)
    ball          ellipsoid semi-axes BALL_A x BALL_B x BALL_C = 12 x 8 x 5 from meshpart.sdf (edge 1.0, scalar math field as
                  in the SKILL); BALL_C is read in build() only to be passed into the field; docked on ball_seat (220, 50)
                  turned 90 deg about Z; print, PETG
Project B (R2): base STEP Box 200 x 40 x 10; housing hm (Mesh) / hs (STEP, same code) placed twice each on the base:
    hm_ok + M2x6, hm_deep + M2x8, hs_ok + M2x6, hs_deep + M2x8 (templates/screw.py docked by `head` on the boss joint)

    python -m pytest skills/machine-project/scripts/tests/test_mesh_solid_review.py -n 8 -q -W ignore     (from agent/)
"""
import json
import sys

import numpy as np
import pytest
import trimesh

import helpers as H
import meshsolid_proj as MP
from meshsolid_proj import load_mesh, trimesh_of, placement_map

# ================================================================ project A
PARAMS_A = "BALL_A = 12.0\nBALL_B = 8.0\nBALL_C = 5.0\nUNUSED = 1.0\n"

BASE_A = '''
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    s = Box(300, 100, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    for name, (x, y) in {"mv_m": (20, 20), "mv_s": (120, 20), "tn_m": (20, 70), "tn_s": (120, 70)}.items():
        RigidJoint(name + "_seat", s, Location((x, y, 10)))
    RigidJoint("ball_seat", s, Location((220, 50, 10), (0, 0, 90)))
    return P("base", s, "#888888", process="mill", material="S235JR")
'''

# {wrap} is `meshpart.Mesh(meshpart.solid(SHAPE))` for the mesh twin and `SHAPE` for the B-rep twin; everything else identical
MOVED = '''
import meshpart
from build123d import Align, Box, Location, Pos, RigidJoint
from geom import P


def build():
    s = Box(10, 20, 30, align=(Align.MIN, Align.MIN, Align.MIN))
    m = {wrap}
    m = Pos(0, 0, 50) * m
    RigidJoint("bottom", m, Location((5, 10, 50)))
    return P("{name}", m, "#dddddd", process="{process}", material="PETG")
'''

TURNED = '''
import meshpart
from build123d import Align, Box, Location, Rot, RigidJoint
from geom import P


def build():
    s = Box(10, 20, 30, align=(Align.MIN, Align.MIN, Align.MIN))
    m = {wrap}
    m = Rot(0, 90, 0) * m
    RigidJoint("bottom", m, Location((15, 10, -10)))
    return P("{name}", m, "#dddddd", process="{process}", material="PETG")
'''

BALL = '''
import math
import params
import meshpart as mp
from build123d import Location, RigidJoint
from geom import P


def blob(a, b, c):
    """Roughly the signed distance of an a x b x c ellipsoid (negative inside); numbers come in as arguments."""
    def f(x, y, z):
        return (math.sqrt((x / a) ** 2 + (y / b) ** 2 + (z / c) ** 2) - 1.0) * min(a, b, c)
    return f


def build():
    skin = blob(params.BALL_A, params.BALL_B, params.BALL_C)
    body = mp.sdf(skin, (-14.3, -10.3, -8.3, 14.3, 10.3, 8.3), edge=1.0)
    m = mp.Mesh(body)
    RigidJoint("bottom", m, Location((0, 0, -8)))
    return P("ball", m, "#cc3333", process="print", material="PETG")
'''

FRAME_A = '''
from build123d import Pos
from geom import place
from . import ball, base, mv_m, mv_s, tn_m, tn_s


def build():
    b = base.build()
    out = [place(b, Pos(0, 0, 0))]
    for mod, name in ((mv_m, "mv_m"), (mv_s, "mv_s"), (tn_m, "tn_m"), (tn_s, "tn_s"), (ball, "ball")):
        p = mod.build()
        b["shape"].joints[name + "_seat"].connect_to(p["shape"].joints["bottom"])
        out.append(place(p))
    return out
'''


def _twin(src, name, mesh):
    wrap = "meshpart.Mesh(meshpart.solid(s))" if mesh else "s"
    return src.replace("{wrap}", wrap).replace("{name}", name).replace("{process}", "print" if mesh else "mill")


FILES_A = {"frame/__init__.py": FRAME_A, "frame/base.py": BASE_A, "frame/ball.py": BALL,
           "frame/mv_m.py": _twin(MOVED, "mv_m", True), "frame/mv_s.py": _twin(MOVED, "mv_s", False),
           "frame/tn_m.py": _twin(TURNED, "tn_m", True), "frame/tn_s.py": _twin(TURNED, "tn_s", False)}
SEAT_DX = 100.0          # the B-rep twin's seat is 100 mm further along X than the mesh twin's


def _build_a(shared):
    root = MP.write_project(shared / "proj", FILES_A, params=PARAMS_A)
    r = H.run_model(root)
    (shared / "cold.json").write_text(json.dumps({"rc": r.rc, "log": r.log}))


@pytest.fixture(scope="module")
def proj_a(tmp_path_factory):
    import conftest
    shared = conftest._shared(tmp_path_factory, "meshsolid_review_a", _build_a)
    return shared / "proj", json.loads((shared / "cold.json").read_text())


def _a_ok(proj_a):
    root, cold = proj_a
    assert cold["rc"] == 0 and (root / "build/model/parts.json").is_file(), \
        f"the --model build of review project A failed (rc={cold['rc']}):\n{cold['log'][-2500:]}"
    return root


def _step_vertices(root, entry):
    """Every vertex of the stored STEP of a B-rep part (its own coordinates, location baked in)."""
    from build123d import import_step
    s = import_step(str(root / entry["step"]))
    return np.array([tuple(v) for v in s.vertices()], dtype=float)


def _bbox(v):
    return np.array([v.min(0), v.max(0)])


def _world(v, loc):
    M = MP.matrix(loc)
    return v @ M[:3, :3].T + M[:3, 3]


# ---------------------------------------------------------------- R1: a moved / turned Mesh agrees with its B-rep twin
@pytest.mark.parametrize("pair", [("mv_m", "mv_s"), ("tn_m", "tn_s")], ids=["moved", "turned"])
def test_r1_stored_triangles_of_a_moved_mesh_match_its_brep_twin(proj_a, pair):
    root = _a_ok(proj_a)
    m, s = (H.by_id(root)[pid] for pid in pair)
    assert m.get("mesh_solid") is True and "step" not in m, m
    v, f = load_mesh(root, m)
    np.testing.assert_allclose(_bbox(v), _bbox(_step_vertices(root, s)), atol=1e-3,
                               err_msg=f"{pair[0]}: stored triangles must stand where the location puts them, as the STEP of {pair[1]}")
    assert abs(trimesh_of(v, f).volume) == pytest.approx(6000.0, rel=1e-3)


@pytest.mark.parametrize("pair", [("mv_m", "mv_s"), ("tn_m", "tn_s")], ids=["moved", "turned"])
def test_r1_joints_of_a_moved_mesh_match_its_brep_twin(proj_a, pair):
    root = _a_ok(proj_a)
    m, s = (H.by_id(root)[pid] for pid in pair)
    jm, js = m.get("joints") or {}, s.get("joints") or {}
    assert set(jm) == set(js) == {"bottom"}, (jm, js)
    assert jm["bottom"]["type"] == js["bottom"]["type"]
    np.testing.assert_allclose(MP.matrix(jm["bottom"]["loc"]), MP.matrix(js["bottom"]["loc"]), atol=1e-6, err_msg=pair[0])


@pytest.mark.parametrize("pair", [("mv_m", "mv_s"), ("tn_m", "tn_s")], ids=["moved", "turned"])
def test_r1_a_moved_mesh_stands_where_its_brep_twin_stands(proj_a, pair):
    """Docked the same way on seats SEAT_DX apart: the world boxes differ by exactly that shift."""
    root = _a_ok(proj_a)
    pm = placement_map(root)
    (m, plm), (s, pls) = pm[pair[0]], pm[pair[1]]
    shift = np.eye(4)
    shift[0, 3] = SEAT_DX
    np.testing.assert_allclose(shift @ MP.matrix(plm["loc"]), MP.matrix(pls["loc"]), atol=1e-6, err_msg="placement locs")
    v, _ = load_mesh(root, m)
    wm = _bbox(_world(v, plm["loc"]))
    ws = _bbox(_world(_step_vertices(root, s), pls["loc"]))
    np.testing.assert_allclose(wm + [SEAT_DX, 0, 0], ws, atol=1e-3, err_msg=f"{pair[0]} world box vs its twin")
    assert wm[0][2] == pytest.approx(10.0, abs=1e-3), f"{pair[0]} must stand on the base (z = 10): {wm}"


# ---------------------------------------------------------------- R3: sdf with a closure over params, rebuilt by a param change
def test_r3_a_part_whose_field_closes_over_params_builds(proj_a):
    root = _a_ok(proj_a)
    p = H.by_id(root)["ball"]
    v, f = load_mesh(root, p)
    tm = trimesh_of(v, f)
    assert tm.is_watertight
    ext = v.max(0) - v.min(0)
    np.testing.assert_allclose(ext, [24, 16, 10], atol=1.5)       # a coarse field: within about one edge


def test_r3_a_param_read_only_for_the_field_rebuilds_the_part_and_changes_its_triangles(proj_a, tmp_path):
    import conftest
    root = conftest._clean_copy(_a_ok(proj_a), tmp_path / "proj")
    old = H.by_id(root)["ball"]
    sha = H.sha_file(root / old["mesh"])
    z0 = np.ptp(load_mesh(root, old)[0][:, 2])
    H.edit(H.cad_dir(root) / "params.py", "BALL_C = 5.0", "BALL_C = 7.0")
    r = H.run_model(root).ok()
    assert "frame.ball.build" in r.rebuilt, f"ball's field reads BALL_C: the part must rebuild, rebuilt {r.rebuilt}"
    new = H.by_id(root)["ball"]
    assert H.sha_file(root / new["mesh"]) != sha, "the stored triangles must change"
    z1 = np.ptp(load_mesh(root, new)[0][:, 2])
    assert z1 > z0 + 2.0, f"BALL_C 5 -> 7 makes the ellipsoid 4 mm taller: z extent {z0:.2f} -> {z1:.2f}"


# ---------------------------------------------------------------- R4: no flat face -> printed as it stands
def test_r4_a_mesh_part_with_no_flat_face_is_printed_as_it_stands_at_its_first_placement(proj_a, tmp_path):
    import conftest
    from scipy.spatial import cKDTree
    root = conftest._clean_copy(_a_ok(proj_a), tmp_path / "proj")
    d = H.run_derive(root, "print").ok()
    stls = [f for f in H.file_list(H.entry(d, "ball"), root) if f.suffix.lower() == ".stl"]
    assert len(stls) == 1, stls
    stl = trimesh.load(str(stls[0]), force="mesh")
    p, pl = placement_map(root)["ball"]
    v, _ = load_mesh(root, p)
    w = _world(v, pl["loc"])
    want = w - w.min(0)                                          # turned 90 deg about Z by its docking, box at the origin
    np.testing.assert_allclose(stl.bounds, [[0, 0, 0], want.max(0)], atol=0.01,
                               err_msg="the STL box must be the placed part's box moved to the origin (no tilt)")
    dist, _ = cKDTree(want).query(np.asarray(stl.vertices))
    assert dist.max() < 0.01, f"STL vertices must be the placed part's vertices, only moved: max distance {dist.max():.3f} mm"


# ================================================================ project B: screw in a boss of a small closed housing
HOUSING = '''
import meshpart
from build123d import Align, Cylinder, Location, Pos, RigidJoint
from geom import P

UP = (Align.CENTER, Align.CENTER, Align.MIN)


def build():
    s = Cylinder(12, 20, align=UP) - Pos(0, 0, 2) * Cylinder(10, 16, align=UP)     # closed shell, 2 mm walls
    s += Pos(0, 0, 2) * Cylinder(3, 10, align=UP)                                  # boss Ø6 on the axis, top at z = 12
    s -= Pos(0, 0, 6) * Cylinder(0.85, 6, align=UP)                                # Ø1.7 M2 hole, 6 deep from the boss top
    h = {wrap}
    RigidJoint("bottom", h, Location((0, 0, 0)))
    RigidJoint("boss", h, Location((0, 0, 12)))
    return P("{name}", h, "#d9d4ca", process="{process}", material="PETG")
'''

BASE_B = '''
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    s = Box(200, 40, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    for name, x in {"hm_ok": 20, "hm_deep": 60, "hs_ok": 110, "hs_deep": 150}.items():
        RigidJoint(name + "_seat", s, Location((x, 20, 10)))
    return P("base", s, "#888888", process="mill", material="S235JR")
'''

FRAME_B = '''
from build123d import Pos
from geom import place
from . import base, hm, hs, screw


def build():
    b = base.build()
    out = [place(b, Pos(0, 0, 0))]
    for mod, tag in ((hm, "hm"), (hs, "hs")):
        for kind, length in (("ok", 6), ("deep", 8)):
            h = mod.build()
            b["shape"].joints[f"{tag}_{kind}_seat"].connect_to(h["shape"].joints["bottom"])
            out.append(place(h, name=f"{tag}_{kind}"))
            s = screw.build("M2", length, "DIN 912")
            h["shape"].joints["boss"].connect_to(s["shape"].joints["head"])
            out.append(place(s, name=f"scr_{tag}_{kind}"))
    return out
'''

FILES_B = {"frame/__init__.py": FRAME_B, "frame/base.py": BASE_B,
           "frame/hm.py": _twin(HOUSING, "hm", True), "frame/hs.py": _twin(HOUSING, "hs", False)}


def _build_b(shared):
    root = MP.write_project(shared / "proj", FILES_B, screw=True)
    r = H.run_model(root)
    (shared / "cold.json").write_text(json.dumps({"rc": r.rc, "log": r.log}))


@pytest.fixture(scope="module")
def fit_b(tmp_path_factory):
    import conftest
    shared = conftest._shared(tmp_path_factory, "meshsolid_review_b", _build_b)
    cold = json.loads((shared / "cold.json").read_text())
    if cold["rc"] != 0:
        return None, cold["log"]
    root = conftest._clean_copy(shared / "proj", tmp_path_factory.mktemp("msr") / "proj")
    if str(H.SCRIPTS) not in sys.path:
        sys.path.insert(0, str(H.SCRIPTS))
    import fit_checks
    return fit_checks.fit_report(root), cold["log"]


def _fit(fit_b):
    rep, log = fit_b
    assert rep is not None, f"the --model build of review project B failed:\n{log[-2500:]}"
    return rep


def _naming(rep, name):
    return [it for it in rep["fastener"] + rep["clash"] if name in it["parts"]]


@pytest.mark.parametrize("tag", ["hm", "hs"])
def test_r2_m2x6_in_the_6_mm_boss_hole_is_not_reported(fit_b, tag):
    """1.7 is the M2 tap drill band, 6 mm of it under the head: nothing to report (no thread-engagement WARN either)."""
    rep = _fit(fit_b)
    assert _naming(rep, f"scr_{tag}_ok") == [], f"M2x6 in the boss of {tag}: {_naming(rep, f'scr_{tag}_ok')}"


@pytest.mark.parametrize("tag", ["hm", "hs"])
def test_r2_m2x8_in_the_6_mm_boss_hole_bottoms_out(fit_b, tag):
    rep = _fit(fit_b)
    got = _naming(rep, f"scr_{tag}_deep")
    fails = [it for it in got if it["level"] == "FAIL"]
    assert fails and "bottom" in fails[0]["why"].lower(), f"M2x8 in a 6 mm hole bottoms out ({tag}): {got}"
    assert fails[0].get("available") == pytest.approx(6.0, abs=0.1), fails[0]
    assert [it for it in got if it["level"] == "WARN"] == [], f"no spurious WARN for {tag}: {got}"


def test_r2_mesh_housing_gets_the_same_verdicts_as_its_step_twin(fit_b):
    rep = _fit(fit_b)
    assert rep["fastener_pairs"] == 4, f"all four screws (two in mesh bosses) must be examined: {rep['fastener_pairs']}"
    for kind in ("ok", "deep"):
        vm = sorted((it["level"], "bottom" in it["why"].lower()) for it in _naming(rep, f"scr_hm_{kind}"))
        vs = sorted((it["level"], "bottom" in it["why"].lower()) for it in _naming(rep, f"scr_hs_{kind}"))
        assert vm == vs, f"{kind}: mesh host {_naming(rep, f'scr_hm_{kind}')} vs STEP host {_naming(rep, f'scr_hs_{kind}')}"
