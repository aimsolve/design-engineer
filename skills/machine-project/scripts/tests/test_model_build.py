"""cad_incremental.py --model: incremental build of a code-model project, on the folder layout.

Each test names the "done when" condition it serves: D1 one param -> only the part file that reads it (and the node placing it)
rebuilt, D2 no-change builds nothing, D3 one-part edit -> only its outputs change, D4 cache holds one model (GC).
Build units are part files and node __init__s (helpers.UNITS); `H.units_for(pid, ...)` = the part files of those parts plus the
nodes that place them, which is what one edit of a part's inputs rebuilds.
"""
import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

import helpers as H


# ------------------------------------------------------------------ first run (D1 baseline, D3 correct files)
def test_first_run_builds_every_node_and_writes_the_model(baseline):
    root, run = baseline
    run.ok()
    assert run.summary == (H.N_UNITS, 0, len(H.PART_IDS)), run.log[-800:]
    assert run.rebuilt == H.UNITS, run.rebuilt
    assert run.has_rebuilt_line
    parts = H.by_id(root)
    assert set(parts) == H.PART_IDS
    for pid, p in parts.items():
        for key in ("id", "name", "node", "func", "process", "color", "key", "step"):
            assert key in p, (pid, key)
        assert re.fullmatch(r"[0-9a-f]{64}", p["key"]), (pid, p["key"])
        assert p["step"] == f"build/model/step/{p['key']}.step"       # named by the part key (inputs), not by content
        f = root / p["step"]
        assert f.is_file(), f"{pid}: STEP file missing"
        content = hashlib.sha256(f.read_bytes()).hexdigest()
        assert p["key"] != content, f"{pid}: the key must be derived from inputs, not be the STEP content sha"
        assert p.get("sha256", content) == content, f"{pid}: the informational sha256 field must describe the file"
        assert p["node"] == H.OWNER[pid] or p["node"].endswith(H.OWNER[pid])
    # extra JSON fields of the part dict ride along; the shape and any non-JSON value do not
    assert parts["laser_plate"]["material"] == "S235JR" and parts["laser_plate"]["thickness"] == 2.0
    assert parts["screw_a"]["code"] == "M5x10" and parts["screw_a"]["qty"] == 2
    assert {pid: p["qty"] for pid, p in parts.items()} == H.QTY, "qty = number of placements"
    assert all("shape" not in p for p in parts.values())
    nodes = json.loads((root / "build/model/nodes.json").read_text())
    text = json.dumps(nodes)
    for node in H.FUNCS:
        assert node in text, f"nodes.json has no record for {node}"
    for pid in H.PART_IDS:
        assert pid in text, f"nodes.json does not list part {pid} as produced"


def test_step_output_is_deterministic_across_clean_runs(baseline, fresh):
    root, run = baseline
    run.ok()
    H.run_model(fresh).ok()          # different directory, different time: nothing may leak into the STEP bytes
    a, b = H.by_id(root), H.by_id(fresh)
    assert {k: v["key"] for k, v in a.items()} == {k: v["key"] for k, v in b.items()}, "same inputs must give the same keys"
    for pid in a:
        assert (root / a[pid]["step"]).read_bytes() == (fresh / b[pid]["step"]).read_bytes(), pid


def test_part_ids_are_viewer_ids(baseline):
    root, run = baseline
    run.ok()
    for p in H.read_parts(root):
        import re
        assert p["id"] == re.sub(r"_+", "_", re.sub(r"[^\w]+", "_", p["name"])).strip("_")


def test_duplicate_part_id_is_a_hard_error(project):
    assert H.read_parts(project), "precondition: a valid model exists"
    cad = H.cad_dir(project)
    H.write_node(project, "dup", {"frame_x": H.PART_FILES["frame"]["frame"].replace('"frame"', '"frame!"')
                                  .replace('_tick("frame!")', '_tick("dup")')})
    (cad / "assemble.py").write_text(H.assemble_src(extra=("dup",)))
    r = H.run_model(project)
    assert r.rc != 0, "two parts normalising to the id 'frame' must abort the build"
    assert "duplicate" in r.log.lower() and "frame" in r.log, r.log[-800:]


