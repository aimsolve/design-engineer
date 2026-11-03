#!/usr/bin/env python3
"""Tests for the docking distance in scripts/step_to_project.py: contact(), _exact_contact(), bbox_gap().

Run (build123d 0.13.0; one pytest process at a time on the Mac):
  <venv>/bin/python -m pytest -q -p no:cacheprovider agent/skills/step-import/scripts/tests/test_step_to_project_contact.py

Contract under test (from the spec, not from the code):
  S._exact_contact(a_topods, b_topods) -> (distance, point_on_a | None)   OCC BRepExtrema between whole shapes: the reference.
  S.contact(a, b, cache=None) -> (distance, point_on_a)   two placed build123d shapes. Returns the SAME distance as
      _exact_contact(a.wrapped, b.wrapped) to 1e-9 mm, with a point that lies on a's surface (or inside a when the distance
      is 0 by containment) at that distance from b.
      Exception: when the two parts together have more than S.MESH_FACES faces the distance is measured on meshes; it is
      never less than exact - 0.25 mm and, for parts with small faces, within a few mm of exact.
  A part entirely inside another part's material is at distance 0 (as OCC has it).
  S.FACE_PAIRS caps the face-pair work; past it the function falls back to the whole-shape measure, still exact.
  S.bbox_gap(a, b, cache=None): the gap between the parts' tight boxes; with a cache dict the value is the same as without.
"""
import sys
from pathlib import Path

import pytest
from build123d import Align, Axis, Box, Cylinder, Pos, Sphere

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import step_to_project as S  # noqa: E402

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex  # noqa: E402
from OCP.BRepExtrema import BRepExtrema_DistShapeShape  # noqa: E402
from OCP.gp import gp_Pnt  # noqa: E402
from OCP.TopAbs import TopAbs_SOLID  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402

EXACT_TOL = 1e-9        # contact() must agree with _exact_contact() this closely
POINT_TOL = 1e-6        # the returned point lies on a / at distance d from b this closely
MESH_UNDER = 0.25       # mesh path: never below exact - 0.25 mm
MESH_OVER = 3.0         # mesh path: "within a few mm" for parts whose faces are small
MIN = (Align.MIN, Align.MIN, Align.MIN)


# ---------------------------------------------------------------- helpers

def _xyz(p):
    """The returned point as (x, y, z), whatever type contact() chose (gp_Pnt, build123d Vector, tuple, ndarray)."""
    if isinstance(p, gp_Pnt):
        return (p.X(), p.Y(), p.Z())
    if hasattr(p, "X") and not callable(getattr(p, "X")):
        return (p.X, p.Y, p.Z)
    x, y, z = (float(v) for v in p)
    return (x, y, z)


def _dist_point_shape(p, topods):
    """OCC distance from a point to a shape's material: 0 for a point inside one of its solids. BRepExtrema treats the
    inside as material only for a TopoDS_Solid (a build123d Part wraps its solid in a Compound), so measure solid by solid."""
    v = BRepBuilderAPI_MakeVertex(gp_Pnt(*_xyz(p))).Vertex()
    targets = []
    ex = TopExp_Explorer(topods, TopAbs_SOLID)
    while ex.More():
        targets.append(ex.Current())
        ex.Next()
    best = None
    for t in targets or [topods]:
        d = BRepExtrema_DistShapeShape(v, t)
        assert d.IsDone()
        best = d.Value() if best is None else min(best, d.Value())
    return best


def box(x0, y0, z0, dx, dy, dz):
    return Pos(x0, y0, z0) * Box(dx, dy, dz, align=MIN)


def l_bracket():
    """An L of 2 mm sheet: a vertical leg x in [0,2], a horizontal leg z in [0,2]; both 30 long, 20 deep (y in [0,20])."""
    return box(0, 0, 0, 2, 20, 30) + box(0, 0, 0, 30, 20, 2)


def holed_plate(nx, ny, pitch=8.0, d_hole=6.0, t=4.0, x0=0.0, y0=0.0, z0=0.0):
    """A t-thick plate with an nx x ny grid of through holes d_hole at `pitch`; hole (i, j) centred at
    (x0 + pitch*(i+1), y0 + pitch*(j+1)). Many small faces: each hole is its own cylindrical face."""
    w, h = pitch * (nx + 1), pitch * (ny + 1)
    plate = box(x0, y0, z0, w, h, t)
    holes = None
    for i in range(nx):
        for j in range(ny):
            c = Pos(x0 + pitch * (i + 1), y0 + pitch * (j + 1), z0 - 1) * Cylinder(
                d_hole / 2, t + 2, align=(Align.CENTER, Align.CENTER, Align.MIN))
            holes = c if holes is None else holes + c
    return plate - holes


