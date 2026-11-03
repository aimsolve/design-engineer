"""cycle_clearance.py reads the per-part model (`--model build/model/parts.json`) and measures only what changed.

Everything runs the script as a CLI (subprocess) on a tiny synthetic machine written into a temp dir, and looks only at what a
user sees: the exit code, stdout, and <out>/clearance.json.

    python -m pytest agent/skills/sim/scripts/tests/test_cycle_clearance.py -q
    (the interpreter needs build123d 0.13.0 + python-fcl + numpy; CYCLE_PY overrides the one that runs the script)

The machine (world mm, home pose; period 4 s):
    base        static              x[0,200]      y[0,60]   z[0,10]
    stop        static              x[101.5,111.5] y[10,50] z[10,40]
    post        static              x[0,6]        y[50,56]  z[24,35]
    carriage    group slider        x[10,40]      y[10,50]  z[10,20]   linear +X, 60 mm, out t 0..1, back t 1..2
    arm         group arm           x[24,26]      y[30,70]  z[25,30]   rotary about Z through (25,30), 90 deg, t 2..3, back 3..4;
                (parent slider)                                         its STEP is a bar along +X, placed turned 90 deg about Z
    gate_plate  group gate          x[113,130]    y[10,50]  z[11.5,30] linear +Z, 20 mm, t 0..2, back 2..4

Verdicts at --clear 0.5:
    carriage~base     SLIDE   (sits on it the whole cycle)
    carriage~stop     ok      (closest 1.5 mm at full travel)
    arm~post          HIT     (the arm swings through the post)
    gate_plate~stop   ok      (1.5 mm)
    gate_plate~base   ok      (1.5 mm at home)
Group assignment: carriage and arm through motion.json `node_groups`; gate_plate through the part's own `motion_group` field in
parts.json (the --step reference gets it through --groups motion_groups.json, since a STEP has no such field).

Stdout of a --model run must say how many pairs it measured and how many it took from the cache, e.g.
"5 pairs: measured 2, cached 3" or "2 measured, 3 cached" (the number right next to the word).
"""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(os.environ.get("CYCLE_SCRIPT") or Path(__file__).resolve().parent.parent / "cycle_clearance.py")   # override: mutation checks
PY = os.environ.get("CYCLE_PY") or sys.executable

PERIOD = 4.0
GROUPS = {
    "slider": {"kind": "linear", "axis": "x", "travel_mm": 60.0, "segments": [[0, 1, 0, 1], [1, 2, 1, 0]]},
    "arm": {"kind": "rotary", "axis": "z", "travel_deg": 90.0, "pivot_mm": [25, 30, 0], "parent": "slider",
            "segments": [[2, 3, 0, 1], [3, 4, 1, 0]]},
    "gate": {"kind": "linear", "axis": "z", "travel_mm": 20.0, "segments": [[0, 2, 0, 1], [2, 4, 1, 0]]},
}
NODE_GROUPS = {"carriage": "slider", "arm": "arm"}          # gate_plate: by the part's motion_group field / --groups

# name -> (own-frame box size, own-frame offset of the box centre, placement translation, placement turn about Z deg, group field)
PARTS = {
    "base":       ((200, 60, 10), (0, 0, 0), (100, 30, 5), 0, None),
    "stop":       ((10, 40, 30), (0, 0, 0), (106.5, 30, 25), 0, None),
    "post":       ((6, 6, 11), (0, 0, 0), (3, 53, 29.5), 0, None),
    "carriage":   ((30, 40, 10), (0, 0, 0), (25, 30, 15), 0, None),
    "arm":        ((40, 2, 5), (20, 0, 0), (25, 30, 27.5), 90, None),      # bar x[0,40] in its own frame, turned to +Y
    "gate_plate": ((17, 40, 18.5), (0, 0, 0), (121.5, 30, 20.75), 0, "gate"),
}

EXPECT = ["--expect", "arm~post"]
COMMON = ["--motion", "build/twin/motion.json", "--max-step", "1", "--out", "build/clearance"]
COLD_STATUS = {
    frozenset({"carriage", "base"}): "SLIDE",
    frozenset({"carriage", "stop"}): "ok",
    frozenset({"arm", "post"}): "HIT (expected)",
    frozenset({"gate_plate", "stop"}): "ok",
    frozenset({"gate_plate", "base"}): "ok",
}


# ---------------------------------------------------------------- project writing

def loc_matrix(t, rz_deg):
    c, s = math.cos(math.radians(rz_deg)), math.sin(math.radians(rz_deg))
    return [c, -s, 0, t[0],
            s, c, 0, t[1],
            0, 0, 1, t[2],
            0, 0, 0, 1]


def own_shape(size, off):
    from build123d import Box, Pos
    return Pos(*off) * Box(*size)


def write_part_step(root, name, size, off):
    """The part alone in its own frame -> build/model/step/<key>.step, key = content hash. Returns the key."""
    from build123d import export_step
    d = Path(root) / "build/model/step"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"_tmp_{name}.step"
    export_step(own_shape(size, off), str(tmp))
    key = hashlib.sha256(tmp.read_bytes() + name.encode()).hexdigest()
    tmp.replace(d / f"{key}.step")
    return key


