"""Issue #19: the overlap (interference) check built into the model build - the new `clearance.py`.

Written before the module existed; every test imports it inside its own body (through `report()` / `run_cli()`), so a missing
module is a FAILING test and not a collection error.

Contract under test:
  clearance.run(root, cycle=None, groups=None) -> {clashes: [{a, b, volume_mm3, t_s, window_s, at_mm}], measured, cached, ...}
      and <root>/build/clearance/report.json with the same content.
  python clearance.py <project> [--motion motion.json] [--groups groups.json]
      one line per clash naming both parts, the volume in mm3 and the time; exit 1 on any clash, 0 otherwise.

The model is hand-written (build/model/parts.json + one own-frame STEP per part, like test_export_robot.py), independent of
cad_incremental, so these tests fail only for clearance reasons. World mm; every box is Align.MIN at its placement:

    base           root      400 x 100 x 10   x[0,400]   y[0,100]  z[-10,0]
    strut_a/b      -> base   20^3 each, cross by x[20,30] y[20,30] z[10,20] = 1000 mm3            -> clash (rule a)
    nib_a/b        -> base   10^3 each, cross by 1 x 1 x 1 = 1 mm3                                 -> NOT a clash (<= 2 mm3)
    touch_a/b      -> base   20^3 side by side, share the face x = 100                            -> NOT a clash (rule b)
    dock_a/b       dock_b -> dock_a, sunk 2 mm into it: 20 x 20 x 2 = 800 mm3                    -> NOT here (rule c)
    plate_b        -> base   40 x 40 x 10 at x[170,210] y[10,50] z[0,10], NO hole
    plate_a        -> plate_b  40 x 40 x 5 on top of it, a 6.6 through hole, joint `hole` on its top face (Z out)
    screw_through  -> plate_a by `head`: M6x12, head r5 x 6 above the face, shank r3 x 12 below: the shank runs on
                   through plate_a's hole into plate_b (~198 mm3 along its own axis)                -> NOT a clash (rule d)
    plate_a2       -> base   the same holed plate at x[230,270] z[0,5]
    screw_sunk     -> plate_a2 by `head`: M6x12; its shank goes into base (along its axis: fine) and its HEAD z[5,11]
                   is sunk into cover_c                                                             -> clash with cover_c (rule d)
    cover_c        -> base   20 x 20 x 12 at x[240,260] y[20,40] z[8,20]: head overlap ~236 mm3
    slider         -> base   10^3 at x[300,310] y[60,70] z[0,10], group `slide`: +X 60 mm over t 0..1 s, period 1.5 s
    post           -> base   6 x 20 x 20 at x[328,334] y[55,75] z[0,20]: the slider passes THROUGH it only for
                   f in (0.3, 0.567), i.e. t in (0.3, 0.567); clear at t = 0 and at the end            -> clash in motion (rule e)
                   and nothing with cycle=None (rule f)

    python -m pytest agent/skills/machine-project/scripts/tests/test_clearance.py -q   (needs build123d 0.13.0 + python-fcl)
"""
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
CLEARANCE = SCRIPTS / "clearance.py"

CLASH_V = 2.0
PERIOD = 1.5
CYCLE = {"period_s": PERIOD,
         "groups": {"slide": {"kind": "linear", "axis": "x", "travel_mm": 60.0, "segments": [[0, 1, 0, 1]]}}}
GROUPS = {"slider": "slide"}
# the slider's box x[300+60t, 310+60t] crosses the post's x[328,334] for t in (0.3, 0.5667); a sampled run may miss the very
# edges, so the window is checked with this much slack on each side
T_IN, T_OUT, T_SLACK = 0.30, 34.0 / 60.0, 0.06

IDENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
HOLE_JOINT = {"type": "rigid", "loc": [1, 0, 0, 20, 0, 1, 0, 20, 0, 0, 1, 5, 0, 0, 0, 1]}   # plate top face, hole centre, Z out
SCREW = {"process": "bought", "kind": "fasteners", "title": "Socket head cap screw M6x12 DIN 912",
         "code": "M6x12", "joints": {"head": {"type": "rigid", "loc": list(IDENT)}}}


# ---------------------------------------------------------------- the synthetic model
def T(x, y, z):
    return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]


