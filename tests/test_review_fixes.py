"""Reproducing tests for the stage-1 review: the local server refuses what another web page could send, the installer's
Claude Code step is real and loud, the note and syntax hooks, `de hw` locally."""
import json
import os
import stat
import time
from pathlib import Path

import pytest

from conftest import AGENT, clean_env, copy_project, de, http, inbox, init_project, need_built, need_server, run, serve, stop
from test_hooks import fire
from test_serve import jpeg_data_url

pytestmark = pytest.mark.slow
NOTE = {"comment": "move the hole 2 mm", "image": None, "view": {"parts": []}}


def note_body():
    return json.dumps(dict(NOTE, image=jpeg_data_url())).encode()


# ---------------------------------------------------------------- B1: nothing from another site reaches the agent
def test_cross_site_text_plain_note_is_refused(served):
    s = need_server(served)
    before = inbox(s["dir"])
    st, _, _ = http("POST", s["port"], f"/feedback/{s['slug']}", body=note_body(),
                    headers={"Content-Type": "text/plain", "Origin": "https://evil.example"})
    assert 400 <= st < 500, st
    assert inbox(s["dir"]) == before


def test_text_plain_note_is_refused_even_without_origin(served):
    s = need_server(served)
    before = inbox(s["dir"])
    st, _, _ = http("POST", s["port"], f"/feedback/{s['slug']}", body=note_body(), headers={"Content-Type": "text/plain"})
    assert 400 <= st < 500, st
    assert inbox(s["dir"]) == before