def exact(a, b):
    d, _ = S._exact_contact(a.wrapped, b.wrapped)
    return d


def assert_matches_exact(a, b, cache=None):
    """contact(a, b) equals the OCC whole-shape distance, and its point is on (or in) a at that distance from b."""
    ref = exact(a, b)
    d, p = S.contact(a, b, cache) if cache is not None else S.contact(a, b)
    assert abs(d - ref) <= EXACT_TOL, f"contact {d!r} != exact {ref!r}"
    assert p is not None, "contact() returned no point on a"
    on_a = _dist_point_shape(p, a.wrapped)
    to_b = _dist_point_shape(p, b.wrapped)
    assert on_a <= POINT_TOL, f"point {_xyz(p)} is {on_a} mm off part a"
    assert abs(to_b - d) <= POINT_TOL, f"point {_xyz(p)} is {to_b} mm from b, contact says {d}"
    return d


# ---------------------------------------------------------------- fixtures (built once: the booleans are the slow part)

@pytest.fixture(scope="module")
def plate150():
    """15 x 10 = 150 holes d6 at pitch 8: 156 faces."""
    return holed_plate(15, 10)


@pytest.fixture(scope="module")
def pin_in_hole():
    """A d5.6 pin through the hole at grid (7, 4) of plate150 (centre (64, 40)), sticking out both sides: 0.2 mm radial
    clearance, the pin's box inside the plate's box footprint."""
    return Pos(64, 40, -5) * Cylinder(2.8, 14, align=(Align.CENTER, Align.CENTER, Align.MIN))


# ---------------------------------------------------------------- exact path

def test_boxes_touching_face_to_face():
    """Condition: same distance as _exact_contact (here 0) for two boxes sharing a face."""
    a = box(0, 0, 0, 10, 10, 10)
    b = box(10, 2, 2, 5, 5, 5)
    d = assert_matches_exact(a, b)
    assert d <= 1e-7


def test_boxes_apart_with_overlapping_boxes_l_bracket_beside_plate():
    """Condition: exact distance when the parts' boxes overlap. A plate sits in the L's inner corner, 0.3 mm from the
    vertical leg and 0.5 mm above the horizontal leg; the L's box contains the plate's."""
    a = l_bracket()
    b = box(2.3, 2, 2.5, 15, 10, 8)
    assert S.bbox_gap(a, b) <= 1e-6, "test setup: the boxes must overlap"
    d = assert_matches_exact(a, b)
    assert abs(d - 0.3) <= 1e-6
    d2 = assert_matches_exact(b, a)
    assert abs(d2 - 0.3) <= 1e-6


def test_small_box_inside_big_box_both_orders():
    """Condition: a part entirely inside another's material gets the reference's own answer (OCC on the whole shapes, as the
    converter measured before): for build123d Parts (Compounds) that is the distance between their surfaces, 20 mm here.
    (The first spec said 0, which OCC gives only between bare solids.)"""
    big = box(0, 0, 0, 50, 50, 50)
    small = box(20, 20, 20, 5, 5, 5)
    for a, b in ((small, big), (big, small)):     # the distance only: the helper's point check measures to b's solid
        assert abs(S.contact(a, b)[0] - exact(a, b)) <= 1e-9


def test_small_solid_inside_big_solid_reference_is_zero():
    """Condition (reference): between bare solids OCC already reports containment as 0; contact() agrees."""
    big = box(0, 0, 0, 50, 50, 50).solid()
    small = box(20, 20, 20, 5, 5, 5).solid()
    assert exact(small, big) <= 1e-9
    assert assert_matches_exact(small, big) <= 1e-9


def test_interpenetrating_boxes_are_zero():
    """Condition: two boxes that cut into each other are at distance 0."""
    a = box(0, 0, 0, 10, 10, 10)
    b = box(7, 7, 7, 10, 10, 10)
    assert assert_matches_exact(a, b) <= 1e-9
    assert assert_matches_exact(b, a) <= 1e-9


