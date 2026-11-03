"""schemes.py: the machine schemes are DRAWINGS, not tables of boxes and addresses.

Written from the spec before the implementation (the test author has not seen it). Same hand-written model as
test_schemes_model.py (build/model/parts.json + mechanics/cad/connections.py; nothing heavy runs).

  uv run --with matplotlib --with pymupdf --with pytest \
      pytest agent/skills/machine-project/scripts/tests/test_schemes_drawing.py -q

Contract under test (done-when, user side):
  [1] `schemes.py <kind> build/kit/<kind>.json` writes build/kit/schemes/<kind>.pdf AND build/kit/schemes/<kind>.svg; the manifest
      gains "svg": "build/kit/schemes/<kind>.svg"; "scheme", "sheets", "elements", "connections" stay.
  [2] the PDF's first sheets are drawings: every element is named on them by its HUMAN name (part `title`, else `card.name`, else
      `desc`), and the wires are vector lines (pymupdf get_drawings() finds many line items on sheet 1). After the drawing sheets
      comes the connection table: both ends "<placement>.<port>" of every connection are text in the PDF.
  [3] the SVG is one valid-XML file with every drawing sheet: every element's human name is text in it. A supply net ("+24 V",
      "0 V", "GND") on 3+ ports is drawn as a supply / ground mark with the net name (asserted loosely: the net name is text).
  [4] pneumatic: filter unit, 5/2 valve with coil ports, cylinder or suction cup, silencer hosed into the valve's R port: both
      files; the hoses are numbered H1.. on the drawing.
Out of scope (not asserted): positions, colours, symbol shapes.
"""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import test_schemes_model as M          # the fixture helpers (make_project, part, run_schemes ...); conftest puts tests/ on sys.path
from samples_i18n import SAMPLES

RU = SAMPLES["ru"]                       # the names and nets as a Russian project writes them


def squash(s):
    return re.sub(r"\s+", "", s or "")


def build(root, kind):
    r = M.run_schemes(root, kind)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return M.manifest(root, kind)


def page_texts(pdf):
    import fitz
    with fitz.open(pdf) as doc:
        return [squash(p.get_text()) for p in doc]


def line_items(pdf, pages):
    """Number of straight-line path items ("l") pymupdf finds on the given pages."""
    import fitz
    n = 0
    with fitz.open(pdf) as doc:
        for i in pages:
            for d in doc[i].get_drawings():
                n += sum(1 for it in d["items"] if it[0] == "l")
    return n


def page_raw(pdf):
    import fitz
    with fitz.open(pdf) as doc:
        return [p.get_text() for p in doc]


def has_end(text, end):
    """`end` ("CTRL.P1") is a whole whitespace-delimited token of the raw page text: not the head of "CTRL.P10", and not glued to
    the next table cell ("0 V")."""
    return re.search(r"(?<!\S)" + re.escape(end) + r"(?!\S)", text) is not None


def split_sheets(pdf, connections):
    """(drawing pages, table pages): the connection table is the SHORTEST tail of the PDF whose text still holds both ends of every
    connection; the drawing sheets are everything before it."""
    raw = page_raw(pdf)
    texts = [squash(t) for t in raw]
    ends = [e for c in connections for e in (c["from"], c["to"])]
    t = None
    for start in range(len(raw) - 1, -1, -1):
        tail = "\n".join(raw[start:])
        if all(has_end(tail, e) for e in ends):
            t = start
            break
    assert t is not None, "the PDF lacks a connection end, so there is no connection table"
    return list(range(t)), list(range(t, len(texts))), texts


def svg_root(path):
    assert Path(path).is_file(), f"no SVG drawn: {path}"
    try:
        return ET.parse(path).getroot()
    except ET.ParseError as e:
        pytest.fail(f"{path} is not valid XML: {e}")


def svg_text(path):
    """All text of the SVG's <text> elements (tspans included), whitespace removed. Glyphs drawn as paths do NOT count."""
    root = svg_root(path)
    return "".join(squash("".join(el.itertext())) for el in root.iter() if el.tag.rsplit("}", 1)[-1] == "text")


def svg_strings(path):
    root = svg_root(path)
    return [squash("".join(el.itertext())) for el in root.iter() if el.tag.rsplit("}", 1)[-1] == "text"]


