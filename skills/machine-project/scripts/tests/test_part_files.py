"""One part, one file; a node, a folder: cad_incremental --model on the folder layout.

Done-when map (from the spec):
  DW1  editing one part file rebuilds only that part (model side; the derive side is in test_part_files_derive.py)
  DW3  identical parts = one file with parameters; qty = number of placements
  C*   contract items the done-when conditions rest on (variants, placement, cache units, loud errors, old layout)
The synthetic project is described in partfiles.py.
"""
import json
from pathlib import Path

import pytest

import helpers as H
import partfiles as F
from partfiles import new_baseline, nproj  # noqa: F401  (fixtures)


def _rebuild(root):
    H.clear_ticks(root)
    return F.run_model(root).ok()


# ================================================================ cold build: what the model contains
def test_cold_build_has_one_entry_per_variant_with_qty_from_placements(new_baseline):
    """DW3: one parts.json entry per variant; qty = number of placements (rows keep their own qty)."""
    root, m, _ = new_baseline
    m.ok()
    ps = F.parts(root)
    assert set(ps) == set(F.QTY), f"parts.json ids {sorted(ps)} != {sorted(F.QTY)}"
    for pid, q in F.QTY.items():
        assert ps[pid].get("qty") == q, f"{pid}: qty {ps[pid].get('qty')} != {q}"
    for pid in ("rail", "foot", "plate", "screw", "knob", "slider"):
        assert len(F.placements(ps[pid])) == F.QTY[pid], f"{pid}: qty must equal the number of placements"


def test_default_and_equal_numeric_arguments_are_one_variant_built_once(new_baseline):
    """C: build(), build(length=600) and build(length=600.0) are ONE variant, built once in a cold run."""
    root, m, ticks = new_baseline
    m.ok()
    ids = [p["id"] for p in H.read_parts(root)]
    assert ids.count("rail") == 1 and not any(i.startswith("rail_") and i != "rail" for i in ids), ids
    assert ticks.count("rail") == 1, f"one variant must be built once, rail.build ran {ticks.count('rail')} times"
    assert ticks.count("foot") == 1 and ticks.count("screw") == 1, "a part placed 4 times is built once"


def test_part_step_is_in_its_own_coordinates_and_placements_carry_the_position(new_baseline):
    """C: the part STEP is the part at its own origin; the placement loc (4x4) round-trips to the placed position."""
    root, m, _ = new_baseline
    m.ok()
    rail = F.parts(root)["rail"]
    c = F.step_center(root, rail)
    assert c == pytest.approx((0, 0, 0), abs=1e-6), f"rail STEP must be centred at its own origin, got {c}"
    size = H.step_bbox(root / rail["step"]).size
    assert (size.X, size.Y, size.Z) == pytest.approx((600, 20, 20), abs=1e-6)
    got = sorted(F.origin(pl) for pl in F.placements(rail))
    assert got == sorted(map(tuple, map(lambda t: tuple(float(x) for x in t), F.RAIL_AT))), got
    feet = sorted(F.origin(pl) for pl in F.placements(F.parts(root)["foot"]))
    assert feet == sorted((float(x), float(y), -20.0) for x, y in F.FEET), feet


def test_sub_assembly_placement_composes_locations(new_baseline):
    """C: carriage places slider at (5,0,0); tower places the carriage list at (0,0,50) -> slider ends at (5,0,50)."""
    root, m, _ = new_baseline
    m.ok()
    ps = F.parts(root)
    assert [F.origin(pl) for pl in F.placements(ps["slider"])] == [(5.0, 0.0, 50.0)]
    knobs = sorted(F.origin(pl) for pl in F.placements(ps["knob"]))
    assert knobs == [(5.0, 0.0, 80.0), (500.0, 0.0, 0.0)], knobs


