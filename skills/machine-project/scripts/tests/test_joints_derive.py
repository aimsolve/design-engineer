"""Issue #16, the download side: derive.py on a project assembled by stock build123d joints (jointfiles.py).

Done-when map:
  DW1  the downloaded STEP model (derive.py step -> model.step) and the viewer (derive.py viewer -> parts.json bbox_mm, world mm)
       show every part where its joints put it
  DW2  after a size edit of the base, model.step and the viewer show the docked parts moved with it
  DW3  template screws are listed in the bought list with their size, counted by placements
"""
import json

import pytest

import helpers as H
import partfiles as F
import jointfiles as J
from jointfiles import joint_baseline, jproj  # noqa: F401  (fixtures)

TOL = 1e-3


def _bb(s):
    b = s.bounding_box()
    return (b.min.X, b.min.Y, b.min.Z), (b.max.X, b.max.Y, b.max.Z)


def _same_box(a, b, tol=TOL):
    return all(abs(float(x) - float(y)) <= tol for pa, pb in zip(a, b) for x, y in zip(pa, pb))


def _shift(box, dz):
    (a, b) = box
    return (a[0], a[1], a[2] + dz), (b[0], b[1], b[2] + dz)


def _model_leaves(root):
    d = H.run_derive(root, "step").ok()
    tree = F.step_tree(F.abs_path(root, d.manifest["model_step"]))
    return [(lbl, _bb(s)) for _, lbl, s in F.leaves(tree)]


def _viewer(root):
    d = H.run_derive(root, "viewer").ok()
    vd = F.abs_path(root, d.manifest["viewer_dir"])
    return {p["id"]: p for p in json.loads((vd / "parts.json").read_text())["parts"]}


def _screw_boxes_ok(boxes, t):
    """Six screw boxes: four M5x12 down to t-12 and two M4x8 down to t-8, each centred on its own hole, head above t."""
    want = sorted([(x, y, t - 12) for x, y in J.HOLES_M5.values()] + [(x, y, t - 8) for x, y in J.HOLES_M4.values()])
    got = sorted((round((a[0] + b[0]) / 2, 3), round((a[1] + b[1]) / 2, 3), round(a[2], 3)) for a, b in boxes if b[2] > t + TOL)
    return got == sorted((float(x), float(y), float(z)) for x, y, z in want), got


# ================================================================ DW1: model.step
@pytest.mark.parametrize("name", ["base", "rail", "slider", "arm"])
def test_model_step_shows_each_part_where_its_joints_put_it(jproj, name):
    leaves = _model_leaves(jproj)
    want = J.WORLD_BBOX[name]
    assert any(_same_box(b, want) for _, b in leaves), \
        f"model.step has no solid of {name} at {want}; leaves: {[(l, tuple(tuple(round(v, 3) for v in p) for p in b)) for l, b in leaves]}"


def test_model_step_shows_every_screw_in_its_hole(jproj):
    leaves = _model_leaves(jproj)
    others = [b for _, b in leaves if not any(_same_box(b, w) for w in J.WORLD_BBOX.values())]
    assert len(leaves) == 4 + 6, f"model.step: base, rail, slider, arm + 6 screws expected, got {len(leaves)}"
    ok, got = _screw_boxes_ok(others, J.T)
    assert ok, f"screws in model.step must hang from their holes (centre XY, shaft end z): {got}"


# ================================================================ DW1: viewer
@pytest.mark.parametrize("name", ["base", "rail", "slider", "arm"])
def test_viewer_bbox_is_where_the_joints_put_the_part(jproj, name):
    v = _viewer(jproj)
    assert name in v, f"viewer has no part {name!r}: {sorted(v)}"
    assert _same_box(tuple(map(tuple, v[name]["bbox_mm"])), J.WORLD_BBOX[name]), \
        f"viewer {name}: bbox {v[name]['bbox_mm']}, the joints put it at {J.WORLD_BBOX[name]}"


def test_viewer_lists_every_screw_placement_in_its_hole(jproj):
    v = _viewer(jproj)
    assert len(v) == 10, f"10 placements in the viewer expected (4 parts + 6 screws), got {sorted(v)}"
    boxes = [tuple(map(tuple, p["bbox_mm"])) for k, p in v.items() if k not in J.WORLD_BBOX]
    ok, got = _screw_boxes_ok(boxes, J.T)
    assert ok, f"viewer screws must hang from their holes: {got}"


# ================================================================ DW2: downloads follow a size edit
def test_after_a_base_thickness_edit_model_step_and_viewer_show_the_docked_parts_moved(jproj):
    assert (jproj / "build/model/parts.json").is_file(), "precondition: the cold build made a model"
    H.run_derive(jproj, "step").ok()
    H.edit(F.part_file(jproj, "frame/base.py"), "T = 10.0", "T = 16.0")
    leaves = _model_leaves(jproj)
    want = {"base": ((0, 0, 0), (200, 100, 16))}
    want.update({n: _shift(J.WORLD_BBOX[n], 6) for n in J.DOCKED})
    for n, w in want.items():
        assert any(_same_box(b, w) for _, b in leaves), f"model.step after T=16: no {n} at {w}"
    others = [b for _, b in leaves if not any(_same_box(b, w) for w in want.values())]
    ok, got = _screw_boxes_ok(others, 16.0)
    assert ok, f"model.step after T=16: screws must follow the plate top: {got}"
    v = _viewer(jproj)
    for n, w in want.items():
        assert _same_box(tuple(map(tuple, v[n]["bbox_mm"])), w), f"viewer after T=16: {n} bbox {v[n]['bbox_mm']}, expected {w}"


