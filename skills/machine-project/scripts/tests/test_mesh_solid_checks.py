"""Issue #78 «mesh parts», the geometric checks: clearance.run (the build's overlap check) and fit_checks.fit_report
(`fastener fit`, `docked parts clash`) measure a mesh part like a STEP part instead of skipping it as "unmeasured".

Done-when served here (numbers of the ticket):
  DW7  clearance: a mesh part crossing a STEP part by a known volume is a clash with that volume (5 %); touching is no clash;
       mesh vs mesh is measured too
  DW8  fit checks: a screw docked into a hole joint of a mesh part gets the verdicts it gets with a STEP host; a STEP part
       docked onto a mesh part and sunk into it FAILs `docked parts clash` with its volume; a touching docked pair passes

The project (node `frame`, built once per run by `cad_incremental.py --model`; every part docked by joints, base the root):

    base    STEP Box 300 x 100 x 10 (corner at the origin), every seat on its top face (z = 10)
    mcube   MESH cube 10 (centred in X/Y, bottom at 0), placed 4 times:  mbox at (30, 50)  mtouch at (60, 50)
                                                                         mm_a at (100, 50)  mm_b at (104, 52)
    scube   STEP cube 10, the same box, placed:  sbox at (36, 55)  stouch at (70, 50)   and peg_* / pad_* below
        mbox x[25,35] y[45,55] / sbox x[31,41] y[50,60], z[10,20] both: 4 x 5 x 10 = 200 mm3          -> clash (mesh vs STEP)
        mtouch x[55,65] / stouch x[65,75]: they share the face x = 65                                    -> no clash
        mm_a x[95,105] y[45,55] / mm_b x[99,109] y[47,57]: 6 x 8 x 10 = 480 mm3                         -> clash (mesh vs mesh)
    mplate  MESH plate 140 x 40 x 10 (meshpart.solid of the build123d plate), docked at (150, 5, 10)
    splate  the SAME plate as a STEP part, docked at (150, 55, 10): the control host
        both draw, in own coordinates: a 5.2 through hole at (20, 20) (joint hole_52), a 4.6 through hole at (60, 20)
        (hole_46), a blind 4.2 tap-drill pocket 6.5 deep at (100, 20) (seat_blind), all joints on the top face (z = 10, Z
        out); peg_seat at (125, 10, 8), 2 mm BELOW the top face; pad_seat at (125, 30, 10) on it
    screws  templates/screw.py, docked by `head`: M5x8 into hole_52 (scr_m52 / scr_s52), M5x8 into hole_46 (scr_m46 /
            scr_s46), M5x12 into seat_blind (scr_mblind / scr_sblind)
    peg_m / peg_s   scube docked on peg_seat: sunk 2 mm into the plate, 10 x 10 x 2 = 200 mm3          -> docked parts clash
    pad_m / pad_s   scube docked on pad_seat: on the face                                               -> touching, passes

    python -m pytest skills/machine-project/scripts/tests/test_mesh_solid_checks.py -n 8 -q -W ignore     (from agent/)
"""
import json
import sys

import pytest

import helpers as H
import meshsolid_proj as MP

PLATE_HELPER = '''
from build123d import Align, Box, Cylinder, Pos

T = 10.0
POCKET = 6.5
HOLES = {"hole_52": (20, 20, 5.2), "hole_46": (60, 20, 4.6)}
JOINTS = {"hole_52": (20, 20, T), "hole_46": (60, 20, T), "seat_blind": (100, 20, T),
          "peg_seat": (125, 10, T - 2), "pad_seat": (125, 30, T), "bottom": (0, 0, 0)}


def solid():
    s = Box(140, 40, T, align=(Align.MIN, Align.MIN, Align.MIN))
    for x, y, d in HOLES.values():
        s -= Pos(x, y, T / 2) * Cylinder(d / 2, 3 * T)
    s -= Pos(100, 20, T - POCKET / 2) * Cylinder(4.2 / 2, POCKET)
    return s
'''

MPLATE = '''
import meshpart
from build123d import Location, RigidJoint
from geom import P
from . import _plate


def build():
    m = meshpart.Mesh(meshpart.solid(_plate.solid()))
    for label, xyz in _plate.JOINTS.items():
        RigidJoint(label, m, Location(xyz))
    return P("mplate", m, "#dddddd", process="print", material="PETG")
'''

SPLATE = '''
from build123d import Location, RigidJoint
from geom import P
from . import _plate


def build():
    s = _plate.solid()
    for label, xyz in _plate.JOINTS.items():
        RigidJoint(label, s, Location(xyz))
    return P("splate", s, "#999999", process="mill", material="AL6060")
'''

BASE = '''
from build123d import Align, Box, Location, RigidJoint
from geom import P

SEATS = {"mbox": (30, 50), "mtouch": (60, 50), "mm_a": (100, 50), "mm_b": (104, 52), "sbox": (36, 55), "stouch": (70, 50),
         "mplate": (150, 5), "splate": (150, 55)}


def build():
    s = Box(300, 100, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    for name, (x, y) in SEATS.items():
        RigidJoint(name + "_seat", s, Location((x, y, 10)))
    return P("base", s, "#888888", process="mill", material="S235JR")
'''