def test_node_paths_follow_folders_and_placement_node_is_the_placing_node(new_baseline):
    """C: part `node` defaults to the part file's folder path; a placement's node is the node that placed it."""
    root, m, _ = new_baseline
    m.ok()
    ps = F.parts(root)
    assert ps["rail"]["node"] == "frame" and ps["knob"]["node"] == "tower"
    assert ps["slider"]["node"] == "tower/carriage", ps["slider"]["node"]
    assert {pl["node"] for pl in F.placements(ps["rail"])} == {"frame"}
    assert [pl["node"] for pl in F.placements(ps["slider"])] == ["tower/carriage"]
    assert sorted(pl["node"] for pl in F.placements(ps["knob"])) == ["tower", "tower/carriage"]


def test_placement_names_default_to_id_then_suffixes_and_are_unique(new_baseline):
    """C: first placement = the part id, repeats id_2, id_3...; all placement names unique in the model."""
    root, m, _ = new_baseline
    m.ok()
    ps = F.parts(root)
    assert sorted(pl["name"] for pl in F.placements(ps["rail"])) == ["rail", "rail_2", "rail_3"]
    assert sorted(pl["name"] for pl in F.placements(ps["foot"])) == ["foot", "foot_2", "foot_3", "foot_4"]
    assert sorted(pl["name"] for pl in F.placements(ps["knob"])) == ["knob", "knob_2"]
    names = [pl["name"] for _, pl in F.all_placements(root)]
    assert len(names) == len(set(names)), f"duplicate placement names: {names}"


def test_a_part_only_read_for_a_dimension_is_not_in_the_model(new_baseline):
    """C: gauge.build() is called by slider only to read a size; it is not placed and must not be a model part."""
    root, m, ticks = new_baseline
    m.ok()
    assert "gauge" not in F.parts(root)
    assert "gauge" not in json.dumps(json.loads((root / "build/model/parts.json").read_text()))
    assert F.parts(root)["slider"]
    assert "gauge" in ticks, "precondition: gauge.build() did run (slider used its size)"


def test_row_without_shape_keeps_its_qty_and_has_no_step(new_baseline):
    """C: a bought row (shape None) returned by the node without place keeps its own qty field."""
    root, m, _ = new_baseline
    m.ok()
    pipe = F.parts(root)["pipe"]
    assert pipe["process"] == "bought" and pipe["qty"] == 3 and pipe["code"] == "TR50" and "step" not in pipe


def test_relative_and_absolute_imports_inside_and_across_nodes_work(new_baseline):
    """C: `from . import _common` (rail, plate) and `from parts.frame import _common` (tower/carriage/slider) build."""
    root, m, _ = new_baseline
    m.ok()
    size = H.step_bbox(root / F.parts(root)["slider"]["step"]).size
    assert (size.X, size.Y, size.Z) == pytest.approx((7, 20, 10), abs=1e-6), "slider = gauge width x _common.section() x 10"


# ================================================================ DW1: incremental, per part file
def test_no_change_rerun_runs_no_part_code(nproj):
    before = F.snapshot_steps(nproj)
    _rebuild(nproj)
    t = H.ticks(nproj)
    assert not set(t) & set(F.PART_FILES), f"part code ran on a no-change rerun: {t}"
    assert F.snapshot_steps(nproj) == before


def test_editing_one_part_file_rebuilds_only_that_part(nproj):
    """DW1: edit frame/foot.py -> only foot.build runs, the rebuilt line names frame/foot, other STEPs untouched."""
    before = F.parts(nproj)
    steps = F.snapshot_steps(nproj)
    H.edit(F.part_file(nproj, "frame/foot.py"), "Box(30, 30,", "Box(32, 30,")
    m = _rebuild(nproj)
    t = H.ticks(nproj)
    assert [x for x in t if x in F.PART_FILES] == ["foot"], f"only foot may rebuild, ran: {t}"
    assert m.rebuilt("frame/foot"), f"rebuilt line must name frame/foot: {m.rebuilt_tokens}"
    for other in ("frame/rail", "frame/plate", "tower/knob", "tower/carriage/slider"):
        assert not m.rebuilt(other), f"{other} must not be rebuilt: {m.rebuilt_tokens}"
    after = F.parts(nproj)
    assert after["foot"]["key"] != before["foot"]["key"]
    assert H.step_bbox(nproj / after["foot"]["step"]).size.X == pytest.approx(32, abs=1e-6)
    now = F.snapshot_steps(nproj)
    for pid in ("rail", "plate", "screw", "knob", "slider"):
        assert after[pid]["key"] == before[pid]["key"], pid
        name = Path(before[pid]["step"]).name
        assert now.get(name) == steps[name], f"STEP of untouched part {pid} was rewritten"
    assert after["foot"]["qty"] == 4 and len(F.placements(after["foot"])) == 4