def test_pin_through_holed_plate_beyond_face_pairs(plate150, pin_in_hole):
    """Condition: a plate of 150 holes and a pin through one hole (156 x 3 face pairs > FACE_PAIRS) still gives the exact
    distance, 0.2 mm."""
    assert len(plate150.faces()) * len(pin_in_hole.faces()) > S.FACE_PAIRS, "test setup: more face pairs than the cap"
    assert len(plate150.faces()) + len(pin_in_hole.faces()) <= S.MESH_FACES, "test setup: must stay on the exact path"
    d = assert_matches_exact(plate150, pin_in_hole)
    assert abs(d - 0.2) <= 1e-6
    d2 = assert_matches_exact(pin_in_hole, plate150)
    assert abs(d2 - 0.2) <= 1e-6


def test_face_pairs_cap_forced_low_falls_back_exact(monkeypatch, plate150, pin_in_hole):
    """Condition: with FACE_PAIRS at 1 the fallback to the whole-shape measure must still be exact."""
    monkeypatch.setattr(S, "FACE_PAIRS", 1)
    assert abs(assert_matches_exact(plate150, pin_in_hole) - 0.2) <= 1e-6
    a = l_bracket()
    b = box(2.3, 2, 2.5, 15, 10, 8)
    assert abs(assert_matches_exact(a, b) - 0.3) <= 1e-6


def test_parts_far_apart():
    """Condition: exact distance for parts whose boxes are far apart (diagonal offset: 30, 40, 0 -> 50)."""
    a = box(0, 0, 0, 10, 10, 10)
    b = box(40, 50, 0, 10, 10, 10)
    d = assert_matches_exact(a, b)
    assert abs(d - 50.0) <= 1e-6


def test_sphere_inside_box():
    """Condition: a sphere (a single face, no corner vertices) inside a box gets the reference's answer, in both orders."""
    bx = box(-10, -10, -10, 20, 20, 20)
    sp = Sphere(4)
    for a, b in ((sp, bx), (bx, sp)):             # the distance only, as above
        assert abs(S.contact(a, b)[0] - exact(a, b)) <= 1e-9


def test_sphere_beside_box_corner_with_overlapping_boxes():
    """Condition: exact distance for a sphere off a box corner where the boxes overlap: |(8,8,8)-(5,5,5)| - 4."""
    bx = box(-5, -5, -5, 10, 10, 10)
    sp = Pos(8, 8, 8) * Sphere(4)
    assert S.bbox_gap(sp, bx) <= 1e-6, "test setup: the boxes must overlap"
    ref = 27 ** 0.5 - 4
    assert abs(assert_matches_exact(sp, bx) - ref) <= 1e-6
    assert abs(assert_matches_exact(bx, sp) - ref) <= 1e-6


def test_sphere_in_l_corner_nearest_point_mid_face():
    """Condition: exact distance when the nearest points lie mid-face on both parts (no vertex near them): a sphere r5 at
    (10, 10, 10) in the L's inner corner is 3 mm from each leg."""
    a = l_bracket()
    sp = Pos(10, 10, 10) * Sphere(5)
    assert abs(assert_matches_exact(sp, a) - 3.0) <= 1e-6
    assert abs(assert_matches_exact(a, sp) - 3.0) <= 1e-6


def test_sphere_beside_box_far():
    """Condition: exact distance for a sphere whose box is clear of the box's: centre 20 from a face at 5, r 5 -> 10."""
    bx = box(-5, -5, -5, 10, 10, 10)
    sp = Pos(20, 0, 0) * Sphere(5)
    assert abs(assert_matches_exact(sp, bx) - 10.0) <= 1e-6


def test_contact_with_cache_equals_without(plate150, pin_in_hole):
    """Condition: a cache dict does not change contact()'s distance, on first use and on reuse."""
    cache = {}
    a = l_bracket()
    b = box(2.3, 2, 2.5, 15, 10, 8)
    for _ in range(2):
        assert abs(assert_matches_exact(a, b, cache) - 0.3) <= 1e-6
        assert abs(assert_matches_exact(plate150, pin_in_hole, cache) - 0.2) <= 1e-6


def test_at_mesh_faces_limit_still_exact(monkeypatch):
    """Condition: the mesh approximation applies only when the faces are MORE than MESH_FACES; at exactly MESH_FACES the
    distance is still exact (a sphere's mesh would be off by its sag)."""
    a = l_bracket()
    sp = Pos(10, 10, 10) * Sphere(5)
    monkeypatch.setattr(S, "MESH_FACES", len(a.faces()) + len(sp.faces()))
    assert abs(assert_matches_exact(sp, a) - 3.0) <= 1e-6


