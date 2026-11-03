"""The STATIC rules of `fit_checks.py` and its wiring into check.py.

Nothing here builds a model or imports build123d: `build/model/parts.json` is hand-written, which is all the static rules read
(`bought = drawn`, `bought part verified`), plus the offline mode (`compute=False`) and the no-model case. The geometric rules are
in test_fit_checks.py.

The module does not exist yet, so every test imports it inside its own body: a missing module is then a FAILING test and not a
collection error.

One convention these tests pin down, because the offline mode cannot be tested without it: `fit.json`'s `parts_sha` is the hex
sha256 of `build/model/parts.json` as it stands on disk (helpers.sha_file). test_fit_checks.py asserts the same from the writing
side, so the two ends agree.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import helpers as H
import partfiles as F
import procgroup
from samples_i18n import SAMPLES

RU = SAMPLES["ru"]          # purchase texts as a Russian project writes them

DRAWN = "bought = drawn"
VERIFIED = "bought part verified"
FASTENER = "fastener fit"
CLASH = "docked parts clash"

IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
STEP_STUB = "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"

# The mismatched-part case: a linear bearing drawn from the maker's own STEP, ordered from a marketplace under another article.
MISUMI = {"file": "vendor/misumi-LBSBL6-2028.stp", "code": "LBSBL6-2028",
          "link": "https://us.misumi-ec.com/vona2/detail/110302634480/?HissuCode=LBSBL6-2028"}
MISUMI_A184 = {"file": "vendor/misumi-A184.stp", "code": "A184",
               "link": "https://us.misumi-ec.com/vona2/detail/110302634480/?HissuCode=A184"}
SHOP_T01 = "https://shop.example/product/podshipnik-linejnyj-t01-162534879/"
SHOP_A184 = "https://shop.example/product/podshipnik-linejnyj-a184-978675645/"
CHECKED = {"against": "misumi LBSBL6-2028 datasheet (vendor/misumi-LBSBL6-2028.stp)",
           "checked": ["bore 6 h7", "flange 20 x 28", "height 9.5"], "when": "2026-10-26"}


# ---------------------------------------------------------------- a hand-written model
def drawn(pid, **fields):
    """A bought part WITH 3D: it has a STEP, so `bought = drawn` is about it."""
    key = hashlib.sha256(pid.encode()).hexdigest()
    e = {"id": pid, "name": pid, "node": "example", "func": "example.build", "process": "bought", "color": "#9da3a8",
         "key": key, "kind": "bearings", "qty": 1, "sha256": "0" * 64, "step": f"build/model/step/{key}.step",
         "placements": [{"name": pid, "node": "example", "loc": list(IDENT),
                         "joint": {"to": "frame", "at": "mount", "by": "bottom", "type": "rigid"}}]}
    e.update(fields)
    return e


def row(pid, **fields):
    """A bought part that is a row of the purchase list only: no shape, no STEP, qty per unit."""
    e = {"id": pid, "name": pid, "node": "example", "func": "example.build", "process": "bought", "color": "#999999",
         "key": hashlib.sha256(pid.encode()).hexdigest(), "kind": "profile", "qty": 3, "unit": "m"}
    e.update(fields)
    return e


def frame_root(pid="frame"):
    """The milled part the bought parts are docked to: the model's root, so the joints gate of check.py is happy."""
    key = hashlib.sha256(pid.encode()).hexdigest()
    return {"id": pid, "name": pid, "node": "example", "func": "example.build", "process": "mill", "color": "#888888",
            "key": key, "qty": 1, "sha256": "0" * 64, "step": f"build/model/step/{key}.step", "material": "S235JR",
            "placements": [{"name": pid, "node": "example", "loc": list(IDENT)}]}


def write_model(root, parts, vendor=()):
    """build/model/parts.json with `parts`, the STEP files their entries name, and the vendor files `src` points at (under the
    project root and under mechanics/cad/, so it is found whichever way a src path is read)."""
    root = Path(root)
    (root / "build" / "model").mkdir(parents=True, exist_ok=True)
    for e in parts:
        if e.get("step"):
            f = root / e["step"]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(STEP_STUB)
    for src in vendor:
        for base in (root, root / "mechanics" / "cad"):
            f = base / src["file"]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(STEP_STUB)
    (root / "build" / "model" / "parts.json").write_text(json.dumps({"parts": parts}, ensure_ascii=False, indent=1))
    (root / "build" / "model" / "nodes.json").write_text("{}")
    return root


def code_sha():
    """fit.json carries the sha of the rules that wrote it, so a corrected rule is not reported from an old report."""
    import fit_checks
    return fit_checks._code_sha()


def parts_sha(root):
    return H.sha_file(Path(root) / "build" / "model" / "parts.json")


# ---------------------------------------------------------------- calling the module under test
def fit_lines(root, rule, compute=True):
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


def unreported(root, rule, *names):
    """Assert the rule says nothing about `names`: at most its own PASS line, and that line names none of them."""
    for st, _, detail in fit_lines(root, rule):
        assert st == "PASS", f"{rule!r} must have nothing to report about {names}, it said {st}: {detail}"
        for n in names:
            assert n not in detail, f"{rule!r} must not report {n!r}: {detail}"


