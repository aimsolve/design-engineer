"""Assembly drawings: a fastener is pulled out of the part it holds to the side without material, whatever the part's shape.
Review of the first two rules: "away from the parent's centre" buried a screw on a washer, "leave the parent's box soonest" pushed a
screw on an L-bracket's leg down through the leg. Pure explode() on build123d shapes, no PDF.

Run in the drawings venv: SHOP_DRAW_VENV=<venv> $SHOP_DRAW_VENV/bin/python -m pytest -q test_assembly_explode.py
"""
import sys
from pathlib import Path

import pytest

build123d = pytest.importorskip("build123d")
from build123d import Align, Box, Cylinder, Pos  # noqa: E402

BOT = (Align.CENTER, Align.CENTER, Align.MIN)
TOP = (Align.CENTER, Align.CENTER, Align.MAX)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import assembly_drawing as A  # noqa: E402

M5_HOLE = 2.75


def screw(z_head):
    """An M5 screw standing on its bearing face at z_head: head above, shaft below."""
    return Pos(0, 0, z_head) * (Cylinder(4.25, 5, align=BOT) + Pos(0, 0, -12) * Cylinder(2.5, 12, align=BOT))


def offsets(parts, items):
    return A.explode(items, {n: s.wrapped for n, s in parts.items()})


def item(name, parent=None, axis=None, kind=""):
    return {"name": name, "part": name, "parent": parent, "axis": axis, "kind": kind}


def test_screw_on_a_plate_comes_out_of_its_face():
    plate = Box(60, 60, 4, align=BOT) - Pos(0, 0, 2) * Cylinder(M5_HOLE, 10)
    for d in ([0, 0, 1.0], [0, 0, -1.0]):                 # either sign of the hole joint's axis
        off = offsets({"plate": plate, "s": screw(4)}, [item("plate"), item("s", "plate", [[0, 0, 4], d], "fasteners")])
        assert off["s"][2] > 5, f"axis {d}: screw pulled up out of the plate, got {off['s']}"


def test_screw_on_an_l_bracket_leg_comes_up_not_through_the_leg():
    leg = Pos(0, -15, 0) * Box(30, 30, 3, align=BOT)
    wall = Pos(0, 13.5, 0) * Box(30, 3, 30, align=BOT)
    bracket = leg + wall - Pos(0, -15, 1.5) * Cylinder(M5_HOLE, 10)
    off = offsets({"b": bracket, "s": Pos(0, -15, 0) * screw(3)},
                  [item("b"), item("s", "b", [[0, -15, 3], [0, 0, 1.0]], "fasteners")])
    assert off["s"][2] > 5, f"screw pulled up off the leg, got {off['s']}"


def test_nut_on_a_screw_tip_comes_off_the_tip():
    s = screw(0)                                          # tip at z = -12
    nut = Pos(0, 0, -12) * Cylinder(4, 4, align=TOP)
    off = offsets({"plate": Pos(0, 0, -2) * Box(60, 60, 2, align=BOT), "s": s, "nut": nut},
                  [item("plate"), item("s", "plate", [[0, 0, 0], [0, 0, 1.0]], "fasteners"),
                   item("nut", "s", [[0, 0, -12], [0, 0, 1.0]], "fasteners")])
    assert off["nut"][2] - off["s"][2] < -3, f"nut pulled down off the tip, got nut {off['nut']} screw {off['s']}"


def test_washer_on_a_plate_comes_up():
    plate = Box(60, 60, 4, align=BOT) - Pos(0, 0, 2) * Cylinder(M5_HOLE, 10)
    washer = Pos(0, 0, 4) * (Cylinder(5, 1, align=BOT) - Cylinder(2.65, 3))
    off = offsets({"plate": plate, "w": washer}, [item("plate"), item("w", "plate", [[0, 0, 4], [0, 0, -1.0]], "fasteners")])
    assert off["w"][2] > 3, f"washer pulled up off the plate, got {off['w']}"


def test_a_docking_cycle_loses_no_part():
    """Items docked in a cycle (a model error) are never reached from a root; they must still be drawn."""
    items = [item("base"), item("a", "b"), item("b", "a"), item("c", "base")]
    drawn = {it["name"] for _, its, _, _ in A.plan(items) for it in its}
    assert drawn == {"base", "a", "b", "c"}, drawn
