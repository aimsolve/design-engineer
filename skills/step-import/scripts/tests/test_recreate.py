#!/usr/bin/env python3
"""Tests for scripts/recreate.py: a made part of a client STEP (one solid, in its own coordinates) is recreated as parametric
build123d code, and the code is checked against the source geometry.

Run (build123d 0.13.0, the version the VPS and the project containers use):
  uv venv --python 3.12 <venv> && uv pip install --python <venv>/bin/python 'build123d==0.13.0' pytest trimesh numpy
  cd agent/skills/step-import/scripts/tests && <venv>/bin/python -m pytest -q test_recreate.py

Contract under test (from the ticket, not from the code):
  recreate(shape, name) -> {"kind": "sheet" | "turned" | "prism" | None,
                            "code": a complete Python module text that defines build(**params) and returns a build123d Part/Solid
                                    in the SAME coordinates as `shape`, using build123d only (no file reads, no OCP),
                            "params": the named numbers build() takes as keyword defaults (sheet: "thickness"; turned:
                                      diameters / lengths),
                            "reason": why it could not be recreated (set when kind is None)}
  compare(ref, got) -> {"volume_rel": |dV| / V(ref), "bbox_mm": max abs difference of the bbox corners,
                        "sym_diff_rel": V(ref XOR got) / V(ref), "holes": {"ref": n, "got": n} cylindrical through/blind holes,
                        "ok": all of the above within the module-level TOL dict and equal hole counts}

Every shape here is synthetic, built with build123d in the test; no client file is read. Each "compare ok" assertion is backed by
an independent volume / bbox check made here (0.5 % volume, 0.05 mm bbox), so a loose TOL cannot make a wrong recreation pass.
"""
import ast
import inspect
import sys
from pathlib import Path

import pytest
from build123d import Align, Axis, Box, BuildLine, BuildSketch, Circle, Cylinder, Part, Plane, Polyline, Pos, Rectangle, Rot, \
    Solid, loft, make_face, revolve

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

MIN = (Align.MIN, Align.MIN, Align.MIN)
VOL_REL = 0.005      # independent checks, the contract's example tolerances
BBOX_MM = 0.05
ALLOWED_IMPORTS = {"build123d", "math"}
FORBIDDEN_NAMES = {"open", "import_step", "import_brep", "import_stl", "import_svg", "exec", "eval", "__import__"}


def R():
    """The module under test, imported inside each test so a missing module fails every test (not one collection error)."""
    import recreate
    return recreate


# ---------------------------------------------------------------- synthetic parts

def _bend(s, x, z, r):
    """Fillet the one edge parallel to Y at (x, z): the inner (r) or outer (r + t) side of a sheet bend."""
    es = s.edges().filter_by(Axis.Y).filter_by(lambda e: abs(e.center().X - x) < 1e-6 and abs(e.center().Z - z) < 1e-6)
    assert len(es) == 1, f"test shape: expected one bend edge at x={x} z={z}, got {len(es)}"
    return s.fillet(r, es)


def l_bracket(t=2.0, r=2.0):
    """Bent sheet L: 40 x 25 base leg, 30 high wall, thickness t, inner bend radius r, 2 holes d5 in the base and 1 hole d6 in the
    wall. Moved off the origin to (5, -3, 7) so 'same coordinates' is tested."""
    s = Box(40, 25, t, align=MIN) + Box(t, 25, 30, align=MIN)
    s = _bend(s, t, t, r)
    s = _bend(s, 0, 0, r + t)
    s -= Pos(25, 8, 0) * Cylinder(2.5, 10)
    s -= Pos(25, 18, 0) * Cylinder(2.5, 10)
    s -= Pos(0, 12.5, 20) * Rot(0, 90, 0) * Cylinder(3, 10)
    return Pos(5, -3, 7) * s


