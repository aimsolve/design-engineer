"""device.py: the virtual device is a PROJECTION OF THE ONE MODEL, like the schemes.

Written from the spec before the implementation. The model is hand-written here (build/model/parts.json as the model build writes
it, part records with `ports` and `sim`) plus mechanics/cad/connections.py WIRES; only the last two tests run a real model build
(build123d, skipped without it).

  uv run --isolated --no-project --python 3.12 --with pytest \
      pytest agent/skills/machine-project/scripts/tests/test_device_model.py -q
  (the model-build tests: ~/.cache/b3d-venv/bin/python -m pytest ... , a venv with build123d==0.13.0)

Contract under test:
  part field `sim`: {"model": "esp32", "fw": "<firmware build>"} = an MCU board (ports GPIO<n> / IO<n> = pin n; GND, 3V3, 5V, VCC =
  rails); {"model": "button", ...} (two contacts, any names); {"model": "encoder", ...} (A, B, C, optional SW + SW2);
  {"model": "st7789"|"st7735"|"ili9341", w, h, x_off, y_off, glass} (SCL SDA DC CS RST BLK VCC GND); {"model": "ssd1306", w, h, addr}
  (SCL SDA VCC GND). Rails: a port is gnd if its net reaches an MCU port GND or a wire with net like "GND" / "0 V"; vcc if it reaches
  3V3 / 5V / VCC or a net like "+3.3 V". Nets = union of WIRES endpoints (a connector port with two wires joins them).
  device.derive(parts, conns) -> (device, problems): parts = schemes.load_parts(root) shape {placement: part record},
  conns = {"WIRES": [...], "HOSES": [...]}. device = {"boards": [{id, part, model, fw}], "elements": [{id, part, model, board,
  params: <sim minus model>, ports: {port: {"board", "pin"} | {"rail": "gnd"|"vcc"} | None}}]}; problems = sentences.
  `python3 device.py <root>` writes <root>/build/model/device.json, exit 0; problems -> printed, exit 1. A project with no `sim`
  part is untouched (no device.json, no problem). check.py rule "device" FAILs with the problems. The model build
  (cad_incremental.py --model) writes build/model/device.json.
Test names carry the done-when they serve: [1] device.json derived from parts.json + connections, problems as sentences, rule
«device».
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

SCRIPTS = Path(__file__).resolve().parent.parent
DEVICE = SCRIPTS / "device.py"
CHECK = SCRIPTS / "check.py"
EYE = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
sys.path.insert(0, str(SCRIPTS))


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def part(pid, ports=None, sim=None, placements=None, kind="electrical", **extra):
    """A parts.json record as the model build writes it (one placement named after the part unless given)."""
    rec = {"id": pid, "key": f"k-{pid}", "name": pid, "node": "device", "process": "bought", "kind": kind,
           "placements": [{"name": n, "node": "device", "loc": EYE} for n in (placements or [pid])]}
    if ports is not None:
        rec["ports"] = {p: "" for p in ports} if isinstance(ports, (list, tuple)) else ports
    if sim is not None:
        rec["sim"] = sim
    rec.update(extra)
    return rec


ESP_PORTS = ["GPIO27", "GPIO26", "GPIO14", "IO25", "GPIO32", "GPIO33", "GPIO18", "GPIO23", "GPIO5", "GPIO16", "GPIO17", "GPIO4",
             "GPIO21", "GPIO22", "GPIO12", "GPIO13", "GND", "3V3", "5V"]
TFT_PORTS = ["SCL", "SDA", "DC", "CS", "RST", "BLK", "VCC", "GND"]
GLASS = {"center": [0, 0, 1.6], "normal": [0, 0, 1], "up": [0, 1, 0], "size": [30.2, 37.7]}

# The MP3 player of the issue, in small: an ESP32 board, two placements of one tact switch, a button to a 3V3 net of the LDO,
# an encoder reached through a connector, an ST7789, an SSD1306, an LDO, a connector J1 and a part that is not simulated.
PARTS = [
    part("esp32_devkit", ESP_PORTS, {"model": "esp32", "fw": "player"}, placements=["mcu"]),
    part("tact_switch", ["1", "2"], {"model": "button", "key": "Space", "bounce_ms": 2, "label": "Play"}, placements=["btn_play", "btn_next"]),
    part("btn_hi", ["A", "B"], {"model": "button"}),
    part("ec11", ["A", "B", "C", "SW", "SW2"], {"model": "encoder", "keys": ["ArrowLeft", "ArrowRight"], "push_key": "Enter", "detent": 4},
         placements=["enc"]),
    part("tft_st7789", TFT_PORTS, {"model": "st7789", "w": 170, "h": 320, "x_off": 35, "y_off": 0, "glass": GLASS}, placements=["tft"]),
    part("oled", ["SCL", "SDA", "VCC", "GND"], {"model": "ssd1306", "w": 128, "h": 64, "addr": 60}),
    part("ldo", ["IN", "OUT", "GND"]),
    part("j1", ["1", "2", "3", "4"]),
    part("battery", ["+", "-"]),
]

WIRES = [
    {"from": "mcu.GPIO27", "to": "btn_play.1"}, {"from": "btn_play.2", "to": "mcu.GND", "net": "GND"},
    {"from": "mcu.GPIO14", "to": "btn_next.2"}, {"from": "btn_next.1", "to": "ldo.GND", "net": "0 V"},
    {"from": "mcu.GPIO26", "to": "btn_hi.A"}, {"from": "btn_hi.B", "to": "ldo.OUT", "net": "+3.3 V"},
    # encoder through connector J1: GPIO32 -> J1.1 -> enc.A, GPIO33 -> J1.2 -> enc.B, IO25 -> J1.3 -> enc.SW, GND -> J1.4 -> enc.C / SW2
    {"from": "mcu.GPIO32", "to": "j1.1"}, {"from": "j1.1", "to": "enc.A"},
    {"from": "mcu.GPIO33", "to": "j1.2"}, {"from": "j1.2", "to": "enc.B"},
    {"from": "mcu.IO25", "to": "j1.3"}, {"from": "j1.3", "to": "enc.SW"},
    {"from": "mcu.GND", "to": "j1.4"}, {"from": "j1.4", "to": "enc.C"}, {"from": "enc.C", "to": "enc.SW2"},
    {"from": "mcu.GPIO18", "to": "tft.SCL"}, {"from": "mcu.GPIO23", "to": "tft.SDA"}, {"from": "mcu.GPIO16", "to": "tft.DC"},
    {"from": "mcu.GPIO5", "to": "tft.CS"}, {"from": "mcu.GPIO17", "to": "tft.RST"}, {"from": "mcu.GPIO4", "to": "tft.BLK"},
    {"from": "mcu.3V3", "to": "tft.VCC"}, {"from": "mcu.GND", "to": "tft.GND"},
    {"from": "mcu.GPIO22", "to": "oled.SCL"}, {"from": "mcu.GPIO21", "to": "oled.SDA"},
    {"from": "mcu.3V3", "to": "oled.VCC"}, {"from": "mcu.GND", "to": "oled.GND"},
    {"from": "battery.+", "to": "ldo.IN", "net": "VBAT"}, {"from": "battery.-", "to": "ldo.GND", "net": "0 V"},
]


def model(parts=None):
    """{placement: part record}, the shape schemes.load_parts returns."""
    out = {}
    for p in (PARTS if parts is None else parts):
        for pl in p["placements"]:
            out[pl["name"]] = p
    return out


def conns(wires=None):
    return {"WIRES": WIRES if wires is None else wires, "HOSES": []}


def derive(parts=None, wires=None):
    import device            # imported here: a missing module fails each test, not the collection
    dev, problems = device.derive(model(parts), conns(wires))
    assert isinstance(problems, list) and all(isinstance(p, str) for p in problems), problems
    return dev, problems


def el(dev, eid):
    found = [e for e in dev["elements"] if e["id"] == eid]
    assert found, f"no element {eid!r} in {[e['id'] for e in dev['elements']]}"
    return found[0]


PIN = lambda n: {"board": "mcu", "pin": n}  # noqa: E731
GND, VCC = {"rail": "gnd"}, {"rail": "vcc"}


# ---------------------------------------------------------------- derive(): the projection

def test_the_full_model_derives_without_problems():
    dev, problems = derive()
    assert problems == []
    assert set(dev) >= {"boards", "elements"}


def test_the_mcu_is_a_board_named_by_its_placement_with_its_firmware():
    dev, _ = derive()
    assert dev["boards"] == [{"id": "mcu", "part": "esp32_devkit", "model": "esp32", "fw": "player"}]


def test_a_button_pin_comes_from_the_wire_and_the_other_contact_to_gnd_is_a_rail():
    """[1] btn_play: GPIO27 on contact 1, contact 2 wired to the MCU's GND port."""
    b = el(derive()[0], "btn_play")
    assert b["model"] == "button" and b["part"] == "tact_switch" and b["board"] == "mcu"
    assert b["ports"] == {"1": PIN(27), "2": GND}


