"""A part's "note" in parts.json given as a plain string was once printed letter by letter ("7. P", "8. a", ...).
Done-when 1: "note": "<one sentence>" -> exactly ONE numbered note on page 1 holding the whole sentence, no single-letter notes.
Done-when 2: "note": ["first", "second"] -> both appear, each as its own numbered note."""
import re

import pytest

from conftest import _parts_kit

pytestmark = pytest.mark.slow

SENTENCE = "Powder coat RAL 7035 after bending."
LIST_NOTES = ["Deburr the contour.", "Mark the part number."]

# a numbered note whose whole text is one character: "7. P", "12. o"
LETTER_NOTE = re.compile(r"(?<![\d,.])\d{1,3}\.\s(\S)(?=\s|$)")


def _box(synth_steps, tmp_path_factory, tag, note):
    return _parts_kit(tmp_path_factory, tag, {"name": "Tray", "step": str(synth_steps / "box.step"),
                                              "material": "DC01 steel", "t": 1.5, "R": 1.5, "qty": 1, "note": note})


@pytest.fixture(scope="session")
def note_str(synth_steps, tmp_path_factory):
    return _box(synth_steps, tmp_path_factory, "note_str", SENTENCE)


@pytest.fixture(scope="session")
def note_list(synth_steps, tmp_path_factory):
    return _box(synth_steps, tmp_path_factory, "note_list", LIST_NOTES)


def numbered(text, note):
    """How many times `note` appears on the page as a numbered note "N. <note>"."""
    return len(re.findall(rf"\d+\.\s{re.escape(note)}", text))


def test_string_note_has_no_single_letter_notes(note_str):
    letters = LETTER_NOTE.findall(note_str.p1)
    assert not letters, f"string note split into one-letter numbered notes {letters[:12]}: {note_str.p1[-600:]}"


def test_string_note_is_one_whole_note(note_str):
    assert numbered(note_str.p1, SENTENCE) == 1, \
        f"the whole sentence as exactly one numbered note on page 1: {note_str.p1[-600:]}"


def test_list_note_gives_each_item_its_own_note(note_list):
    for n in LIST_NOTES:
        assert numbered(note_list.p1, n) == 1, f"{n!r} as its own numbered note on page 1: {note_list.p1[-600:]}"
    assert not LETTER_NOTE.findall(note_list.p1), "no one-letter numbered notes"