def test_json_note_from_a_foreign_origin_is_refused(served):
    s = need_server(served)
    before = inbox(s["dir"])
    st, _, _ = http("POST", s["port"], f"/feedback/{s['slug']}", body=note_body(),
                    headers={"Content-Type": "application/json", "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert 400 <= st < 500, st
    assert inbox(s["dir"]) == before


def test_foreign_host_is_refused(served):
    s = need_server(served)
    st, _, _ = http("GET", s["port"], "/_de/ping", headers={"Host": f"evil.example:{s['port']}"})
    assert st == 403, st


def test_hw_api_needs_the_token(served):
    s = need_server(served)
    st, _, _ = http("POST", s["port"], "/api/hw/cmd", body=b'{"op": "reset", "port": "0000:0000"}',
                    headers={"Content-Type": "application/json"})
    assert st == 401, st
    token = json.loads((s["dir"] / ".engineer" / "serve.json").read_text())["token"]
    st, _, _ = http("GET", s["port"], "/api/hw/status", headers={"Authorization": f"Bearer {token}"})
    assert st == 200, st


def test_the_pages_own_json_note_still_lands(served):
    s = need_server(served)
    before = inbox(s["dir"])
    origin = f"http://127.0.0.1:{s['port']}"
    st, _, _ = http("POST", s["port"], f"/feedback/{s['slug']}", body=note_body(),
                    headers={"Content-Type": "application/json", "Origin": origin, "Sec-Fetch-Site": "same-origin"})
    assert st == 200, st
    assert len(inbox(s["dir"]) - before) == 2


def test_negative_content_length_is_a_bad_request(served):
    s = need_server(served)
    st, _, _ = http("POST", s["port"], f"/feedback/{s['slug']}", body=b"", headers={"Content-Type": "application/json",
                                                                                  "Content-Length": "-1"}, timeout=10)
    assert 400 <= st < 500, st


# ---------------------------------------------------------------- M1: the installer's Claude Code step
STUB = """#!/bin/sh
echo "$@" >> "{log}"
case "$*" in {fail}) echo "Invalid API key · Please run /login" >&2; exit 1 ;; esac
case "$1 $2 $3" in "plugin marketplace list") echo "{listed}" ;; esac
exit 0
"""


def stub_claude(tmp_path, fail="__never__", listed=""):
    log = tmp_path / "claude-calls.log"
    p = tmp_path / "claude"
    p.write_text(STUB.format(log=log, fail=fail, listed=listed))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p, log


def install(tmp_path, claude):
    home = tmp_path / "home"
    home.mkdir()
    env = clean_env(HOME=str(home), CLAUDE_CONFIG_DIR=str(tmp_path / "cfg"), CLAUDE_BIN=str(claude),
                    DE_VENV=os.environ.get("DE_VENV") or str(Path.home() / ".local/share/design-engineer/venv"))
    # --no-firmware: these tests are about the Claude Code step; the firmware downloads are covered in test_install.py.
    return run(["sh", str(AGENT / "install.sh"), "--no-firmware"], cwd=tmp_path, env=env, timeout=1800), home


def test_install_registers_the_clean_clone_as_marketplace_and_installs(tmp_path):
    claude, log = stub_claude(tmp_path)
    r, home = install(tmp_path, claude)
    assert r.rc == 0, r.why("install.sh")
    calls = log.read_text()
    src = home / ".local/share/design-engineer/src"   # the clean clone; the plugin is its agent/ folder in the private repo
    assert f"plugin marketplace add {src / 'agent'}" in calls or f"plugin marketplace add {src}\n" in calls, calls
    assert "plugin install design-engineer@design-engineer" in calls, calls


def test_install_replaces_a_marketplace_added_from_elsewhere(tmp_path):
    claude, log = stub_claude(tmp_path, listed="design-engineer  Directory (/some/old/checkout/agent)")
    r, _ = install(tmp_path, claude)
    assert r.rc == 0, r.why("install.sh")
    calls = log.read_text().splitlines()
    assert any(c.startswith("plugin marketplace remove design-engineer") for c in calls), calls
    assert any(c.startswith("plugin marketplace add") for c in calls), calls


def test_install_says_what_failed_when_claude_refuses(tmp_path):
    claude, _ = stub_claude(tmp_path, fail="plugin install*")
    r, _ = install(tmp_path, claude)
    assert r.rc != 0
    assert "failed" in r.text and "Invalid API key" in r.text and "claude plugin install" in r.text, r.text


# ---------------------------------------------------------------- hooks: notes once, a part that does not parse
def test_user_prompt_hook_hands_a_note_over_once(built, tmp_path):
    d = copy_project(need_built(built), tmp_path / "notes")
    ib = d / ".engineer" / "inbox"
    ib.mkdir(parents=True, exist_ok=True)
    (ib / "20990101-000000-000000-note.json").write_text(json.dumps({"time": "t", "comment": "thicker wall here", "pictures": [],
                                                                     "parts": ["p1"]}))
    first = fire("UserPromptSubmit", d, {"prompt": "hi"})[0]
    assert first.rc == 0 and "thicker wall here" in first.out, first.why("user-prompt")
    second = fire("UserPromptSubmit", d, {"prompt": "hi"})[0]
    assert "thicker wall here" not in second.out, second.out


def test_post_tool_hook_blocks_a_part_that_does_not_parse(built, tmp_path):
    d = copy_project(need_built(built), tmp_path / "syntax")
    part = next((d / "mechanics/cad/parts").rglob("block.py"))
    part.write_text(part.read_text() + "\n\ndef oops(:\n    pass\n")
    r = fire("PostToolUse", d, {"tool_name": "Edit", "tool_input": {"file_path": str(part)}}, tool="Edit")[0]
    out = json.loads(r.out.strip().splitlines()[-1])
    assert out.get("decision") == "block" and "block.py" in out.get("reason", ""), r.out


# ---------------------------------------------------------------- de hw locally
def test_de_hw_without_a_server_says_how(built, tmp_path):
    d = copy_project(need_built(built), tmp_path / "hw-off")
    (d / ".engineer" / "serve.json").unlink(missing_ok=True)
    r = de("hw", "status", cwd=d, timeout=120)
    assert r.rc == 2 and "de serve" in r.text, r.why("de hw status")


def test_de_hw_status_through_the_local_server(built, tmp_path):
    d = copy_project(need_built(built), tmp_path / "hw-on")
    r, port, _ = serve(d)
    try:
        assert port, r.why("de serve")
        h = de("hw", "status", cwd=d, timeout=120)
        assert h.rc == 0 and "Viewer not open" in h.text, h.why("de hw status")
    finally:
        stop(d)


# ---------------------------------------------------------------- second review: /_viewer/ stays in the framework folder
def test_viewer_route_never_serves_a_file_outside_the_framework(served, tmp_path):
    s = need_server(served)
    probe = tmp_path / "probe.js"
    probe.write_text("secret = 1")
    for path in (f"/_viewer/{probe}", f"/_viewer//{probe}", f"/_viewer/vc/{probe}", f"/_viewer/vc//{probe}"):
        st, _, body = http("GET", s["port"], path)
        assert st == 404 and b"secret" not in (body or b""), (path, st)
    assert http("GET", s["port"], "/_viewer/index.html")[0] == 200
