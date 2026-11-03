"""migration_check's «lists» row (the download lists before and after the port) and the screenshot stitching of «shots»,
on small manifests and images: no build, no browser (review 4).

  python3 -m pytest -q test_migration_check_lists.py
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import migration_check as MC  # noqa: E402

REF = {"parts": {"01.001": {"kind": "made"}, "01.002": {"kind": "made"}, "01.003": {"kind": "made"},
                 "Screw_M6": {"kind": "bought"}}}


def kit(root, **scopes):
    d = Path(root) / "build" / "kit"
    d.mkdir(parents=True, exist_ok=True)
    for scope, data in scopes.items():
        (d / f"{scope}.json").write_text(json.dumps(data), encoding="utf-8")
    return Path(root)


def before(tmp):
    """His project as it was: a printed part, two parts as bare STEPs, 10 screws."""
    return kit(tmp / "before",
               print={"parts": [{"id": "01.001", "process": "print", "qty": 2}]},
               other={"parts": [{"id": "01.002", "process": "other", "qty": 1}, {"id": "01.003", "process": "other", "qty": 4}]},
               bought={"bought": [{"name": "Screw M6", "desc": "Screw M6", "qty": 10}]})


def after(tmp, **change):
    """The ported project: the same, but 01.002 is a sheet part now (laser kit), the rest as before unless `change` says."""
    scopes = dict(print={"parts": [{"id": "01_001", "process": "print", "qty": 2}]},
                  laser={"parts": [{"id": "01_002", "process": "laser", "qty": 1}]},
                  step={"parts": [{"id": "01_003", "process": "other", "qty": 4}]},
                  bought={"bought": [{"name": "Screw M6", "desc": "Screw M6", "qty": 10}]})
    scopes.update(change)
    return kit(tmp / "after", **scopes)


def lists(tmp, **change):
    rep = MC.Report()
    MC.check_lists(rep, after(tmp, **change), before(tmp), REF, None)
    (name, result, summary), = rep.rows
    assert name == "lists"
    return result, summary, rep.detail["lists"]


def test_the_same_lists_with_a_step_part_moved_to_laser_pass(tmp_path):
    result, summary, d = lists(tmp_path)
    assert result == "PASS", summary
    assert d["moved_to_laser"] == ["01_002"]
    assert d["made_in_no_list"] == []


def test_a_part_gone_from_a_list_fails_and_is_named(tmp_path):
    result, _, d = lists(tmp_path, step={"parts": []})
    assert result == "FAIL"
    assert d["other (STEP)"]["removed"] == ["01_003"]
    assert "01_003" in d["made_in_no_list"]


def test_a_changed_quantity_fails_and_is_named(tmp_path):
    result, _, d = lists(tmp_path, print={"parts": [{"id": "01_001", "process": "print", "qty": 3}]})
    assert result == "FAIL"
    assert d["print"]["qty_changed"] == ["01_001"]


def test_fewer_bought_pieces_fail(tmp_path):
    result, _, d = lists(tmp_path, bought={"bought": [{"name": "Screw M6", "desc": "Screw M6", "qty": 8}]})
    assert result == "FAIL"
    assert d["bought"]["qty_changed"] == ["Screw M6: 10 -> 8"]


def test_bought_parts_added_on_purpose_pass(tmp_path):
    result, summary, _ = lists(tmp_path, bought={"bought": [{"name": "Screw M6", "desc": "Screw M6", "qty": 10},
                                                            {"name": "Arduino Nano", "desc": "Arduino Nano", "qty": 9}]})
    assert result == "PASS", summary


def test_more_bought_pieces_of_a_row_fail(tmp_path):
    result, _, d = lists(tmp_path, bought={"bought": [{"name": "Screw M6", "desc": "Screw M6", "qty": 12}]})
    assert result == "FAIL"
    assert d["bought"]["qty_changed"] == ["Screw M6: 10 -> 12"]


def test_a_bought_row_renamed_to_another_size_fails(tmp_path):
    def run_(before_name, after_name):
        root = tmp_path / after_name.replace(" ", "_")
        root.mkdir()
        old = kit(root / "b", print={"parts": []}, bought={"bought": [{"name": before_name, "qty": 10, "kind": "fasteners"}]})
        new = kit(root / "a", print={"parts": []}, bought={"bought": [{"name": after_name, "qty": 10, "kind": "fasteners"}]})
        rep = MC.Report()
        MC.check_lists(rep, new, old, {"parts": {}}, None)
        return rep.rows[0][1]
    assert run_("hex screw gradeab_din_DIN EN 24017 - M6 x 20-N", "Hex head bolt M6x20 DIN 933") == "PASS"
    assert run_("plain washer 10673 type snl_iso_PreviewCfg", "Washer M6 DIN 125") == "PASS"
    assert run_("Screw M6x20 DIN 912", "Screw M6x25 DIN 912") == "FAIL"


def test_an_unsized_row_does_not_take_a_sized_rows_match(tmp_path):
    """A washer with no size in his name and a M5x30 screw, both 4 pieces: the screw keeps its M5x30 row, the washer gets
    the washer's (a real client list)."""
    old = kit(tmp_path / "b", print={"parts": []}, bought={"bought": [
        {"name": "plain washer 10673 type snl_iso_PreviewCfg", "qty": 4, "kind": "fasteners"},
        {"name": "socket head cap screw 4762_din_EN ISO 4762 M5 x 30 - 22N", "qty": 4, "kind": "fasteners"}]})
    new = kit(tmp_path / "a", print={"parts": []}, bought={"bought": [
        {"name": "Socket head cap screw M5x30 DIN 912", "qty": 4, "kind": "fasteners"},
        {"name": "Washer M6 DIN 125", "qty": 4, "kind": "fasteners"}]})
    rep = MC.Report()
    MC.check_lists(rep, new, old, {"parts": {}}, None)
    assert rep.rows[0][1] == "PASS", rep.detail["lists"]["bought"]


