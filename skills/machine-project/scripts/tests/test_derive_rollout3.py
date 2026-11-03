"""Rollout items 6-9 (found migrating a client project): flat plates are not bent, bought rows group by (code, name),
product parts stay out of the machine's exports, identical sheet parts with one code become one drawing with the summed qty."""
import json
from pathlib import Path

import pytest

import helpers as H
from test_derive import needs_cadgen
import text_i18n as TX
from samples_i18n import SAMPLES

RU = SAMPLES["ru"]


def _model(root, specs):
    """specs: [(id, process, build123d shape, fields)] -> a hand-written build/model like helpers.make_derived_model."""
    root = Path(root)
    (root / "build/model/step").mkdir(parents=True)
    (root / "mechanics/cad").mkdir(parents=True)
    (root / "mechanics/cad/params.py").write_text("X = 1\n")
    parts = [H._write_part(root, pid, proc, shape, extra) for pid, proc, shape, extra in specs]
    H._write_parts_json(root, parts)
    return root


def _box(x, y, z, at=0):
    from build123d import Box, Pos
    return Pos(at, 0, 0) * Box(x, y, z)


def _ids(d):
    return [e["id"] for e in d.manifest["parts"]]


# ---------------------------------------------------------------- 7: bought grouped by (code, name)
def test_bought_groups_by_code_and_name(tmp_path):
    rows = [("a", "Bolt A", "M5", 2), ("b", "Bolt A", "M5", 3), ("c", "Bolt B", "M5", 4)]
    root = _model(tmp_path / "p", [(i, "bought", _box(1, 1, 1, at=k * 3), {"code": c, "desc": n, "qty": q}) for k, (i, n, c, q) in enumerate(rows)])
    d = H.run_derive(root, "bought").ok()
    got = {(r["code"], r["name"]): r["qty"] for r in d.manifest["bought"]}
    assert got == {("M5", "Bolt A"): 5, ("M5", "Bolt B"): 4}


# ---------------------------------------------------------------- 8: product parts are not machine parts
def test_product_parts_are_excluded_from_every_machine_scope(tmp_path):
    root = _model(tmp_path / "p", [
        ("frame", "mill", _box(30, 10, 5), {}),
        ("box_1", "other", _box(10, 10, 10, at=50), {"product": True}),
        ("box_print", "print", _box(10, 10, 10, at=70), {"product": True}),
        ("box_buy", "bought", _box(10, 10, 10, at=90), {"product": True, "code": "BOX", "qty": 9}),
        ("knob", "print", _box(5, 5, 5, at=110), {}),
    ])
    assert "box_1" not in _ids(H.run_derive(root, "step").ok())
    assert _ids(H.run_derive(root, "print").ok()) == ["knob"]
    assert H.run_derive(root, "bought").ok().manifest["bought"] == []
    step = H.run_derive(root, "step").ok().manifest["model_step"]
    from build123d import import_step
    assert len(import_step(str(root / step)).solids()) == 2, "model.step holds frame + knob only"


# ---------------------------------------------------------------- 9: identical sheet parts -> one drawing, summed qty
@needs_cadgen
def test_same_code_sheet_parts_become_one_entry_with_summed_qty(tmp_path):
    f = {"material": "S235JR", "thickness": 2.0, "code": "PL-1"}
    root = _model(tmp_path / "p", [("plate_1", "laser", _box(30, 20, 2), dict(f, qty=2)),
                                   ("plate_2", "laser", _box(20, 30, 2, at=60), dict(f, qty=3)),      # same part, turned
                                   ("other", "laser", _box(40, 20, 2, at=120), dict(f, code="PL-2", qty=1))])
    d = H.run_derive(root, "laser").ok()
    assert len(d.manifest["parts"]) == 2, d.manifest
    e = [e for e in d.manifest["parts"] if e["id"] == "plate_1"][0]
    assert e["qty"] == 5 and sorted(e["ids"]) == ["plate_1", "plate_2"]
    one = H.run_derive(root, "laser", part_id="plate_2").ok()
    assert len(one.manifest["parts"]) == 1 and one.manifest["parts"][0]["qty"] == 5


def _failed_group(d, root, *ids):
    """The one manifest entry that carries `error`, naming every id, with its note file instead of a drawing."""
    bad = [e for e in d.manifest["parts"] if e.get("error")]
    assert len(bad) == 1, d.manifest
    assert all(i in bad[0]["error"] for i in ids) and "PL-1" in bad[0]["error"], bad[0]["error"]
    assert [f.suffix for f in H.file_list(bad[0], root)] == [".txt"] and H.file_list(bad[0], root)[0].name.endswith(TX.t("error.suffix") + ".txt")
    return bad[0]