def named(pid, kind, ports, title=None, card=None, desc="", placements=None):
    """A parts.json record whose human name comes from `title`, else `card.name`, else `desc`."""
    rec = M.part(pid, kind, ports, placements=placements, desc=desc)
    rec.pop("title", None)
    if title is not None:
        rec["title"] = title
    if card is not None:
        rec["card"] = card
    return rec


# ---------------------------------------------------------------- an electric model with human names and supply nets

EL_PARTS = [
    named("PSU", "electrical", {"V+": "24V", "V-": "0V"}, title=RU["psu"]),
    named("CTRL", "electrical", {"J1.1": "+24", "J1.2": "GND", "J3.2": "STEP", "J3.3": "STEP", "J4.1": "OUT", "J4.2": "IN"},
          card={"name": RU["ctrl_card"]}, desc=RU["ctrl_desc"]),
    named("DRV", "electrical", {"VCC": "", "GND": "", "PUL+": "", "A+": "", "A-": ""}, desc=RU["driver"],
          placements=["DRV", "DRV_2"]),
    named("M1", "motors", {"A+": "", "A-": ""}, title=RU["stepper"], placements=["M1", "M1_2"]),
    named("K1", "electrical", {"A1": "", "A2": ""}, title=RU["relay"], card={"name": RU["relay_card"]}),
    named("SENS", "electrical", {"+": "", "-": "", "OUT": ""}, title=RU["sensor"]),
    M.part("bolt_m5", "fasteners"),
]
EL_NAMES = {  # placement -> the human name the drawing must show
    "PSU": RU["psu"], "CTRL": RU["ctrl_card"], "DRV": RU["driver"], "DRV_2": RU["driver"],
    "M1": RU["stepper"], "M1_2": RU["stepper"], "K1": RU["relay"], "SENS": RU["sensor"],
}
P24, P0 = RU["p24"], RU["p0"]
EL_WIRES = [
    {"from": "PSU.V+", "to": "CTRL.J1.1", "net": P24},
    {"from": "PSU.V+", "to": "DRV.VCC", "net": P24},
    {"from": "PSU.V+", "to": "DRV_2.VCC", "net": P24},
    {"from": "PSU.V+", "to": "SENS.+", "net": P24},
    {"from": "PSU.V-", "to": "CTRL.J1.2", "net": P0},
    {"from": "PSU.V-", "to": "DRV.GND", "net": P0},
    {"from": "PSU.V-", "to": "DRV_2.GND", "net": P0},
    {"from": "PSU.V-", "to": "SENS.-", "net": P0},
    {"from": "PSU.V-", "to": "K1.A2", "net": P0},
    {"from": "CTRL.J3.2", "to": "DRV.PUL+", "net": "STEP_X"},
    {"from": "CTRL.J3.3", "to": "DRV_2.PUL+", "net": "STEP_Y"},
    {"from": "DRV.A+", "to": "M1.A+"},
    {"from": "DRV.A-", "to": "M1.A-"},
    {"from": "DRV_2.A+", "to": "M1_2.A+"},
    {"from": "DRV_2.A-", "to": "M1_2.A-"},
    {"from": "CTRL.J4.1", "to": "K1.A1", "net": "RELAY"},
    {"from": "SENS.OUT", "to": "CTRL.J4.2", "net": "HOME"},
]


@pytest.fixture
def electric(tmp_path):
    root = M.make_project(tmp_path, parts=EL_PARTS, wires=EL_WIRES, hoses=[])
    return root, build(root, "electric")


# ---------------------------------------------------------------- [1] two files, one manifest

def test_electric_writes_the_pdf_and_the_svg_and_names_both(electric):
    """[1]"""
    root, m = electric
    assert m.get("svg") == "build/kit/schemes/electric.svg", m
    assert (root / "build/kit/schemes/electric.svg").is_file(), "no SVG next to the PDF"
    assert m["scheme"] == "build/kit/schemes/electric.pdf" and (root / m["scheme"]).is_file(), m
    assert isinstance(m["sheets"], int) and m["sheets"] == len(page_texts(root / m["scheme"])), m
    assert set(m["elements"]) == set(EL_NAMES), m["elements"]
    assert m["connections"] == len(EL_WIRES), m


def test_pneumatic_writes_the_pdf_and_the_svg_and_names_both(tmp_path):
    """[1] same for the pneumatic scheme (the full test_schemes_model model)."""
    root = M.make_project(tmp_path)
    m = build(root, "pneumatic")
    assert m.get("svg") == "build/kit/schemes/pneumatic.svg", m
    assert (root / "build/kit/schemes/pneumatic.svg").is_file()
    assert m["scheme"] == "build/kit/schemes/pneumatic.pdf" and (root / m["scheme"]).is_file(), m
    assert {"sheets", "elements", "connections"} <= set(m), m
    assert m["connections"] == len(M.HOSES), m