def u_bracket(t=2.0, r=2.0):
    """Bent sheet U: 50 wide base, two 25 high walls, 20 deep, thickness t, inner bend radius r, one hole d6 in the base."""
    s = Box(50, 20, t, align=MIN) + Box(t, 20, 25, align=MIN) + Pos(50 - t, 0, 0) * Box(t, 20, 25, align=MIN)
    s = _bend(s, t, t, r)
    s = _bend(s, 50 - t, t, r)
    s = _bend(s, 0, 0, r + t)
    s = _bend(s, 50, 0, r + t)
    s -= Pos(25, 10, 0) * Cylinder(3, 10)
    return Pos(-25, 0, 0) * s


def _revolve_xz(pts, axis):
    with BuildSketch(Plane.XZ) as sk:
        with BuildLine():
            Polyline(*pts, close=True)
        make_face()
    return revolve(sk.sketch.faces()[0], axis)


def stepped_shaft():
    """Turned: d20 x 30 then d12 x 40, 1 x 45 deg chamfers at both ends; axis along X, moved to (10, 5, -3)."""
    pts = [(0, 0), (0, 9), (1, 10), (30, 10), (30, 6), (69, 6), (70, 5), (70, 0)]
    return Pos(10, 5, -3) * _revolve_xz(pts, Axis.X)


def bushing():
    """Turned: OD 30, bore 16, length 25, axis Z, 1 x 45 deg chamfer on the outer bottom edge."""
    return _revolve_xz([(8, 0), (14, 0), (15, 1), (15, 25), (8, 25)], Axis.Z)


PLATE_HOLES = [(8, 8), (72, 8), (8, 42), (72, 42)]


def plate(t=3.0, extra_hole=False):
    """Flat 80 x 50 x t plate with 4 through holes d6, corner at (10, 20, 5) (thickness along Z)."""
    p = Pos(10, 20, 5) * Box(80, 50, t, align=MIN)
    for x, y in PLATE_HOLES:
        p -= Pos(10 + x, 20 + y, 0) * Cylinder(3, 30)
    if extra_hole:
        p -= Pos(50, 45, 0) * Cylinder(4, 30)
    return p


def lofted():
    """Free form: a loft from a circle d20 to an offset 12 x 6 rectangle 30 above. Not a sheet, not turned, not a prism."""
    return loft([Plane.XY * Circle(10), Plane.XY.offset(30) * Pos(5, 3) * Rectangle(12, 6)])


# ---------------------------------------------------------------- helpers

