"""Issue #17: the `mechanism.py` template is a working machine, not prose.

Done-when map:
  DW10  a machine copying templates/mechanism.py, writing only its own params.py, gets a solved cycle:
        the template states a real loop, keeps the contract cycle_from_mechanism.py reads, and runs
The stub params.py below is the smallest a machine needs for the template's example loop (a cylinder
swinging a lever). If the template's names drift from the contract, this fails rather than the first
machine that copies it.
"""
import json
import math
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
TEMPLATE = SCRIPTS.parent / "templates" / "mechanism.py"

PARAMS = """
PIVOT_Z_MM = 1100.0
PICK_TIP_DEG = 72.1
CYL_PIVOT_X_MM = -348.6592334896662
CYL_PIVOT_Z_MM = 1100.0
CYL_RETRACTED_MM = 306.0
CYL_ARM_R_MM = 87.59417162345012
CYL_STROKE_MM = 100.0
L_LEG_ANGLE_DEG = -53.55
"""


def machine(tmp_path):
    """A project tree the template expects: mechanics/cad/params.py and mechanics/kinematics/."""
    cad = tmp_path / "mechanics" / "cad"
    kinematics = tmp_path / "mechanics" / "kinematics"
    cad.mkdir(parents=True)
    kinematics.mkdir(parents=True)
    (cad / "params.py").write_text(PARAMS)
    copied = kinematics / "mechanism.py"
    copied.write_text(TEMPLATE.read_text())
    return copied


def test_the_template_solves_a_cycle_as_copied(tmp_path):
    copied = machine(tmp_path)
    done = subprocess.run([sys.executable, str(copied)], capture_output=True, text=True,
                          env={"PYTHONPATH": str(SCRIPTS), "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr
    moved = {line.split()[0]: (float(line.split()[1]), float(line.split()[3])) for line in done.stdout.splitlines()}
    assert set(moved) == {"lever", "cyl_body", "cyl_rod"}
    assert abs((moved["cyl_rod"][1] - moved["cyl_rod"][0]) - 100.0) < 1e-6      # the stroke it was driven by
    assert abs(moved["lever"][1] - moved["lever"][0]) > 10.0                    # and the lever swung


def test_the_template_writes_the_cycle_block(tmp_path):
    copied = machine(tmp_path)
    out = tmp_path / "build" / "motion.json"
    done = subprocess.run([sys.executable, str(SCRIPTS / "cycle_from_mechanism.py"),
                           "--mechanism", str(copied), "--out", str(out)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    cycle = json.loads(out.read_text())["cycle"]
    assert cycle["period_s"] == 3.0
    assert set(cycle["groups"]) == {"lever", "cylbody", "cylrod"}
    assert cycle["groups"]["cylrod"]["parent"] == "cylbody"
    for group in cycle["groups"].values():
        assert len(group["segments"]) == 48
        assert abs(group["segments"][0][2]) < 1e-9 and abs(group["segments"][23][3] - 1.0) < 1e-9

    # the viewer turns or slides about these and takes no default for either
    assert cycle["groups"]["lever"]["axis"] == "y"
    assert cycle["groups"]["lever"]["pivot_mm"] == [0.0, 0.0, 1100.0]
    assert cycle["groups"]["cylbody"]["pivot_mm"][1] == 0.0
    rod = cycle["groups"]["cylrod"]["axis"]
    assert len(rod) == 3 and all(math.isfinite(v) for v in rod)
    assert abs(math.hypot(*rod) - 1.0) < 1e-9                                  # a direction, not a length