def part_entry(name, key, t, rz, group):
    e = {"name": name, "id": name, "key": key, "process": "mill", "qty": 1, "step": f"build/model/step/{key}.step",
         "placements": [{"name": name, "loc": loc_matrix(t, rz)}]}
    if group:
        e["motion_group"] = group
    return e


def read_parts(root):
    return json.loads((Path(root) / "build/model/parts.json").read_text())["parts"]


def write_parts(root, parts):
    (Path(root) / "build/model/parts.json").write_text(json.dumps({"parts": parts}, indent=1))


def write_motion(root, groups=None):
    d = Path(root) / "build/twin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "motion.json").write_text(json.dumps({"cycle": {"period_s": PERIOD, "groups": groups or GROUPS,
                                                          "node_groups": NODE_GROUPS}}, indent=1))
    (d / "motion_groups.json").write_text(json.dumps({"gate_plate": "gate"}))


def make_project(root):
    """parts.json + one STEP per part (own frame) + motion.json, and the same geometry as ONE machine STEP at home pose
    (build/cad/machine.step, a compound of solids labelled with the part names) for the reference --step run."""
    from build123d import Compound, Pos, Rot, export_step
    root = Path(root)
    parts, world = [], []
    for name, (size, off, t, rz, group) in PARTS.items():
        parts.append(part_entry(name, write_part_step(root, name, size, off), t, rz, group))
        w = Pos(*t) * Rot(0, 0, rz) * own_shape(size, off)
        w.label = name
        world.append(w)
    write_parts(root, parts)
    write_motion(root)
    (root / "build/cad").mkdir(parents=True, exist_ok=True)
    m = Compound(children=world)
    m.label = "machine"
    export_step(m, str(root / "build/cad/machine.step"))
    return root


# ---------------------------------------------------------------- running and reading

class Run:
    def __init__(self, root, cp, before):
        self.rc = cp.returncode
        self.out = (cp.stdout or "") + (cp.stderr or "")
        f = report_file(root)
        written = f.exists() and f.stat().st_mtime_ns != before   # a report left by an earlier run is not this run's
        self.report = json.loads(f.read_text()) if written else None

    @property
    def status(self):
        assert self.report is not None, f"this run wrote no build/clearance/clearance.json (rc={self.rc}):\n{self.out[-2000:]}"
        return {frozenset({r["a"], r["b"]}): r["status"] for r in self.report["pairs"]}

    def counts(self):
        """(measured, cached) from the last stdout line that names them."""
        def num(word, line):
            m = re.search(rf"\b{word}\b\s*[:=]?\s*(\d+)", line) or re.search(rf"(\d+)\s+(?:pairs?\s+)?\b{word}\b", line)
            return int(m.group(1)) if m else None
        got = None
        for line in self.out.splitlines():
            m, c = num("measured", line), num("cached", line)
            if m is not None and c is not None:
                got = (m, c)
        assert got, f"stdout must report the pairs measured and taken from the cache (e.g. 'measured 2, cached 3'):\n{self.out[-2000:]}"
        return got


def report_file(root):
    return Path(root) / "build/clearance/clearance.json"


def run(root, *args, timeout=300, xdg=None):
    """XDG_CACHE_HOME is set only when `xdg` is given (a remote worker sets it; the developer's shell may too), so the mesh
    cache lands where the test expects whatever the calling environment has."""
    f = report_file(root)
    before = f.stat().st_mtime_ns if f.exists() else None
    env = {k: v for k, v in os.environ.items() if k != "XDG_CACHE_HOME"}
    if xdg is not None:
        env["XDG_CACHE_HOME"] = str(xdg)
    cp = subprocess.run([PY, str(SCRIPT), *args], cwd=root, env=env, capture_output=True, text=True, timeout=timeout)
    return Run(root, cp, before)


def run_model(root, *extra, common=COMMON, expect=EXPECT, timeout=300, xdg=None):
    return run(root, "--model", "build/model/parts.json", *common, *expect, *extra, timeout=timeout, xdg=xdg)


def run_step(root, *extra, expect=EXPECT):
    return run(root, "--step", "build/cad/machine.step", "--groups", "build/twin/motion_groups.json", *COMMON, *expect, *extra)


def involving(report, names):
    rows = report["pairs"]
    hit = sum(1 for r in rows if r["a"] in names or r["b"] in names)
    return hit, len(rows) - hit


@pytest.fixture(scope="session")
def cold(tmp_path_factory):
    """One project, run cold once with --model and once with --step (each into its own copy). Tests copy, never write here."""
    base = tmp_path_factory.mktemp("cycle")
    proj = make_project(base / "proj")
    model_dir = base / "model_run"
    shutil.copytree(proj, model_dir)
    step_dir = base / "step_run"
    shutil.copytree(proj, step_dir)
    return {"model_dir": model_dir, "model": run_model(model_dir), "step_dir": step_dir, "step": run_step(step_dir)}


@pytest.fixture
def warm(cold, tmp_path):
    """A private copy of the project after one cold --model run (its cache under build/clearance included)."""
    return shutil.copytree(cold["model_dir"], tmp_path / "proj")


def test_reference_step_run_sees_the_designed_verdicts(cold):
    """Guards the fixture itself: the existing --step path on the machine STEP gives the verdicts the geometry was built for."""
    s = cold["step"]
    assert s.rc == 0, s.out[-2000:]
    assert s.status == COLD_STATUS, s.status


