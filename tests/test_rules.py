"""The agent's rules in two modes (stage 1): core PRINCIPLES.md works in plain Claude Code, platform-only rules in platform.md,
local rules in local.md; the platform image still gets core + platform (done-when 5)."""
import re

import pytest

from conftest import AGENT, REPO

TG_CMD = re.compile(r"`?\btg (link|say|dm-" r"owner|send|ask|file)\b")


def test_core_principles_do_not_depend_on_telegram():
    text = (AGENT / "PRINCIPLES.md").read_text()
    hits = [ln.strip() for ln in text.splitlines() if TG_CMD.search(ln)]
    assert not hits, "PRINCIPLES.md (loaded in every mode) tells the agent to use Telegram commands:\n" + "\n".join(hits[:8])


PRIVATE = pytest.mark.skipif(not (AGENT / "platform.md").is_file() or not (REPO / "project" / "Dockerfile").is_file(),
                             reason="the hosted platform's files are not part of the public repo")


@PRIVATE
def test_platform_rules_hold_the_telegram_and_router_parts():
    p = AGENT / "platform.md"
    assert p.is_file(), f"no {p}"
    text = p.read_text()
    assert "tg link" in text, "platform.md lost the `tg link` rule (how a file reaches the client on the platform)"
    assert "job run" in text


def test_local_rules_say_how_files_answers_and_builds_work_here():
    p = AGENT / "local.md"
    assert p.is_file(), f"no {p}"
    text = p.read_text()
    assert "127.0.0.1" in text, "local.md does not say a file / the model is shown as a http://127.0.0.1 link"
    assert "de build" in text and "de check" in text, "local.md does not tell the agent to run de build / de check"


@PRIVATE
def test_platform_image_gets_core_plus_platform_rules():
    df = (REPO / "project" / "Dockerfile").read_text()
    instrs = re.sub(r"\\\n", " ", df).splitlines()          # one line per Dockerfile instruction
    target = [ln for ln in instrs if "/etc/claude-code/CLAUDE.md" in ln and not ln.lstrip().startswith("#")]
    assert target, "project/Dockerfile no longer writes /etc/claude-code/CLAUDE.md"
    assert any("PRINCIPLES.md" in ln and "platform.md" in ln for ln in target), \
        "project/Dockerfile builds /etc/claude-code/CLAUDE.md without platform.md:\n" + "\n".join(target)
