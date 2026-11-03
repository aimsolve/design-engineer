"""Issue #15: derive.py scopes on a model built from the folder layout (one part = one file, node = folder).

Done-when map:
  DW1  editing one part file -> each download scope (step, laser, print, bought, viewer) gives the new part, the rest reused
  DW3  qty in laser and bought = number of placements
  C    step groups by node path (nested node -> nested group); placed geometry in model.step; viewer lists one entry per placement
The project is partfiles.py's synthetic machine; derive brings the model up to date itself.
"""
import json
from pathlib import Path

import pytest

import helpers as H
import partfiles as F
from partfiles import new_baseline, nproj  # noqa: F401  (fixtures)
from test_derive import needs_cadgen


def _mt(paths):
    return {str(p): p.stat().st_mtime_ns for p in paths}


def _model_step(root, d):
    return F.abs_path(root, d.manifest["model_step"])


# ================================================================ step
def test_model_step_has_every_placement_at_its_placed_position(nproj):
    """C: whole-machine STEP = every placement, in world coordinates (rail built at origin, placed at x=100)."""
    d = H.run_derive(nproj, "step").ok()
    tree = F.step_tree(_model_step(nproj, d))
    rails = [s for _, lbl, s in F.leaves(tree) if lbl == "rail" or (lbl or "").startswith("rail")]
    assert len(rails) == 3, f"3 rail placements expected in model.step, got labels {[l for _, l, _ in F.leaves(tree)]}"
    centres = sorted(tuple(round(v, 4) for v in (s.bounding_box().center().X, s.bounding_box().center().Y,
                                                 s.bounding_box().center().Z)) for s in rails)
    assert centres == sorted(tuple(float(x) for x in t) for t in F.RAIL_AT), centres
    n_shaped = sum(F.QTY[p] for p in ("rail", "foot", "plate", "screw", "knob", "slider"))
    assert len(tree.solids()) == n_shaped, f"model.step must hold one solid per placement ({n_shaped}), got {len(tree.solids())}"


def test_model_step_groups_by_node_path_nested(nproj):
    """C: frame/ -> group frame; tower/carriage -> a carriage group INSIDE the tower group, holding the slider."""
    d = H.run_derive(nproj, "step").ok()
    tree = F.step_tree(_model_step(nproj, d))
    tower = F.find_group(tree, lambda l: l == "tower")
    assert tower is not None, f"no 'tower' group in model.step: {[(p, l) for p, l, _ in F.leaves(tree)]}"
    carriage = F.find_group(tower, lambda l: l in ("carriage", "tower/carriage"))
    assert carriage is not None and carriage is not tower, "tower/carriage must be a group nested inside tower"
    assert "slider" in [l for _, l, _ in F.leaves(carriage)]
    frame = F.find_group(tree, lambda l: l == "frame")
    assert frame is not None and sum(1 for _, l, _ in F.leaves(frame) if (l or "").startswith("rail")) == 3


def test_step_scope_part_entries_carry_qty_from_placements(nproj):
    d = H.run_derive(nproj, "step").ok()
    rail = H.entry(d, "rail")
    assert rail["qty"] == 3
    steps = [f for f in H.file_list(rail, nproj) if f.suffix in (".step", ".stp")]
    assert len(steps) == 1
    size = H.step_bbox(steps[0]).size
    assert (size.X, size.Y, size.Z) == pytest.approx((600, 20, 20), abs=1e-6)


def test_step_after_editing_one_part_file_recomputes_only_that_part(nproj):
    """DW1 (step): edit frame/foot.py -> the step scope gives the new foot, other part files are reused untouched."""
    d1 = H.run_derive(nproj, "step").ok()
    rail_files = H.file_list(H.entry(d1, "rail"), nproj)
    mt = _mt(rail_files)
    H.edit(F.part_file(nproj, "frame/foot.py"), "Box(30, 30,", "Box(30, 30, 3 * ")
    d2 = H.run_derive(nproj, "step").ok()
    assert d2.computed >= 1 and d2.reused >= 1 and d2.reused + d2.computed == d2.parts_k, d2.log[-800:]
    assert _mt(rail_files) == mt, "the rail's derived STEP must be reused untouched"
    foot = [f for f in H.file_list(H.entry(d2, "foot"), nproj) if f.suffix in (".step", ".stp")][0]
    assert H.step_bbox(foot).size.Z == pytest.approx(30, abs=1e-6)
    feet = [s for _, l, s in F.leaves(F.step_tree(_model_step(nproj, d2))) if (l or "").startswith("foot")]
    assert len(feet) == 4 and all(s.bounding_box().size.Z == pytest.approx(30, abs=1e-6) for s in feet)


# ================================================================ bought
def test_bought_qty_is_the_number_of_placements_and_rows_keep_theirs(nproj):
    """DW3 (bought): screw placed 4x -> qty 4; the pipe row keeps qty 3."""
    d = H.run_derive(nproj, "bought").ok()
    rows = {r["code"]: r for r in d.manifest["bought"]}
    assert rows["M5x10"]["qty"] == 4, rows
    assert rows["TR50"]["qty"] == 3, rows


def test_bought_after_editing_one_part_file_gives_the_new_row(nproj):
    """DW1 (bought): edit frame/screw.py's code -> the list shows the new code with qty 4, the old one is gone."""
    H.run_derive(nproj, "bought").ok()
    H.edit(F.part_file(nproj, "frame/screw.py"), 'code="M5x10", desc="Screw M5x10"', 'code="M6x10", desc="Screw M6x10"')
    d = H.run_derive(nproj, "bought").ok()
    rows = {r["code"]: r["qty"] for r in d.manifest["bought"]}
    assert rows.get("M6x10") == 4 and "M5x10" not in rows, rows


