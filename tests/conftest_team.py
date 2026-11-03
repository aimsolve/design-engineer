"""Shared harness for the team-mode tests (stage 2: a local manager + background executors).

Drives the plugin only through its doors: `de init --team`, `de team on|off`, `de issue ...` and the `issue` shim,
`de exec start|stop`, the hooks registered in agent/hooks/hooks.json. Nothing imports the implementation.

No real Claude Code is ever started from here: every command runs with $DE_CLAUDE pointing at a fake `claude` AND with a
directory holding a fake `claude` first on PATH. The fake records each call (argv, cwd, pid, ENG_LANE/ENG_ISSUE) as one JSON
line in $FAKE_CLAUDE_LOG and behaves per $FAKE_CLAUDE_MODE:
  bgnoid `--bg` works but prints no session id (exit 0)
  bg    `--bg` works: prints a session id (a UUID) and exits 0 (with FAKE_CLAUDE_WORK=1 it first spawns a detached worker
        that runs `issue show` + `issue close "<result>"` as the executor would);
  nobg  `--bg` fails with "error: unknown option '--bg'" (an old Claude Code); `-p` prints a marker line and then sleeps
        (FAKE_CLAUDE_SLEEP s, default 600) like a long-running executor; with FAKE_CLAUDE_WORK=1 it hands over first.
Any other call (a stop/kill subcommand, `agents`, ...) is recorded and exits 0.
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import AGENT, DE, Result, clean_env, init_project, run

ISSUE_SHIM = AGENT / "local" / "bin" / "issue"
HOOKS = AGENT / "hooks" / "hooks.json"
MANAGER_RULES = AGENT / "local" / "roles" / "manager.md"
EXECUTOR_RULES = AGENT / "local" / "roles" / "executor.md"
PRINCIPLES = AGENT / "PRINCIPLES.md"
LANE_VARS = ("ENG_LANE", "ENG_ISSUE", "DE_CLAUDE", "FAKE_CLAUDE_LOG", "FAKE_CLAUDE_MODE", "FAKE_CLAUDE_WORK", "FAKE_CLAUDE_SLEEP")
UNKNOWN_BG = "error: unknown option '--bg'"
FAKE_MARKER = "FAKE-CLAUDE-OUTPUT"

FAKE_CLAUDE = r'''#!{python}
import json, os, subprocess, sys, time, uuid
argv = sys.argv[1:]
mode = os.environ.get("FAKE_CLAUDE_MODE", "bg")
work = os.environ.get("FAKE_CLAUDE_WORK") == "1"
issue = os.environ.get("FAKE_ISSUE", "issue")
rec = {{"argv": argv, "cwd": os.getcwd(), "pid": os.getpid(), "time": time.time(),
       "env": {{k: os.environ.get(k) for k in ("ENG_LANE", "ENG_ISSUE")}}}}

def log(**extra):
    rec.update(extra)
    with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
        f.write(json.dumps(rec) + "\n")

if "--bg" in argv and mode == "nobg":
    log(refused=True)
    print("error: unknown option '--bg'", file=sys.stderr)
    sys.exit(1)
if argv[:2] == ["agents", "--json"]:   # the background sessions this fake started and nobody stopped, as `claude agents --json`
    calls = [json.loads(x) for x in open(os.environ["FAKE_CLAUDE_LOG"])]
    stopped = {{c["argv"][1] for c in calls if c["argv"][:1] == ["stop"] and len(c["argv"]) > 1}}
    log()
    print(json.dumps([{{"id": c["session"], "name": c["argv"][c["argv"].index("--name") + 1], "status": "running"}}
                      for c in calls if c.get("session") and c["session"] not in stopped]))
    sys.exit(0)
if "--bg" in argv and mode == "bgnoid":
    log(session=None)
    print("Started in the background")
    sys.exit(0)
if "--bg" in argv:
    sid = str(uuid.uuid4())
    worker = None
    if work:
        w = subprocess.Popen(["sh", "-c", 'sleep 1; "$0" show >/dev/null; "$0" close "fake result: the bracket is modelled"', issue],
                             start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        worker = w.pid
    log(session=sid, worker=worker)
    print(f"Started background session {{sid}}")
    print(f"session_id: {{sid}}")
    sys.exit(0)
if "-p" in argv or "--print" in argv:
    log(print_mode=True)
    print("FAKE-CLAUDE-OUTPUT working on it", flush=True)
    if work:
        subprocess.run([issue, "show"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([issue, "close", "fake result: the bracket is modelled"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "600")))
    sys.exit(0)
log()
print("ok")
'''


# ---------------------------------------------------------------- the fake claude
class Fake:
    def __init__(self, root: Path):
        self.bin = root / "fakebin"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.path = self.bin / "claude"
        self.path.write_text(FAKE_CLAUDE.format(python=sys.executable))
        self.path.chmod(0o755)
        self.log = root / "fake-claude.jsonl"
        self.log.touch()

    def calls(self):
        out = []
        for ln in self.log.read_text().splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
        return out

    def starts(self, n=None):
        """Calls that try to start a session (--bg or -p), optionally only those for issue n (by ENG_ISSUE)."""
        c = [x for x in self.calls() if "--bg" in x["argv"] or x.get("print_mode")]
        return [x for x in c if n is None or str(x["env"].get("ENG_ISSUE")) == str(n)]

    def wait_starts(self, n=None, count=1, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            s = self.starts(n)
            if len(s) >= count:
                return s
            time.sleep(0.2)
        return self.starts(n)

    def pids(self):
        out = []
        for c in self.calls():
            out += [p for p in (c.get("pid"), c.get("worker")) if p]
        return out


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    st = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


def wait_dead(pid: int, timeout=15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.2)
    return not alive(pid)


# ---------------------------------------------------------------- env + commands
def team_env(fake: Fake, mode="bg", lane=None, issue=None, work=False, sleep=None, **extra):
    """The user's shell with the fake claude in place. lane='exec' + issue=N = an executor session; else the manager."""
    env = {k: v for k, v in clean_env().items() if k not in LANE_VARS}
    env.update(DE_CLAUDE=str(fake.path), FAKE_CLAUDE_LOG=str(fake.log), FAKE_CLAUDE_MODE=mode, FAKE_ISSUE=str(ISSUE_SHIM),
               PATH=f"{fake.bin}{os.pathsep}{AGENT / 'local' / 'bin'}{os.pathsep}{env.get('PATH', '')}")
    if work:
        env["FAKE_CLAUDE_WORK"] = "1"
    if sleep is not None:
        env["FAKE_CLAUDE_SLEEP"] = str(sleep)
    if lane:
        env["ENG_LANE"] = lane
    if issue is not None:
        env["ENG_ISSUE"] = str(issue)
    env.update({k: str(v) for k, v in extra.items()})
    return env