def test_a_kind_without_connections_writes_no_svg(tmp_path):
    """[1] edge: no HOSES -> "scheme": null as today, and no SVG either (nothing to show)."""
    root = M.make_project(tmp_path, hoses=[])
    m = build(root, "pneumatic")
    assert m["scheme"] is None and m.get("svg") is None, m
    assert not (root / "build/kit/schemes/pneumatic.svg").exists()


# ---------------------------------------------------------------- [2] the PDF: drawing sheets first, then the table

def test_pdf_starts_with_drawing_sheets_naming_every_element_then_the_table(electric):
    """[2] the drawing sheets (before the connection table) carry every element's human name: title, else card.name, else desc."""
    root, m = electric
    drawing, table, texts = split_sheets(root / m["scheme"], EL_WIRES)
    assert drawing, f"the connection table starts on sheet 1: no drawing sheet before it ({len(texts)} sheets)"
    on_drawings = "".join(texts[i] for i in drawing)
    missing = {pl: nm for pl, nm in EL_NAMES.items() if squash(nm) not in on_drawings}
    assert not missing, f"elements not named on the drawing sheets: {missing}"


def test_the_human_name_prefers_title_then_card_name_then_desc(electric):
    """[2] K1 has a title and a card name: the title. CTRL has no title: its card name, not its desc. DRV has neither: its desc."""
    root, m = electric
    drawing, _, texts = split_sheets(root / m["scheme"], EL_WIRES)
    on_drawings = "".join(texts[i] for i in drawing)
    assert squash(RU["relay"]) in on_drawings
    assert squash(RU["ctrl_card"]) in on_drawings
    assert squash(RU["driver"]) in on_drawings


def test_the_connection_table_has_both_ends_of_every_wire(electric):
    """[2] after the drawings, as today: "<placement>.<port>" of both ends of every wire."""
    root, m = electric
    _, table, _ = split_sheets(root / m["scheme"], EL_WIRES)
    raw = page_raw(root / m["scheme"])
    tail = "\n".join(raw[i] for i in table)
    for w in EL_WIRES:
        assert has_end(tail, w["from"]) and has_end(tail, w["to"]), w


def test_sheet_1_draws_the_wires_as_vector_lines(electric):
    """[2] pymupdf finds many straight line items on sheet 1 (at least one per wire)."""
    root, m = electric
    n = line_items(root / m["scheme"], [0])
    assert n >= len(EL_WIRES), f"only {n} line items on sheet 1 for {len(EL_WIRES)} wires"


# Three blocks with the same ports in two models: few wires vs. six more signal wires between them.
TRIO = [named(p, "electrical", {f"P{i}": "" for i in range(1, 8)}, title=RU["block"].format(p=p)) for p in ("AA", "BB", "CC")]
FEW = [{"from": "AA.P1", "to": "BB.P1"}, {"from": "BB.P2", "to": "CC.P1"}]
MANY = FEW + [{"from": "AA.P2", "to": "BB.P3"}, {"from": "AA.P3", "to": "CC.P2"}, {"from": "AA.P4", "to": "CC.P3"},
              {"from": "BB.P4", "to": "CC.P4"}, {"from": "AA.P5", "to": "BB.P5"}, {"from": "AA.P6", "to": "CC.P5"}]


def test_every_wire_adds_drawn_lines(tmp_path):
    """[2] the lines ARE the wires: the same three blocks with six more wires between them draw at least six more line items on
    the drawing sheets (a box-and-address layout draws the same boxes either way)."""
    counts = {}
    for name, wires in (("few", FEW), ("many", MANY)):
        d = tmp_path / name
        d.mkdir()
        root = M.make_project(d, parts=TRIO, wires=wires, hoses=[])
        m = build(root, "electric")
        drawing, _, _ = split_sheets(root / m["scheme"], wires)
        assert drawing, f"{name}: no drawing sheet before the connection table"
        counts[name] = line_items(root / m["scheme"], drawing)
    assert counts["many"] - counts["few"] >= len(MANY) - len(FEW), counts


# ---------------------------------------------------------------- [3] the SVG