# ------------------------------------------------------------------ D2: no change builds nothing
def test_no_change_rerun_runs_no_node_code(project):
    assert H.read_parts(project), "precondition: a built model exists"
    before_steps = H.step_files(project)
    before_vol = json.loads((project / "assembled.json").read_text())
    H.clear_ticks(project)
    r = H.run_model(project).ok()
    assert r.summary == (0, H.N_UNITS, len(H.PART_IDS)), r.log[-800:]
    assert not r.has_rebuilt_line
    assert H.ticks(project) == [], f"part or node code executed on a no-change rerun: {H.ticks(project)}"
    assert H.step_files(project) == before_steps, "part STEP files were rewritten"
    after_vol = json.loads((project / "assembled.json").read_text())
    assert set(after_vol) == set(before_vol)
    for k, v in before_vol.items():     # the reused build() hands the assembler real shapes read back from the STEPs
        assert after_vol[k] == pytest.approx(v, rel=1e-6), k


def test_touching_files_without_changing_content_rebuilds_nothing(project):
    cad = H.cad_dir(project)
    for f in [cad / "params.py", cad / "parts" / "_helper.py", cad / "parts" / "frame" / "frame.py", cad / "parts" / "frame" / "__init__.py",
              cad / "data" / "dims.txt"]:
        f.write_text(f.read_text())          # new mtime, same bytes
    H.clear_ticks(project)
    r = H.run_model(project).ok()
    assert r.summary[0] == 0 and H.ticks(project) == [], r.log[-800:]


# ------------------------------------------------------------------ D1/D3: exactly the affected node rebuilds
def _other_steps_untouched(project, before, changed_ids):
    after = H.step_files(project)
    for pid, p in before["parts"].items():
        if pid not in changed_ids:
            name = Path(p["step"]).name
            assert after.get(name) == before["steps"][name], f"STEP of unaffected part {pid} was touched"


def _snapshot(project):
    return {"parts": H.by_id(project), "steps": H.step_files(project)}


def test_param_read_by_one_part_rebuilds_only_that_part_and_its_node(project):
    before = _snapshot(project)
    H.edit(H.cad_dir(project) / "params.py", "A_W = 10.0", "A_W = 14.0")
    H.clear_ticks(project)
    r = H.run_model(project).ok()
    assert r.summary == (2, H.N_UNITS - 2, len(H.PART_IDS)), r.log[-800:]
    assert r.rebuilt == H.units_for("frame"), r.rebuilt
    assert set(H.ticks(project)) == {"frame", "node_frame"}
    after = H.by_id(project)
    assert after["frame"]["key"] != before["parts"]["frame"]["key"]
    assert {k: v["key"] for k, v in after.items() if k != "frame"} == \
           {k: v["key"] for k, v in before["parts"].items() if k != "frame"}
    _other_steps_untouched(project, before, {"frame"})
    assert H.step_bbox(project / after["frame"]["step"]).size.X == pytest.approx(14.0, abs=1e-6)


def test_param_read_by_two_parts_rebuilds_exactly_those_two(project):
    before = _snapshot(project)
    H.edit(H.cad_dir(project) / "params.py", "PLATE_T = 2.0", "PLATE_T = 3.0")
    r = H.run_model(project).ok()
    assert r.rebuilt == H.units_for("laser_plate", "bent_angle"), r.log[-800:]
    assert r.summary[0] == 4
    _other_steps_untouched(project, before, {"laser_plate", "bent_angle"})


def test_param_nobody_reads_rebuilds_nothing(project):
    before = _snapshot(project)
    H.edit(H.cad_dir(project) / "params.py", "UNUSED = 99.0", "UNUSED = 5.0")
    H.clear_ticks(project)
    r = H.run_model(project).ok()
    assert r.summary[0] == 0 and H.ticks(project) == [], r.log[-800:]
    assert H.step_files(project) == before["steps"]


