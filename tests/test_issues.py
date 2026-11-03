"""Team mode, the task backend and the executors (stage 2).

Done-when 1: in a `de init --team` project the manager opens a task with `issue new ...` and a background Claude Code
session for it starts (here: the fake claude from conftest_team records the call), in the project folder, as the executor.
Done-when 2 (second half): an executor cannot hand over while it has unread manager comments.
Done-when 3: the executor hands over with `issue close "result"` (state review, not closed); the manager closes it with
`issue close N "reason"`, which stops the executor.
Done-when 4: two open tasks on the same part are refused unless ordered (--after) or separated (--separate).
Plus: builds from several sessions in one project are serialized.
"""
import json
import os
import re
import subprocess
import time

import pytest

from conftest import DE, clean_env, init_project, run
from conftest_team import (FAKE_MARKER, ISSUE_SHIM, UNKNOWN_BG, Team, alive, has_string, issue_json, issue_number, lanes,
                           mentions, solo, state, team, team_env, wait_dead, wait_state)  # noqa: F401  (fixtures)

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


# ---------------------------------------------------------------- opt-in: de init --team, de team on|off
def test_init_team_writes_the_team_lane(team):
    t = team.need()
    assert "team" in lanes(t.dir), f".engineer/lanes after `de init --team`: {lanes(t.dir)!r}"
    assert (t.dir / ".engineer" / "mode").read_text().strip() == "local", "a team project is still a local project"


def test_init_without_team_stays_single_lane(solo):
    t = solo.need()
    assert "team" not in lanes(t.dir), f"plain `de init` made a team project: .engineer/lanes = {lanes(t.dir)!r}"


def test_team_on_and_off_in_an_existing_project(solo):
    t = solo.need()
    r = t.de("team", "on")
    assert r.rc == 0, r.why("de team on")
    assert r.out.strip(), r.why("de team on printed nothing")
    assert "team" in lanes(t.dir), f".engineer/lanes after `de team on`: {lanes(t.dir)!r}"
    r = t.de("team", "off")
    assert r.rc == 0, r.why("de team off")
    assert "team" not in lanes(t.dir), f".engineer/lanes after `de team off`: {lanes(t.dir)!r}"


# ---------------------------------------------------------------- manager ops
def test_issue_new_numbers_from_one_and_stores_one_json_per_issue(team):
    t = team.need()
    r = t.mgr("new", "--title", "Motor bracket", "--body", "A bracket for a NEMA 17 on a 2020 profile.", "--no-start")
    assert r.rc == 0, r.why("issue new")
    assert issue_number(r.out) == 1, r.why("the first issue is not #1")
    assert t.new("Second task") == 2
    one = issue_json(t.dir, 1)
    assert one.get("state") == "open", one
    assert has_string(one, "Motor bracket"), f"the title is not stored: {one}"
    assert has_string(one, "A bracket for a NEMA 17 on a 2020 profile."), f"the body is not stored: {one}"
    assert sorted(p.name for p in (t.dir / ".engineer" / "issues").glob("*.json")) == ["1.json", "2.json"]


def test_issue_new_waiting_is_waiting_and_starts_no_executor(team):
    t = team.need()
    r = t.mgr("new", "--title", "Ask the user first", "--body", "Which motor?", "--waiting")
    assert r.rc == 0, r.why("issue new --waiting")
    n = issue_number(r.out)
    assert state(t, n) == "waiting"
    time.sleep(1)
    assert not t.fake.starts(), f"--waiting started an executor: {t.fake.calls()}"


def test_issue_new_no_start_is_open_and_starts_no_executor(team):
    t = team.need()
    n = t.new("Later", start=False)
    assert state(t, n) == "open"
    time.sleep(1)
    assert not t.fake.starts(), f"--no-start started an executor: {t.fake.calls()}"


