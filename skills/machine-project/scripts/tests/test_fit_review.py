"""The cases an independent review of `fit_checks.py` reproduced, each one a verdict the rules got
wrong. Written after those rules existed, unlike test_fit_static.py and test_fit_checks.py, so each case here was first
run against the broken code and observed to fail.

Two directions, both expensive. A wrong model reporting PASS defeats the point of the ticket:
  r1  a fastener verdict is cached per pair, but it measures every part on the screw's axis; editing one of those left
      the old verdict in place, so "it bottoms out" vanished on the most ordinary workflow there is
  r2  the stored report was validated against the model only, so a corrected rule never took effect
  r3  a counterbored or countersunk seat lost the diameter check altogether - and that is the ticket's own defect,
      since the repo's own screw template ships countersunk heads
  r7  a boolean, a distance or a probe that could not compute read as "no overlap" / "no material"
A correct model reporting FAIL reaches eight clients' agents through the router as a system item:
  r4  the row's product link not spelling the article, or a vendor file not named after it
  r5  the bare word "analogue" (in any language) on a part whose row orders the article it is drawn from
  r6  a male fastener whose name is not in a word list, judged by the clash rule for its own thread
And the row half of the maker-drawing check:
  r8  a part that reached the list with no maker-drawing check leaves no mark in its purchase row
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import helpers as H
import jointfiles as J
import partfiles as F
import procgroup
import test_fit_static as S
from samples_i18n import SAMPLES

RU = SAMPLES["ru"]          # purchase texts and fastener names as a Russian project writes them

FASTENER = "fastener fit"
CLASH = "docked parts clash"
DRAWN = "bought = drawn"


# ================================================================ r4, r5: correct models that must not FAIL
A184 = {"file": "vendor/acme-A184.step", "code": "A184"}
A184_SLUG = "https://shop.example/product/ugolok-krepezhnyy-20h20-mm-stal-1634859201/"   # no article in the slug
A184_LINK = "https://shop.example/product/uglovoy-soedinitel-paz-6-a184-7-sht-1543219412/"
T01_LINK = "https://shop.example/product/uglovoy-soedinitel-paz-6-t01-5sht-1210824793/"

OK_ROWS = [
    ("the product link's slug carries no article at all",
     {"src": A184, "code": "A184", "link": A184_SLUG}),
    ("the vendor file is not named after the article",
     {"src": {"file": "vendor/ugolok-20x20.step"}, "code": "A184", "link": "https://acme.example/product/ugolok-a184/"}),
    ("the vendor file is <article>-<size>, so the maker/article split reads it backwards",
     {"src": {"file": "vendor/A184-20x20.step"}, "code": "A184", "link": A184_LINK}),
    ("a marketplace link whose URL names no product slug",
     {"src": A184, "code": "A184", "link": "https://market.example/catalog/178349201/detail.aspx"}),
    ("«TCST2103 or an analogue» on a part that records NO `src`",
     {"code": "TCST2103", "link": "https://parts.example/product/tcst2103", "note": RU["or_analogue"].format(code="TCST2103")}),
    ("«an analogue of any maker will do» on a profile that records NO `src`",
     {"code": "V20x20L", "link": "https://shop.example/product/profil-v20h20l-1424355320/",
      "note": RU["any_maker_analogue"]}),
]

# The deliberate exception to r5, and the reason the two rows above record no `src`: `src` is an opt-in statement that
# the machine's fits ARE this maker's geometry. A purchase text that then offers an analogue contradicts it, and only a
# FAIL reaches the agent (the router forwards no WARNs), so the engineer has to drop one of the two.
ANALOGUE_WITH_SRC = [
    ("«or an analogue» on a part drawn from a maker's 3D",
     {"src": {"file": "vendor/omron-TCST2103.stp", "code": "TCST2103"}, "code": "TCST2103",
      "link": "https://parts.example/product/tcst2103", "note": RU["or_analogue"].format(code="TCST2103")}),
    ("English «analogue of the drawn one» on a part drawn from a maker's 3D",
     {"src": A184, "code": "A184", "link": A184_LINK, "desc": "Corner bracket A184, analogue of the drawn one"}),
]


@pytest.mark.parametrize("label,fields", ANALOGUE_WITH_SRC, ids=[r[0][:40] for r in ANALOGUE_WITH_SRC])
def test_r5_an_analogue_note_on_a_part_with_a_recorded_3d_source_fails(label, fields):
    import fit_checks
    p = dict({"id": "part", "name": "part", "key": "k", "process": "bought", "kind": "profile", "qty": 1,
              "step": "build/model/step/k.step", "verified": S.CHECKED}, **fields)
    st, _, detail = fit_checks.gate_bought_is_drawn([p])[0]
    assert st == "FAIL", f"{label} must FAIL: {detail}"
    assert "analogue" in detail, detail


@pytest.mark.parametrize("label,fields", OK_ROWS, ids=[r[0][:40] for r in OK_ROWS])
def test_r4_r5_a_correct_bought_part_never_fails_bought_is_drawn(tmp_path, label, fields):
    """r4, r5: a FAIL needs positive evidence that ANOTHER article is ordered. Anything less is a WARN, because this
    rule's FAIL becomes a system item on eight live projects."""
    import fit_checks
    p = dict({"id": "bracket", "name": "bracket", "key": "k", "process": "bought", "kind": "profile", "qty": 8,
              "step": "build/model/step/k.step", "verified": S.CHECKED}, **fields)
    st = fit_checks.gate_bought_is_drawn([p])[0][0]
    assert st != "FAIL", f"a correct part must not FAIL when {label}: {fit_checks.gate_bought_is_drawn([p])}"


