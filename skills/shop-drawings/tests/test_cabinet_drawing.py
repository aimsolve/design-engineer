"""Done-when 1: a U wall (kit_probe.py cabinet_u: 1 mm galvanised DC01, inner R 1, two bends DOWN 90°, 24 round holes, 12 of
them in the flanges) gives the 2-page PDF of the drawing standard: page 1 = flat pattern + views + iso with "**" flat dims,
bend labels, the thickness and R, the notes and the title block; page 2 = the hole table, one row per DXF circle, X/Y from the
blank's lower-left corner. The Russian texts are the default project's (drawings_i18n.py)."""
import itertools
import math
import re

import pytest

from conftest import CABINET, NUM, hole_rows, num, ru  # noqa: F401
from kit_probe import CABINET_U as U

pytestmark = pytest.mark.slow

BENDS = 2
K = 0.33                                                   # steel
# blank across the bends: the straight web and flanges (minus the setback R + t at each bend) + a bend allowance per bend
BLANK = (U["W"] - 2 * (U["R"] + U["t"]) + 2 * (U["H"] - (U["R"] + U["t"])) + BENDS * math.pi / 2 * (U["R"] + K * U["t"]), U["L"])


def bend_label(way, angle=90, r="1"):
    return (rf"{ru(way)}, {re.escape(ru('bend.turn', angle=angle))} \({re.escape(ru('bend.between', between=180 - angle))}\)"
            rf"\s*R\s*{r}(?![\d,.])")


def test_two_pages(cabinet):
    assert len(cabinet.pages) == 2, f"page 1 drawing + page 2 holes, got {len(cabinet.pages)} pages"


def test_page1_bend_label_per_bend_with_radius(cabinet):
    labels = re.findall(f"{bend_label('dir.down')}|{bend_label('dir.up')}", cabinet.p1)
    assert len(labels) == BENDS, f"one 'DOWN/UP, bend 90° (90° between flanges) R 1' per bend on page 1, got {labels}"
    assert all(ru("dir.down") in x for x in labels), "both flanges of the U wall bend DOWN"


def test_page1_flat_pattern_dims_marked_double_star(cabinet):
    vals = [num(v) for v in re.findall(rf"({NUM})\s*\*\*", cabinet.p1)]
    assert any(abs(v - BLANK[0]) <= 0.5 for v in vals), f"overall blank length {BLANK[0]} ** on page 1, got {vals}"
    assert any(abs(v - BLANK[1]) <= 0.5 for v in vals), f"overall blank width {BLANK[1]} ** on page 1, got {vals}"
    rest = list(vals)
    for b in BLANK:                                        # drop one overall dim each; a chain segment may be ~ the width
        rest.remove(min(rest, key=lambda v: abs(v - b)))
    chains = [c for c in itertools.combinations(rest, BENDS + 1) if any(abs(sum(c) - b) <= 0.5 for b in BLANK)]
    assert chains, f"a chain of {BENDS + 1} '**' dims between the bend lines summing to the blank, got {rest}"


def test_page1_thickness_and_inner_radius(cabinet):
    assert re.search(r"(?<![\d,.])1\s?\*(?!\*)", cabinet.p1), "thickness dimensioned on a view as '1 *' / '1*'"
    assert re.search(r"(?<![\w])R1(?![\d,.])", cabinet.p1), "inner radius 'R1' on a view"


@pytest.mark.parametrize("note", ["note.ref", "note.theor", "note.tol", "note.holes_sheet2"])
def test_page1_notes(cabinet, note):
    assert ru(note) in cabinet.p1


def test_page1_quantity_note(cabinet):
    assert ru("note.qty", qty=1) in cabinet.p1


def test_page1_has_no_hole_positions(cabinet):
    assert ru("holes.cols")[1] not in cabinet.p1, "hole positions belong on page 2 only"
    x0, y0 = cabinet.dxf["extmin"]
    coords = {round(v, 1) for cx, cy, _ in cabinet.dxf["circles"] for v in (cx - x0, cy - y0)}
    coords = {v for v in coords if abs(v - round(v)) > 0.05}           # 7.5, 22.2, 556.9 ...: only a hole sits there
    star = [num(v) for v in re.findall(rf"({NUM})\s*\*\*", cabinet.p1)]
    coords = {v for v in coords if all(abs(v - s) > 0.05 for s in star)}
    on_p1 = {round(num(t), 1) for t in re.findall(rf"(?<![\w.,]){NUM}(?![\w.,])", cabinet.p1)}
    assert not coords & on_p1, f"hole-centre coordinates dimensioned on page 1: {sorted(coords & on_p1)}"


def test_title_block(cabinet):
    p1 = cabinet.p1
    assert CABINET["name"] in p1
    assert re.search(re.escape(CABINET["material"]) + r".{0,30}?\b1\s?" + ru("mm"), p1), "material with thickness in the title block"
    assert re.search(re.escape(ru("title.scale", scale="")) + r"\s*\d", p1)
    assert " ".join(ru("title.sheet", no=1, of=2).split()) in p1
    assert ru("title.qty", qty=1) in p1, "qty"


def test_page1_flat_pattern_caption(cabinet):
    assert ru("flat.bent") in cabinet.p1


def test_page2_hole_table_header(cabinet):
    for h in ru("holes.cols"):
        assert h in cabinet.p2, f"hole table header {h!r} on page 2"


def test_page2_one_row_per_dxf_circle_from_lower_left(cabinet):
    circles = cabinet.dxf["circles"]
    assert len(circles) == 24, "the U wall has 24 round holes"
    x0, y0 = cabinet.dxf["extmin"]
    rows = hole_rows(cabinet.p2)
    used = set()
    for cx, cy, r in circles:
        hit = [row for row in rows if abs(row[1] - (cx - x0)) <= 0.06 and abs(row[2] - (cy - y0)) <= 0.06
               and abs(row[3] - 2 * r) <= 0.06]
        assert hit, f"no table row for hole at X {cx - x0:.1f} Y {cy - y0:.1f} Ø{2 * r:.1f} (rows {rows[:6]}...)"
        used.add(hit[0][0])
    assert len(used) == len(circles), "every hole its own numbered row"
    assert len({r[0] for r in rows if r[0] in used}) == len(circles)
