"""model_from_steps.py ROOT PARTS_JSON STEP_DIR: a client-CAD project (per-part STEPs + a parts list, no node code) gets a
build/model/ so derive.py works for it."""
import json
import subprocess
import procgroup
import sys
from pathlib import Path

import helpers as H
import text_i18n as TX

sys.path.insert(0, str(H.SCRIPTS))
MFS = H.SCRIPTS / "model_from_steps.py"


def _src(root):
    """A client-CAD-shaped source: build/parts.json + build/parts/<id>.step."""
    from build123d import Box, Cylinder, Pos, export_step
    d = Path(root) / "build" / "parts"
    d.mkdir(parents=True)
    specs = [
        ("03.01.01.301", "M1 Plate", "made", "steel", "03.01.01.301", 4, Pos(0, 0, 0) * Box(60, 40, 2)),      # flat sheet
        ("03.01.01.302", "M1 Bar", "made", "steel", "03.01.01.302", 1, Pos(0, 50, 0) * Box(60, 40, 30)),     # not a sheet
        ("03.01.01.303", "M1 Handle", "made", "printed", "03.01.01.303", 2, Pos(0, 100, 0) * Box(10, 10, 10)),
        ("hex_nut_M16", "hex nut M16", "standard", "fastener", None, 8, Pos(0, 150, 0) * Cylinder(8, 6)),
    ]
    rows = []
    for pid, name, kind, mat, drawing, n, shape in specs:
        export_step(shape, str(d / f"{pid}.step"))
        row = {"id": pid, "name": name, "drawing": drawing, "kind": kind, "material": mat, "module": "03.01", "color": "#404040",
               "instances": n, "files": {"step": f"parts/{pid}.step"}, "type": None}
        if kind == "standard":
            row.update(type="fastener", params={"standard": "ISO 4034", "thread": "M16"}, bought_kind="fasteners")
        rows.append(row)
    (Path(root) / "build" / "parts.json").write_text(json.dumps({"assembly": {}, "parts": rows}, ensure_ascii=False))
    (Path(root) / "kit.json").write_text(json.dumps({"model": "steps", "scopes": {}}))
    return Path(root)


def _run(root, *extra):
    return procgroup.run([sys.executable, str(MFS), str(root), str(root / "build/parts.json"), str(root / "build/parts"), *extra],
                          capture_output=True, text=True, timeout=300)


def test_writes_parts_json_and_content_keyed_steps(tmp_path):
    root = _src(tmp_path / "p")
    cp = _run(root)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    parts = H.by_id(root)
    assert set(parts) == {"03.01.01.301", "03.01.01.302", "03.01.01.303", "hex_nut_M16"}
    for p in parts.values():
        assert p["step"] == f"build/model/step/{p['key']}.step" and (root / p["step"]).is_file()
        assert len(p["key"]) == 64


def test_mapping_of_process_material_thickness_qty_code(tmp_path):
    root = _src(tmp_path / "p")
    assert _run(root).returncode == 0
    p = H.by_id(root)
    plate = p["03.01.01.301"]
    assert plate["process"] == "laser" and plate["qty"] == 4 and plate["code"] == "03.01.01.301" and plate["material"] == TX.t("material.steel")
    assert abs(plate["thickness"] - 2.0) < 0.1
    assert p["03.01.01.302"]["process"] == "other"
    assert p["03.01.01.303"]["process"] == "print" and p["03.01.01.303"]["qty"] == 2
    nut = p["hex_nut_M16"]
    assert nut["process"] == "bought" and nut["qty"] == 8 and nut["code"] == "ISO 4034 M16" and nut["name"] == "hex nut M16"


def test_key_follows_step_bytes_and_row_fields_and_is_stable(tmp_path):
    root = _src(tmp_path / "p")
    _run(root)
    k1 = H.part_keys(root)
    _run(root)
    assert H.part_keys(root) == k1
    pj = root / "build/parts.json"
    d = json.loads(pj.read_text())
    d["parts"][1]["instances"] = 5
    pj.write_text(json.dumps(d, ensure_ascii=False))
    _run(root)
    k2 = H.part_keys(root)
    assert k2["03.01.01.302"] != k1["03.01.01.302"] and k2["03.01.01.301"] == k1["03.01.01.301"]
    assert len(list((root / "build/model/step").glob("*.step"))) == 4, "the old version of the changed part is collected"