class Team:
    """A `de init --team` project + the fake claude. t.mgr(...) runs `de issue ...` as the manager, t.ex(n, ...) as the
    executor of issue n."""

    def __init__(self, root: Path, name="team-proj", init_args=("--team",)):
        self.fake = Fake(root)
        self.root = root
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        self.init = run([DE, "init", d, *init_args], cwd=root, timeout=300, env=team_env(self.fake))
        self.dir = d
        self.mode = "bg"

    def need(self):
        assert self.init.rc == 0, self.init.why("de init --team")
        return self

    def env(self, **kw):
        kw.setdefault("mode", self.mode)
        return team_env(self.fake, **kw)

    def de(self, *args, timeout=120, **kw):
        return run([DE, *args], cwd=self.dir, timeout=timeout, env=self.env(**kw))

    def mgr(self, *args, timeout=120, **kw):
        return self.de("issue", *args, timeout=timeout, **kw)

    def ex(self, n, *args, timeout=120, **kw):
        return self.de("issue", *args, timeout=timeout, lane="exec", issue=n, **kw)

    def shim(self, *args, timeout=120, **kw):
        return run([ISSUE_SHIM, *args], cwd=self.dir, timeout=timeout, env=self.env(**kw))

    def new(self, title, body="Body of the task.", *extra, start=False, **kw):
        """`issue new` -> the number it printed (asserts it worked). start=False adds --no-start (no executor launched)."""
        args = ["new", "--title", title, "--body", body, *extra]
        if not start and "--waiting" not in extra:
            args.append("--no-start")
        r = self.mgr(*args, **kw)
        assert r.rc == 0, r.why(f"issue {' '.join(args)}")
        n = issue_number(r.out)
        assert n, r.why("issue new printed no #<number>")
        return n

    def issue(self, n):
        return issue_json(self.dir, n)

    def kill_all(self):
        for pid in self.fake.pids():
            for kill in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
                try:
                    kill()
                except (ProcessLookupError, PermissionError, OSError):
                    pass


@pytest.fixture
def team(tmp_path):
    t = Team(tmp_path)
    yield t
    try:
        if t.init.rc == 0 and (t.dir / ".engineer" / "issues").is_dir():
            for f in (t.dir / ".engineer" / "issues").glob("*.json"):
                t.de("exec", "stop", f.stem, timeout=30)
    finally:
        t.kill_all()


