"""Crank links that the viewer moves by SAMPLED POSE TRACKS are moved the same way by the
overlap check.

Track format (world data JSON key `CRANK_TRACKS`, what a machine's world module applies on its page):
    {"n": N, "period": P, "nodes": {placement name: [12 * N floats]}}
sample k = row-major 3x4 matrix (rows r0 r1 r2, CAD frame, mm) applied to the node's home pose; it REPLACES the node's
cycle-group motion. At cycle time t the viewer shows sample k = floor(((t mod P) / P) * N + 0.5) mod N.

Public surface under test:
    clearance.run(root, cycle, groups, tracks=None)
    build.py --motion M --world W --world-data D   (D holds CRANK_TRACKS) -> <out>/clearance.json

Written from the contract, not from the code. Every call imports clearance inside its body (as test_clearance.py does).
The tracks here are pure translations, so "applied to the home pose" has one meaning whichever side it multiplies.

World mm (boxes Align.MIN at their placement; base top at z = 0):
    base     400 x 100 x 10 at z[-10,0]
    pin      cylinder r3 x 20, axis at (50, 50), z[0,20]; group `slide` (+X 60 mm, out on t 0..0.6, back on t 0.9..1.5)
    rod_end  80 x 16 x 6 at x[42,122] y[42,58] z[7,13], an r4 eye through (50, 50): the pin sits in the eye at home and
             runs into the rod end's body once it moves -> today (static rod_end) a clash; tracked with the slider: clear
    ram      10^3 at x[200,210] y[20,30] z[0,10], no group; its track pushes it +Y 0 -> 20 -> 0 over the period
    wall     20 x 6 x 20 at x[195,215] y[40,46] z[0,20]: the ram is in it while its offset > 10 mm: t in (0.375, 1.125)
    pin2     the pin's shape at (300, 50) in group `slide`, with a track that holds it at home
    post2    6 x 20 x 20 at x[330,336] y[40,60] z[0,20]: pin2 would cross it by its GROUP motion; the track replaces that

    cd agent/skills/machine-project/scripts/tests && <venv>/bin/python -m pytest -q test_clearance_tracks.py
    (needs build123d 0.13.0 + python-fcl)
"""
import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
BUILD = SCRIPTS.parents[1] / "web-viewer/templates/assembly/build.py"

PERIOD = 1.5
TRAVEL = 60.0
SEGMENTS = [[0, 0.6, 0, 1], [0.9, 1.5, 1, 0]]     # linear inside a segment, holds between (web-viewer SKILL.md)
CYCLE = {"period_s": PERIOD, "loop": True,
         "groups": {"slide": {"kind": "linear", "axis": "x", "travel_mm": TRAVEL, "segments": SEGMENTS}}}
GROUPS = {"pin": "slide", "pin2": "slide"}
N = 720
RAM_PUSH = 20.0
RAM_IN, RAM_OUT, SLACK = 0.375, 1.125, 0.06


def frac(t):
    """The group's fraction at cycle time t, per the documented segment rule (the cycle wraps)."""
    t = t % PERIOD
    last = SEGMENTS[-1][3]
    for t0, t1, f0, f1 in SEGMENTS:
        if t < t0:
            return last
        if t <= t1:
            return f0 + (f1 - f0) * (t - t0) / (t1 - t0)
        last = f1
    return last


def tri(t):
    """0 -> 1 -> 0 over the period, linear."""
    u = (t % PERIOD) / PERIOD
    return 2 * u if u <= 0.5 else 2 * (1 - u)


def track(offset):
    """12 * N floats: sample k (shown at t = k * P / N) is a translation by offset(t_k)."""
    out = []
    for k in range(N):
        dx, dy, dz = offset(k * PERIOD / N)
        out += [1, 0, 0, dx, 0, 1, 0, dy, 0, 0, 1, dz]
    return out


def tracks(**nodes):
    return {"n": N, "period": PERIOD, "nodes": nodes}


WITH_SLIDER = lambda t: (TRAVEL * frac(t), 0.0, 0.0)          # noqa: E731  the rod end rides with the pin
RAM_IN_WALL = lambda t: (0.0, RAM_PUSH * tri(t), 0.0)         # noqa: E731
AT_HOME = lambda t: (0.0, 0.0, 0.0)                           # noqa: E731


# ---------------------------------------------------------------- the synthetic model
def T(x, y, z):
    return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]


def shape_of(kind, size=None):
    from build123d import Align, Box, Cylinder, Pos
    mn, cz = (Align.MIN, Align.MIN, Align.MIN), (Align.CENTER, Align.CENTER, Align.MIN)
    if kind == "box":
        return Box(*size, align=mn)
    if kind == "pin":                                  # axis at the local origin
        return Cylinder(3, 20, align=cz)
    if kind == "rod_end":                              # 80 x 16 x 6, eye r4 through local (8, 8)
        return Box(80, 16, 6, align=mn) - Pos(8, 8, -5) * Cylinder(4, 16, align=cz)
    raise ValueError(kind)


