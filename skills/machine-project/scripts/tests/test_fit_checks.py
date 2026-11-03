"""Issue #47, the GEOMETRIC rules of the new `fit_checks.py`: `fastener fit` and `docked parts clash`.

The module does not exist yet; every test here imports it inside its own body, so a missing module is a FAILING test and not a
collection error. The static rules (`bought = drawn`, `bought part verified`) and the wiring live in test_fit_static.py.

Two projects are built, once per module each, both from jointfiles.py's joint project with the model replaced:

  fitproj    frame/plate.py   200 x 100 x 10 plate, DRILLED THROUGH HOLES of 4.6 / 5.5 / 4.2 / 7.5 mm (joints hole_46, hole_55,
                              hole_42, hole_75 on the top face, Z out of it) and two blind tap-drill pockets 4.2 x 6.5 deep
                              (joints seat_deep, seat_short on the top face above them)
             frame/leg.py     20 x 20 x 3 leg with a 5.5 clearance hole through it; joints bottom (on the pocket) and hole (top face)
             docking          M5x12 into each of the four through holes; a leg on each pocket, then M5x12 into seat_deep's leg
                              (3 mm leg + 6.5 mm pocket = 9.5 mm of hole, the screw bottoms out) and M5x8 into seat_short's leg
             covers           done-when 1, 2, 3, 4, 6 and the fit.json contract 19/20 rests on

  clashproj  frame/base.py    200 x 100 x 10 plate with the mounting joints of everything below
             block_a/b        block_b docked on block_a's `top` joint, which sits 2 mm BELOW block_a's top face: 20 x 20 x 2 of
                              solid overlap  -> done-when 7
             block_c/d        block_d docked on block_c's `top` joint, on the face itself: coincident, no overlap -> done-when 8
             tnut             14 mm block with a blind 4.2 tap-drill hole 10 deep, M5x8 screwed into it -> done-when 9
             shim             docked on a base joint 0.8 mm ABOVE the plate's top face: a 0.8 mm gap -> done-when 10
             strut_a/strut_b  both docked to the PLATE, crossing each other 10 x 10 x 10 -> done-when 11

done-when 5 needs a hole joint with no hole drawn at all: that is jointfiles.py's unmodified baseline (`jproj`), which costs no
extra build.
"""
import json
from pathlib import Path

import pytest

import helpers as H
import jointfiles as J
import partfiles as F
from jointfiles import joint_baseline, jproj  # noqa: F401  (fixtures)

FASTENER = "fastener fit"
CLASH = "docked parts clash"


# ---------------------------------------------------------------- calling the module under test
def fit_lines(root, rule, compute=True):
    """The `rule` lines of fit_checks.run(root), as check.py reads them: [(status, rule, detail)]."""
    import fit_checks                      # in the body on purpose: a missing module must FAIL the test, not error collection
    out = fit_checks.run(root, compute=compute)
    assert isinstance(out, list) and all(isinstance(r, (list, tuple)) and len(r) >= 3 for r in out), \
        f"run() must return (status, rule, detail, ru) tuples like cad_gates.run, got {out!r}"
    return [(r[0], r[1], r[2]) for r in out if r[1] == rule]


def rule_line(root, rule, compute=True):
    """(worst status, rule, every detail of it) — the rule may report its findings on one line or on several."""
    lines = fit_lines(root, rule, compute)
    assert lines, f"fit_checks.run must report the {rule!r} rule on a project with a built model"
    for st, _, _ in lines:
        assert st in ("PASS", "WARN", "FAIL"), f"status must be PASS|WARN|FAIL: {lines}"
    worst = next((st for st in ("FAIL", "WARN", "PASS") if any(l[0] == st for l in lines)))
    return worst, rule, " | ".join(l[2] for l in lines)


