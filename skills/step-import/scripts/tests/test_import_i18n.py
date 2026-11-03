"""The language tables of step-import (scripts/import_i18n.py) and the AAS namespace (aas_lib):

- part names in Cyrillic are read as before (kind, material, type, fastener template, sizes with a look-alike `x`);
- a project code before the drawing number is dropped from the identifiers, in any script;
- the material and unit the converter writes come in the project's language (`.engineer/locale.json`, Russian by default);
- AAS ids: the configured base for a new file, an existing file keeps its own, readers take both.

  python3 -m pytest -q test_import_i18n.py"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))
import aas_lib  # noqa: E402
import import_i18n as I  # noqa: E402
import migration_check as MC  # noqa: E402
import params as PRM  # noqa: E402
import step_import as SI  # noqa: E402
import step_to_project as S  # noqa: E402
from step_names_i18n import NAMES  # noqa: E402

RU = NAMES["ru"]


def typed(name, drawing=None):
    part = {"name": name, "drawing": drawing, "bbox_mm": [[0, 0, 0], [10, 10, 20]], "files": {}}
    return PRM.extract_params(part, "/nonexistent", geometry=False)


# ---------------------------------------------------------------- names in another language
def test_a_cyrillic_screw_is_a_standard_fastener():
    name = RU["screw"] + " M6" + RU["x"] + "20 DIN 912"
    assert SI.classify(name, None) == "standard"
    assert SI.material_of(name, None) == "fastener"
    assert typed(name)["type"] == "fastener"


def test_a_cyrillic_printed_part_is_printed_even_with_a_drawing():
    assert SI.material_of(RU["printed"] + " 01.02.003", "01.02.003") == "printed"


def test_a_cyrillic_motor_is_a_motor():
    assert SI.material_of(RU["motor"] + " NEMA17", None) == "motor"


def test_a_size_with_a_look_alike_x_is_read():
    name = RU["screw"] + " M6" + RU["x"] + "20"
    tpl, _stds, size, length = S.fastener_of({"kind": "standard", "name": name})
    assert (tpl, size, length) == ("screw", "M6", 20.0)
    assert MC._sizes(name) == {"M6x20"}
    assert I.latinise("M6" + RU["x"] + "20") == "M6x20"


def test_every_language_is_read_at_once():
    assert re.search(I.words("nut"), RU["nut"].lower()) and re.search(I.words("screw_or_bolt"), RU["screw"].lower())
    assert I.words("no_such_key") == r"(?!x)x"


# ---------------------------------------------------------------- identifiers
def test_slug_transliterates_and_drops_the_project_code():
    assert S.slug(RU["bushing"]) == "vtulka"
    assert S.slug("%s 02.01.121 %s" % (RU["code"], RU["plate"])) == "planka"
    assert S.slug("%s-1 01.02.03.456 %s" % (RU["code"], RU["plate"])) == "planka"
    assert S.slug("B-KV Bracket") == "bracket"
    assert S.slug("ABC 02.01.121 Plate") == "plate"


def test_slug_keeps_words_that_only_look_like_a_code():
    assert S.slug("DIN 912 M6x20") == "din_912_m6x20"          # no drawing number after DIN
    assert S.slug("NEMA 17 motor") == "nema_17_motor"


def test_params_drop_the_project_code_before_the_type():
    assert typed("ABC-1 NES-350 Mean Well")["params"]["model"] == "NES-350"


# ---------------------------------------------------------------- texts in the project's language
def test_rows_are_russian_by_default_and_english_on_request():
    made = {"name": "Plate 01.02.003", "kind": "made", "material": "steel", "drawing": "01.02.003"}
    sheet = {"kind": "sheet", "code": "bend(", "params": {"thickness": 2}}
    ru = S._P_fields(made, sheet)
    en = S._P_fields(made, sheet, "en")
    assert ru["material"] == I.TEXT["ru"]["material.steel"] and en["material"] == "steel"
    bought = {"name": "Bearing 608ZZ", "kind": "bought", "material": "bearing"}
    assert S._P_fields(bought, {"kind": "vendor"})["unit"] == I.TEXT["ru"]["unit.pcs"]
    assert S._P_fields(bought, {"kind": "vendor"}, "en")["unit"] == "pcs"
    printed = dict(made, material="printed")
    assert S._P_fields(printed, sheet, "en")["material"] == "plastic (printed)"
    poly = dict(made, name=RU["polyamide"] + " 01.02.004", material="other")
    assert S._P_fields(poly, {"kind": "turned"}, "en")["material"] == "polyamide"


def test_the_project_language_comes_from_locale_json(tmp_path):
    assert I.project_lang(tmp_path) == "ru"
    (tmp_path / ".engineer").mkdir()
    (tmp_path / ".engineer" / "locale.json").write_text(json.dumps({"lang": "en"}))
    assert I.project_lang(tmp_path) == "en"
    (tmp_path / ".engineer" / "locale.json").write_text(json.dumps({"lang": "de"}))
    assert I.project_lang(tmp_path) == "en"                     # no German table: English
    (tmp_path / ".engineer" / "locale.json").write_text("not json")
    assert I.project_lang(tmp_path) == "ru"


def test_a_library_part_is_written_with_the_projects_unit(tmp_path):
    pytest.importorskip("build123d")
    from build123d import Box, export_step
    step = tmp_path / "a.step"
    export_step(Box(10, 20, 30), str(step))
    part = {"name": "Stepper motor NEMA17 17HS4401", "kind": "bought", "type": "motor"}
    group, mod, _ = S.library_part(tmp_path / "en", part, "bought", step, "en")
    assert 'unit="pcs"' in (tmp_path / "en" / "library" / group / f"{mod}.py").read_text(encoding="utf-8")
    group, mod, _ = S.library_part(tmp_path / "ru", part, "bought", step)
    assert f'unit="{I.TEXT["ru"]["unit.pcs"]}"' in (tmp_path / "ru" / "library" / group / f"{mod}.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- AAS namespace
def shell(base, machine="m"):
    return {"assetAdministrationShells": [{"id": f"{base}ids/{machine}/aas"}], "submodels": []}


def test_aas_base_default_and_env_override():
    code = "import aas_lib; print(aas_lib.NS, aas_lib.ID)"
    env = {k: v for k, v in os.environ.items() if k != "DE_AAS_BASE"}
    out = subprocess.run([sys.executable, "-c", code], cwd=SCRIPTS, env=env, capture_output=True, text=True).stdout.split()
    assert out == ["https://design-engineer.dev/semantics/", "https://design-engineer.dev/ids/"]
    env["DE_AAS_BASE"] = "https://example.org/aas"
    out = subprocess.run([sys.executable, "-c", code], cwd=SCRIPTS, env=env, capture_output=True, text=True).stdout.split()
    assert out == ["https://example.org/aas/semantics/", "https://example.org/aas/ids/"]


def test_an_existing_aas_keeps_its_base_and_its_semantics_are_read(tmp_path, monkeypatch):
    for k in ("BASE", "NS", "ID"):
        monkeypatch.setattr(aas_lib, k, getattr(aas_lib, k))
    old = "https://old.example/"
    path = tmp_path / "m.aas.json"
    path.write_text(json.dumps(shell(old)))
    assert aas_lib.base_of(shell(old)) == old
    assert aas_lib.own_semantic(old + "semantics/pulley/teeth", shell(old)) == "pulley/teeth"
    assert aas_lib.own_semantic(aas_lib.NS + "pulley/teeth", shell(old)) == "pulley/teeth"
    assert aas_lib.own_semantic("https://admin-shell.io/idta/Nameplate/3/0", shell(old)) is None
    configured = aas_lib.NS
    assert aas_lib.adopt(tmp_path / "missing.aas.json") == (configured, aas_lib.ID)   # no file: the configured base
    assert aas_lib.adopt(path) == (old + "semantics/", old + "ids/")
    assert aas_lib.adopt(shell("https://other.example/")) == ("https://other.example/semantics/", "https://other.example/ids/")