def test_params_are_the_sim_field_minus_model():
    b = el(derive()[0], "btn_play")
    assert b["params"] == {"key": "Space", "bounce_ms": 2, "label": "Play"}
    t = el(derive()[0], "tft")
    assert t["params"] == {"w": 170, "h": 320, "x_off": 35, "y_off": 0, "glass": GLASS}


def test_every_placement_of_a_sim_part_is_its_own_element():
    """[1] one tact switch part, placed twice: btn_play on GPIO27, btn_next on GPIO14 (its contact 1 to a "0 V" net)."""
    dev, _ = derive()
    n = el(dev, "btn_next")
    assert n["part"] == "tact_switch"
    assert n["ports"] == {"1": GND, "2": PIN(14)}


def test_a_net_named_like_3v3_makes_a_vcc_rail():
    """[1] btn_hi's contact B reaches the LDO output through a wire with net "+3.3 V": pressing drives HIGH."""
    assert el(derive()[0], "btn_hi")["ports"] == {"A": PIN(26), "B": VCC}


def test_mcu_rail_ports_are_rails_and_io_names_are_pins():
    t = el(derive()[0], "tft")
    assert t["ports"] == {"SCL": PIN(18), "SDA": PIN(23), "DC": PIN(16), "CS": PIN(5), "RST": PIN(17), "BLK": PIN(4), "VCC": VCC, "GND": GND}


