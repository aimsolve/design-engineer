"""derive.py SCOPE MANIFEST [PART_ID]: per-part derived outputs keyed by inputs (D2/D3).

The model on disk (build/model/parts.json + STEPs) is hand-written by helpers.make_derived_model, so these tests do not
depend on cad_incremental --model. The project's node files write a marker when imported; derive must never import them.
"""
import json
import os
import time
from pathlib import Path

import pytest

import helpers as H

needs_cadgen = pytest.mark.skipif(not H.SHOP_VENV.exists(),
                                  reason=f"cadgen venv not available at {H.SHOP_VENV} (run skills/shop-drawings/scripts/drawings_env.sh once)")


def _mtimes(paths):
    return {str(p): p.stat().st_mtime_ns for p in paths}


# ---------------------------------------------------------------- never imports node code
def test_derive_never_imports_node_code_in_any_scope(dproj):
    for scope in ("bought", "print", "step", "viewer"):
        H.run_derive(dproj, scope).ok()
    assert H.ticks(dproj) == [], f"derive executed or imported project node code: {H.ticks(dproj)}"


# ---------------------------------------------------------------- bought
def test_bought_rows_are_aggregated_by_code_with_quantity(dproj):
    d = H.run_derive(dproj, "bought").ok()
    rows = {r["code"]: r for r in d.manifest["bought"]}
    assert set(rows) == {"M5x10", "NUT-M5"} and len(d.manifest["bought"]) == 2
    assert rows["M5x10"]["qty"] == 3 and rows["NUT-M5"]["qty"] == 4


def test_bought_manifest_follows_a_quantity_change_and_computes_nothing_else(dproj):
    H.run_derive(dproj, "bought").ok()
    H.replace_part(dproj, "screw_b", qty=5)            # fields only, STEP untouched
    d = H.run_derive(dproj, "bought").ok()
    assert {r["code"]: r["qty"] for r in d.manifest["bought"]}["M5x10"] == 7
    assert d.reused + d.computed == d.parts_k


def test_bought_second_run_computes_nothing(dproj):
    first = H.run_derive(dproj, "bought").ok()
    second = H.run_derive(dproj, "bought").ok()
    assert second.computed == 0, second.log
    assert second.manifest["bought"] == first.manifest["bought"]


# ---------------------------------------------------------------- print
def test_print_writes_one_stl_per_print_part(dproj):
    d = H.run_derive(dproj, "print").ok()
    assert d.parts_k == 2 and d.computed == 2 and d.reused == 0, d.log
    ids = {e["id"] for e in d.manifest["parts"]}
    assert ids == {"knob", "cap"}, ids
    for pid in ("knob", "cap"):
        e = H.entry(d, pid)
        assert e["process"] == "print" and "name" in e and "node" in e and "qty" in e
        stls = [f for f in H.file_list(e, dproj) if f.suffix == ".stl"]
        assert len(stls) == 1 and stls[0].is_file() and H.stl_triangles(stls[0]) > 100
        assert stls[0].is_relative_to(dproj / "build/derived")


def test_print_second_run_reuses_everything_and_keeps_files_untouched(dproj):
    d1 = H.run_derive(dproj, "print").ok()
    files = [f for e in d1.manifest["parts"] for f in H.file_list(e, dproj)]
    before = _mtimes(files)
    d2 = H.run_derive(dproj, "print").ok()
    assert d2.counts == (2, 2, 0), d2.log
    assert _mtimes(files) == before


def test_print_after_one_part_change_recomputes_only_that_part(dproj):
    from build123d import Pos, Sphere
    d1 = H.run_derive(dproj, "print").ok()
    cap_files = H.file_list(H.entry(d1, "cap"), dproj)
    cap_before = _mtimes(cap_files)
    old_knob = H.file_list(H.entry(d1, "knob"), dproj)
    H.replace_part(dproj, "knob", Pos(100, 0, 0) * Sphere(6))
    d2 = H.run_derive(dproj, "print").ok()
    assert d2.counts == (2, 1, 1), d2.log
    assert _mtimes(cap_files) == cap_before
    new_knob = H.file_list(H.entry(d2, "knob"), dproj)
    assert new_knob != old_knob and new_knob[0].is_file()
    assert not old_knob[0].exists(), "the stale derived file of the changed part must be garbage-collected"


def test_derive_for_a_single_part_id_touches_only_that_part(dproj):
    d = H.run_derive(dproj, "print", part_id="knob").ok()
    assert [e["id"] for e in d.manifest["parts"]] == ["knob"]
    assert d.computed == 1
    derived = list((dproj / "build/derived").iterdir()) if (dproj / "build/derived").exists() else []
    assert len(derived) == 1, "only the requested part may have a derived result"