def test_param_read_by_one_part_only_rebuilds_only_that_part(nproj):
    """DW1: KNOB_R is read by tower/knob.py only -> only knob rebuilds (both placements keep pointing at the new STEP)."""
    before = F.parts(nproj)
    H.edit(H.cad_dir(nproj) / "params.py", "KNOB_R = 4.0", "KNOB_R = 5.0")
    m = _rebuild(nproj)
    assert [x for x in H.ticks(nproj) if x in F.PART_FILES] == ["knob"], H.ticks(nproj)
    assert m.rebuilt("tower/knob") and not m.rebuilt("frame/foot")
    after = F.parts(nproj)
    assert after["knob"]["key"] != before["knob"]["key"]
    assert {k: v["key"] for k, v in after.items() if k != "knob"} == {k: v["key"] for k, v in before.items() if k != "knob"}
    assert H.step_bbox(nproj / after["knob"]["step"]).size.X == pytest.approx(10, abs=1e-3)


def test_unused_param_change_rebuilds_nothing(nproj):
    H.edit(H.cad_dir(nproj) / "params.py", "UNUSED = 1.0", "UNUSED = 2.0")
    _rebuild(nproj)
    assert not set(H.ticks(nproj)) & set(F.PART_FILES), H.ticks(nproj)


def test_editing_a_node_helper_rebuilds_exactly_the_parts_that_use_it(nproj):
    """C: frame/_common.py is used by rail, plate (relative import) and tower/carriage/slider (absolute import)."""
    before = F.parts(nproj)
    H.edit(F.part_file(nproj, "frame/_common.py"), "SECTION = 20.0", "SECTION = 22.0")
    _rebuild(nproj)
    ran = {x for x in H.ticks(nproj) if x in F.PART_FILES} - {"gauge"}      # gauge may be re-read by slider
    assert ran == F.COMMON_USERS, f"exactly {sorted(F.COMMON_USERS)} must rebuild, ran {sorted(ran)}"
    after = F.parts(nproj)
    for pid in ("foot", "screw", "knob"):
        assert after[pid]["key"] == before[pid]["key"], pid
    assert H.step_bbox(nproj / after["rail"]["step"]).size.Y == pytest.approx(22, abs=1e-6)


def test_editing_a_part_read_by_another_part_rebuilds_the_reader(nproj):
    """C: slider imports gauge (a project module it read a size from): editing gauge rebuilds slider, not the rest."""
    H.edit(F.part_file(nproj, "tower/carriage/gauge.py"), "Box(7, 7, 7)", "Box(9, 7, 7)")
    _rebuild(nproj)
    ran = {x for x in H.ticks(nproj) if x in F.PART_FILES}
    assert "slider" in ran and not ran - {"slider", "gauge"}, ran
    assert H.step_bbox(nproj / F.parts(nproj)["slider"]["step"]).size.X == pytest.approx(9, abs=1e-6)
    assert "gauge" not in F.parts(nproj)


def test_a_file_opened_by_one_part_rebuilds_only_that_part(nproj):
    (H.cad_dir(nproj) / "data" / "plate_w.txt").write_text("35\n")
    _rebuild(nproj)
    assert [x for x in H.ticks(nproj) if x in F.PART_FILES] == ["plate"], H.ticks(nproj)
    assert H.step_bbox(nproj / F.parts(nproj)["plate"]["step"]).size.Y == pytest.approx(35, abs=1e-6)


