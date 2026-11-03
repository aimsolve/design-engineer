#!/usr/bin/env python3
"""Bends whose ends are cut across the turn: template `bend(..., ends=...)` and their recognition by recreate.py.

Run (build123d 0.13.0):
  <venv>/bin/python -m pytest -q agent/skills/step-import/scripts/tests/test_recreate_bend_ends.py

Contract under test (from the spec, not from the code):
  template bend(pl, r, t, angle, length, convex=True, trim=(0, 0), ends=None)
    ends=(start, end), each end one of
      (a, b, c)                 at turn theta (radians, 0..angle about pl's +Z from pl's +X) the end lies at
                                w = a + b cos(theta) + c sin(theta) along pl's Z, on the inner and the outer radius alike;
      (a, b, c, a2, b2, c2)     the first triple on the inner radius, the second on the outer one;
      ("pts", [w_in...], [w_out...])  w at n + 1 evenly spaced turns from 0 to angle, inner and outer.
    With ends given, `length` and `trim` are ignored. Calls without ends build as before.
    convex=True: material between r - t and r; convex=False: between r and r + t.
  recreate(shape, name): a bent sheet whose bend ends are cut by a slanted cut (two slanted panel ends with the bend end
    A + B cos + C sin between them; a plane through the bend; a free-form end) comes back as kind "sheet", compare ok, code
    with `ends=`. Square and helix-trimmed bends keep the code the plain path gave (pinned: the base commit BASE_COMMIT),
    without `ends=`. A bend end with an axial jog is not drawn with `ends=`: kind None, or a correct match.

Every shape is synthetic. Each "compare ok" is backed by an independent volume / bbox / overlap check made here.
"""
import ast
import importlib.util
import math
import subprocess
import sys
from pathlib import Path

import pytest
from build123d import Align, Axis, Box, Face, Part, Pos, Rot, Solid, Vector, Wire, export_step, extrude, import_step

SCRIPTS = Path(__file__).resolve().parent.parent
TEMPLATES = SCRIPTS.parent.parent / "machine-project" / "templates"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TEMPLATES))

BASE_COMMIT = "30ecd6bfa07d0dc5d9df66ddaa4552f91c414f44"     # on main, before bend ends existed: the "plain path" the old parts must keep
MIN = (Align.MIN, Align.MIN, Align.MIN)
VOL_REL = 0.005
BBOX_MM = 0.05
ALLOWED_IMPORTS = {"build123d", "math"}
FORBIDDEN_NAMES = {"open", "import_step", "import_brep", "import_stl", "import_svg", "exec", "eval", "__import__"}
END_MARK = "# --- end recreated.py ---"


def R():
    """recreate.py, imported per test so a broken module fails each test, not the collection."""
    import recreate
    return recreate


def T():
    """The framework template recreated.py (frame, panel, bend, sheet)."""
    import recreated
    return recreated


# ---------------------------------------------------------------- the base commit (the plain path before bend ends)