def test_edited_part_file_rebuilds_that_part_and_gives_it_a_new_key(project):
    """The module sha is an input: a comment-only edit is accepted to change the key (geometry, and so the STEP bytes, do not)."""
    before = _snapshot(project)
    knob_bytes = (project / before["parts"]["knob"]["step"]).read_bytes()
    f = H.cad_dir(project) / "parts" / "printed" / "knob.py"
    f.write_text(f.read_text() + "\n# a comment: an input changes, geometry does not\n")
    H.clear_ticks(project)
    r = H.run_model(project).ok()
    assert r.rebuilt == H.units_for("knob") and set(H.ticks(project)) == {"knob", "node_printed"}, r.log[-800:]
    after = H.by_id(project)
    assert after["knob"]["key"] != before["parts"]["knob"]["key"], "an input of the part changed: the key must change"
    assert {k: v["key"] for k, v in after.items() if k != "knob"} == {k: v["key"] for k, v in before["parts"].items() if k != "knob"}
    assert (project / after["knob"]["step"]).read_bytes() == knob_bytes, "same geometry still gives the same STEP bytes"
    assert Path(before["parts"]["knob"]["step"]).name not in H.step_files(project), "the old key's STEP must be collected"


def test_edited_imported_helper_rebuilds_every_importer(project):
    before = _snapshot(project)
    H.edit(H.cad_dir(project) / "parts" / "_helper.py", "x * 1", "x * 2")     # frame/frame.py and misc/misc_block.py import it
    r = H.run_model(project).ok()
    assert r.rebuilt == H.units_for("frame", "misc_block"), r.log[-800:]
    after = H.by_id(project)
    assert after["frame"]["key"] != before["parts"]["frame"]["key"]
    assert H.step_bbox(project / after["frame"]["step"]).size.X == pytest.approx(20.0, abs=1e-6)
    _other_steps_untouched(project, before, {"frame", "misc_block"})


def test_changed_data_file_rebuilds_the_part_that_opened_it(project):
    before = _snapshot(project)
    (H.cad_dir(project) / "data" / "dims.txt").write_text("25\n")
    H.clear_ticks(project)
    r = H.run_model(project).ok()
    assert r.rebuilt == H.units_for("data_plate") and set(H.ticks(project)) == {"data_plate", "node_datanode"}, r.log[-800:]
    after = H.by_id(project)
    assert H.step_bbox(project / after["data_plate"]["step"]).size.X == pytest.approx(25.0, abs=1e-6)
    _other_steps_untouched(project, before, {"data_plate"})


def test_part_calling_another_nodes_build_follows_its_dependencies(project):
    """caller/caller_part.py calls callee.build() (another node); C_D is read only by callee/callee_part.py. caller_part's output
    depends on it through the call."""
    before = _snapshot(project)
    assert H.step_bbox(project / before["parts"]["caller_part"]["step"]).size.Z == pytest.approx(3.0 + 1, abs=1e-6)
    H.edit(H.cad_dir(project) / "params.py", "C_D = 3.0", "C_D = 7.0")
    H.clear_ticks(project)
    r = H.run_model(project).ok()
    assert r.rebuilt == H.units_for("callee_part", "caller_part"), r.log[-800:]
    after = H.by_id(project)
    assert H.step_bbox(project / after["callee_part"]["step"]).size.Z == pytest.approx(7.0, abs=1e-6)
    assert H.step_bbox(project / after["caller_part"]["step"]).size.Z == pytest.approx(7.0 + 1, abs=1e-6), \
        "caller_part is stale: the caller's record does not cover what the callee read"
    _other_steps_untouched(project, before, {"callee_part", "caller_part"})
    # and nothing is stale afterwards
    H.clear_ticks(project)
    assert H.run_model(project).ok().summary[0] == 0 and H.ticks(project) == []


def test_rebuilt_output_matches_a_clean_build(project, fresh):
    """Incremental result after an edit == the same project built from scratch with the edit."""
    H.edit(H.cad_dir(project) / "params.py", "A_W = 10.0", "A_W = 14.0")
    H.run_model(project).ok()
    H.edit(H.cad_dir(fresh) / "params.py", "A_W = 10.0", "A_W = 14.0")
    H.run_model(fresh).ok()
    assert H.part_keys(project) == H.part_keys(fresh), "an edited-then-rebuilt model must equal a clean build of the same inputs"


# ------------------------------------------------------------------ D4: the cache holds one model
def test_gc_removes_superseded_part_steps(project):
    old = H.by_id(project)["frame"]["key"]
    H.edit(H.cad_dir(project) / "params.py", "A_W = 10.0", "A_W = 14.0")
    H.run_model(project).ok()
    now = {p["key"] for p in H.read_parts(project)}
    assert old not in now
    assert set(H.step_files(project)) == {s + ".step" for s in now}, "step dir must hold exactly the current parts"