def test_moving_a_part_in_the_node_rebuilds_no_geometry_and_updates_the_placement(nproj):
    """C: a placement change in frame/__init__.py runs no part file; keys and STEPs stay, the placement moves."""
    before = F.parts(nproj)
    steps = F.snapshot_steps(nproj)
    H.edit(F.part_file(nproj, "frame/__init__.py"), "Pos(0, -200, 0)", "Pos(0, -250, 0)")
    _rebuild(nproj)
    assert not set(H.ticks(nproj)) & set(F.PART_FILES), f"a placement change must not run part code: {H.ticks(nproj)}"
    after = F.parts(nproj)
    assert {k: v["key"] for k, v in after.items()} == {k: v["key"] for k, v in before.items()}
    assert F.snapshot_steps(nproj) == steps
    assert sorted(F.origin(pl) for pl in F.placements(after["plate"])) == [(0.0, -250.0, 0.0), (0.0, -100.0, 0.0)]


def test_placing_a_variant_once_more_changes_qty_without_rebuilding(nproj):
    """DW3: a fifth foot placement -> qty 5, no part code runs."""
    H.edit(F.part_file(nproj, "frame/__init__.py"), "for x, y in FEET]\n    out += [place(plate",
           "for x, y in FEET + [(150, 150)]]\n    out += [place(plate")
    _rebuild(nproj)
    assert not set(H.ticks(nproj)) & set(F.PART_FILES), H.ticks(nproj)
    foot = F.parts(nproj)["foot"]
    assert foot["qty"] == 5 and len(F.placements(foot)) == 5


def test_a_new_argument_value_is_a_second_variant_and_only_it_is_built(nproj):
    """DW3/C: rail.build(length=400) is a new variant (its own entry and STEP); the 600 variant is reused."""
    before = F.parts(nproj)
    H.edit(F.part_file(nproj, "frame/__init__.py"), "place(rail.build(length=600.0), Pos(100, 200, 0))]",
           "place(rail.build(length=600.0), Pos(100, 200, 0)), place(rail.build(length=400), Pos(100, 300, 0))]")
    _rebuild(nproj)
    assert H.ticks(nproj).count("rail") == 1, f"only the new variant may be built: {H.ticks(nproj)}"
    ps = F.parts(nproj)
    assert ps["rail"]["key"] == before["rail"]["key"] and ps["rail"]["qty"] == 3
    assert ps["rail_400"]["qty"] == 1 and ps["rail_400"]["key"] != ps["rail"]["key"]
    assert H.step_bbox(nproj / ps["rail_400"]["step"]).size.X == pytest.approx(400, abs=1e-6)
    assert [F.origin(pl) for pl in F.placements(ps["rail_400"])] == [(100.0, 300.0, 0.0)]


def test_explicit_placement_name_is_used(nproj):
    H.edit(F.part_file(nproj, "frame/__init__.py"), "place(plate.build(), Pos(0, -200, 0))",
           "place(plate.build(), Pos(0, -200, 0), name=\"plate_rear\")")
    _rebuild(nproj)
    assert sorted(pl["name"] for pl in F.placements(F.parts(nproj)["plate"])) == ["plate", "plate_rear"]


def test_rotated_placement_matrix_round_trips(nproj):
    """C: loc is a full 4x4 transform: a 90 deg turn about Z maps the part's +X to +Y."""
    H.edit(F.part_file(nproj, "tower/__init__.py"), "from build123d import Pos", "from build123d import Pos, Location")
    H.edit(F.part_file(nproj, "tower/__init__.py"), "place(knob.build(), Pos(500, 0, 0))",
           "place(knob.build(), Location((500, 0, 0), (0, 0, 90)))")
    _rebuild(nproj)
    pl = [p for p in F.placements(F.parts(nproj)["knob"]) if F.origin(p)[0] == pytest.approx(500)]
    assert len(pl) == 1, F.placements(F.parts(nproj)["knob"])
    assert F.apply(pl[0]["loc"], (1, 0, 0)) == pytest.approx((500, 1, 0), abs=1e-6)


