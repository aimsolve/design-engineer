"""Issue #113 «Wires in 3D» on the framework's own example, examples/wired-box (a divider with a slot, a board, a motor, a
jack, a carriage with a limit switch that slides 25 mm over the cycle): the shipped example passes the harness check, and
the cases the first review found are caught:
  - a wire that stands still across the carriage's stroke: clear at rest, hit at the end of the stroke -> clash at a time of the cycle;
  - bad numbers in HARNESS or on a wire are `route` problems, never a traceback, and the electric scheme still draws.
Slow (a model build): marked `slow`.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import helpers as H

pytestmark = pytest.mark.slow

SCRIPTS = H.SCRIPTS
EXAMPLE = SCRIPTS.parents[2] / "examples" / "wired-box"
CYCLE = SCRIPTS.parents[1] / "web-viewer" / "scripts" / "cycle_from_params.py"


def run(args, cwd):
    r = subprocess.run([sys.executable, *map(str, args)], cwd=cwd, capture_output=True, text=True, timeout=900)
    return r.returncode, r.stdout + r.stderr


@pytest.fixture(scope="module")
def box(tmp_path_factory):
    root = tmp_path_factory.mktemp("wired") / "wired-box"
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns("build", "public", ".engineer"))
    rc, out = run([SCRIPTS / "cad_incremental.py", "--model", "assemble.py"], root / "mechanics" / "cad")
    assert rc == 0, out[-2000:]
    rc, out = run([CYCLE, "--params", "mechanics/cad/params.py", "--out", "build/motion.json"], root)
    assert rc == 0, out[-2000:]
    return root


def with_connections(root, tmp, edit):
    """a copy of the built example whose connections.py is edited by `edit(src) -> src`, dated as the model (built after)"""
    dst = tmp / "copy"
    shutil.copytree(root, dst)
    f = dst / "mechanics" / "cad" / "connections.py"
    f.write_text(edit(f.read_text()))
    st = (dst / "build" / "model" / "parts.json").stat()
    import os
    os.utime(f, (st.st_atime - 5, st.st_mtime - 5))
    return dst


def harness(root):
    rc, out = run([SCRIPTS / "harness.py", ".", "--motion", "build/motion.json"], root)
    rep = json.loads((root / "build" / "harness" / "report.json").read_text())
    return rc, out, rep


def test_the_example_passes(box):
    rc, out, rep = harness(box)
    assert rc == 0 and not rep["problems"], out
    assert len([w for w in rep["wires"] if not w.get("unrouted")]) == 8, rep["wires"]
    rc, out = run([SCRIPTS / "check.py", ".", "--no-validate"], box)
    assert re.search(r"^PASS\s+harness", out, re.M), out


def test_a_still_wire_across_the_carriage_stroke_clashes_over_the_cycle(box, tmp_path):
    # the jack's wires go over everything and dip, straight down, into the carriage's path past its end at rest
    # (the carriage stands at x 20..40 and slides to 45..65 over the cycle)
    def edit(s):
        s = s.replace('"via": ["pwr"]}', '"via": ["pwr", "above", "dip", "above", "pwr"]}')
        return s + '\nWAYPOINTS["above"] = (55.0, 25.0, 44.0)\nWAYPOINTS["dip"] = (55.0, 25.0, 7.0)\n'
    root = with_connections(box, tmp_path, edit)
    rc, out, rep = harness(root)
    hits = [p for p in rep["problems"] if p["rule"] in ("clash", "gap") and "carriage" in p["text"]]
    assert rc == 1 and hits, out
    assert all("at t " in p["text"] for p in hits), f"clear at rest, hit over the cycle: {hits}"


def test_a_model_without_port_locations_and_a_broken_connections_py_gets_nothing_of_wires(box, tmp_path):
    """rule «schemes» reports the broken connections.py; the harness stays out of it (no report, no wires layer)"""
    root = with_connections(box, tmp_path, lambda s: s + "\nraise RuntimeError('broken on purpose')\n")
    pj = root / "build" / "model" / "parts.json"
    m = json.loads(pj.read_text())
    for p in m["parts"]:
        p.pop("ports_at", None)
    pj.write_text(json.dumps(m))
    sys.path.insert(0, str(SCRIPTS))
    import harness
    assert harness.run(root) is None
    assert not (root / "build" / "harness" / "report.json").exists()


@pytest.mark.parametrize("edit, word", [
    (lambda s: s + '\nHARNESS = {"bend": "soft"}\n', "bend"),
    (lambda s: s + '\nHARNESS = {"slack": -0.5}\n', "slack"),
    (lambda s: s.replace('"loop": 30}', '"loop": "x"}', 1), "loop"),
    (lambda s: s.replace('"spec": "0.5 mm²", "via": ["pwr"]}', '"spec": "0.5 mm²", "via": ["pwr"], "od": 0}', 1), "od"),
])
def test_bad_numbers_are_route_problems_not_a_crash(box, tmp_path, edit, word):
    root = with_connections(box, tmp_path, edit)
    rc, out, rep = harness(root)
    assert rc == 1 and "Traceback" not in out, out
    assert any(p["rule"] == "route" and word in p["text"] for p in rep["problems"]), rep["problems"]
    rc, out = run([SCRIPTS / "check.py", ".", "--no-validate"], root)
    assert "Traceback" not in out and re.search(r"^FAIL\s+harness", out, re.M), out
    rc, out = run([SCRIPTS / "schemes.py", "electric", "build/kit/electric.json"], root)
    assert rc == 0 and (root / "build" / "kit" / "schemes" / "electric.pdf").is_file(), out
