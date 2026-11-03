"""Shared harness for the local-mode tests of the design-engineer plugin (stage 1: init / build / check / serve / job / hooks).

Everything here drives the plugin through the doors a user has: the `de` command (agent/local/bin/de, or $DE), install.sh,
the HTTP server `de serve` starts, the Claude Code hooks registered in agent/hooks/hooks.json. Nothing imports the implementation.

  cd agent && uv run --group dev pytest tests -q              # everything except the opt-in Claude run
  cd agent && uv run --group dev pytest tests -q -m "not slow"  # the fast part (no model build)
  DE_E2E_CLAUDE=1 ... pytest tests/test_claude_e2e.py           # the real `claude -p` run (~20 min, uses the subscription)

A missing `de` is reported as a failed test (rc 127 + the path), never as a collection error.
"""
import http.client as httpclient
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]          # the plugin root (agent/)
REPO = AGENT.parent                                   # the checkout around the plugin (the private platform repo; public: outside)
DE = Path(os.environ.get("DE") or AGENT / "local" / "bin" / "de")
JOB = AGENT / "local" / "bin" / "job"
PROJECT_NAME = "nema-mount"                           # folder of the shared built project = its default name and its slug
URL_RE = re.compile(r"http://127\.0\.0\.1:(\d+)/([a-z0-9-]+)/")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: builds a model (30-60 s each) or runs a long command")
    config.addinivalue_line("markers", "e2e: drives a real browser or a real Claude Code session")


class Result:
    def __init__(self, rc, out, err):
        self.rc, self.out, self.err = rc, out, err

    @property
    def text(self):
        return (self.out or "") + "\n" + (self.err or "")

    def why(self, what):
        return f"{what}: exit {self.rc}\n--- stdout ---\n{(self.out or '')[-3000:]}\n--- stderr ---\n{(self.err or '')[-3000:]}"


def clean_env(**extra):
    """The user's shell, minus anything that would make the plugin think it runs on the hosted platform."""
    env = {k: v for k, v in os.environ.items() if k not in ("ENGINEER_API", "DE_ROOT", "CLAUDE_PROJECT_DIR", "VIRTUAL_ENV")}
    env.update({k: str(v) for k, v in extra.items()})
    return env


def run(cmd, cwd=None, timeout=900, env=None, input=None):
    """Run a command in its own process group; a timeout kills the whole group. A missing executable -> rc 127."""
    try:
        p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env or clean_env(), stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    except (FileNotFoundError, PermissionError) as e:
        return Result(127, "", f"cannot run {cmd[0]}: {e}")
    try:
        out, err = p.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, err = p.communicate()
        return Result(-9, out, (err or "") + f"\n[test] killed after {timeout} s")
    return Result(p.returncode, out, err)


def de(*args, cwd=None, timeout=900, env=None):
    return run([DE, *args], cwd=cwd, timeout=timeout, env=env)


def init_project(parent: Path, name: str = PROJECT_NAME, *args) -> tuple[Path, Result]:
    d = Path(parent) / name
    d.mkdir(parents=True, exist_ok=True)
    return d, de("init", d, *args, cwd=parent, timeout=300)


def part_files(project: Path):
    """The model's part files (not node __init__.py, not fasteners)."""
    parts = Path(project) / "mechanics" / "cad" / "parts"
    return sorted(p for p in parts.rglob("*.py") if p.name != "__init__.py" and "fasteners" not in p.parts)


def parts_json(project: Path):
    return json.loads((Path(project) / "build" / "model" / "parts.json").read_text())["parts"]


def http(method, port, path, body=None, headers=None, timeout=30):
    """One request, redirects NOT followed. -> (status, headers dict lower-case, body bytes)."""
    c = httpclient.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        c.close()


def inbox(project: Path):
    d = Path(project) / ".engineer" / "inbox"
    return {p.relative_to(d) for p in d.rglob("*") if p.is_file()} if d.is_dir() else set()


def serve(project: Path, *args, timeout=120):
    """`de serve <project> --port 0 --no-open ...` -> (Result, port or None, slug or None)."""
    r = de("serve", project, "--port", "0", "--no-open", *args, cwd=project, timeout=timeout)
    m = URL_RE.search(r.text)
    return r, (int(m.group(1)) if m else None), (m.group(2) if m else None)


def stop(project: Path):
    de("serve", project, "--stop", cwd=project, timeout=60)


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """ONE project for the whole run: `de init` in an empty folder named nema-mount, then `de build`. Tests that change it copy it."""
    base = tmp_path_factory.mktemp("built")
    d, ri = init_project(base)
    rb = de("build", d, cwd=d, timeout=1200) if ri.rc == 0 else Result(None, "", "skipped: de init failed")
    return {"dir": d, "init": ri, "build": rb}


def need_built(built) -> Path:
    """In the test body (a missing `de` is a FAILED test, not a setup ERROR): the shared project was made and built."""
    assert built["init"].rc == 0, built["init"].why("de init")
    assert built["build"].rc == 0, built["build"].why("de build")
    return built["dir"]


def need_init(init, broken=False):
    """(dir, Result) of a fixture's `de init` -> dir; broken=True also puts a syntax error into the first part file -> (dir, part)."""
    d, r = init
    assert r.rc == 0, r.why("de init")
    if not broken:
        return d
    part = part_files(d)[0]
    part.write_text(part.read_text() + "\n\ndef oops(:\n    pass\n")       # a Python syntax error
    return d, part


@pytest.fixture(scope="session")
def served(built):
    """`de serve` on the built project (background, any free port); stopped at the end of the run."""
    d = built["dir"]
    if built["build"].rc != 0:
        yield {"dir": d, "built": built, "result": built["build"], "port": None, "slug": None}
        return
    r, port, slug = serve(d)
    if port:                                   # the server may take a moment after printing the URL
        for _ in range(60):
            try:
                http("GET", port, "/", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
    yield {"dir": d, "built": built, "result": r, "port": port, "slug": slug}
    stop(d)


def need_server(served):
    need_built(served["built"])
    assert served["result"].rc == 0, served["result"].why("de serve")
    assert served["port"], served["result"].why("de serve printed no http://127.0.0.1:<port>/<slug>/ URL")
    return served


def copy_project(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst, symlinks=True)        # copy2: mtimes kept (the incremental build keys on them)
    return dst