def test_override_file_wins(tmp_path):
    root = _src(tmp_path / "p")
    (root / "mechanics/export").mkdir(parents=True)
    (root / "mechanics/export/parts_meta.json").write_text(json.dumps({"03.01.01.302": {"process": "bent", "thickness": 3.0, "weld": "A"}}))
    assert _run(root).returncode == 0
    p = H.by_id(root)["03.01.01.302"]
    assert p["process"] == "bent" and p["thickness"] == 3.0 and p["weld"] == "A"


def test_missing_step_is_a_loud_error(tmp_path):
    root = _src(tmp_path / "p")
    (root / "build/parts/hex_nut_M16.step").unlink()
    cp = _run(root)
    assert cp.returncode != 0 and "hex_nut_M16" in cp.stdout + cp.stderr


def test_derive_builds_the_model_itself_when_kit_json_declares_steps(tmp_path):
    root = _src(tmp_path / "p")
    d = H.run_derive(root, "bought").ok()
    assert d.manifest["bought"][0]["code"] == "ISO 4034 M16" and d.manifest["bought"][0]["qty"] == 8
    d = H.run_derive(root, "print").ok()
    assert [e["id"] for e in d.manifest["parts"]] == ["03.01.01.303"]
    d = H.run_derive(root, "step").ok()
    assert d.manifest["model_step"] and (root / d.manifest["model_step"]).is_file()


# ---------------------------------------------------------------- review follow-ups
def _one(tmp_path, shape, **row):
    from build123d import export_step
    root = Path(tmp_path) / "p"
    (root / "build/parts").mkdir(parents=True)
    export_step(shape, str(root / "build/parts/x.step"))
    r = dict({"id": "x", "name": "X", "kind": "made", "material": "steel", "drawing": "D1", "module": "m", "instances": 1,
              "files": {"step": "parts/x.step"}}, **row)
    (root / "build/parts.json").write_text(json.dumps({"parts": [r]}))
    return root


def test_key_changes_when_the_classifier_changes(tmp_path, monkeypatch):
    import model_from_steps as M
    root = _one(tmp_path, __import__("build123d").Box(60, 40, 2))
    M.main(root, root / "build/parts.json", root / "build/parts")
    k1 = H.part_keys(root)
    monkeypatch.setattr(M, "_code_sha", lambda *a: "another classifier")
    M.main(root, root / "build/parts.json", root / "build/parts")
    assert H.part_keys(root)["x"] != k1["x"]


def test_a_pocketed_5mm_plate_is_machined_not_laser(tmp_path):
    from build123d import Box, Pos
    plate = Box(60, 40, 5) - Pos(0, 0, 1) * Box(30, 20, 3)           # 2 mm floor under a 3 mm pocket: thin, but not a sheet
    root = _one(tmp_path, plate)
    assert _run(root).returncode == 0
    assert H.by_id(root)["x"]["process"] == "other"


def test_a_plain_5mm_plate_with_holes_is_laser(tmp_path):
    from build123d import Box, Cylinder, Pos
    root = _one(tmp_path, Box(60, 40, 5) - Pos(10, 0, 0) * Cylinder(3, 5))
    assert _run(root).returncode == 0
    p = H.by_id(root)["x"]
    assert p["process"] == "laser" and p["thickness"] == 5.0


def test_material_comes_from_the_row(tmp_path):
    root = _one(tmp_path, __import__("build123d").Box(60, 40, 2), material="aluminium")
    assert _run(root).returncode == 0
    assert H.by_id(root)["x"]["material"] == "aluminium"


def test_two_rows_with_one_step_basename_are_an_error(tmp_path):
    root = _src(tmp_path / "p")
    d = json.loads((root / "build/parts.json").read_text())
    d["parts"][1]["files"]["step"] = d["parts"][0]["files"]["step"]
    (root / "build/parts.json").write_text(json.dumps(d, ensure_ascii=False))
    cp = _run(root)
    assert cp.returncode != 0 and "03.01.01.301" in cp.stdout + cp.stderr and "03.01.01.302" in cp.stdout + cp.stderr