def _check_code(code):
    """The recreated module is self-contained build123d: imports only build123d / math, reads no file, loads no foreign geometry."""
    assert isinstance(code, str) and code.strip(), "recreate() returned no code"
    tree = ast.parse(code)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods = [a.name.split(".")[0] for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            mods = [(n.module or "").split(".")[0]]
        else:
            mods = []
        bad = [m for m in mods if m not in ALLOWED_IMPORTS]
        assert not bad, f"recreated code imports {bad}: only build123d (and math) are allowed\n{code}"
        if isinstance(n, ast.Name) and n.id in FORBIDDEN_NAMES:
            raise AssertionError(f"recreated code uses {n.id!r}: it must build the part, not load it\n{code}")
        if isinstance(n, ast.Attribute) and n.attr in FORBIDDEN_NAMES:
            raise AssertionError(f"recreated code uses .{n.attr}: it must build the part, not load it\n{code}")
    assert any(isinstance(n, ast.FunctionDef) and n.name == "build" for n in tree.body), f"no top-level build() in\n{code}"


def _load_build(res):
    _check_code(res["code"])
    ns = {"__name__": "recreated_part"}
    exec(compile(res["code"], "<recreated>", "exec"), ns)
    return ns["build"]


def _bbox(s):
    b = s.bounding_box()
    return (b.min.X, b.min.Y, b.min.Z), (b.max.X, b.max.Y, b.max.Z)


def _bbox_diff(a, b):
    (a0, a1), (b0, b1) = _bbox(a), _bbox(b)
    return max(abs(x - y) for x, y in zip(a0 + a1, b0 + b1))


def _same_geometry(ref, got, what):
    """Independent of compare(): volume within 0.5 %, bbox corners within 0.05 mm, the overlap is (nearly) all of it."""
    assert isinstance(got, (Part, Solid)), f"{what}: build() must return a build123d Part/Solid, got {type(got)}"
    dv = abs(got.volume - ref.volume) / ref.volume
    assert dv <= VOL_REL, f"{what}: volume {got.volume:.3f} vs source {ref.volume:.3f} ({dv:.4%})"
    db = _bbox_diff(ref, got)
    assert db <= BBOX_MM, f"{what}: bbox differs by {db:.4f} mm: source {_bbox(ref)}, recreated {_bbox(got)} (same coordinates?)"
    common = (ref & got).volume
    assert common >= ref.volume * (1 - 0.01), f"{what}: only {common / ref.volume:.2%} of the source is covered by the recreation"


def _params_are_build_defaults(build, params):
    sig = inspect.signature(build)
    for k, v in params.items():
        assert k in sig.parameters, f"param {k!r} is not a keyword of build{sig}"
        d = sig.parameters[k].default
        assert d is not inspect.Parameter.empty, f"build() param {k!r} has no default"
        assert d == pytest.approx(v), f"build() default {k}={d!r}, params says {v!r}"
    build()           # every default given: build() runs with no arguments


def _recreate_ok(shape, name, kinds):
    m = R()
    res = m.recreate(shape, name)
    assert set(res) >= {"kind", "code", "params", "reason"}, f"recreate() keys: {sorted(res)}"
    assert res["kind"] in kinds, f"{name}: kind {res['kind']!r}, expected one of {kinds} (reason: {res.get('reason')!r})"
    assert isinstance(res["params"], dict) and res["params"], f"{name}: no named params: {res['params']!r}"
    build = _load_build(res)
    _params_are_build_defaults(build, res["params"])
    got = build()
    c = m.compare(shape, got)
    assert c["ok"] is True, f"{name}: compare() of the recreation against the source is not ok: {c}"
    _same_geometry(shape, got, name)
    return res, build, got, c


# ---------------------------------------------------------------- sheet

@pytest.mark.parametrize("make,holes", [(l_bracket, 3), (u_bracket, 1)], ids=["L", "U"])
def test_bent_sheet_bracket_is_recreated_as_sheet_with_thickness(make, holes):
    """A bent sheet bracket (constant thickness 2, bend radius 2, holes) -> kind "sheet", params has thickness ~2, the exec'd code
    builds the same solid in the same coordinates, and the bend fillets are not counted as holes."""
    shape = make()
    res, build, got, c = _recreate_ok(shape, f"bracket_{make.__name__}", {"sheet"})
    assert "thickness" in res["params"], f"sheet params without thickness: {res['params']}"
    assert res["params"]["thickness"] == pytest.approx(2.0, abs=0.01)
    assert c["holes"] == {"ref": holes, "got": holes}, f"hole count (bend fillets are not holes): {c['holes']}"


def test_bent_sheet_thickness_param_changes_the_part():
    """thickness is a live parameter, not a label: building with another thickness gives another solid."""
    shape = l_bracket()
    res = R().recreate(shape, "bracket")
    assert res["kind"] == "sheet", res.get("reason")
    build = _load_build(res)
    v2 = build().volume
    v3 = build(**{**res["params"], "thickness": 3.0}).volume
    assert v3 > v2 * 1.3, f"thickness 2 -> 3 should grow the volume by ~50 %: {v2:.1f} -> {v3:.1f}"


# ---------------------------------------------------------------- turned

@pytest.mark.parametrize("make", [stepped_shaft, bushing], ids=["stepped_shaft", "bushing"])
def test_revolved_part_with_chamfer_is_recreated_as_turned(make):
    """A part made by revolve (stepped shaft along X off the origin; bushing with a bore), each with a chamfer -> kind "turned",
    params name its diameters / lengths, and the code builds the same solid in the same coordinates."""
    shape = make()
    res, build, got, c = _recreate_ok(shape, make.__name__, {"turned"})
    nums = [v for v in res["params"].values() if isinstance(v, (int, float))]
    assert len(nums) >= 2, f"a turned part names at least a diameter and a length: {res['params']}"


# ---------------------------------------------------------------- flat plate

def test_flat_plate_with_holes_is_recreated_and_thickness_is_a_parameter():
    """A flat 80 x 50 x 3 plate with 4 holes -> "sheet" or "prism", compare ok; build(thickness=5) gives a 5 mm plate of the same
    outline (extent along the thin axis 5, the other two unchanged, volume x 5/3)."""
    shape = plate()
    res, build, got, c = _recreate_ok(shape, "plate", {"sheet", "prism"})
    assert "thickness" in res["params"], f"plate params without thickness: {res['params']}"
    assert res["params"]["thickness"] == pytest.approx(3.0, abs=0.01)
    assert c["holes"] == {"ref": 4, "got": 4}, c["holes"]
    thick = build(**{**res["params"], "thickness": 5.0})
    (x0, y0, z0), (x1, y1, z1) = _bbox(thick)
    assert z1 - z0 == pytest.approx(5.0, abs=0.01), f"thickness 5 asked, Z extent {z1 - z0:.3f}"
    assert x1 - x0 == pytest.approx(80.0, abs=BBOX_MM) and y1 - y0 == pytest.approx(50.0, abs=BBOX_MM), _bbox(thick)
    assert thick.volume == pytest.approx(shape.volume * 5 / 3, rel=0.01)


# ---------------------------------------------------------------- not recreatable

def test_free_form_loft_is_not_recreated_and_says_why():
    """A lofted / free-form solid -> kind None with a non-empty reason (it stays client geometry)."""
    res = R().recreate(lofted(), "blob")
    assert res["kind"] is None, f"a loft is not sheet/turned/prism: kind {res['kind']!r}"
    assert isinstance(res["reason"], str) and res["reason"].strip(), f"no reason given: {res['reason']!r}"


# ---------------------------------------------------------------- compare

def test_tol_is_a_module_level_dict_of_the_compared_measures():
    tol = R().TOL
    assert isinstance(tol, dict)
    for k in ("volume_rel", "bbox_mm", "sym_diff_rel"):
        assert isinstance(tol.get(k), (int, float)) and tol[k] > 0, f"TOL[{k!r}] = {tol.get(k)!r}"


def test_compare_shape_with_itself_is_ok():
    p = plate()
    c = R().compare(p, p)
    assert set(c) >= {"volume_rel", "bbox_mm", "sym_diff_rel", "holes", "ok"}, sorted(c)
    assert c["ok"] is True, c
    assert c["volume_rel"] == pytest.approx(0, abs=1e-9)
    assert c["bbox_mm"] == pytest.approx(0, abs=1e-6)
    assert c["sym_diff_rel"] == pytest.approx(0, abs=1e-6)
    assert c["holes"] == {"ref": 4, "got": 4}


def test_compare_detects_one_extra_hole():
    """Same plate plus one d8 hole: not ok, and the hole counts say 4 vs 5."""
    c = R().compare(plate(), plate(extra_hole=True))
    assert c["ok"] is False, c
    assert c["holes"] == {"ref": 4, "got": 5}, c["holes"]
    assert c["sym_diff_rel"] > 0.005, c


def test_compare_detects_a_shift_with_equal_volume():
    """The same plate moved 0.2 mm along X has the same volume but is not the same part in the same place."""
    ref = plate()
    c = R().compare(ref, Pos(0.2, 0, 0) * ref)
    assert c["ok"] is False, c
    assert c["volume_rel"] == pytest.approx(0, abs=1e-6)
    assert c["bbox_mm"] == pytest.approx(0.2, abs=0.01), c
    assert c["sym_diff_rel"] > 0, c