def test_issue_list_shows_every_non_closed_issue_one_line_each(team):
    t = team.need()
    a, b, c = t.new("Alpha plate"), t.new("Beta shaft"), t.new("Gamma cover")
    assert t.mgr("close", str(b), "not needed").rc == 0
    r = t.mgr("list")
    assert r.rc == 0, r.why("issue list")
    lines = [ln for ln in r.out.splitlines() if ln.strip()]
    for n, title in ((a, "Alpha plate"), (c, "Gamma cover")):
        mine = [ln for ln in lines if re.search(rf"#?\b{n}\b", ln) and title in ln]
        assert len(mine) == 1, r.why(f"issue list has no single line for #{n} {title!r}")
        assert "open" in mine[0], r.why(f"the line of #{n} does not show its state")
    assert not any("Beta shaft" in ln for ln in lines), r.why("issue list shows a closed issue")


def test_issue_show_has_title_body_and_every_comment_with_its_author(team):
    t = team.need()
    n = t.new("Show me", "The body text 7f3a.")
    assert t.mgr("comment", str(n), "manager says 91c2").rc == 0
    assert t.ex(n, "comment", "executor says 4be8").rc == 0
    r = t.mgr("show", str(n))
    assert r.rc == 0, r.why("issue show")
    for s in ("Show me", "The body text 7f3a.", "manager says 91c2", "executor says 4be8"):
        assert s in r.out, r.why(f"issue show lacks {s!r}")
    lines = r.out.splitlines()

    def near(text):
        i = next(i for i, ln in enumerate(lines) if text in ln)
        return "\n".join(lines[max(0, i - 2): i + 1]).lower()
    assert "manager" in near("manager says 91c2"), r.why("the manager's comment is not marked as the manager's")
    assert re.search(r"exec", near("executor says 4be8")), r.why("the executor's comment is not marked as the executor's")


def test_issue_label_adds_and_removes_stop_and_urgent(team):
    t = team.need()
    n = t.new("Label me")
    for lab in ("+stop", "+urgent"):
        r = t.mgr("label", str(n), lab)
        assert r.rc == 0, r.why(f"issue label {lab}")
    one = t.issue(n)
    assert has_string(one, "stop") and has_string(one, "urgent"), f"labels not stored: {one}"
    assert t.mgr("label", str(n), "-stop").rc == 0
    one = t.issue(n)
    assert not has_string(one, "stop") and has_string(one, "urgent"), f"-stop did not remove only stop: {one}"


def test_issue_label_refuses_an_unknown_label(team):
    t = team.need()
    n = t.new("Label me")
    r = t.mgr("label", str(n), "+banana")
    assert r.rc != 0, r.why("issue label +banana must be refused")
    assert not has_string(t.issue(n), "banana")


def test_issue_reopen_puts_a_handed_over_issue_back_with_the_reason(team):
    t = team.need()
    n = t.new("Reopen me")
    assert t.ex(n, "close", "done, I think").rc == 0
    assert state(t, n) == "review"
    r = t.mgr("reopen", str(n), "the hole is 2 mm off")
    assert r.rc == 0, r.why("issue reopen")
    assert state(t, n) == "open"
    assert "the hole is 2 mm off" in t.mgr("show", str(n)).out


def test_ops_on_a_missing_issue_fail_with_a_reason(team):
    t = team.need()
    for args in (("show", "42"), ("comment", "42", "hi"), ("close", "42", "x"), ("label", "42", "+stop")):
        r = t.mgr(*args)
        assert r.rc != 0, r.why(f"issue {' '.join(args)} on a missing issue")
        assert "42" in r.text, r.why("the error does not name the missing issue")


def test_the_issue_shim_is_the_same_as_de_issue(team):
    t = team.need()
    r = t.shim("new", "--title", "Via the shim", "--body", "b", "--no-start")
    assert r.rc == 0, r.why(f"{ISSUE_SHIM} new")
    n = issue_number(r.out)
    assert n == 1 and has_string(issue_json(t.dir, n), "Via the shim")
    r = t.shim("show", str(n))
    assert r.rc == 0 and "Via the shim" in r.out, r.why("issue show (shim)")


