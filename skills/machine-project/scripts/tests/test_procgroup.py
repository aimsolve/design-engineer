"""procgroup: a child (and its grandchildren) must not outlive a timeout or an interrupt."""
import os
import signal
import subprocess
import sys
import threading
import time

import procgroup

SLEEPER = "import subprocess,sys,time; c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)']); print(c.pid, flush=True); time.sleep(300)"


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()[:1] != "Z"


def _gone(pid):
    end = time.time() + 5
    while time.time() < end and _alive(pid):
        time.sleep(0.1)
    return not _alive(pid)


def test_timeout_kills_child_and_grandchild():
    p = procgroup.popen([sys.executable, "-c", SLEEPER], stdout=subprocess.PIPE, text=True)
    grand = int(p.stdout.readline())
    try:
        procgroup.finish(p, timeout=1)
        assert False, "should time out"
    except subprocess.TimeoutExpired:
        pass
    assert _gone(p.pid) and _gone(grand)


def test_interrupt_leaves_no_process_behind():
    pids = {}

    def interrupt():
        while "child" not in pids:
            time.sleep(0.05)
        time.sleep(0.5)
        signal.pthread_kill(threading.main_thread().ident, signal.SIGINT)

    real_popen = procgroup.popen

    def spy(cmd, **kw):
        p = real_popen(cmd, **kw)
        pids["child"] = p.pid
        return p

    procgroup.popen = spy
    threading.Thread(target=interrupt, daemon=True).start()
    try:
        procgroup.run([sys.executable, "-c", SLEEPER], capture_output=True, text=True, timeout=60)
        assert False, "should be interrupted"
    except KeyboardInterrupt:
        pass
    finally:
        procgroup.popen = real_popen
    assert _gone(pids["child"])


SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# A procgroup user in the middle (like derive.py under a test): it runs SLEEPER through procgroup, which puts SLEEPER in a session of
# its own, out of reach of a killpg on the middle's group. Prints the sleeper's and its child's pids.
MIDDLE = ("import sys; sys.path.insert(0, %r); import procgroup, subprocess; "
          "p = procgroup.popen([sys.executable, '-c', %r], stdout=subprocess.PIPE, text=True); "
          "print(p.pid, p.stdout.readline().strip(), flush=True); procgroup.finish(p)") % (SCRIPTS, SLEEPER)


def _middle():
    p = procgroup.popen([sys.executable, "-c", MIDDLE], stdout=subprocess.PIPE, text=True)
    return p, [int(x) for x in p.stdout.readline().split()]


def test_nested_timeout_kills_every_level():
    p, inner = _middle()
    try:
        procgroup.finish(p, timeout=1)
        assert False, "should time out"
    except subprocess.TimeoutExpired:
        pass
    assert _gone(p.pid) and all(_gone(pid) for pid in inner)


def test_sigterm_to_a_procgroup_user_kills_its_children():
    p, inner = _middle()
    os.kill(p.pid, signal.SIGTERM)
    assert p.wait(timeout=10) == 128 + signal.SIGTERM
    procgroup.reap(p)
    assert all(_gone(pid) for pid in inner)


def test_timeout_keeps_partial_output():
    try:
        procgroup.run([sys.executable, "-c", "import time; print('started', flush=True); time.sleep(60)"], capture_output=True,
                      text=True, timeout=1)
        assert False, "should time out"
    except subprocess.TimeoutExpired as e:
        assert "started" in e.output


def test_shop_drawings_copy_is_identical():
    with open(os.path.join(SCRIPTS, "procgroup.py"), "rb") as a, \
            open(os.path.join(SCRIPTS, "..", "..", "shop-drawings", "tests", "procgroup.py"), "rb") as b:
        assert a.read() == b.read(), "skills/shop-drawings/tests/procgroup.py is a copy of scripts/procgroup.py: keep them identical"
