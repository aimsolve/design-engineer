"""The texts a person reads follow the project's language: a project whose .engineer/locale.json says "en" gets the drawing,
the kit folder, the parts list and the README in English (the same geometry as the Russian default), the checker reads the
English set and passes it, and its reasons come in the language asked for."""
import re
import shutil

import pytest

from conftest import CABINET, check, en, hole_rows, probe, ru
from test_cabinet_drawing import BENDS

pytestmark = pytest.mark.slow


def test_english_bend_labels(cabinet_en):
    label = f"{en('dir.down')}, {en('bend.turn', angle=90)} ({en('bend.between', between=90)})"
    assert len(re.findall(re.escape(label) + r"\s*R\s*1(?![\d,.])", cabinet_en.p1)) == BENDS, cabinet_en.p1[:800]
    assert en("flat.bent") in cabinet_en.p1


@pytest.mark.parametrize("key", ["note.ref", "note.theor", "note.tol", "note.holes_sheet2"])
def test_english_notes(cabinet_en, key):
    assert en(key) in cabinet_en.p1


def test_english_title_block(cabinet_en):
    p1 = cabinet_en.p1
    assert en("title.qty", qty=1) in p1 and " ".join(en("title.sheet", no=1, of=2).split()) in p1
    assert re.search(re.escape(CABINET["material"]) + r".{0,30}?\b1\s?mm", p1)
    assert re.search(re.escape(en("title.scale", scale="")) + r"\s*\d", p1)


def test_no_russian_text_in_the_english_set(cabinet_en):
    cyrillic = re.compile("[%s-%s]" % (chr(0x400), chr(0x4FF)))
    assert not cyrillic.search(" ".join(cabinet_en.pages)), "an English project's drawing has no Russian words"


def test_english_hole_table_has_the_same_rows(cabinet, cabinet_en):
    rows_en, rows_ru = hole_rows(cabinet_en.p2, "en"), hole_rows(cabinet.p2)
    assert rows_en and sorted(r[1:] for r in rows_en) == sorted(r[1:] for r in rows_ru)
    assert len(cabinet_en.dxf["circles"]) == 24


def test_english_kit_folder_list_and_readme(cabinet_en):
    assert cabinet_en.pdf_path.parent.name == en("kit.folder", t="1", group=en("group.steel"))
    head = (cabinet_en.out / "spisok.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    assert head.split(";") == en("kit.list_cols")
    assert (cabinet_en.out / "README.txt").read_text(encoding="utf-8") == en("kit.readme")


def test_russian_default_folder_list_and_readme(cabinet):
    assert cabinet.pdf_path.parent.name == ru("kit.folder", t="1", group=ru("group.steel"))
    assert (cabinet.out / "spisok.csv").read_text(encoding="utf-8-sig").splitlines()[0].split(";") == ru("kit.list_cols")
    assert (cabinet.out / "README.txt").read_text(encoding="utf-8") == ru("kit.readme")


def test_checker_passes_the_english_set(cabinet_en):
    rc, out = check(cabinet_en.out)
    assert rc == 0 and "CHECK: 1 ok, 0 fail" in out, out[-1500:]


@pytest.mark.parametrize("lang, say", [("en", en), ("ru", ru)])
def test_checker_reasons_in_the_language_asked(cabinet_en, tmp_path, lang, say):
    """The English set with its hole sheet dropped fails; the reason comes in the --lang language."""
    kit = tmp_path / "kit"
    shutil.copytree(cabinet_en.out, kit)
    pdf = kit / cabinet_en.pdf_path.relative_to(cabinet_en.out)
    assert probe("drop_page", pdf, tmp_path / "p1.pdf", 1)["pages"] == 1
    shutil.copy(tmp_path / "p1.pdf", pdf)
    rc, out = check(kit, "--lang", lang)
    assert rc == 1 and say("chk.no_hole_sheet") in out, out[-1500:]