def test_every_op_prints_a_line(team):
    t = team.need()
    n = t.new("Talkative")
    for args, kw in ((("comment", str(n), "c"), {}), (("label", str(n), "+urgent"), {}), (("list",), {})):
        r = t.mgr(*args, **kw)
        assert r.rc == 0 and r.out.strip(), r.why(f"issue {' '.join(args)} printed nothing")
    for args in (("show",), ("comment", "progress"), ("ask", "which motor?")):
        r = t.ex(n, *args)
        assert r.rc == 0 and r.out.strip(), r.why(f"executor issue {' '.join(args)} printed nothing")


# ---------------------------------------------------------------- one task per part (done-when 4)
def test_a_second_open_issue_on_the_same_part_is_refused_naming_the_first(team):
    t = team.need()
    a = t.new("Bracket body", "b", "--part", "bracket")
    r = t.mgr("new", "--title", "Bracket holes", "--body", "b", "--part", "bracket", "--no-start")
    assert r.rc != 0, r.why("a second issue on part `bracket` must be refused")
    assert f"#{a}" in r.text or re.search(rf"\b{a}\b", r.text), r.why(f"the refusal does not name #{a}")
    assert not (t.dir / ".engineer" / "issues" / "2.json").exists(), "the refused issue was stored anyway"


def test_one_overlapping_part_out_of_several_is_enough_to_refuse(team):
    t = team.need()
    t.new("Plate", "b", "--part", "plate", "--part", "bracket")
    r = t.mgr("new", "--title", "Other", "--body", "b", "--part", "shaft", "--part", "bracket", "--no-start")
    assert r.rc != 0, r.why("an issue sharing part `bracket` must be refused")


def test_same_part_is_allowed_with_after_or_separate(team):
    t = team.need()
    a = t.new("Bracket body", "b", "--part", "bracket")
    after = t.new("Bracket holes", "b", "--part", "bracket", "--after", str(a))
    sep = t.new("Bracket colour", "b", "--part", "bracket", "--separate", "only the colour, no geometry")
    assert len({a, after, sep}) == 3


def test_different_parts_and_closed_issues_do_not_conflict(team):
    t = team.need()
    a = t.new("Bracket", "b", "--part", "bracket")
    t.new("Shaft", "b", "--part", "shaft")
    assert t.mgr("close", str(a), "done").rc == 0
    t.new("Bracket again", "b", "--part", "bracket")


def test_an_issue_in_review_still_holds_its_part(team):
    t = team.need()
    a = t.new("Bracket", "b", "--part", "bracket")
    assert t.ex(a, "close", "handed over").rc == 0
    r = t.mgr("new", "--title", "Bracket 2", "--body", "b", "--part", "bracket", "--no-start")
    assert r.rc != 0, r.why("an issue in review is not closed: its part is still taken")


# ---------------------------------------------------------------- executor ops
def test_executor_show_needs_no_number(team):
    t = team.need()
    n = t.new("Exec view", "Body e51d")
    r = t.ex(n, "show")
    assert r.rc == 0, r.why("executor issue show")
    assert "Exec view" in r.out and "Body e51d" in r.out, r.why("executor issue show lacks title/body")


def test_executor_comment_with_files(team, tmp_path):
    t = team.need()
    n = t.new("With files")
    f = t.dir / "build" / "render.png"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\x89PNG\r\n")
    r = t.ex(n, "comment", "here is the render", "--file", str(f))
    assert r.rc == 0, r.why("executor issue comment --file")
    shown = t.mgr("show", str(n)).out
    assert "here is the render" in shown and "render.png" in shown, f"the comment or its file is not in issue show:\n{shown}"