# ---------------------------------------------------------------- mesh path

def _mesh_bound(a, b):
    ref = exact(a, b)
    d, _ = S.contact(a, b)
    assert d >= ref - MESH_UNDER, f"mesh distance {d} under exact {ref} by more than {MESH_UNDER}"
    assert d <= ref + MESH_OVER, f"mesh distance {d} over exact {ref} by more than {MESH_OVER}"
    return ref, d


def test_mesh_path_two_holed_plates_stacked(monkeypatch):
    """Condition: over MESH_FACES (patched to 10) two holed plates 3 mm apart, one above the other, measure within
    [exact - 0.25, exact + a few mm]."""
    a = holed_plate(4, 3)
    b = holed_plate(4, 3, x0=4, y0=4, z0=4 + 3)
    monkeypatch.setattr(S, "MESH_FACES", 10)
    assert len(a.faces()) + len(b.faces()) > S.MESH_FACES
    ref, _ = _mesh_bound(a, b)
    assert abs(ref - 3.0) <= 1e-6
    _mesh_bound(b, a)


def test_mesh_path_holed_plates_perpendicular(monkeypatch):
    """Condition: over MESH_FACES, a holed plate standing upright 2 mm beside the edge of another lying flat measures
    within [exact - 0.25, exact + a few mm]."""
    flat = holed_plate(4, 3)                                            # x in [0,40], y in [0,32], z in [0,4]
    upright = Pos(46, 0, 0) * holed_plate(3, 3).rotate(Axis.Y, -90)     # x in [42,46], y in [0,32], z in [0,32]
    gap = exact(flat, upright)
    assert abs(gap - 2.0) <= 1e-6, f"test setup: plates should be 2 mm apart, got {gap}"
    monkeypatch.setattr(S, "MESH_FACES", 10)
    _mesh_bound(flat, upright)
    _mesh_bound(upright, flat)


def test_mesh_path_pin_through_hole_never_under(monkeypatch, plate150, pin_in_hole):
    """Condition: on the mesh path a curved clearance (pin d5.6 in a hole d6: exact 0.2) is never reported below
    exact - 0.25 mm, i.e. the mesh must not invent a deep overlap."""
    monkeypatch.setattr(S, "MESH_FACES", 10)
    _mesh_bound(plate150, pin_in_hole)


# ---------------------------------------------------------------- bbox_gap

def test_bbox_gap_far_apart_and_cache():
    """Condition: bbox_gap is the gap between tight boxes (50 along x here) and a cache dict gives the same value."""
    a = box(0, 0, 0, 10, 10, 10)
    b = box(60, 0, 0, 10, 10, 10)
    g = S.bbox_gap(a, b)
    assert abs(g - 50.0) <= 1e-3
    cache = {}
    assert S.bbox_gap(a, b, cache) == pytest.approx(g, abs=1e-12)
    assert S.bbox_gap(a, b, cache) == pytest.approx(g, abs=1e-12)
    assert S.bbox_gap(b, a, cache) == pytest.approx(g, abs=1e-12)


def test_bbox_gap_tight_box_of_sphere():
    """Condition: the box is tight -- a sphere r5 at x=20 and a box ending at x=5 are 10 apart, not less (a loose box
    would be padded by OCC's tolerance and gap)."""
    bx = box(-5, -5, -5, 10, 10, 10)
    sp = Pos(20, 0, 0) * Sphere(5)
    g = S.bbox_gap(sp, bx)
    assert abs(g - 10.0) <= 1e-3
    cache = {}
    assert S.bbox_gap(sp, bx, cache) == pytest.approx(g, abs=1e-12)


def test_bbox_gap_overlapping_is_not_positive():
    """Condition: parts whose boxes overlap have no gap."""
    assert S.bbox_gap(l_bracket(), box(2.3, 2, 2.5, 15, 10, 8)) <= 1e-6


# ---------------------------------------------------------------- mesh path next to large flat faces (review: overstated)