def test_adding_a_placement_raises_the_bought_qty(nproj):
    H.run_derive(nproj, "bought").ok()
    H.edit(F.part_file(nproj, "frame/__init__.py"), "Pos(x, y, 5)) for x, y in FEET]", "Pos(x, y, 5)) for x, y in FEET + [(9, 9)]]")
    d = H.run_derive(nproj, "bought").ok()
    assert {r["code"]: r["qty"] for r in d.manifest["bought"]}["M5x10"] == 5


# ================================================================ print
def test_print_gives_one_stl_per_variant_from_its_own_geometry(nproj):
    """C: knob (2 placements) and slider -> one print entry each, qty from placements, STL of the part itself."""
    d = H.run_derive(nproj, "print").ok()
    assert {e["id"] for e in d.manifest["parts"]} == {"knob", "slider"}, d.manifest["parts"]
    knob = H.entry(d, "knob")
    assert knob["qty"] == 2
    stls = [f for f in H.file_list(knob, nproj) if f.suffix == ".stl"]
    assert len(stls) == 1 and H.stl_triangles(stls[0]) > 100
    assert sorted(F.stl_bbox_size(stls[0])) == pytest.approx([8, 8, 8], abs=0.2)
    sl = [f for f in H.file_list(H.entry(d, "slider"), nproj) if f.suffix == ".stl"][0]
    assert sorted(F.stl_bbox_size(sl)) == pytest.approx([7, 10, 20], abs=1e-3), "slider STL laid flat: 7 x 10 x 20"


def test_print_after_editing_one_part_file_recomputes_only_that_part(nproj):
    """DW1 (print): edit tower/knob.py -> 1 computed (knob), 1 reused (slider)."""
    d1 = H.run_derive(nproj, "print").ok()
    sl = H.file_list(H.entry(d1, "slider"), nproj)
    mt = _mt(sl)
    H.edit(F.part_file(nproj, "tower/knob.py"), "Sphere(params.KNOB_R)", "Sphere(params.KNOB_R + 1)")
    d2 = H.run_derive(nproj, "print").ok()
    assert d2.counts == (2, 1, 1), d2.log[-800:]
    assert _mt(sl) == mt
    stl = [f for f in H.file_list(H.entry(d2, "knob"), nproj) if f.suffix == ".stl"][0]
    assert max(F.stl_bbox_size(stl)) == pytest.approx(10, abs=0.2)


# ================================================================ viewer
def _viewer_parts(root, d):
    vd = F.abs_path(root, d.manifest["viewer_dir"])
    return json.loads((vd / "parts.json").read_text())["parts"]


def test_viewer_lists_one_entry_per_placement_by_placement_name(nproj):
    d = H.run_derive(nproj, "viewer").ok()
    ids = sorted(p["id"] for p in _viewer_parts(nproj, d))
    want = sorted(["rail", "rail_2", "rail_3", "foot", "foot_2", "foot_3", "foot_4", "plate", "plate_2",
                   "screw", "screw_2", "screw_3", "screw_4", "knob", "knob_2", "slider"])
    assert ids == want, ids
    _, names = H.glb_names(H.find_glb(d, nproj))
    assert set(want) <= names, f"glb is missing placements: {set(want) - names}"


def test_viewer_after_editing_one_part_file_remeshes_only_that_part(nproj):
    """DW1 (viewer): edit frame/foot.py -> only foot meshes are recomputed (one variant, up to its 4 placements)."""
    H.run_derive(nproj, "viewer").ok()
    d0 = H.run_derive(nproj, "viewer").ok()
    assert d0.computed == 0, d0.log[-800:]
    H.edit(F.part_file(nproj, "frame/foot.py"), "Box(30, 30,", "Box(31, 30,")
    d = H.run_derive(nproj, "viewer").ok()
    assert 1 <= d.computed <= 4 and d.reused == d.parts_k - d.computed and d.reused >= 1, d.log[-800:]


# ================================================================ laser (needs the shop-drawings venv)
@needs_cadgen
def test_laser_qty_is_the_number_of_placements(nproj):
    """DW3 (laser): plate placed twice -> one laser entry, qty 2, with a DXF."""
    d = H.run_derive(nproj, "laser").ok()
    assert [e["id"] for e in d.manifest["parts"]] == ["plate"], d.manifest["parts"]
    e = H.entry(d, "plate")
    assert e["qty"] == 2 and e["thickness"] == 2.0 and e["material"] == "S235JR"
    assert any(f.suffix == ".dxf" and f.is_file() for f in H.file_list(e, nproj))


@needs_cadgen
def test_laser_after_editing_one_part_file_gives_the_new_part(nproj):
    """DW1 (laser): edit the plate width (its data file) -> the plate is recomputed; a second run reuses it."""
    d1 = H.run_derive(nproj, "laser").ok()
    old = H.file_list(H.entry(d1, "plate"), nproj)
    (H.cad_dir(nproj) / "data" / "plate_w.txt").write_text("36\n")
    d2 = H.run_derive(nproj, "laser").ok()
    assert d2.counts == (1, 0, 1), d2.log[-800:]
    assert H.file_list(H.entry(d2, "plate"), nproj) != old
    assert H.run_derive(nproj, "laser").ok().counts == (1, 1, 0)