# ---------------------------------------------------------------- done-when 1: same verdicts as --step

def test_c1_model_gives_the_same_verdicts_as_the_machine_step(cold):
    m, s = cold["model"], cold["step"]
    assert m.rc == 0, f"--model run failed (an expected HIT and a SLIDE must not fail it):\n{m.out[-2000:]}"
    assert m.status == COLD_STATUS, f"--model verdicts {m.status}"
    assert m.status == s.status, "--model and --step disagree on the same geometry"


def test_c1_part_motion_group_field_assigns_the_group(cold):
    """gate_plate is in no node_groups map: only its parts.json `motion_group` puts it in the gate group."""
    cold["model"].status                       # asserts this run wrote a report
    rows = {frozenset({r["a"], r["b"]}): r for r in cold["model"].report["pairs"]}
    row = rows.get(frozenset({"gate_plate", "stop"}))
    assert row is not None, f"gate_plate~stop was not checked: gate_plate's motion_group was ignored ({sorted(map(sorted, rows))})"
    assert "gate" in row["groups"], row


def test_c1_unexpected_hit_exits_1_like_step(cold, tmp_path):
    """Without --expect the arm~post HIT fails the run, in both modes, with the same verdicts."""
    md = shutil.copytree(cold["model_dir"], tmp_path / "m")
    sd = shutil.copytree(cold["step_dir"], tmp_path / "s")
    m, s = run_model(md, expect=[]), run_step(sd, expect=[])
    assert s.rc == 1, s.out[-2000:]
    assert m.rc == 1, f"--model must exit 1 on an unexpected HIT (rc={m.rc}):\n{m.out[-2000:]}"
    assert m.status[frozenset({"arm", "post"})] == "HIT"
    assert m.status == s.status


# ---------------------------------------------------------------- done-when 2: incremental

def test_c2_cold_run_reports_everything_measured(cold):
    m = cold["model"]
    measured, cached = m.counts()
    assert cached == 0, f"nothing can come from a cache on the first run: {m.out[-1500:]}"
    assert measured >= len(m.report["pairs"]), m.out[-1500:]


def test_c2_identical_second_run_measures_nothing(warm, cold):
    r = run_model(warm)
    assert r.rc == 0, r.out[-2000:]
    measured, cached = r.counts()
    assert measured == 0, f"an identical second run must measure 0 pairs:\n{r.out[-1500:]}"
    assert cached == len(r.report["pairs"]), f"every pair comes from the cache:\n{r.out[-1500:]}"
    assert r.status == cold["model"].status, "cached verdicts differ from the measured ones"


def test_c2_changed_part_step_remeasures_only_its_pairs(warm):
    """stop gets a new STEP (new key): its box shifted 8 mm towards the carriage, which now runs into it at full travel."""
    parts = read_parts(warm)
    size, off, t, rz, group = PARTS["stop"]
    old = next(p for p in parts if p["name"] == "stop")
    (warm / old["step"]).unlink()
    key = write_part_step(warm, "stop", size, (-8, 0, 0))
    assert key != old["key"]
    parts = [part_entry("stop", key, t, rz, group) if p["name"] == "stop" else p for p in parts]
    write_parts(warm, parts)

    r = run_model(warm)
    assert r.status[frozenset({"carriage", "stop"})] == "HIT", f"the moved stop must now be hit: {r.status}"
    assert r.rc == 1, "an unexpected HIT exits 1"
    for pair in (frozenset({"carriage", "base"}), frozenset({"arm", "post"}), frozenset({"gate_plate", "base"})):
        assert r.status[pair] == COLD_STATUS[pair], pair
    with_stop, without = involving(r.report, {"stop"})
    assert r.counts() == (with_stop, without), \
        f"only the pairs containing stop are measured ({with_stop}), the other {without} come from the cache:\n{r.out[-1500:]}"


def test_c2_changed_placement_is_not_served_from_cache(warm):
    """Same STEP and key, only stop's placement moves 8 mm towards the carriage: the cached ok must not survive."""
    parts = read_parts(warm)
    size, off, t, rz, group = PARTS["stop"]
    for p in parts:
        if p["name"] == "stop":
            p["placements"][0]["loc"] = loc_matrix((t[0] - 8, t[1], t[2]), rz)
    write_parts(warm, parts)
    r = run_model(warm)
    assert r.status[frozenset({"carriage", "stop"})] == "HIT", f"a moved placement served a stale verdict: {r.status}"
    assert r.rc == 1
    measured, _ = r.counts()
    assert measured >= 1, r.out[-1500:]


def test_c2_changed_parent_group_motion_remeasures_its_pairs_and_its_childs(warm):
    """slider travel 60 -> 61.2: carriage now comes 0.3 mm from stop (CLOSE). The slider's pairs and those of arm (its child
    group) are measured again; gate_plate's pairs stay cached."""
    groups = json.loads(json.dumps(GROUPS))
    groups["slider"]["travel_mm"] = 61.2
    write_motion(warm, groups)
    r = run_model(warm)
    assert r.status[frozenset({"carriage", "stop"})] == "CLOSE", r.status
    assert r.rc == 1, "an unexpected CLOSE exits 1"
    assert r.status[frozenset({"arm", "post"})] == "HIT (expected)"
    moved, other = involving(r.report, {"carriage", "arm"})
    assert other == 2, f"gate_plate's two pairs must still be in the report: {r.report['pairs']}"
    assert r.counts() == (moved, other), \
        f"slider + child arm pairs measured ({moved}), gate pairs cached ({other}):\n{r.out[-1500:]}"