def test_svg_is_valid_xml_with_every_human_name_as_text(electric):
    """[3] text, not glyph outlines: the viewer and a search find the names."""
    root, m = electric
    svg = root / "build/kit/schemes/electric.svg"
    assert svg_root(svg).tag.rsplit("}", 1)[-1] == "svg"
    text = svg_text(svg)
    missing = {pl: nm for pl, nm in EL_NAMES.items() if squash(nm) not in text}
    assert not missing, f"elements not named in the SVG: {missing}"


def test_a_supply_net_on_many_ports_is_marked_with_its_name(electric):
    """[3] +24 V reaches 5 ports, 0 V 6 ports: drawn as supply / ground marks carrying the net name (loose: the name is text in
    the SVG and on the PDF's drawing sheets)."""
    root, m = electric
    text = svg_text(root / "build/kit/schemes/electric.svg")
    assert squash(P24) in text and squash(P0) in text, "the supply net names are not on the drawing"
    drawing, _, texts = split_sheets(root / m["scheme"], EL_WIRES)
    on_drawings = "".join(texts[i] for i in drawing)
    assert squash(P24) in on_drawings and squash(P0) in on_drawings


def test_gnd_is_a_supply_net_too(tmp_path):
    """[3] "GND" on 3+ ports: its name is on the drawing."""
    wires = [dict(w, net="GND") if w.get("net") == P0 else w for w in EL_WIRES]
    root = M.make_project(tmp_path, parts=EL_PARTS, wires=wires, hoses=[])
    build(root, "electric")
    assert "GND" in svg_text(root / "build/kit/schemes/electric.svg")


def test_one_svg_holds_every_drawing_sheet(tmp_path):
    """[3] a scheme too big for one sheet (a controller with 40 wired pins, 40 sensors): the one SVG names every element, as the
    PDF's drawing sheets do."""
    n = 40
    parts = [named("CTRL", "electrical", {f"P{i}": "" for i in range(n)}, title=RU["line_ctrl"])]
    parts += [named(f"S{i}", "electrical", {"OUT": ""}, title=RU["sensor_n"].format(i=i)) for i in range(n)]
    wires = [{"from": f"S{i}.OUT", "to": f"CTRL.P{i}"} for i in range(n)]
    root = M.make_project(tmp_path, parts=parts, wires=wires, hoses=[])
    m = build(root, "electric")
    names = [RU["line_ctrl"]] + [RU["sensor_n"].format(i=i) for i in range(n)]
    text = svg_text(root / "build/kit/schemes/electric.svg")
    assert not [x for x in names if squash(x) not in text], "elements missing from the SVG"
    drawing, _, texts = split_sheets(root / m["scheme"], wires)
    on_drawings = "".join(texts[i] for i in drawing)
    assert not [x for x in names if squash(x) not in on_drawings], "elements missing from the PDF drawing sheets"


# ---------------------------------------------------------------- [4] pneumatics

PN_PARTS = [
    named("FRL", "pneumatic", {"IN": "", "OUT": ""}, title=RU["frl_full"]),
    named("V1", "pneumatic", {"1": "P", "2": "B", "4": "A", "3": "R", "5": "R", "Y1+": RU["coil"], "Y1-": RU["coil"]},
          title=RU["valve_52"]),
    named("CYL", "pneumatic", {"A": "", "B": ""}, title=RU["cylinder"]),
    named("SIL", "pneumatic", {"IN": ""}, title=RU["silencer"], placements=["SIL", "SIL_2"]),
]
PN_HOSES = [
    {"from": "FRL.OUT", "to": "V1.1", "spec": "PU 8x6"},
    {"from": "V1.4", "to": "CYL.A", "spec": "PU 6x4"},
    {"from": "V1.2", "to": "CYL.B", "spec": "PU 6x4"},
    {"from": "V1.3", "to": "SIL.IN"},
    {"from": "V1.5", "to": "SIL_2.IN"},
]
CUP = named("CUP", "pneumatic", {"IN": ""}, title=RU["vacuum_cup"])