def test_executor_ask_sets_waiting(team):
    t = team.need()
    n = t.new("Ask")
    r = t.ex(n, "ask", "Which profile, 2020 or 2040?")
    assert r.rc == 0, r.why("executor issue ask")
    assert state(t, n) == "waiting"
    assert "Which profile, 2020 or 2040?" in t.mgr("show", str(n)).out


def test_executor_close_hands_over_to_review_and_does_not_close(team):
    t = team.need()
    n = t.new("Hand over", "b")
    f = t.dir / "build" / "bracket.step"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("ISO-10303-21;")
    r = t.ex(n, "close", "bracket modelled, check passes", "--file", str(f))
    assert r.rc == 0, r.why("executor issue close")
    assert state(t, n) == "review", f"executor close must hand over (review), state is {state(t, n)!r}"
    shown = t.mgr("show", str(n)).out
    assert "bracket modelled, check passes" in shown and "bracket.step" in shown, shown
    assert any("Hand over" in ln for ln in t.mgr("list").out.splitlines()), "a handed-over issue left the list"


def test_executor_cannot_hand_over_with_an_unread_manager_comment(team):
    """Done-when 2: it cannot hand over while it has unread manager comments; `issue show` makes them read."""
    t = team.need()
    n = t.new("Unread", "b")
    assert t.mgr("comment", str(n), "also add a slot for the belt").rc == 0
    r = t.ex(n, "close", "done")
    assert r.rc != 0, r.why("executor close with an unread manager comment must be refused")
    assert state(t, n) != "review", "the refused close still handed over"
    s = t.ex(n, "show")
    assert "also add a slot for the belt" in s.out, s.why("executor issue show does not print the manager's comment")
    r = t.ex(n, "close", "done, slot added")
    assert r.rc == 0, r.why("executor close after reading the comment")
    assert state(t, n) == "review"


def test_executor_own_comments_do_not_block_hand_over(team):
    t = team.need()
    n = t.new("Own", "b")
    assert t.ex(n, "comment", "progress: half done").rc == 0
    r = t.ex(n, "close", "done")
    assert r.rc == 0, r.why("the executor's own comment blocked its hand-over")


def test_a_manager_comment_on_another_issue_does_not_block(team):
    t = team.need()
    a, b = t.new("A", "b"), t.new("B", "b")
    assert t.mgr("comment", str(b), "for B only").rc == 0
    r = t.ex(a, "close", "A done")
    assert r.rc == 0, r.why("a comment on #B blocked the executor of #A")


# ---------------------------------------------------------------- executors: start, record, stop (done-when 1, 3)
def started_ok(t: Team, n, title):
    calls = t.fake.wait_starts(n)
    assert calls, f"no claude session was started for #{n}; fake calls: {t.fake.calls()}"
    c = calls[0]
    assert "--bg" in c["argv"], f"the executor was not started with --bg: {c['argv']}"
    assert c["env"] == {"ENG_LANE": "exec", "ENG_ISSUE": str(n)}, f"executor env: {c['env']}"
    from pathlib import Path
    assert Path(c["cwd"]).resolve() == t.dir.resolve(), f"the executor runs in {c['cwd']}, not the project {t.dir}"
    argv = c["argv"]
    assert "--name" in argv, f"no --name: {argv}"
    name = argv[argv.index("--name") + 1]
    assert name == f"#{n} {title}", f"--name is {name!r}, want '#{n} {title}'"
    rest = [a for i, a in enumerate(argv) if i != argv.index("--name") + 1]
    assert any(re.search(rf"(#|issue\s*){n}\b", a, re.I) for a in rest), f"the prompt does not name issue {n}: {argv}"
    return c


def test_issue_new_starts_a_background_executor_session(team):
    """Done-when 1: `issue new` -> `claude --bg --name "#N <title>"` in the project, as the executor of #N, recorded."""
    t = team.need()
    r = t.mgr("new", "--title", "Motor bracket", "--body", "NEMA 17 on 2020")
    assert r.rc == 0, r.why("issue new (starts an executor)")
    n = issue_number(r.out)
    c = started_ok(t, n, "Motor bracket")
    assert mentions(t.issue(n), c["session"]), f"the issue does not record the session {c['session']}: {t.issue(n)}"


