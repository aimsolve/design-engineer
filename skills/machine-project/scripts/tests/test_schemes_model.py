"""schemes.py: the machine scheme is a PROJECTION OF THE ONE MODEL of the machine.

Written from the spec before the implementation. The model is hand-written here (build/model/parts.json, as the model build
writes it) plus mechanics/cad/connections.py; no project has assemble.py, so nothing heavy runs.

  uv run --isolated --no-project --python 3.12 --with pymupdf --with matplotlib --with pytest \
      pytest agent/skills/machine-project/scripts/tests/test_schemes_model.py -q

Contract under test:
  parts.json part records may carry `ports` {port: label} and `scheme: false`. Scheme elements = placements of parts whose kind is
  electrical, motors or pneumatic and not `scheme: false`, addressed by placement name. mechanics/cad/connections.py has WIRES
  (electric) and HOSES (pneumatic): [{"from": "<placement>.<port>", "to": "<placement>.<port>", "net"?, "spec"?}]; the placement is
  everything before the FIRST dot, so a port may contain dots ("CTRL.J3.2").
  `python schemes.py <electric|pneumatic> <manifest>` (cwd = project root) draws ONE PDF build/kit/schemes/<kind>.pdf and writes
  {"scheme": "build/kit/schemes/<kind>.pdf", "sheets": n, "elements": [...], "connections": n}; the PDF text has every element's
  placement name and both endpoints of every connection; the sheets carry public/config.json "title". No connections of a kind:
  exit 0, "scheme": null, "sheets": 0, no PDF. Unknown placement / undeclared port / connections.py raising / no connections.py:
  exit non-zero, no manifest.
  schemes.gate(root) -> [(status, "schemes", detail)]: two-way check of connections.py against parts.json (unknown endpoint FAIL,
  unconnected element FAIL naming it and `scheme: false`), raising connections.py FAIL with the error, parts.json missing WARN,
  a page link to a PDF FAIL (kept from step 1).
Test names carry the done-when they serve: [1] the button gives a PDF drawn from the model: every element and wire is on it;
[2] check.py tells the agent about a wire naming something the model lacks, or a model element on no scheme.
"""
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from samples_i18n import SAMPLES

RU = SAMPLES["ru"]

SCRIPTS = Path(__file__).resolve().parent.parent
SCHEMES = SCRIPTS / "schemes.py"
CHECK = SCRIPTS / "check.py"
SCAFFOLD = SCRIPTS / "scaffold.py"
TITLE = "LINE-7 Wrapper"
EYE = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def part(pid, kind, ports=None, placements=None, node="machine", **extra):
    """A parts.json record as the model build writes it; placements default to one placement named after the part."""
    rec = {"id": pid, "key": f"k-{pid}", "name": pid, "node": node, "process": "bought", "kind": kind,
           "title": f"{pid} title", "desc": "", "code": f"CODE-{pid}",
           "placements": [{"name": n, "node": node, "loc": EYE} for n in (placements or [pid])]}
    if ports is not None:
        rec["ports"] = ports
    rec.update(extra)
    return rec


# The model of a small packing machine: a stepper on two axes driven by TB6600 drivers, a controller board with a dotted
# connector pin, a PSU, a valve, a cylinder, an air prep unit; plus parts that are never scheme elements.
PARTS = [
    part("PSU", "electrical", {"V+": "24V", "V-": "0V"}),
    part("CTRL", "electrical", {"J3.2": "STEP", "J3.3": "DIR", "J1.1": "+24", "J1.2": "GND"}),
    part("TB6600", "electrical", {"PUL+": "", "DIR+": "", "VCC": "", "GND": "", "A+": "", "A-": ""},
         placements=["TB6600", "TB6600_2"]),
    part("M1", "motors", {"A+": "coil A", "A-": "coil A"}, placements=["M1", "M1_2"]),
    part("FRL", "pneumatic", {"IN": "", "OUT": ""}),
    part("V1", "pneumatic", {"1": "P", "2": "A", "3": "R"}),
    part("CYL", "pneumatic", {"A": "", "B": ""}),
    part("bolt_m5", "fasteners", placements=["bolt_m5", "bolt_m5_2"]),
    part("bearing_608", "bearings"),
    part("cable_tray", "electrical", {"X": ""}, scheme=False),          # electrical, but not a scheme element
]

