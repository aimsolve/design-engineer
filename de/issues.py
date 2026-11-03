"""Local task backend of team mode: the platform's GitHub-issue lanes (manager + one executor per issue) on files in .engineer/.

  .engineer/lanes             `team` turns team mode on for the project (`de team on|off`, `de init --team`)
  .engineer/issues/<n>.json   one issue: {n, title, body, parts, state, labels, comments, executor, seen}
      state: open (an executor works) | waiting (waits for the user) | review (handed over) | closed
      comments: [{i, by: manager|executor|user, kind: comment|ask|handover|reopen|close, text, files, t}]
      seen: {lane: the last comment index that lane has seen} — the read cursor of each side

The lane is the environment: ENG_LANE=exec + ENG_ISSUE=<n> is that issue's executor (a background Claude Code session that
`de exec start` launched), anything else is the manager (the interactive session the user talks to). The ops and their
refusals follow the platform's `issue` CLI, so the roles' rules read the same in both."""
import argparse
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from de import cli

OPEN = ("open", "waiting", "review")
LABELS = ("stop", "urgent", "stuck")


# ---------------------------------------------------------------- storage

def team(d):
    try:
        return (d / ".engineer" / "lanes").read_text().strip() == "team"
    except OSError:
        return False


def idir(d):
    p = d / ".engineer" / "issues"
    p.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def locked(d):
    with open(idir(d) / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load(d, n):
    try:
        return json.loads((idir(d) / f"{int(n)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(d, x):
    p = idir(d) / f"{x['n']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(x, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def all_issues(d):
    out = []
    for p in sorted(idir(d).glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0):
        if p.stem.isdigit():
            x = load(d, p.stem)
            if x:
                out.append(x)
    return out


def add(x, by, kind, text, files=()):
    c = {"i": len(x["comments"]), "by": by, "kind": kind, "text": text, "files": list(files), "t": round(time.time())}
    x["comments"].append(c)
    return c


def unseen(x, lane, by_other=True):
    """Comments the lane has not seen yet (by default only those of the other side)."""
    cur = int((x.get("seen") or {}).get(lane, -1))
    side = "executor" if lane == "exec" else "manager"
    return [c for c in x["comments"] if c["i"] > cur and (not by_other or c["by"] != side)]


def mark_seen(x, lane):
    x.setdefault("seen", {})[lane] = len(x["comments"]) - 1


def fmt(c):
    files = f" [files: {', '.join(c['files'])}]" if c.get("files") else ""
    return f"[{c['by']} · {c['kind']}] {c['text']}{files}"


# ---------------------------------------------------------------- executors

def claude_bin():
    return os.environ.get("DE_CLAUDE") or shutil.which("claude") or "claude"


EXEC_TOOLS = ("Read Edit Write MultiEdit Glob Grep TodoWrite Bash(de *) Bash(job *) Bash(issue *) Bash(hw *) Bash(python *) "
              "Bash(python3 *) Bash(uv *) Bash(git status*) Bash(git diff*) Bash(git log*) Bash(ls *) Bash(cat *) Bash(mkdir *)")


def exec_prompt(x):
    return (f"You are the executor of issue #{x['n']} «{x['title']}» of this machine project (team mode). Start with "
            "`issue show`, then do the work as your rules say, and hand it over with `issue close \"result\"`.")


def start(d, n):
    """Launch issue n's executor: a Claude Code background session (`claude --bg`), else a detached `claude -p`."""
    x = load(d, n)
    if not x:
        return f"no issue #{n}", 1
    if x["state"] == "closed":
        return f"#{n} is closed", 1
    if running(d, x):
        return f"#{n}: its executor is already running ({x['executor'].get('id') or x['executor'].get('pid')})", 0
    env = dict(cli.env(), ENG_LANE="exec", ENG_ISSUE=str(n))
    env.pop("CLAUDE_PROJECT_DIR", None)
    perm = os.environ.get("DE_EXEC_PERMISSIONS")   # e.g. bypassPermissions; default: edits + the project's own commands
    base = [claude_bin()] + (["--permission-mode", perm] if perm else ["--permission-mode", "acceptEdits", "--allowedTools", EXEC_TOOLS])
    name = f"#{n} {x['title']}"[:60]
    r = subprocess.run(base + ["--bg", "--name", name, exec_prompt(x)], cwd=d, env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"claude attach (\S+)|session[_ ]id:?\s+(\S+)|background session (\S+)", out)   # the id `attach` / `stop` take
    sid = next((g for g in m.groups() if g), None) if m else None
    if r.returncode == 0:   # a background session started: never a second executor beside it, even when its id is not printed
        rec = {"kind": "bg", "id": sid, "name": name, "started": round(time.time())}
        msg = (f"#{n}: executor started as background session {sid} (`claude attach {sid}` to watch, `claude logs {sid}`)" if sid
               else f"#{n}: executor started as background session «{name}» (`claude agents` lists it)")
    else:   # no background session (an older Claude Code, an untrusted folder, …): a detached print-mode run
        log = open(idir(d) / f"{n}.log", "a")
        log.write(f"[de] claude --bg was not available (exit {r.returncode}): {out.strip()[-600:]}\n[de] running claude -p instead\n")
        log.flush()
        p = subprocess.Popen(base + ["-p", exec_prompt(x)], cwd=d, env=env, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        rec = {"kind": "print", "pid": p.pid, "started": round(time.time()), "log": f".engineer/issues/{n}.log"}
        msg = f"#{n}: executor started (claude -p, pid {p.pid}, log .engineer/issues/{n}.log)"
    with locked(d):
        x = load(d, n)
        x["executor"] = rec
        save(d, x)
    return msg, 0


def running(d, x):
    e = x.get("executor") or {}
    if e.get("kind") == "print":
        return cli.alive(e.get("pid"))
    if e.get("kind") == "bg":
        r = subprocess.run([claude_bin(), "agents", "--json"], capture_output=True, text=True, timeout=30)
        try:
            return any((a.get("id") == e["id"] if e.get("id") else a.get("name") == e.get("name"))
                       and a.get("status") not in ("done", "stopped") for a in json.loads(r.stdout or "[]"))
        except ValueError:
            return False
    return False


def is_claude(pid):
    r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return "claude" in r.stdout


def start_after(d, n):
    """Start the issues that waited for #n (`issue new --after n`) now that it is closed."""
    msgs = [start(d, o["n"])[0] for o in all_issues(d)
            if o.get("after") == n and o["state"] == "open" and not running(d, o)]
    return "\n".join(msgs)


def stop(d, n):
    x = load(d, n)
    e = (x or {}).get("executor") or {}
    if e.get("kind") == "bg" and e.get("id"):
        subprocess.run([claude_bin(), "stop", e["id"]], capture_output=True, text=True, timeout=60)
    elif e.get("kind") == "print" and cli.alive(e.get("pid")) and is_claude(e["pid"]):   # a pid reused by another program: leave it
        try:
            os.killpg(e["pid"], signal.SIGTERM)
        except OSError:
            pass
    return f"#{n}: executor stopped" if e else f"#{n}: no executor", 0


# ---------------------------------------------------------------- ops

def lane():
    return "exec" if os.environ.get("ENG_LANE") == "exec" and os.environ.get("ENG_ISSUE") else "manager"


def show(x, as_lane):
    lines = [f"#{x['n']} [{x['state']}{' · ' + ', '.join(x['labels']) if x['labels'] else ''}] {x['title']}",
             f"parts: {', '.join(x['parts']) or '-'}", "", x["body"], ""]
    lines += [f"{c['i']}. {fmt(c)}" for c in x["comments"]]
    mark_seen(x, as_lane)
    return "\n".join(lines)


def op_new(d, a):
    with locked(d):
        if a.parts and not (a.after or a.separate):
            for o in all_issues(d):
                both = set(o["parts"]) & set(a.parts)
                if o["state"] in OPEN and both:
                    return (f"refused: #{o['n']} «{o['title']}» is open on {', '.join(sorted(both))}: comment into it, "
                            f"or --after {o['n']}, or --separate \"why\""), 1
        n = max([o["n"] for o in all_issues(d)] + [0]) + 1
        x = {"n": n, "title": a.title, "body": a.body, "parts": a.parts, "state": "waiting" if a.waiting else "open",
             "labels": [], "comments": [], "executor": None, "seen": {}, "after": a.after, "separate": a.separate,
             "created": round(time.time())}
        save(d, x)
    print(f"#{n}")
    if a.waiting or a.no_start or (a.after and (load(d, a.after) or {}).get("state") in OPEN):
        return "", 0
    return start(d, n)


def op_comment(d, x, by, text, files, kind="comment"):
    add(x, by, kind, text, files)   # no cursor moves: a side's read cursor moves only when it is shown the other side's words
    return f"#{x['n']}: {kind} added", 0


def main(argv):
    d = cli.project(None)
    if not cli.is_local(d):
        print("not a local project (`de init` first)", file=sys.stderr)
        return 2
    me = lane()
    ap = argparse.ArgumentParser(prog="issue", description="Tasks of a team-mode project (manager + executors).")
    sub = ap.add_subparsers(dest="op", required=True)
    if me == "manager":
        p = sub.add_parser("new")
        p.add_argument("--title", required=True)
        p.add_argument("--body", required=True)
        p.add_argument("--part", dest="parts", action="append", default=[])
        p.add_argument("--after", type=int)
        p.add_argument("--separate")
        p.add_argument("--waiting", action="store_true")
        p.add_argument("--no-start", action="store_true")
        for name in ("comment", "reopen", "close"):
            p = sub.add_parser(name)
            p.add_argument("n", type=int)
            p.add_argument("text", nargs="?", default="")
        p = sub.add_parser("label")
        p.add_argument("n", type=int)
        p.add_argument("labels", nargs="+")
        p = sub.add_parser("show")
        p.add_argument("n", type=int)
        sub.add_parser("list")
        p = sub.add_parser("peek")
        p.add_argument("n", type=int)
    else:
        sub.add_parser("show")
        for name in ("comment", "ask", "close"):
            p = sub.add_parser(name)
            p.add_argument("text")
            p.add_argument("--file", action="append", default=[])
            if name == "comment":
                p.add_argument("--to", type=int)
    if me == "manager" and argv[:1] == ["label"]:   # `-stop` reads as an option to argparse: labels are parsed here
        if len(argv) < 3 or not argv[1].isdigit():
            ap.error("label N +LABEL|-LABEL ...")
        a = argparse.Namespace(op="label", n=int(argv[1]), labels=argv[2:])
    else:
        a = ap.parse_args(argv)
    if me == "manager" and a.op == "new":
        msg, code = op_new(d, a)
    elif me == "manager" and a.op == "list":
        rows = [f"#{x['n']} {x['state']}{' [' + ','.join(x['labels']) + ']' if x['labels'] else ''} {x['title']}"
                for x in all_issues(d) if x["state"] != "closed"]
        msg, code = "\n".join(rows) or "no open issues", 0
    else:
        n = a.n if me == "manager" else int(os.environ["ENG_ISSUE"])
        with locked(d):
            x = load(d, n)
            if not x:
                print(f"no issue #{n}", file=sys.stderr)
                return 1
            msg, code, after = run_op(d, x, me, a)
            save(d, x)
        if after:
            m2, _ = after()
            msg = (msg + "\n" + m2).strip()
    if msg:
        print(msg, file=sys.stdout if code == 0 else sys.stderr)
    return code


def run_op(d, x, me, a):
    """-> (message, exit code, a follow-up to run outside the lock or None)."""
    n = x["n"]
    if a.op == "show":
        return show(x, "exec" if me == "exec" else "manager"), 0, None
    if me == "manager":
        if a.op == "peek":
            e = x.get("executor") or {}
            if e.get("kind") == "bg":
                r = subprocess.run([claude_bin(), "logs", e["id"]], capture_output=True, text=True, timeout=30)
                return (r.stdout or r.stderr)[-3000:], 0, None
            return cli_tail(d / (e.get("log") or "none")), 0, None
        if a.op == "label":
            bad = [lab for lab in a.labels if lab[:1] not in "+-" or lab.lstrip("+-") not in LABELS]
            if bad:
                return f"refused: labels are +/-{'|'.join(LABELS)} (got {' '.join(bad)})", 2, None
            for lab in a.labels:
                name = lab.lstrip("+-")
                if lab.startswith("-"):
                    x["labels"] = [v for v in x["labels"] if v != name]
                elif name not in x["labels"]:
                    x["labels"].append(name)
            return f"#{n}: labels {', '.join(x['labels']) or '-'}", 0, None
        if a.op == "comment":   # an executor that handed over, asked or never ran reads it only when it runs: start it again
            resume = x["state"] in ("review", "waiting") or (x["state"] == "open" and x.get("executor") and not running(d, x))
            if resume:
                x["state"] = "open"
            m, c = op_comment(d, x, "manager", a.text, ())
            return m, c, (lambda: start(d, n)) if resume else None
        if a.op == "reopen":
            x["state"] = "open"
            m, c = op_comment(d, x, "manager", a.text, (), "reopen")
            return m, c, lambda: start(d, n)
        if a.op == "close":
            x["state"] = "closed"
            m, c = op_comment(d, x, "manager", a.text or "closed", (), "close")
            def after():
                msg, code = stop(d, n)
                return "\n".join(filter(None, [msg, start_after(d, n)])), code
            return m, c, after
    else:
        if a.op == "comment" and a.to:
            o = load(d, a.to)
            if not o or o["state"] == "closed":
                return f"refused: #{a.to} is closed or does not exist", 1, None
            add(o, "executor", "comment", f"(from the executor of #{n}) {a.text}", a.file)
            save(d, o)
            return f"#{a.to}: comment added", 0, None
        if a.op == "comment":
            return (*op_comment(d, x, "executor", a.text, a.file), None)
        if a.op == "ask":
            x["state"] = "waiting"
            return (*op_comment(d, x, "executor", a.text, a.file, "ask"), None)
        if a.op == "close":
            new = unseen(x, "exec")
            if new:
                return ("refused: the manager wrote what you have not seen yet: `issue show` first\n"
                        + "\n".join(fmt(c) for c in new)), 1, None
            x["state"] = "review"
            return (*op_comment(d, x, "executor", a.text, a.file, "handover"), None)
    return f"unknown op {a.op}", 2, None


def cli_tail(p, n=60):
    try:
        return "\n".join(p.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return "no log"


# ---------------------------------------------------------------- what the hooks hand over

def news_for_exec(d, n):
    """New manager words on issue n for its executor (marked seen), and whether it was told to stop."""
    with locked(d):
        x = load(d, n)
        if not x:
            return "", False
        new = unseen(x, "exec")
        stop_now = "stop" in x["labels"] or x["state"] == "closed"
        if new:
            mark_seen(x, "exec")
            save(d, x)
    text = (f"New on your issue #{n}:\n" + "\n".join(fmt(c) for c in new)) if new else ""
    return text, stop_now


def news_for_manager(d):
    """Executor events on every issue since the manager last saw them (marked seen)."""
    out = []
    with locked(d):
        for x in all_issues(d):
            new = unseen(x, "manager")
            if new:
                mark_seen(x, "manager")
                save(d, x)
                out += [f"#{x['n']} «{x['title']}» ({x['state']}): {fmt(c)}" for c in new]
    if not out:
        return ""
    return ("From the executors since you last looked (a handover = check it against the user's words, then "
            "`issue close N \"why\"` or `issue comment N` to send it back):\n" + "\n".join(out))