def test_a_before_with_no_lists_is_not_checked_not_passed(tmp_path):
    for name, setup in (("missing", lambda d: None), ("empty", lambda d: d.mkdir()),
                        ("nobought", lambda d: kit(d, print={"parts": []}, other={"parts": []}))):
        old = tmp_path / name
        setup(old)
        rep = MC.Report()
        MC.check_lists(rep, after(tmp_path / f"a_{name}"), old, REF, None)
        assert rep.rows[0][1] == "NOT CHECKED", (name, rep.rows)


def test_an_old_kit_missing_one_list_does_not_hide_a_lost_laser_part(tmp_path):
    """His old kit had 01.002 in a laser list; the copy given to --before lacks laser.json, and the port moved 01.002 to the
    STEP list: no old list has it, so its move is not checked, not passed (critic)."""
    old = kit(tmp_path / "b", print={"parts": [{"id": "01.001", "process": "print", "qty": 2}]},
              other={"parts": [{"id": "01.003", "process": "other", "qty": 4}]},
              bought={"bought": [{"name": "Screw M6", "desc": "Screw M6", "qty": 10}]})
    new = after(tmp_path, laser={"parts": []}, step={"parts": [{"id": "01_003", "process": "other", "qty": 4},
                                                               {"id": "01_002", "process": "other", "qty": 1}]})
    rep = MC.Report()
    MC.check_lists(rep, new, old, REF, None)
    assert rep.rows[0][1] == "NOT CHECKED" and "01_002" in rep.rows[0][2], rep.rows


def test_a_broken_old_list_is_not_checked_not_a_crash(tmp_path):
    old = kit(tmp_path / "b", print={"parts": []}, bought={"bought": []})
    (old / "build" / "kit" / "bought.json").write_text("{broken")
    rep = MC.Report()
    MC.check_lists(rep, after(tmp_path), old, REF, None)
    assert rep.rows[0][1] == "NOT CHECKED" and "bought list is unreadable" in rep.rows[0][2], rep.rows


def test_a_difference_in_a_readable_list_fails_when_another_list_is_missing(tmp_path):
    """The old kit lacks its laser list, its bought list says 12 screws: 10 now fails, it is not hidden by the missing list."""
    old = kit(tmp_path / "b", print={"parts": [{"id": "01.001", "process": "print", "qty": 2}]},
              other={"parts": [{"id": "01.002", "process": "other", "qty": 1}, {"id": "01.003", "process": "other", "qty": 4}]},
              bought={"bought": [{"name": "Screw M6", "desc": "Screw M6", "qty": 12}]})
    rep = MC.Report()
    MC.check_lists(rep, after(tmp_path), old, REF, None)
    assert rep.rows[0][1] == "FAIL", rep.rows


def test_old_lists_of_the_wrong_shape_are_unreadable_not_a_crash_or_a_pass(tmp_path):
    for i, (scope, data) in enumerate((("laser", []), ("laser", {"parts": "x"}), ("bought", {"bought": [1, "a", None]}),
                                       ("bought", {"bought": [{"name": "Screw M6", "qty": "four"}]}),
                                       ("bought", {"parts": [], "bought": None}), ("other", {"parts": None}))):
        lists = dict(print={"parts": [{"id": "01.001", "process": "print", "qty": 2}]},
                     other={"parts": [{"id": "01.002", "process": "other", "qty": 1}, {"id": "01.003", "process": "other", "qty": 4}]},
                     bought={"bought": [{"name": "Screw M6", "desc": "Screw M6", "qty": 10}]})
        lists[scope] = data
        old = kit(tmp_path / f"b{i}", **lists)
        rep = MC.Report()
        MC.check_lists(rep, after(tmp_path / f"a{i}"), old, REF, None)
        assert rep.rows[0][1] in ("NOT CHECKED", "FAIL") and "unreadable" in rep.rows[0][2], (scope, data, rep.rows)