def shape_of(kind, size=None):
    from build123d import Align, Box, Cylinder, Pos
    mn, cz = (Align.MIN, Align.MIN, Align.MIN), (Align.CENTER, Align.CENTER, Align.MIN)
    if kind == "box":
        return Box(*size, align=mn)
    if kind == "holed_plate":                        # 40 x 40 x 5 with a 6.6 mm (M6 clearance) hole through (20, 20)
        return Box(40, 40, 5, align=mn) - Pos(20, 20, -5) * Cylinder(3.3, 15, align=cz)
    if kind == "screw":                              # head above the joint (z 0..6), shank below it (z -12..0)
        return Cylinder(5, 6, align=cz) + Pos(0, 0, -12) * Cylinder(3, 12, align=cz)
    if kind == "notched_strut":                      # strut_b with a 5 x 5 x 10 notch INSIDE the strut_a overlap: same box
        return Box(20, 20, 20, align=mn) - Box(5, 5, 10, align=mn)
    raise ValueError(kind)


# name -> (shape, world translation, docked to, extra part fields)
MODEL = {
    "base":          (("box", (400, 100, 10)), (0, 0, -10), None, {"joints": {"top": {"type": "rigid", "loc": T(0, 0, 10)}}}),
    "strut_a":       (("box", (20, 20, 20)), (10, 10, 0), "base", {}),
    "strut_b":       (("box", (20, 20, 20)), (20, 20, 10), "base", {}),
    "nib_a":         (("box", (10, 10, 10)), (50, 10, 0), "base", {}),
    "nib_b":         (("box", (10, 10, 10)), (59, 19, 9), "base", {}),
    "touch_a":       (("box", (20, 20, 20)), (80, 10, 0), "base", {}),
    "touch_b":       (("box", (20, 20, 20)), (100, 10, 0), "base", {}),
    "dock_a":        (("box", (20, 20, 20)), (130, 10, 0), "base", {"joints": {"top": {"type": "rigid", "loc": T(0, 0, 18)}}}),
    "dock_b":        (("box", (20, 20, 20)), (130, 10, 18), "dock_a", {}),
    "plate_b":       (("box", (40, 40, 10)), (170, 10, 0), "base", {"joints": {"top": {"type": "rigid", "loc": T(0, 0, 10)}}}),
    "plate_a":       (("holed_plate", None), (170, 10, 10), "plate_b", {"joints": {"hole": HOLE_JOINT}}),
    "screw_through": (("screw", None), (190, 30, 15), "plate_a", SCREW),
    "plate_a2":      (("holed_plate", None), (230, 10, 0), "base", {"joints": {"hole": HOLE_JOINT}}),
    "screw_sunk":    (("screw", None), (250, 30, 5), "plate_a2", SCREW),
    "cover_c":       (("box", (20, 20, 12)), (240, 20, 8), "base", {}),
    "slider":        (("box", (10, 10, 10)), (300, 60, 0), "base", {}),
    "post":          (("box", (6, 20, 20)), (328, 55, 0), "base", {}),
}

# the model with nothing that clashes, for the CLI's exit 0
CLEAN = [n for n in MODEL if n not in ("strut_b", "cover_c", "post")]


def write_step(root, name, kind, size):
    """The part in its own frame -> build/model/step/<key>.step; key = hash of the STEP bytes and the name."""
    from build123d import export_step
    d = Path(root) / "build/model/step"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"_tmp_{name}.step"
    export_step(shape_of(kind, size), str(tmp))
    key = hashlib.sha256(tmp.read_bytes() + name.encode()).hexdigest()
    tmp.replace(d / f"{key}.step")
    return key


def part_entry(name, key, at, to, extra):
    e = {"id": name, "name": name, "node": "m", "func": "m.build", "process": "mill", "color": "#999999", "key": key,
         "qty": 1, "material": "S235JR", "step": f"build/model/step/{key}.step"}
    e.update(extra)
    pl = {"name": name, "node": "m", "loc": T(*at)}
    if to:
        screw = e.get("kind") == "fasteners"
        pl["joint"] = {"to": to, "at": "hole" if screw else "top", "by": "head" if screw else "bottom", "type": "rigid"}
    e["placements"] = [pl]
    return e