@pytest.mark.parametrize("actuator", ["cylinder", "suction cup"])
def test_pneumatic_drawing_with_valve_actuator_and_silencer(tmp_path, actuator):
    """[4] both files; every element named on the SVG and the PDF's drawing sheets; hoses numbered H1.. on the drawing."""
    parts, hoses = list(PN_PARTS), list(PN_HOSES)
    if actuator == "suction cup":
        parts = [p for p in parts if p["id"] != "CYL"] + [CUP]
        hoses = [h for h in hoses if not h["to"].startswith("CYL.")] + [{"from": "V1.4", "to": "CUP.IN"}]
    root = M.make_project(tmp_path, parts=M.PARTS[:4] + parts, wires=M.WIRES, hoses=hoses)
    m = build(root, "pneumatic")
    svg = root / "build/kit/schemes/pneumatic.svg"
    assert m.get("svg") == "build/kit/schemes/pneumatic.svg" and svg.is_file(), m
    assert (root / "build/kit/schemes/pneumatic.pdf").is_file()
    assert m["connections"] == len(hoses), m
    names = [p["title"] for p in parts]
    text = svg_text(svg)
    assert not [x for x in names if squash(x) not in text], f"pneumatic elements missing from the SVG: {names}"
    drawing, table, texts = split_sheets(root / m["scheme"], hoses)
    assert drawing, "no drawing sheet before the hose table"
    on_drawings = "".join(texts[i] for i in drawing)
    assert not [x for x in names if squash(x) not in on_drawings], "pneumatic elements missing from the drawing sheets"
    strings = svg_strings(svg)
    for i in range(1, len(hoses) + 1):
        assert any(re.search(rf"(?<![A-Za-z0-9])H{i}(?![0-9])", s) for s in strings), f"hose H{i} is not numbered on the drawing"


# ---------------------------------------------------------------- review regressions

def test_every_hose_of_a_branching_net_has_its_own_number(tmp_path):
    """One port hosed to two places (valves fed in a chain): every hose of the table is numbered on the drawing, not only the
    first of its net."""
    parts = [named("FRL", "pneumatic", {"IN": "", "OUT": ""}, title=RU["frl"]),
             named("V1", "pneumatic", {"P": "", "A": "", "coil+": "", "coil-": ""}, title=RU["valve_n"].format(n=1)),
             named("V2", "pneumatic", {"P": "", "A": "", "coil+": "", "coil-": ""}, title=RU["valve_n"].format(n=2)),
             named("C1", "pneumatic", {"IN": ""}, title=RU["cup_n"].format(n=1)), named("C2", "pneumatic", {"IN": ""}, title=RU["cup_n"].format(n=2))]
    hoses = [{"from": "FRL.OUT", "to": "V1.P"}, {"from": "V1.P", "to": "V2.P"}, {"from": "V1.A", "to": "C1.IN"},
             {"from": "V2.A", "to": "C2.IN"}]
    root = M.make_project(tmp_path, parts=M.PARTS[:4] + parts, wires=M.WIRES, hoses=hoses)
    m = build(root, "pneumatic")
    strings = svg_strings(root / m["svg"])
    for i in range(1, len(hoses) + 1):
        assert any(re.search(rf"(?<![A-Za-z0-9])H{i}(?![0-9])", s) for s in strings), f"hose H{i} is not numbered on the drawing"


def test_a_net_between_two_controllers_is_a_flag_at_both_ends(tmp_path):
    """Two boards with pins (6+ GPIO ports each) joined by a named net: the name stands at both boards (a flag each)."""
    pins = {f"GPIO{i}": "" for i in range(8)}
    parts = [named("A", "electrical", pins, title=RU["board_a"]), named("B", "electrical", pins, title=RU["board_b"])]
    wires = [{"from": "A.GPIO1", "to": "B.GPIO2", "net": "UART_TX"}, {"from": "B.GPIO3", "to": "A.GPIO4", "net": "UART_RX"}]
    root = M.make_project(tmp_path, parts=parts, wires=wires, hoses=[])
    m = build(root, "electric")
    strings = svg_strings(root / m["svg"])
    for net in ("UART_TX", "UART_RX"):
        assert sum(s.strip() == net for s in strings) >= 2, f"{net} is not flagged at both boards: {strings}"


def test_the_layout_engine_is_tracked_so_it_ships():
    """The framework goes to the workers as `git archive HEAD:agent`; the repo ignores vendor/ dirs, so ELK must be force-added."""
    import subprocess
    here = Path(__file__).resolve().parents[1]
    for f in ("vendor/elk.bundled.js", "elk_layout.cjs", "scheme_draw.py"):
        r = subprocess.run(["git", "ls-files", "--error-unmatch", f], cwd=here, capture_output=True, text=True)
        assert r.returncode == 0, f"{f} is not tracked by git: the workers would not get it"