# ---------------------------------------------------------------- step
def test_step_scope_writes_part_steps_for_mill_turn_other_and_a_whole_machine_step(dproj):
    d = H.run_derive(dproj, "step").ok()
    ids = {e["id"] for e in d.manifest["parts"]}
    assert {"frame", "shaft", "misc_block"} <= ids
    for pid in ("frame", "shaft", "misc_block"):
        steps = [f for f in H.file_list(H.entry(d, pid), dproj) if f.suffix in (".step", ".stp")]
        assert len(steps) == 1 and steps[0].is_file(), pid
        want = H.step_bbox(dproj / H.by_id(dproj)[pid]["step"]).size
        got = H.step_bbox(steps[0]).size
        assert (got.X, got.Y, got.Z) == pytest.approx((want.X, want.Y, want.Z), abs=1e-6), pid
    ms = Path(d.manifest["model_step"])
    ms = ms if ms.is_absolute() else dproj / ms
    assert ms.is_file()
    whole = H.step_bbox(ms)
    for pid in ("frame", "shaft", "misc_block", "knob", "nut_a"):       # world coordinates: all parts are in the machine
        pb = H.step_bbox(dproj / H.by_id(dproj)[pid]["step"])
        assert whole.min.X <= pb.min.X + 1e-6 and whole.max.X >= pb.max.X - 1e-6, pid
        assert whole.min.Y <= pb.min.Y + 1e-6 and whole.max.Y >= pb.max.Y - 1e-6, pid


def test_step_after_one_part_change_recomputes_only_that_part_and_the_model(dproj):
    from build123d import Box, Pos
    d1 = H.run_derive(dproj, "step").ok()
    shaft_files = H.file_list(H.entry(d1, "shaft"), dproj)
    shaft_bytes = [f.read_bytes() for f in shaft_files]
    shaft_mt = _mtimes(shaft_files)
    ms1 = dproj / d1.manifest["model_step"] if not Path(d1.manifest["model_step"]).is_absolute() else Path(d1.manifest["model_step"])
    h1 = H.step_bbox(ms1).size.Z
    d2 = H.run_derive(dproj, "step").ok()
    assert d2.computed == 0, d2.log
    H.replace_part(dproj, "frame", Pos(0, 0, 0) * Box(30, 10, 50))
    d3 = H.run_derive(dproj, "step").ok()
    assert d3.computed >= 1 and d3.reused >= 2, d3.log
    assert _mtimes(shaft_files) == shaft_mt and [f.read_bytes() for f in shaft_files] == shaft_bytes
    ms3 = Path(d3.manifest["model_step"]); ms3 = ms3 if ms3.is_absolute() else dproj / ms3
    assert H.step_bbox(ms3).size.Z > h1 + 5, "the whole-machine STEP must contain the changed frame"


# ---------------------------------------------------------------- viewer
def test_viewer_glb_contains_every_part_by_id(dproj):
    d = H.run_derive(dproj, "viewer").ok()
    assert d.parts_k == 10 and d.computed == 10 and d.reused == 0, d.log
    glb = H.find_glb(d, dproj)
    j, names = H.glb_names(glb)
    assert set(H.by_id(dproj)) <= names, f"missing from glb: {set(H.by_id(dproj)) - names}"
    assert len(j["meshes"]) >= 10


def test_viewer_no_change_remeshes_nothing_and_file_is_identical(dproj):
    d1 = H.run_derive(dproj, "viewer").ok()
    glb = H.find_glb(d1, dproj)
    data, mt = glb.read_bytes(), glb.stat().st_mtime_ns
    d2 = H.run_derive(dproj, "viewer").ok()
    assert d2.computed == 0 and d2.reused == d2.parts_k, d2.log
    glb2 = H.find_glb(d2, dproj)
    assert glb2 == glb and glb2.read_bytes() == data and glb2.stat().st_mtime_ns == mt


def test_viewer_one_part_change_remeshes_exactly_one_part(dproj):
    from build123d import Box, Pos
    d1 = H.run_derive(dproj, "viewer").ok()
    before = H.find_glb(d1, dproj).read_bytes()
    H.replace_part(dproj, "frame", Pos(0, 0, 0) * Box(30, 10, 50))
    d2 = H.run_derive(dproj, "viewer").ok()
    assert d2.computed == 1 and d2.reused == d2.parts_k - 1, d2.log
    glb = H.find_glb(d2, dproj)
    assert glb.read_bytes() != before
    assert set(H.by_id(dproj)) <= H.glb_names(glb)[1]


