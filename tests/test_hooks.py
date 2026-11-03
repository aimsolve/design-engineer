"""Claude Code hooks of the plugin (agent/hooks/hooks.json), run exactly as Claude Code runs them: the registered command, with
${CLAUDE_PLUGIN_ROOT} = agent/, the hook input as JSON on stdin.

Done-when 2/4 (locally the Stop hook builds + checks a changed model and sends Claude back to fix a failure) and done-when 5
(on the hosted platform, ENGINEER_API set, and in any non-local project every hook is a strict no-op)."""
import json
import re

import pytest

from conftest import AGENT, clean_env, copy_project, init_project, need_built, need_init, part_files, run, stop

HOOKS = AGENT / "hooks" / "hooks.json"
EVENTS = ("SessionStart", "PostToolUse", "Stop")


def hook_commands(event, tool=None):
    assert HOOKS.is_file(), f"no {HOOKS}"
    cfg = json.loads(HOOKS.read_text())
    groups = cfg.get("hooks", {}).get(event, [])
    cmds = []
    for g in groups:
        if tool and not re.fullmatch(g.get("matcher", ".*") or ".*", tool):
            continue
        cmds += [h["command"] for h in g.get("hooks", []) if h.get("type") == "command"]
    return cmds


def fire(event, project, payload=None, env_extra=None, tool=None, timeout=900):
    """Run every command registered for `event` -> list of Results."""
    cmds = hook_commands(event, tool)
    assert cmds, f"no {event} hook registered" + (f" for {tool}" if tool else "")
    data = {"hook_event_name": event, "cwd": str(project), "session_id": "test", "transcript_path": "/dev/null", **(payload or {})}
    env = clean_env(CLAUDE_PLUGIN_ROOT=AGENT, CLAUDE_PROJECT_DIR=project, **(env_extra or {}))
    out = []
    for c in cmds:
        c = c.replace("${CLAUDE_PLUGIN_ROOT}", str(AGENT)).replace("$CLAUDE_PLUGIN_ROOT", str(AGENT))
        out.append(run(["sh", "-c", c], cwd=project, env=env, input=json.dumps(data), timeout=timeout))
    return out


def decision(results):
    """The JSON decision a hook printed on stdout (or None)."""
    for r in results:
        for chunk in (r.out.strip(), *r.out.strip().splitlines()):
            try:
                d = json.loads(chunk)
            except ValueError:
                continue
            if isinstance(d, dict):
                return d
    return None


def blocked(results):
    d = decision(results)
    return bool(d) and d.get("decision") == "block"


def payload(event, project):
    return {"Stop": {"stop_hook_active": False},
            "SessionStart": {"source": "startup"},
            "PostToolUse": {"tool_name": "Edit", "tool_input": {"file_path": str(part_files(project)[0])}, "tool_response": {"success": True}}}[event]


@pytest.fixture
def broken_init(tmp_path):
    """A local project whose model sources changed since the last build (never built); need_init(..., broken=True) breaks a part:
    any hook that builds would react."""
    return init_project(tmp_path, "hook-proj")


# ---------------------------------------------------------------- registration
def test_hooks_are_registered_for_session_start_edits_and_stop():
    assert HOOKS.is_file(), f"no {HOOKS}"
    cfg = json.loads(HOOKS.read_text()).get("hooks", {})
    missing = [e for e in EVENTS if not cfg.get(e)]
    assert not missing, f"hooks.json registers no {missing}"
    for tool in ("Edit", "Write", "MultiEdit"):
        assert hook_commands("PostToolUse", tool), f"PostToolUse does not match {tool}"
    # stage 2 (team mode): an executor hears its manager after ANY tool call, so PostToolUse matches every tool; outside an
    # executor de-hook returns before Python for anything but an edit (the no-op tests below hold it to that)


# ---------------------------------------------------------------- no-op off local mode (done-when 5)
def assert_noop(results, project):
    for r in results:
        assert r.rc == 0, r.why("hook")
        assert not r.out.strip() and not r.err.strip(), r.why("a no-op hook printed something")
    assert not (project / "build").exists(), "a no-op hook started a build"


@pytest.mark.parametrize("event", EVENTS)
def test_every_hook_is_a_noop_on_the_platform(broken_init, event):
    broken_local = need_init(broken_init, broken=True)
    d, _ = broken_local
    assert_noop(fire(event, d, payload(event, d), env_extra={"ENGINEER_API": "http://router:8080"}, tool="Edit"), d)