def write_model(root, names=None):
    root = Path(root)
    parts = []
    for name in names or MODEL:
        (kind, size), at, to, extra = MODEL[name]
        parts.append(part_entry(name, write_step(root, name, kind, size), at, to, extra))
    (root / "build/model/parts.json").write_text(json.dumps({"parts": parts}, ensure_ascii=False, indent=1))
    (root / "build/model/nodes.json").write_text("{}")
    return root


def write_motion(root):
    (Path(root) / "motion.json").write_text(json.dumps({"cycle": CYCLE}, indent=1))
    (Path(root) / "groups.json").write_text(json.dumps(GROUPS))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The full model, written once for the module (no clearance run yet)."""
    root = write_model(tmp_path_factory.mktemp("clr") / "proj")
    write_motion(root)
    return root


@pytest.fixture
def proj(built, tmp_path):
    """A private copy of the full model with no build/clearance/ yet, for the tests that run clearance themselves."""
    dst = tmp_path / "proj"
    shutil.copytree(built, dst, ignore=shutil.ignore_patterns("clearance"))
    shutil.rmtree(dst / "build/clearance", ignore_errors=True)
    return dst


# ---------------------------------------------------------------- calling the module under test
def run(root, cycle=None, groups=None):
    import clearance                     # in the body on purpose: a missing module must FAIL the test, not error collection
    rep = clearance.run(Path(root), cycle=cycle, groups=groups)
    assert isinstance(rep, dict), f"clearance.run must return a dict, got {rep!r}"
    for k in ("clashes", "measured", "cached"):
        assert k in rep, f"the report needs {k!r}: {sorted(rep)}"
    assert isinstance(rep["clashes"], list), rep
    for c in rep["clashes"]:
        assert isinstance(c, dict) and {"a", "b", "volume_mm3", "t_s", "window_s", "at_mm"} <= set(c), \
            f"every clash carries a, b, volume_mm3, t_s, window_s, at_mm: {c!r}"
    return rep


_FULL = {}


def full_report(root):
    """The full model checked over the cycle, computed once per module (every read-only test shares it)."""
    if root not in _FULL:
        _FULL[root] = run(root, cycle=CYCLE, groups=GROUPS)
    return _FULL[root]


def n(x):
    """`measured` / `cached` as a count: an int, or a list of the pairs."""
    return len(x) if isinstance(x, (list, tuple)) else int(x)


def clash(rep, a, b):
    got = [c for c in rep["clashes"] if {c["a"], c["b"]} == {a, b}]
    assert len(got) <= 1, f"the pair {a}/{b} is reported more than once: {got}"
    return got[0] if got else None


def pairs(rep):
    return sorted(tuple(sorted((c["a"], c["b"]))) for c in rep["clashes"])


def inside(pt, lo, hi, tol=0.5):
    return len(pt) == 3 and all(lo[i] - tol <= float(pt[i]) <= hi[i] + tol for i in range(3))


def run_cli(root, *args):
    return subprocess.run([sys.executable, str(CLEARANCE), str(root), *map(str, args)], capture_output=True, text=True,
                          timeout=600, cwd=str(root))


# ---------------------------------------------------------------- rule a: an overlap > 2 mm3 is a clash
def test_a1_two_static_parts_crossing_by_1000_mm3_are_a_clash_with_volume_and_point(built):
    rep = full_report(built)
    c = clash(rep, "strut_a", "strut_b")
    assert c, f"strut_a and strut_b overlap by 10 x 10 x 10 mm and must be a clash; clashes: {pairs(rep)}"
    assert c["volume_mm3"] == pytest.approx(1000.0, rel=0.02), f"the overlap is 1000 mm3: {c}"
    assert c["t_s"] is None and c["window_s"] is None, f"neither part moves: no time, no window: {c}"
    assert inside(c["at_mm"], (20, 20, 10), (30, 30, 20)), f"at_mm must lie inside the overlap x[20,30] y[20,30] z[10,20]: {c}"


def test_a2_an_overlap_of_1_mm3_is_not_a_clash(built):
    rep = full_report(built)
    assert clash(rep, "nib_a", "nib_b") is None, f"1 mm3 is under the {CLASH_V} mm3 threshold: {clash(rep, 'nib_a', 'nib_b')}"


def test_a3_exactly_the_three_designed_clashes_are_reported(built):
    """Everything else in the model touches, is docked, or is a screw along its own axis."""
    rep = full_report(built)
    want = sorted([("strut_a", "strut_b"), ("cover_c", "screw_sunk"), ("post", "slider")])
    assert pairs(rep) == want, f"clashes reported {pairs(rep)}, the model holds exactly {want}"


def test_a4_report_json_is_written_with_the_same_content(built):
    rep = full_report(built)
    f = built / "build/clearance/report.json"
    assert f.is_file(), "run() must write build/clearance/report.json"
    disk = json.loads(f.read_text(encoding="utf-8"))
    assert disk.get("clashes") == json.loads(json.dumps(rep["clashes"])), "report.json must hold the clashes run() returned"
    for k in ("measured", "cached"):
        assert k in disk, f"report.json must carry {k!r}: {sorted(disk)}"


# ---------------------------------------------------------------- rule b: touching is not a clash
def test_b1_parts_sharing_a_face_are_not_a_clash(built):
    rep = full_report(built)
    assert clash(rep, "touch_a", "touch_b") is None, f"a shared face is contact, not overlap: {clash(rep, 'touch_a', 'touch_b')}"


# ---------------------------------------------------------------- rule c: docked pairs belong to fit_checks
def test_c1_a_docked_pair_is_never_reported_even_when_it_overlaps(built):
    rep = full_report(built)
    assert clash(rep, "dock_a", "dock_b") is None, \
        f"dock_b is docked to dock_a (fit_checks owns that pair), so its 800 mm3 must not be reported here: {rep['clashes']}"
    assert clash(rep, "plate_a2", "screw_sunk") is None, "a screw and the plate it is docked into are a docked pair"


# ---------------------------------------------------------------- rule d: a screw along its own axis
def test_d1_a_screw_shank_running_on_into_the_plate_behind_is_not_a_clash(built):
    rep = full_report(built)
    got = clash(rep, "plate_b", "screw_through")
    assert got is None, f"screw_through's shank goes on through plate_a into plate_b along its own axis: not a clash, got {got}"
    assert clash(rep, "base", "screw_sunk") is None, f"screw_sunk's shank in base is along its axis too: {rep['clashes']}"


def test_d2_a_screw_head_sunk_into_another_part_is_a_clash(built):
    rep = full_report(built)
    c = clash(rep, "screw_sunk", "cover_c")
    assert c, f"screw_sunk's head (z 5..11) is sunk 3 mm into cover_c (z 8..20): a clash; clashes {pairs(rep)}"
    assert c["volume_mm3"] == pytest.approx(math.pi * 25 * 3, rel=0.08), f"the head overlap is about 236 mm3: {c}"
    x, y, z = (float(v) for v in c["at_mm"])
    assert 8 - 0.5 <= z <= 11 + 0.5 and math.hypot(x - 250, y - 30) <= 5.5, \
        f"at_mm must lie in the head inside cover_c (z 8..11, within 5 mm of the axis at 250, 30): {c}"


# ---------------------------------------------------------------- rule e: a clash in the middle of the travel
def test_e1_a_moving_part_through_a_static_one_mid_travel_is_a_clash_inside_the_travel(built):
    rep = full_report(built)
    c = clash(rep, "slider", "post")
    assert c, f"the slider passes through the post for t in ({T_IN}, {T_OUT:.3f}) s: a clash; clashes {pairs(rep)}"
    assert c["t_s"] is not None and c["window_s"] is not None, f"a clash in motion carries its time and window: {c}"
    t0, t1 = (float(v) for v in c["window_s"])
    assert T_IN - T_SLACK <= t0 <= T_IN + T_SLACK, f"the overlap starts at about {T_IN} s, not {t0}: {c}"
    assert T_OUT - T_SLACK <= t1 <= T_OUT + T_SLACK, f"the overlap ends at about {T_OUT:.3f} s, not {t1}: {c}"
    assert t0 <= float(c["t_s"]) <= t1, f"t_s lies inside its window: {c}"
    assert float(c["t_s"]) > 0.0, f"the parts are clear at t = 0, so the clash is not at 0: {c}"
    assert CLASH_V < c["volume_mm3"] <= 600 * 1.03, f"the overlap is at most the post's 6 x 10 x 10 inside the slider: {c}"
    assert inside(c["at_mm"], (328, 60, 0), (334, 70, 10)), f"at_mm must lie inside the post where the slider crosses it: {c}"


# ---------------------------------------------------------------- rule f: without a cycle, only the model pose
def test_f1_without_a_cycle_the_moving_pair_is_not_reported(proj):
    rep = run(proj, cycle=None, groups=None)
    assert clash(rep, "slider", "post") is None, f"at the model pose the slider is 18 mm clear of the post: {rep['clashes']}"
    c = clash(rep, "strut_a", "strut_b")
    assert c and c["t_s"] is None, f"the static clash is still found without a cycle: {rep['clashes']}"


# ---------------------------------------------------------------- rule g: incremental
def test_g1_an_identical_second_run_measures_nothing(proj):
    cold = run(proj, cycle=CYCLE, groups=GROUPS)
    assert n(cold["measured"]) >= 2, f"a cold run measures its pairs: {cold['measured']}"
    warm = run(proj, cycle=CYCLE, groups=GROUPS)
    assert n(warm["measured"]) == 0, f"nothing changed, so nothing is measured again: measured {warm['measured']}"
    assert n(warm["cached"]) == n(cold["measured"]), \
        f"every pair the cold run measured comes from the cache: cached {warm['cached']}, cold measured {cold['measured']}"
    assert pairs(warm) == pairs(cold), "the cached run reports the same clashes"


def test_g2_a_changed_part_step_remeasures_only_its_own_pairs(proj):
    cold = run(proj, cycle=CYCLE, groups=GROUPS)
    total = n(cold["measured"]) + n(cold["cached"])
    # strut_b gets a 5 x 5 x 10 notch inside the overlap: same bounding box, overlap 1000 -> 750 mm3, a new STEP and key
    key = write_step(proj, "strut_b_v2", "notched_strut", None)
    pj = proj / "build/model/parts.json"
    data = json.loads(pj.read_text())
    for p in data["parts"]:
        if p["id"] == "strut_b":
            p["key"], p["step"] = key, f"build/model/step/{key}.step"
    pj.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    rep = run(proj, cycle=CYCLE, groups=GROUPS)
    m, k = n(rep["measured"]), n(rep["cached"])
    assert m >= 1, "strut_b changed: its pairs must be measured again"
    assert m < total and k >= 1, f"only strut_b's pairs are measured again, the rest come from the cache: measured {m}, cached {k}"
    assert m + k == total, f"the same pairs as before, split into measured and cached: {m} + {k} != {total}"
    if isinstance(rep["measured"], (list, tuple)):
        for pr in rep["measured"]:
            assert "strut_b" in json.dumps(pr), f"a re-measured pair that does not involve strut_b: {pr}"
    c = clash(rep, "strut_a", "strut_b")
    assert c and c["volume_mm3"] == pytest.approx(750.0, rel=0.02), \
        f"the clash must carry the NEW overlap (750 mm3), not a stale cached one: {c}"


# ---------------------------------------------------------------- the CLI
def nums(line):
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", line)]


def test_cli1_prints_one_line_per_clash_and_exits_1(proj):
    r = run_cli(proj, "--motion", proj / "motion.json", "--groups", proj / "groups.json")
    out = r.stdout + r.stderr
    assert r.returncode == 1, f"clashes must exit 1, got {r.returncode}:\n{out[-3000:]}"
    lines = out.splitlines()
    strut = [ln for ln in lines if "strut_a" in ln and "strut_b" in ln]
    assert strut, f"a line must name both strut_a and strut_b:\n{out[-3000:]}"
    assert any(990 <= v <= 1010 for ln in strut for v in nums(ln)), f"the strut line must give the volume, 1000 mm3: {strut}"
    slide = [ln for ln in lines if "slider" in ln and "post" in ln]
    assert slide, f"a line must name both slider and post:\n{out[-3000:]}"
    assert any(T_IN - T_SLACK <= v <= T_OUT + T_SLACK for ln in slide for v in nums(ln)), \
        f"the slider line must give the time of the clash (about 0.3..0.57 s): {slide}"
    assert any("cover_c" in ln and "screw_sunk" in ln for ln in lines), f"a line must name screw_sunk and cover_c:\n{out[-3000:]}"
    assert (proj / "build/clearance/report.json").is_file(), "the CLI writes build/clearance/report.json too"


def test_cli2_a_model_with_no_clash_exits_0(tmp_path):
    root = write_model(tmp_path / "clean", CLEAN)
    write_motion(root)
    r = run_cli(root, "--motion", root / "motion.json", "--groups", root / "groups.json")
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"touching, docked and screw-axis overlaps only: exit 0, got {r.returncode}:\n{out[-3000:]}"
    rep = json.loads((root / "build/clearance/report.json").read_text(encoding="utf-8"))
    assert rep.get("clashes") == [], f"no clash in the clean model: {rep.get('clashes')}"


def test_h1_a_product_part_is_not_checked(proj):
    """`product: true` (what the machine handles, drawn at one instant of its travel) is left out, as derive.py leaves it out
    of the kits: the same 1000 mm3 strut overlap is no clash once strut_b is the product."""
    f = proj / "build/model/parts.json"
    model = json.loads(f.read_text())
    for p in model["parts"]:
        if p["id"] == "strut_b":
            p["product"] = True
    f.write_text(json.dumps(model))
    rep = run(proj, CYCLE, GROUPS)
    assert clash(rep, "strut_a", "strut_b") is None, f"a product part is not checked: {pairs(rep)}"
    assert clash(rep, "slider", "post"), f"the machine's own clashes are still reported: {pairs(rep)}"


def _add_placement(root, part_id, name, at):
    f = Path(root) / "build/model/parts.json"
    model = json.loads(f.read_text())
    for p in model["parts"]:
        if p["id"] == part_id:
            p["placements"].append({"name": name, "node": "m", "loc": T(*at),
                                    "joint": {"to": "base", "at": "top", "by": "bottom", "type": "rigid"}})
    f.write_text(json.dumps(model))


def test_h2_a_fixed_repeat_of_a_moving_part_stays_fixed(proj):
    """The viewer's groups go by exact placement name: `slider_2`, the same part fixed on top of the slider's start, must not
    ride with `slider` (the `_2` -> base-name fallback is node_groups' only) - riding, it would sweep through the post."""
    _add_placement(proj, "slider", "slider_2", (300, 60, 10))
    rep = run(proj, CYCLE, GROUPS)
    assert clash(rep, "slider_2", "post") is None, f"slider_2 is fixed: it never reaches the post: {pairs(rep)}"
    assert clash(rep, "slider", "post"), f"the moving slider still clashes: {pairs(rep)}"


def test_h3_a_part_wholly_inside_another_is_a_clash(proj):
    """No surface crosses when one part sits wholly inside another: the boxes say so, the boolean measures it."""
    _add_placement(proj, "nib_a", "nib_inside", (12, 12, 2))      # 10 mm cube inside strut_a x[10,30] y[10,30] z[0,20]
    rep = run(proj, CYCLE, GROUPS)
    c = clash(rep, "strut_a", "nib_inside")
    assert c and c["volume_mm3"] == pytest.approx(1000.0, rel=0.02), f"nib_inside lies wholly in strut_a: {pairs(rep)}"


def test_h4_cycle_clearance_runs_in_between_do_not_wipe_the_cache(proj):
    """cycle_clearance.py keeps build/clearance/pairs.json (its documented --out); the build's check keeps its own file, so
    an agent's clearance run between two builds does not make the next build measure everything again."""
    write_motion(proj)
    run(proj, CYCLE, GROUPS)
    cli = Path(__file__).resolve().parents[3] / "sim/scripts/cycle_clearance.py"
    r = subprocess.run([sys.executable, str(cli), "--model", "build/model/parts.json", "--motion", "motion.json",
                        "--groups", "groups.json"], cwd=str(proj), capture_output=True, text=True, timeout=600)
    assert "measured" in r.stdout, r.stdout + r.stderr
    rep = run(proj, CYCLE, GROUPS)
    assert n(rep["measured"]) == 0, f"nothing changed since the last check: {rep['measured']} measured"


def test_h5_a_part_moving_through_a_screw_shank_is_a_clash(proj):
    """The thread exemption is for parts at rest against the screw: a bar of the slider's group sweeping through the shank of
    screw_through (inside plates a and b, below its head) mid-stroke is a clash, not a thread."""
    _add_placement(proj, "nib_a", "sweeper", (150, 25, 3))        # 10 mm cube, z[3,13]: under the head (z 15..21), on the shank
    rep = run(proj, CYCLE, {**GROUPS, "sweeper": "slide"})
    c = clash(rep, "sweeper", "screw_through")
    assert c and c["t_s"], f"the sweeper cuts through the screw's shank mid-stroke: {pairs(rep)}"
