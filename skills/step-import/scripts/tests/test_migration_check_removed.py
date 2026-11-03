"""migration_check: parts or groups of his CAD removed on purpose (migration/removed.json) and groups added on purpose (a group
path in migration/added.json): a cabinet keeps 1 of his 8 ESP32 slots and adds a group of boards.

  python3 -m pytest -q test_migration_check_removed.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import migration_check as MC  # noqa: E402

EYE = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def at(x):
    m = list(EYE)
    m[3] = x
    return m


def ref(n):
    return {"parts": {"esp": {}}, "placements": [{"part": "esp", "matrix": at(10 * k)} for k in range(n)]}


def model(xs):
    return [{"id": "esp", "placements": [{"loc": at(x)} for x in xs]}]


def placements(n_his, xs, removed=None):
    rep = MC.Report()
    MC.check_placements(rep, ref(n_his), model(xs), {"esp": "esp"}, removed)
    return rep.rows[0][1]


def test_copies_removed_on_purpose_pass_and_no_more():
    assert placements(8, [60], {"esp": 7}) == "PASS"
    assert placements(8, [60]) == "FAIL", "without removed.json one copy of 8 is a difference"
    assert placements(8, [], {"esp": 7}) == "FAIL", "one more gone than said"
    assert placements(8, [61], {"esp": 7}) == "FAIL", "the copy that stays stands where his does"


def tree(tmp, his_children, have_children, added=(), removed=None):
    (tmp / "build" / "kit").mkdir(parents=True, exist_ok=True)
    (tmp / "view").mkdir(exist_ok=True)
    (tmp / "build" / "kit" / "viewer.json").write_text(json.dumps({"viewer_dir": "view"}))
    (tmp / "view" / "parts.json").write_text(json.dumps({"assembly": {"tree": {"name": "M", "children": have_children}}, "parts": []}))
    imp = tmp / "imp"
    imp.mkdir(exist_ok=True)
    (imp / "instances.json").write_text(json.dumps({"name": "M", "product": "M", "children": his_children}))
    rep = MC.Report()
    MC.check_tree(rep, tmp, {"placements": []}, imp, added, removed)
    return rep.rows[0][1]


def test_group_copies_removed_and_a_group_added_on_purpose(tmp_path):
    slot = {"name": "Esp", "product": "Esp", "children": []}
    his = [dict(slot) for _ in range(8)]
    ours = [{"name": "Esp", "children": []}, {"name": "Boards", "children": []}]
    assert tree(tmp_path, his, ours, ["M/Boards"], {"M/Esp": 7}) == "PASS"
    assert tree(tmp_path / "a", his, ours, [], {"M/Esp": 7}) == "FAIL", "a group his CAD has not is a difference unless added"
    assert tree(tmp_path / "b", his, ours, ["M/Boards"]) == "FAIL", "1 copy of his 8 is a difference unless removed"
