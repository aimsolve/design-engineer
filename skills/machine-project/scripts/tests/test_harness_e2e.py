"""Issue #113 «Wires in 3D», stage 1, end to end: a tiny project (harness_proj.py: a floor, a wall with a 12 mm slot, a board
on a standoff, a motor, a carriage with a sensor that moves 100 mm over the cycle) built by the real
`cad_incremental.py --model assemble.py`, then driven through the doors the agent and the client use:

  python harness.py <project> [--motion motion.json]   exit 1 on a problem; writes build/harness/report.json
                                                        {"wires": [...], "problems": [{"rule", "text"}]}
  python check.py <project> --no-validate               rule "harness": FAIL naming the wire(s) and the part or waypoint;
                                                        a report older than the model or connections.py is a WARN
  web-viewer build.py (cwd = project)                   build/harness/report.json, <site>/wires.json, config.json "wires"
  derive.py bought <manifest>                           cut-list rows of kind "electrical" with each wire's cut length,
                                                        a total in metres per stock
  schemes.py electric <manifest>                        build/kit/schemes/electric.pdf gains harness sheet(s)

Each BAD variant breaks one rule and nothing else; GOOD passes all (see harness_proj.py for the coordinates):
  rule 1  THROUGH  W3 runs straight through the wall            -> FAIL naming W3 and `wall`
  rule 2  GAP      W1..W3 8 mm thick bundled through the slot   -> FAIL naming a wire and `wall` / `slot`
  rule 3  BEND     W1 turns 90 deg twice within 3 mm (k1, k2)   -> FAIL naming W1 and k1 / k2
  rule 4  STRETCH  W4 to the moving sensor with a 10 mm loop    -> FAIL ("stretched") naming W4, only with --motion
  refs    BADREF   W1 via `nowhere`                             -> FAIL naming `nowhere`
Assumed besides the brief: the harness.py CLI writes build/harness/report.json too (as clearance.py does); a problem's
"rule" is one of route | bend | clash | stretch (the gap one: clash, or gap).

Slow (a model build, the CLIs): marked `slow`; a quick run skips them with -m "not slow".
    <venv with build123d 0.13.0 + python-fcl + matplotlib>/bin/python -m pytest \
        agent/skills/machine-project/scripts/tests/test_harness_e2e.py -q
"""
import json
import math
import re
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

import harness_proj as HP
import helpers as H
import text_i18n as TX

pytestmark = pytest.mark.slow