def test_c2_changed_child_group_motion_leaves_other_groups_and_statics_cached(warm):
    """arm travel 90 -> 60 deg (still swings through the post): only arm~post is measured again."""
    groups = json.loads(json.dumps(GROUPS))
    groups["arm"]["travel_deg"] = 60.0
    write_motion(warm, groups)
    r = run_model(warm)
    assert r.rc == 0, r.out[-2000:]
    assert r.status == COLD_STATUS, r.status
    arm_pairs, other = involving(r.report, {"arm"})
    assert arm_pairs == 1
    assert r.counts() == (1, other), f"only arm's pair measured, slider and gate pairs cached:\n{r.out[-1500:]}"


# ---------------------------------------------------------------- cache invalidation on settings

def test_c3_raising_clear_turns_ok_into_close_and_back(warm):
    r = run_model(warm, "--clear", "2")
    st = r.status
    for pair in (frozenset({"carriage", "stop"}), frozenset({"gate_plate", "stop"}), frozenset({"gate_plate", "base"})):
        assert st[pair] == "CLOSE", f"{sorted(pair)} is 1.5 mm apart: CLOSE at --clear 2, got {st[pair]}"
    assert st[frozenset({"carriage", "base"})] == "SLIDE"
    assert r.rc == 1, "unexpected CLOSE exits 1"
    back = run_model(warm, "--clear", "0.5")
    assert back.status == COLD_STATUS, f"back at --clear 0.5 the verdicts are the cold ones again: {back.status}"
    assert back.rc == 0, back.out[-2000:]


def test_c3_changing_max_step_remeasures_every_pair(warm):
    common = [x if x != "1" else "0.5" for x in COMMON]
    assert "0.5" in common
    r = run_model(warm, common=common)
    assert r.rc == 0, r.out[-2000:]
    assert r.status == COLD_STATUS
    assert r.counts() == (len(r.report["pairs"]), 0), \
        f"every pair is sampled by --max-step, so a new value re-measures all of them:\n{r.out[-1500:]}"



# ---------------------------------------------------------------- group rule (the viewer's: derive.py motion_groups, motion.mjs)
# A placement's group: its part's own `motion_group`, else the group of the placement it is docked to by ANY joint type, up the
# joint graph; then motion.json node_groups / --groups override per placement by name (an explicit "static" wins). A group
# given only by node_groups is NOT inherited through a joint (derive.motion_groups never sees node_groups).

DERIVE = Path(__file__).resolve().parents[3] / "machine-project" / "scripts" / "derive.py"

# Extra parts, only in this section's own project: (box world min, box world max, joint {to, type}).
DOCKED = {
    "washer":    ((15, 15, 18), (20, 20, 22), {"to": "carriage", "type": "rigid"}),   # overlaps the carriage's top
    "screw":     ((16, 16, 14), (19, 19, 24), {"to": "washer", "type": "rigid"}),     # through washer into the carriage
    "rigid_tab": ((38, 40, 13), (43, 48, 18), {"to": "carriage", "type": "rigid"}),   # sticks out 3 mm: at +60 it is in stop
    "lin_slide": ((60, 15, 13), (65, 25, 18), {"to": "carriage", "type": "linear"}),  # moves with the carriage through stop
    "rev_knob":  ((75, 15, 13), (80, 25, 18), {"to": "carriage", "type": "revolute"}),
    "pinned":    ((85, 15, 13), (90, 25, 18), {"to": "carriage", "type": "rigid"}),   # node_groups says static
    "arm_clip":  ((24, 60, 30), (26, 64, 32), {"to": "arm", "type": "rigid"}),        # arm's group is node_groups only
}


def add_box_part(root, parts, name, lo, hi, joint=None, group=None):
    size = tuple(h - l for l, h in zip(lo, hi))
    centre = tuple((l + h) / 2 for l, h in zip(lo, hi))
    e = part_entry(name, write_part_step(root, name, size, (0, 0, 0)), centre, 0, group)
    if joint:
        e["placements"][0]["joint"] = joint
    parts.append(e)
    return e


