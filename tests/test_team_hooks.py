"""Team mode through the Claude Code hooks (agent/hooks/hooks.json), run as Claude Code runs them: the registered command,
the hook input JSON on stdin, cwd = the project, the session's env (ENG_LANE=exec + ENG_ISSUE=N for an executor).

Done-when 1: the manager session gets the manager rules, an executor session the executor rules and its issue.
Done-when 2: a manager comment or the `stop` label reaches the executor on its next tool call (PostToolUse, any tool), once.
Done-when 3: the manager sees an executor's hand-over (and comments, questions) on its next prompt (UserPromptSubmit), once.
Viewer notes (.engineer/inbox/*-note.json) still go to the manager only.

NOTE for the builder: "PostToolUse on any tool" for executors contradicts the stage-1 test
test_hooks.py::test_hooks_are_registered_for_session_start_edits_and_stop, which asserts that PostToolUse does not fire on
Bash. Both cannot pass with one PostToolUse matcher; the developer has to decide (the Bash cases below are marked).
"""
import json
import re

import pytest

from conftest_team import (EXECUTOR_RULES, MANAGER_RULES, additional_context, added_context, all_ok, fire, principle_sections,
                           rules_line, solo, state, team)  # noqa: F401  (fixtures)

TOOLS = ["Edit", pytest.param("Bash", id="Bash-conflicts-with-stage-1-matcher"), "Read"]
NOTE = {"comment": "move the left hole 2 mm up", "parts": ["bracket"], "view": {"camera": {"yaw": 0.5}}}


def post_tool(t, n, tool="Edit"):
    res = fire(t, "PostToolUse", tool=tool, lane="exec", issue=n)
    all_ok(res, f"executor PostToolUse ({tool})")
    return additional_context(res)


def manager_prompt(t, text="what is the status?"):
    res = fire(t, "UserPromptSubmit", {"prompt": text})
    all_ok(res, "manager UserPromptSubmit")
    return added_context(res)


def put_note(t):
    inbox = t.dir / ".engineer" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "20261103-101500-note.json").write_text(json.dumps(NOTE), encoding="utf-8")


# ---------------------------------------------------------------- SessionStart
def test_manager_session_start_has_the_manager_rules_and_the_principles(team):
    t = team.need()
    mgr, exe = rules_line(MANAGER_RULES), rules_line(EXECUTOR_RULES)
    res = fire(t, "SessionStart", {"source": "startup"})
    all_ok(res, "SessionStart (manager)")
    ctx = added_context(res)
    assert mgr in ctx, f"the manager's SessionStart lacks the manager rules ({mgr!r})"
    assert exe not in ctx, "the manager's SessionStart carries the executor rules"
    missing = [h for h in principle_sections() if h not in ctx]
    assert not missing, f"the manager's SessionStart lacks PRINCIPLES.md sections: {missing[:5]}"


def test_executor_session_start_has_the_executor_rules_its_issue_and_the_principles(team):
    t = team.need()
    mgr, exe = rules_line(MANAGER_RULES), rules_line(EXECUTOR_RULES)
    t.new("Other task zeta")
    n = t.new("NEMA bracket kappa", "Bracket for a NEMA 17.")
    res = fire(t, "SessionStart", {"source": "startup"}, lane="exec", issue=n)
    all_ok(res, "SessionStart (executor)")
    ctx = added_context(res)
    assert exe in ctx, f"the executor's SessionStart lacks the executor rules ({exe!r})"
    assert mgr not in ctx, "the executor's SessionStart carries the manager rules"
    assert "NEMA bracket kappa" in ctx, "the executor's SessionStart does not name its issue's title"
    assert "Other task zeta" not in ctx, "the executor's SessionStart names another issue"
    missing = [h for h in principle_sections() if h not in ctx]
    assert not missing, f"the executor's SessionStart lacks PRINCIPLES.md sections: {missing[:5]}"


def test_single_lane_session_start_has_no_team_rules(solo):
    t = solo.need()
    mgr, exe = rules_line(MANAGER_RULES), rules_line(EXECUTOR_RULES)
    ctx = added_context(fire(t, "SessionStart", {"source": "startup"}))
    assert mgr not in ctx and exe not in ctx, "a single-lane project got the team rules at SessionStart"


def test_team_on_switches_the_session_start_to_the_manager_rules(solo):
    t = solo.need()
    mgr = rules_line(MANAGER_RULES)
    r = t.de("team", "on")
    assert r.rc == 0, r.why("de team on")
    assert mgr in added_context(fire(t, "SessionStart", {"source": "startup"})), "after `de team on` no manager rules"
    assert t.de("team", "off").rc == 0
    assert mgr not in added_context(fire(t, "SessionStart", {"source": "startup"})), "after `de team off` still manager rules"


# ---------------------------------------------------------------- executor PostToolUse (done-when 2)
@pytest.mark.parametrize("tool", TOOLS)
def test_executor_gets_a_new_manager_comment_on_its_next_tool_call_once(team, tool):
    t = team.need()
    n = t.new("Bracket", "b")
    assert t.mgr("comment", str(n), "please use M3 screws, not M4").rc == 0
    ctx = post_tool(t, n, tool)
    assert "please use M3 screws, not M4" in ctx, f"PostToolUse ({tool}) did not hand the manager's comment over: {ctx!r}"
    again = post_tool(t, n, tool)
    assert "please use M3 screws, not M4" not in again, "the same comment was handed over twice"


def test_a_comment_delivered_by_the_hook_counts_as_seen_for_hand_over(team):
    t = team.need()
    n = t.new("Bracket", "b")
    assert t.mgr("comment", str(n), "make it 5 mm thick").rc == 0
    assert t.ex(n, "close", "done").rc != 0, "close must be refused before the comment is seen"
    assert "make it 5 mm thick" in post_tool(t, n)
    r = t.ex(n, "close", "done, 5 mm")
    assert r.rc == 0, r.why("close after the hook delivered the comment")
    assert state(t, n) == "review"