SCRIPTS = H.SCRIPTS
HARNESS = SCRIPTS / "harness.py"
CHECK = SCRIPTS / "check.py"
SCHEMES = SCRIPTS / "schemes.py"
VIEWER_BUILD = SCRIPTS.parents[1] / "web-viewer" / "templates" / "assembly" / "build.py"
ROUTED = ("W1", "W2", "W3", "W4")


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The rig after one clean model build (GOOD connections), once per module."""
    root = HP.make_project(tmp_path_factory.mktemp("harness") / "rig")
    run = HP.build_model(root)
    assert run.rc == 0, f"the rig's model build failed:\n{run.log[-2000:]}"
    return root


@pytest.fixture
def rig(built, tmp_path):
    return HP.copy_project(built, tmp_path / "rig")


def run(cmd, root, timeout=900):
    return subprocess.run([str(c) for c in cmd], cwd=str(root), env=HP.env(root), capture_output=True, text=True,
                          timeout=timeout)


def harness_cli(root, motion=True):
    return run([sys.executable, HARNESS, root] + (["--motion", root / "motion.json"] if motion else []), root)


def report(root):
    f = Path(root) / "build/harness/report.json"
    assert f.is_file(), "the harness run must write build/harness/report.json"
    rep = json.loads(f.read_text(encoding="utf-8"))
    assert isinstance(rep.get("wires"), list) and isinstance(rep.get("problems"), list), \
        f"report.json is {{wires: [...], problems: [...]}}: {sorted(rep)}"
    return rep


def routed(rep):
    return {w["id"]: w for w in rep["wires"] if isinstance(w, dict) and w.get("path")}


def problems(rep, rule=None, *names):
    """Problems of `rule` (or rules) whose text names every one of `names` (a tuple in names = any one of them)."""
    rules = {rule} if isinstance(rule, str) else set(rule or ())
    out = []
    for p in rep["problems"]:
        if rules and p.get("rule") not in rules:
            continue
        t = str(p.get("text", ""))
        if all((any(re.search(rf"\b{re.escape(x)}\b", t) for x in n) if isinstance(n, tuple)
                else re.search(rf"\b{re.escape(n)}\b", t)) for n in names):
            out.append(p)
    return out


def out_of(r):
    return (r.stdout or "") + (r.stderr or "")


def variant(rig, name):
    HP.write_connections(rig, name)
    return rig


# ================================================================ the model carries the port locations
def test_m1_parts_json_carries_ports_at_on_the_part(built):
    parts = {p["id"]: p for p in H.read_parts(built)}
    assert parts["board"].get("ports_at", {}).get("D3") == [[40, 15, 1], [1, 0, 0]], \
        f"board's ports_at reach build/model/parts.json: {parts['board'].get('ports_at')}"
    assert parts["board"]["ports_at"].get("D2") == [5, 15, 2], "a point-only port stays a point"
    assert set(parts["motor"].get("ports_at", {})) == {"A+", "A-", "VM"}, parts["motor"].get("ports_at")


# ================================================================ harness.py: the good rig
def test_g1_the_good_rig_passes_over_the_cycle_and_reports_every_routed_wire(rig):
    r = harness_cli(rig, motion=True)
    rep = report(rig)
    assert r.returncode == 0, f"GOOD breaks no rule: exit 0, got {r.returncode}; problems {rep['problems']}\n{out_of(r)[-2000:]}"
    assert rep["problems"] == [], rep["problems"]
    got = routed(rep)
    assert set(got) == set(ROUTED), f"W1..W4 have ports_at at both ends, W5 has none: routed {sorted(got)}"
    assert got["W1"]["length_mm"] == pytest.approx(140.0, abs=0.5), \
        f"W1 runs straight from board.D3 (80,100,50) through the slot to motor.A+ (220,100,50): 140 mm: {got['W1']}"
    assert 0.9 * 180.6 <= got["W4"]["length_mm"] <= 180.6 + 0.5, \
        f"W4: 70 up + 71.6 across + 39 down (a little less with rounded corners): {got['W4']['length_mm']}"
    for wid, w in got.items():
        assert w["cut_mm"] >= w["length_mm"], f"{wid}: the cut length is never shorter than the route: {w}"
    assert got["W4"]["cut_mm"] >= got["W4"]["length_mm"] + 40, f"W4's 40 mm loop is in its cut length: {got['W4']}"
    assert got["W1"]["path"][0] == pytest.approx([80, 100, 50], abs=1e-3), f"W1 starts at board.D3: {got['W1']['path'][0]}"
    assert got["W1"]["path"][-1] == pytest.approx([220, 100, 50], abs=1e-3), f"W1 ends at motor.A+: {got['W1']['path'][-1]}"


def test_g2_the_good_rig_passes_without_a_cycle_too(rig):
    r = harness_cli(rig, motion=False)
    assert r.returncode == 0, f"exit 0 without --motion: {report(rig)['problems']}\n{out_of(r)[-2000:]}"


# ================================================================ harness.py: one broken rule per variant
def test_f1_rule1_a_wire_through_the_wall_fails_naming_the_wire_and_the_wall(rig):
    r = harness_cli(variant(rig, "through"))
    rep = report(rig)
    assert r.returncode == 1, f"a wire through a part must exit 1, got {r.returncode}:\n{out_of(r)[-2000:]}"
    assert problems(rep, "clash", "W3", "wall"), \
        f"W3 runs straight through the solid wall at y = 90 (the slot is y 94..106): a clash naming W3 and wall: {rep['problems']}"
    assert re.search(r"\bW3\b", out_of(r)) and "wall" in out_of(r), f"the CLI says it too:\n{out_of(r)[-2000:]}"


def test_f2_rule2_a_bundle_thicker_than_the_slot_fails(rig):
    r = harness_cli(variant(rig, "gap"))
    rep = report(rig)
    assert r.returncode == 1, f"a bundle that does not fit the slot must exit 1:\n{out_of(r)[-2000:]}"
    assert problems(rep, ("clash", "gap"), ("W1", "W2", "W3"), ("wall", "slot")), \
        f"three 8 mm wires (a bundle >= 13.9 mm) through the 12 mm slot: a problem naming the wire(s) and wall or slot: {rep['problems']}"


def test_f3_rule3_two_right_angles_3_mm_apart_fail_the_bend(rig):
    r = harness_cli(variant(rig, "bend"))
    rep = report(rig)
    assert r.returncode == 1, f"no room to bend must exit 1:\n{out_of(r)[-2000:]}"
    assert problems(rep, "bend", "W1", ("k1", "k2")), f"W1 turns 90 deg at k1 and again 3 mm later at k2: {rep['problems']}"


def test_f4_rule4_a_wire_to_the_moving_sensor_with_a_short_loop_is_stretched(rig):
    r = harness_cli(variant(rig, "stretch"), motion=True)
    rep = report(rig)
    assert r.returncode == 1, f"a stretched wire must exit 1:\n{out_of(r)[-2000:]}"
    bad = problems(rep, "stretch", "W4")
    assert bad, f"over the cycle W4 needs 23.9 mm more than at rest, its loop is 10 mm: {rep['problems']}"
    assert any("stretch" in p["text"].lower() for p in bad), f"the sentence says it is stretched: {bad}"


def test_f5_rule4_without_the_cycle_the_same_wire_is_not_stretched(rig):
    r = harness_cli(variant(rig, "stretch"), motion=False)
    rep = report(rig)
    assert not problems(rep, "stretch"), f"nothing moves without --motion: {rep['problems']}"
    assert r.returncode == 0, f"and nothing else is wrong: exit 0:\n{out_of(r)[-2000:]}"


def test_f6_an_unknown_waypoint_fails_naming_it(rig):
    r = harness_cli(variant(rig, "badref"))
    rep = report(rig)
    assert r.returncode == 1, f"a bad reference must exit 1:\n{out_of(r)[-2000:]}"
    assert problems(rep, "route", "W1", "nowhere"), f"W1's via names `nowhere`: {rep['problems']}"


def test_f7_a_project_without_ports_at_has_nothing_to_route_and_no_problem(rig):
    """A project from before #113: WIRES with from/to/net/spec only, no WAYPOINTS, no ports_at in the model."""
    HP.write_connections(rig, "plain")
    HP.strip_ports_at(rig)
    r = harness_cli(rig)
    assert r.returncode == 0, f"no ports_at: nothing to check, exit 0:\n{out_of(r)[-2000:]}"
    rep_f = rig / "build/harness/report.json"
    if rep_f.is_file():
        rep = report(rig)
        assert routed(rep) == {} and rep["problems"] == [], rep