EL_ELEMENTS = {"PSU", "CTRL", "TB6600", "TB6600_2", "M1", "M1_2"}
PN_ELEMENTS = {"FRL", "V1", "CYL"}
NOT_ELEMENTS = {"bolt_m5", "bolt_m5_2", "bearing_608", "cable_tray"}

WIRES = [
    {"from": "PSU.V+", "to": "CTRL.J1.1", "net": "+24V", "spec": "1.5 mm2"},
    {"from": "PSU.V-", "to": "CTRL.J1.2", "net": "GND"},
    {"from": "CTRL.J3.2", "to": "TB6600.PUL+", "net": "STEP_X"},
    {"from": "CTRL.J3.3", "to": "TB6600_2.PUL+", "net": "STEP_Y"},
    {"from": "PSU.V+", "to": "TB6600.VCC"},
    {"from": "PSU.V+", "to": "TB6600_2.VCC"},
    {"from": "TB6600.A+", "to": "M1.A+", "spec": "0.75 mm2"},
    {"from": "TB6600.A-", "to": "M1.A-"},
    {"from": "TB6600_2.A+", "to": "M1_2.A+"},
    {"from": "TB6600_2.A-", "to": "M1_2.A-"},
]
HOSES = [
    {"from": "FRL.OUT", "to": "V1.1", "spec": "PU 8x6"},
    {"from": "V1.2", "to": "CYL.A", "spec": "PU 6x4"},
    {"from": "V1.3", "to": "CYL.B"},
]


def connections_py(wires, hoses, head=""):
    return head + f"WIRES = {wires!r}\nHOSES = {hoses!r}\n"


def make_project(tmp_path, parts=None, wires=None, hoses=None, connections=None, title=TITLE):
    """A code-CAD project root: build/model/parts.json, mechanics/cad/connections.py (None -> the full WIRES / HOSES;
    False -> no file), public/config.json with the title. No assemble.py (no model refresh)."""
    root = tmp_path / "proj"
    root.mkdir()
    write(root, "mechanics/cad/params.py", "WIRE = '0.75 mm2'\n")
    write(root, "build/model/parts.json", json.dumps({"parts": PARTS if parts is None else parts}, ensure_ascii=False))
    if connections is None:
        connections = connections_py(WIRES if wires is None else wires, HOSES if hoses is None else hoses)
    if connections is not False:
        write(root, "mechanics/cad/connections.py", connections)
    if title is not None:
        write(root, "public/config.json", json.dumps({"title": title, "links": []}, ensure_ascii=False))
    return root


def run_schemes(root, kind):
    return subprocess.run([sys.executable, str(SCHEMES), kind, f"build/kit/{kind}.json"], cwd=root,
                          capture_output=True, text=True, timeout=180)


def manifest(root, kind):
    return json.loads((Path(root) / f"build/kit/{kind}.json").read_text())


def pdf_text(pdf):
    """All text of the PDF, whitespace removed (a label may be drawn with spaces or line breaks between its glyph runs)."""
    import fitz
    with fitz.open(pdf) as doc:
        return re.sub(r"\s+", "", "".join(p.get_text() for p in doc))


def pdf_pages(pdf):
    import fitz
    with fitz.open(pdf) as doc:
        return doc.page_count


def built(root, kind):
    r = run_schemes(root, kind)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return manifest(root, kind)


# ---------------------------------------------------------------- schemes.py <kind> <manifest>: the PDF from the model

def test_electric_one_pdf_and_the_manifest_from_the_model(tmp_path):
    """[1] one PDF; the manifest names it, counts its pages, lists the electric elements and counts the wires."""
    root = make_project(tmp_path)
    m = built(root, "electric")
    assert m["scheme"] == "build/kit/schemes/electric.pdf", m
    pdf = root / m["scheme"]
    assert pdf.is_file()
    assert isinstance(m["sheets"], int) and m["sheets"] >= 1 and m["sheets"] == pdf_pages(pdf), m
    assert m["connections"] == len(WIRES), m
    assert EL_ELEMENTS <= set(m["elements"]), f"every electrical / motors placement is an element: {m['elements']}"
    assert not NOT_ELEMENTS & set(m["elements"]), f"fasteners, bearings and scheme: false are no elements: {m['elements']}"


