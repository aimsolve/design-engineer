"""Issue #78 «mesh parts», the build: a part file returns `meshpart.Mesh(...)` as its shape and `cad_incremental.py --model`
stores it as a first-class part (triangles in its own coordinates, joints, placements, qty), cached like any part.

Done-when served here (numbers of the ticket):
  DW1   build + cache + joints (the main project of meshsolid_proj.py; docking both ways, two placements, reuse, a param change)
  DW2   a Mesh that is not a closed solid fails the build naming the part
  DW11  meshpart.sdf / meshpart.solid / Mesh(...) and copy / deepcopy, called directly
DW12 (the old view-only mesh parts) is test_mesh_parts.py, run unchanged.

    python -m pytest skills/machine-project/scripts/tests/test_mesh_solid.py -n 8 -q -W ignore     (from agent/)
"""
import copy
import math
import sys
from pathlib import Path

import numpy as np
import pytest

import helpers as H
import meshsolid_proj as MP
from meshsolid_proj import mesh_main, mesh_copy, main_ok, load_mesh, trimesh_of, world_bbox, placement_map  # noqa: F401

IDENTITY = np.eye(4)


def _meshpart():
    """The module under test, imported in the test body: a missing module FAILS the test instead of erroring collection."""
    if str(H.SCRIPTS) not in sys.path:
        sys.path.insert(0, str(H.SCRIPTS))
    import meshpart
    return meshpart


def T(x, y, z):
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


# ================================================================ DW1: the build stores a mesh part
def test_dw1_a_part_file_returning_a_mesh_builds(mesh_main):
    root = main_ok(mesh_main)
    parts = H.by_id(root)
    assert MP.MESH_IDS | MP.STEP_IDS <= set(parts), f"every part must be in parts.json: {sorted(parts)}"


def test_dw1_mesh_part_entry_has_mesh_file_mesh_solid_and_no_step(mesh_main):
    root = main_ok(mesh_main)
    parts = H.by_id(root)
    for pid in MP.MESH_IDS:
        p = parts[pid]
        assert "step" not in p, f"{pid}: a mesh part has no STEP, got {p.get('step')!r}"
        assert p.get("mesh_solid") is True, f"{pid}: `mesh_solid: true` expected, got {p.get('mesh_solid')!r}"
        v, f = load_mesh(root, p)
        assert len(f) >= 12, f"{pid}: {len(f)} triangles stored"
    assert parts["shell"]["process"] == "print" and parts["shell"].get("material") == "PETG", parts["shell"]
    assert len({parts[pid]["mesh"] for pid in MP.MESH_IDS}) == 3, "each mesh part has its own file"
    for pid in MP.STEP_IDS:
        assert parts[pid].get("step") and "mesh" not in parts[pid], f"{pid}: a STEP part stays a STEP part: {parts[pid]}"


def test_dw1_stored_triangles_are_the_closed_body_in_its_own_coordinates(mesh_main):
    """shell = Box(40, 10, 30) centred in X/Y, bottom at 0: the stored file is that solid, not moved to a placement."""
    root = main_ok(mesh_main)
    v, f = load_mesh(root, H.by_id(root)["shell"])
    tm = trimesh_of(v, f)
    assert tm.is_watertight, "the stored shell mesh must be closed"
    assert abs(tm.volume) == pytest.approx(40 * 10 * 30, rel=1e-3)
    np.testing.assert_allclose([v.min(0), v.max(0)], [[-20, -5, 0], [20, 5, 30]], atol=1e-3)


def test_dw1_mesh_part_joints_are_recorded_like_a_step_parts(mesh_main):
    root = main_ok(mesh_main)
    j = H.by_id(root)["shell"].get("joints") or {}
    assert set(j) == {"bottom", "top"}, f"shell joints: {j}"
    assert j["bottom"]["type"] == j["top"]["type"] == "rigid", j
    np.testing.assert_allclose(MP.matrix(j["bottom"]["loc"]), IDENTITY, atol=1e-9)
    np.testing.assert_allclose(MP.matrix(j["top"]["loc"]), T(0, 0, 30), atol=1e-9)


