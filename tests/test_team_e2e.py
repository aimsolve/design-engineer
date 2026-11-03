"""Done-when 5 (opt-in, DE_E2E_CLAUDE=1, up to 30 min, real subscription): in a fresh `de init --team` folder the manager,
plain `claude -p` with the plugin, asked for a NEMA 17 bracket on a 2020 profile, opens an issue; an executor (a background
Claude Code session) picks it up and hands it over (state review); the result builds, checks and has a bracket part.
Uses the `claude` on PATH for the manager and (no $DE_CLAUDE) for the executors."""
import json
import os
import re
import shutil
import time
from pathlib import Path

import pytest

from conftest import AGENT, clean_env, de, init_project, parts_json, run

pytestmark = [pytest.mark.slow, pytest.mark.e2e, pytest.mark.timeout(30 * 60),
              pytest.mark.skipif(os.environ.get("DE_E2E_CLAUDE") != "1", reason="opt-in: DE_E2E_CLAUDE=1 runs real Claude Code (~30 min)")]
PROMPT = "design a NEMA 17 motor bracket for a 2020 aluminium profile"
BRACKET = re.compile(r"bracket", re.I)
BUDGET = 30 * 60 - 120          # leave time for build + check inside the 30-minute timeout


def issues(d: Path):
    out = {}
    for f in sorted((d / ".engineer" / "issues").glob("*.json")) if (d / ".engineer" / "issues").is_dir() else []:
        try:
            out[int(f.stem)] = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass                 # being written right now
    return out


def test_manager_opens_an_issue_an_executor_hands_over_and_the_bracket_builds(tmp_path):
    t0 = time.time()
    claude = shutil.which("claude")
    assert claude, "no `claude` on PATH"
    d, ri = init_project(tmp_path, "nema-team", "--team")
    assert ri.rc == 0, ri.why("de init --team")
    env = {k: v for k, v in clean_env().items() if k not in ("ENG_LANE", "ENG_ISSUE", "DE_CLAUDE")}
    try:
        r = run([claude, "-p", "--plugin-dir", AGENT, "--dangerously-skip-permissions", PROMPT], cwd=d, env=env, timeout=BUDGET - 300)
        (tmp_path / "manager.log").write_text(r.text)
        assert r.rc == 0, r.why("the manager's claude -p")
        opened = issues(d)
        assert opened, f"the manager opened no issue (.engineer/issues/ is empty); manager log: {tmp_path / 'manager.log'}"

        bg = [n for n in opened if (json.loads((d / ".engineer" / "issues" / f"{n}.json").read_text()).get("executor") or {}).get("kind") == "bg"]
        if bg:   # a background session (the folder is trusted and this Claude Code has --bg): the user can see and attach it
            ag = run([claude, "agents", "--json"], env=env, cwd=d, timeout=60)   # plain `claude agents` needs a terminal
            assert any(re.search(rf"#{n}\b", ag.out) for n in bg), ag.why("`claude agents --json` lists no executor session #N")

        handed = None
        while time.time() - t0 < BUDGET:
            now = issues(d)
            handed = next((n for n, i in now.items() if i.get("state") == "review"), None)
            if handed:
                break
            time.sleep(15)
        assert handed, f"no issue reached state review in time: {[(n, i.get('state')) for n, i in issues(d).items()]}"
        one = issues(d)[handed]
        recorded = any(re.search(r"exec|session|pid", k, re.I) and one[k] for k in one) or \
            re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", json.dumps(one))
        assert recorded, f"no executor (session id or pid) recorded on #{handed}: {one}"

        rb = de("build", d, cwd=d, timeout=1200)
        assert rb.rc == 0, rb.why("de build after the hand-over")
        rc = de("check", d, cwd=d, timeout=900)
        assert rc.rc == 0, rc.why("de check after the hand-over")
        parts = parts_json(d)
        assert any(BRACKET.search(f"{p.get('id', '')} {p.get('name', '')}") for p in parts), \
            f"no part named as a bracket: {[(p.get('id'), p.get('name')) for p in parts]}"
    finally:
        for n in issues(d):
            de("exec", "stop", str(n), cwd=d, timeout=60)
