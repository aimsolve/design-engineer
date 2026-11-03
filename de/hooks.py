"""Claude Code hooks of local mode (hooks/hooks.json -> local/bin/de-hook -> here). What the router does around a turn on the
platform, done around a turn of a plain Claude Code session:

  session-start       the principles + local rules into the context; DE_ROOT and `de` / `job` / `hw` on the Bash PATH
  user-prompt         notes drawn on the model page since the last prompt (.engineer/inbox/) into the context
  post-tool           a part file that does not even compile goes straight back to the agent
  stop                the model sources changed since the last build: build + check; a failure sends the agent back
                      (decision: block), never twice for the same failure and at most MAX_BLOCKS times in a row;
                      success: the viewer address for the user

local/bin/de-hook already returned for anything that is not a local project (ENGINEER_API set = the platform, or no
`.engineer/mode` = local up the tree), so nothing here ever runs in a platform turn."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from de import cli, issues

MAX_BLOCKS = 3
LOG_TAIL = 40


def out(obj):
    print(json.dumps(obj, ensure_ascii=False))


def read_input():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return {}


def project_of(inp):
    d = cli.project(inp.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    return d if cli.is_local(d) else None


def session_start(d, inp):
    envf = os.environ.get("CLAUDE_ENV_FILE")
    if envf:
        with open(envf, "a") as f:
            f.write(f'export DE_ROOT="{cli.ROOT}"\nexport PATH="{cli.ROOT / "local" / "bin"}:$PATH"\n')
    st = cli.state(d)
    lines = [f"Local machine project: {d} (viewer slug `{cli.slug(d)}`).",
             f"`de` is at {cli.ROOT / 'local' / 'bin' / 'de'} (also on the Bash PATH, with `job` and `hw`).",
             "Model: " + (f"v{st['built_n']} built" if st.get("built_n") else "not built yet") +
             ("; the last build FAILED (.engineer/build.log)" if st.get("build_ok") is False else "") + "."]
    role = None
    n = executor_of(d)
    if issues.team(d):
        role = "executor" if n else "manager"
        if n:
            x = issues.load(d, n) or {}
            lines.append(f"Team mode: you are the EXECUTOR of issue #{n} «{x.get('title', '?')}». Start with `issue show`.")
        else:
            lines.append("Team mode: you are the MANAGER. `issue list` shows the open issues.")
            news = issues.news_for_manager(d)
            if news:
                lines.append(news)
    if not n:   # notes drawn on the model are the user's words: the manager's (or the single session's), never an executor's
        notes = unseen(d)
        if notes:
            lines.append(notes_text(d, notes))
    out({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                "additionalContext": cli.rules(role) + "\n\n## This session\n" + "\n".join(lines)}})
    return 0


def executor_of(d):
    """This session's issue number when it is an executor of a team-mode project, else None."""
    if os.environ.get("ENG_LANE") == "exec" and str(os.environ.get("ENG_ISSUE", "")).isdigit() and issues.team(d):
        return int(os.environ["ENG_ISSUE"])
    return None


def unseen(d):
    inbox = d / ".engineer" / "inbox"
    seen = set((inbox / ".seen").read_text().split()) if (inbox / ".seen").is_file() else set()
    return sorted(p.name for p in inbox.glob("*-note.json") if p.name not in seen) if inbox.is_dir() else []


def notes_text(d, names):
    inbox = d / ".engineer" / "inbox"
    with open(inbox / ".seen", "a") as f:
        f.write("".join(n + "\n" for n in names))
    rows = []
    for n in names:
        try:
            r = json.loads((inbox / n).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pics = ", ".join(f".engineer/inbox/{p}" for p in r.get("pictures") or [])
        where = r.get("part") or r.get("node") or ", ".join(r.get("parts") or []) or "—"
        rows.append(f"- {r.get('time')}: «{r.get('comment')}» · on: {where} · picture: {pics or '—'} · full note: .engineer/inbox/{n}")
    return ("The user drew notes on the model page (treat each as their message; open the picture before answering):\n" + "\n".join(rows)) if rows else ""


def user_prompt(d, inp):
    if executor_of(d):
        return 0
    parts = []
    names = unseen(d)
    if names:
        parts.append(notes_text(d, names))
    if issues.team(d):
        parts.append(issues.news_for_manager(d))
    text = "\n\n".join(p for p in parts if p)
    if text:
        out({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}})
    return 0


def post_tool(d, inp):
    n = executor_of(d)
    if n:   # the executor hears its manager after every tool call
        text, stop_now = issues.news_for_exec(d, n)
        if stop_now:
            text = (text + "\n\n" if text else "") + (f"STOP: the manager stopped issue #{n} (label stop, or closed). Make no "
                                                       "more changes; end your turn now.")
        if text:
            out({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}})
            return 0
    if inp.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        return 0
    f = (inp.get("tool_input") or {}).get("file_path") or ""
    p = Path(f).resolve()
    if p.suffix != ".py" or not p.is_file():
        return 0
    try:
        p.relative_to(d / "mechanics")
    except ValueError:
        return 0
    r = subprocess.run([sys.executable, "-c", "import sys,ast; ast.parse(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1])", str(p)],
                       capture_output=True, text=True)
    if r.returncode:
        out({"decision": "block", "reason": f"{p.relative_to(d)} does not parse:\n" + r.stderr.strip().splitlines()[-1][:600]})
    return 0