def test_dw1_mesh_part_placed_twice_has_two_placements_and_qty_2(mesh_main):
    """Docked onto a STEP part (base) twice: each placement carries its loc and its joint."""
    root = main_ok(mesh_main)
    p = H.by_id(root)["shell"]
    pls = {pl["name"]: pl for pl in p.get("placements") or []}
    assert set(pls) == {"shell_a", "shell_b"}, f"two placements expected: {sorted(pls)}"
    assert p.get("qty") == 2, f"qty = number of placements: {p.get('qty')}"
    for name, at, xyz in (("shell_a", "shell_seat_a", (30, 20, 10)), ("shell_b", "shell_seat_b", (100, 20, 10))):
        jt = pls[name].get("joint") or {}
        assert (jt.get("to"), jt.get("at"), jt.get("by")) == ("base", at, "bottom"), f"{name}: joint {jt}"
        np.testing.assert_allclose(MP.matrix(pls[name]["loc"]), T(*xyz), atol=1e-6, err_msg=name)


def test_dw1_step_part_docked_onto_the_mesh_part_stands_on_its_joint(mesh_main):
    root = main_ok(mesh_main)
    pm = placement_map(root)
    p, pl = pm["cap"]
    jt = pl.get("joint") or {}
    assert (jt.get("to"), jt.get("at"), jt.get("by")) == ("shell_a", "top", "bottom"), f"cap joint: {jt}"
    np.testing.assert_allclose(MP.matrix(pl["loc"]), T(30, 20, 40), atol=1e-6)


def test_dw1_world_boxes_of_the_mesh_placements(mesh_main):
    """Stored triangles moved by each placement's loc land where the joints put them (wedge turned 90 deg about Z)."""
    root = main_ok(mesh_main)
    pm = placement_map(root)
    for name in ("shell_a", "shell_b", "wedge", "twin"):
        p, pl = pm[name]
        v, _ = load_mesh(root, p)
        np.testing.assert_allclose(world_bbox(v, pl["loc"]), MP.WORLD[name], atol=1e-3, err_msg=name)


def test_dw1_deepcopy_and_copy_of_a_mesh_keep_triangles_and_joints(mesh_main):
    """wedge is returned as copy.deepcopy(Mesh), twin (raw vertices + triangles) as copy.copy(Mesh): both keep their triangles
    and their `bottom` joint, and both dock."""
    root = main_ok(mesh_main)
    parts = H.by_id(root)
    v, f = load_mesh(root, parts["wedge"])
    assert abs(trimesh_of(v, f).volume) == pytest.approx(10 * 20 * 30, rel=1e-3)
    v, f = load_mesh(root, parts["twin"])
    assert len(f) == 12 and abs(trimesh_of(v, f).volume) == pytest.approx(12 ** 3, rel=1e-6)
    for pid in ("wedge", "twin"):
        assert "bottom" in (parts[pid].get("joints") or {}), f"{pid}: joints lost in the copy: {parts[pid].get('joints')}"
        jt = parts[pid]["placements"][0].get("joint") or {}
        assert jt.get("to") == "base" and jt.get("by") == "bottom", f"{pid}: {jt}"


def test_dw1_second_unchanged_run_rebuilds_nothing_and_keeps_the_mesh_file(mesh_copy):
    root = mesh_copy
    before = {pid: (H.by_id(root)[pid]["mesh"], H.sha_file(root / H.by_id(root)[pid]["mesh"])) for pid in MP.MESH_IDS}
    pls_before = {n: pl["loc"] for n, (_, pl) in placement_map(root).items()}
    r = H.run_model(root).ok()
    assert r.summary[0] == 0, f"nothing changed: no unit may rebuild, rebuilt {r.rebuilt}"
    for pid, (rel, sha) in before.items():
        p = H.by_id(root)[pid]
        assert p.get("mesh") == rel and H.sha_file(root / rel) == sha, f"{pid}: the stored mesh changed on a no-change run"
        assert p.get("mesh_solid") is True and "step" not in p
    assert {n: pl["loc"] for n, (_, pl) in placement_map(root).items()} == pls_before, "placements changed on a no-change run"