def report(root, key):
    """The `key` ("fastener" | "clash") list of fit_checks.fit_report(root), each item checked against the contract."""
    import fit_checks
    rep = fit_checks.fit_report(root)
    assert isinstance(rep, dict) and key in rep, f"fit_report must return a dict with a {key!r} list, got {rep!r}"
    for it in rep[key]:
        assert isinstance(it, dict) and {"level", "why", "parts"} <= set(it), \
            f"every {key} item needs level, why and parts: {it!r}"
        assert it["level"] in ("FAIL", "WARN"), f"a reported item is FAIL or WARN: {it!r}"
    return rep[key]


def about(items, *names):
    """The report items that name every one of `names` among their parts."""
    return [it for it in items if set(names) <= set(it["parts"])]


def levels(items, *names):
    return sorted(it["level"] for it in about(items, *names))


# ---------------------------------------------------------------- the fastener-fit project
PLATE = '''
from build123d import Align, Box, Cylinder, Location, Pos, RigidJoint
from geom import P

T = 10.0
POCKET = 6.5                   # blind tap-drill pocket under a leg: 3 mm leg + 6.5 mm pocket = 9.5 mm of hole on the screw axis
HOLES = {"hole_46": (20, 20, 4.6), "hole_55": (60, 20, 5.5), "hole_42": (100, 20, 4.2), "hole_75": (140, 20, 7.5)}
SEATS = {"seat_deep": (60, 70), "seat_short": (140, 70)}


def build():
    s = Box(200, 100, T, align=(Align.MIN, Align.MIN, Align.MIN))
    for x, y, d in HOLES.values():
        s -= Pos(x, y, T / 2) * Cylinder(d / 2, 3 * T)                     # drilled right through
    for x, y in SEATS.values():
        s -= Pos(x, y, T - POCKET / 2) * Cylinder(4.2 / 2, POCKET)         # blind, from the top face down
    for label, (x, y, d) in HOLES.items():
        RigidJoint(label, s, Location((x, y, T)))
    for label, (x, y) in SEATS.items():
        RigidJoint(label, s, Location((x, y, T)))
    return P("plate", s, "#888888", process="mill", material="S235JR")
'''

LEG = '''
from build123d import Align, Box, Cylinder, Location, Pos, RigidJoint
from geom import P

T = 3.0


def build():
    s = Box(20, 20, T, align=(Align.CENTER, Align.CENTER, Align.MIN))
    s -= Pos(0, 0, T / 2) * Cylinder(5.5 / 2, 3 * T)                       # M5 clearance hole, through
    RigidJoint("bottom", s, Location((0, 0, 0)))
    RigidJoint("hole", s, Location((0, 0, T)))
    return P("leg", s, "#aaaaaa", process="mill", material="AL6060")
'''

FIT_FRAME = '''
from build123d import Pos
from geom import place
from . import leg, plate, screw

THROUGH = ["hole_46", "hole_55", "hole_42", "hole_75"]
SEATS = [("seat_deep", 12), ("seat_short", 8)]


def build():
    p = plate.build()
    out = [place(p, Pos(0, 0, 0))]
    for h in THROUGH:
        s = screw.build("M5", 12, "DIN 912")
        p["shape"].joints[h].connect_to(s["shape"].joints["head"])
        out.append(place(s, name="screw_" + h))
    for seat, length in SEATS:
        g = leg.build()
        p["shape"].joints[seat].connect_to(g["shape"].joints["bottom"])
        out.append(place(g, name="leg_" + seat))
        s = screw.build("M5", length, "DIN 912")
        g["shape"].joints["hole"].connect_to(s["shape"].joints["head"])
        out.append(place(s, name="screw_" + seat))
    return out
'''

ROOT_NODE = '''
from . import frame


def build():
    return frame.build()
'''

NO_HEAD = {"__init__.py": ROOT_NODE, "head/__init__.py": None, "head/slider.py": None, "head/arm.py": None,
           "frame/base.py": None, "frame/rail.py": None}

FIT_FILES = dict(NO_HEAD, **{"frame/__init__.py": FIT_FRAME, "frame/plate.py": PLATE, "frame/leg.py": LEG})


