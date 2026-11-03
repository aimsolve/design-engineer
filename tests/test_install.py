"""Done-when 1: on a clean HOME, `install.sh` -> `de` works from the shell. Plus the repo-root marketplace Claude Code installs from."""
import json
import os
import shutil
import subprocess

import pytest

from conftest import AGENT, REPO, run

INSTALL = AGENT / "install.sh"   # the plugin folder holds the installer (the public repo's root)


def _uv_cache():
    """The real uv cache, so a clean HOME does not re-download build123d (~1 GB). DE_TEST_COLD_CACHE=1: a truly cold install."""
    if os.environ.get("DE_TEST_COLD_CACHE") == "1" or not shutil.which("uv"):
        return None
    return subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True).stdout.strip() or None


@pytest.mark.slow
def test_install_on_clean_home_puts_a_working_de_on_the_path(tmp_path):
    home, cfg = tmp_path / "home", tmp_path / "claude-config"
    home.mkdir(), cfg.mkdir()
    env = {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(cfg), "PATH": os.environ["PATH"], "TERM": "dumb", "LANG": "en_US.UTF-8"}
    if cache := _uv_cache():
        env["UV_CACHE_DIR"] = cache
    assert INSTALL.is_file(), f"no install.sh at the repo root ({INSTALL})"
    r = run(["sh", INSTALL, "--no-claude"], cwd=tmp_path, env=env, timeout=1800)
    assert r.rc == 0, r.why("sh install.sh --no-claude")

    de = home / ".local" / "bin" / "de"
    assert de.exists(), f"install.sh did not put de on {de}\n{r.text[-2000:]}"
    assert os.access(de, os.X_OK), f"{de} is not executable"

    # A new shell of that user: $HOME/.local/bin on PATH, started somewhere else.
    elsewhere = tmp_path / "somewhere"
    elsewhere.mkdir()
    env2 = dict(env, PATH=f"{home / '.local' / 'bin'}:{os.environ['PATH']}")
    h = run(["sh", "-c", "de --help"], cwd=elsewhere, env=env2, timeout=300)
    assert h.rc == 0, h.why("de --help after install")
    for cmd in ("init", "build", "check", "serve", "job"):
        assert cmd in h.text, f"`de --help` does not mention the `{cmd}` command:\n{h.text[-2000:]}"


def test_the_plugin_folder_is_a_marketplace_of_itself():
    """`claude plugin marketplace add <plugin folder>` (install.sh, and the public repo's root on GitHub)."""
    mp = json.loads((AGENT / ".claude-plugin" / "marketplace.json").read_text())
    by_name = {p["name"]: p for p in mp.get("plugins", [])}
    assert by_name.get("design-engineer", {}).get("source") == "./", mp
    assert (AGENT / ".claude-plugin" / "plugin.json").is_file()
    outer = REPO / ".claude-plugin" / "marketplace.json"   # the private repo's root lists the plugin folder too
    if outer.is_file():
        plugins = {p["name"]: p for p in json.loads(outer.read_text()).get("plugins", [])}
        assert plugins.get("design-engineer", {}).get("source") == "./agent", plugins