# ================================================================ check.py rule "harness"
LINE = re.compile(r"^(PASS|WARN|FAIL)\s+harness\b(.*)$", re.M)


def check_lines(root):
    r = run([sys.executable, CHECK, root, "--no-validate"], root)
    return LINE.findall(out_of(r)), out_of(r)


def test_k1_check_fails_on_the_report_naming_the_wire_and_the_part(rig):
    harness_cli(variant(rig, "through"))
    lines, out = check_lines(rig)
    assert lines, f"check.py reports a rule named harness:\n{out[-3000:]}"
    assert any(st == "FAIL" and re.search(r"\bW3\b", d) and "wall" in d for st, d in lines), \
        f"the harness rule is a FAIL naming W3 and the wall: {lines}"


def test_k2_check_passes_on_a_good_fresh_report(rig):
    harness_cli(rig)
    lines, out = check_lines(rig)
    assert lines and all(st == "PASS" for st, _ in lines), f"a fresh report with no problem: PASS: {lines}\n{out[-2000:]}"


def test_k3_a_report_older_than_connections_py_is_a_warn_not_a_pass(rig):
    harness_cli(variant(rig, "through"))
    assert problems(report(rig), "clash", "W3"), "precondition: a fresh report with a problem"
    HP.bump(rig / "mechanics/cad/connections.py", rig / "build/harness/report.json")
    lines, out = check_lines(rig)
    assert lines and all(st == "WARN" for st, _ in lines), \
        f"connections.py changed after the report: the report proves nothing either way, WARN: {lines}\n{out[-2000:]}"