def test_electric_pdf_has_every_element_and_both_ends_of_every_wire(tmp_path):
    """[1] The client's check: every element of the model and every wire (both endpoints, as text) is on the PDF."""
    root = make_project(tmp_path)
    m = built(root, "electric")
    text = pdf_text(root / m["scheme"])
    for name in EL_ELEMENTS:
        assert name in text, f"element {name} is not on the scheme"
    for w in WIRES:
        for end in (w["from"], w["to"]):
            assert end.replace(" ", "") in text, f"wire endpoint {end} is not on the scheme"


def test_every_placement_of_a_part_is_its_own_element(tmp_path):
    """[1] M1 and M1_2 are two motors of one part file: both are on the scheme, each with its own wires."""
    root = make_project(tmp_path)
    m = built(root, "electric")
    assert {"M1", "M1_2", "TB6600", "TB6600_2"} <= set(m["elements"]), m["elements"]
    text = pdf_text(root / m["scheme"])
    assert "M1_2.A+" in text and "TB6600_2.PUL+" in text


def test_a_dotted_board_pin_resolves_to_its_placement(tmp_path):
    """[1] "CTRL.J3.2": placement CTRL, port J3.2 (split at the first dot): valid, and on the PDF as written."""
    root = make_project(tmp_path)
    m = built(root, "electric")
    assert "CTRL" in m["elements"]
    text = pdf_text(root / m["scheme"])
    assert "CTRL.J3.2" in text and "CTRL.J1.1" in text


def test_the_sheets_carry_the_project_title(tmp_path):
    root = make_project(tmp_path, title="DRYER-2 Packline")
    m = built(root, "electric")
    assert "DRYER-2Packline" in pdf_text(root / m["scheme"])


def test_pneumatic_one_pdf_with_every_valve_and_hose(tmp_path):
    """[1] The pneumatic scheme: its own PDF from HOSES; the electric PDF is not written by this run."""
    root = make_project(tmp_path)
    m = built(root, "pneumatic")
    assert m["scheme"] == "build/kit/schemes/pneumatic.pdf", m
    assert m["connections"] == len(HOSES), m
    assert PN_ELEMENTS <= set(m["elements"]), m["elements"]
    assert not NOT_ELEMENTS & set(m["elements"]), m["elements"]
    pdf = root / m["scheme"]
    assert m["sheets"] == pdf_pages(pdf) >= 1
    text = pdf_text(pdf)
    for name in PN_ELEMENTS:
        assert name in text, f"element {name} is not on the scheme"
    for h in HOSES:
        for end in (h["from"], h["to"]):
            assert end in text, f"hose endpoint {end} is not on the scheme"
    assert not (root / "build/kit/schemes/electric.pdf").exists()


def test_connections_py_may_import_params(tmp_path):
    """connections.py is plain Python next to params.py: `import params` works (cwd and sys.path are the project's)."""
    head = "import params\n"
    wires = [dict(w, spec="PARAM") for w in WIRES]
    src = connections_py(wires, HOSES, head).replace("'PARAM'", "params.WIRE")
    root = make_project(tmp_path, connections=src)
    m = built(root, "electric")
    assert m["connections"] == len(WIRES)


def test_a_kind_without_connections_gives_no_pdf_and_says_none(tmp_path):
    """No HOSES: pneumatic exits 0 with "scheme": null and 0 sheets, and writes no PDF (the button shows «none»)."""
    root = make_project(tmp_path, hoses=[])
    m = built(root, "pneumatic")
    assert m["scheme"] is None and m["sheets"] == 0, m
    assert not (root / "build/kit/schemes/pneumatic.pdf").exists()
    assert built(root, "electric")["scheme"] == "build/kit/schemes/electric.pdf", "the other kind still draws"


def test_a_kind_whose_list_is_missing_counts_as_no_connections(tmp_path):
    root = make_project(tmp_path, connections=f"WIRES = {WIRES!r}\n")
    m = built(root, "pneumatic")
    assert m["scheme"] is None and m["sheets"] == 0, m


# ---------------------------------------------------------------- invalid data: non-zero, no manifest

def assert_refused(root, kind, why):
    r = run_schemes(root, kind)
    assert r.returncode != 0, f"{why}: exit 0\n{r.stdout[-1000:]}"
    assert not (root / f"build/kit/{kind}.json").exists(), f"{why}: a manifest was written"
    return r