# ================================================================ DW3: bought list
def _rows(root, spelled):
    d = H.run_derive(root, "bought").ok()
    rows = [r for r in d.manifest["bought"] if spelled in str(r.get("code", "")) or spelled in str(r.get("name", ""))]
    return rows, d.manifest["bought"]


@pytest.mark.parametrize("spelled,qty", [("M5x12", 4), ("M4x8", 2)])
def test_bought_lists_template_screws_by_size_counted_by_placements(jproj, spelled, qty):
    rows, all_rows = _rows(jproj, spelled)
    assert len(rows) == 1, f"one bought row spelling {spelled} expected, got {rows} (all: {all_rows})"
    r = rows[0]
    assert r["qty"] == qty, f"{spelled}: qty {r['qty']}, placed {qty} times"
    assert r.get("kind") == "fasteners", r
    text = " ".join(str(r.get(k, "")) for k in ("code", "name", "desc"))
    assert "DIN 912" in text, f"{spelled}: the row must spell the standard ('{spelled} DIN 912'): {r}"


@pytest.mark.parametrize("spelled,qty", [("M5x12", 4), ("M4x8", 2)])
def test_parts_json_has_one_bought_fastener_entry_per_size(joint_baseline, spelled, qty):
    root, m, _ = joint_baseline
    m.ok()
    e = J.screw_entries(root, spelled)
    assert len(e) == 1, f"one parts.json entry for {spelled} expected, got {[p['id'] for p in e]}"
    e = e[0]
    assert e["qty"] == qty and e.get("kind") == "fasteners" and e.get("process") == "bought", e
    text = " ".join(str(e.get(k, "")) for k in ("code", "desc"))
    assert f"{spelled} DIN 912" in text, f"code/desc must spell '{spelled} DIN 912': {e.get('code')!r} / {e.get('desc')!r}"


def test_adding_a_screw_placement_raises_the_bought_qty(jproj):
    assert (jproj / "build/model/parts.json").is_file(), "precondition: the cold build made a model"
    H.edit(F.part_file(jproj, "frame/base.py"), "HOLES = {", "HOLES = {'hole_7': (100, 20), ")
    H.edit(F.part_file(jproj, "frame/__init__.py"), 'M5 = ["hole_1",', 'M5 = ["hole_7", "hole_1",')
    rows, _ = _rows(jproj, "M5x12")
    assert len(rows) == 1 and rows[0]["qty"] == 5, rows


# ================================================================ viewer: motion_group is inherited up the joint graph
SLIDER_SHUTTLE = J.FILES["head/slider.py"].replace('material="PETG")', 'material="PETG", motion_group="shuttle")')
ARM_HOLE = J.FILES["head/arm.py"].replace(
    '            RigidJoint("pivot", s, Location((0, 0, 0)))\n',
    '            RigidJoint("pivot", s, Location((0, 0, 0)))\n            RigidJoint("hole", s, Location((40, 0, 5)))\n')
ARM_SWING = ARM_HOLE.replace('material="PLA")', 'material="PLA", motion_group="swing")')
HEAD_SCREW = J.FILES["head/__init__.py"].replace("from . import arm, slider", "from parts.frame import screw\n        from . import arm, slider") \
    .replace("            return [place(s), place(a)]",
             '            sc = screw.build("M4", 8, "DIN 912")\n'
             '            a["shape"].joints["hole"].connect_to(sc["shape"].joints["head"])\n'
             '            return [place(s), place(a), place(sc, name="arm_screw")]')
assert SLIDER_SHUTTLE != J.FILES["head/slider.py"] and "arm_screw" in HEAD_SCREW and ARM_SWING != ARM_HOLE


def _viewer_groups(tmp_path, arm_src):
    root = J.write_project(tmp_path / "p", files={"head/slider.py": SLIDER_SHUTTLE, "head/arm.py": arm_src,
                                                  "head/__init__.py": HEAD_SCREW})
    F.run_model(root).ok()
    v = _viewer(root)
    assert "arm_screw" in v, f"precondition: the screw in the arm's hole is in the viewer: {sorted(v)}"
    return {k: p.get("motion_group") for k, p in v.items()}


def test_viewer_docked_parts_take_the_motion_group_of_what_they_are_docked_to(tmp_path):
    """slider has motion_group "shuttle"; the arm (docked on it) and the screw in the arm's hole have none of their own -> both
    show "shuttle"; base, rail and the screws in the base show none."""
    g = _viewer_groups(tmp_path, ARM_HOLE)
    for k in ("slider", "arm", "arm_screw"):
        assert g[k] == "shuttle", f"{k}: motion_group {g[k]!r}, inherited from slider expected 'shuttle' ({g})"
    for k, grp in g.items():
        if k not in ("slider", "arm", "arm_screw"):
            assert not grp, f"{k} is not docked under the slider: no motion_group expected, got {grp!r}"


def test_viewer_own_motion_group_wins_over_the_inherited_one(tmp_path):
    """arm has its own motion_group "swing": it keeps it, and the screw docked in the arm takes "swing" (its nearest parent)."""
    g = _viewer_groups(tmp_path, ARM_SWING)
    assert g["slider"] == "shuttle", g
    assert g["arm"] == "swing", f"arm's own motion_group must win: {g['arm']!r}"
    assert g["arm_screw"] == "swing", f"the screw in the arm inherits the arm's own group: {g['arm_screw']!r}"
    assert not g["base"] and not g["rail"], g