@pytest.fixture(scope="module")
def fitproj(tmp_path_factory):
    """(root, Model) of the drilled-hole project, built once for this module."""
    root = J.write_project(tmp_path_factory.mktemp("fit") / "proj", files=FIT_FILES)
    return Path(root), F.run_model(root)


def fit_built(fitproj):
    """The built project, with the placements the fastener tests name present in parts.json."""
    root, m = fitproj
    m.ok()
    pls = J.placement_map(root)
    for name in ("plate", "screw_hole_46", "screw_hole_55", "screw_hole_42", "screw_hole_75",
                 "leg_seat_deep", "screw_seat_deep", "leg_seat_short", "screw_seat_short"):
        assert name in pls, f"the fixture project must place {name!r}; placements: {sorted(pls)}"
    return root


def test_g1_m5_screw_in_a_4_6_hole_fails_naming_the_screw_the_part_and_the_diameter(fitproj):
    """done-when 1: the mismatched-bracket case. 4.6 is neither the M5 clearance (5.3-5.5) nor the M5 tap drill (4.2)."""
    root = fit_built(fitproj)
    st, _, detail = rule_line(root, FASTENER)
    assert st == "FAIL", f"an M5 screw in a 4.6 mm hole must FAIL the {FASTENER!r} rule, got {st}: {detail}"
    assert "screw_hole_46" in detail, f"the detail must name the screw placement: {detail}"
    assert "plate" in detail, f"the detail must name the part the hole is in: {detail}"
    assert "4.6" in detail, f"the detail must name the hole diameter it measured: {detail}"
    low = detail.lower()
    assert "clearance" in low and "tap" in low, \
        f"the detail must say the diameter is neither a clearance hole nor a tap drill: {detail}"
    got = about(report(root, "fastener"), "screw_hole_46")
    assert [it for it in got if it["level"] == "FAIL"], f"fit.json must carry the 4.6 hole as a FAIL item, got {got}"


def test_g2_m5_screw_in_a_5_5_clearance_hole_is_not_reported(fitproj):
    """done-when 2: 5.5 is the M5 clearance hole -> PASS, nothing about this screw in the report."""
    root = fit_built(fitproj)
    got = about(report(root, "fastener"), "screw_hole_55")
    assert got == [], f"an M5 screw in a 5.5 mm clearance hole must pass the fit check, got {got}"


def test_g3_m5_screw_in_a_4_2_tap_drill_hole_is_not_reported(fitproj):
    """done-when 3: 4.2 is the M5 tap drill -> a tapped seat, PASS."""
    root = fit_built(fitproj)
    got = about(report(root, "fastener"), "screw_hole_42")
    assert got == [], f"an M5 screw in a 4.2 mm tap-drill hole must pass the fit check (a tapped seat), got {got}"


def test_g4_m5x12_in_a_9_5_mm_deep_seat_fails_saying_it_bottoms_out(fitproj):
    """done-when 4, first half: 3 mm leg + 6.5 mm blind pocket = 9.5 mm on the axis, the M5x12 shaft is 12 mm."""
    root = fit_built(fitproj)
    got = about(report(root, "fastener"), "screw_seat_deep")
    bad = [it for it in got if it["level"] == "FAIL"]
    assert bad, f"an M5x12 screw into a 9.5 mm deep seat must FAIL, got {got}"
    why = " ".join(it["why"] for it in bad).lower()
    assert "bottom" in why, f"the reason must say the screw bottoms out: {bad}"
    assert "9.5" in " ".join(str(it) for it in bad), f"the report must name the available depth 9.5 mm: {bad}"
    st, _, detail = rule_line(root, FASTENER)
    assert st == "FAIL" and "screw_seat_deep" in detail, f"the rule line must name the bottoming screw: {st} {detail}"


def test_g5_m5x8_in_the_same_9_5_mm_seat_is_not_reported(fitproj):
    """done-when 4, second half: 8 mm of shaft fits in 9.5 mm of hole."""
    root = fit_built(fitproj)
    got = about(report(root, "fastener"), "screw_seat_short")
    assert got == [], f"an M5x8 screw into the same 9.5 mm deep seat must pass the fit check, got {got}"