def test_a_wire_to_an_unknown_placement_is_refused(tmp_path):
    """The pneumatic kind of the same project draws (so schemes.py works); the electric one names M9, which the model lacks."""
    root = make_project(tmp_path, wires=WIRES + [{"from": "TB6600.A+", "to": "M9.A+"}])
    assert built(root, "pneumatic")["scheme"]
    r = assert_refused(root, "electric", "unknown placement M9")
    assert "M9" in r.stdout + r.stderr, "the error names the unknown placement"


def test_a_wire_to_an_undeclared_port_is_refused(tmp_path):
    root = make_project(tmp_path, wires=WIRES + [{"from": "PSU.V+", "to": "TB6600.ENA+"}])
    assert built(root, "pneumatic")["scheme"]
    r = assert_refused(root, "electric", "TB6600 declares no port ENA+")
    assert "ENA+" in r.stdout + r.stderr


def test_a_hose_to_a_part_without_that_port_is_refused(tmp_path):
    root = make_project(tmp_path, hoses=HOSES + [{"from": "V1.4", "to": "CYL.A"}])
    assert built(root, "electric")["scheme"]
    assert_refused(root, "pneumatic", "V1 declares no port 4")


def test_connections_py_that_raises_is_refused(tmp_path):
    root = make_project(tmp_path)
    assert built(root, "electric")["scheme"]
    (root / "build/kit/electric.json").unlink()
    write(root, "mechanics/cad/connections.py", "raise RuntimeError('wiring table broken-42')\n")
    assert_refused(root, "electric", "connections.py raises")


def test_no_connections_py_is_refused(tmp_path):
    root = make_project(tmp_path)
    assert built(root, "electric")["scheme"]
    (root / "build/kit/electric.json").unlink()
    (root / "mechanics/cad/connections.py").unlink()
    assert_refused(root, "electric", "no connections.py")


# ---------------------------------------------------------------- schemes.gate(root): the two-way check

def model_built_after(root):
    """The model build ran after the sources were written (as the router does after a turn): parts.json is newer than them."""
    pj = Path(root) / "build/model/parts.json"
    if pj.is_file():
        t = time.time() + 5
        os.utime(pj, (t, t))


def gate(root, fresh_model=True):
    import schemes          # imported here: a missing module fails each test, not the collection
    if fresh_model:
        model_built_after(root)
    res = schemes.gate(str(root))
    assert isinstance(res, list) and res, res
    assert all(len(r) == 3 and r[0] in ("PASS", "WARN", "FAIL") and r[1] == "schemes" for r in res), res
    return res


def fails(res):
    return [r for r in res if r[0] == "FAIL"]


def fail_text(res):
    return "\n".join(r[2] for r in fails(res))


def test_gate_passes_a_project_without_connections_and_pdf_links(tmp_path):
    res = gate(make_project(tmp_path, connections=False))
    assert not fails(res), res
    assert any(r[0] == "PASS" for r in res), res


def test_gate_passes_only_when_every_element_is_wired_and_every_end_resolves(tmp_path):
    """The full model PASSes (fasteners, bearings and the `scheme: false` cable tray have no connection and need none); the same
    model with the PSU's wires removed FAILs: the PASS is the two-way check passing, not the gate looking at nothing."""
    root = make_project(tmp_path)
    res = gate(root)
    assert not fails(res), res
    assert any(r[0] == "PASS" for r in res), res
    write(root, "mechanics/cad/connections.py", connections_py([w for w in WIRES if "PSU" not in w["from"] + w["to"]], HOSES))
    assert "PSU" in fail_text(gate(root))


def test_gate_fails_a_wire_naming_an_unknown_placement(tmp_path):
    """[2] the wire names M9, the model has no M9."""
    res = gate(make_project(tmp_path, wires=WIRES + [{"from": "TB6600.A+", "to": "M9.A+"}]))
    assert fails(res), res
    assert "M9" in fail_text(res), res


def test_gate_fails_a_wire_naming_an_undeclared_port(tmp_path):
    """[2] TB6600 is in the model, its port ENA+ is not."""
    res = gate(make_project(tmp_path, wires=WIRES + [{"from": "PSU.V+", "to": "TB6600.ENA+"}]))
    assert fails(res), res
    assert "ENA+" in fail_text(res), res


def test_gate_fails_a_hose_naming_an_unknown_placement(tmp_path):
    res = gate(make_project(tmp_path, hoses=HOSES + [{"from": "V1.2", "to": "CYL9.A"}]))
    assert "CYL9" in fail_text(res), res