def test_de_exec_start_starts_an_executor_for_an_existing_issue(team):
    t = team.need()
    n = t.new("Later bracket", start=False)
    r = t.de("exec", "start", str(n))
    assert r.rc == 0, r.why("de exec start")
    c = started_ok(t, n, "Later bracket")
    assert mentions(t.issue(n), c["session"]), f"the issue does not record the session: {t.issue(n)}"


def test_executor_uses_claude_on_path_without_de_claude(team):
    t = team.need()
    n = t.new("Path claude", start=False)
    env = t.env()
    env.pop("DE_CLAUDE")
    r = run([DE, "exec", "start", str(n)], cwd=t.dir, env=env, timeout=120)
    assert r.rc == 0, r.why("de exec start with `claude` from PATH")
    assert t.fake.wait_starts(n), "the `claude` on PATH was not called"


def test_without_bg_support_it_falls_back_to_a_detached_print_session_with_a_log(team):
    t = team.need()
    t.mode = "nobg"
    n = t.new("Old claude", start=False)
    t0 = time.time()
    r = t.de("exec", "start", str(n), timeout=60)
    assert r.rc == 0, r.why("de exec start, claude without --bg")
    assert time.time() - t0 < 30, "de exec start waited for the -p session instead of detaching it"
    calls = t.fake.wait_starts(n, count=2)
    assert any(c.get("refused") for c in calls), f"--bg was never tried: {calls}"
    p = [c for c in calls if c.get("print_mode")]
    assert p, f"no fallback `claude -p` after --bg failed: {calls}"
    assert p[0]["env"] == {"ENG_LANE": "exec", "ENG_ISSUE": str(n)}, p[0]["env"]
    log = t.dir / ".engineer" / "issues" / f"{n}.log"
    end = time.time() + 15
    while time.time() < end and not (log.is_file() and FAKE_MARKER in log.read_text()):
        time.sleep(0.2)
    assert log.is_file() and FAKE_MARKER in log.read_text(), f"the -p output did not go to {log}"
    assert alive(p[0]["pid"]), "the detached executor died with `de exec start`"
    r = t.de("exec", "stop", str(n))
    assert r.rc == 0, r.why("de exec stop")
    assert wait_dead(p[0]["pid"]), "de exec stop left the -p executor running"


def test_manager_close_stops_a_fallback_executor(team):
    t = team.need()
    t.mode = "nobg"
    r = t.mgr("new", "--title", "Stop me", "--body", "b")
    assert r.rc == 0, r.why("issue new")
    n = issue_number(r.out)
    p = [c for c in t.fake.wait_starts(n, count=2) if c.get("print_mode")]
    assert p and alive(p[0]["pid"]), f"no running executor: {t.fake.calls()}"
    r = t.mgr("close", str(n), "not needed any more")
    assert r.rc == 0, r.why("issue close N")
    assert state(t, n) == "closed"
    assert wait_dead(p[0]["pid"]), "issue close N left the executor running"


def test_manager_close_stops_a_background_session(team):
    """Done-when 3: a --bg session is stopped through claude, naming that session."""
    t = team.need()
    r = t.mgr("new", "--title", "Bg stop", "--body", "b")
    n = issue_number(r.out)
    c = started_ok(t, n, "Bg stop")
    before = len(t.fake.calls())
    r = t.mgr("close", str(n), "done")
    assert r.rc == 0, r.why("issue close N")
    later = t.fake.calls()[before:]
    assert any(c["session"] in " ".join(x["argv"]) and "--bg" not in x["argv"] for x in later), \
        f"issue close did not ask claude to stop session {c['session']}: {later}"