# ---------------------------------------------------------------- laser / bent
def test_laser_scope_with_no_sheet_parts_is_an_empty_cheap_noop(tmp_path):
    root = H.make_derived_model(tmp_path / "proj")
    parts = [p for p in H.read_parts(root) if p["process"] not in ("laser", "bent")]
    H._write_parts_json(root, parts)
    d = H.run_derive(root, "laser").ok()
    assert d.manifest["parts"] == [] and d.counts == (0, 0, 0), d.log


@needs_cadgen
def test_laser_and_bent_parts_get_dxf_and_bent_also_pdf(dproj):
    d = H.run_derive(dproj, "laser").ok()
    assert {e["id"] for e in d.manifest["parts"]} == {"laser_plate", "bent_angle"}
    plate = H.file_list(H.entry(d, "laser_plate"), dproj)
    bent = H.file_list(H.entry(d, "bent_angle"), dproj)
    assert any(f.suffix == ".dxf" for f in plate)
    assert {".dxf", ".pdf"} <= {f.suffix for f in bent}
    for f in plate + bent:
        assert f.is_file() and f.stat().st_size > 0
        if f.suffix == ".dxf":
            assert "ENTITIES" in f.read_text(errors="ignore")
        if f.suffix == ".pdf":
            assert f.read_bytes()[:4] == b"%PDF"
    e = H.entry(d, "laser_plate")
    assert e["material"] == "S235JR" and e["thickness"] == 2.0 and e["qty"] == 2


@needs_cadgen
def test_laser_after_one_sheet_part_change_recomputes_only_that_part(dproj):
    from build123d import Box, Pos
    d1 = H.run_derive(dproj, "laser").ok()
    bent_files = H.file_list(H.entry(d1, "bent_angle"), dproj)
    mt = _mtimes(bent_files)
    assert H.run_derive(dproj, "laser").ok().computed == 0
    H.replace_part(dproj, "laser_plate", Pos(50, 0, 0) * Box(35, 20, 2))
    d3 = H.run_derive(dproj, "laser").ok()
    assert d3.counts == (2, 1, 1), d3.log
    assert _mtimes(bent_files) == mt


# ---------------------------------------------------------------- GC in derive
def test_derive_gc_removes_unreferenced_model_and_derived_files(dproj):
    (dproj / "build/derived/deadbeefdeadbeef").mkdir(parents=True)
    (dproj / "build/derived/deadbeefdeadbeef/stale.txt").write_text("x")
    (dproj / "build/model/step/orphan.step").write_text("x")
    H.run_derive(dproj, "print").ok()
    assert not (dproj / "build/derived/deadbeefdeadbeef").exists()
    assert not (dproj / "build/model/step/orphan.step").exists()
    assert {p["key"] + ".step" for p in H.read_parts(dproj)} == set(H.step_files(dproj)), "current part STEPs must survive"


# ---------------------------------------------------------------- derived dirs are tied to part keys (decision 14)
def test_derived_meta_lists_part_keys_not_content_hashes(dproj):
    H.run_derive(dproj, "print").ok()
    H.run_derive(dproj, "step").ok()
    keys = {p["key"] for p in H.read_parts(dproj)}
    contents = {p["sha256"] for p in H.read_parts(dproj)}
    assert not (keys & contents), "precondition: the fixture's keys are not content hashes"
    metas = list((dproj / "build/derived").glob("*/meta.json"))
    assert metas, "derived dirs must carry meta.json"
    for m in metas:
        listed = H.meta_part_keys(m)
        assert listed and listed <= keys, f"{m.parent.name}: meta.json parts must be current part keys, got {listed - keys}"


def test_derive_key_ignores_step_bytes_of_a_part_with_the_same_key(dproj):
    H.run_derive(dproj, "print").ok()
    H.foreign_machine(dproj)
    d = H.run_derive(dproj, "print").ok()
    assert d.counts == (2, 2, 0), d.log


def test_derive_gc_keeps_a_derived_dir_iff_all_its_listed_keys_are_current(dproj):
    keys = H.part_keys(dproj)
    past = time.time() - 3600
    for name, listed in (("1" * 16, [keys["knob"], keys["cap"]]), ("2" * 16, [keys["knob"], "0" * 64])):
        d = dproj / "build/derived" / name
        d.mkdir(parents=True)
        (d / "out.stl").write_text("x")
        (d / "meta.json").write_text(json.dumps({"parts": listed}))
        for p in (d / "out.stl", d / "meta.json", d):
            os.utime(p, (past, past))
    H.run_derive(dproj, "bought").ok()
    assert (dproj / "build/derived" / ("1" * 16)).exists(), "all listed keys are current: the dir must be kept"
    assert not (dproj / "build/derived" / ("2" * 16)).exists(), "one listed key is stale: the dir must be collected"
