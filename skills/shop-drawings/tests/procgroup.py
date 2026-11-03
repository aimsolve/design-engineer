"""Child processes that cannot outlive their parent: every child runs in its own process group (start_new_session) with a timeout,
and the whole group is killed on timeout, on any exception (KeyboardInterrupt, pytest-timeout), on SIGTERM and at interpreter exit.
Drop-in for subprocess.run / Popen / call. Default timeout: PROCGROUP_TIMEOUT seconds (env), else 1800.

A kill is SIGTERM to the group, then SIGKILL after a grace period. The grace lets a child that is itself a procgroup user (derive.py
under a test) forward the SIGTERM to its own groups, which sit in sessions of their own that the outer killpg cannot reach.
Importing this module installs a SIGTERM handler (if none is set) that does that forwarding and exits with 128 + SIGTERM."""
import atexit
import os
import signal
import subprocess
import threading
import time

DEFAULT_TIMEOUT = float(os.environ.get("PROCGROUP_TIMEOUT", "1800"))
GRACE = 5.0                                     # outer wait for a child to exit after SIGTERM
FORWARD_GRACE = 2.0                             # the SIGTERM handler's wait for its own children; shorter, so nesting unwinds in time
_live = {}
_lock = threading.RLock()                       # re-entrant: the SIGTERM handler may run while the main thread holds it


def _signal(p, sig):
    try:
        os.killpg(p.pid, sig)                   # pid == pgid: the child is its own session leader
    except (ProcessLookupError, PermissionError):
        pass


def kill_group(p):
    """SIGKILL whatever is left in the group of an already finished p. (The pgid outlives its reaped leader only while members remain,
    so in the tiny window after the last one exits it could in theory name a new session; accepted.)"""
    _signal(p, signal.SIGKILL)


def terminate_group(p, grace=GRACE):
    """SIGTERM the group, wait up to grace for p to exit, then SIGKILL the group."""
    _signal(p, signal.SIGTERM)
    end = time.monotonic() + grace
    while p.poll() is None and time.monotonic() < end:
        time.sleep(0.05)
    _signal(p, signal.SIGKILL)


def popen(cmd, **kw):
    p = subprocess.Popen(cmd, start_new_session=True, **kw)
    with _lock:
        _live[p.pid] = p
    return p


def _done(p):
    with _lock:
        _live.pop(p.pid, None)


def reap(p):
    """Kill the group of p and collect it; returns what it had written to its pipes, (None, None) if they stay held open."""
    terminate_group(p)
    try:
        out = p.communicate(timeout=10)
    except subprocess.TimeoutExpired:           # a process that left the group still holds the pipe
        out = (None, None)
    _done(p)
    return out


def _wait(p, input, timeout):
    try:
        return p.communicate(input, timeout=DEFAULT_TIMEOUT if timeout is None else timeout)
    except subprocess.TimeoutExpired as e:
        e.output, e.stderr = reap(p)
        raise
    except BaseException:
        reap(p)
        raise


def finish(p, timeout=None):
    """communicate() with the group killed on timeout or any exception; returns (stdout, stderr)."""
    out = _wait(p, None, timeout)
    kill_group(p)                               # stragglers the child left in its group
    _done(p)
    return out


def run(cmd, *, timeout=None, check=False, input=None, capture_output=False, **kw):
    if capture_output:
        if "stdout" in kw or "stderr" in kw:
            raise ValueError("stdout and stderr arguments may not be used with capture_output.")
        kw["stdout"] = kw["stderr"] = subprocess.PIPE
    if input is not None:
        if "stdin" in kw:
            raise ValueError("stdin and input arguments may not both be used.")
        kw["stdin"] = subprocess.PIPE
    p = popen(cmd, **kw)
    out, err = _wait(p, input, timeout)
    kill_group(p)
    _done(p)
    cp = subprocess.CompletedProcess(cmd, p.returncode, out, err)
    if check:
        cp.check_returncode()
    return cp


def call(cmd, *, timeout=None, **kw):
    return run(cmd, timeout=timeout, **kw).returncode


def kill_all(grace=0.0):
    with _lock:
        procs = list(_live.values())
        _live.clear()
    for p in procs:
        _signal(p, signal.SIGTERM if grace else signal.SIGKILL)
    end = time.monotonic() + grace
    while procs and any(p.poll() is None for p in procs) and time.monotonic() < end:
        time.sleep(0.05)
    for p in procs:
        _signal(p, signal.SIGKILL)


atexit.register(kill_all)


def _on_term(signum, frame):
    kill_all(FORWARD_GRACE)
    raise SystemExit(128 + signum)


if threading.current_thread() is threading.main_thread() and signal.getsignal(signal.SIGTERM) == signal.SIG_DFL:
    signal.signal(signal.SIGTERM, _on_term)