def _git_show(rel):
    """The file at BASE_COMMIT. Outside a git checkout the comparison cannot run (skip); inside one, a missing commit is a
    failure: the regression guard must not go quiet."""
    try:
        top = subprocess.run(["git", "-C", str(SCRIPTS), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout: cannot compare with the plain path")
    r = subprocess.run(["git", "-C", top, "show", f"{BASE_COMMIT}:{rel}"], capture_output=True, text=True)
    if r.returncode:
        pytest.fail(f"base commit {BASE_COMMIT} is not in this checkout (a shallow clone?): {r.stderr.strip()}")
    return r.stdout


def _base_modules(tmp_path):
    """recreate.py and recreated.py as they were at BASE_COMMIT, loaded under their own names, side by side as in the repo."""
    rec = tmp_path / "base" / "step-import" / "scripts" / "recreate.py"
    tpl = tmp_path / "base" / "machine-project" / "templates" / "recreated.py"
    rec.parent.mkdir(parents=True)
    tpl.parent.mkdir(parents=True)
    rec.write_text(_git_show("agent/skills/step-import/scripts/recreate.py"), encoding="utf-8")
    tpl.write_text(_git_show("agent/skills/machine-project/templates/recreated.py"), encoding="utf-8")
    saved_path, saved_mod = list(sys.path), sys.modules.get("recreated")
    try:
        mods = []
        for name, path in (("recreate_base", rec), ("recreated_base", tpl)):
            spec = importlib.util.spec_from_file_location(name, path)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            mods.append(m)
        return mods
    finally:
        sys.path[:] = saved_path
        if saved_mod is not None:
            sys.modules["recreated"] = saved_mod


def _base_recreate(base_rec, shape, name):
    saved_path, saved_mod = list(sys.path), sys.modules.get("recreated")
    try:
        return base_rec.recreate(shape, name)
    finally:
        sys.path[:] = saved_path
        if saved_mod is not None:
            sys.modules["recreated"] = saved_mod
        else:
            sys.modules.pop("recreated", None)


# ---------------------------------------------------------------- geometry helpers

def _bbox(s):
    b = s.bounding_box()
    return (b.min.X, b.min.Y, b.min.Z), (b.max.X, b.max.Y, b.max.Z)


def _bbox_diff(a, b):
    (a0, a1), (b0, b1) = _bbox(a), _bbox(b)
    return max(abs(x - y) for x, y in zip(a0 + a1, b0 + b1))


def _one_solid(s, what):
    solids = s.solids()
    assert len(solids) == 1, f"{what}: expected one solid, got {len(solids)}"
    return solids[0]


def _common_volume(a, b):
    """V(a AND b); when the plain boolean gives up (a STEP-read B-spline face against a lofted one): V(a) + V(b) - V(a OR b),
    the union fused with a 1 micron tolerance."""
    r = a & b
    if r is not None:
        return r.volume
    u = a.fuse(b, tol=1e-3)
    assert u is not None, "test helper: neither the common nor the fuse of source and recreation could be computed"
    return a.volume + b.volume - u.volume


def _same_geometry(ref, got, what):
    """Independent of compare(): volume within 0.5 %, bbox corners within 0.05 mm, the overlap is (nearly) all of it."""
    assert isinstance(got, (Part, Solid)), f"{what}: build() must return a build123d Part/Solid, got {type(got)}"
    dv = abs(got.volume - ref.volume) / ref.volume
    assert dv <= VOL_REL, f"{what}: volume {got.volume:.3f} vs source {ref.volume:.3f} ({dv:.4%})"
    db = _bbox_diff(ref, got)
    assert db <= BBOX_MM, f"{what}: bbox differs by {db:.4f} mm: source {_bbox(ref)}, recreated {_bbox(got)}"
    common = _common_volume(ref, got)
    assert common >= ref.volume * (1 - 0.01), f"{what}: only {common / ref.volume:.2%} of the source is covered by the recreation"


def _check_code(code):
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
        assert not bad, f"recreated code imports {bad}\n{code}"
        if isinstance(n, ast.Name) and n.id in FORBIDDEN_NAMES:
            raise AssertionError(f"recreated code uses {n.id!r}\n{code}")
        if isinstance(n, ast.Attribute) and n.attr in FORBIDDEN_NAMES:
            raise AssertionError(f"recreated code uses .{n.attr}\n{code}")


def _build(res):
    _check_code(res["code"])
    ns = {"__name__": "recreated_part"}
    exec(compile(res["code"], "<recreated>", "exec"), ns)
    return ns["build"]()


def _part_code(code):
    """The part's own code: what follows the inlined template (the template text itself changed with this feature)."""
    return code.split(END_MARK, 1)[1] if END_MARK in code else code


def _recognised_sheet(shape, name):
    """recreate -> kind "sheet", compare ok, and the built code matches the source by the independent checks here."""
    m = R()
    res = m.recreate(shape, name)
    assert res["kind"] == "sheet", f"{name}: kind {res['kind']!r}, expected 'sheet'; reason: {res.get('reason')!r}"
    got = _build(res)
    c = m.compare(shape, got)
    assert c["ok"] is True, f"{name}: compare() of the recreation against the source is not ok: {c}"
    _same_geometry(shape, got, name)
    return res


def _step_roundtrip(shape, tmp_path, name):
    """The part as a client sends it: written to STEP and read back (B-spline-free planes/cylinders survive, lofts don't)."""
    path = tmp_path / f"{name}.step"
    assert export_step(shape, str(path)), f"export_step failed for {name}"
    return _one_solid(import_step(str(path)), f"{name} re-imported")


def _at(pl, rho, theta_deg, w):
    """The point at radius rho, turn theta (degrees about pl's +Z from pl's +X) and w along pl's Z."""
    a = math.radians(theta_deg)
    return pl.origin + pl.x_dir * (rho * math.cos(a)) + pl.y_dir * (rho * math.sin(a)) + pl.z_dir * w


def _sector_volume(ri, ro, angle_deg, w_in, w_out=None, n=2000):
    """Volume between radii ri..ro over the turn 0..angle, between w = 0 and the end w(theta), linear in rho between the
    inner end w_in(theta) and the outer end w_out(theta) (midpoint rule; the other end is at w = 0)."""
    w_out = w_out or w_in
    A = math.radians(angle_deg)
    i1 = (ro ** 2 - ri ** 2) / 2                                               # int rho drho
    i2 = (ro ** 3 / 3 - ri * ro ** 2 / 2) - (ri ** 3 / 3 - ri ** 3 / 2)        # int (rho - ri) rho drho
    v = 0.0
    for k in range(n):
        th = A * (k + 0.5) / n
        wi, wo = w_in(th), w_out(th)
        v += (wi * i1 + (wo - wi) / (ro - ri) * i2) * A / n
    return v


# ---------------------------------------------------------------- template bend(ends=...)

# a bend frame off the origin and off the global axes: axis along +Y, turn starts at +Z
PL_ARGS = ((1.0, 2.0, 3.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))


def test_bend_ends_one_triple_is_the_cos_sin_end_on_both_radii():
    """ends=((0, 0, 0), (10, 0, 5)): square start at w=0, far end at w = 10 + 5 sin(theta) on both radii. Volume and bbox are
    the analytic ones, and points just inside / outside the far end at several turns say the end follows the formula."""
    t = T()
    pl = t.frame(*PL_ARGS)
    r, th, ang = 4.0, 2.0, 90.0
    b = t.bend(pl, r, th, ang, 7.0, ends=((0, 0, 0), (10, 0, 5)))
    s = _one_solid(b, "bend")
    w = lambda a: 10 + 5 * math.sin(a)
    v = _sector_volume(r - th, r, ang, w)
    assert s.volume == pytest.approx(v, rel=VOL_REL), f"volume {s.volume:.3f}, analytic {v:.3f}"
    for deg in (2, 30, 60, 88):
        we = w(math.radians(deg))
        for rho in (r - th + 0.2, r - 0.2):
            assert s.is_inside(_at(pl, rho, deg, we - 0.3)), f"turn {deg}, rho {rho}: w {we - 0.3:.2f} should be inside"
            assert not s.is_inside(_at(pl, rho, deg, we + 0.3)), f"turn {deg}, rho {rho}: w {we + 0.3:.2f} should be outside"
    zs = [v_.Y for v_ in s.vertices()]       # pl's Z is global +Y, origin y = 2
    assert min(zs) == pytest.approx(2.0, abs=0.01) and max(zs) == pytest.approx(2.0 + 15.0, abs=0.01), \
        f"w range of the bend {min(zs) - 2:.3f}..{max(zs) - 2:.3f}, expected 0..15"


@pytest.mark.parametrize("convex", [True, False], ids=["convex", "concave"])
def test_bend_ends_two_triples_differ_on_inner_and_outer_radius(convex):
    """ends=((0,)*6, (10, 0, 0, 10, 2, 0)): far end at w = 10 on the inner radius and 10 + 2 cos(theta) on the outer one
    (a plane cut through the bend). Inner radius is r - t when convex, r when not."""
    t = T()
    pl = t.frame(*PL_ARGS)
    r, th, ang = 4.0, 2.0, 90.0
    ri = r - th if convex else r
    ro = ri + th
    b = t.bend(pl, r, th, ang, 3.0, convex=convex, ends=((0, 0, 0, 0, 0, 0), (10, 0, 0, 10, 2, 0)))
    s = _one_solid(b, "bend")
    v = _sector_volume(ri, ro, ang, lambda a: 10.0, lambda a: 10 + 2 * math.cos(a))
    assert s.volume == pytest.approx(v, rel=0.01), f"volume {s.volume:.3f}, analytic {v:.3f}"
    # at turn 10 deg: near the outer radius the end is at ~11.97, near the inner one at 10
    assert s.is_inside(_at(pl, ro - 0.05, 10, 11.5)), "near the outer radius the end must reach past w = 11.5"
    assert not s.is_inside(_at(pl, ri + 0.05, 10, 10.5)), "near the inner radius the end must stop at w = 10"
    assert s.is_inside(_at(pl, ri + 0.05, 10, 9.7)), "near the inner radius w = 9.7 is material"
    # at turn 85 deg cos ~ 0: both radii end near 10
    assert not s.is_inside(_at(pl, ro - 0.05, 85, 10.6)), "at turn 85 the outer end is ~10.17, w = 10.6 is outside"


def test_bend_ends_pts_match_the_same_curve_given_as_a_triple():
    """("pts", w_in, w_out) sampled from 10 + 5 sin(theta) at 19 even turns builds (nearly) the solid the triple builds."""
    t = T()
    pl = t.frame(*PL_ARGS)
    n = 18
    ws = [10 + 5 * math.sin(math.radians(90.0 * k / n)) for k in range(n + 1)]
    a = _one_solid(t.bend(pl, 4.0, 2.0, 90.0, 1.0, ends=((0, 0, 0), (10, 0, 5))), "triple")
    b = _one_solid(t.bend(pl, 4.0, 2.0, 90.0, 1.0, ends=(("pts", [0.0] * (n + 1), [0.0] * (n + 1)), ("pts", ws, ws))), "pts")
    assert b.volume == pytest.approx(a.volume, rel=VOL_REL), f"pts volume {b.volume:.3f} vs triple {a.volume:.3f}"
    sym = a.volume + b.volume - 2 * (a & b).volume
    assert sym <= 0.01 * a.volume, f"pts and triple differ by {sym:.3f} mm3 ({sym / a.volume:.2%})"
    assert _bbox_diff(a, b) <= BBOX_MM


def test_bend_ends_given_ignore_length_and_trim():
    """With ends, `length` and `trim` change nothing."""
    t = T()
    pl = t.frame(*PL_ARGS)
    e = ((1, 0, 0), (12, -2, 3))
    a = _one_solid(t.bend(pl, 3.0, 1.5, 70.0, 5.0, ends=e), "a")
    b = _one_solid(t.bend(pl, 3.0, 1.5, 70.0, 99.0, trim=(4, 6), ends=e), "b")
    assert b.volume == pytest.approx(a.volume, rel=1e-6), f"length/trim changed the volume: {a.volume:.4f} -> {b.volume:.4f}"
    assert _bbox_diff(a, b) <= 1e-6


@pytest.mark.parametrize("trim", [(0, 0), (3.0, 2.0)], ids=["plain", "trim"])
def test_bend_without_ends_builds_as_before(trim, tmp_path):
    """Old calls (no ends, or ends=None) build as before: the analytic volume / bbox, and the same as the base-commit template.
    (A regression guard: it passes before the feature by design.)"""
    t = T()
    pl = t.frame(*PL_ARGS)
    r, th, ang, L = 4.0, 2.0, 90.0, 20.0
    kw = {} if trim == (0, 0) else {"trim": trim}
    a = _one_solid(t.bend(pl, r, th, ang, L, **kw), "no ends")
    b = _one_solid(t.bend(pl, r, th, ang, L, ends=None, **kw), "ends=None")
    v = _sector_volume(r - th, r, ang, lambda x: L - trim[1] * x / math.radians(ang)) - \
        _sector_volume(r - th, r, ang, lambda x: trim[0] * x / math.radians(ang))
    assert a.volume == pytest.approx(v, rel=VOL_REL), f"volume {a.volume:.3f}, analytic {v:.3f}"
    assert b.volume == pytest.approx(a.volume, rel=1e-9) and _bbox_diff(a, b) <= 1e-9
    _, base_tpl = _base_modules(tmp_path)
    c = _one_solid(base_tpl.bend(base_tpl.frame(*PL_ARGS), r, th, ang, L, **kw), "base template")
    assert a.volume == pytest.approx(c.volume, rel=1e-6), f"volume {a.volume:.4f}, base template {c.volume:.4f}"
    assert _bbox_diff(a, c) <= 1e-6, f"bbox {_bbox(a)} vs base template {_bbox(c)}"


# ---------------------------------------------------------------- synthetic sheet parts with cut bend ends

def _fillet_y(s, x, z, r):
    es = s.edges().filter_by(Axis.Y).filter_by(lambda e: abs(e.center().X - x) < 1e-6 and abs(e.center().Z - z) < 1e-6)
    assert len(es) == 1, f"test shape: expected one bend edge at x={x} z={z}, got {len(es)}"
    return s.fillet(r, es)


def l_sheet(t=2.0, r=2.0, depth=25.0):
    """Bent sheet L, bend axis along Y: base 40 long (x) at z 0..t, wall 30 high (z) at x 0..t, y 0..depth, inner radius r."""
    s = Box(40, depth, t, align=MIN) + Box(t, depth, 30, align=MIN)
    s = _fillet_y(s, t, t, r)
    return _fillet_y(s, 0, 0, r + t)


def l_plane_cut(rot_z=25.0, rot_x=0.0):
    """The L sheet with its far end (y ~ 18) cut by one oblique plane: a big box whose -Y face passes through (20, 18, 15),
    turned rot_z degrees about Z (the base panel's normal) and rot_x about X. The cut crosses the base, the bend and the wall:
    the bend end is a plane section, different on the inner and the outer radius."""
    cutter = Pos(20, 18, 15) * Rot(rot_x, 0, rot_z) * Box(400, 200, 400, align=(Align.CENTER, Align.MIN, Align.CENTER))
    return _one_solid(l_sheet() - cutter, "plane-cut L")


def _prism(pts, d):
    """A closed planar polygon (3D points) extruded by the vector d."""
    f = Face(Wire.make_polygon([Vector(*p) for p in pts], close=True))
    return extrude(f, amount=Vector(*d).length, dir=Vector(*d))


def l_cut_end(W, base_far, wall_far, n=10):
    """The client's way: the L sheet (true cylinder bend faces; t 2, inner radius 2, bend axis Y, centre (4, *, 4)) made 45
    deep, then its far end cut by three tools that meet at the bend's two edges:
      base panel: the line from (x 4, y W(0)) to (x 40, y base_far), square through the thickness;
      wall panel: the line from (z 4, y W(90)) to (z 30, y wall_far), square through the thickness;
      bend: y = W(theta) on every radius, theta from the base (-Z) to the wall (-X) about the bend axis (a smooth loft through
      radial sections, i.e. a B-spline end face, as a client's CAD writes it)."""
    s = l_sheet(depth=45.0)
    w0, w90 = W(0.0), W(math.pi / 2)
    kb, kw = (base_far - w0) / 36, (wall_far - w90) / 26
    base_c = _prism([(4, w0, -5), (4, 80, -5), (50, 80, -5), (50, w0 + kb * 46, -5)], (0, 0, 10))
    wall_c = _prism([(-5, w90, 4), (-5, 80, 4), (-5, 80, 40), (-5, w90 + kw * 36, 40)], (10, 0, 0))
    secs = []
    for k in range(n + 1):
        th = math.pi / 2 * k / n
        c, d, w = Vector(4, 0, 4), Vector(-math.sin(th), 0, -math.cos(th)), W(th)
        secs.append(Wire.make_polygon([c + d * 0.5 + Vector(0, w, 0), c + d * 7 + Vector(0, w, 0),
                                       c + d * 7 + Vector(0, 80, 0), c + d * 0.5 + Vector(0, 80, 0)], close=True))
    return _one_solid(s - base_c - wall_c - Solid.make_loft(secs), "cut-end L")


# SolidWorks-style ends: two panels whose ends slant in their planes, the bend end between them w = A + B cos + C sin (the
# same on both radii). w(0) = 28 at the base, w(90) = 23 at the wall, w(45) = 27.8 where a helix between them gives 25.5.
SW_END = (20.0, 8.0, 3.0)


def l_slanted_ends_sw():
    a, b, c = SW_END
    return l_cut_end(lambda th: a + b * math.cos(th) + c * math.sin(th), 38.8, 17.8)


def l_free_form_end():
    """Square panel ends at y = 25, the bend end bulging to w = 25 + 2 sin(2 theta): no a + b cos + c sin curve fits it."""
    return l_cut_end(lambda th: 25 + 2 * math.sin(2 * th), 25.0, 25.0)


def l_helix_trim():
    """A helix-trimmed bend (the plain path's trim): base ends at y = 25, wall at y = 22, the bend end y = 25 - 3 theta/90deg."""
    return l_cut_end(lambda th: 25 - 3 * th / (math.pi / 2), 25.0, 22.0)


def l_template_ends():
    """The same SolidWorks-style L made from the template itself: panels + bend(ends=((0, 0, 0), SW_END)), fused by sheet()."""
    t = T()
    a, b, c = SW_END
    base = _prism([(4, 0, 0), (40, 0, 0), (40, 38.8, 0), (4, a + b, 0)], (0, 0, 2))
    wall = _prism([(0, 0, 4), (0, 0, 30), (0, 17.8, 30), (0, a + c, 4)], (2, 0, 0))
    pl = t.frame((4.0, 0.0, 4.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))      # turn 0 at the base (-Z), 90 at the wall (-X)
    bend = t.bend(pl, 4.0, 2.0, 90.0, 1.0, convex=True, ends=((0, 0, 0), SW_END))
    return _one_solid(t.sheet([base, bend, wall]), "template-made L")


def l_axial_jog():
    """The L sheet (y 0..25) with the bend's far end stepping along the axis at turn 45: the half-space z > x (through the bend
    axis at 45 deg, holding the wall) is cut back from y = 20. Base ends at 25, wall at 20, bend 25 then 20."""
    cutter = _prism([(-60, 20, -60), (60, 20, 60), (-60, 20, 60)], (0, 20, 0))
    return _one_solid(l_sheet() - cutter, "axial-jog L")


def u_sheet(t=2.0, r=2.0):
    s = Box(50, 20, t, align=MIN) + Box(t, 20, 25, align=MIN) + Pos(50 - t, 0, 0) * Box(t, 20, 25, align=MIN)
    s = _fillet_y(s, t, t, r)
    s = _fillet_y(s, 50 - t, t, r)
    s = _fillet_y(s, 0, 0, r + t)
    return Pos(-25, 0, 0) * _fillet_y(s, 50, 0, r + t)


# ---------------------------------------------------------------- recognition

@pytest.mark.xfail(strict=True, reason="one plane through a bend slants a panel's end across its thickness, which panel() "
                                       "cannot draw; the bend ends alone are not enough (follow-up: panels with slanted ends)")
@pytest.mark.parametrize("rot", [(25.0, 0.0), (20.0, 15.0)], ids=["about-base-normal", "compound-tilt"])
def test_plane_cut_through_the_bend_is_recognised_with_ends(rot):
    """Condition 2 (plane cut, as at a mitred corner): the L sheet cut by one oblique plane through both panels and the bend
    -> kind "sheet", compare ok, the code draws the bend with ends=."""
    shape = l_plane_cut(*rot)
    res = _recognised_sheet(shape, "plane_cut_l")
    assert "ends=" in _part_code(res["code"]), f"the cut bend is not drawn with ends=:\n{_part_code(res['code'])}"


def test_solidworks_slanted_panel_ends_from_step_are_recognised_with_ends(tmp_path):
    """Condition 2 (SolidWorks): two panels with slanted ends and the bend end A + B cos + C sin between them, written to STEP
    and read back -> kind "sheet", compare ok, ends= in the code."""
    shape = _step_roundtrip(l_slanted_ends_sw(), tmp_path, "sw_l")
    res = _recognised_sheet(shape, "sw_l")
    assert "ends=" in _part_code(res["code"]), f"the cut bend is not drawn with ends=:\n{_part_code(res['code'])}"


def test_free_form_bend_end_from_step_is_recognised_with_ends(tmp_path):
    """Condition 2 (free-form end): a bend end that is no cos/sin curve, written to STEP and read back -> kind "sheet",
    compare ok, ends= in the code."""
    shape = _step_roundtrip(l_free_form_end(), tmp_path, "free_l")
    res = _recognised_sheet(shape, "free_l")
    assert "ends=" in _part_code(res["code"]), f"the cut bend is not drawn with ends=:\n{_part_code(res['code'])}"


def test_template_made_bend_with_ends_from_step_is_recognised(tmp_path):
    """Conditions 1 + 2 together: a part built with the template's own panels + bend(ends=(a, b, c)), written to STEP and read
    back -> kind "sheet", compare ok (what recreate draws, it can read back)."""
    shape = _step_roundtrip(l_template_ends(), tmp_path, "tpl_l")
    _recognised_sheet(shape, "tpl_l")


@pytest.mark.parametrize("make", [l_sheet, u_sheet, l_helix_trim], ids=["square-L", "square-U", "helix-trim-L"])
def test_square_and_helix_trim_bends_keep_the_plain_code(make, tmp_path):
    """Condition 3: parts the plain path recognised keep exactly its code (after the inlined template), with no ends=.
    The plain path is recreate.py at BASE_COMMIT. (A regression guard: it passes before the feature by design.)"""
    shape = make()
    base_rec, _ = _base_modules(tmp_path)
    old = _base_recreate(base_rec, shape, make.__name__)
    assert old["kind"] == "sheet", f"test premise: the plain path recognised {make.__name__}: {old.get('reason')!r}"
    res = _recognised_sheet(shape, make.__name__)
    new_code = _part_code(res["code"])
    assert "ends=" not in new_code, f"{make.__name__}: a square / helix bend drawn with ends=:\n{new_code}"
    assert new_code == _part_code(old["code"]), \
        f"{make.__name__}: code changed from the plain path\n--- before\n{_part_code(old['code'])}\n--- now\n{new_code}"


def test_bend_end_with_an_axial_jog_is_not_drawn_with_ends():
    """Condition 4: a bend end that steps along the axis at turn 45 is not drawn with ends=: kind None, or a correct sheet
    without ends= (backed by the independent checks)."""
    shape = l_axial_jog()
    m = R()
    res = m.recreate(shape, "jog_l")
    if res["kind"] is None:
        assert res.get("reason"), "kind None without a reason"
        return
    assert "ends=" not in _part_code(res["code"]), f"the jogged bend end is drawn with ends=:\n{_part_code(res['code'])}"
    got = _build(res)
    assert m.compare(shape, got)["ok"] is True
    _same_geometry(shape, got, "jog_l")