def test_non_json_argument_is_allowed_uncached_and_warned(nproj):
    """C: an argument that is not JSON (a Location) builds, is rebuilt on every run, and warns on stderr naming the part."""
    files = {"frame/bracket.py": '''
        from build123d import Box
        from geom import P
        {TICK}

        def build(at=None):
            _tick("bracket")
            return P("bracket", Box(4, 4, 4), "#123456", process="other")
        '''}
    (F.part_file(nproj, "frame/bracket.py")).write_text(F._src(files["frame/bracket.py"]))
    H.edit(F.part_file(nproj, "frame/__init__.py"), "from . import rail, foot, plate, screw",
           "from . import rail, foot, plate, screw, bracket")
    H.edit(F.part_file(nproj, "frame/__init__.py"), "    return out\n",
           "    out.append(place(bracket.build(at=Pos(1, 2, 3)), Pos(0, 0, 100)))\n    return out\n")
    m1 = _rebuild(nproj)
    assert "bracket" in H.ticks(nproj) and "bracket" in F.parts(nproj)
    m2 = _rebuild(nproj)
    t = H.ticks(nproj)
    assert "bracket" in t, "a call with a non-JSON argument must not be cached"
    assert not set(t) & (set(F.PART_FILES)), f"the other parts stay cached: {t}"
    for m in (m1, m2):
        assert "warn" in m.err.lower() and "bracket" in m.err, f"warning naming bracket expected on stderr:\n{m.err[-1500:]}"


# ================================================================ loud errors
def _loud(root, *words):
    """The build exits non-zero with a message naming `words`, and it is the framework's error, not a crash of the test project."""
    m = F.run_model(root).failed()
    for bad in ("ImportError", "SyntaxError", "NameError", "ModuleNotFoundError"):
        assert bad not in m.log, f"the build crashed ({bad}) instead of reporting the contract error:\n{m.log[-1500:]}"
    for w in words:
        assert w in m.log, f"the error must name {w!r}:\n{m.log[-1500:]}"
    return m


def test_explicit_qty_on_a_placed_part_is_a_loud_error(nproj):
    H.edit(F.part_file(nproj, "frame/foot.py"), 'material="S235JR")', 'material="S235JR", qty=4)')
    _loud(nproj, "foot", "qty")


def test_two_part_files_with_the_same_part_name_is_a_loud_error_naming_both(nproj):
    H.edit(F.part_file(nproj, "tower/knob.py"), 'P("knob",', 'P("foot",')
    m = _loud(nproj, "foot", "knob")
    assert "frame" in m.log and "tower" in m.log, f"the error must name both part files (frame/foot, tower/knob):\n{m.log[-1500:]}"


def test_two_variants_of_one_file_with_the_same_name_is_a_loud_error(nproj):
    H.edit(F.part_file(nproj, "frame/rail.py"), 'name = "rail" if length == 600 else "rail_%d" % int(length)', 'name = "rail"')
    H.edit(F.part_file(nproj, "frame/__init__.py"), "place(rail.build(length=600.0), Pos(100, 200, 0))]",
           "place(rail.build(length=600.0), Pos(100, 200, 0)), place(rail.build(length=400), Pos(100, 300, 0))]")
    _loud(nproj, "rail", "400", "600")


def test_duplicate_explicit_placement_name_is_a_loud_error(nproj):
    H.edit(F.part_file(nproj, "frame/__init__.py"), "place(plate.build(), Pos(0, -200, 0))",
           "place(plate.build(), Pos(0, -200, 0), name=\"foot\")")
    _loud(nproj, "foot", "plate")


def test_changing_the_shape_of_a_placed_copy_is_a_loud_error(nproj):
    H.edit(F.part_file(nproj, "frame/__init__.py"), "from build123d import Pos", "from build123d import Box, Pos")
    H.edit(F.part_file(nproj, "frame/__init__.py"), "place(plate.build(), Pos(0, -200, 0))",
           "{**place(plate.build(), Pos(0, -200, 0)), \"shape\": Box(1, 1, 1)}")
    _loud(nproj, "plate", "shape")
