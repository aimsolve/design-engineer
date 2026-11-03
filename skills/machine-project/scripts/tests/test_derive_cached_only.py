"""derive.py --cached-only (done-when 6): the router's download probe after a model rebuild.

  derive.py --no-rebuild --cached-only SCOPE MANIFEST [PART_ID]
  - every result it needs is already in build/derived/ -> writes MANIFEST as a normal run would and exits 0;
  - any result would have to be computed -> exit 3, computes nothing, runs no GC (nothing under build/ is deleted).

Model on disk = helpers.make_derived_model (hand-written parts.json + STEPs), as in test_derive.py.

  cd agent/skills/machine-project/scripts
  env -u VIRTUAL_ENV uv run --isolated --no-project --python 3.12 --with build123d --with pytest \
      pytest tests/test_derive_cached_only.py -q -W ignore
"""
import json
import sys
from pathlib import Path

from build123d import Pos, Sphere

import helpers as H
import procgroup

FLAGS = ("--no-rebuild", "--cached-only")


def run(root, scope, manifest, part_id=None):
    args = [sys.executable, str(H.DERIVE), *FLAGS, scope, str(manifest)] + ([part_id] if part_id else [])
    cp = procgroup.run(args, cwd=root, env=H.env_for(root), capture_output=True, text=True, timeout=300)
    return cp.returncode, (cp.stdout or "") + (cp.stderr or "")


def snapshot(root):
    """Every file under build/ -> (mtime_ns, size): a change, a new file or a deleted one shows."""
    b = Path(root) / "build"
    return {str(p.relative_to(b)): (p.stat().st_mtime_ns, p.stat().st_size) for p in b.rglob("*") if p.is_file()}


def test_everything_cached_writes_the_manifest_and_exits_0(dproj):
    full = H.run_derive(dproj, "print").ok()
    out = dproj / "build" / "probe_print.json"
    rc, log = run(dproj, "print", out)
    assert rc == 0, log[-1500:]
    assert out.is_file(), "the manifest piece is written as usual"
    m = json.loads(out.read_text())
    assert {e["id"] for e in m["parts"]} == {e["id"] for e in full.manifest["parts"]}
    assert m["parts"] == full.manifest["parts"], "the same rows and files as the run that computed them"
    assert "computed 0" in log, log[-500:]


def test_one_cached_part_writes_its_piece_and_exits_0(dproj):
    H.run_derive(dproj, "print").ok()
    out = dproj / "build" / "probe_cap.json"
    rc, log = run(dproj, "print", out, "cap")
    assert rc == 0, log[-1500:]
    m = json.loads(out.read_text())
    assert [e["id"] for e in m["parts"]] == ["cap"]
    for f in H.file_list(m["parts"][0], dproj):
        assert f.is_file(), f


def test_a_changed_part_exits_3_and_computes_nothing(dproj):
    H.run_derive(dproj, "print").ok()
    H.replace_part(dproj, "knob", shape=Pos(100, 0, 0) * Sphere(6))     # a new key: its STL is not cached
    dirs = H.derived_dirs(dproj)
    out = dproj / "build" / "probe_knob.json"
    rc, log = run(dproj, "print", out, "knob")
    assert rc == 3, f"rc={rc}\n{log[-1500:]}"
    assert H.derived_dirs(dproj) == dirs, "nothing computed (no new derived dir)"
    assert not out.exists(), "no manifest piece for something it did not make"


def test_a_scope_with_one_changed_part_exits_3(dproj):
    H.run_derive(dproj, "print").ok()
    H.replace_part(dproj, "knob", shape=Pos(100, 0, 0) * Sphere(6))
    rc, log = run(dproj, "print", dproj / "build" / "probe_all.json")
    assert rc == 3, f"rc={rc}\n{log[-1500:]}"


def test_an_unchanged_part_next_to_a_changed_one_is_still_served(dproj):
    H.run_derive(dproj, "print").ok()
    H.replace_part(dproj, "knob", shape=Pos(100, 0, 0) * Sphere(6))
    rc, log = run(dproj, "print", dproj / "build" / "probe_cap.json", "cap")
    assert rc == 0, f"cap did not change, its STL is cached: rc={rc}\n{log[-1500:]}"


def test_exit_3_runs_no_gc_nothing_is_deleted(dproj):
    H.run_derive(dproj, "print").ok()
    H.run_derive(dproj, "step").ok()
    H.replace_part(dproj, "knob", shape=Pos(100, 0, 0) * Sphere(6))     # the old knob results are now garbage for a normal GC
    for f in (dproj / "build").glob("manifest_*.json"):
        f.unlink()                                                    # no kit manifest keeps them alive either
    before = snapshot(dproj)
    rc, log = run(dproj, "print", dproj / "build" / "probe_knob.json", "knob")
    assert rc == 3, f"rc={rc}\n{log[-1500:]}"
    after = snapshot(dproj)
    gone = sorted(set(before) - set(after))
    assert gone == [], f"--cached-only deleted files (GC ran): {gone[:10]}"


def test_a_normal_run_still_computes_and_gcs_afterwards(dproj):
    """Control: the same situation without --cached-only computes the part (so the exit-3 cases above are about the flag)."""
    H.run_derive(dproj, "print").ok()
    H.run_derive(dproj, "step").ok()
    H.replace_part(dproj, "knob", shape=Pos(100, 0, 0) * Sphere(6))
    for f in (dproj / "build").glob("manifest_*.json"):
        f.unlink()
    before = snapshot(dproj)
    d = H.run_derive(dproj, "print", part_id="knob").ok()
    assert d.computed == 1, d.log
    assert set(before) - set(snapshot(dproj)), "in this state a normal run's GC deletes the old knob results"
