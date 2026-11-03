import fcntl
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # procgroup.py
os.environ.setdefault("PROCGROUP_TIMEOUT", "900")
import helpers as H


def _clean_copy(src, dst):
    shutil.copytree(src, dst)          # copy2: file mtimes are preserved, which the incremental build keys on
    for p in Path(dst).rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    shutil.rmtree(Path(dst) / "counter", ignore_errors=True)
    return dst


def _model_bytes(root):
    """Relative path -> sha256 of every file under build/model (contents only, no mtimes)."""
    base = Path(root) / "build" / "model"
    return {str(p.relative_to(base)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(base.rglob("*")) if p.is_file()}


def _shared(tmp_path_factory, name, build):
    """Run `build(dir)` once per pytest run, however many xdist workers: whoever takes the lock first builds into a
    directory under the run's shared base temp; everybody else waits on the lock and reuses it."""
    base = tmp_path_factory.getbasetemp()
    if os.environ.get("PYTEST_XDIST_WORKER"):     # workers live in a subdir of the run base, which is shared
        base = base.parent
    shared = base / f"shared_{name}"
    shared.mkdir(exist_ok=True)
    with open(shared / ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        if not (shared / "done").exists():
            build(shared)
            (shared / "done").write_text("1")
    return shared


def _build_baseline(shared):
    root = H.make_project(shared / "proj")
    run = H.run_model(root)
    (shared / "cold.json").write_text(json.dumps({"rc": run.rc, "log": run.log}))


def _verify_copy_once(shared):
    """A copied cold build must be byte-identical to a fresh one: asserted once per run, by whichever worker gets here first,
    outside the build lock so nobody waits for it."""
    root = shared / "proj"
    with open(shared / ".verify", "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return
        if (shared / "verified").exists() or not (root / "build/model/parts.json").exists():
            return
        fresh = H.make_project(shared / "fresh_check")
        assert H.run_model(fresh).rc == 0
        assert _model_bytes(fresh) == _model_bytes(root), "a fresh cold build differs from the cached one"
        probe = _clean_copy(root, shared / "copy_check")
        assert _model_bytes(probe) == _model_bytes(root), "the copied cold build differs from the original"
        names = lambda r: {str(p.relative_to(r)): p.stat().st_mtime_ns for p in (r / "mechanics/cad/parts").rglob("*.py")}
        assert names(probe) == names(root), "the copy lost source mtimes"
        (shared / "verified").write_text("1")


class _CP:
    def __init__(self, d):
        self.returncode, self.stdout, self.stderr = d["rc"], d["log"], ""


@pytest.fixture(scope="session")
def baseline(tmp_path_factory):
    """(root, Run): the synthetic project after one clean --model build, built once per run. Tests read it only."""
    shared = _shared(tmp_path_factory, "base", _build_baseline)
    _verify_copy_once(shared)
    return shared / "proj", H.Run(_CP(json.loads((shared / "cold.json").read_text())))


@pytest.fixture
def project(baseline, tmp_path):
    """A private copy of the baseline after one clean --model build (a failed baseline surfaces as failing assertions)."""
    root, run = baseline
    return _clean_copy(root, tmp_path / "proj")


@pytest.fixture
def fresh(tmp_path):
    return H.make_project(tmp_path / "fresh")


@pytest.fixture(scope="session")
def derived_base(tmp_path_factory):
    shared = _shared(tmp_path_factory, "dbase", lambda s: H.make_derived_model(s / "proj"))
    return shared / "proj"


@pytest.fixture
def dproj(derived_base, tmp_path):
    return _clean_copy(derived_base, tmp_path / "proj")


def pytest_collection_modifyitems(config, items):
    """Per-test limit for pytest-timeout (run with `--with pytest-timeout`; without the plugin no mark is added). Set here, not in
    pytest.ini, because a run over both test dirs finds no common ini. A hung test is killed with its child process groups (procgroup)."""
    if not config.pluginmanager.hasplugin("timeout"):
        return
    for it in items:
        if it.get_closest_marker("timeout") is None:
            it.add_marker(pytest.mark.timeout(1200))