def test_gc_removes_unreferenced_derived_dirs_legacy_cache_and_orphans(project):
    (project / "build/derived/deadbeefdeadbeef").mkdir(parents=True)
    (project / "build/derived/deadbeefdeadbeef/stale.txt").write_text("x")
    (project / "build/cad_cache").mkdir(parents=True)
    (project / "build/cad_cache/junk.bin").write_bytes(b"0" * 1000)
    assert H.read_parts(project), "precondition: a built model exists"
    (project / "build/model/step/orphan.step").write_text("x")
    r = H.run_model(project).ok()
    assert r.summary[0] == 0
    assert not (project / "build/derived/deadbeefdeadbeef").exists()
    assert not (project / "build/cad_cache").exists(), "legacy cache must be deleted in --model mode"
    assert not (project / "build/model/step/orphan.step").exists()


def test_cache_size_stays_at_one_model_after_repeated_edits(project):
    def model_bytes():
        return sum((project / p["step"]).stat().st_size for p in {p["key"]: p for p in H.read_parts(project)}.values())
    for i, w in enumerate(("11.0", "12.0", "13.0")):
        H.edit(H.cad_dir(project) / "params.py", f"A_W = {'10.0' if i == 0 else ('11.0' if i == 1 else '12.0')}", f"A_W = {w}")
        H.run_model(project).ok()
        assert len(H.step_files(project)) == len({p["key"] for p in H.read_parts(project)})
        assert H.dir_size(project / "build/model") <= model_bytes() + 256 * 1024
    assert not (project / "build/cad_cache").exists()


def test_gc_keeps_derived_results_of_unchanged_parts_and_drops_changed_ones(project):
    d = H.run_derive(project, "print").ok()
    assert d.computed == 1
    keys_before = {p.name for p in (project / "build/derived").iterdir()}
    assert keys_before
    H.edit(H.cad_dir(project) / "params.py", "A_W = 10.0", "A_W = 14.0")          # frame only; knob untouched
    H.run_model(project).ok()
    assert {p.name for p in (project / "build/derived").iterdir()} == keys_before
    d2 = H.run_derive(project, "print").ok()
    assert d2.computed == 0 and d2.reused == 1
    H.edit(H.cad_dir(project) / "params.py", "PRINT_R = 4.0", "PRINT_R = 5.0")     # knob changes: its old result is stale
    H.run_model(project).ok()
    assert not ({p.name for p in (project / "build/derived").iterdir()} & keys_before), "stale derived dir survived GC"


# ------------------------------------------------------------------ decision 14: a part is identified by its inputs, not by its STEP bytes
def _derive_all(root):
    return {s: H.run_derive(root, s).ok() for s in ("print", "step", "viewer", "bought")}


def test_foreign_machine_steps_with_same_keys_are_reused_as_they_are(project):
    """Another machine (other CPU) writes STEPs that differ in the last bits. Same inputs -> same key -> nothing is
    rebuilt, rewritten or recomputed here, whatever the STEP bytes are."""
    first = _derive_all(project)
    assert first["print"].computed == 1 and first["viewer"].computed == len(H.PART_IDS), "precondition: derived results exist"
    keys = H.part_keys(project)
    glb = H.find_glb(first["viewer"], project)
    glb_state = (glb.read_bytes(), glb.stat().st_mtime_ns)
    foreign = H.foreign_machine(project)
    steps = H.step_files(project)
    derived_before = {p: p.stat().st_mtime_ns for p in (project / "build/derived").rglob("*") if p.is_file()}
    H.clear_ticks(project)

    r = H.run_model(project).ok()
    assert r.summary == (0, H.N_UNITS, len(H.PART_IDS)) and not r.has_rebuilt_line, r.log[-800:]
    assert H.ticks(project) == [], "part or node code ran although every input is unchanged"
    assert H.part_keys(project) == keys, "keys must not depend on the STEP bytes"
    assert H.step_files(project) == steps, "a foreign STEP with a current key was rewritten or renamed"
    assert {p["id"]: H.sha_file(project / p["step"]) for p in H.read_parts(project)} == foreign

    for scope, before in first.items():
        d = H.run_derive(project, scope).ok()
        assert d.computed == 0 and d.reused == d.parts_k, f"{scope}: {d.log[-400:]}"
        assert d.manifest == before.manifest, f"{scope}: manifest changed although only the STEP bytes did"
    assert {p: p.stat().st_mtime_ns for p in derived_before} == derived_before, "a derived file was rewritten"
    assert (glb.read_bytes(), glb.stat().st_mtime_ns) == glb_state
    assert H.ticks(project) == []


