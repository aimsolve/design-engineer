"""Regressions of the first independent review of device.py: ambiguous nets and shorts are problems, an unknown
SIM key is a problem, the result does not depend on hash order, a project without sim parts never runs connections.py, a device with
problems leaves no stale device.json."""
import json
import os
import subprocess
import sys
from pathlib import Path

import test_device_model as T

SCRIPTS = Path(__file__).resolve().parent.parent


def test_a_net_on_two_pins_of_one_board_is_a_problem():
    wires = T.WIRES + [{"from": "mcu.GPIO12", "to": "btn_play.1"}]
    _, problems = T.derive(wires=wires)
    assert any("btn_play.1" in p and "GPIO12" in p and "GPIO27" in p for p in problems), problems


def test_a_net_on_gnd_and_3v3_is_a_problem():
    wires = T.WIRES + [{"from": "btn_play.2", "to": "mcu.3V3"}]
    _, problems = T.derive(wires=wires)
    assert any("btn_play.2" in p and "GND" in p and "3V3" in p for p in problems), problems


def test_an_unknown_sim_key_is_a_problem():
    import device
    _, problems = device.derive(T.model(), {"WIRES": T.WIRES, "HOSES": [], "SIM": {"btn_ply": {"key": "Space"}}})
    assert any("btn_ply" in p for p in problems), problems


def test_sim_overrides_reach_the_params():
    import device
    dev, problems = device.derive(T.model(), {"WIRES": T.WIRES, "HOSES": [], "SIM": {"btn_next": {"key": "KeyD", "label": "Next"}}})
    assert problems == []
    assert T.el(dev, "btn_next")["params"] == {"key": "KeyD", "label": "Next", "bounce_ms": 2}


def test_device_json_does_not_depend_on_hash_order(tmp_path):
    root = T.make_project(tmp_path)
    outs = []
    for seed in ("1", "2", "3"):
        r = subprocess.run([sys.executable, str(SCRIPTS / "device.py"), str(root)], capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": seed}, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        outs.append((root / "build/model/device.json").read_text(encoding="utf-8"))
    assert outs[0] == outs[1] == outs[2]


def test_a_project_without_sim_parts_never_runs_connections_py(tmp_path):
    parts = [T.part("psu", ["V+", "V-"])]
    root = T.make_project(tmp_path, parts=parts, wires=[])
    (root / "mechanics/cad/connections.py").write_text("raise SystemExit('connections.py ran')\n", encoding="utf-8")
    import device
    assert device.write(root) == []


def test_problems_remove_a_stale_device_json(tmp_path):
    root = T.make_project(tmp_path)
    assert T.run_device(root).returncode == 0
    assert (root / "build/model/device.json").is_file()
    T.write(root, "mechanics/cad/connections.py", f"WIRES = {[w for w in T.WIRES if w['to'] != 'tft.SCL']!r}\nHOSES = []\n")
    assert T.run_device(root).returncode == 1
    assert not (root / "build/model/device.json").exists(), "a device with problems keeps no device.json of an older wiring"


def test_an_avr_board_takes_d_and_a_ports():
    """review 2: a Nano is a board with D<n> / A<n> ports (A0 = 14)."""
    parts = [T.part("nano", ["D2", "A1", "GND", "5V"], {"model": "avr", "fw": "nano"}, placements=["nano"]),
             T.part("tact", ["1", "2"], {"model": "button"}, placements=["b1", "b2"])]
    wires = [{"from": "nano.D2", "to": "b1.1"}, {"from": "b1.2", "to": "nano.GND"}, {"from": "nano.A1", "to": "b2.1"}, {"from": "b2.2", "to": "nano.5V"}]
    dev, problems = T.derive(parts, wires)
    assert problems == []
    assert T.el(dev, "b1")["ports"] == {"1": {"board": "nano", "pin": 2}, "2": {"rail": "gnd"}}
    assert T.el(dev, "b2")["ports"] == {"1": {"board": "nano", "pin": 15}, "2": {"rail": "vcc"}}


def test_a_short_is_reported_once():
    wires = T.WIRES + [{"from": "mcu.GPIO12", "to": "tft.CS"}]
    _, problems = T.derive(wires=wires)
    assert len(problems) == len(set(problems)), problems