def test_mesh_path_block_above_a_big_plate_with_a_hole_cluster_off_to_one_side(monkeypatch):
    """Condition: over MESH_FACES (patched to 10) the mesh distance must not be much over the exact one next to LARGE flat
    faces either. A 1000 x 1000 x 5 plate with a 6 x 6 cluster of d6 holes about 100 mm off its middle (so the plate's top
    face is one huge face meshed into long triangles), and a small holed block (24 x 24 x 4, 4 holes) 1 mm above the middle
    of the plate: contact within [exact - 0.25, exact + 1.0] mm, in both orders."""
    plate = box(0, 0, 0, 1000, 1000, 5)
    holes = None
    for i in range(6):
        for j in range(6):
            c = Pos(600 + 8 * i, 480 + 8 * j, -1) * Cylinder(3, 7, align=(Align.CENTER, Align.CENTER, Align.MIN))
            holes = c if holes is None else holes + c
    plate = plate - holes
    block = holed_plate(2, 2, x0=488, y0=488, z0=6)          # x, y in [488, 512], z in [6, 10]
    ref = exact(plate, block)
    assert abs(ref - 1.0) <= 1e-6, f"test setup: the block should be 1 mm above the plate, got {ref}"
    monkeypatch.setattr(S, "MESH_FACES", 10)
    assert len(plate.faces()) + len(block.faces()) > S.MESH_FACES, "test setup: must take the mesh path"
    for a, b in ((plate, block), (block, plate)):
        d, _ = S.contact(a, b)
        assert d >= ref - MESH_UNDER, f"mesh distance {d} under exact {ref} by more than {MESH_UNDER}"
        assert d <= ref + 1.0, f"mesh distance {d} over exact {ref} by more than 1.0 mm (large flat faces)"


# ---------------------------------------------------------------- the docking memo of convert() changes nothing

class _MemoNeverHits(dict):
    """A memo that stores but never reports a key as present: every `key not in memo` is True, so every pair is measured."""

    def __contains__(self, key):
        return False

    def get(self, key, default=None):
        return default


def _contact_bypassing_the_memo(real, calls, swapped):
    """S.contact wrapped: counts its calls and, from its caller (convert's touch()), swaps convert's per-run docking memo
    `touched` for a _MemoNeverHits before anything is stored, so no pair in the same relative place reuses a measure."""
    import gc
    import inspect

    def wrapped(a, b, cache=None):
        calls.append(1)
        code = sys._getframe(1).f_code
        if "touched" in code.co_freevars:
            i = code.co_freevars.index("touched")
            for fn in gc.get_referrers(code):
                if inspect.isfunction(fn) and fn.__closure__ and not isinstance(fn.__closure__[i].cell_contents, _MemoNeverHits):
                    fn.__closure__[i].cell_contents = _MemoNeverHits()
                    swapped.append(1)
        return real(a, b, cache)
    return wrapped


ROW_N = 4


def _row_machine(imp):
    """An import dir (parts.json, instances.json, parts/<id>.step) of one node: a 200 x 60 x 5 base plate at the origin and a
    row of ROW_N identical holed blocks 20 x 20 x 10 floating 3 mm above it, 1 mm apart: every block is nearer to the block
    before it than to the plate, and every pair of neighbours is in the same relative place (the memo's case)."""
    import json
    from build123d import export_step
    (imp / "parts").mkdir(parents=True)
    blk = box(0, 0, 0, 20, 20, 10) - Pos(10, 10, -1) * Cylinder(3, 12, align=(Align.CENTER, Align.CENTER, Align.MIN))
    shapes = {"01.01.001": box(0, 0, 0, 200, 60, 5), "01.01.002": blk}
    names = {"01.01.001": "Base plate 01.01.001", "01.01.002": "Block 01.01.002"}
    parts = []
    for pid, sh in shapes.items():
        export_step(sh, str(imp / "parts" / f"{pid}.step"))
        parts.append({"id": pid, "name": names[pid], "drawing": pid, "kind": "made", "volume_mm3": sh.volume,
                      "instances": 1 if pid == "01.01.001" else ROW_N})
    (imp / "parts.json").write_text(json.dumps({"assembly": {"source": "row.step"}, "parts": parts}))

    def at(x, y, z):
        return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]
    tree = {"name": "Machine 05.00.000", "product": "Machine 05.00.000", "ref": "R", "matrix": at(0, 0, 0), "children": [
        {"name": "Base plate:1", "product": names["01.01.001"], "ref": "p", "matrix": at(0, 0, 0), "part": "01.01.001"}] + [
        {"name": f"Block:{k + 1}", "product": names["01.01.002"], "ref": "b", "matrix": at(20 + 21 * k, 20, 8),
         "part": "01.01.002"} for k in range(ROW_N)]}
    (imp / "instances.json").write_text(json.dumps(tree))
    return tree