def test_derived_result_made_from_a_foreign_step_survives_a_rebuild_here(project):
    """Derived output computed on the other machine (from its STEP bytes) stays valid here: GC keeps it, the manifest
    still points at it after this machine rebuilt a different node."""
    parts = H.by_id(project)
    foreign = H.foreign_machine(project)
    d1 = H.run_derive(project, "print").ok()                     # "made on the other machine", from the foreign STEP
    knob_files = H.file_list(H.entry(d1, "knob"), project)
    assert knob_files and all(f.is_file() for f in knob_files)
    knob_mt = {f: f.stat().st_mtime_ns for f in knob_files}
    dirs = H.derived_dirs(project)

    H.edit(H.cad_dir(project) / "params.py", "A_W = 10.0", "A_W = 14.0")          # this machine rebuilds frame only
    r = H.run_model(project).ok()
    assert r.rebuilt == H.units_for("frame"), r.log[-800:]
    assert H.sha_file(project / H.by_id(project)["knob"]["step"]) == foreign["knob"], "the foreign knob STEP was replaced"
    assert H.by_id(project)["knob"]["key"] == parts["knob"]["key"]
    assert dirs <= H.derived_dirs(project), "GC removed a derived dir whose part keys are all current"
    assert all(f.is_file() for f in knob_files)

    d2 = H.run_derive(project, "print").ok()
    assert d2.computed == 0 and d2.reused == 1, d2.log
    assert H.file_list(H.entry(d2, "knob"), project) == knob_files, "the manifest must reference the foreign-made result"
    assert {f: f.stat().st_mtime_ns for f in knob_files} == knob_mt


def _ghost_project(tmp_path):
    root = tmp_path / "proj"
    cad = H.write_base(root, params="GHOST = 1.0\nR = 4.0\n")
    H.write_node(root, "ghost", {"ghost":
        "import params\nfrom build123d import Sphere\n"
        "def build():\n    _unused = params.GHOST      # read, but the geometry does not depend on it\n"
        "    return {'name': 'ghost', 'shape': Sphere(params.R), 'process': 'print', 'material': 'PLA'}\n"})
    (cad / "assemble.py").write_text("import sys, os\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
                                     "from parts import ghost\nout = ghost.build()\n")
    return root


def test_input_change_without_geometry_change_gets_a_new_key_and_the_old_key_is_collected(tmp_path):
    """Accepted cost of keying on inputs: a param the part file read but did not use changes the key. The old key's STEP and
    derived results must be garbage-collected, the new STEP is the same geometry under a new name."""
    root = _ghost_project(tmp_path)
    H.run_model(root).ok()
    old = H.by_id(root)["ghost"]
    old_bytes = (root / old["step"]).read_bytes()
    assert H.run_derive(root, "print").ok().computed == 1
    old_dirs = H.derived_dirs(root)
    assert old_dirs

    H.edit(H.cad_dir(root) / "params.py", "GHOST = 1.0", "GHOST = 2.0")
    r = H.run_model(root).ok()
    assert r.rebuilt == {"ghost.ghost.build", "ghost.build"}, r.log[-800:]
    new = H.by_id(root)["ghost"]
    assert new["key"] != old["key"], "an input the part file read changed: the key must change"
    assert (root / new["step"]).read_bytes() == old_bytes, "precondition: geometry is unchanged"
    assert set(H.step_files(root)) == {new["key"] + ".step"}, "the old key's STEP must be removed"
    assert not (old_dirs & H.derived_dirs(root)), "the old key's derived dir must be removed by GC"

    d = H.run_derive(root, "print").ok()
    assert d.computed == 1, "the new key has no derived result yet"
    assert H.derived_dirs(root) and not (old_dirs & H.derived_dirs(root))
