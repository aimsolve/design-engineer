"""migration_check's «geometry» row on small parts, no project build: a bought part drawn as code is held to the
tolerances of a part drawn by hand, and a standard nut put on the framework template stands as his nut does (its flats where
his flats are), so the per-axis box check passes.

  python -m pytest -q test_migration_check_geometry.py
"""
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from step_names_i18n import NAMES  # noqa: E402
bd = pytest.importorskip("build123d")
import migration_check as MC  # noqa: E402
import step_to_project as S  # noqa: E402


def tube(r_out=5.0, r_in=3.2, length=45.0):
    a = (bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
    return bd.Cylinder(r_out, length, align=a) - bd.Cylinder(r_in, length, align=a)


def geometry(tmp, his, mine, report_row):
    """check_geometry on one part: his STEP in the import dir, mine as the model's STEP."""
    (tmp / "imp" / "parts").mkdir(parents=True)
    (tmp / "build" / "model").mkdir(parents=True)
    bd.export_step(his, str(tmp / "imp" / "parts" / "X_1.step"))
    bd.export_step(mine, str(tmp / "build" / "model" / "x.step"))
    rep = MC.Report()
    MC.check_geometry(rep, tmp, {"parts": {"X_1": {"name": "X 1"}}}, [{"id": "X_1", "step": "build/model/x.step"}],
                      {"X_1": "X_1"}, tmp / "imp", {"parts": [dict(report_row, id="X_1")]})
    (name, result, summary), = rep.rows
    return result, rep.detail.get("geometry", {}).get("off", [])


def library_part(tmp, vendor_3d):
    """The bought part's file in the project takes library part other/spacer; `vendor_3d`: the library keeps a STEP beside
    its module (his 3D), else the module draws the part as code. -> the report row of that part."""
    lib = tmp / "mechanics" / "cad" / "library" / "other"
    lib.mkdir(parents=True)
    (lib / "spacer.py").write_text("def build():\n    pass\n")
    if vendor_3d:
        bd.export_step(tube(), str(lib / "spacer.step"))
    part = tmp / "mechanics" / "cad" / "parts" / "spacer.py"
    part.parent.mkdir(parents=True)
    part.write_text("from library.other import spacer\n")
    return {"kind": "bought", "how": "library", "file": "mechanics/cad/parts/spacer.py"}


def test_a_bought_part_drawn_as_code_passes_within_tolerance(tmp_path):
    """His spacer tube and ours drawn as a revolved profile: not the same B-rep, the same part."""
    prof = bd.Polyline((3.2, 0), (5.0, 0), (5.0, 45.0), (3.2, 45.0), close=True)
    mine = bd.revolve(bd.make_face(bd.Plane.XZ * prof), bd.Axis.Z)
    result, off = geometry(tmp_path, tube(), mine.moved(bd.Location((0, 0, 0.01))), library_part(tmp_path, False))
    assert result == "PASS", off


def test_a_bought_part_drawn_off_fails(tmp_path):
    result, off = geometry(tmp_path, tube(), tube(length=47.0), library_part(tmp_path, False))
    assert result == "FAIL" and off[0]["kind"] == "bought (drawn)", off


def test_a_bought_part_with_his_3d_must_be_his_exactly(tmp_path):
    """A library part that keeps his 3D file is held to «the same shape»: one that drifted by a hair (a re-export, another
    revision) FAILs as «bought (his 3D)» and is not let through by the drawn-part tolerances."""
    result, off = geometry(tmp_path, tube(), tube(length=45.02), library_part(tmp_path, True))
    assert result == "FAIL" and off[0]["kind"] == "bought (his 3D)", off


def hex_nut(s=10.0, m=5.2, hole=3.0):
    """His nut as one client CAD has it: corners on X (a RegularPolygon's first vertex), flats on Y."""
    return bd.extrude(bd.RegularPolygon(s / math.sqrt(3), 6), amount=m) - bd.Cylinder(hole, 3 * m)


def test_a_hex_nut_with_corners_on_x_stands_as_his_on_the_template(tmp_path):
    """fastener_fit picks the template's turn about the axis from his flats, so the template nut in his frame has his box on
    every axis (corners on X, flats on Y), and migration_check's per-axis standard check passes."""
    his = hex_nut()
    fit = S.fastener_fit(his, S.fastener_of({"kind": "standard", "name": NAMES["ru"]["nut"] + " DIN 934 M6"}))
    assert fit["ok"], fit
    tpl = S._template(fit["template"]).build(**fit["args"])["shape"].moved(S.from_np(S.mat(fit["frame"])))
    hb, tb = his.bounding_box(), tpl.bounding_box()
    tol = max(0.6, 0.06 * max(hb.size.X, hb.size.Y, hb.size.Z))
    assert abs(hb.size.X - tb.size.X) <= tol and abs(hb.size.Y - tb.size.Y) <= tol, (hb.size, tb.size)
    result, off = geometry(tmp_path, his, tpl, {"kind": "bought", "how": "standard", "fit": fit["check"],
                                                "template": fit["template"], "args": fit["args"]})
    assert result == "PASS", off


def test_a_hole_whose_halves_sit_a_micron_apart_on_a_rounding_boundary_is_one_hole():
    """A STEP writer can move the two halves of one hole's axis by a micron (a real part, a Ø1.27 hole at
    z -28.645): rounding the axis to 0.01 mm split them across the boundary and the hole was not counted."""
    import recreate as RC
    c, e, r = 12.345, 0.0005, 1.0
    plate = bd.Box(40, 40, 3)
    half = lambda x, keep: bd.split(bd.Pos(x, 0, 0) * bd.Cylinder(r, 10), bd.Plane.YZ.offset(c), keep=keep)   # noqa: E731
    holed = plate.moved(bd.Location((c, 0, 0))) - half(c + e, bd.Keep.TOP) - half(c - e, bd.Keep.BOTTOM)
    assert RC._concave_holes(RC._wrapped(holed)) == 1


def test_two_holes_two_hundredths_apart_stay_two():
    """The tolerance that joins a hole's halves (0.01 mm) does not merge two separate holes 0.02 mm apart."""
    import recreate as RC
    plate = bd.Box(40, 40, 3)
    holed = plate - bd.Pos(-0.01, 0, 0) * bd.Cylinder(1.0, 10) - bd.Pos(10.01, 0, 0) * bd.Cylinder(1.0, 10)
    assert RC._concave_holes(RC._wrapped(holed)) == 2