# name -> (shape, world translation, motion_group in the viewer's parts.json)
MODEL = {
    "base":    (("box", (400, 100, 10)), (0, 0, -10), None),
    "pin":     (("pin", None), (50, 50, 0), "slide"),
    "rod_end": (("rod_end", None), (42, 42, 7), None),
    "ram":     (("box", (10, 10, 10)), (200, 20, 0), None),
    "wall":    (("box", (20, 6, 20)), (195, 40, 0), None),
    "pin2":    (("pin", None), (300, 50, 0), "slide"),
    "post2":   (("box", (6, 20, 20)), (330, 40, 0), None),
}


def write_model(root, names=None):
    """build/model/parts.json + nodes.json + one own-frame STEP per part (what cad_incremental.py --model leaves)."""
    from build123d import export_step
    root = Path(root)
    d = root / "build/model/step"
    d.mkdir(parents=True, exist_ok=True)
    parts = []
    for name in names or MODEL:
        (kind, size), at, _ = MODEL[name]
        tmp = d / f"_tmp_{name}.step"
        export_step(shape_of(kind, size), str(tmp))
        key = hashlib.sha256(tmp.read_bytes() + name.encode()).hexdigest()
        tmp.replace(d / f"{key}.step")
        pl = {"name": name, "node": "m", "loc": T(*at)}
        if name != "base":
            pl["joint"] = {"to": "base", "at": "top", "by": "bottom", "type": "rigid"}
        e = {"id": name, "name": name, "node": "m", "func": "m.build", "process": "mill", "color": "#999999", "key": key,
             "qty": 1, "material": "S235JR", "step": f"build/model/step/{key}.step", "placements": [pl]}
        if name == "base":
            e["joints"] = {"top": {"type": "rigid", "loc": T(0, 0, 10)}}
        parts.append(e)
    (root / "build/model/parts.json").write_text(json.dumps({"parts": parts}, indent=1))
    (root / "build/model/nodes.json").write_text("{}")
    return root


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return write_model(tmp_path_factory.mktemp("trk") / "proj")


@pytest.fixture
def proj(built, tmp_path):
    """A private copy with no build/clearance/ cache, so no test sees another's measurements."""
    dst = tmp_path / "proj"
    shutil.copytree(built, dst)
    shutil.rmtree(dst / "build/clearance", ignore_errors=True)
    return dst


# ---------------------------------------------------------------- calling the module under test
def run(root, tracks=None, groups=GROUPS):
    import clearance                 # in the body on purpose: a missing module must FAIL the test, not error collection
    rep = clearance.run(Path(root), CYCLE, groups, tracks=tracks)
    assert isinstance(rep, dict) and isinstance(rep.get("clashes"), list), rep
    return rep


def clash(rep, a, b):
    got = [c for c in rep["clashes"] if {c["a"], c["b"]} == {a, b}]
    assert len(got) <= 1, f"the pair {a}/{b} is reported more than once: {got}"
    return got[0] if got else None


def pairs(rep):
    return sorted(tuple(sorted((c["a"], c["b"]))) for c in rep["clashes"])


# ---------------------------------------------------------------- 2a: a rod end tracked with the slider's pin
def test_2a0_without_tracks_the_static_rod_end_clashes_with_the_moving_pin(proj):
    """The production problem: the viewer moves the rod end, the check does not, so the pin runs through it."""
    rep = run(proj, tracks=None)
    c = clash(rep, "pin", "rod_end")
    assert c and c["t_s"], f"precondition: a static rod end clashes with the moving pin: {pairs(rep)}"


def test_2a1_a_rod_end_tracked_with_the_slider_is_clear_of_its_pin(proj):
    rep = run(proj, tracks=tracks(rod_end=track(WITH_SLIDER)))
    assert clash(rep, "pin", "rod_end") is None, \
        f"the track moves the rod end with the pin, the pin stays in its eye all cycle: {pairs(rep)}"


def test_2a2_a_cached_untracked_result_is_not_reused_once_tracks_arrive(proj):
    """The same model checked first without tracks (an older build), then with them: the pair is measured again."""
    assert clash(run(proj, tracks=None), "pin", "rod_end")
    rep = run(proj, tracks=tracks(rod_end=track(WITH_SLIDER)))
    assert clash(rep, "pin", "rod_end") is None, f"a stale cached clash must not survive the tracks: {pairs(rep)}"
    back = run(proj, tracks=None)
    assert clash(back, "pin", "rod_end"), "and without tracks again it is a clash again"