def test_k4_a_report_older_than_the_model_is_a_warn(rig):
    harness_cli(rig)
    report(rig)
    # the model rebuilt into something else (a kit export rewrites parts.json unchanged: that is no new model)
    pj = rig / "build/model/parts.json"
    pj.write_text(pj.read_text() + "\n")
    lines, _ = check_lines(rig)
    assert lines and all(st == "WARN" for st, _ in lines), f"the model was rebuilt after the report: WARN: {lines}"


def test_k5_a_project_without_ports_at_is_silent_or_passes(tmp_path):
    """An existing project that never heard of ports_at: no new FAIL or WARN (the classic synthetic project of helpers.py)."""
    root = H.make_project(tmp_path / "plain")
    assert H.run_model(root).rc == 0
    lines, out = check_lines(root)
    assert all(st == "PASS" for st, _ in lines), f"no ports_at anywhere: the harness rule is silent or PASS: {lines}"


# ================================================================ the viewer build
def make_import(root):
    """The importer output the site build hands build.py (a stand-in: build.py only copies model.glb)."""
    src = root / "build/import_test"
    (src / "parts").mkdir(parents=True)
    (src / "model.glb").write_bytes(b"glTF-fake")
    cat = []
    for p in H.read_parts(root):
        e = {"id": p["id"], "name": p["name"], "kind": "made", "instances": 1, "files": {"step": f"parts/{p['id']}.step"}}
        if p["id"] in HP.MOVING:
            e["motion_group"] = "slide"
        cat.append(e)
        (src / "parts" / f"{p['id']}.step").write_text("ISO-10303-21;")
    (src / "parts.json").write_text(json.dumps({"assembly": {"root": "rig"}, "parts": cat}))
    return src


def viewer_build(root, motion=True):
    src = make_import(root)
    site = root / "build/site_test"
    cmd = [sys.executable, VIEWER_BUILD, "--in", src, "--out", site, "--up", "z", "--flash", "none", "--kit", "none",
           "--feedback", "none"] + (["--motion", root / "motion.json"] if motion else [])
    return run(cmd, root), site


def test_v1_the_viewer_build_writes_the_report_and_the_wires_layer(rig):
    r, site = viewer_build(rig)
    assert r.returncode == 0, f"build.py must succeed:\n{out_of(r)[-2000:]}"
    rep = report(rig)
    assert set(routed(rep)) == set(ROUTED) and rep["problems"] == [], rep
    cfg = json.loads((site / "config.json").read_text(encoding="utf-8"))
    assert cfg.get("wires") == "wires.json", f"config.json names the wires layer: {cfg}"
    assert (site / "wires.json").is_file(), "the site carries wires.json"
    text = (site / "wires.json").read_text(encoding="utf-8")
    json.loads(text)
    for wid in ROUTED:
        assert re.search(rf'"{wid}"', text), f"wires.json holds every routed wire: {wid} missing"
    lines, _ = check_lines(rig)
    assert lines and all(st == "PASS" for st, _ in lines), f"check.py reads the report the viewer build wrote: {lines}"


