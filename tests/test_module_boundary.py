"""Stage 3: the core never calls a module directly. Telegram (`tg …`), the platform's part search and shops (`parts`, `shop`),
the router's jobs gateway (`tg job`) and GitHub issues are modules of the hosted platform, off unless a platform connects them.
A rule text of the core may name a module command only in a paragraph that says it is the platform's (and so says what to do
without it); the local runtime (`de/`, `local/`) never runs one."""
import re
from pathlib import Path

AGENT = Path(__file__).resolve().parents[1]
PLATFORM_ONLY = {"platform.md", "roles", "reviews", "skills/group-chat", "skills/client-talk"}   # not in the public core
MODULE_CMD = re.compile(r"`(tg (link|say|file|dm-" r"owner|propose|react|job|app-[a-z]+)|parts (search|get)|shop (sites|search|find)?)\b")
PLATFORM_WORD = re.compile(r"(?i)platform|router|hosted")


def core_files(suffix):
    for p in sorted(AGENT.rglob(f"*{suffix}")):
        rel = p.relative_to(AGENT).as_posix()
        if any(rel == x or rel.startswith(x + "/") for x in PLATFORM_ONLY) or "/." in "/" + rel or rel.startswith("tests/"):
            continue
        if "/lib/" in rel or "/vendor/" in rel or "/node_modules/" in rel:
            continue
        yield rel, p


def test_core_rules_name_module_commands_only_as_the_platforms():
    bad = []
    for rel, p in core_files(".md"):
        for para in re.split(r"\n\s*\n", p.read_text(encoding="utf-8")):
            if MODULE_CMD.search(para) and not PLATFORM_WORD.search(para):
                bad.append(f"{rel}: {MODULE_CMD.search(para).group(0)} in: {para.strip()[:140]}")
    assert not bad, "a module command outside a platform paragraph:\n" + "\n".join(bad)


def test_the_local_runtime_runs_no_module_command():
    bad = []
    for sub in ("de", "local"):
        for p in (AGENT / sub).rglob("*"):
            if p.is_file() and p.suffix in (".py", "") and p.name != "hw_cli.py":
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if re.search(r"""["'](tg|parts|shop)["',\s]""", line) and "subprocess" in line:
                        bad.append(f"{p.relative_to(AGENT)}:{i}: {line.strip()}")
    assert not bad, "\n".join(bad)
