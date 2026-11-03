"""de — the design engineer's local runtime: what the router does on the platform, done here in plain Claude Code.

  de init [DIR] [--name NAME] [--title TITLE]   a machine project in DIR (default: here): model skeleton, wiki/, kit.json, .engineer/mode
  de build [DIR]                                the model + the viewer site in public/ + the AAS (kit.json `site`)
  de check [DIR] [--json]                       the framework's conformance check (machine-project check.py)
  de serve [DIR] [--port P] [--open] [--foreground] [--stop]   the viewer on http://127.0.0.1 (+ /feedback, /kit, /hw)
  de kit SCOPE [DIR]                            a download kit now (step | print | laser | bought | assembly)
  de job run "<cmd>" [--timeout S]              heavy work, here, with a time limit (the skills' `job run`)
  de hw status|flash|reset|identify|log ...     a board on the desk, through the open viewer page (the skills' `hw`)
  de init --team / de team on|off               team mode: you talk to a manager, executors (background sessions) work
  de issue new|comment|ask|close|list|show ...  the local task backend of team mode (`issue` in the roles)
  de exec start|stop N                          an issue's executor session
  de rules                                      the principles + the local-mode rules
  de hook session-start|post-tool|stop          Claude Code hooks (hooks/hooks.json): no-op outside a local project

The plugin root is this file's grandparent (DE_ROOT); every command runs the framework's scripts with this environment's
python first on PATH, so `python` in kit.json / sim/site.sh is the one with build123d."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
MP = SKILLS / "machine-project" / "scripts"
TPL = ROOT / "local" / "templates"
DEFAULT_PORT = 8765


def env():
    """The environment every framework script runs in: DE_ROOT, this venv's python first, no bytecode in project folders."""
    e = dict(os.environ, DE_ROOT=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    e["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), str(ROOT / "local" / "bin"), e.get("PATH", "")])
    e.pop("VIRTUAL_ENV", None)
    return e


def is_local(d):
    try:
        return (Path(d) / ".engineer" / "mode").read_text().strip() == "local"
    except OSError:
        return False


def project(arg=None):
    """The project folder: the nearest one up from DIR (or here) whose .engineer/mode is local, else DIR itself."""
    start = Path(arg or os.getcwd()).resolve()
    for d in (start, *start.parents):
        if is_local(d):
            return d
    return start


def slug(d):
    return re.sub(r"[^a-z0-9]+", "-", Path(d).name.lower()).strip("-") or "project"


# ---------------------------------------------------------------- init

def cmd_init(a):
    d = Path(a.dir or os.getcwd()).resolve()
    d.mkdir(parents=True, exist_ok=True)
    name = a.name or slug(d)
    title = a.title or a.name or d.name
    try:   # an existing project keeps its language; the scaffold writes its texts (fastener names, …) in it
        lang = json.loads((d / ".engineer" / "locale.json").read_text(encoding="utf-8")).get("lang") or a.lang
    except (OSError, ValueError, AttributeError):
        lang = a.lang
    r = subprocess.run([sys.executable, str(MP / "scaffold.py"), str(d), "--name", name, "--title", title, "--cad", "build123d",
                        "--lang", lang], env=env(), capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode:
        sys.stderr.write(r.stderr)
        return r.returncode
    added = []

    def put(rel, text):
        p = d / rel
        if p.exists():
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        added.append(rel)

    wt = SKILLS / "project-wiki" / "templates"
    put("wiki/index.md", (wt / "index.md").read_text(encoding="utf-8"))
    put("wiki/log.md", (wt / "log.md").read_text(encoding="utf-8"))
    put("CLAUDE.md", (TPL / "CLAUDE.md").read_text(encoding="utf-8").replace("{title}", title).replace("{slug}", slug(d)))
    put(".engineer/mode", "local\n")
    put(".engineer/locale.json", json.dumps({"lang": lang}) + "\n")   # what the person reads (viewer, drawings, BOM); the platform's default is ru
    if a.team:
        put(".engineer/lanes", "team\n")
        cm = d / "CLAUDE.md"
        if cm.is_file() and "Team mode" not in cm.read_text(encoding="utf-8"):
            cm.write_text(cm.read_text(encoding="utf-8").replace("\n## Current state", TEAM_NOTE + "\n## Current state", 1), encoding="utf-8")
    gi = d / ".gitignore"   # the scaffold ignores all of .engineer/ (platform runtime); the mode files belong to the project
    if gi.is_file() and "!.engineer/mode" not in gi.read_text(encoding="utf-8"):
        gi.write_text(gi.read_text(encoding="utf-8").replace(".engineer/\n", ".engineer/*\n!.engineer/mode\n!.engineer/lanes\n!.engineer/locale.json\n", 1), encoding="utf-8")
    for sub in ("inbox", "outbox"):
        (d / ".engineer" / sub).mkdir(parents=True, exist_ok=True)
    if not (d / ".git").exists():
        subprocess.run(["git", "init", "-q", str(d)], check=False)
    print("added:", ", ".join(added) or "-")
    print(f"next: open Claude Code here and describe the part; `de build`, `de check`, `de serve` (http://127.0.0.1:{DEFAULT_PORT}/{slug(d)}/)")
    return 0


TEAM_NOTE = """
## Team mode
This project runs in team mode: the session the user talks to is the MANAGER. It does not model: every request becomes an issue
(`issue new --title … --body "<the user's words>" --part …`), worked by its own executor session; the manager checks each
handover and closes it (rules: `local/roles/manager.md`, loaded at session start).
"""


# ---------------------------------------------------------------- build / check

def inputs_fingerprint(d):
    """Paths, sizes and mtimes of the model's sources (kit.json `inputs` + kit.json + sim/): a build is needed when it changes."""
    try:
        cfg = json.loads((d / "kit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = {d / "kit.json"}
    for g in cfg.get("inputs") or []:
        files |= {p for p in d.glob(g) if p.is_file()}
    files |= {p for p in (d / "sim").rglob("*") if p.is_file()} if (d / "sim").is_dir() else set()
    h = hashlib.sha256()
    for p in sorted(files):
        if "__pycache__" in p.parts:
            continue
        st = p.stat()
        h.update(f"{p.relative_to(d)}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def state(d):
    try:
        return json.loads((d / ".engineer" / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def save_state(d, **kw):
    s = dict(state(d), **kw)
    (d / ".engineer").mkdir(exist_ok=True)
    (d / ".engineer" / "state.json").write_text(json.dumps(s, indent=1))
    return s


def model_json(d, **kw):
    """public/model.json {built, building}: the open viewer page polls it and reloads onto a new build (as on the platform)."""
    (d / "public").mkdir(exist_ok=True)
    p = d / "public" / "model.json"
    try:
        m = json.loads(p.read_text())
    except (OSError, ValueError):
        m = {"built": 0}
    m.update(kw)
    p.write_text(json.dumps(m))


def run_logged(cmd, d, log, timeout, echo=True):
    """Run a shell command in d, output to log (and the terminal when echo); -> exit code (124 = time limit)."""
    import threading
    sys.path.insert(0, str(MP))
    import procgroup
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as f:
        p = procgroup.popen(["sh", "-c", cmd], cwd=d, env=env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        late = threading.Event()
        timer = threading.Timer(timeout, lambda: (late.set(), procgroup.terminate_group(p)))
        timer.daemon = True
        timer.start()
        try:
            for line in p.stdout:
                if echo:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                f.write(line)
            code = p.wait()
        finally:
            timer.cancel()
            procgroup.reap(p)
        if late.is_set():
            f.write(f"\n[de] time limit {timeout} s: stopped\n")
            if echo:
                print(f"[de] time limit {timeout} s: stopped")
            return 124
        return code


@contextlib.contextmanager
def model_lock(d):
    """One model writer at a time per project: builds of several sessions (team mode) and kit exports wait for each other."""
    (d / ".engineer").mkdir(exist_ok=True)
    with open(d / ".engineer" / "build.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def build(d):
    with model_lock(d):
        return _build(d)


def _build(d):
    try:
        cfg = json.loads((d / "kit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"de build: no readable kit.json in {d} ({e}); run `de init` first")
        return 2
    site = cfg.get("site")
    if not site:
        print("de build: kit.json has no `site` command")
        return 2
    fp = inputs_fingerprint(d)
    model_json(d, building=True)
    t0 = time.time()
    code = run_logged(site, d, d / ".engineer" / "build.log", int(cfg.get("timeout") or 3600))
    if code == 0 and (d / ".engineer" / "locale.json").is_file():   # the viewer's language (the router does this on the platform)
        (d / "public" / "locale.json").write_bytes((d / ".engineer" / "locale.json").read_bytes())
    if code == 0:
        n = int(state(d).get("built_n") or 0) + 1
        save_state(d, built_fp=fp, built_n=n, built_at=time.time(), build_ok=True)
        model_json(d, built=n, building=False)
        print(f"de build: OK in {time.time() - t0:.0f} s (model v{n}); viewer: `de serve`")
    else:
        save_state(d, build_ok=False, failed_fp=fp)
        model_json(d, building=False)
        print(f"de build: FAILED (exit {code}); full log: .engineer/build.log")
    return code


def cmd_build(a):
    return build(project(a.dir))


def check(d, as_json=False):
    args = [sys.executable, str(MP / "check.py"), str(d)] + (["--json"] if as_json else [])
    return subprocess.run(args, cwd=d, env=env(), capture_output=as_json, text=True)


def cmd_check(a):
    d = project(a.dir)
    fp = inputs_fingerprint(d)
    r = check(d, a.json)
    if a.json:
        sys.stdout.write(r.stdout)
    if is_local(d):
        save_state(d, checked_fp=fp if r.returncode == 0 else None)
    return r.returncode


# ---------------------------------------------------------------- serve

def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def answers(url, d):
    """Is the server at url ours: does it serve this very project folder (a copied folder carries the original's serve.json)?"""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # loopback: never through an HTTP(S)_PROXY
    try:
        with opener.open(url + "_de/ping", timeout=2) as r:
            return r.status == 200 and json.loads(r.read()).get("project") == str(d)
    except (OSError, ValueError):
        return False


def running_server(d):
    try:
        s = json.loads((d / ".engineer" / "serve.json").read_text())
    except (OSError, ValueError):
        return None
    base = f"http://127.0.0.1:{s['port']}/"
    return s if alive(s.get("pid")) and answers(base, d) else None


def cmd_serve(a):
    d = project(a.dir)
    from de import server
    if a.stop:
        s = running_server(d)
        if s:
            os.kill(s["pid"], 15)
            print(f"stopped {s['url']}")
        else:
            print("no viewer server running for this project")
        (d / ".engineer" / "serve.json").unlink(missing_ok=True)
        return 0
    if a.foreground:
        return server.serve(d, a.port if a.port is not None else DEFAULT_PORT, slug(d))
    s = running_server(d)
    if not s:
        port = a.port if a.port is not None else server.free_port_from(DEFAULT_PORT)
        (d / ".engineer").mkdir(exist_ok=True)
        (d / ".engineer" / "serve.json").unlink(missing_ok=True)
        log = open(d / ".engineer" / "serve.log", "a")
        subprocess.Popen([sys.executable, "-m", "de.cli", "serve", str(d), "--foreground", "--port", str(port)], cwd=str(ROOT),
                         env=env(), stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        for _ in range(100):
            time.sleep(0.1)
            s = running_server(d)
            if s:
                break
        if not s:
            print("de serve: the server did not start; see .engineer/serve.log")
            return 1
    print(s["url"])
    if not (d / "public" / "config.json").exists():
        print("(no model built yet: `de build` first, the page reloads by itself)")
    if a.open:
        import webbrowser
        webbrowser.open(s["url"])
    return 0


# ---------------------------------------------------------------- kit

def cmd_kit(a):
    d = project(a.dir)
    from de import kit
    code, out = kit.export(d, a.scope)
    print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False))
    return 0 if code == 200 else 1


# ---------------------------------------------------------------- job

def cmd_job(a, rest):
    ap = argparse.ArgumentParser(prog="job", description="Local jobs run here, in the foreground, with a time limit.")
    sub = ap.add_subparsers(dest="op", required=True)
    r = sub.add_parser("run")
    r.add_argument("cmd")
    r.add_argument("--timeout", type=int, default=3600)
    r.add_argument("--needs", action="append", default=[], help="accepted for platform compatibility; locally everything runs here")
    r.add_argument("--expect", help="accepted for platform compatibility")
    r.add_argument("--image", help="accepted for platform compatibility")
    for op in ("wait", "status", "log", "logs", "cancel", "list", "running"):
        sub.add_parser(op).add_argument("id", nargs="?")
    j = ap.parse_args(rest)
    if j.op != "run":
        print("local jobs run in the foreground (`job run` returns when the command ends): nothing is pending")
        return 0
    p = project(None)
    log = (p if is_local(p) else Path(os.getcwd())) / ".engineer" / "jobs" / f"{time.time_ns()}-{os.getpid()}.log"
    return run_logged(j.cmd, Path(os.getcwd()), log, j.timeout)


# ---------------------------------------------------------------- hw

def cmd_hw(a, rest):
    d = project(None)
    s = running_server(d)
    e = env()
    if s:
        e["ENGINEER_API"] = f"http://127.0.0.1:{s['port']}"
        e["ENGINEER_TOKEN"] = s.get("token") or ""
        e["NO_PROXY"] = e["no_proxy"] = "127.0.0.1,localhost"
        e["DE_VIEWER_URL"] = s["url"]
    else:
        print("the viewer server is not running: `de serve`, then open the page in Chrome or Edge and press the connect-a-board button in the panel")
        return 2
    return subprocess.run([sys.executable, str(Path(__file__).with_name("hw_cli.py")), *rest], env=e).returncode


# ---------------------------------------------------------------- team

def cmd_team(a):
    d = project(None)
    if not is_local(d):
        print("not a local project (`de init` first)", file=sys.stderr)
        return 2
    f, cm = d / ".engineer" / "lanes", d / "CLAUDE.md"
    text = cm.read_text(encoding="utf-8") if cm.is_file() else ""
    if a.state == "on":
        f.write_text("team\n")
        if text and "## Team mode" not in text:
            cm.write_text(text.replace("\n## Current state", TEAM_NOTE + "\n## Current state", 1), encoding="utf-8")
        print("team mode on: this session is the manager from its next start; `issue new` starts an executor")
    else:
        f.unlink(missing_ok=True)
        if TEAM_NOTE in text:
            cm.write_text(text.replace(TEAM_NOTE, ""), encoding="utf-8")
        print("team mode off: one session does the work")
    return 0


# ---------------------------------------------------------------- rules

def rules(role=None):
    """The principles + the local rules (+ the team role's rules: manager | executor)."""
    files = ["PRINCIPLES.md", "local.md"] + ([f"local/roles/{role}.md"] if role else [])
    return "\n\n".join((ROOT / f).read_text(encoding="utf-8") for f in files)


# ---------------------------------------------------------------- main

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "job":
        return cmd_job(None, argv[1:])
    if argv and argv[0] == "hw":
        return cmd_hw(None, argv[1:])
    if argv and argv[0] == "issue":
        from de import issues
        return issues.main(argv[1:])
    if argv and argv[0] == "exec":
        from de import issues
        ap = argparse.ArgumentParser(prog="de exec", description="Executors of team mode: one background Claude Code session per issue.")
        ap.add_argument("op", choices=["start", "stop"])
        ap.add_argument("n", type=int)
        a = ap.parse_args(argv[1:])
        msg, code = (issues.start if a.op == "start" else issues.stop)(project(None), a.n)
        print(msg, file=sys.stdout if code == 0 else sys.stderr)
        return code
    if argv and argv[0] == "rules":
        print(rules())
        return 0
    if argv and argv[0] == "hook":
        from de import hooks
        return hooks.main(argv[1:])
    ap = argparse.ArgumentParser(prog="de", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("dir", nargs="?")
    p.add_argument("--team", action="store_true", help="team mode: you talk to a manager, executors work the issues")
    p.add_argument("--lang", default="en", choices=["en", "ru", "pl"], help="the project's language for what people read (default en)")
    p.add_argument("--name")
    p.add_argument("--title")
    for name in ("build", "check"):
        p = sub.add_parser(name)
        p.add_argument("dir", nargs="?")
        if name == "check":
            p.add_argument("--json", action="store_true")
    p = sub.add_parser("serve")
    p.add_argument("dir", nargs="?")
    p.add_argument("--port", type=int)
    p.add_argument("--open", action="store_true", help="open the page in the default browser")
    p.add_argument("--no-open", action="store_true", help="(the default) do not open a browser")
    p.add_argument("--foreground", action="store_true")
    p.add_argument("--stop", action="store_true")
    p = sub.add_parser("kit")
    p.add_argument("scope")
    p.add_argument("dir", nargs="?")
    sub.add_parser("job", help="job run \"<cmd>\" [--timeout S]")
    sub.add_parser("hw", help="status | flash CARD | reset CARD | identify --port KEY | log CARD")
    p = sub.add_parser("team", help="team mode on | off (manager + executors) for this project")
    p.add_argument("state", choices=["on", "off"])
    sub.add_parser("issue", help="new | comment | ask | close | list | show … (team mode)")
    sub.add_parser("exec", help="start | stop N: an issue's executor session")
    sub.add_parser("rules", help="the principles + the local-mode rules (what the SessionStart hook loads)")
    sub.add_parser("hook", help="session-start | post-tool | stop (Claude Code hooks)")
    a = ap.parse_args(argv)
    return {"init": cmd_init, "build": cmd_build, "check": cmd_check, "serve": cmd_serve, "kit": cmd_kit, "team": cmd_team}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