def test_dw1_changing_a_param_the_mesh_part_reads_rebuilds_it_and_changes_its_triangles(mesh_copy):
    root = mesh_copy
    old = H.by_id(root)["shell"]
    sha = H.sha_file(root / old["mesh"])
    H.edit(H.cad_dir(root) / "params.py", "SHELL_W = 40.0", "SHELL_W = 50.0")
    r = H.run_model(root).ok()
    assert "frame.shell.build" in r.rebuilt, f"shell reads SHELL_W and must rebuild: {r.rebuilt}"
    assert "frame.base.build" not in r.rebuilt, f"base does not read SHELL_W: {r.rebuilt}"
    new = H.by_id(root)["shell"]
    assert H.sha_file(root / new["mesh"]) != sha, "the stored triangles must change"
    v, f = load_mesh(root, new)
    np.testing.assert_allclose([v.min(0), v.max(0)], [[-25, -5, 0], [25, 5, 30]], atol=1e-3)
    assert abs(trimesh_of(v, f).volume) == pytest.approx(50 * 10 * 30, rel=1e-3)
    assert new.get("qty") == 2 and len(new.get("placements") or []) == 2


def test_dw1_docking_holds_when_the_mesh_part_is_reused_from_the_cache(mesh_copy):
    """SEAT_X is read by base only: base and the node rebuild, the shell comes from the cache (its file unchanged), and still
    shell_a moves with the seat and the cap docked on the cached shell's `top` moves with it."""
    root = mesh_copy
    sha = H.sha_file(root / H.by_id(root)["shell"]["mesh"])
    H.edit(H.cad_dir(root) / "params.py", "SEAT_X = 30.0", "SEAT_X = 36.0")
    r = H.run_model(root).ok()
    assert "frame.base.build" in r.rebuilt, f"base reads SEAT_X: {r.rebuilt}"
    assert "frame.shell.build" not in r.rebuilt, f"shell does not read SEAT_X and must be reused: {r.rebuilt}"
    p = H.by_id(root)["shell"]
    assert H.sha_file(root / p["mesh"]) == sha and p.get("mesh_solid") is True
    pm = placement_map(root)
    np.testing.assert_allclose(MP.matrix(pm["shell_a"][1]["loc"]), T(36, 20, 10), atol=1e-6)
    np.testing.assert_allclose(MP.matrix(pm["shell_b"][1]["loc"]), T(100, 20, 10), atol=1e-6)
    np.testing.assert_allclose(MP.matrix(pm["cap"][1]["loc"]), T(36, 20, 40), atol=1e-6)
    assert (pm["cap"][1].get("joint") or {}).get("to") == "shell_a"


def test_dw1_cap_change_rebuilds_the_node_and_the_cached_mesh_part_still_docks(mesh_copy):
    """Only cap.py changes: the node re-runs with shell and base read back from the cache; every placement is as before."""
    root = mesh_copy
    pls_before = {n: pl["loc"] for n, (_, pl) in placement_map(root).items()}
    H.edit(H.cad_dir(root) / "parts/frame/cap.py", "def build", "# touched\ndef build")
    r = H.run_model(root).ok()
    assert "frame.cap.build" in r.rebuilt and "frame.shell.build" not in r.rebuilt, r.rebuilt
    after = {n: pl["loc"] for n, (_, pl) in placement_map(root).items()}
    assert set(after) == set(pls_before)
    for n in pls_before:
        np.testing.assert_allclose(MP.matrix(after[n]), MP.matrix(pls_before[n]), atol=1e-9, err_msg=n)


# ================================================================ DW2: an open mesh fails the build naming the part
OPEN_LID = '''
import meshpart
from geom import P

V = [[x, y, z] for x in (0.0, 10.0) for y in (0.0, 10.0) for z in (0.0, 10.0)]
F = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
     [0, 2, 6], [0, 6, 4], [1, 5, 7]]                 # the 12th triangle [1, 7, 3] is missing: the box is open


def build():
    return P("lid_open", meshpart.Mesh(vertices=V, triangles=F), "#cccccc", process="print", material="PLA")
'''
LID_NODE = '''
from build123d import Pos
from geom import place
from . import lid_open


def build():
    return [place(lid_open.build(), Pos(0, 0, 0))]
'''


def test_dw2_an_open_mesh_fails_the_build_naming_the_part(tmp_path):
    root = MP.write_project(tmp_path / "p", {"lid/__init__.py": LID_NODE, "lid/lid_open.py": OPEN_LID},
                            assemble="from parts import lid\nout = lid.build()\n")
    r = H.run_model(root)
    assert r.rc != 0, f"an open mesh must fail the model build, it exited 0:\n{r.log[-1500:]}"
    import re
    said = [ln for ln in r.log.splitlines()
            if "lid_open" in ln and re.search(r"clos|manifold|watertight|\bopen\b", ln, re.I)]
    assert said, f"the build must say the mesh of part 'lid_open' is not a closed solid; log:\n{r.log[-2000:]}"


