"""Done-when 2: a small part is drawn enlarged (scale >= 2:1, never 1:N).
Done-when 3: a part flagged "weld": true in parts.json carries weld marks (the seam mark + the weld note); an unflagged one does not."""
import re

import pytest

from conftest import NUM, num, ru

WELD_WORD = ru("weld.default").split()[0]                     # the note's verb

pytestmark = pytest.mark.slow


def scales(text):
    return [(num(a), num(b)) for a, b in re.findall(re.escape(ru("title.scale", scale="")) + rf"\s*({NUM})\s*:\s*({NUM})", text)]


def test_small_part_page1_scale_is_enlarged(small):
    sc = scales(small.p1)
    assert sc, f"'Scale N:M' in the title block of page 1: {small.p1[-300:]}"
    for a, b in sc:
        assert b == 1 and a >= 2, f"40x30 mm bracket must be drawn at >= 2:1, got {a:g}:{b:g}"


def test_weld_flag_puts_weld_marks_and_note(weld_box):
    assert ru("weld.mark") in weld_box.p1, "weld mark on page 1"
    assert WELD_WORD in weld_box.p1.lower(), "a note saying what to weld"


def test_no_weld_flag_no_weld_marks(plain_box):
    text = " ".join(plain_box.pages)
    assert ru("weld.mark") not in text
    assert WELD_WORD not in text.lower()