def test_executor_does_not_get_comments_of_other_issues_or_its_own(team):
    t = team.need()
    a, b = t.new("A", "b"), t.new("B", "b")
    assert t.mgr("comment", str(b), "only for B 3d1f").rc == 0
    assert t.ex(a, "comment", "my own note 8e0a").rc == 0
    ctx = post_tool(t, a)
    assert "only for B 3d1f" not in ctx, "the executor of A got a comment on B"
    assert "my own note 8e0a" not in ctx, "the executor got its own comment back"


def test_nothing_new_means_no_stop_and_no_comment(team):
    t = team.need()
    n = t.new("Quiet", "b")
    ctx = post_tool(t, n)
    assert not re.search(r"\bstop\b", ctx, re.I), f"PostToolUse says stop without the label: {ctx!r}"


@pytest.mark.parametrize("tool", TOOLS)
def test_label_stop_tells_the_executor_to_stop_on_its_next_tool_call(team, tool):
    t = team.need()
    n = t.new("Bracket", "b")
    assert t.mgr("label", str(n), "+stop").rc == 0
    ctx = post_tool(t, n, tool)
    assert re.search(r"stop", ctx, re.I), f"PostToolUse ({tool}) did not tell the executor to stop: {ctx!r}"


def test_stop_is_only_for_the_labelled_issue(team):
    t = team.need()
    a, b = t.new("A", "b"), t.new("B", "b")
    assert t.mgr("label", str(b), "+stop").rc == 0
    assert not re.search(r"\bstop\b", post_tool(t, a), re.I), "the executor of A was told to stop by B's label"


def test_manager_post_tool_gets_no_executor_delivery(team):
    t = team.need()
    n = t.new("Bracket", "b")
    assert t.mgr("comment", str(n), "manager text 55aa").rc == 0
    res = fire(t, "PostToolUse", tool="Edit")
    all_ok(res, "manager PostToolUse")
    assert "manager text 55aa" not in additional_context(res), "the manager's own comment came back to the manager"


# ---------------------------------------------------------------- manager UserPromptSubmit (done-when 3)
@pytest.mark.parametrize("op,args,text", [
    ("comment", ("progress: base plate done 6c1e",), "progress: base plate done 6c1e"),
    ("ask", ("2020 or 2040 profile? 0b7d",), "2020 or 2040 profile? 0b7d"),
    ("close", ("bracket modelled, check passes 9a4f",), "bracket modelled, check passes 9a4f"),
], ids=["comment", "ask", "hand-over"])
def test_manager_sees_an_executor_event_on_its_next_prompt_once(team, op, args, text):
    t = team.need()
    t.new("First", "b")
    n = t.new("Second", "b")
    r = t.ex(n, op, *args)
    assert r.rc == 0, r.why(f"executor issue {op}")
    ctx = manager_prompt(t)
    assert text in ctx, f"the manager's UserPromptSubmit lacks the executor's {op}: {ctx[-1500:]!r}"
    assert re.search(rf"#{n}\b", ctx), f"the event does not name issue #{n}: {ctx[-1500:]!r}"
    again = manager_prompt(t)
    assert text not in again, f"the executor's {op} was shown to the manager twice"


def test_manager_sees_events_of_several_issues_together(team):
    t = team.need()
    a, b = t.new("A", "b"), t.new("B", "b")
    assert t.ex(a, "comment", "from A 1111").rc == 0
    assert t.ex(b, "close", "from B 2222").rc == 0
    ctx = manager_prompt(t)
    assert "from A 1111" in ctx and "from B 2222" in ctx, ctx[-1500:]


def test_manager_does_not_get_its_own_comments_back(team):
    t = team.need()
    n = t.new("A", "b")
    assert t.mgr("comment", str(n), "manager wrote this 7777").rc == 0
    assert "manager wrote this 7777" not in manager_prompt(t)


def test_viewer_notes_go_to_the_manager_in_a_team_project(team):
    t = team.need()
    put_note(t)
    assert NOTE["comment"] in manager_prompt(t), "a viewer note did not reach the manager's prompt"


def test_viewer_notes_never_go_to_an_executor(team):
    t = team.need()
    n = t.new("A", "b")
    put_note(t)
    ex_prompt = fire(t, "UserPromptSubmit", {"prompt": "go on"}, lane="exec", issue=n)
    all_ok(ex_prompt, "executor UserPromptSubmit")
    assert NOTE["comment"] not in added_context(ex_prompt), "a viewer note went to an executor's prompt"
    assert NOTE["comment"] not in post_tool(t, n), "a viewer note went to an executor's tool call"
    assert NOTE["comment"] in manager_prompt(t), "after an executor fired its hooks the note no longer reaches the manager"


# ---------------------------------------------------------------- Stop
def test_manager_stop_neither_builds_nor_blocks_over_an_executors_unfinished_part(team):
    """Review M2: the manager may not edit the model, so its Stop never sends it back to fix one; executors check their own."""
    t = team.need()
    parts = t.dir / "mechanics" / "cad" / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    (parts / "half_done.py").write_text("def shape(:\n")
    log = t.dir / ".engineer" / "build.log"
    before = log.stat().st_mtime if log.exists() else None
    res = fire(t, "Stop", {"stop_hook_active": False})
    all_ok(res, "manager Stop")
    assert not any('"block"' in r.out for r in res), [r.out for r in res]
    assert (log.stat().st_mtime if log.exists() else None) == before, "the manager's Stop ran a build"
