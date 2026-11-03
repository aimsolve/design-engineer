"""Team mode: the manager cannot edit the model (found by the real e2e: told not to, it modelled anyway)."""
import json

from conftest import de, init_project
from test_hooks import fire


def test_manager_edit_of_a_model_file_is_denied(tmp_path):
    d, r = init_project(tmp_path, "guarded", "--team")
    assert r.rc == 0, r.why("de init --team")
    assert "## Team mode" in (d / "CLAUDE.md").read_text()
    part = next((d / "mechanics/cad/parts").rglob("block.py"))
    res = fire("PreToolUse", d, {"tool_name": "Edit", "tool_input": {"file_path": str(part)}}, tool="Edit")[0]
    out = json.loads(res.out.strip().splitlines()[-1])["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "issue new" in out["permissionDecisionReason"], res.out
    wiki = fire("PreToolUse", d, {"tool_name": "Write", "tool_input": {"file_path": str(d / "wiki/log.md")}}, tool="Write")[0]
    assert not wiki.out.strip(), wiki.out          # the wiki stays the manager's


def test_executor_and_single_lane_may_edit(tmp_path):
    d, r = init_project(tmp_path, "free", "--team")
    part = next((d / "mechanics/cad/parts").rglob("block.py"))
    ex = fire("PreToolUse", d, {"tool_name": "Edit", "tool_input": {"file_path": str(part)}}, tool="Edit",
              env_extra={"ENG_LANE": "exec", "ENG_ISSUE": "1"})[0]
    assert not ex.out.strip(), ex.out
    assert de("team", "off", cwd=d).rc == 0
    assert "## Team mode" not in (d / "CLAUDE.md").read_text()
    solo = fire("PreToolUse", d, {"tool_name": "Edit", "tool_input": {"file_path": str(part)}}, tool="Edit")[0]
    assert not solo.out.strip(), solo.out


def test_executor_cannot_end_its_turn_with_the_issue_still_open(tmp_path):
    """Found by the real e2e: the executor ended with a question in its final text, which reaches nobody."""
    d, r = init_project(tmp_path, "openend", "--team")
    assert de("issue", "new", "--title", "Bracket", "--body", "a bracket", "--no-start", cwd=d).rc == 0
    ex = {"ENG_LANE": "exec", "ENG_ISSUE": "1"}
    res = fire("Stop", d, {"stop_hook_active": False}, env_extra=ex)[0]
    out = json.loads(res.out.strip().splitlines()[-1])
    assert out.get("decision") == "block" and "issue ask" in out.get("reason", ""), res.out
    again = fire("Stop", d, {"stop_hook_active": True}, env_extra=ex)[0]
    assert '"block"' not in again.out, again.out          # never a loop
    assert de("issue", "ask", "which side?", cwd=d, env=dict(__import__("os").environ, **ex)).rc == 0
    waiting = fire("Stop", d, {"stop_hook_active": False}, env_extra=ex)[0]
    assert '"block"' not in waiting.out, waiting.out      # waiting for the user: it may stop