def test_v2_the_viewer_build_checks_the_wires_over_the_cycle_it_publishes(rig):
    r, site = viewer_build(variant(rig, "stretch"), motion=True)
    assert (rig / "build/harness/report.json").is_file(), f"the report is written even with a problem:\n{out_of(r)[-2000:]}"
    assert problems(report(rig), "stretch", "W4"), f"build.py hands its --motion cycle to the check: {report(rig)['problems']}"
    lines, _ = check_lines(rig)
    assert any(st == "FAIL" and re.search(r"\bW4\b", d) for st, d in lines), f"check.py fails on that report: {lines}"


def test_v3_a_project_without_ports_at_gets_no_wire_problems_from_the_viewer_build(tmp_path):
    root = H.make_project(tmp_path / "plain")
    assert H.run_model(root).rc == 0
    r, site = viewer_build(root, motion=False)
    assert r.returncode == 0, out_of(r)[-2000:]
    f = root / "build/harness/report.json"
    if f.is_file():
        assert report(root)["problems"] == [], "nothing routed, nothing wrong"


# ================================================================ the purchase list
def bought(root):
    d = H.run_derive(root, "bought")
    d.ok()
    return d.manifest["bought"]


def test_p1_the_purchase_list_has_a_cut_row_per_wire_and_a_total_per_stock(rig):
    harness_cli(rig)
    cut = {wid: w["cut_mm"] for wid, w in routed(report(rig)).items()}
    rows = [r for r in bought(rig) if r.get("kind") == "electrical"]
    dump = json.dumps(rows, ensure_ascii=False, indent=1)
    metre = TX.t("unit.m", TX.project_lang(rig))
    totals = [r for r in rows if r.get("unit") == metre]
    pieces = [r for r in rows if r.get("unit") != metre]
    for wid, mm in cut.items():
        named = [r for r in pieces if re.search(rf"\b{wid}\b", str(r.get("name", "")))]
        assert named, f"a cut row names {wid}:\n{dump}"
        assert any(str(round(mm)) in r["name"] or str(math.ceil(mm)) in r["name"] for r in named), \
            f"{wid}'s row gives its cut length {mm:.1f} mm:\n{dump}"
    stock = {"0.5": ["W1", "W2", "W4"], "1.0": ["W3"]}
    for spec, ids in stock.items():
        want = sum(cut[i] for i in ids) / 1000
        got = [r for r in totals if spec in str(r.get("name", "")).replace(",", ".")]
        assert len(got) == 1, f"one total in metres for the {spec} mm² stock:\n{dump}"
        assert float(got[0]["qty"]) == pytest.approx(want, abs=0.005), \
            f"the {spec} mm² total is the sum of its cut lengths, {want:.3f} m:\n{dump}"
    assert not any(re.search(r"\bW5\b", str(r.get("name", ""))) for r in rows), f"W5 is not routed: it has no cut length:\n{dump}"


def test_p2_a_project_without_ports_at_gets_no_wire_rows(dproj):
    rows = bought(dproj)
    assert not any(re.search(r"\bW\d+\b", str(r.get("name", ""))) for r in rows), json.dumps(rows, ensure_ascii=False)


# ================================================================ the harness drawing in the electric scheme
def _pdf_unescape(b):
    out, i = bytearray(), 0
    while i < len(b):
        c = b[i]
        if c == 0x5C and i + 1 < len(b):
            n = b[i + 1]
            if n in b"()\\":
                out.append(n); i += 2; continue
            m = re.match(rb"[0-7]{1,3}", b[i + 1:i + 4])
            if m:
                out.append(int(m.group(0), 8) & 0xFF); i += 1 + len(m.group(0)); continue
            out.append({ord("n"): 10, ord("r"): 13, ord("t"): 9}.get(n, n)); i += 2; continue
        out.append(c); i += 1
    return bytes(out)