def bg_starts(t, n):
    return [c for c in t.fake.starts(n) if "--bg" in c["argv"]]


def test_a_manager_comment_after_handover_starts_the_executor_again(team):
    """Review M1: a comment that sends the work back reaches an executor: the issue is open again and its executor runs."""
    t = team.need()
    r = t.mgr("new", "--title", "Send back", "--body", "b")
    n = issue_number(r.out)
    started_ok(t, n, "Send back")
    t.de("exec", "stop", str(n))
    assert t.ex(n, "close", "done, I think").rc == 0
    assert state(t, n) == "review"
    r = t.mgr("comment", str(n), "the second hole is missing")
    assert r.rc == 0, r.why("issue comment N after handover")
    assert state(t, n) == "open", t.issue(n)
    assert len(t.fake.wait_starts(n, count=2)) >= 2 and len(bg_starts(t, n)) == 2, t.fake.calls()


def test_a_manager_answer_to_an_ask_starts_the_executor_again(team):
    t = team.need()
    r = t.mgr("new", "--title", "Ask me", "--body", "b")
    n = issue_number(r.out)
    started_ok(t, n, "Ask me")
    t.de("exec", "stop", str(n))
    assert t.ex(n, "ask", "steel or aluminium?").rc == 0
    assert state(t, n) == "waiting"
    assert t.mgr("comment", str(n), "aluminium").rc == 0
    assert state(t, n) == "open"
    assert len(t.fake.wait_starts(n, count=2)) >= 2 and len(bg_starts(t, n)) == 2, t.fake.calls()


def test_a_comment_to_a_running_executor_starts_no_second_one(team):
    t = team.need()
    r = t.mgr("new", "--title", "Busy", "--body", "b")
    n = issue_number(r.out)
    started_ok(t, n, "Busy")
    assert t.mgr("comment", str(n), "also round the corners").rc == 0
    time.sleep(1)
    assert len(bg_starts(t, n)) == 1, t.fake.calls()


def test_closing_an_issue_starts_the_one_that_waited_for_it(team):
    t = team.need()
    first = t.new("Plate", "b", "--part", "plate", start=True)
    second = t.new("Plate holes", "b", "--part", "plate", "--after", str(first), start=True)
    assert t.fake.wait_starts(first) and not bg_starts(t, second), "an --after issue started before its first one closed"
    r = t.mgr("close", str(first), "done")
    assert r.rc == 0, r.why("issue close N")
    assert t.fake.wait_starts(second), f"closing #{first} did not start #{second}: {t.fake.calls()}"


def test_a_background_session_without_a_printed_id_gets_no_second_executor(team):
    """Review m7: --bg exited 0 but printed no id -> recorded as a background session, no `claude -p` beside it."""
    t = team.need()
    t.mode = "bgnoid"
    n = t.new("No id", start=False)
    r = t.de("exec", "start", str(n))
    assert r.rc == 0, r.why("de exec start")
    time.sleep(1)
    assert not [c for c in t.fake.starts(n) if c.get("print_mode")], f"a -p executor beside the --bg one: {t.fake.calls()}"
    assert (t.issue(n).get("executor") or {}).get("kind") == "bg", t.issue(n)


def test_de_exec_stop_stops_a_background_session(team):
    t = team.need()
    r = t.mgr("new", "--title", "Bg stop 2", "--body", "b")
    n = issue_number(r.out)
    c = started_ok(t, n, "Bg stop 2")
    before = len(t.fake.calls())
    r = t.de("exec", "stop", str(n))
    assert r.rc == 0, r.why("de exec stop")
    later = t.fake.calls()[before:]
    assert any(c["session"] in " ".join(x["argv"]) for x in later), f"de exec stop did not stop {c['session']}: {later}"