MCUBE = '''
import meshpart
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    m = meshpart.Mesh(meshpart.solid(Box(10, 10, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))))
    RigidJoint("bottom", m, Location((0, 0, 0)))
    return P("mcube", m, "#dd8800", process="print", material="PETG")
'''

SCUBE = '''
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    s = Box(10, 10, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    RigidJoint("bottom", s, Location((0, 0, 0)))
    return P("scube", s, "#4444aa", process="mill", material="AL6060")
'''

FRAME = '''
from build123d import Pos
from geom import place
from . import base, mcube, mplate, scube, screw, splate


def build():
    b = base.build()
    out = [place(b, Pos(0, 0, 0))]

    def dock(host, at, part, by="bottom", name=None):
        host["shape"].joints[at].connect_to(part["shape"].joints[by])
        out.append(place(part, name=name))
        return part

    for n in ("mbox", "mtouch", "mm_a", "mm_b"):
        dock(b, n + "_seat", mcube.build(), name=n)
    for n in ("sbox", "stouch"):
        dock(b, n + "_seat", scube.build(), name=n)
    for tag, plate in (("m", mplate), ("s", splate)):
        p = dock(b, ("mplate" if tag == "m" else "splate") + "_seat", plate.build())
        dock(p, "hole_52", screw.build("M5", 8, "DIN 912"), by="head", name=f"scr_{tag}52")
        dock(p, "hole_46", screw.build("M5", 8, "DIN 912"), by="head", name=f"scr_{tag}46")
        dock(p, "seat_blind", screw.build("M5", 12, "DIN 912"), by="head", name=f"scr_{tag}blind")
        dock(p, "peg_seat", scube.build(), name=f"peg_{tag}")
        dock(p, "pad_seat", scube.build(), name=f"pad_{tag}")
    return out
'''

FILES = {"frame/__init__.py": FRAME, "frame/_plate.py": PLATE_HELPER, "frame/base.py": BASE, "frame/mplate.py": MPLATE,
         "frame/splate.py": SPLATE, "frame/mcube.py": MCUBE, "frame/scube.py": SCUBE}
HOST = {"m": "mplate", "s": "splate"}
MESH_PLACEMENTS = {"mbox", "mtouch", "mm_a", "mm_b", "mplate"}


def _build(shared):
    root = MP.write_project(shared / "proj", FILES, screw=True)
    r = H.run_model(root)
    (shared / "cold.json").write_text(json.dumps({"rc": r.rc, "log": r.log}))


@pytest.fixture(scope="module")
def checked(tmp_path_factory):
    """(root, clearance report, fit report) on a private copy of the once-per-run build; the reports are None when the build
    failed (each test then fails on the build log)."""
    import conftest
    shared = conftest._shared(tmp_path_factory, "meshsolid_checks", _build)
    cold = json.loads((shared / "cold.json").read_text())
    if cold["rc"] != 0:
        return None, cold["log"], None, None
    root = conftest._clean_copy(shared / "proj", tmp_path_factory.mktemp("msc") / "proj")
    if str(H.SCRIPTS) not in sys.path:
        sys.path.insert(0, str(H.SCRIPTS))
    import clearance
    import fit_checks
    return root, cold["log"], clearance.run(root), fit_checks.fit_report(root)


def _ok(checked):
    root, log, clr, fit = checked
    assert root is not None, f"the --model build of the checks project failed:\n{log[-2500:]}"
    return root, clr, fit


def _clash(rep, a, b):
    got = [c for c in rep["clashes"] if {c["a"], c["b"]} == {a, b}]
    assert len(got) <= 1, got
    return got[0] if got else None


def _unmeasured(rep, a, b):
    return [c for c in rep.get("unmeasured") or [] if {c["a"], c["b"]} == {a, b}]


def _about(items, *names):
    return [it for it in items if set(names) <= set(it["parts"])]


def _naming(fit, name):
    """Every fit item (fastener and clash lists) that names the placement."""
    return [it for it in fit["fastener"] + fit["clash"] if name in it["parts"]]


# ================================================================ DW7: clearance
def test_dw7_mesh_part_crossing_a_step_part_is_a_clash_with_its_volume(checked):
    root, clr, _ = _ok(checked)
    assert not _unmeasured(clr, "mbox", "sbox"), f"a mesh part must be measured, not left unmeasured: {clr.get('unmeasured')}"
    c = _clash(clr, "mbox", "sbox")
    assert c, f"mbox (mesh) and sbox (STEP) overlap by 200 mm3 and must be a clash; clashes {clr['clashes']}"
    assert c["volume_mm3"] == pytest.approx(200.0, rel=0.05), c


