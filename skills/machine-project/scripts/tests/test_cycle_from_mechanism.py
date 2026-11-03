"""Issue #17: the viewer's cycle block written from a solved mechanism, not by hand.

Done-when map:
  DW6  motion.json is generated: the machine states the loops, which viewer group follows which joint and
       when the drive runs, and the `cycle` block falls out of the solve
  DW7  every group runs 0 -> 1 over a forward run and 1 -> 0 back, in the period the machine states
  DW8  what the machine states wrong is refused by name: a group that never moves, a parent that is not a group
  DW9  only `cycle` is touched; everything else already in motion.json is kept
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from samples_i18n import SAMPLES

RU = SAMPLES["ru"]
SCRIPTS = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS / "cycle_from_mechanism.py"

MACHINE = '''
import sys
sys.path.insert(0, %r)
from linkage import Mechanism

PERIOD_S = 2.0
RUNS = [(0.0, 1.0, True), (1.2, 2.0, False)]
FRAMES = 4

def mechanism():
    m = Mechanism()
    m.turn("crank", on="ground", at=(0.0, 0.0), start=60.0)
    m.turn("rocker", on="ground", at=(300.0, 0.0), start=100.0)
    m.meet(("crank", (100.0, 0.0)), ("rocker", (180.0, 0.0)), distance=260.0)
    return m

def poses(frames=41):
    m = mechanism()
    m.assemble("crank")
    return m.sweep("crank", 60.0, 150.0, frames - 1)

def groups(solved):
    return {
        "arm":  {"kind": "rotary", "axis": "y", "pivot_mm": [0.0, 0.0, 0.0], "read": lambda f: f["crank"]},
        "hand": {"kind": "rotary", "axis": "y", "pivot_mm": [300.0, 0.0, 0.0], "parent": "arm",
                 "read": lambda f: f["rocker"]},
    }
''' % str(SCRIPTS)


def write(tmp_path, body=MACHINE):
    path = tmp_path / "mechanism.py"
    path.write_text(body)
    return path


def run(tmp_path, body=MACHINE, out=None):
    out = out or tmp_path / "motion.json"
    done = subprocess.run([sys.executable, str(SCRIPT), "--mechanism", str(write(tmp_path, body)), "--out", str(out)],
                          capture_output=True, text=True)
    return done, out


def test_the_cycle_block_comes_out_of_the_solve(tmp_path):
    done, out = run(tmp_path)
    assert done.returncode == 0, done.stderr
    cycle = json.loads(out.read_text())["cycle"]
    assert cycle["period_s"] == 2.0 and cycle["loop"] is True
    assert set(cycle["groups"]) == {"arm", "hand"}
    assert abs(cycle["groups"]["arm"]["travel_deg"] - 90.0) < 1e-6      # the crank's own 60 -> 150
    assert cycle["groups"]["hand"]["parent"] == "arm"


def test_each_group_runs_nought_to_one_and_back(tmp_path):
    done, out = run(tmp_path)
    assert done.returncode == 0, done.stderr
    for name, group in json.loads(out.read_text())["cycle"]["groups"].items():
        segments = group["segments"]
        assert len(segments) == 8                                       # two runs of FRAMES
        assert segments[0][:2] == [0.0, 0.25] and segments[3][:2] == [0.75, 1.0]
        assert segments[4][:2] == [1.2, 1.4] and segments[7][:2] == [1.8, 2.0]
        assert abs(segments[0][2]) < 1e-9 and abs(segments[3][3] - 1.0) < 1e-9, name
        assert abs(segments[4][2] - 1.0) < 1e-9 and abs(segments[7][3]) < 1e-9, name
        for before, after in zip(segments, segments[1:]):               # continuous, no jumps
            if before[1] == after[0]:
                assert abs(before[3] - after[2]) < 1e-9, name


def test_a_group_that_never_moves_is_refused_by_name(tmp_path):
    body = MACHINE.replace('"read": lambda f: f["rocker"]', '"read": lambda f: 7.0')
    done, _ = run(tmp_path, body)
    assert done.returncode != 0 and "hand" in done.stderr


def test_riding_on_something_that_is_not_a_group_is_refused_by_name(tmp_path):
    body = MACHINE.replace('"parent": "arm"', '"parent": "trolley"')
    done, _ = run(tmp_path, body)
    assert done.returncode != 0 and "trolley" in done.stderr


def test_what_was_already_in_motion_json_is_kept(tmp_path):
    out = tmp_path / "motion.json"
    out.write_text(json.dumps({"actuators": {"lift": {"kind": "linear"}}, "cycle": {"stale": True}}))
    done, _ = run(tmp_path, out=out)
    assert done.returncode == 0, done.stderr
    whole = json.loads(out.read_text())
    assert whole["actuators"] == {"lift": {"kind": "linear"}}
    assert "stale" not in whole["cycle"]


def test_a_mechanism_missing_part_of_the_contract_says_which(tmp_path):
    done, _ = run(tmp_path, MACHINE.replace("FRAMES = 4", ""))
    assert done.returncode != 0 and "FRAMES" in done.stderr


# --- what the machine states wrong, refused before anything is written -------------------

def test_a_run_outside_the_period_is_refused(tmp_path):
    done, out = run(tmp_path, MACHINE.replace("RUNS = [(0.0, 1.0, True), (1.2, 2.0, False)]",
                                              "RUNS = [(0.0, 5.0, True)]"))
    assert done.returncode != 0 and "5.0" in done.stderr and not out.exists()


def test_a_run_that_does_not_go_forwards_is_refused(tmp_path):
    done, _ = run(tmp_path, MACHINE.replace("RUNS = [(0.0, 1.0, True), (1.2, 2.0, False)]",
                                            "RUNS = [(1.0, 1.0, True)]"))
    assert done.returncode != 0 and "forwards" in done.stderr


def test_two_runs_over_the_same_time_are_refused(tmp_path):
    done, _ = run(tmp_path, MACHINE.replace("RUNS = [(0.0, 1.0, True), (1.2, 2.0, False)]",
                                            "RUNS = [(0.0, 1.5, True), (1.0, 2.0, False)]"))
    assert done.returncode != 0 and "same time" in done.stderr


def test_a_group_that_comes_back_to_its_start_still_gets_a_usable_travel(tmp_path):
    """A rocker on a crank driven a whole turn ends where it began and swings tens of degrees.

    Measured end to end its travel is nought and it would be called still; measured from the
    model's own pose to the furthest the group reaches, it is the swing, and every fraction
    stays inside -1..1 with frame zero at nought, which is what the viewer needs.
    """
    body = (MACHINE.replace('m.turn("crank", on="ground", at=(0.0, 0.0), start=60.0)',
                            'm.turn("crank", on="ground", at=(0.0, 0.0), start=0.0)')
                   .replace('return m.sweep("crank", 60.0, 150.0, frames - 1)',
                            'return m.sweep("crank", 0.0, 360.0, frames - 1)'))
    done, out = run(tmp_path, body)
    assert done.returncode == 0, done.stderr
    hand = json.loads(out.read_text())["cycle"]["groups"]["hand"]
    assert abs(hand["travel_deg"]) > 10.0
    for segment in hand["segments"]:
        assert -1.0 - 1e-9 <= segment[2] <= 1.0 + 1e-9 and -1.0 - 1e-9 <= segment[3] <= 1.0 + 1e-9
    assert abs(hand["segments"][0][2]) < 1e-9


#: one key of the 'hand' group taken away at a time. `axis` and `pivot_mm` are not optional:
#: the viewer dereferences both as it mounts the cycle, so a group without them is either a
#: page where nothing moves at all or a part turning about the model's origin.
DROPPED = {
    "kind": ('"kind": "rotary", "axis": "y", "pivot_mm": [300.0, 0.0, 0.0]',
             '"axis": "y", "pivot_mm": [300.0, 0.0, 0.0]'),
    "axis": ('"axis": "y", "pivot_mm": [300.0, 0.0, 0.0]', '"pivot_mm": [300.0, 0.0, 0.0]'),
    "pivot_mm": ('"pivot_mm": [300.0, 0.0, 0.0], ', ""),
    "read": ('"read": lambda f: f["rocker"]', '"axis2": "y"'),
}


@pytest.mark.parametrize("missing", sorted(DROPPED))
def test_a_group_missing_part_of_its_section_is_refused_by_name(tmp_path, missing):
    was, now = DROPPED[missing]
    assert was in MACHINE
    done, out = run(tmp_path, MACHINE.replace(was, now))
    assert done.returncode != 0 and not out.exists()
    assert done.stderr.strip().count("\n") == 0
    assert "hand" in done.stderr and missing in done.stderr


def test_an_axis_the_viewer_cannot_name_is_refused(tmp_path):
    done, _ = run(tmp_path, MACHINE.replace('"axis": "y", "pivot_mm": [300.0', '"axis": "Y2", "pivot_mm": [300.0'))
    assert done.returncode != 0 and "Y2" in done.stderr and "hand" in done.stderr


def test_naming_no_run_at_all_is_refused(tmp_path):
    done, out = run(tmp_path, MACHINE.replace("RUNS = [(0.0, 1.0, True), (1.2, 2.0, False)]", "RUNS = []"))
    assert done.returncode != 0 and "RUNS" in done.stderr and not out.exists()


@pytest.mark.parametrize("period", ['"two"', "0.0", "-2.0"])
def test_a_period_that_is_not_some_seconds_is_refused(tmp_path, period):
    done, _ = run(tmp_path, MACHINE.replace("PERIOD_S = 2.0", "PERIOD_S = %s" % period))
    assert done.returncode != 0 and "PERIOD_S" in done.stderr


def test_a_kind_the_viewer_does_not_know_is_refused(tmp_path):
    done, _ = run(tmp_path, MACHINE.replace('"hand": {"kind": "rotary"', '"hand": {"kind": "wobbly"'))
    assert done.returncode != 0 and "wobbly" in done.stderr


def test_naming_no_group_at_all_is_refused_and_writes_nothing(tmp_path):
    body = MACHINE[:MACHINE.index("def groups(")] + "def groups(solved):\n    return {}\n"
    done, out = run(tmp_path, body)
    assert done.returncode != 0 and not out.exists()


def test_no_segments_at_all_is_refused(tmp_path):
    done, _ = run(tmp_path, MACHINE.replace("FRAMES = 4", "FRAMES = 0"))
    assert done.returncode != 0 and "FRAMES" in done.stderr


def test_text_already_in_motion_json_keeps_its_own_letters(tmp_path):
    """This script and cycle_from_params.py rewrite the same file; one must not re-escape it."""
    out = tmp_path / "motion.json"
    out.write_text(json.dumps({"note": RU["suction_frame"]}, ensure_ascii=False), encoding="utf-8")
    done, _ = run(tmp_path, out=out)
    assert done.returncode == 0, done.stderr
    assert RU["suction_frame"] in out.read_text(encoding="utf-8")