def test_r4_the_mismatched_bracket_case_still_fails(tmp_path):
    """The rule may not be relaxed into uselessness: the drawn article is Misumi's, the link orders another maker's T01."""
    import fit_checks
    p = {"id": "tower_joint_0", "name": "Misumi LBSBL6-2028 corner bracket (analogue: connector 20x20 T01)",
         "key": "k", "process": "bought", "kind": "profile", "qty": 8, "step": "build/model/step/k.step",
         "src": {"file": "vendor/misumi-LBSBL6-2028.stp", "code": "LBSBL6-2028"},
         "code": "LBSBL6-2028, T01", "link": T01_LINK}
    st, _, detail = fit_checks.gate_bought_is_drawn([p])[0], None, fit_checks.gate_bought_is_drawn([p])[0][2]
    assert st[0] == "FAIL", f"the mismatched-bracket case must still FAIL: {st}"
    assert "T01" in detail and "LBSBL6-2028" in detail, f"the detail must name both articles: {detail}"


def test_r5_an_explicitly_drawn_part_ordered_as_another_article_fails(tmp_path):
    """The other side of r5: the analogue IS what the row orders, which is the mismatched-bracket shape without the word "analogue"."""
    import fit_checks
    p = {"id": "bracket", "name": "bracket", "key": "k", "process": "bought", "kind": "profile", "qty": 1,
         "step": "build/model/step/k.step", "src": {"file": "vendor/misumi-LBSBL6-2028.stp", "code": "LBSBL6-2028"},
         "code": "T01", "link": T01_LINK}
    st, _, detail = fit_checks.gate_bought_is_drawn([p])[0]
    assert st == "FAIL", f"a part drawn from one article and ordered as another must FAIL: {detail}"


# ================================================================ r6: what counts as a male fastener
FASTENER_NAMES = RU["male_fasteners"] + ["Threaded rod M5x12", "Dowel pin M5x12"]
NOT_FASTENERS = RU["not_male_fasteners"] + ["Washer M5 DIN 125", "Hex nut M5 DIN 934"]


@pytest.mark.parametrize("name", FASTENER_NAMES)
def test_r6_a_male_fastener_is_recognised_whatever_it_is_called(name):
    """r6: a word list missed dowel pins and "threaded rod", and every male fastener it missed went to the clash rule,
    which then FAILed it for the thread overlap that is its whole purpose."""
    import fit_checks
    assert fit_checks._is_fastener({"name": name, "kind": "fasteners"}), f"{name!r} must be checked by `fastener fit`"


@pytest.mark.parametrize("name", NOT_FASTENERS)
def test_r6_a_nut_or_a_washer_is_not_a_male_fastener(name):
    import fit_checks
    assert not fit_checks._is_fastener({"name": name, "kind": "fasteners"}), f"{name!r} is not a male fastener"


def test_r6_a_part_of_another_kind_is_not_a_male_fastener():
    import fit_checks
    assert not fit_checks._is_fastener({"name": RU["screw_m5x12"], "kind": "other"})


# ================================================================ r2: the stored report is stale when the rules change
def test_r2_a_report_made_by_older_rules_is_stale(tmp_path):
    """r2: `fit.json` was validated against the model only, so a corrected tolerance or hole finder kept reporting the
    pre-fix verdicts on every project whose parts.json had not moved."""
    import fit_checks
    root = S.write_model(tmp_path / "p", [S.frame_root(), S.drawn("bearing", src=S.MISUMI_A184, code="A184",
                                                                  link=S.SHOP_A184, verified=S.CHECKED)])
    sha = fit_checks.parts_sha(root)
    f = Path(root) / "build" / "model" / "fit.json"
    f.write_text(json.dumps({"parts_sha": sha, "code_sha": "0" * 12, "fastener": [], "clash": [],
                             "fastener_pairs": 3, "clash_pairs": 1}))
    st, _, detail = S.rule_line(root, FASTENER, compute=False)
    assert st == "WARN", f"a report from older rules must not be reported as current: {st}: {detail}"
    assert "version of the fit rules" in detail, detail