def test_pins_reach_through_a_connector_port_with_two_wires():
    """[1] GPIO32 -> J1.1 -> enc.A: one net. IO25 (the IO<n> spelling) -> J1.3 -> enc.SW. enc.SW2 shares enc.C's net (GND via J1.4)."""
    e = el(derive()[0], "enc")
    assert e["ports"] == {"A": PIN(32), "B": PIN(33), "C": GND, "SW": PIN(25), "SW2": GND}
    assert e["params"] == {"keys": ["ArrowLeft", "ArrowRight"], "push_key": "Enter", "detent": 4}


def test_the_ssd1306_on_i2c():
    o = el(derive()[0], "oled")
    assert o["model"] == "ssd1306"
    assert o["ports"] == {"SCL": PIN(22), "SDA": PIN(21), "VCC": VCC, "GND": GND}
    assert o["params"] == {"w": 128, "h": 64, "addr": 60}


def test_parts_without_sim_are_not_elements():
    dev, _ = derive()
    assert {e["id"] for e in dev["elements"]} == {"btn_play", "btn_next", "btn_hi", "enc", "tft", "oled"}


def test_an_optional_display_port_left_unwired_is_null_not_a_problem():
    """CS not wired = the display takes every byte on its bus; BLK not wired = always lit."""
    wires = [w for w in WIRES if w["to"] not in ("tft.CS", "tft.BLK")]
    dev, problems = derive(wires=wires)
    assert problems == []
    t = el(dev, "tft")
    assert t["ports"]["CS"] is None and t["ports"]["BLK"] is None


# ---------------------------------------------------------------- derive(): problems as sentences

def test_a_button_whose_contacts_reach_no_mcu_pin_is_a_problem_naming_it():
    wires = [w for w in WIRES if "btn_play" not in w["from"] + w["to"]] + [{"from": "btn_play.2", "to": "mcu.GND"}]
    _, problems = derive(wires=wires)
    hit = [p for p in problems if "btn_play" in p]
    assert hit, problems
    assert len(hit[0].split()) >= 4, f"a sentence, not a code: {hit[0]!r}"


def test_an_unwired_display_signal_port_is_a_problem_naming_the_port():
    _, problems = derive(wires=[w for w in WIRES if w["to"] != "tft.SCL"])
    assert any("tft" in p and "SCL" in p for p in problems), problems


def test_an_unwired_encoder_channel_is_a_problem():
    wires = [w for w in WIRES if w["to"] != "enc.B"]
    _, problems = derive(wires=wires)
    assert any(re.search(r"\benc\b", p) and re.search(r"\bB\b", p) for p in problems), problems


def test_an_unknown_model_name_is_a_problem_naming_it():
    parts = PARTS + [part("buzzer", ["+", "-"], {"model": "st9999"})]
    wires = WIRES + [{"from": "mcu.GPIO13", "to": "buzzer.+"}]
    _, problems = derive(parts, wires)
    assert any("st9999" in p for p in problems), problems


