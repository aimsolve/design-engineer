"""A production machine's rollout: subset kits by a part field (derive.py --where), human `title` next to the id `name`,
and trade names of bought parts in the whole-machine STEP. Parts without these fields behave exactly as before."""
import re
import subprocess
import procgroup
from pathlib import Path

import helpers as H
from test_derive import needs_cadgen
from samples_i18n import SAMPLES

RU = SAMPLES["ru"]


def _ids(d):
    return {e["id"] for e in d.manifest["parts"]}


def _mark(root, **by_id):
    for pid, fields in by_id.items():
        H.replace_part(root, pid, **fields)


def _pdf_text(pdf):
    code = "import sys,fitz;d=fitz.open(sys.argv[1]);print(' '.join(p.get_text() for p in d))"
    out = procgroup.run([str(H.SHOP_VENV), "-c", code, str(pdf)], capture_output=True, text=True).stdout
    return " ".join(out.split())


# ---------------------------------------------------------------- --where
def test_where_restricts_print_to_matching_parts(dproj):
    _mark(dproj, knob={"stage": "s1"}, cap={"stage": "s2"})
    assert _ids(H.run_derive(dproj, "print", where=["stage=s1"]).ok()) == {"knob"}
    assert _ids(H.run_derive(dproj, "print", where=["stage=s2"]).ok()) == {"cap"}
    assert _ids(H.run_derive(dproj, "print").ok()) == {"knob", "cap"}          # no filter: everything, as before


def test_where_matches_a_list_valued_field(dproj):
    _mark(dproj, knob={"stage": ["s1", "s2"]}, cap={"stage": "s2"})
    assert _ids(H.run_derive(dproj, "print", where=["stage=s1"]).ok()) == {"knob"}
    assert _ids(H.run_derive(dproj, "print", where=["stage=s2"]).ok()) == {"knob", "cap"}


def test_where_is_repeatable_and_ands(dproj):
    _mark(dproj, knob={"stage": "s1", "zone": "a"}, cap={"stage": "s1", "zone": "b"})
    assert _ids(H.run_derive(dproj, "print", where=["stage=s1", "zone=b"]).ok()) == {"cap"}


def test_where_with_no_match_is_an_empty_manifest_not_an_error(dproj):
    d = H.run_derive(dproj, "print", where=["stage=nope"]).ok()
    assert d.manifest["parts"] == []


def test_where_restricts_bought_rows(dproj):
    _mark(dproj, screw_a={"stage": "s1"}, screw_b={"stage": "s2"}, nut_a={"stage": "s1"})
    d = H.run_derive(dproj, "bought", where=["stage=s1"]).ok()
    assert {r["code"]: r["qty"] for r in d.manifest["bought"]} == {"M5x10": 2, "NUT-M5": 4}


def test_where_restricts_step_parts_and_the_model_step(dproj):
    _mark(dproj, frame={"stage": "s1"}, shaft={"stage": "s2"})
    d = H.run_derive(dproj, "step", where=["stage=s1"]).ok()
    assert _ids(d) == {"frame"}
    ms = Path(d.manifest["model_step"])
    ms = ms if ms.is_absolute() else dproj / ms
    whole = H.step_bbox(ms)
    assert whole.size.X < 35 and whole.size.Y < 15, "the stage model must hold only the matching parts"


def test_where_restricts_viewer(dproj):
    _mark(dproj, frame={"stage": "s1"})
    d = H.run_derive(dproj, "viewer", where=["stage=s1"]).ok()
    glb = H.find_glb(d, dproj)
    names = H.glb_names(glb)[1]
    assert "frame" in names and not {"shaft", "knob", "laser_plate"} & names


@needs_cadgen
def test_where_restricts_laser(dproj):
    _mark(dproj, laser_plate={"stage": "s1"}, bent_angle={"stage": "s2"})
    assert _ids(H.run_derive(dproj, "laser", where=["stage=s1"]).ok()) == {"laser_plate"}


def test_where_never_changes_the_model_or_deletes_other_stages_results(dproj):
    _mark(dproj, knob={"stage": "s1"}, cap={"stage": "s2"})
    parts_before = (dproj / "build/model/parts.json").read_bytes()
    d_all = H.run_derive(dproj, "print").ok()
    files = [f for e in d_all.manifest["parts"] for f in H.file_list(e, dproj)]
    H.run_derive(dproj, "print", where=["stage=s1"]).ok()
    assert (dproj / "build/model/parts.json").read_bytes() == parts_before
    assert all(f.is_file() for f in files), "GC must follow the whole model, not the filtered view"
    assert H.run_derive(dproj, "print").ok().computed == 0