def _decode(s):
    s = _pdf_unescape(s)
    if len(s) >= 2 and len(s) % 2 == 0 and all(s[k] == 0 for k in range(0, len(s), 2)):
        return s.decode("utf-16-be", "replace")
    return s.decode("latin-1")


def pdf_pages(pdf):
    """Text of each page: pymupdf, else pypdf, else the text runs of the (matplotlib-written) content streams."""
    try:
        import fitz
        with fitz.open(pdf) as doc:
            return [p.get_text() for p in doc]
    except ImportError:
        pass
    try:
        import pypdf
        return [p.extract_text() or "" for p in pypdf.PdfReader(str(pdf)).pages]
    except ImportError:
        pass
    raw = Path(pdf).read_bytes()
    assert raw.startswith(b"%PDF"), f"{pdf} is not a PDF"
    objs = {int(m.group(1)): m.group(2) for m in re.finditer(rb"(\d+) 0 obj(.*?)endobj", raw, re.S)}

    def stream(n):
        m = re.search(rb"stream\r?\n(.*?)\r?\nendstream", objs.get(n, b""), re.S)
        if not m:
            return b""
        try:
            return zlib.decompress(m.group(1))
        except zlib.error:
            return m.group(1)
    pages = []
    for n, body in sorted(objs.items()):
        if re.search(rb"/Type\s*/Page(?![s\w])", body):
            refs = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+ 0 R)", body)
            ids = [int(x) for x in re.findall(rb"(\d+) 0 R", refs.group(1))] if refs else []
            txt = b"".join(stream(i) for i in ids)
            runs = []
            for arr in re.findall(rb"\[((?:[^\[\]\\]|\\.)*)\]\s*TJ", txt, re.S):
                runs.append("".join(_decode(s) for s in re.findall(rb"\(((?:[^()\\]|\\.)*)\)", arr, re.S)))
            runs += [_decode(s) for s in re.findall(rb"\(((?:[^()\\]|\\.)*)\)\s*Tj", txt, re.S)]
            pages.append("\n".join(runs))
    assert pages, f"no page found in {pdf}"
    return pages


def electric(root):
    r = run([sys.executable, SCHEMES, "electric", "build/kit/electric.json"], root)
    assert r.returncode == 0, f"schemes.py electric must succeed:\n{out_of(r)[-2000:]}"
    pdf = root / "build/kit/schemes/electric.pdf"
    assert pdf.is_file(), "the electric scheme PDF is written"
    return pdf_pages(pdf)


def test_h1_the_electric_pdf_gains_harness_sheets_with_routes_connectors_and_cut_lengths(rig, tmp_path):
    harness_cli(rig)
    cut = {wid: w["cut_mm"] for wid, w in routed(report(rig)).items()}
    pages = electric(rig)

    plain = HP.copy_project(rig, tmp_path / "plain")       # the same wires before #113 (no routes): the sheets it had before
    HP.write_connections(plain, "plain")
    HP.strip_ports_at(plain)
    shutil.rmtree(plain / "build/harness", ignore_errors=True)
    before = electric(plain)
    assert len(pages) > len(before), f"the harness sheet(s) are added: {len(pages)} pages with routes vs {len(before)} without"

    def has_len(t, mm):
        return any(re.search(rf"(?<![\d.,]){v}(?![\d])", t) for v in {str(round(mm)), str(math.ceil(mm)), str(math.floor(mm))})
    sheets = [t for t in pages if sum(has_len(t, mm) for mm in cut.values()) >= 2]
    assert sheets, f"some page lists the cut lengths {cut}; page texts: {[t[:300] for t in pages]}"
    alltext = "\n".join(sheets)
    for wid, mm in cut.items():
        assert re.search(rf"\b{wid}\b", alltext) and has_len(alltext, mm), \
            f"the harness sheets name {wid} with its cut length {mm:.1f} mm"
    for end in ("board.D3", "motor.A+", "sensor.OUT", "board.D2"):
        assert end in alltext.replace(" ", ""), f"the harness sheets show the connector {end} at its wire's end"