# ---------------------------------------------------------------- bought = drawn
def test_s1_part_drawn_as_one_article_and_ordered_as_another_fails_naming_both(tmp_path):
    """done-when 12: the STEP is misumi's LBSBL6-2028, the purchase row is a marketplace T01."""
    root = write_model(tmp_path / "p", [frame_root(), drawn("bearing", src=MISUMI, code="T01", link=SHOP_T01,
                                                            desc="Linear bearing T01", verified=CHECKED)], vendor=[MISUMI])
    st, _, detail = rule_line(root, DRAWN)
    assert st == "FAIL", f"a part drawn as LBSBL6-2028 and ordered as T01 must FAIL {DRAWN!r}, got {st}: {detail}"
    assert "bearing" in detail, f"the detail must name the part: {detail}"
    up = detail.upper()                     # the article may be spelled as the row spells it or as its link spells it
    assert "LBSBL6-2028" in up, f"the detail must name the drawn article: {detail}"
    assert "T01" in up, f"the detail must name the ordered article: {detail}"


def test_s2_part_drawn_and_ordered_as_the_same_article_passes(tmp_path):
    """done-when 13: src.code A184, and the purchase row is the A184 product."""
    root = write_model(tmp_path / "p", [frame_root(), drawn("bearing", src=MISUMI_A184, code="A184", link=SHOP_A184,
                                                            desc="Linear bearing A184", verified=CHECKED)],
                       vendor=[MISUMI_A184])
    st, _, detail = rule_line(root, DRAWN)
    assert st == "PASS", f"drawn and ordered as the same article must PASS {DRAWN!r}, got {st}: {detail}"


def test_s3_purchase_text_calling_the_item_an_analogue_of_the_drawn_one_fails(tmp_path):
    """done-when 14: the articles match, but the text says what was ordered is only an analogue of what was drawn. You may not
    draw one product and buy another."""
    root = write_model(tmp_path / "p", [
        frame_root(),
        drawn("bearing_en", src=MISUMI_A184, code="A184", link=SHOP_A184, verified=CHECKED,
              desc="Linear bearing A184, analogue of the drawn one"),
        drawn("bearing_ru", src=MISUMI_A184, code="A184", link=SHOP_A184, verified=CHECKED,
              desc=RU["linear_bearing"].format(code="A184"), note=RU["analogue_taken"]),
    ], vendor=[MISUMI_A184])
    st, _, detail = rule_line(root, DRAWN)
    assert st == "FAIL", f"an ordered analogue of the drawn part must FAIL {DRAWN!r}, got {st}: {detail}"
    assert "bearing_en" in detail, f"the detail must name the part whose desc says analogue: {detail}"
    assert "bearing_ru" in detail, f"the detail must name the part whose note says analogue in Russian: {detail}"


def test_s4_bought_part_with_a_step_and_no_src_warns_and_never_fails(tmp_path):
    """done-when 15: adoptable. Every project built before #47 has no src at all, so this may never fail a project."""
    root = write_model(tmp_path / "p", [frame_root(), drawn("bearing", code="A184", link=SHOP_A184, verified=CHECKED,
                                                            desc="Linear bearing A184")])
    st, _, detail = rule_line(root, DRAWN)
    assert st == "WARN", f"a bought part with 3D and no src is a WARN, never a FAIL, got {st}: {detail}"
    assert "bearing" in detail, f"the detail must name the part whose src is missing: {detail}"


def test_s5_bought_row_without_3d_is_not_reported(tmp_path):
    """done-when 16: a row of the purchase list has nothing drawn, so there is nothing to compare it against."""
    root = write_model(tmp_path / "p", [frame_root(), row("pipe", code="TR50", desc="Pipe 50", link=SHOP_A184,
                                                          verified=CHECKED)])
    unreported(root, DRAWN, "pipe")


# ---------------------------------------------------------------- bought part verified
def test_s6_bought_part_with_no_verified_record_warns_naming_it(tmp_path):
    """done-when 17: nobody checked the ordered part against a maker's drawing or STEP before it reached the list."""
    root = write_model(tmp_path / "p", [frame_root(), drawn("bearing", src=MISUMI_A184, code="A184", link=SHOP_A184,
                                                            desc="Linear bearing A184")], vendor=[MISUMI_A184])
    st, _, detail = rule_line(root, VERIFIED)
    assert st == "WARN", f"an unverified bought part is a WARN, got {st}: {detail}"
    assert "bearing" in detail, f"the detail must name the unverified part: {detail}"
    low = detail.lower()
    assert any(w in low for w in ("drawing", "datasheet", "step")), \
        f"the detail must say a maker's drawing or STEP has to be checked: {detail}"
    assert any(w in low for w in ("check", "verif")), f"the detail must say it must be checked: {detail}"