def test_where_with_a_part_id_needs_the_part_to_match(dproj):
    _mark(dproj, knob={"stage": "s1"}, cap={"stage": "s2"})
    assert _ids(H.run_derive(dproj, "print", part_id="cap", where=["stage=s2"]).ok()) == {"cap"}
    assert H.run_derive(dproj, "print", part_id="cap", where=["stage=s1"]).rc != 0


def test_where_without_equals_is_a_usage_error(dproj):
    assert H.run_derive(dproj, "print", where=["stage"]).rc != 0


# ---------------------------------------------------------------- title
def test_title_is_the_manifest_name_and_the_bought_row_name(dproj):
    _mark(dproj, knob={"title": "Knob"}, nut_a={"title": "Hex nut M5"})
    d = H.run_derive(dproj, "print").ok()
    assert H.entry(d, "knob")["name"] == "Knob" and H.entry(d, "knob")["id"] == "knob"
    assert H.entry(d, "cap")["name"] == "cap"                                  # no title: the name, as before
    rows = {r["code"]: r["name"] for r in H.run_derive(dproj, "bought").ok().manifest["bought"]}
    assert rows["NUT-M5"] == "Nut M5"                                           # desc (trade name) still wins over title
    _mark(dproj, nut_a={"desc": None})
    rows = {r["code"]: r["name"] for r in H.run_derive(dproj, "bought").ok().manifest["bought"]}
    assert rows["NUT-M5"] == "Hex nut M5"


def test_title_does_not_rename_files_of_the_part(dproj):
    _mark(dproj, knob={"title": "Knob / large"})
    d = H.run_derive(dproj, "print").ok()
    assert {f.name for f in H.file_list(H.entry(d, "knob"), dproj)} == {"knob.stl", "knob.step"}


@needs_cadgen
def test_title_goes_to_the_drawing_title_block_but_not_the_file_names(dproj):
    _mark(dproj, laser_plate={"title": RU["mount_plate"]}, bent_angle={"title": RU["stiffening_angle"]})
    d = H.run_derive(dproj, "laser").ok()
    e = H.entry(d, "laser_plate")
    assert e["name"] == RU["mount_plate"] and e["id"] == "laser_plate"
    assert {f.name for f in H.file_list(e, dproj)} >= {"laser_plate.dxf"}
    pdf = [f for f in H.file_list(H.entry(d, "bent_angle"), dproj) if f.suffix == ".pdf"][0]
    assert pdf.name == "bent_angle.pdf"
    assert RU["stiffening_angle"] in _pdf_text(pdf)


@needs_cadgen
def test_changing_a_title_redraws_the_sheet(dproj):
    H.run_derive(dproj, "laser").ok()
    _mark(dproj, bent_angle={"title": "New name"})
    d = H.run_derive(dproj, "laser").ok()
    assert d.computed == 1, d.log


# ---------------------------------------------------------------- whole-machine STEP labels
def _labels(step_file):
    raw = Path(step_file).read_text(encoding="ascii")
    return [re.sub(r"\\X2\\([0-9A-F]{4})\\X0\\", lambda x: chr(int(x.group(1), 16)), m) for m in re.findall(r"'([^']*)'", raw)]


def _model_labels(d, root):
    ms = Path(d.manifest["model_step"])
    return _labels(ms if ms.is_absolute() else root / ms)


def test_model_step_labels_parts_with_title_and_desc(dproj):
    _mark(dproj, knob={"title": RU["knob"]})
    labels = _model_labels(H.run_derive(dproj, "step").ok(), dproj)
    assert RU["knob"] in labels                                       # title
    assert "Nut M5" in labels and "Screw M5x10" in labels         # desc of bought parts: the trade name survives
    assert "frame" in labels                                       # no title / desc: the name, as before
    assert "nut_a" not in labels and "knob" not in labels


def test_model_step_without_titles_keeps_names(dproj):
    labels = _model_labels(H.run_derive(dproj, "step").ok(), dproj)
    assert "frame" in labels and "shaft" in labels