def test_a_bought_row_with_another_article_fails(tmp_path):
    rows = lambda code: {"bought": [{"name": "Motor", "desc": "Motor", "code": code, "qty": 1}]}   # noqa: E731
    old = kit(tmp_path / "b", print={"parts": []}, bought=rows("NEMA23"))
    new = kit(tmp_path / "a", print={"parts": []}, bought=rows("NEMA17"))
    rep = MC.Report()
    MC.check_lists(rep, new, old, {"parts": {}}, None)
    assert rep.rows[0][1] == "FAIL" and rep.detail["lists"]["bought"]["code_changed"] == ["Motor: NEMA23 -> NEMA17"]


def test_a_bought_row_without_a_name_or_a_true_quantity_is_unreadable(tmp_path):
    for i, row in enumerate(({"qty": 1}, {"name": None, "qty": 1}, {"name": 5, "qty": 1}, {"name": "Motor", "qty": True})):
        old = kit(tmp_path / f"b{i}", print={"parts": []}, bought={"bought": [row]})
        new = kit(tmp_path / f"a{i}", print={"parts": []}, bought={"bought": [{"name": "Motor", "qty": 1}]})
        rep = MC.Report()
        MC.check_lists(rep, new, old, {"parts": {}}, None)
        assert rep.rows[0][1] == "NOT CHECKED" and "unreadable" in rep.rows[0][2], (row, rep.rows)


def test_another_item_is_not_a_rename(tmp_path):
    def run_(old_row, new_row, sub):
        old = kit(tmp_path / sub / "b", print={"parts": []}, bought={"bought": old_row})
        new = kit(tmp_path / sub / "a", print={"parts": []}, bought={"bought": new_row})
        rep = MC.Report()
        MC.check_lists(rep, new, old, {"parts": {}}, None)
        return rep.rows[0][1]
    motor = {"name": "Stepper motor NEMA17 17HS4401", "code": "NEMA17", "kind": "motors", "qty": 1, "unit": "pcs"}
    assert run_([{"name": "Bearing 608ZZ", "code": "608ZZ", "kind": "bearings", "qty": 1}], [motor], "bearing") == "FAIL"
    assert run_([{"name": "Another motor", "code": "NEMA23", "kind": "motors", "qty": 1}], [motor], "renamed") == "FAIL"
    two = [dict(motor, code="NEMA23"), dict(motor, qty=0)]
    assert run_(two, [motor], "order1") == "FAIL" and run_(two[::-1], [motor], "order2") == "FAIL"
    assert run_([dict(motor, unit="set")], [motor], "unit") == "FAIL"
    assert run_([dict(motor, code="")], [motor], "filled") == "PASS"
    assert run_([{"name": "Motor", "code": "NEMA17", "kind": "motors", "qty": 1, "unit": "set"}], [motor], "unit_renamed") == "FAIL"
    assert run_([dict(motor, kind="fasteners")], [motor], "kind") == "FAIL"


def test_a_renamed_row_tells_its_new_article(tmp_path):
    old = kit(tmp_path / "b", print={"parts": []}, bought={"bought": [{"name": "Screws M6x20", "kind": "fasteners", "qty": 4}]})
    new = kit(tmp_path / "a", print={"parts": []}, bought={"bought": [
        {"name": "Socket head cap screw M6x20 DIN 912", "code": "M6x20 DIN 912", "kind": "fasteners", "qty": 4}]})
    rep = MC.Report()
    MC.check_lists(rep, new, old, {"parts": {}}, None)
    assert rep.rows[0][1] == "PASS" and "article given where his had none 1" in rep.rows[0][2], rep.rows


def test_stitch_puts_the_views_side_by_side_and_measures_the_difference(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    Image.new("RGB", (100, 50), "white").save(a)
    im = Image.new("RGB", (100, 50), "white")
    for x in range(25):                                   # a quarter of the picture black
        for y in range(50):
            im.putpixel((x, y), (0, 0, 0))
    im.save(b)
    diffs = MC.stitch([("iso", [a, a]), ("top", [a, b])], tmp_path / "out")
    assert diffs == [("iso", 0.0), ("top", 0.25)]
    side = Image.open(tmp_path / "out" / "top.png")
    assert side.size == (210, 50)
    assert MC.shots_ok([("iso", 0.019), ("top", 0.02)]) and not MC.shots_ok([("iso", 0.019), ("top", 0.25)])