def test_s7_bought_part_with_a_verified_record_is_not_reported(tmp_path):
    """done-when 18: verified = {against, checked, when} is the record that someone did look."""
    root = write_model(tmp_path / "p", [frame_root(), drawn("bearing", src=MISUMI_A184, code="A184", link=SHOP_A184,
                                                            desc="Linear bearing A184", verified=CHECKED)],
                       vendor=[MISUMI_A184])
    unreported(root, VERIFIED, "bearing")


# ---------------------------------------------------------------- wiring and the offline mode
def clean_model(root):
    """A scaffolded project (it passes check.py as it stands) plus a hand-written model: one milled root and one bought screw
    with nothing for the static rules to say about it."""
    root = F.scaffold(root)
    return write_model(root, [frame_root(), drawn("screw", kind="fasteners", src=MISUMI_A184, code="A184", link=SHOP_A184,
                                                  desc="Screw A184", verified=CHECKED)], vendor=[MISUMI_A184])


def test_s8_check_py_prints_the_fit_rule_and_exits_1_when_the_offline_report_fails(tmp_path):
    """done-when 19: check.py --no-validate (the router's offline run) reads build/model/fit.json and a FAIL in it is a FAIL of
    the report and exit 1. No geometry: the report is pre-written."""
    root = clean_model(tmp_path / "p")
    before = F.run_check(root)
    assert before.returncode == 0 and not F.fail_lines(before), \
        f"precondition: the project passes check.py before the report is written: {F.fail_lines(before)}"
    (root / "build" / "model" / "fit.json").write_text(json.dumps({
        "parts_sha": parts_sha(root),
        "code_sha": code_sha(),                 # a report is current for a model AND for the rules that made it
        "fastener": [{"level": "FAIL", "parts": ["screw"],
                      "why": "M5 screw in a 4.6 mm hole: neither an M5 clearance hole nor the M5 tap drill"}],
        "clash": []}))
    cp = F.run_check(root)
    lines = F.fail_lines(cp)
    assert any(FASTENER in ln for ln in lines), f"check.py must print the {FASTENER!r} FAIL line; FAIL lines: {lines}"
    assert cp.returncode == 1, f"a fit FAIL must make check.py exit 1, got {cp.returncode}; FAIL lines: {lines}"


PROBE = r'''
import json, sys
stub, scripts, root = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, scripts)
sys.path.insert(0, stub)                    # shadows build123d with one that records the import and explodes
import fit_checks
print(json.dumps([list(r[:3]) for r in fit_checks.run(root, compute=False)]))
'''

STUB = '''import os
open(os.environ["FIT_IMPORT_MARKER"], "a").close()
raise RuntimeError("fit_checks.run(..., compute=False) must not import build123d")
'''


def test_s9_offline_mode_warns_about_a_stale_report_without_importing_build123d(tmp_path):
    """done-when 20: parts.json has changed since fit.json was written, so the geometric rules may not be reported from it; and
    compute=False may not import build123d, which a stub on sys.path proves."""
    root = write_model(tmp_path / "p", [frame_root(), drawn("screw", kind="fasteners", src=MISUMI_A184, code="A184",
                                                            link=SHOP_A184, desc="Screw A184", verified=CHECKED)],
                       vendor=[MISUMI_A184])
    (root / "build" / "model" / "fit.json").write_text(json.dumps({
        "parts_sha": "0" * 64,              # not today's parts.json: the report is stale
        "fastener": [], "clash": []}))
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "build123d.py").write_text(STUB)
    marker = tmp_path / "build123d-was-imported"
    env = dict(os.environ, FIT_IMPORT_MARKER=str(marker), PYTHONDONTWRITEBYTECODE="1")
    cp = procgroup.run([sys.executable, "-c", PROBE, str(stub), str(H.SCRIPTS), str(root)],
                       capture_output=True, text=True, env=env, timeout=300)
    out = (cp.stdout or "") + (cp.stderr or "")
    assert cp.returncode == 0, f"fit_checks.run(root, compute=False) must run with build123d unimportable:\n{out[-2500:]}"
    assert not marker.exists(), "fit_checks imported build123d although compute=False: the offline run must read fit.json only"
    lines = [tuple(x) for x in json.loads(cp.stdout.strip().splitlines()[-1])]
    assert not [l for l in lines if l[0] == "FAIL"], f"a stale report may not FAIL anything, it is not known: {lines}"
    stale = [l for l in lines if l[0] == "WARN" and l[1] in (FASTENER, CLASH)
             and any(w in l[2].lower() for w in ("stale", "out of date", "outdated", "older", "changed", "no longer"))]
    assert stale, f"a geometric rule must WARN that build/model/fit.json is stale; lines: {lines}"


def test_s10_project_without_a_built_model_reports_nothing(tmp_path):
    """done-when 21: no build/model/parts.json -> run(root) is []."""
    import fit_checks
    root = tmp_path / "p"
    (root / "mechanics" / "cad").mkdir(parents=True)
    assert not (root / "build" / "model" / "parts.json").exists(), "precondition: this project has no built model"
    assert fit_checks.run(root) == [], "a project with no built model has nothing to check"
    assert fit_checks.run(str(root)) == [], "run() must take the root as a string too, as check.py passes it"