def test_g6_m5_screw_in_a_7_5_mm_oversize_hole_warns_about_a_loose_fit(fitproj):
    """done-when 6: far oversize is a WARN, never a FAIL."""
    root = fit_built(fitproj)
    got = about(report(root, "fastener"), "screw_hole_75")
    assert got, "an M5 screw in a 7.5 mm hole must be reported as a loose fit"
    assert levels(got, "screw_hole_75") == ["WARN"] * len(got), f"a far oversize hole is a WARN, not a FAIL: {got}"
    why = " ".join(it["why"] for it in got).lower()
    assert any(w in why for w in ("loose", "play", "slop")), f"the reason must say the fit is loose: {got}"


def test_g7_hole_joint_with_no_hole_drawn_warns_and_never_fails(jproj):
    """done-when 5: jointfiles' baseline plate carries hole_1..hole_6 joints with NO hole cut in the geometry. The rule must say
    the fit could not be checked and must not FAIL the project."""
    root = Path(jproj)
    pls = J.placement_map(root)
    screws = [n for n, (p, _) in pls.items() if p.get("process") == "bought"]
    assert len(screws) == 6, f"the baseline docks 6 screws into undrilled hole joints; got {sorted(pls)}"
    st, _, detail = rule_line(root, FASTENER)
    assert st == "WARN", f"an undrilled hole joint is a WARN, never a FAIL: {st}: {detail}"
    low = detail.lower()
    assert "hole" in low and any(w in low for w in ("could not", "cannot", "not checked", "unchecked")), \
        f"the detail must say the fit could not be checked for want of a drawn hole: {detail}"
    got = report(root, "fastener")
    assert got and all(it["level"] == "WARN" for it in got), f"every item must be a WARN: {got}"
    assert any(set(it["parts"]) & set(screws) for it in got), f"the items must name the screw placements {screws}: {got}"


def test_g8_fit_report_is_written_to_build_model_fit_json_with_todays_parts_sha(fitproj):
    """The file contract the offline mode (done-when 19, 20) rests on: fit_report writes build/model/fit.json, and its parts_sha
    is the sha256 of build/model/parts.json as it stands, so a later parts.json makes the report stale."""
    root = fit_built(fitproj)
    import fit_checks
    rep = fit_checks.fit_report(root)
    f = root / "build" / "model" / "fit.json"
    assert f.is_file(), "fit_report must also write build/model/fit.json"
    on_disk = json.loads(f.read_text())
    assert on_disk == rep, f"build/model/fit.json must be what fit_report returned:\n{on_disk}\n{rep}"
    assert rep.get("parts_sha") == H.sha_file(root / "build" / "model" / "parts.json"), \
        f"parts_sha must be the sha256 of build/model/parts.json: {rep.get('parts_sha')!r}"


# ---------------------------------------------------------------- the clash project
BASE = '''
from build123d import Align, Box, Location, RigidJoint
from geom import P

T = 10.0
# label -> where the joint sits (Z out of the plate's top face). gap_mount is 0.8 mm ABOVE the face on purpose.
MOUNTS = {"a_mount": (20, 20, T), "c_mount": (70, 20, T), "nut_mount": (120, 20, T), "gap_mount": (170, 20, T + 0.8),
          "strut_a": (40, 70, T), "strut_b": (60, 40, T)}


def build():
    s = Box(200, 100, T, align=(Align.MIN, Align.MIN, Align.MIN))
    for label, xyz in MOUNTS.items():
        RigidJoint(label, s, Location(xyz))
    return P("base", s, "#888888", process="mill", material="S235JR")
'''

BLOCK = '''
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    """A plain block. Its `top` joint sits %(sink)g mm below the top face, so whatever docks on it sinks %(sink)g mm in."""
    s = Box(%(w)g, %(w)g, %(h)g, align=(Align.CENTER, Align.CENTER, Align.MIN))
    RigidJoint("bottom", s, Location((0, 0, 0)))
    RigidJoint("top", s, Location((0, 0, %(h)g - %(sink)g)))
    return P("%(id)s", s, "#4444aa", process="mill", material="AL6060")
'''