def test_r2_a_current_report_is_used(tmp_path):
    """The twin: the same report with today's code sha is reported, so r2's fix cannot be a blanket recompute."""
    import fit_checks
    root = S.write_model(tmp_path / "p", [S.frame_root(), S.drawn("bearing", src=S.MISUMI_A184, code="A184",
                                                                  link=S.SHOP_A184, verified=S.CHECKED)])
    f = Path(root) / "build" / "model" / "fit.json"
    f.write_text(json.dumps({"parts_sha": fit_checks.parts_sha(root), "code_sha": fit_checks._code_sha(),
                             "fastener": [{"level": "FAIL", "why": "screw1 bottoms out", "parts": ["screw1"]}],
                             "clash": [], "fastener_pairs": 1, "clash_pairs": 0}))
    st, _, detail = S.rule_line(root, FASTENER, compute=False)
    assert st == "FAIL" and "bottoms out" in detail, f"a current report must be reported: {st}: {detail}"


# ================================================================ Minor 1: a PASS says what it looked at
def test_m1_a_passing_rule_says_how_many_pairs_it_checked(tmp_path):
    """"nothing to report" read the same whether 37 screws all fit or no screw was ever looked at, and WARNs never
    reach the router."""
    import fit_checks
    root = S.write_model(tmp_path / "p", [S.frame_root(), S.drawn("bearing", src=S.MISUMI_A184, code="A184",
                                                                  link=S.SHOP_A184, verified=S.CHECKED)])
    f = Path(root) / "build" / "model" / "fit.json"
    f.write_text(json.dumps({"parts_sha": fit_checks.parts_sha(root), "code_sha": fit_checks._code_sha(),
                             "fastener": [], "clash": [], "fastener_pairs": 37, "clash_pairs": 0}))
    st, _, detail = S.rule_line(root, FASTENER, compute=False)
    assert st == "PASS" and "37" in detail, f"a PASS must say how many pairs it examined: {st}: {detail}"
    st2, _, d2 = S.rule_line(root, CLASH, compute=False)
    assert st2 == "PASS" and "no pair" in d2, f"zero pairs must not read like a clean result: {st2}: {d2}"


# ================================================================ Minor 2: an unreadable parts.json says why
def test_m2_a_malformed_parts_json_reports_why_instead_of_dropping_every_rule(tmp_path):
    import fit_checks
    root = tmp_path / "p"
    (root / "build" / "model").mkdir(parents=True)
    (root / "build" / "model" / "parts.json").write_text("{not json")
    out = fit_checks.run(str(root), compute=False)
    assert out, "a parts.json that cannot be read must not silently remove every fit rule"
    assert all(r[0] == "WARN" for r in out), out
    assert any("could not be read" in r[2] for r in out), out


# ================================================================ Minor 4, 5: the pure functions
@pytest.mark.parametrize("text,want", RU["screw_spec_cases"] + [("Screw M12x1.5x40 DIN 912", (12.0, 40.0)),
                                                                  ("Cable duct 25x25", None)])
def test_m4_screw_spec_reads_the_thread_and_the_length(text, want):
    """M1.6x8 used to parse as M1 with no length, and M12x1.5x40 as a 1.5 mm long screw, which skipped the depth check
    and then warned that 1.5 mm of thread was not enough."""
    import fit_checks
    assert fit_checks.screw_spec({"name": text}) == want


def test_m5_vol_sums_a_shapelist_and_says_None_when_it_cannot():
    """`a & b` returns a Part on build123d 0.13 but a ShapeList elsewhere, and a ShapeList carries no `volume`:
    reading that as 0 is how every overlap went unnoticed once."""
    import fit_checks
    B = pytest.importorskip("build123d")
    a, b = B.Box(10, 10, 10), B.Pos(20, 0, 0) * B.Box(10, 10, 10)
    assert fit_checks._vol(B.ShapeList([a, b])) == pytest.approx(2000.0)
    assert fit_checks._vol(None) is None
    assert fit_checks._vol(["not a shape"]) is None


def test_m7_tap_drill_has_no_silent_guess():
    """A guessed pitch lands near seat_kind's band edges and would judge a correct hole."""
    import fit_checks
    assert fit_checks.tap_drill(5) == pytest.approx(4.2)
    assert fit_checks.tap_drill(3.5) == pytest.approx(2.9)
    assert fit_checks.tap_drill(7.3) is None
    assert fit_checks.seat_kind(6.0, 7.3) == "unknown"