def test_2a3_a_coarse_track_is_measured_at_its_samples_not_against_the_slider_in_between(proj):
    """As on a real machine: 96 samples over 2.13 s while the shuttle moves 8 mm a sample. Between two samples the
    page holds the rod end at the nearest one while the pin's group moves on; that is no clash of the machine."""
    n = 8   # 60 mm in 0.6 s: 10 mm a sample
    coarse = []
    for k in range(n):
        dx, dy, dz = WITH_SLIDER(k * PERIOD / n)
        coarse += [1, 0, 0, dx, 0, 1, 0, dy, 0, 0, 1, dz]
    rep = run(proj, tracks={"n": n, "period": PERIOD, "nodes": {"rod_end": coarse}})
    assert clash(rep, "pin", "rod_end") is None, f"at every sample the rod end stands on its pin: {pairs(rep)}"


# ---------------------------------------------------------------- 2b: a track that drives a part into a static one
def test_2b0_without_tracks_the_ram_is_clear_of_the_wall(proj):
    rep = run(proj, tracks=None)
    assert clash(rep, "ram", "wall") is None, f"precondition: at home the ram is 10 mm clear of the wall: {pairs(rep)}"


def test_2b1_a_track_driving_a_part_into_a_static_one_is_a_clash_inside_the_overlapping_samples(proj):
    rep = run(proj, tracks=tracks(ram=track(RAM_IN_WALL)))
    c = clash(rep, "ram", "wall")
    assert c, f"the ram's track pushes it 10 mm into the wall mid-cycle: a clash; clashes {pairs(rep)}"
    assert c["t_s"] is not None, f"a clash in motion carries its time: {c}"
    t = float(c["t_s"]) % PERIOD
    assert RAM_IN - SLACK <= t <= RAM_OUT + SLACK, \
        f"the ram is in the wall only for t in ({RAM_IN}, {RAM_OUT}) s (samples {int(RAM_IN / PERIOD * N)}.." \
        f"{int(RAM_OUT / PERIOD * N)} of {N}), not at {t}: {c}"
    assert 2.0 < c["volume_mm3"] <= 600 * 1.03, f"at most 10 x 6 x 10 of the ram is inside the wall: {c}"
    if c.get("window_s"):
        t0, t1 = (float(v) for v in c["window_s"])
        assert RAM_IN - SLACK <= t0 and t1 <= RAM_OUT + SLACK, f"the window lies inside the overlapping samples: {c}"


# ---------------------------------------------------------------- a track replaces the node's group motion
def test_2r_a_track_replaces_the_group_motion_of_its_node(proj):
    """pin2 is in group `slide`, whose travel would take it through post2; its track holds it at home, and the track
    carries the whole motion, so pin2 stays clear."""
    untracked = run(proj, tracks=None)
    assert clash(untracked, "pin2", "post2"), f"precondition: by its group motion pin2 crosses post2: {pairs(untracked)}"
    rep = run(proj, tracks=tracks(pin2=track(AT_HOME)))
    assert clash(rep, "pin2", "post2") is None, f"the track, not the group, moves pin2: {pairs(rep)}"
    assert clash(rep, "pin", "rod_end"), f"an untracked node still moves by its group: {pairs(rep)}"


# ---------------------------------------------------------------- 2c: build.py end to end
def make_input(root, names):
    """The importer output dir, as test_build_clearance.make_input writes it: motion_group per catalogue entry."""
    src = root / "in"
    (src / "parts").mkdir(parents=True)
    (src / "model.glb").write_bytes(b"glTF-fake")
    cat = []
    for name in names:
        e = {"id": name, "name": name, "kind": "made", "material": "steel", "instances": 1, "volume_mm3": 1000.0,
             "files": {"step": f"parts/{name}.step"}}
        if MODEL[name][2]:
            e["motion_group"] = MODEL[name][2]
        cat.append(e)
        (src / "parts" / f"{name}.step").write_text("ISO-10303-21;")
    (src / "parts.json").write_text(json.dumps({"assembly": {"root": "Tracks"}, "parts": cat}, ensure_ascii=False))
    return src


def build_site(tmp_path, world_data):
    names = ["base", "pin", "rod_end"]
    root = write_model(tmp_path / "proj", names)
    src = make_input(tmp_path, names)
    (tmp_path / "motion.json").write_text(json.dumps({"cycle": CYCLE}))
    (tmp_path / "world.mjs").write_text("export default function world() { return {}; }\n")
    (tmp_path / "world_data.json").write_text(json.dumps(world_data))
    out = tmp_path / "site"
    r = subprocess.run([sys.executable, str(BUILD), "--in", str(src), "--out", str(out),
                        "--motion", str(tmp_path / "motion.json"),
                        "--world", str(tmp_path / "world.mjs"), "--world-data", str(tmp_path / "world_data.json"),
                        "--flash", "none", "--kit", "none", "--feedback", "none"],
                       cwd=str(root), capture_output=True, text=True, timeout=900)
    text = r.stdout + r.stderr
    assert r.returncode == 0, text[-3000:]
    f = out / "clearance.json"
    assert f.is_file(), f"build.py publishes clearance.json:\n{text[-3000:]}"
    rep = json.loads(f.read_text())
    assert not rep.get("error"), f"the check ran: {rep}"
    return rep