TNUT = '''
from build123d import Align, Box, Cylinder, Location, Pos, RigidJoint
from geom import P

T = 14.0
DEPTH = 10.0


def build():
    s = Box(20, 20, T, align=(Align.CENTER, Align.CENTER, Align.MIN))
    s -= Pos(0, 0, T - DEPTH / 2) * Cylinder(4.2 / 2, DEPTH)               # blind M5 tap drill, threads cut in it
    RigidJoint("bottom", s, Location((0, 0, 0)))
    RigidJoint("thread", s, Location((0, 0, T)))
    return P("tnut", s, "#777777", process="mill", material="S235JR")
'''

SHIM = '''
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    s = Box(20, 20, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    RigidJoint("bottom", s, Location((0, 0, 0)))
    return P("shim", s, "#00aa00", process="mill", material="S235JR")
'''

STRUT = '''
from build123d import Align, Box, Location, RigidJoint
from geom import P


def build():
    s = Box(%(x)g, %(y)g, 10, align=(Align.%(ax)s, Align.%(ay)s, Align.MIN))
    RigidJoint("end", s, Location((0, 0, 0)))
    return P("%(id)s", s, "#ff8800", process="mill", material="AL6060")
'''

CLASH_FRAME = '''
from build123d import Pos
from geom import place
from . import base, block_a, block_b, block_c, block_d, screw, shim, strut_a, strut_b, tnut


def build():
    b = base.build()
    out = [place(b, Pos(0, 0, 0))]

    a, bb = block_a.build(), block_b.build()                               # overlapping pair: block_b sinks 2 mm into block_a
    b["shape"].joints["a_mount"].connect_to(a["shape"].joints["bottom"])
    out.append(place(a, name="block_a"))
    a["shape"].joints["top"].connect_to(bb["shape"].joints["bottom"])
    out.append(place(bb, name="block_b"))

    c, d = block_c.build(), block_d.build()                                # touching pair: coincident faces
    b["shape"].joints["c_mount"].connect_to(c["shape"].joints["bottom"])
    out.append(place(c, name="block_c"))
    c["shape"].joints["top"].connect_to(d["shape"].joints["bottom"])
    out.append(place(d, name="block_d"))

    n = tnut.build()                                                       # a screw in a tapped hole
    b["shape"].joints["nut_mount"].connect_to(n["shape"].joints["bottom"])
    out.append(place(n, name="tnut"))
    s = screw.build("M5", 8, "DIN 912")
    n["shape"].joints["thread"].connect_to(s["shape"].joints["head"])
    out.append(place(s, name="screw_tnut"))

    sh = shim.build()                                                      # docked 0.8 mm above the face it docks on
    b["shape"].joints["gap_mount"].connect_to(sh["shape"].joints["bottom"])
    out.append(place(sh, name="shim"))

    for mod, label in ((strut_a, "strut_a"), (strut_b, "strut_b")):        # both docked to the PLATE, crossing each other
        p = mod.build()
        b["shape"].joints[label].connect_to(p["shape"].joints["end"])
        out.append(place(p, name=label))
    return out
'''

CLASH_FILES = dict(NO_HEAD, **{
    "frame/__init__.py": CLASH_FRAME,
    "frame/base.py": BASE,
    "frame/block_a.py": BLOCK % {"id": "block_a", "w": 30, "h": 20, "sink": 2.0},
    "frame/block_b.py": BLOCK % {"id": "block_b", "w": 20, "h": 20, "sink": 0.0},
    "frame/block_c.py": BLOCK % {"id": "block_c", "w": 30, "h": 20, "sink": 0.0},
    "frame/block_d.py": BLOCK % {"id": "block_d", "w": 20, "h": 20, "sink": 0.0},
    "frame/tnut.py": TNUT,
    "frame/shim.py": SHIM,
    "frame/strut_a.py": STRUT % {"id": "strut_a", "x": 60, "y": 10, "ax": "MIN", "ay": "CENTER"},
    "frame/strut_b.py": STRUT % {"id": "strut_b", "x": 10, "y": 60, "ax": "CENTER", "ay": "MIN"},
})