def test_m6_a_hole_the_size_of_the_screw_warns_rather_than_fails():
    """Drawing a clearance hole at nominal size and leaving the clearance to the process is a common shortcut."""
    import fit_checks
    assert fit_checks.seat_kind(5.0, 5) == "nominal"
    assert fit_checks.seat_kind(4.6, 5) is None          # the T01: still a FAIL
    assert fit_checks.seat_kind(5.5, 5) == "free"
    assert fit_checks.seat_kind(4.2, 5) == "thread"


# ================================================================ r3: counterbored and countersunk seats
def test_r3_seat_of_picks_the_shaft_hole_under_a_counterbore_or_a_countersink():
    """r3: the shaft hole does not start at the joint's face, and refusing it there lost the diameter check - the
    ticket's own defect, on the seat shape the repo's own screw template produces."""
    import fit_checks
    plain = [(4.6, 0.0, 10.0)]
    counterbore = [(9.0, 0.0, 3.0), (4.6, 3.0, 10.0)]
    countersink = [(11.2, 0.0, 3.0), (4.6, 3.0, 10.0)]      # a cone comes back at its widest diameter
    for label, holes in (("plain", plain), ("counterbore", counterbore), ("countersink", countersink)):
        seat = fit_checks.seat_of(holes)
        assert seat is not None and seat[0] == pytest.approx(4.6), f"{label}: the seat is the shaft hole, got {seat}"
        assert fit_checks.seat_kind(seat[0], 5) is None, f"{label}: a 4.6 hole takes no M5"


def test_r3_a_hole_the_shaft_cannot_reach_is_not_the_seat():
    """The guard this replaces was there for a reason: a hole behind solid material is not a seat. With no hole at the
    face at all there is no seat; where a bore stops short of the shaft hole, the seat is the BORE (the shaft does enter
    it) and the depth probe is what then says the screw bottoms out on the material behind it."""
    import fit_checks
    assert fit_checks.seat_of([(4.6, 3.0, 10.0)]) is None
    short = fit_checks.seat_of([(9.0, 0.0, 1.0), (4.6, 3.0, 10.0)])
    assert short == (9.0, 0.0, 1.0), f"the reachable bore is the seat, not the hole behind the material: {short}"


def test_r3_a_countersink_cone_is_found_as_a_coaxial_void():
    """The lead-in of an ISO 10642 seat is a CONE, not a cylinder, so axis_holes has to report cones too."""
    B = pytest.importorskip("build123d")
    import fit_checks
    plate = B.Box(40, 40, 10, align=(B.Align.CENTER, B.Align.CENTER, B.Align.MAX))
    cone = B.Pos(0, 0, -3) * B.Cone(11.2 / 2, 4.6 / 2, 3.0, align=(B.Align.CENTER, B.Align.CENTER, B.Align.MIN))
    hole = B.Pos(0, 0, -10) * B.Cylinder(4.6 / 2, 10, align=(B.Align.CENTER, B.Align.CENTER, B.Align.MIN))
    holes = fit_checks.axis_holes(plate - cone - hole, (0, 0, 0), (0, 0, -1))
    assert len(holes) >= 2, f"the cone and the shaft hole must both be found: {holes}"
    seat = fit_checks.seat_of(holes)
    assert seat[0] == pytest.approx(4.6, abs=0.05), f"the seat is the 4.6 shaft hole: {seat}"


# ================================================================ r7: what could not be measured is not "clear"
def test_r7_a_boolean_that_does_not_compute_is_reported_as_unmeasured(tmp_path, monkeypatch):
    """r7: `_overlap` returning 0.0 on an OCC failure, with `_gap` doing the same, made the pair read as fine with no
    note anywhere - the identical shape as the ShapeList bug."""
    import fit_checks

    class Boom:
        def __and__(self, other):
            raise RuntimeError("OCC says no")

    class Geo:
        pls = {"a": ({"id": "a", "key": "ka"}, {"name": "a"}), "b": ({"id": "b", "key": "kb"}, {"name": "b"})}
        unread = {}

        def bbox(self, name):
            return ((0.0, 0.0, 0.0), (10.0, 10.0, 10.0))

        def world(self, name):
            return Boom()
    got = fit_checks._check_clash(Geo(), "a", "b", {"id": "a"}, {"id": "b"}, {"by": "x", "at": "y"})
    assert got, "a pair OCC could not boolean must be reported, not silently passed"
    assert got[0]["level"] == "WARN" and got[0].get("unread"), got
    assert "could not" in got[0]["why"], got


def test_r7_an_unmeasured_verdict_is_not_cached(tmp_path):
    """A verdict that measured nothing must not outlive the run: the next run has to try again."""
    import fit_checks
    used = set()
    v = fit_checks._cached(str(tmp_path), "deadbeef", lambda: [{"level": "WARN", "unread": True, "why": "x"}], used)
    assert v and not (Path(tmp_path) / "build" / "fit" / "deadbeef.json").exists(), \
        "an unmeasured verdict must not be written to the cache"