@pytest.mark.parametrize("mode", [None, "platform", ""], ids=["no-mode-file", "mode-platform", "mode-empty"])
@pytest.mark.parametrize("event", EVENTS)
def test_every_hook_is_a_noop_outside_a_local_project(broken_init, event, mode):
    broken_local = need_init(broken_init, broken=True)
    d, _ = broken_local
    m = d / ".engineer" / "mode"
    if mode is None:
        m.unlink()
    else:
        m.write_text(mode + "\n")
    assert_noop(fire(event, d, payload(event, d), tool="Edit"), d)


# ---------------------------------------------------------------- SessionStart (local)
def session_context(results):
    d = decision(results) or {}
    extra = (d.get("hookSpecificOutput") or {}).get("additionalContext", "")
    return "\n".join([extra] + [r.out for r in results])


def test_session_start_gives_the_principles_and_the_local_rules(tmp_path):
    d, r = init_project(tmp_path, "ctx-proj")
    assert r.rc == 0, r.why("de init")
    res = fire("SessionStart", d, {"source": "startup"})
    assert all(x.rc == 0 for x in res), res[0].why("SessionStart")
    ctx = session_context(res)
    principles = [ln.strip() for ln in (AGENT / "PRINCIPLES.md").read_text().splitlines() if ln.startswith("## ")]
    assert principles, "PRINCIPLES.md has no sections"
    missing = [h for h in principles if h not in ctx]
    assert not missing, f"SessionStart context lacks PRINCIPLES.md sections: {missing[:5]}"
    local = AGENT / "local.md"
    assert local.is_file(), f"no {local}"
    first = next(ln.strip() for ln in local.read_text().splitlines() if ln.strip() and not ln.startswith("#"))
    assert first in ctx, f"SessionStart context lacks the local rules (first line of local.md: {first!r})"


# ---------------------------------------------------------------- Stop (local)
@pytest.mark.slow
def test_stop_blocks_on_a_broken_build_naming_it_then_lets_go_when_it_repeats(broken_init):
    broken_local = need_init(broken_init, broken=True)
    d, part = broken_local
    first = fire("Stop", d, {"stop_hook_active": False})
    assert blocked(first), first[0].why("Stop did not block on a model that does not build")
    assert part.name in decision(first).get("reason", ""), f"the block reason does not name {part.name}: {decision(first)}"

    again = fire("Stop", d, {"stop_hook_active": True})
    assert not blocked(again), again[0].why("Stop blocked again on the same failure with stop_hook_active: an endless loop")


@pytest.mark.slow
def test_stop_does_nothing_when_the_model_did_not_change(built):
    built_ok = need_built(built)
    stamp = (built_ok / "build" / "model" / "parts.json").stat().st_mtime_ns
    res = fire("Stop", built_ok, {"stop_hook_active": False}, timeout=120)
    assert not blocked(res), res[0].why("Stop blocked on an up-to-date model")
    assert (built_ok / "build" / "model" / "parts.json").stat().st_mtime_ns == stamp, "Stop rebuilt a model that did not change"


@pytest.mark.slow
def test_stop_builds_a_changed_good_model_and_points_at_the_local_viewer(built, tmp_path):
    built_ok = need_built(built)
    d = copy_project(built_ok, tmp_path / "changed-proj")
    try:
        params = d / "mechanics" / "cad" / "params.py"
        src = params.read_text()
        assert re.search(r"^[A-Z_]+_MM = \d", src, re.M), "params.py has no _MM number to change"
        params.write_text(re.sub(r"^([A-Z_]+_MM) = [0-9.]+", r"\1 = 77.0", src, count=1, flags=re.M))   # a real, valid size change
        before = (d / "build" / "model" / "parts.json").read_text()
        res = fire("Stop", d, {"stop_hook_active": False}, timeout=1200)
        assert not blocked(res), res[0].why("Stop blocked on a good model")
        assert all(r.rc == 0 for r in res), res[0].why("Stop")
        text = "\n".join(r.text for r in res)
        assert re.search(r"http://(127\.0\.0\.1|localhost)", text), f"Stop's output does not point at the localhost viewer:\n{text[-1500:]}"
        assert (d / "build" / "model" / "parts.json").read_text() != before, "Stop did not rebuild the changed model"
    finally:
        stop(d)