def load_derive():
    """derive.py by path. At import it only puts its own folder on sys.path (procgroup, cad_incremental: stdlib-only at
    import) and takes ROOT = cwd; motion_groups() reads neither, so no env or chdir is needed."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("derive_for_cycle_tests", DERIVE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def docked(tmp_path_factory):
    root = make_project(tmp_path_factory.mktemp("docked") / "proj")
    parts = read_parts(root)
    for p in parts:
        if p["name"] == "carriage":
            p["motion_group"] = "slider"      # the viewer's source of a group a docked part can inherit
    for name, (lo, hi, joint) in DOCKED.items():
        add_box_part(root, parts, name, lo, hi, joint)
    write_parts(root, parts)
    motion = json.loads((root / "build/twin/motion.json").read_text())
    motion["cycle"]["node_groups"]["pinned"] = "static"
    (root / "build/twin/motion.json").write_text(json.dumps(motion, indent=1))
    r = run_model(root)
    r.status                                   # asserts this run wrote a report
    rows = {frozenset({x["a"], x["b"]}): x for x in r.report["pairs"]}
    return {"root": root, "rows": rows, "motion": motion, "parts": parts}


def test_c4_rigid_docking_carries_the_group_transitively(docked):
    """washer (rigid -> carriage), screw (rigid -> washer) and rigid_tab (rigid -> carriage) are in slider: they overlap the
    carriage at home, yet no pair with it is reported (same group, skipped), and rigid_tab rides into stop at full travel."""
    rows = docked["rows"]
    for name in ("washer", "screw", "rigid_tab"):
        row = rows.get(frozenset({"carriage", name}))
        assert row is None, f"{name} is docked (screw via washer) and must move with the carriage: {row}"
    assert rows.get(frozenset({"screw", "washer"})) is None, "screw and washer are both in slider: no pair"
    row = rows.get(frozenset({"rigid_tab", "stop"}))
    assert row is not None and row["status"] == "HIT", f"rigid_tab moves with the slider into stop: {row}"
    assert "slider" in row["groups"], row


def test_c4_linear_and_revolute_docking_also_carry_the_group(docked):
    """Any joint type carries the group (the viewer's rule): lin_slide and rev_knob ride with the carriage, so no pair with
    it, and on the way out they run through stop as slider parts."""
    rows = docked["rows"]
    for name in ("lin_slide", "rev_knob"):
        row = rows.get(frozenset({"carriage", name}))
        assert row is None, f"{name} is docked to the carriage and must move with it: {row}"
        row = rows.get(frozenset({name, "stop"}))
        assert row is not None and row["status"] == "HIT", f"{name} rides with the slider through stop: {row}"
        assert sorted(row["groups"]) == ["slider", "static"], row


def test_c4_explicit_static_wins_over_a_docking(docked):
    row = docked["rows"].get(frozenset({"carriage", "pinned"}))
    assert row is not None and row["status"] == "HIT", f"pinned is mapped to static and stands in the carriage's path: {row}"
    assert sorted(row["groups"]) == ["slider", "static"], row


def test_c4_groups_agree_with_derive_motion_groups(docked):
    """Every name in clearance.json carries the group derive.motion_groups() gives its placement, overridden by node_groups /
    --groups by name; an unknown or missing group is static. (arm_clip: docked to arm, whose group comes only from
    node_groups, so it stays static.)"""
    derived = load_derive().motion_groups(docked["parts"])
    over = dict(docked["motion"]["cycle"]["node_groups"])
    want = {}
    for name, g in derived.items():
        g = over[name] if name in over else g
        want[name] = g if g in GROUPS else "static"
    seen = {}
    for x in docked["rows"].values():
        seen[x["a"]], seen[x["b"]] = x["groups"][0], x["groups"][1]
    assert "arm_clip" in seen, f"arm_clip touches the arm at home, so it has a pair: {sorted(seen)}"
    wrong = {n: (g, want.get(n)) for n, g in seen.items() if g != want.get(n)}
    assert not wrong, f"name: (clearance.json group, viewer rule group) {wrong}"


def test_c4_docking_cycle_is_static_and_terminates(warm):
    """cyc_a docked to cyc_b and cyc_b to cyc_a, neither grouped: both static, the run finishes."""
    parts = read_parts(warm)
    add_box_part(warm, parts, "cyc_a", (50, 30, 13), (55, 38, 18), {"to": "cyc_b", "type": "rigid"})
    add_box_part(warm, parts, "cyc_b", (65, 30, 13), (70, 38, 18), {"to": "cyc_a", "type": "revolute"})
    write_parts(warm, parts)
    r = run_model(warm, timeout=90)
    st = r.status
    for name in ("cyc_a", "cyc_b"):
        row = next((x for x in r.report["pairs"] if {x["a"], x["b"]} == {"carriage", name}), None)
        assert row is not None and row["status"] == "HIT", f"{name} is static in the carriage's path: {row}"
        assert sorted(row["groups"]) == ["slider", "static"], row
    assert st[frozenset({"carriage", "stop"})] == "ok"


# ---------------------------------------------------------------- stored mesh parts (no STEP)

def box_mesh(size):
    """A closed box in its own frame, centred: {"v": flat xyz, "f": flat triangle indices} (derive.py _mesh_part reads both,
    reshaped to (-1, 3))."""
    h = [s / 2 for s in size]
    v = [[(-1) ** (1 - x) * h[0], (-1) ** (1 - y) * h[1], (-1) ** (1 - z) * h[2]] for x in (0, 1) for y in (0, 1) for z in (0, 1)]
    f = [0, 1, 3, 0, 3, 2, 4, 6, 7, 4, 7, 5, 0, 4, 5, 0, 5, 1, 2, 3, 7, 2, 7, 6, 0, 2, 6, 0, 6, 4, 1, 5, 7, 1, 7, 3]
    return {"v": [c for p in v for c in p], "f": f}


def test_c5_stored_mesh_part_is_checked_like_a_step_part(warm):
    """A bought part with `mesh` and no `step`, static in the carriage's path: carriage~mesh_block is a HIT."""
    lo, hi = (92, 30, 13), (97, 38, 18)
    size = tuple(b - a for a, b in zip(lo, hi))
    data = json.dumps(box_mesh(size))
    key = hashlib.sha256(data.encode()).hexdigest()
    (warm / "build/model/mesh").mkdir(parents=True, exist_ok=True)
    (warm / f"build/model/mesh/{key}.json").write_text(data)
    parts = read_parts(warm)
    parts.append({"name": "mesh_block", "id": "mesh_block", "key": key, "process": "bought", "qty": 1,
                  "mesh": f"build/model/mesh/{key}.json",
                  "placements": [{"name": "mesh_block", "loc": loc_matrix(tuple((a + b) / 2 for a, b in zip(lo, hi)), 0)}]})
    write_parts(warm, parts)
    r = run_model(warm)
    row = next((x for x in (r.report or {"pairs": []})["pairs"] if {x["a"], x["b"]} == {"carriage", "mesh_block"}), None)
    assert row is not None and row["status"] == "HIT", f"the mesh part must be collision-checked (rc={r.rc}): {row}\n{r.out[-1500:]}"
    assert r.rc == 1, "an unexpected HIT exits 1"


# ---------------------------------------------------------------- the cache: tessellation, narrowed runs, damaged files

def tessellated(r):
    """Parts tessellated per stdout line '<model>: N parts tessellated in S s' (None when no such line)."""
    m = re.findall(r"(\d+)\s+parts?\s+tessellated", r.out)
    return int(m[-1]) if m else None


def test_c2_warm_run_tessellates_nothing(warm):
    r = run_model(warm)
    assert r.rc == 0, r.out[-2000:]
    assert tessellated(r) is None, f"nothing changed, nothing to tessellate:\n{r.out[-1500:]}"


def test_c2_one_changed_step_tessellates_one_part(warm):
    parts = read_parts(warm)
    size, off, t, rz, group = PARTS["stop"]
    old = next(p for p in parts if p["name"] == "stop")
    (warm / old["step"]).unlink()
    key = write_part_step(warm, "stop", size, (-8, 0, 0))
    write_parts(warm, [part_entry("stop", key, t, rz, group) if p["name"] == "stop" else p for p in parts])
    r = run_model(warm)
    assert tessellated(r) == 1, f"only the changed stop is tessellated:\n{r.out[-1500:]}"


def test_c2_only_run_keeps_the_rest_of_the_cache(warm):
    """--only narrows one look at a group: the other pairs' results stay for the next full run."""
    first = run_model(warm, "--only", "arm")
    assert first.report is not None, f"the --only run failed (rc={first.rc}):\n{first.out[-1500:]}"
    r = run_model(warm)
    assert r.rc == 0, r.out[-2000:]
    assert r.status == COLD_STATUS
    assert r.counts() == (0, len(r.report["pairs"])), f"a full run after --only arm must find every pair still cached:\n{r.out[-1500:]}"


def test_c2_skip_run_keeps_exactly_the_pairs_it_used(warm):
    """--skip is part of the standard command, so a --skip run keeps only its own pairs (else pairs.json grows forever):
    a full run afterwards measures exactly the pairs of the skipped part and takes the rest from the cache."""
    first = run_model(warm, "--skip", "gate_plate")
    assert first.report is not None, f"the --skip run failed (rc={first.rc}):\n{first.out[-1500:]}"
    r = run_model(warm)
    assert r.rc == 0, r.out[-2000:]
    assert r.status == COLD_STATUS
    gate, rest = involving(r.report, {"gate_plate"})
    assert gate == 2
    assert r.counts() == (gate, rest), f"gate_plate's {gate} pairs measured again, the other {rest} cached:\n{r.out[-1500:]}"


def candidates(r):
    m = re.findall(r"(\d+)\s+candidate pairs", r.out)
    assert m, f"stdout must say 'N candidate pairs':\n{r.out[-1500:]}"
    return int(m[-1])


def test_c2_pair_cache_does_not_grow_across_skip_runs(warm):
    """Three slider travels, each run with --skip gate_plate: pairs.json never holds more entries than that run's candidates."""
    for travel in (60.5, 61.0, 59.5):
        groups = json.loads(json.dumps(GROUPS))
        groups["slider"]["travel_mm"] = travel
        write_motion(warm, groups)
        r = run_model(warm, "--skip", "gate_plate")
        assert r.report is not None, f"travel {travel}: run failed (rc={r.rc}):\n{r.out[-1500:]}"
        cache = json.loads((warm / "build/clearance/pairs.json").read_text())
        assert isinstance(cache, dict), type(cache)
        assert len(cache) <= candidates(r), f"travel {travel}: pairs.json has {len(cache)} entries for {candidates(r)} candidate pairs"


def fresh_copy(cold, dst):
    """The project with no build/clearance at all: no mesh or pair cache."""
    d = shutil.copytree(cold["model_dir"], dst)
    shutil.rmtree(d / "build/clearance", ignore_errors=True)
    return d


def test_c2_meshes_go_to_xdg_cache_home_when_set(cold, tmp_path):
    xdg = tmp_path / "xdg"
    one = fresh_copy(cold, tmp_path / "a" / "proj")
    r = run_model(one, xdg=xdg)
    assert r.status == COLD_STATUS, r.out[-1500:]
    meshes = one / "build/clearance/meshes"
    assert not (meshes.exists() and any(meshes.iterdir())), f"with XDG_CACHE_HOME set nothing goes to <out>/meshes: {list(meshes.iterdir())}"
    assert (xdg / "cycle_clearance").is_dir() and any((xdg / "cycle_clearance").rglob("*")), \
        f"per-part meshes must be under $XDG_CACHE_HOME/cycle_clearance/: {sorted(map(str, xdg.rglob('*')))}"
    two = fresh_copy(cold, tmp_path / "b" / "proj")  # the same project in another job dir on the same worker: same cache
    r2 = run_model(two, xdg=xdg)
    assert r2.status == COLD_STATUS
    assert tessellated(r2) is None, f"the meshes are in $XDG_CACHE_HOME already, nothing to tessellate:\n{r2.out[-1500:]}"


def test_c2_meshes_go_to_out_without_xdg_cache_home(cold, tmp_path):
    one = fresh_copy(cold, tmp_path / "one")
    r = run_model(one)
    assert r.status == COLD_STATUS, r.out[-1500:]
    assert any((one / "build/clearance/meshes").glob("*.npz")), "without XDG_CACHE_HOME the meshes go to <out>/meshes"


def test_c2_identical_second_step_run_measures_nothing(cold, tmp_path):
    """--step caches pairs too."""
    sd = shutil.copytree(cold["step_dir"], tmp_path / "s")
    r = run_step(sd)
    assert r.rc == 0, r.out[-2000:]
    assert r.status == COLD_STATUS
    assert r.counts() == (0, len(r.report["pairs"])), f"a second identical --step run measures 0 pairs:\n{r.out[-1500:]}"


def test_c2_truncated_mesh_cache_is_rebuilt(warm):
    files = sorted((warm / "build/clearance/meshes").glob("*.npz"))
    assert files, "the cold run left no build/clearance/meshes/*.npz"
    for f in files:
        f.write_bytes(f.read_bytes()[:50])
    r = run_model(warm)
    assert "Traceback" not in r.out, r.out[-2000:]
    assert r.rc == 0, f"a damaged mesh cache is recomputed, the verdict code stays 0 (rc={r.rc}):\n{r.out[-1500:]}"
    assert r.status == COLD_STATUS


def test_c2_truncated_pair_cache_is_rebuilt(warm):
    f = warm / "build/clearance/pairs.json"
    assert f.is_file(), "the cold run left no build/clearance/pairs.json"
    f.write_bytes(f.read_bytes()[:50])
    r = run_model(warm)
    assert "Traceback" not in r.out, r.out[-2000:]
    assert r.rc == 0, f"a damaged pair cache is recomputed, the verdict code stays 0 (rc={r.rc}):\n{r.out[-1500:]}"
    assert r.status == COLD_STATUS


def mesh_files(d):
    return {f.name for f in d.rglob("*") if f.is_file()} if d.is_dir() else set()


def new_stop_step(root):
    """stop gets a new STEP and key (box shifted 8 mm in its own frame); the old STEP is removed."""
    parts = read_parts(root)
    size, off, t, rz, group = PARTS["stop"]
    old = next(p for p in parts if p["name"] == "stop")
    (root / old["step"]).unlink()
    key = write_part_step(root, "stop", size, (-8, 0, 0))
    write_parts(root, [part_entry("stop", key, t, rz, group) if p["name"] == "stop" else p for p in parts])


def test_c2_xdg_mesh_cache_is_per_project(cold, tmp_path):
    """$XDG_CACHE_HOME/cycle_clearance/<project folder name>/: projA's run never deletes projB's meshes, and a part edit in
    projA deletes projA's superseded mesh (the same rule as <out>/meshes)."""
    xdg = tmp_path / "xdg"
    a, b = fresh_copy(cold, tmp_path / "projA"), fresh_copy(cold, tmp_path / "projB")
    assert run_model(a, xdg=xdg).report is not None
    assert run_model(b, xdg=xdg).report is not None
    da, db = xdg / "cycle_clearance" / "projA", xdg / "cycle_clearance" / "projB"
    before_a, before_b = mesh_files(da), mesh_files(db)
    assert before_a and before_b, f"each project's meshes under $XDG_CACHE_HOME/cycle_clearance/<folder>/: {sorted(map(str, xdg.rglob('*')))}"
    new_stop_step(a)
    r = run_model(a, xdg=xdg)
    assert r.status[frozenset({"carriage", "stop"})] == "HIT", r.out[-1500:]
    assert mesh_files(db) == before_b, "a run of projA deleted or changed projB's meshes"
    after_a = mesh_files(da)
    assert len(before_a - after_a) == 1 and len(after_a - before_a) == 1, \
        f"projA: the old stop mesh is replaced by the new one (gone {before_a - after_a}, new {after_a - before_a})"


# ---------------------------------------------------------------- concurrent runs and their temp files

def load_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cycle_clearance_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_c2_two_runs_at_once_share_a_cold_xdg_cache(cold, tmp_path):
    """Two jobs of the same project at the same time on one worker (same folder name, same keys, cold cache): both finish
    normally."""
    xdg = tmp_path / "xdg"
    roots = [fresh_copy(cold, tmp_path / n / "proj") for n in ("a", "b")]
    env = {k: v for k, v in os.environ.items() if k != "XDG_CACHE_HOME"}
    env["XDG_CACHE_HOME"] = str(xdg)
    args = ["--model", "build/model/parts.json", *COMMON, *EXPECT]
    procs = [subprocess.Popen([PY, str(SCRIPT), *args], cwd=r, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
             for r in roots]
    outs = [p.communicate(timeout=300)[0] for p in procs]
    for r, p, out in zip(roots, procs, outs):
        assert "Traceback" not in out, out[-2000:]
        assert p.returncode == 0, f"{r}: rc={p.returncode}\n{out[-1500:]}"
        assert {frozenset({x["a"], x["b"]}): x["status"] for x in json.loads(report_file(r).read_text())["pairs"]} == COLD_STATUS


def test_c2_write_atomic_temp_name_is_unique_per_call(tmp_path, monkeypatch):
    """Two writers of one file in one pid namespace (two containers both see pid 1, or two threads) must not share a temp
    file: os.getpid is pinned to a constant and a second write_atomic of the same path starts while the first is writing."""
    mod = load_script()
    monkeypatch.setattr(mod.os, "getpid", lambda: 1)
    target = tmp_path / "pairs.json"
    seen = []

    def inner(tmp):                 # (the temp file may already exist, empty: mkstemp reserves the name that way)
        seen.append(Path(tmp).name)
        Path(tmp).write_text('{"inner": 1}')

    def outer(tmp):
        seen.append(Path(tmp).name)
        Path(tmp).write_text('{"outer": 1}')
        mod.write_atomic(target, inner)
        assert Path(tmp).read_text() == '{"outer": 1}', "the inner call wrote into the outer call's temp file"

    mod.write_atomic(target, outer)
    assert len(set(seen)) == 2, f"both calls used the temp name {seen}"
    assert json.loads(target.read_text()) == {"outer": 1}, "the outer write lost its own temp file to the inner one"


def test_c2_young_pair_temp_file_is_left_alone_old_one_removed(warm):
    """pairs.tmp-*.json younger than a few minutes may be another run's write in progress: kept. An old one is litter: removed."""
    out = warm / "build/clearance"
    young, old = out / "pairs.tmp-young.json", out / "pairs.tmp-old.json"
    young.write_text("{}")
    old.write_text("{}")
    hour_ago = time.time() - 3600
    os.utime(old, (hour_ago, hour_ago))
    r = run_model(warm)
    assert r.report is not None, r.out[-1500:]
    assert young.exists(), "a fresh pairs.tmp-*.json (another run writing) was deleted"
    assert not old.exists(), "an hour-old pairs.tmp-*.json is left from a killed run and must be removed"


# ---------------------------------------------------------------- group rule: the viewer's name fallback and cycle.groups[g].parts

@pytest.fixture(scope="module")
def viewer_names(cold, tmp_path_factory):
    """screw: one part, two placements "screw" and "screw_2" (cad_incremental names repeats so); node_groups maps only
    "screw" -> slider. clamp: in no node_groups, no motion_group field, listed by part id in cycle.groups.slider.parts.
    All three overlap the carriage at home and stick out 3 mm past its front, so at full travel they reach into stop."""
    root = shutil.copytree(cold["model_dir"], tmp_path_factory.mktemp("vnames") / "proj")
    parts = read_parts(root)
    key = write_part_step(root, "screw", (5, 4, 5), (0, 0, 0))
    parts.append({"name": "screw", "id": "screw", "key": key, "process": "bought", "qty": 2, "step": f"build/model/step/{key}.step",
                  "placements": [{"name": "screw", "loc": loc_matrix((40.5, 14, 15.5), 0)},
                                 {"name": "screw_2", "loc": loc_matrix((40.5, 46, 15.5), 0)}]})
    add_box_part(root, parts, "clamp", (38, 26, 13), (43, 30, 18))
    write_parts(root, parts)
    groups = json.loads(json.dumps(GROUPS))
    groups["slider"]["parts"] = ["clamp"]
    motion = {"cycle": {"period_s": PERIOD, "groups": groups, "node_groups": dict(NODE_GROUPS, screw="slider")}}
    (root / "build/twin/motion.json").write_text(json.dumps(motion, indent=1))
    r = run_model(root)
    r.status                                   # asserts this run wrote a report
    return {frozenset({x["a"], x["b"]}): x for x in r.report["pairs"]}


@pytest.mark.parametrize("name", ["screw", "screw_2"])
def test_c4_repeat_placement_name_falls_back_to_its_base_name(viewer_names, name):
    """node_groups {"screw": "slider"} also covers "screw_2" (motion.mjs strips a trailing _<digits>)."""
    row = viewer_names.get(frozenset({"carriage", name}))
    assert row is None, f"{name} must move with the carriage: {row}"
    row = viewer_names.get(frozenset({name, "stop"}))
    assert row is not None and row["status"] == "HIT" and "slider" in row["groups"], f"{name} rides into stop as a slider part: {row}"


def test_c4_cycle_group_parts_list_assigns_the_group(viewer_names):
    """cycle.groups.slider.parts = ["clamp"] (part ids, as motion.mjs matches them) puts clamp in slider."""
    row = viewer_names.get(frozenset({"carriage", "clamp"}))
    assert row is None, f"clamp must move with the carriage: {row}"
    row = viewer_names.get(frozenset({"clamp", "stop"}))
    assert row is not None and row["status"] == "HIT" and "slider" in row["groups"], f"clamp rides into stop as a slider part: {row}"