def test_gate_fails_an_electric_element_on_no_scheme(tmp_path):
    """[2] the PSU is in the model, no wire touches it: FAIL naming it, saying to wire it or mark it `scheme: false`."""
    parts = PARTS + [part("RELAY_K1", "electrical", {"A1": "", "A2": ""})]
    res = gate(make_project(tmp_path, parts=parts))
    bad = [r for r in fails(res) if "RELAY_K1" in r[2]]
    assert bad, res
    assert re.search(r"scheme\W{0,3}false", bad[0][2]), f"the message tells how to exempt it: {bad[0][2]}"


def test_gate_fails_a_second_placement_left_unwired(tmp_path):
    """[2] M1 is wired, its second placement M1_2 is not: the element is the placement, so M1_2 FAILs."""
    wires = [w for w in WIRES if not (w["to"].startswith("M1_2.") or w["from"].startswith("M1_2."))]
    res = gate(make_project(tmp_path, wires=wires))
    assert "M1_2" in fail_text(res), res


def test_gate_fails_a_pneumatic_element_on_no_scheme(tmp_path):
    parts = PARTS + [part("V2", "pneumatic", {"1": "P", "2": "A"})]
    res = gate(make_project(tmp_path, parts=parts))
    assert "V2" in fail_text(res), res


def test_gate_fails_an_unwired_motor(tmp_path):
    parts = PARTS + [part("M_FAN", "motors", {"+": "", "-": ""})]
    res = gate(make_project(tmp_path, parts=parts))
    assert "M_FAN" in fail_text(res), res


def test_gate_does_not_ask_to_wire_scheme_false_or_other_kinds(tmp_path):
    """An unwired part marked `scheme: false`, a new fastener and a bearing: still PASS. The same lamp without `scheme: false`
    FAILs, so it is the mark that exempts it."""
    extra = [part("screw_m3", "fasteners"), part("bearing_6001", "bearings")]
    root = make_project(tmp_path, parts=PARTS + extra + [part("lamp_decor", "electrical", {"L": ""}, scheme=False)])
    res = gate(root)
    assert not fails(res), res
    write(root, "build/model/parts.json", json.dumps({"parts": PARTS + extra + [part("lamp_decor", "electrical", {"L": ""})]}))
    assert "lamp_decor" in fail_text(gate(root))


def test_gate_fails_connections_py_that_raises_with_the_error(tmp_path):
    root = make_project(tmp_path, connections="raise RuntimeError('wiring table broken-42')\n")
    res = gate(root)
    assert "broken-42" in fail_text(res), res


def test_gate_warns_without_parts_json(tmp_path):
    """No model built yet: WARN, not FAIL (the agent cannot wire against a model that is not there)."""
    root = make_project(tmp_path)
    (root / "build/model/parts.json").unlink()
    res = gate(root)
    assert not fails(res), res
    assert any(r[0] == "WARN" for r in res), res


@pytest.mark.parametrize("url", ["docs/electric.pdf", "https://files.example.com/Pneumatic.PDF"])
def test_gate_still_fails_a_page_link_to_a_pdf(tmp_path, url):
    """Kept from step 1: the page's own link to a PDF scheme FAILs, also in a project with a valid connections.py."""
    root = make_project(tmp_path)
    write(root, "public/config.json", json.dumps({"title": TITLE, "links": [{"label": RU["link_scheme"], "url": url}]}))
    assert fails(gate(root)), url


def test_gate_still_fails_a_pdf_link_in_the_site_script(tmp_path):
    root = make_project(tmp_path, connections=False)
    write(root, "kit.json", json.dumps({"inputs": ["mechanics/cad/**/*.py"], "scopes": {"bought": {"export": "true"}},
                                        "site": "bash sim/site.sh"}))
    write(root, "sim/site.sh", f'python3 build.py --out site --link "{RU["link_scheme"]}=docs/scheme.pdf"\n')
    assert fails(gate(root))


# ---------------------------------------------------------------- check.py, as the agent runs it