def test_same_code_but_different_parts_fails_naming_them(tmp_path):
    f = {"material": "S235JR", "thickness": 2.0, "code": "PL-1", "qty": 1}
    root = _model(tmp_path / "p", [("plate_1", "laser", _box(30, 20, 2), f), ("plate_2", "laser", _box(31, 20, 2, at=60), f)])
    d = H.run_derive(root, "laser")
    assert d.rc == 0, d.log[-800:]                                             # that code entry FAILs, not the whole kit
    _failed_group(d, root, "plate_1", "plate_2")
    assert "plate_1" in d.log and "plate_2" in d.log


def test_a_big_plate_with_a_small_extra_hole_is_not_the_same_part(tmp_path):
    """1000 x 500 x 3 plate: four O6 holes are 0.02 % of its volume, far below a relative tolerance, far above 0.5 mm3."""
    from build123d import Box, Cylinder, Pos
    plain = Box(1000, 500, 3)
    holed = plain
    for x in (-300, -100, 100, 300):
        holed = holed - Pos(x, 0, 0) * Cylinder(3, 10)
    f = {"material": "S235JR", "thickness": 3.0, "code": "PL-1", "qty": 1}
    root = _model(tmp_path / "p", [("plate_1", "laser", plain, f), ("plate_2", "laser", Pos(1200, 0, 0) * holed, f)])
    d = H.run_derive(root, "laser")
    assert d.rc == 0, d.log[-800:]
    _failed_group(d, root, "plate_1", "plate_2")


def _chiral_bracket():
    """An L bracket (base + flange) with a hole off the middle of its width: no mirror symmetry, so its mirror image is another part."""
    from build123d import Box, Cylinder, Pos
    b = Pos(0, 0, 1) * Box(40, 20, 2) + Pos(19, 0, 16) * Box(2, 20, 30)
    return b - Pos(-8, 5, 0) * Cylinder(2.5, 10)


@needs_cadgen
def test_a_mirrored_bent_pair_is_not_merged(tmp_path):
    from build123d import Plane, Pos, mirror
    f = {"material": "S235JR", "thickness": 2.0, "code": "PL-1", "qty": 1}
    b = _chiral_bracket()
    root = _model(tmp_path / "p", [("brk_l", "bent", b, f), ("brk_r", "bent", Pos(100, 0, 0) * mirror(b, about=Plane.XZ), f)])
    d = H.run_derive(root, "laser")
    assert d.rc == 0, d.log[-800:]
    _failed_group(d, root, "brk_l", "brk_r")


@needs_cadgen
def test_the_same_bent_part_turned_still_merges(tmp_path):
    from build123d import Pos, Rot
    f = {"material": "S235JR", "thickness": 2.0, "code": "PL-1", "qty": 1}
    b = _chiral_bracket()
    root = _model(tmp_path / "p", [("brk_1", "bent", b, f), ("brk_2", "bent", Pos(100, 0, 0) * Rot(20, 35, 50) * b, f)])
    d = H.run_derive(root, "laser").ok()
    assert len(d.manifest["parts"]) == 1 and d.manifest["parts"][0]["qty"] == 2 and "error" not in d.manifest["parts"][0], d.manifest


@needs_cadgen
def test_a_mirrored_laser_pair_merges(tmp_path):
    """A flat laser blank mirrored is the same blank turned over: one drawing."""
    from build123d import Box, Cylinder, Plane, Pos, mirror
    f = {"material": "S235JR", "thickness": 2.0, "code": "PL-1", "qty": 1}
    b = Box(40, 20, 2) - Pos(-8, 5, 0) * Cylinder(2.5, 10) - Pos(10, -3, 0) * Cylinder(1.5, 10)
    root = _model(tmp_path / "p", [("pl_l", "laser", b, f), ("pl_r", "laser", Pos(100, 0, 0) * mirror(b, about=Plane.XZ), f)])
    d = H.run_derive(root, "laser").ok()
    assert len(d.manifest["parts"]) == 1 and d.manifest["parts"][0]["qty"] == 2 and "error" not in d.manifest["parts"][0], d.manifest


def test_same_code_with_a_different_finish_is_not_the_same_part(tmp_path):
    f = {"material": "S235JR", "thickness": 2.0, "code": "PL-1", "qty": 1}
    root = _model(tmp_path / "p", [("plate_1", "laser", _box(30, 20, 2), dict(f, finish="zinc")), ("plate_2", "laser", _box(30, 20, 2, at=60), f)])
    d = H.run_derive(root, "laser")
    assert d.rc == 0, d.log[-800:]
    _failed_group(d, root, "plate_1", "plate_2")