MODEL_DIRS = ("mechanics", "electronics", "physics", "aas", "sim")


def pre_tool(d, inp):
    """Team mode: the manager never edits the model (the platform keeps those tools out of its lane): an edit of a model file is
    refused with what to do instead."""
    if executor_of(d) or not issues.team(d):
        return 0
    f = (inp.get("tool_input") or {}).get("file_path") or (inp.get("tool_input") or {}).get("notebook_path") or ""
    try:
        rel = Path(f).resolve().relative_to(d.resolve())
    except ValueError:
        return 0
    if rel.parts and (rel.parts[0] in MODEL_DIRS or rel.as_posix() == "kit.json"):
        out({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
             "permissionDecisionReason": f"Team mode: you are the manager and do not edit the model ({rel}). Open an issue with the "
                                         "user's words (`issue new --title … --body … --part …`): its executor does the work."}})
    return 0


def tail(p, n=LOG_TAIL):
    try:
        return "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def stop(d, inp):
    n = executor_of(d)
    if n and not inp.get("stop_hook_active"):
        x = issues.load(d, n) or {}
        if x.get("state") not in ("closed",) and "stop" not in x.get("labels", []):
            text, _ = issues.news_for_exec(d, n)
            if text:   # it does not stop over unread words of its manager
                out({"decision": "block", "reason": text})
                return 0
            if x.get("state") == "open":   # its final text reaches nobody: only the issue does
                out({"decision": "block", "reason": f"Issue #{n} is still open and your final text reaches nobody. Hand the work "
                     "over (`issue close \"result\" --file <picture>`), ask the user ONE question (`issue ask \"…\"`), or say what "
                     "blocks you (`issue comment \"…\"`)."})
                return 0
    if not n and issues.team(d):   # a team manager does not edit the model: its executors build and check their own work
        return 0
    fp = cli.inputs_fingerprint(d)
    st = cli.state(d)
    if fp is None or (fp == st.get("built_fp") and fp == st.get("checked_fp")):
        return 0
    again = bool(inp.get("stop_hook_active"))    # this stop follows our own block: the agent already had a go at it
    blocks = int(st.get("stop_blocks") or 0) if again else 0   # a new turn starts the count again

    def block(reason, sig):
        """Send the agent back, unless it already saw this very failure (or MAX_BLOCKS in a row): then tell the user and stop."""
        if again and (sig == st.get("stop_sig") or blocks >= MAX_BLOCKS):
            cli.save_state(d, stop_blocks=0, stop_sig=None)
            out({"systemMessage": "design engineer: the model still does not build or fails the check; "
                                  "log: .engineer/build.log, `de check`."})
            return 0
        cli.save_state(d, stop_blocks=blocks + 1, stop_sig=sig)
        out({"decision": "block", "reason": reason})
        return 0
    code = 0
    if fp != st.get("built_fp"):          # built by hand already (de build) and only not checked: check only
        with open(os.devnull, "w") as null:
            so = sys.stdout
            sys.stdout = null
            try:
                code = cli.build(d)
            finally:
                sys.stdout = so
    if code:
        log = tail(d / ".engineer" / "build.log")
        return block("The model did not build (`de build`, exit "
                     f"{code}). Fix it before you finish; the end of .engineer/build.log:\n{log}", signature("build", log.splitlines()[-3:]))
    r = cli.check(d, as_json=True)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        res = {"result": "ERROR", "fails": [{"rule": "check.py", "detail": (r.stderr or r.stdout)[-600:]}]}
    if res.get("result") != "PASS":
        cli.save_state(d, checked_fp=None)
        fails = "\n".join(f"- {f.get('rule')}: {f.get('detail')}" for f in res.get("fails") or [])
        return block(f"The model built, but `de check` FAILS. Fix every FAIL before you finish:\n{fails}", signature("check", fails))
    cli.save_state(d, stop_blocks=0, stop_sig=None, checked_fp=fp, checked_at=time.time())
    url = ensure_server(d)
    out({"systemMessage": f"design engineer: model v{cli.state(d).get('built_n')} built, checks PASS. Viewer: {url}"})
    return 0


def signature(kind, text):
    """The same failure twice = the same text once timings and counts are taken out."""
    return kind + ":" + re.sub(r"\d+(\.\d+)?", "#", str(text))[-400:]


def ensure_server(d):
    s = cli.running_server(d)
    if s:
        return s["url"]
    r = subprocess.run([str(cli.ROOT / "local" / "bin" / "de"), "serve", str(d)], capture_output=True, text=True, env=cli.env())
    return (r.stdout.strip().splitlines() or ["`de serve`"])[0]


def main(argv):
    ev = argv[0] if argv else ""
    fn = {"session-start": session_start, "user-prompt": user_prompt, "pre-tool": pre_tool, "post-tool": post_tool, "stop": stop}.get(ev)
    if not fn or os.environ.get("ENGINEER_API"):
        return 0
    inp = read_input()
    d = project_of(inp)
    if not d:
        return 0
    return fn(d, inp)