def scaffolded(tmp_path, wires):
    root = tmp_path / "m"
    r = subprocess.run([sys.executable, str(SCAFFOLD), str(root), "--name", "m"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    write(root, "build/model/parts.json", json.dumps({"parts": PARTS}, ensure_ascii=False))
    write(root, "mechanics/cad/connections.py", connections_py(wires, HOSES))
    return root


def check_json(root):
    model_built_after(root)
    r = subprocess.run([sys.executable, str(CHECK), str(root), "--no-validate", "--json"], capture_output=True, text=True, timeout=300)
    return json.loads(r.stdout), r.stderr


def test_check_py_tells_the_agent_about_a_wire_to_a_missing_part(tmp_path):
    """[2] the agent runs check.py: rule "schemes" FAILs and names the placement the model lacks."""
    out, _ = check_json(scaffolded(tmp_path, WIRES + [{"from": "TB6600.A+", "to": "M9.A+"}]))
    got = [f for f in out["fails"] if f["rule"] == "schemes"]
    assert got and any("M9" in f["detail"] for f in got), out["fails"]


def test_check_py_tells_the_agent_about_an_element_on_no_scheme(tmp_path):
    """[2]"""
    out, _ = check_json(scaffolded(tmp_path, [w for w in WIRES if "PSU" not in w["from"] + w["to"]]))
    got = [f for f in out["fails"] if f["rule"] == "schemes"]
    assert got and any("PSU" in f["detail"] for f in got), out["fails"]


def test_gate_waits_for_the_model_when_the_sources_are_newer(tmp_path):
    """A turn added a part's port and its wire; the router rebuilds the model only after the turn: no false FAIL, a WARN."""
    root = make_project(tmp_path, wires=WIRES + [{"from": "M9.X", "to": "PSU.+V"}])
    pj = root / "build/model/parts.json"
    os.utime(pj, (time.time() - 60, time.time() - 60))
    res = gate(root, fresh_model=False)
    assert res[0][0] == "WARN", res
    assert gate(root)[0][0] == "FAIL"           # once the model is built, the same wire fails


def test_a_controller_with_70_wired_pins_keeps_every_wire_in_the_connection_table(tmp_path):
    """A controller with 70 wired pins (a real ESP32 terminal board has ~53): every pin's wire is in the connection table
    (both ends, on the sheets after the drawings), none dropped; the drawing also writes its SVG."""
    pins = {f"P{i}": "" for i in range(70)}
    parts = [part("CTRL", "electrical", pins)] + [part(f"S{i}", "electrical", {"OUT": ""}) for i in range(70)]
    wires = [{"from": f"S{i}.OUT", "to": f"CTRL.P{i}"} for i in range(70)]
    root = make_project(tmp_path, parts=parts, wires=wires, hoses=[])
    r = run_schemes(root, "electric")
    assert r.returncode == 0, r.stdout + r.stderr
    m = manifest(root, "electric")
    assert m["connections"] == 70, m
    assert m.get("svg") == "build/kit/schemes/electric.svg" and (root / m["svg"]).is_file(), m
    import fitz
    with fitz.open(root / m["scheme"]) as doc:
        pages = [p.get_text() for p in doc]
    table = "\n".join(pages[1:])       # sheet 1 is a drawing; the table follows the drawing sheets
    has = lambda end: re.search(r"(?<!\S)" + re.escape(end) + r"(?!\S)", table)      # a whole token: CTRL.P1 is not CTRL.P10
    missing = [i for i in range(70) if not has(f"S{i}.OUT") or not has(f"CTRL.P{i}")]
    assert not missing, f"wires missing from the connection table: {missing[:10]}"


def test_gate_fails_ports_that_are_not_a_name_label_object(tmp_path):
    parts = [dict(p, ports=list(p["ports"])) if p["id"] == "PSU" else p for p in PARTS]
    res = gate(make_project(tmp_path, parts=parts))
    assert res[0][0] == "FAIL" and "ports must be" in res[0][2], res


def test_gate_fails_a_wire_to_itself_and_a_wire_listed_twice(tmp_path):
    res = gate(make_project(tmp_path, wires=WIRES + [WIRES[0], {"from": WIRES[0]["from"], "to": WIRES[0]["from"]}]))
    assert res[0][0] == "FAIL" and "listed twice" in res[0][2] and "wired to itself" in res[0][2], res


def test_gate_fails_a_pdf_link_with_a_russian_two_word_label(tmp_path):
    root = make_project(tmp_path)
    write(root, "kit.json", json.dumps({"site": "bash sim/site.sh"}))
    write(root, "sim/site.sh", f'python3 build.py --out site --link "{RU["link_scheme_2w"]}=docs/e.pdf"\n')
    assert gate(root)[0][0] == "FAIL"