def test_an_mcu_without_firmware_is_a_problem():
    parts = [part("esp32_devkit", ESP_PORTS, {"model": "esp32"}, placements=["mcu"])] + PARTS[1:]
    _, problems = derive(parts)
    assert any("mcu" in p and "fw" in p for p in problems), problems


def test_sim_parts_without_any_mcu_are_a_problem():
    _, problems = derive(PARTS[1:], [w for w in WIRES if "mcu." not in w["from"] + w["to"]])
    assert problems, "elements with no board to run them"


def test_two_mcus_the_wiring_decides_the_board():
    """Two boards: an element wired to one of them belongs to it, no hint needed."""
    parts = PARTS + [part("esp32_devkit_b", ESP_PORTS, {"model": "esp32", "fw": "aux"}, placements=["mcu2"]),
                     part("btn_aux", ["1", "2"], {"model": "button"})]
    wires = WIRES + [{"from": "mcu2.GPIO27", "to": "btn_aux.1"}, {"from": "btn_aux.2", "to": "mcu2.GND"}]
    dev, problems = derive(parts, wires)
    assert problems == []
    assert {b["id"] for b in dev["boards"]} == {"mcu", "mcu2"}
    assert el(dev, "btn_aux")["board"] == "mcu2"
    assert el(dev, "btn_aux")["ports"]["1"] == {"board": "mcu2", "pin": 27}
    assert el(dev, "btn_play")["board"] == "mcu"


def test_two_mcus_and_an_element_on_both_without_a_board_hint_is_a_problem():
    """A button between a pin of each board: which firmware owns it? Without `board` in its sim field: a problem; with it: fine."""
    parts = PARTS + [part("esp32_devkit_b", ESP_PORTS, {"model": "esp32", "fw": "aux"}, placements=["mcu2"]),
                     part("btn_x", ["1", "2"], {"model": "button"})]
    wires = WIRES + [{"from": "mcu.GPIO12", "to": "btn_x.1"}, {"from": "btn_x.2", "to": "mcu2.GPIO14"}]
    _, problems = derive(parts, wires)
    assert any("btn_x" in p and "board" in p for p in problems), problems
    parts[-1] = part("btn_x", ["1", "2"], {"model": "button", "board": "mcu2"})
    dev, problems = derive(parts, wires)
    assert problems == []
    assert el(dev, "btn_x")["board"] == "mcu2"


def test_a_model_without_sim_parts_gives_no_device_and_no_problem():
    parts = [part("psu", ["V+", "V-"]), part("lamp", ["L", "N"])]
    dev, problems = derive(parts, [{"from": "psu.V+", "to": "lamp.L"}, {"from": "psu.V-", "to": "lamp.N"}])
    assert problems == []
    assert not dev or (dev.get("boards") == [] and dev.get("elements") == [])


# ---------------------------------------------------------------- device.py <root> and check.py rule «device»

def make_project(tmp_path, parts=None, wires=None):
    root = tmp_path / "proj"
    root.mkdir()
    write(root, "mechanics/cad/params.py", "W = 1\n")
    write(root, "mechanics/cad/connections.py", f"WIRES = {(WIRES if wires is None else wires)!r}\nHOSES = []\n")
    write(root, "build/model/parts.json", json.dumps({"parts": PARTS if parts is None else parts}, ensure_ascii=False))
    t = time.time() + 5                     # the model build ran after the sources were written (as after a turn)
    os.utime(root / "build/model/parts.json", (t, t))
    return root


def run_device(root):
    return subprocess.run([sys.executable, str(DEVICE), str(root)], capture_output=True, text=True, timeout=120)


def device_fails(root):
    r = subprocess.run([sys.executable, str(CHECK), "--no-validate", "--json", str(root)], capture_output=True, text=True, timeout=300)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    return [f for f in out["fails"] if f["rule"] == "device"], r.stderr


def test_device_py_writes_build_model_device_json(tmp_path):
    root = make_project(tmp_path)
    r = run_device(root)
    assert r.returncode == 0, r.stdout + r.stderr
    f = root / "build/model/device.json"
    assert f.is_file()
    dev = json.loads(f.read_text(encoding="utf-8"))
    import device
    assert dev == device.derive(model(), conns())[0]
    assert el(dev, "btn_play")["ports"] == {"1": PIN(27), "2": GND}