@needs_cadgen
def test_a_part_lying_at_an_arbitrary_angle_still_matches_its_twin(tmp_path):
    from build123d import Box, Pos, Rot
    f = {"material": "S235JR", "thickness": 2.0, "code": "PL-1", "qty": 1}
    root = _model(tmp_path / "p", [("plate_1", "laser", _box(30, 20, 2), f),
                                   ("plate_2", "laser", Pos(60, 0, 0) * Rot(20, 35, 50) * Box(30, 20, 2), f)])
    d = H.run_derive(root, "laser").ok()
    assert len(d.manifest["parts"]) == 1 and d.manifest["parts"][0]["qty"] == 2


def test_group_representative_is_the_smallest_id_and_a_single_part_keeps_its_key(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(H.DERIVE.parent))
    import derive
    monkeypatch.setattr(derive, "_invariants", lambda p: (1.0, 1.0, 1.0, 1.0, 1.0), raising=True)
    monkeypatch.setattr(derive, "_chirality", lambda p: 0.0, raising=False)
    f = {"material": "S235JR", "thickness": 2.0, "code": "PL-1", "qty": 1, "process": "laser"}
    two = derive.group_identical([dict(f, id="b", key="kb"), dict(f, id="a", key="ka")])
    assert len(two) == 1 and two[0]["id"] == "a" and two[0]["ids"] == ["a", "b"] and two[0]["keys"] == ["ka", "kb"]
    one = derive.group_identical([dict(f, id="solo", key="ks", code="PL-9")])
    assert one[0]["key"] == "ks" and "keys" not in one[0]


def test_step_of_a_product_says_so(tmp_path):
    root = _model(tmp_path / "p", [("box_1", "other", _box(10, 10, 10), {"product": True})])
    d = H.run_derive(root, "step", part_id="box_1")
    assert d.rc != 0 and "product" in d.log and "excluded from kits" in d.log, d.log[-600:]


def _bent_bracket(dx, d):
    from build123d import Box, Cylinder, Pos
    b = Box(100, 60, 2) + Pos(0, -29, 20) * Box(100, 2, 40)
    return b - Pos(dx, 10, 0) * Cylinder(d / 2, 10)


@needs_cadgen
@pytest.mark.parametrize("dx,dia", [(1, 4), (0.5, 3)])
def test_a_nearly_symmetric_mirrored_bent_pair_is_not_merged(tmp_path, dx, dia):
    """A hole 1 mm (or 0.5 mm) off the middle gives a tiny chirality; the mirror image is still another part."""
    from build123d import Plane, Pos, mirror
    f = {"material": "S235JR", "thickness": 2.0, "code": "PL-1", "qty": 1}
    b = _bent_bracket(dx, dia)
    root = _model(tmp_path / "p", [("brk_l", "bent", b, f), ("brk_r", "bent", Pos(300, 0, 0) * mirror(b, about=Plane.YZ), f)])
    d = H.run_derive(root, "laser")
    assert d.rc == 0, d.log[-800:]
    _failed_group(d, root, "brk_l", "brk_r")


@needs_cadgen
def test_a_truly_symmetric_bent_pair_still_merges(tmp_path):
    from build123d import Plane, Pos, mirror
    f = {"material": "S235JR", "thickness": 2.0, "code": "PL-1", "qty": 1}
    b = _bent_bracket(0, 4)
    root = _model(tmp_path / "p", [("brk_l", "bent", b, f), ("brk_r", "bent", Pos(300, 0, 0) * mirror(b, about=Plane.YZ), f)])
    d = H.run_derive(root, "laser").ok()
    assert len(d.manifest["parts"]) == 1 and d.manifest["parts"][0]["qty"] == 2 and "error" not in d.manifest["parts"][0], d.manifest


def test_cyrillic_ids_get_distinct_error_notes(tmp_path):
    import sys
    sys.path.insert(0, str(H.DERIVE.parent))
    import derive

    class C:
        def build(self, key, deps, fill, count=True):
            d = tmp_path / key
            d.mkdir(exist_ok=True)
            fill(d)
            return d
    a = derive._error_note({"id": RU["part_id"] + "_1", "key": "k1"}, "x", C())
    b = derive._error_note({"id": RU["part_id"] + "_2", "key": "k2"}, "x", C())
    suffix = TX.t("error.suffix")
    assert a.name != b.name and a.name.endswith(suffix + ".txt") and a.name.split(suffix)[0].isascii()