@pytest.fixture(scope="module")
def clashproj(tmp_path_factory):
    """(root, Model) of the clash project, built once for this module."""
    root = J.write_project(tmp_path_factory.mktemp("clash") / "proj", files=CLASH_FILES)
    return Path(root), F.run_model(root)


def clash_built(clashproj):
    root, m = clashproj
    m.ok()
    pls = J.placement_map(root)
    for name in ("base", "block_a", "block_b", "block_c", "block_d", "tnut", "screw_tnut", "shim", "strut_a", "strut_b"):
        assert name in pls, f"the fixture project must place {name!r}; placements: {sorted(pls)}"
    assert pls["block_b"][1]["joint"]["to"] == "block_a", f"block_b must be docked to block_a: {pls['block_b'][1]}"
    assert pls["strut_a"][1]["joint"]["to"] == "base" and pls["strut_b"][1]["joint"]["to"] == "base", \
        "the struts must be docked to the plate, not to each other"
    return root


def test_g9_two_docked_parts_whose_solids_overlap_fail_naming_both(clashproj):
    """done-when 7: block_b sinks 2 mm into block_a, 20 x 20 x 2 = 800 mm3 of shared solid."""
    root = clash_built(clashproj)
    st, _, detail = rule_line(root, CLASH)
    assert st == "FAIL", f"overlapping docked solids must FAIL the {CLASH!r} rule, got {st}: {detail}"
    assert "block_a" in detail and "block_b" in detail, f"the detail must name both placements: {detail}"
    got = about(report(root, "clash"), "block_a", "block_b")
    assert [it for it in got if it["level"] == "FAIL"], f"fit.json must carry the overlap as a FAIL item, got {got}"


def test_g10_two_docked_parts_merely_touching_are_not_reported(clashproj):
    """done-when 8: block_d sits on block_c's face, coincident, no shared volume."""
    root = clash_built(clashproj)
    got = about(report(root, "clash"), "block_c", "block_d")
    assert got == [], f"coincident faces are not a clash and not a gap, got {got}"


def test_g11_a_screw_in_a_tapped_hole_is_not_a_clash(clashproj):
    """done-when 9: the M5 shaft sits in a 4.2 tap-drill hole, so the solids share ~46 mm3 of thread; that is how a thread is
    drawn, not a clash."""
    root = clash_built(clashproj)
    got = about(report(root, "clash"), "tnut", "screw_tnut")
    assert got == [], f"a screw threaded into a tapped hole must not be reported as a clash, got {got}"


def test_g12_a_docked_part_left_with_a_gap_warns_naming_both_and_the_gap(clashproj):
    """done-when 10: the shim's joint sits 0.8 mm above the plate face, so the docked parts never touch."""
    root = clash_built(clashproj)
    got = about(report(root, "clash"), "base", "shim")
    assert got, "a part docked with a gap to its parent must be reported"
    assert levels(got, "base", "shim") == ["WARN"] * len(got), f"a gap is a WARN, not a FAIL: {got}"
    assert "0.8" in " ".join(str(it) for it in got), f"the report must name the gap in mm: {got}"
    st, _, detail = rule_line(root, CLASH)
    assert "shim" in detail, f"the rule line must name the part left hanging: {st}: {detail}"


def test_g13_parts_that_are_not_docked_to_each_other_are_not_reported(clashproj):
    """done-when 11: strut_a and strut_b are both docked to the plate and cross each other 10 x 10 x 10; finding that is issue
    #19 and out of scope here."""
    root = clash_built(clashproj)
    got = about(report(root, "clash"), "strut_a", "strut_b")
    assert got == [], f"parts that are not docked to each other belong to issue #19, not to this rule, got {got}"
    _, _, detail = rule_line(root, CLASH)
    assert not ("strut_a" in detail and "strut_b" in detail), f"the rule line must not name the undocked pair: {detail}"