def test_device_py_prints_the_problems_and_exits_1(tmp_path):
    root = make_project(tmp_path, wires=[w for w in WIRES if w["to"] != "tft.SCL"])
    r = run_device(root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "SCL" in r.stdout + r.stderr


def test_device_py_leaves_a_project_without_sim_parts_untouched(tmp_path):
    parts = [part("psu", ["V+", "V-"]), part("lamp", ["L", "N"])]
    root = make_project(tmp_path, parts=parts, wires=[{"from": "psu.V+", "to": "lamp.L"}, {"from": "psu.V-", "to": "lamp.N"}])
    r = run_device(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (root / "build/model/device.json").exists()


def test_check_rule_device_fails_with_the_problem_sentences(tmp_path):
    """[1] check.py, rule «device»: an unknown model name and an unwired display clock, both named."""
    parts = PARTS + [part("buzzer", ["+", "-"], {"model": "st9999"})]
    wires = [w for w in WIRES if w["to"] != "tft.SCL"] + [{"from": "mcu.GPIO13", "to": "buzzer.+"}]
    fails, log = device_fails(make_project(tmp_path, parts, wires))
    assert fails, log[-2000:]
    text = " ".join(f["detail"] for f in fails)
    assert "st9999" in text and "SCL" in text, text


def test_check_rule_device_passes_a_correct_device(tmp_path):
    fails, log = device_fails(make_project(tmp_path))
    assert fails == [], fails
    assert re.search(r"^PASS\s+device\b", log, re.M), f"the rule reports itself as a PASS line in the check log:\n{log[-1500:]}"


def test_check_rule_device_is_quiet_for_a_project_without_sim_parts(tmp_path):
    parts = [part("psu", ["V+", "V-"]), part("lamp", ["L", "N"])]
    fails, _ = device_fails(make_project(tmp_path, parts, [{"from": "psu.V+", "to": "lamp.L"}, {"from": "psu.V-", "to": "lamp.N"}]))
    assert fails == []


# ---------------------------------------------------------------- the model build writes device.json (build123d)

def _has_build123d():
    try:
        import build123d  # noqa: F401
        return True
    except Exception:
        return False


needs_b3d = pytest.mark.skipif(not _has_build123d(), reason="build123d missing: run with a build123d==0.13.0 venv (~/.cache/b3d-venv)")

PART_SRC = {
    "board": '''
from build123d import Box
def build():
    return {"name": "board", "shape": Box(50, 28, 4), "color": "#225522", "process": "bought", "kind": "electrical",
            "ports": {"GPIO27": "", "GND": "", "3V3": ""}, "sim": {"model": "esp32", "fw": "player"}}
''',
    "tact": '''
from build123d import Box
def build():
    return {"name": "tact", "shape": Box(6, 6, 5), "color": "#333333", "process": "bought", "kind": "electrical",
            "ports": {"1": "", "2": ""}, "sim": {"model": "button", "key": "Space"}}
''',
}


def model_project(tmp_path, with_sim=True):
    import helpers as H
    root = tmp_path / "mproj"
    cad = H.write_base(root)
    srcs = dict(PART_SRC)
    if not with_sim:
        srcs = {k: v.replace(', "sim": {"model": "esp32", "fw": "player"}', "").replace(', "sim": {"model": "button", "key": "Space"}', "")
                for k, v in srcs.items()}
    H.write_node(root, "device", srcs, [("board", (0, 0, 0)), ("tact", (40, 0, 0))])
    (cad / "assemble.py").write_text(textwrap.dedent("""
        import os, sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from parts import device
        out = device.build()
    """))
    (cad / "connections.py").write_text(
        'WIRES = [{"from": "board.GPIO27", "to": "tact.1"}, {"from": "tact.2", "to": "board.GND", "net": "GND"}]\nHOSES = []\n')
    return root


@needs_b3d
def test_the_model_build_writes_device_json(tmp_path):
    """[1] after `cad_incremental.py --model` (the model build) build/model/device.json is there, derived from the part files' sim
    fields and connections.py."""
    import helpers as H
    root = model_project(tmp_path)
    H.run_model(root).ok()
    f = root / "build/model/device.json"
    assert f.is_file(), "the model build did not write build/model/device.json"
    dev = json.loads(f.read_text(encoding="utf-8"))
    assert dev["boards"] == [{"id": "board", "part": "board", "model": "esp32", "fw": "player"}]
    assert el(dev, "tact")["ports"] == {"1": {"board": "board", "pin": 27}, "2": GND}


@needs_b3d
def test_the_model_build_of_a_project_without_sim_parts_writes_no_device_json(tmp_path):
    import helpers as H
    root = model_project(tmp_path, with_sim=False)
    H.run_model(root).ok()
    assert (root / "build/model/parts.json").is_file()
    assert not (root / "build/model/device.json").exists()
