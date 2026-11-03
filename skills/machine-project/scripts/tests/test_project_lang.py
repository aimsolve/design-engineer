"""The texts the framework writes for a person follow the project's language (.engineer/locale.json {"lang"}): no file is
Russian (what a project always got), "en" is English, a language without its own table is English.

  text_i18n   project_lang, purchase-list units (a neutral key shown in the language, any other unit as written)
  scaffold    the fastener templates' purchase-list names in the project's language (--lang, else locale.json, else ru)
  catalogue   library/i18n/catalog_i18n.py finds the language of the project its mirror lies in
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import helpers as H
import text_i18n as TX

CATALOG = H.SCRIPTS.parents[2] / "catalog"


def locale(root, lang):
    (Path(root) / ".engineer").mkdir(parents=True, exist_ok=True)
    (Path(root) / ".engineer" / "locale.json").write_text(json.dumps({"lang": lang}), encoding="utf-8")


def test_no_locale_is_the_default_language(tmp_path):
    assert TX.project_lang(tmp_path) == TX.DEFAULT == "ru"


@pytest.mark.parametrize("lang,want", [("ru", "ru"), ("en", "en"), ("pl", "en"), ("", "ru")])
def test_the_locale_picks_the_texts(tmp_path, lang, want):
    locale(tmp_path, lang)
    assert TX.project_lang(tmp_path) == want


def test_a_locale_that_is_not_a_small_regular_file_is_the_default(tmp_path):
    (tmp_path / ".engineer").mkdir()
    os.mkfifo(tmp_path / ".engineer" / "locale.json")      # would block a plain open()
    assert TX.project_lang(tmp_path) == "ru"


def test_units_a_neutral_key_is_shown_in_the_language_anything_else_as_written():
    assert TX.unit("pcs", "ru") != TX.unit("pcs", "en") == "pcs"
    assert TX.unit("m", "en") == "m"
    assert TX.unit("box", "ru") == "box" and TX.unit("box", "en") == "box"


def test_every_language_has_every_text():
    keys = {k for table in TX.TEXT.values() for k in table}
    for lang, table in TX.TEXT.items():
        assert set(table) == keys, f"{lang} lacks {sorted(keys - set(table))}"


def _scaffold(root, *args):
    cp = subprocess.run([sys.executable, str(H.SCRIPTS / "scaffold.py"), str(root), "--name", "m", "--cad", "build123d", *args],
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    return {f: (root / "mechanics/cad/parts/fasteners" / f).read_text(encoding="utf-8") for f in ("screw.py", "nut.py", "washer.py")}


def _names(src):
    """(NAMES, WORD) of a scaffolded fastener file, read as literals."""
    import ast
    got = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(src).body
           if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in ("NAMES", "WORD")}
    return got.get("NAMES"), got["WORD"]


def test_scaffold_writes_the_fastener_names_in_the_project_language(tmp_path):
    ru = _scaffold(tmp_path / "ru")
    en = _scaffold(tmp_path / "en", "--lang", "en")
    loc = tmp_path / "loc"
    locale(loc, "en")
    assert _scaffold(loc) == en, "no --lang: the project's locale.json decides"
    for f, kind in (("screw.py", "screw"), ("nut.py", "nut"), ("washer.py", "washer")):
        names, word = _names(ru[f])
        want = TX.FASTENERS["ru"][kind]
        assert word == want["WORD"] and names == want.get("NAMES"), f
        assert _names(en[f])[1] != word, f"{f}: English in an English project"
        assert 'unit="pcs"' in ru[f] and 'unit="pcs"' in en[f]


def test_the_catalogue_texts_follow_the_project_language(tmp_path):
    proj = tmp_path / "proj"
    lib = proj / "mechanics" / "cad" / "library" / "i18n"
    lib.parent.mkdir(parents=True)
    shutil.copytree(CATALOG / "library" / "i18n", lib)
    sys.path.insert(0, str(lib))
    try:
        import catalog_i18n as C
        assert C.lang() == "ru"
        ru = C.t("kfl.title", code="KFL004")
        locale(proj, "en")
        assert C.lang() == "en" and C.t("kfl.title", code="KFL004") == "Bearing KFL004" != ru
        keys = {k for table in C.TEXT.values() for k in table}
        assert all(set(table) == keys for table in C.TEXT.values())
    finally:
        sys.path.remove(str(lib))
        sys.modules.pop("catalog_i18n", None)
