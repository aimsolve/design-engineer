"""`de job run` / `job run`: the skills' heavy-work door, run right here locally (stage 1).
Serves done-when 4 (the agent follows the skills, which say `job run "..."`, and that must work in plain Claude Code)."""
import os
import subprocess
import time

import pytest

from conftest import DE, JOB, clean_env, de, run


@pytest.fixture
def here(tmp_path):
    d = tmp_path / "jobdir"
    d.mkdir()
    return d


def test_runs_in_the_current_directory_and_keeps_the_exit_code(here):
    r = de("job", "run", "pwd; echo out-line; echo err-line >&2; touch made-here; exit 3", cwd=here, timeout=60)
    assert r.rc == 3, r.why("job run ... exit 3")
    assert "out-line" in r.out and "err-line" in r.text, r.why("output of the command")
    assert os.path.realpath(r.out.splitlines()[0].strip()) == os.path.realpath(here), r.why("pwd")
    assert (here / "made-here").exists(), "the command did not run in the current directory"


def test_success_is_exit_zero(here):
    r = de("job", "run", "true", cwd=here, timeout=60)
    assert r.rc == 0, r.why("job run true")


def test_platform_flags_are_accepted_and_ignored(here):
    r = de("job", "run", "echo ok", "--needs", "freecad", "--expect", "long", cwd=here, timeout=60)
    assert r.rc == 0 and "ok" in r.out, r.why("job run --needs --expect")


def test_output_is_streamed_while_the_command_runs(here):
    assert DE.exists(), f"no {DE}"
    p = subprocess.Popen([str(DE), "job", "run", "echo first; sleep 4; echo second"], cwd=here, env=clean_env(),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    t0 = time.time()
    try:
        line = p.stdout.readline()
        took = time.time() - t0
        p.wait(timeout=30)
    finally:
        if p.poll() is None:
            p.kill()
    assert "first" in line, f"first output line: {line!r}"
    assert took < 3, f"the first line came after {took:.1f} s: output is buffered until the command ends"


def test_timeout_kills_the_whole_process_group_and_exits_124(here):
    pidfile = here / "child.pid"
    t0 = time.time()
    r = de("job", "run", f"sleep 60 & echo $! > {pidfile}; wait", "--timeout", "2", cwd=here, timeout=40)
    assert r.rc == 124, r.why("job run --timeout 2 on a 60 s command")
    assert time.time() - t0 < 20, "the timeout did not stop the job in time"
    time.sleep(0.5)
    pid = int(pidfile.read_text().strip())
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    if alive:
        os.kill(pid, 9)
    assert not alive, "the command's background child survived the timeout"


def test_job_shim_runs_the_same(here):
    assert JOB.exists(), f"no {JOB}: the skills' `job run \"...\"` has no local door"
    r = run([JOB, "run", "echo via-shim; exit 5", "--needs", "heavy"], cwd=here, timeout=60)
    assert r.rc == 5 and "via-shim" in r.out, r.why("job run via the shim")