@pytest.fixture(scope="module")
def row_runs(tmp_path_factory):
    """The row machine converted twice into the same project path: once as convert() runs, once with the docking memo
    bypassed. {"memo": cad files, "bypass": cad files, "calls": (memo run, bypass run), "swapped": bool, "build": cp, ...}"""
    import shutil
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_step_to_project import CAD_INC, cad_files, run
    base = tmp_path_factory.mktemp("row")
    imp = base / "imp"
    tree = _row_machine(imp)
    root = base / "proj"
    real = S.contact
    out = {"tree": tree, "root": root}

    calls = []
    root.mkdir()

    def counting(a, b, cache=None):
        calls.append(1)
        return real(a, b, cache)
    S.contact = counting
    try:
        S.convert(imp, root, "T", log=lambda *a: None)
    finally:
        S.contact = real
    out["build"] = run([sys.executable, CAD_INC, "--model", "assemble.py"], cwd=root / "mechanics" / "cad")
    out["memo"] = cad_files(root)
    out["model"] = (root / "build" / "model" / "parts.json").read_text() \
        if (root / "build" / "model" / "parts.json").is_file() else None

    shutil.rmtree(root)
    root.mkdir()
    calls2, swapped = [], []
    S.contact = _contact_bypassing_the_memo(real, calls2, swapped)
    try:
        S.convert(imp, root, "T", log=lambda *a: None)
    finally:
        S.contact = real
    out["bypass"] = cad_files(root)
    out["calls"] = (len(calls), len(calls2))
    out["swapped"] = bool(swapped)
    return out


def test_docking_memo_gives_the_same_project_as_measuring_every_pair(row_runs):
    """Condition: the per-run contact memo is only a shortcut. A row of 4 identical blocks in identical relative places next
    to a base plate converts to the same files (node file joint lines included) with the memo and with every pair measured."""
    assert row_runs["swapped"], "test setup: convert()'s docking memo (`touched`, seen from contact()'s caller) was not found"
    memo_calls, bypass_calls = row_runs["calls"]
    assert bypass_calls > memo_calls, \
        f"test setup: the memo was never used ({memo_calls} contact() calls with it, {bypass_calls} without)"
    memo, bypass = row_runs["memo"], row_runs["bypass"]
    assert set(memo) == set(bypass), f"files differ: only with memo {sorted(set(memo) - set(bypass))}, " \
                                     f"only without {sorted(set(bypass) - set(memo))}"
    diff = []
    for f in sorted(memo):
        if memo[f] != bypass[f]:
            a = memo[f].decode("utf-8", "replace").splitlines()
            b = bypass[f].decode("utf-8", "replace").splitlines()
            lines = [f"    memo:   {x}\n    bypass: {y}" for x, y in zip(a, b) if x != y][:6]
            diff.append(f"  {f}:\n" + "\n".join(lines))
    assert not diff, "the memo changes the generated project:\n" + "\n".join(diff)


def test_row_of_identical_parts_stands_where_the_source_has_it(row_runs):
    """Condition: with the memo, every placement of the row machine's built model equals its source instance's world
    matrix within 1e-6 mm, and every placement but the root's has a joint."""
    import json
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_step_to_project import leaves, log, mat, mdiff, spell
    assert row_runs["build"].returncode == 0, "cad_incremental.py --model assemble.py failed\n" + log(row_runs["build"])
    model = json.loads(row_runs["model"])["parts"]
    want = {}
    for pid, m, _ in leaves(row_runs["tree"]):
        want.setdefault(spell(pid), []).append(m)
    pls = [pl for p in model for pl in p.get("placements") or []]
    assert len(pls) == 1 + ROW_N, f"{len(pls)} placements, the source has {1 + ROW_N}"
    assert sum(1 for pl in pls if not isinstance(pl.get("joint"), dict)) <= 1, "placed without a joint (only the root may be)"
    for p in model:
        left = list(want.get(spell(p["id"]), []))
        for pl in p.get("placements") or []:
            got = mat(pl["loc"])
            best = min(range(len(left)), key=lambda i: mdiff(left[i], got), default=None)
            assert best is not None, f"{p['id']}: more placements than source instances"
            assert mdiff(left[best], got) <= 1e-6, f"{pl['name']}: {mdiff(left[best], got):.3g} mm off its source instance"
            left.pop(best)
        assert not left, f"{p['id']}: source instances not placed: {left}"