# ================================================================ DW11: meshpart helpers, called directly
def _manifold_ok(m):
    import manifold3d
    assert not m.is_empty(), "empty manifold"
    assert m.status() == manifold3d.Error.NoError, f"manifold status {m.status()}"


def _tm(m):
    mesh = m.to_mesh()
    return trimesh_of(np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts))


def test_dw11_sdf_sphere_has_the_sphere_volume_and_is_closed():
    mp = _meshpart()
    m = mp.sdf(lambda x, y, z: np.sqrt(x * x + y * y + z * z) - 10.0, (-12, -12, -12, 12, 12, 12))
    import manifold3d
    assert isinstance(m, manifold3d.Manifold), f"sdf must return a manifold3d.Manifold, got {type(m)}"
    _manifold_ok(m)
    assert m.volume() == pytest.approx(4 / 3 * math.pi * 1000, rel=0.02)
    assert _tm(m).is_watertight


def test_dw11_sdf_level_offsets_the_surface():
    """{f < level}: level 2 on a r = 10 sphere's distance is the r = 12 sphere."""
    mp = _meshpart()
    m = mp.sdf(lambda x, y, z: np.sqrt(x * x + y * y + z * z) - 10.0, (-14, -14, -14, 14, 14, 14), edge=0.5, level=2.0)
    _manifold_ok(m)
    assert m.volume() == pytest.approx(4 / 3 * math.pi * 12 ** 3, rel=0.02)


def test_dw11_solid_of_a_build123d_box_keeps_its_volume():
    mp = _meshpart()
    from build123d import Box
    m = mp.solid(Box(10, 20, 30))
    _manifold_ok(m)
    assert m.volume() == pytest.approx(6000.0, rel=1e-3)
    bb = _tm(m).bounds
    np.testing.assert_allclose(bb, [[-5, -10, -15], [5, 10, 15]], atol=1e-6)


def test_dw11_solid_keeps_a_fit_hole_within_tol():
    """A 5.5 mm hole through a 20 mm block: the narrowest radius the mesh leaves at the hole is 2.75 within tol (0.01)."""
    mp = _meshpart()
    from build123d import Box, Cylinder
    m = mp.solid(Box(20, 20, 10) - Cylinder(2.75, 20), tol=0.01)
    _manifold_ok(m)
    tm = _tm(m)
    v = tm.vertices
    r = np.hypot(v[:, 0], v[:, 1])
    inner = r[r < 5]
    assert inner.size, "no vertex on the hole"
    # vertices sit on the circle; the chord between two of them comes in by at most tol
    assert inner.max() == pytest.approx(2.75, abs=0.01)
    mids = (tm.vertices[tm.edges_unique[:, 0]] + tm.vertices[tm.edges_unique[:, 1]]) / 2
    rm = np.hypot(mids[:, 0], mids[:, 1])
    assert rm[rm < 5].min() >= 2.75 - 0.0101


def test_dw11_mesh_is_a_build123d_compound_that_takes_joints():
    mp = _meshpart()
    from build123d import Box, Compound, Location, RigidJoint
    m = mp.Mesh(mp.solid(Box(10, 10, 10)))
    assert isinstance(m, Compound), f"Mesh must be a build123d Compound subclass, got {type(m).__mro__}"
    RigidJoint("bottom", m, Location((0, 0, -5)))
    assert "bottom" in m.joints


@pytest.mark.parametrize("how", ["copy", "deepcopy"])
def test_dw11_mesh_survives_copy_with_its_joints(how):
    mp = _meshpart()
    from build123d import Location, RigidJoint
    v = [[x, y, z] for x in (0.0, 1.0) for y in (0.0, 1.0) for z in (0.0, 1.0)]
    f = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
         [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]
    m = mp.Mesh(vertices=np.array(v), triangles=np.array(f))
    RigidJoint("bottom", m, Location((0.5, 0.5, 0)))
    c = (copy.copy if how == "copy" else copy.deepcopy)(m)
    assert isinstance(c, mp.Mesh), type(c)
    assert set(c.joints) == {"bottom"}, f"{how} lost the joints: {c.joints}"
    assert tuple(c.joints["bottom"].location.position) == pytest.approx((0.5, 0.5, 0.0))