@pytest.fixture
def solo(tmp_path):
    """A plain single-lane local project (stage 1), with the fake claude around anyway."""
    t = Team(tmp_path, "solo-proj", init_args=())
    yield t
    t.kill_all()


# ---------------------------------------------------------------- reading the store
NUM_RE = re.compile(r"#(\d+)\b")


def issue_number(text):
    m = NUM_RE.search(text or "")
    return int(m.group(1)) if m else None


def issue_json(project: Path, n) -> dict:
    f = Path(project) / ".engineer" / "issues" / f"{n}.json"
    assert f.is_file(), f"no {f}"
    d = json.loads(f.read_text(encoding="utf-8"))
    assert isinstance(d, dict), f"{f} is not one JSON object"
    return d


def strings(obj):
    """Every string (and number, as text) anywhere inside a JSON value."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)
    elif isinstance(obj, (str, int, float)) and not isinstance(obj, bool):
        yield str(obj)


def has_string(obj, s) -> bool:
    return any(x == s for x in strings(obj))


def mentions(obj, s) -> bool:
    return any(s in x for x in strings(obj))


def state(t: Team, n):
    return t.issue(n).get("state")


def wait_state(t: Team, n, want, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if state(t, n) == want:
                return True
        except (AssertionError, ValueError):
            pass
        time.sleep(0.3)
    return False


# ---------------------------------------------------------------- hooks
def hook_commands(event, tool=None):
    assert HOOKS.is_file(), f"no {HOOKS}"
    cfg = json.loads(HOOKS.read_text())
    cmds = []
    for g in cfg.get("hooks", {}).get(event, []):
        if tool and not re.fullmatch(g.get("matcher", ".*") or ".*", tool):
            continue
        cmds += [h["command"] for h in g.get("hooks", []) if h.get("type") == "command"]
    return cmds


def fire(t: Team, event, payload=None, tool=None, lane=None, issue=None, timeout=300):
    """Run every command registered for `event` (and `tool`) the way Claude Code does: JSON on stdin, cwd = project,
    the session's env (ENG_LANE/ENG_ISSUE for an executor). -> list of Results."""
    cmds = hook_commands(event, tool)
    assert cmds, f"no {event} hook registered" + (f" for tool {tool}" if tool else "")
    data = {"hook_event_name": event, "cwd": str(t.dir), "session_id": "test-" + (lane or "manager"), "transcript_path": "/dev/null"}
    if tool:
        data.update(tool_name=tool, tool_input={"command": "ls"} if tool == "Bash" else {"file_path": str(t.dir / "CLAUDE.md")},
                    tool_response={"success": True})
    data.update(payload or {})
    env = t.env(lane=lane, issue=issue, CLAUDE_PLUGIN_ROOT=AGENT, CLAUDE_PROJECT_DIR=t.dir)
    out = []
    for c in cmds:
        c = c.replace("${CLAUDE_PLUGIN_ROOT}", str(AGENT)).replace("$CLAUDE_PLUGIN_ROOT", str(AGENT))
        out.append(run(["sh", "-c", c], cwd=t.dir, env=env, input=json.dumps(data), timeout=timeout))
    return out


def decisions(results):
    out = []
    for r in results:
        for chunk in (r.out.strip(), *r.out.strip().splitlines()):
            try:
                d = json.loads(chunk)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
    return out


def additional_context(results) -> str:
    """Strict: only hookSpecificOutput.additionalContext of the JSON the hooks printed (what PostToolUse hands Claude)."""
    return "\n".join((d.get("hookSpecificOutput") or {}).get("additionalContext", "") or "" for d in decisions(results))


def added_context(results) -> str:
    """SessionStart / UserPromptSubmit: Claude Code adds both additionalContext and plain stdout."""
    return additional_context(results) + "\n" + "\n".join(r.out for r in results)


def all_ok(results, what):
    for r in results:
        assert r.rc == 0, r.why(what)


def rules_line(path: Path) -> str:
    """A distinctive line of a rules file: its first non-empty, non-heading line."""
    assert path.is_file(), f"no {path}"
    line = next((ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")), "")
    assert line, f"{path} has no text"
    return line


def principle_sections():
    s = [ln.strip() for ln in PRINCIPLES.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")]
    assert s, "PRINCIPLES.md has no sections"
    return s


def lanes(project: Path) -> str:
    f = Path(project) / ".engineer" / "lanes"
    return f.read_text() if f.is_file() else ""


__all__ = ["Result"]