def test_dw7_mesh_part_touching_a_step_part_is_no_clash(checked):
    root, clr, _ = _ok(checked)
    assert _clash(clr, "mtouch", "stouch") is None and not _unmeasured(clr, "mtouch", "stouch"), \
        f"a shared face is contact, not overlap: {clr['clashes']} / {clr.get('unmeasured')}"


def test_dw7_mesh_vs_mesh_overlap_is_measured(checked):
    root, clr, _ = _ok(checked)
    assert not _unmeasured(clr, "mm_a", "mm_b"), clr.get("unmeasured")
    c = _clash(clr, "mm_a", "mm_b")
    assert c and c["volume_mm3"] == pytest.approx(480.0, rel=0.05), f"mm_a / mm_b overlap by 480 mm3: {c}"


def test_dw7_no_pair_with_a_mesh_part_is_left_unmeasured(checked):
    root, clr, _ = _ok(checked)
    bad = [u for u in clr.get("unmeasured") or [] if {u["a"], u["b"]} & MESH_PLACEMENTS]
    assert not bad, f"pairs with a mesh part left unmeasured: {bad}"


# ================================================================ DW8: fit checks (m = mesh host, s = the STEP control)
@pytest.mark.parametrize("tag", ["m", "s"])
def test_dw8_m5_in_a_5_2_clearance_hole_passes(checked, tag):
    root, _, fit = _ok(checked)
    got = _naming(fit, f"scr_{tag}52")
    assert got == [], f"an M5 in a 5.2 clearance hole of {HOST[tag]} must pass the fit check (no item at all), got {got}"


@pytest.mark.parametrize("tag", ["m", "s"])
def test_dw8_m5_in_a_4_6_hole_fails_with_the_drawn_diameter(checked, tag):
    root, _, fit = _ok(checked)
    got = [it for it in _about(fit["fastener"], f"scr_{tag}46") if it["level"] == "FAIL"]
    assert got, f"an M5 in the 4.6 hole of {HOST[tag]} must FAIL: {_about(fit['fastener'], f'scr_{tag}46')}"
    assert got[0].get("hole_d") == pytest.approx(4.6, abs=0.05), got[0]
    assert HOST[tag] in got[0]["parts"], got[0]


@pytest.mark.parametrize("tag", ["m", "s"])
def test_dw8_m5x12_in_a_6_5_blind_pocket_fails_bottoming_out(checked, tag):
    root, _, fit = _ok(checked)
    got = [it for it in _about(fit["fastener"], f"scr_{tag}blind") if it["level"] == "FAIL"]
    assert got and "bottom" in got[0]["why"].lower(), \
        f"M5x12 in the 6.5 mm blind pocket of {HOST[tag]} bottoms out: {_about(fit['fastener'], f'scr_{tag}blind')}"


@pytest.mark.parametrize("tag", ["m", "s"])
def test_dw8_step_part_docked_into_the_plate_fails_docked_parts_clash_with_its_volume(checked, tag):
    root, _, fit = _ok(checked)
    got = [it for it in _about(fit["clash"], f"peg_{tag}", HOST[tag]) if it["level"] == "FAIL"]
    assert got, f"peg_{tag} sunk 2 mm into {HOST[tag]} must FAIL: {_about(fit['clash'], f'peg_{tag}')}"
    assert got[0].get("volume") == pytest.approx(200.0, rel=0.05), got[0]


@pytest.mark.parametrize("tag", ["m", "s"])
def test_dw8_touching_docked_pairs_pass(checked, tag):
    """pad on the plate's face, and the plate on the base (a mesh part docked onto a STEP part, for m)."""
    root, _, fit = _ok(checked)
    assert _naming(fit, f"pad_{tag}") == [], _naming(fit, f"pad_{tag}")
    host = [it for it in _naming(fit, HOST[tag]) if set(it["parts"]) <= {HOST[tag], "base"}]
    assert host == [], host


def test_dw8_mesh_cubes_docked_on_the_base_pass(checked):
    root, _, fit = _ok(checked)
    for n in ("mbox", "mtouch", "mm_a", "mm_b"):
        assert _naming(fit, n) == [], f"{n} stands on base: {_naming(fit, n)}"


def test_dw8_every_docked_pair_with_a_mesh_part_is_examined(checked):
    """12 docked non-fastener pairs (6 cubes and 2 plates on base, peg and pad on each plate) and 6 screws in holes: a pair
    with a mesh part is checked like any other, not skipped as having no STEP."""
    root, _, fit = _ok(checked)
    assert (fit["clash_pairs"], fit["fastener_pairs"]) == (12, 6), \
        f"clash_pairs / fastener_pairs: {fit['clash_pairs']} / {fit['fastener_pairs']}; items {fit['clash'] + fit['fastener']}"
    unread = [it for it in fit["clash"] + fit["fastener"] if it.get("unread") or "no placement with a STEP" in it["why"]]
    assert unread == [], f"pairs with a mesh part left unchecked: {unread}"