def test_2c0_build_without_crank_tracks_publishes_the_pin_rod_end_clash(tmp_path):
    rep = build_site(tmp_path, {"SPEED": 1})
    assert any({c["a"], c["b"]} == {"pin", "rod_end"} for c in rep["clashes"]), \
        f"precondition: world data without CRANK_TRACKS, the rod end is static and clashes: {rep['clashes']}"


def test_2c1_build_with_crank_tracks_in_world_data_publishes_clearance_without_the_cleared_pair(tmp_path):
    rep = build_site(tmp_path, {"SPEED": 1, "CRANK_TRACKS": tracks(rod_end=track(WITH_SLIDER))})
    assert not any({c["a"], c["b"]} == {"pin", "rod_end"} for c in rep["clashes"]), \
        f"build.py hands CRANK_TRACKS to the check: the tracked rod end is clear of its pin: {rep['clashes']}"


# ---------------------------------------------------------------- review regressions
def rot_track(pivot, deg_of_t, n=N):
    """A turn about the z axis through `pivot` (CAD mm), as the page applies it: the sample left of the home pose."""
    out = []
    for k in range(n):
        a = math.radians(deg_of_t(k * PERIOD / n))
        c, s = math.cos(a), math.sin(a)
        px, py, _ = pivot
        out += [c, -s, 0, px - c * px + s * py, s, c, 0, py - s * px - c * py, 0, 0, 1, 0]
    return out


def test_r1_a_rotary_track_about_an_offset_pivot_turns_the_ram_into_the_wall_mid_cycle(proj):
    """Half a turn about (205, 35) lays the ram (x 200..210, y 20..30) onto y 40..50, through the wall (y 40..46)."""
    rep = run(proj, tracks=tracks(ram=rot_track((205, 35, 0), lambda t: 180 * tri(t))))
    c = clash(rep, "ram", "wall")
    assert c and c["t_s"] is not None and abs(c["t_s"] - PERIOD / 2) < 0.3, f"{c}; {pairs(rep)}"


def test_r2_two_differently_tracked_parts_that_meet_clash(proj):
    rep = run(proj, tracks=tracks(ram=track(lambda t: (0.0, 10 * tri(t), 0.0)), wall=track(lambda t: (0.0, -10 * tri(t), 0.0))))
    assert clash(rep, "ram", "wall"), pairs(rep)


def test_r3_a_tracked_part_against_a_moving_group_part_clashes(proj):
    """post2 is tracked (held at home); pin2's group carries it through post2."""
    rep = run(proj, tracks=tracks(post2=track(AT_HOME)))
    assert clash(rep, "pin2", "post2"), pairs(rep)


def test_r4_a_track_of_another_period_than_the_cycle_is_not_applied_and_says_so(proj):
    import clearance
    rep = run(proj, tracks={"n": N, "period": PERIOD / 2, "nodes": {"rod_end": track(WITH_SLIDER)}})
    assert clash(rep, "pin", "rod_end"), "the stale track is not applied: the rod end stands still as before"
    assert rep["tracks"]["skipped"], rep.get("tracks")
    assert any("pose tracks not applied" in ln for ln in clearance.lines(rep))


def test_r5_a_track_of_no_such_part_is_reported(proj):
    rep = run(proj, tracks=tracks(rod_end=track(WITH_SLIDER), nope=track(AT_HOME)))
    assert rep["tracks"]["applied"] == 1 and any("nope" in s for s in rep["tracks"]["skipped"]), rep["tracks"]


def test_r6_a_malformed_track_block_costs_its_tracks_not_the_check(proj):
    rep = run(proj, tracks={"n": "many", "period": PERIOD, "nodes": {"rod_end": [1, 2]}})
    assert clash(rep, "pin", "rod_end") and rep["tracks"]["skipped"], rep.get("tracks")
    rep = run(proj, tracks=tracks(rod_end=["x"] * (12 * N)))
    assert clash(rep, "pin", "rod_end") and any("not numbers" in s for s in rep["tracks"]["skipped"])


def test_r7_a_null_or_nan_sample_costs_that_track_not_the_part(proj):
    for bad in (None, float("nan")):
        arr = track(WITH_SLIDER)
        arr[3] = bad
        rep = run(proj, tracks=tracks(rod_end=arr))
        assert clash(rep, "pin", "rod_end"), f"{bad}: the rod end falls back to standing still, its clash stays: {pairs(rep)}"
        assert any("rod_end" in s for s in rep["tracks"]["skipped"]), rep["tracks"]