def test_the_whole_loop_with_a_working_fake_executor(team):
    """Done-when 1+3 through the fake: issue new -> the executor hands over -> state review -> the manager closes it."""
    t = team.need()
    r = t.mgr("new", "--title", "Loop", "--body", "b", work=True)
    assert r.rc == 0, r.why("issue new")
    n = issue_number(r.out)
    assert wait_state(t, n, "review", timeout=60), f"the executor never handed over; state {state(t, n)!r}; calls {t.fake.calls()}"
    assert "fake result: the bracket is modelled" in t.mgr("show", str(n)).out
    r = t.mgr("close", str(n), "checked, good")
    assert r.rc == 0 and state(t, n) == "closed", r.why("issue close N")


# ---------------------------------------------------------------- builds from several sessions
def cpu_seconds(t):
    """ps TIME: [[dd-]hh:]mm:ss[.cc] -> seconds."""
    days, _, t = t.rpartition("-")
    parts = [float(x) for x in t.split(":")]
    sec = 0.0
    for x in parts:
        sec = sec * 60 + x
    return sec + (int(days) * 86400 if days else 0)


def tree_cpu(roots):
    """{root pid: CPU seconds used so far by that process and all its live descendants}."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,time="], capture_output=True, text=True).stdout
    procs = {}
    for ln in out.splitlines():
        f = ln.split()
        if len(f) == 3 and f[0].isdigit() and f[1].isdigit():
            procs[int(f[0])] = (int(f[1]), cpu_seconds(f[2]))
    tot = {}
    for r in roots:
        tree, todo = set(), [r]
        while todo:
            x = todo.pop()
            if x in procs and x not in tree:
                tree.add(x)
                todo += [p for p, (pp, _) in procs.items() if pp == x]
        tot[r] = sum(procs[x][1] for x in tree)
    return tot


@pytest.mark.slow
def test_two_builds_at_once_both_succeed_and_do_not_run_together(tmp_path):
    """Two `de build` of one project started together: both exit 0, the viewer site is whole, and they never build at the
    same time (sampled once a second: CPU time of each build's process tree; a build waiting for the other uses none).
    Today both build in parallel (~60 s each, both busy every second)."""
    d, ri = init_project(tmp_path, "two-builds")
    assert ri.rc == 0, ri.why("de init")
    env = clean_env()
    ps = []
    for _ in range(2):
        ps.append(subprocess.Popen([str(DE), "build", str(d)], cwd=d, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, start_new_session=True))
    logs = [tmp_path / f"build-{i}.log" for i in range(2)]
    readers = [subprocess.Popen(["cat"], stdin=p.stdout, stdout=open(lg, "w")) for p, lg in zip(ps, logs)]
    pg = [p.pid for p in ps]
    last, both_busy, any_busy, end = tree_cpu(pg), 0, 0, time.time() + 1800
    samples = []
    while any(p.poll() is None for p in ps) and time.time() < end:
        time.sleep(1)
        now = tree_cpu(pg)
        busy = [now[g] - last[g] > 0.3 for g in pg]
        samples.append([round(now[g] - last[g], 1) for g in pg])
        both_busy += all(busy)
        any_busy += any(busy)
        last = now
    for p in ps:
        if p.poll() is None:
            os.killpg(p.pid, 9)
    for r in readers:
        r.wait(timeout=30)
    for p, lg in zip(ps, logs):
        assert p.returncode == 0, f"a concurrent de build failed (exit {p.returncode}):\n{lg.read_text()[-3000:]}"
    assert any_busy >= 10, f"the CPU sampling saw almost no build work ({any_busy} s busy): the measurement is broken"
    assert both_busy <= 2, f"the two builds worked at the same time for {both_busy} of {any_busy} busy seconds: not serialized (CPU s per second: {samples})"
    glb = d / "public" / "model.glb"
    assert glb.is_file() and glb.read_bytes()[:4] == b"glTF", "public/model.glb is missing or not a GLB after two builds"
    json.loads((d / "public" / "config.json").read_text())
    json.loads((d / "build" / "model" / "parts.json").read_text())
