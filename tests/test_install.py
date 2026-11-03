"""Done-when 1: on a clean HOME, `install.sh` -> `de` works from the shell. Plus the repo-root marketplace Claude Code installs from.

The firmware tools (pio, Espressif's QEMU, simavr; Renode with --extras stm32, emscripten with --extras wasm) land in
$HOME/.local/bin and run from a shell whose PATH is only `$HOME/.local/bin:/usr/bin:/bin`: no Homebrew, no developer tools.
ONE heavy install (`fresh_install`, `--extras stm32,fea`) serves the de / firmware / renode / second-run tests; wasm (~1.5 GB)
has its own test, skipped unless DE_TEST_WASM=1."""
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import AGENT, REPO, clean_env, run

INSTALL = AGENT / "install.sh"   # the plugin folder holds the installer (the public repo's root)
FIRMWARE_TOOLS = ("pio", "qemu-system-xtensa", "qemu-system-riscv32", "simavr")
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"     # a fresh macOS/Linux user: git and curl, nothing of the developer's
FOREIGN = ("/opt/homebrew", "/usr/local/", "linuxbrew", "/nix/store")   # tool locations the user may not have
RENODE_SUPPORTED = sys.platform.startswith("linux") and platform.machine() in ("x86_64", "aarch64", "arm64") \
    or sys.platform == "darwin" and platform.machine() == "arm64"


def _uv_cache():
    """The real uv cache, so a clean HOME does not re-download build123d (~1 GB). DE_TEST_COLD_CACHE=1: a truly cold install."""
    if os.environ.get("DE_TEST_COLD_CACHE") == "1" or not shutil.which("uv"):
        return None
    return subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True).stdout.strip() or None


def clean_home_env(tmp_path):
    """A brand-new user: empty HOME, PATH with only the system's tools (no uv, no Homebrew, no ~/.local/bin)."""
    home, cfg = tmp_path / "home", tmp_path / "claude-config"
    home.mkdir(), cfg.mkdir()
    env = {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(cfg), "PATH": SYSTEM_PATH, "TERM": "dumb", "LANG": "en_US.UTF-8"}
    if cache := _uv_cache():
        env["UV_CACHE_DIR"] = cache
    return home, env


def user_shell(home, **extra):
    """A new shell of that user afterwards: $HOME/.local/bin first, then only the system."""
    return {"HOME": str(home), "PATH": f"{home / '.local' / 'bin'}:/usr/bin:/bin", "TERM": "dumb", "LANG": "en_US.UTF-8", **extra}


def tool(home, name, *args, timeout=300):
    """Run `name args` the way the user would: by name, from a shell with PATH=$HOME/.local/bin:/usr/bin:/bin, cwd elsewhere."""
    elsewhere = home.parent / "somewhere"
    elsewhere.mkdir(exist_ok=True)
    return run(["sh", "-c", " ".join([name, *args])], cwd=elsewhere, env=user_shell(home), timeout=timeout)


def assert_installed_by_us(home, name):
    """$HOME/.local/bin/<name> exists, runs, and does not lean on Homebrew or other machine-wide tools."""
    p = home / ".local" / "bin" / name
    assert p.exists(), f"no {p} after the install"
    assert os.access(p, os.X_OK), f"{p} is not executable"
    real = Path(os.path.realpath(p))
    assert str(real).startswith(os.path.realpath(home)), f"{p} resolves outside the user's HOME, to {real}"
    head = real.read_bytes()[:4]
    if head[:2] == b"#!":                                   # a wrapper / entry-point script
        text = real.read_text(errors="replace")
        bad = [f for f in FOREIGN if f in text]
        assert not bad, f"{real} refers to {bad}:\n{text[:1500]}"
    elif sys.platform == "darwin" and head in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe") and Path("/usr/bin/otool").exists():
        libs = subprocess.run(["/usr/bin/otool", "-L", str(real)], capture_output=True, text=True).stdout
        bad = [line.strip() for line in libs.splitlines()[1:] if any(f in line for f in FOREIGN)]
        assert not bad, f"{real} links libraries the user may not have: {bad}"


# ---------------------------------------------------------------- the one heavy install: --no-claude --extras stm32,fea
@pytest.fixture(scope="module")
def fresh_install(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fresh-install")
    home, env = clean_home_env(tmp)
    assert INSTALL.is_file(), f"no install.sh at the repo root ({INSTALL})"
    t0 = time.monotonic()
    r = run(["sh", INSTALL, "--no-claude", "--extras", "stm32,fea"], cwd=tmp, env=env, timeout=3600)
    return {"tmp": tmp, "home": home, "env": env, "result": r, "seconds": time.monotonic() - t0}


def need_install(fresh_install):
    assert fresh_install["result"].rc == 0, fresh_install["result"].why("sh install.sh --no-claude --extras stm32,fea (clean HOME, PATH=" + SYSTEM_PATH + ")")
    return fresh_install["home"]


@pytest.mark.slow
def test_install_on_clean_home_puts_a_working_de_on_the_path(fresh_install):
    home = need_install(fresh_install)
    de = home / ".local" / "bin" / "de"
    assert de.exists(), f"install.sh did not put de on {de}\n{fresh_install['result'].text[-2000:]}"
    assert os.access(de, os.X_OK), f"{de} is not executable"
    h = tool(home, "de", "--help")
    assert h.rc == 0, h.why("de --help after install")
    for cmd in ("init", "build", "check", "serve", "job"):
        assert cmd in h.text, f"`de --help` does not mention the `{cmd}` command:\n{h.text[-2000:]}"


@pytest.mark.slow
def test_install_on_clean_home_puts_working_firmware_tools_on_the_path(fresh_install):
    """Condition 1: pio, Espressif's qemu-system-xtensa / -riscv32 and simavr answer from PATH=$HOME/.local/bin:/usr/bin:/bin."""
    home = need_install(fresh_install)
    for name in FIRMWARE_TOOLS:
        assert_installed_by_us(home, name)

    r = tool(home, "pio", "--version")
    assert r.rc == 0 and "PlatformIO" in r.text, r.why("pio --version")

    for name in ("qemu-system-xtensa", "qemu-system-riscv32"):
        r = tool(home, name, "--version")
        assert r.rc == 0 and "QEMU" in r.text, r.why(f"{name} --version")
    r = tool(home, "qemu-system-xtensa", "-machine", "help")
    assert r.rc == 0 and re.search(r"^esp32\s", r.out, re.M), r.why("qemu-system-xtensa -machine help (Espressif's fork lists esp32)")
    r = tool(home, "qemu-system-riscv32", "-machine", "help")
    assert r.rc == 0 and re.search(r"^esp32c3\s", r.out, re.M), r.why("qemu-system-riscv32 -machine help (Espressif's fork lists esp32c3)")

    # simavr has no --version in most builds; --list-cores / --help print the AVR cores (and exit 1 upstream).
    tries = [tool(home, "simavr", a) for a in ("--version", "--list-cores", "--help")]
    ok = any(t.rc == 0 and "simavr" in t.text.lower() or t.rc in (0, 1) and re.search(r"atmega\d", t.text) for t in tries)
    assert ok, "simavr does not answer --version / --list-cores / --help:\n" + "\n".join(t.why("simavr") for t in tries)
    for t in tries:
        assert "Library not loaded" not in t.text and "error while loading shared libraries" not in t.text, t.why("simavr")


def wrapper_parts(p):
    """A wrapper the installer wrote -> (the binary it execs, {VAR: first path of the value})."""
    text = p.read_text()
    target = re.search(r"^exec '([^']+)'", text, re.M).group(1)
    env = {k: v for k, v in re.findall(r"^export (\w+)='([^']*)'", text, re.M)}
    return Path(target), env


@pytest.mark.slow
def test_qemu_runs_on_the_installed_libraries_alone(fresh_install, tmp_path):
    """Condition 1, without Homebrew: the QEMU builds name Homebrew paths (macOS) or distro libraries (Linux); the wrapper's
    library directory must hold them. macOS: a copy whose Homebrew install names point nowhere still runs under the wrapper's env."""
    home = need_install(fresh_install)
    for name in ("qemu-system-xtensa", "qemu-system-riscv32"):
        target, env = wrapper_parts(home / ".local" / "bin" / name)
        if sys.platform == "darwin":
            libdir = env["DYLD_FALLBACK_LIBRARY_PATH"]
            copy = tmp_path / name
            shutil.copy(target, copy)
            libs = subprocess.run(["otool", "-L", str(copy)], capture_output=True, text=True).stdout.splitlines()[1:]
            foreign = [line.split()[0] for line in libs if line.split()[0].startswith(("/opt/", "/usr/local/"))]
            for lib in foreign:
                subprocess.run(["install_name_tool", "-change", lib, f"/nonexistent/{Path(lib).name}", str(copy)], check=True,
                               capture_output=True)
            subprocess.run(["codesign", "-f", "-s", "-", str(copy)], check=True, capture_output=True)
            r = run([str(copy), "-machine", "help"], env=user_shell(home, DYLD_FALLBACK_LIBRARY_PATH=libdir), timeout=120)
        else:
            libdir = env["LD_LIBRARY_PATH"]
            missing = run(["ldd", str(target)], env=user_shell(home, LD_LIBRARY_PATH=libdir), timeout=60)
            assert "not found" not in missing.text, missing.why(f"ldd {target} with LD_LIBRARY_PATH={libdir}")
            r = run([str(target), "-machine", "help"], env=user_shell(home, LD_LIBRARY_PATH=libdir), timeout=120)
        assert r.rc == 0 and "esp32" in r.out, r.why(f"{name} on the installer's libraries only ({libdir})")


# ---------------------------------------------------------------- the examples and fw_publish on what the installer put there
# Opt-in: the first build of each platform downloads its toolchain (ESP32: ~5 GB on disk, minutes).
FW_EXAMPLES = os.environ.get("DE_TEST_FW_EXAMPLES") == "1"
EXAMPLES = AGENT / "skills" / "firmware-sim" / "examples"


def fw_project(home, tmp_path, example):
    proj = tmp_path / "proj"
    proj.mkdir()
    r = run(["sh", "-c", "de init"], cwd=proj, env=user_shell(home), timeout=600)
    assert r.rc == 0, r.why("de init")
    shutil.copytree(EXAMPLES / example, proj / "fw" / example)
    return proj


@pytest.mark.slow
@pytest.mark.skipif(not FW_EXAMPLES, reason="toolchain downloads (GBs); DE_TEST_FW_EXAMPLES=1 runs it")
@pytest.mark.parametrize("example", ["esp32_qemu", "avr_simavr", "stm32_renode"])
def test_examples_run_on_installed_tools(fresh_install, tmp_path, example):
    """Done-when 2: in a fresh `de init` project each firmware-sim example builds, runs on its simulator, prints its UART log."""
    if example == "stm32_renode" and not RENODE_SUPPORTED:
        pytest.skip("no Renode build for this machine")
    home = need_install(fresh_install)
    ex = fw_project(home, tmp_path, example) / "fw" / example
    r = run(["bash", "./run.sh"], cwd=ex, env=user_shell(home), timeout=3600)
    assert r.rc == 0 and "PASS: firmware reached DONE" in r.text, r.why(f"{example}/run.sh")
    assert "tick 39" in (ex / "uart.log").read_text(errors="replace"), f"{ex / 'uart.log'} has no UART output"


@pytest.mark.slow
@pytest.mark.skipif(not FW_EXAMPLES, reason="toolchain downloads (GBs); DE_TEST_FW_EXAMPLES=1 runs it")
def test_fw_publish_on_installed_pio(fresh_install, tmp_path):
    """Done-when 3: fw_publish.py -> manifest + binaries for an ESP32 sketch, with the installed PlatformIO."""
    home = need_install(fresh_install)
    proj = fw_project(home, tmp_path, "esp32_qemu")
    script = AGENT / "skills" / "firmware-sim" / "scripts" / "fw_publish.py"
    r = run(["python3", str(script), "fw/esp32_qemu", "--card", "demo", "--out", "build/fw_publish"], cwd=proj,
            env=user_shell(home), timeout=3600)
    assert r.rc == 0, r.why("fw_publish.py")
    out = proj / "build" / "fw_publish" / "demo"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["chip"] == "esp32" and [f["offset"] for f in manifest["files"]] == ["0x1000", "0x8000", "0xe000", "0x10000"], manifest
    for f in manifest["files"]:
        assert (out / f["url"]).stat().st_size > 0, f"{out / f['url']} is empty"


@pytest.mark.slow
def test_extras_stm32_puts_a_working_renode_on_the_path(fresh_install):
    """Condition 2: --extras stm32 -> renode in $HOME/.local/bin (Linux x86_64/aarch64, macOS arm64)."""
    if not RENODE_SUPPORTED:
        pytest.skip(f"Renode is not installed on {sys.platform}/{platform.machine()} (out of scope)")
    home = need_install(fresh_install)
    assert_installed_by_us(home, "renode")
    r = tool(home, "renode", "--version", timeout=600)
    assert r.rc == 0 and "renode" in r.text.lower(), r.why("renode --version")


@pytest.mark.slow
def test_tool_extras_mix_with_python_extras(fresh_install):
    """Condition 2: `--extras stm32,fea` -> stm32 is a tool (not a `uv sync --extra`), fea still installs its Python package."""
    home = need_install(fresh_install)
    assert "not defined" not in fresh_install["result"].text, fresh_install["result"].why("stm32 reached uv as a Python extra")
    venv = home / ".local" / "share" / "design-engineer" / "venv"
    assert list(venv.glob("lib/python3.12/site-packages/gmsh*.dist-info")), f"--extras stm32,fea did not install gmsh into {venv}"


@pytest.mark.slow
def test_second_install_on_the_same_home_downloads_nothing_again(fresh_install, tmp_path):
    """Condition 4: run the installer again -> exit 0, no archive fetched again (every curl call is logged), tools still answer."""
    home = need_install(fresh_install)
    shim, log = tmp_path / "shim", tmp_path / "curl-calls.log"
    shim.mkdir()
    (shim / "curl").write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexec /usr/bin/curl "$@"\n')
    (shim / "curl").chmod(0o755)
    env = dict(fresh_install["env"], PATH=f"{shim}:{home / '.local' / 'bin'}:/usr/bin:/bin")
    r = run(["sh", INSTALL, "--no-claude", "--extras", "stm32,fea"], cwd=fresh_install["tmp"], env=env, timeout=3600)
    assert r.rc == 0, r.why("second run of install.sh on the same HOME")
    calls = log.read_text().splitlines() if log.exists() else []
    archive = re.compile(r"releases/download|\.(tar\.gz|tgz|tar\.xz|tar\.bz2|zip|dmg|deb|rpm|pkg|AppImage|whl)\b", re.I)
    fetched = [c for c in calls if archive.search(c)]
    assert not fetched, f"the second run downloaded again:\n" + "\n".join(fetched)
    for name in ("pio", "qemu-system-xtensa", "qemu-system-riscv32"):
        t = tool(home, name, "--version")
        assert t.rc == 0, t.why(f"{name} --version after the second run")
    if RENODE_SUPPORTED:
        t = tool(home, "renode", "--version", timeout=600)
        assert t.rc == 0, t.why("renode --version after the second run")


# ---------------------------------------------------------------- wasm: emscripten 3.1.74 (~1.5 GB), opt-in
@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("DE_TEST_WASM") != "1", reason="emscripten is ~1.5 GB; DE_TEST_WASM=1 runs it")
def test_extras_wasm_puts_emscripten_on_the_path(tmp_path):
    home, env = clean_home_env(tmp_path)
    r = run(["sh", INSTALL, "--no-claude", "--extras", "wasm"], cwd=tmp_path, env=env, timeout=5400)
    assert r.rc == 0, r.why("sh install.sh --no-claude --extras wasm")
    assert "not defined" not in r.text, r.why("wasm reached uv as a Python extra")
    for name in ("em++", "emcc"):
        assert (home / ".local" / "bin" / name).exists(), f"no $HOME/.local/bin/{name}"
        t = tool(home, name, "--version", timeout=600)
        assert t.rc == 0 and "3.1.74" in t.text, t.why(f"{name} --version (emscripten 3.1.74)")


# ---------------------------------------------------------------- cheap installs: the shared venv, no firmware downloads
# (a tool whose ~/.local/bin slot holds the user's own file is not fetched)
def quick_install(tmp_path, *args, home=None):
    """install.sh with the developer's venv (DE_VENV) and PATH, like the Claude-step tests: only the installer's own logic runs."""
    home = home or tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = clean_env(HOME=str(home), CLAUDE_CONFIG_DIR=str(tmp_path / "cfg"),
                    DE_VENV=os.environ.get("DE_VENV") or str(Path.home() / ".local/share/design-engineer/venv"))
    return run(["sh", str(INSTALL), "--no-claude", *args], cwd=tmp_path, env=env, timeout=1800), home


def user_tool(home, name):
    """A tool of the user's own in $HOME/.local/bin, which the installer did not write."""
    b = home / ".local" / "bin"
    b.mkdir(parents=True, exist_ok=True)
    p = b / name
    p.write_bytes(f"#!/bin/sh\n# my own {name}, hands off\necho 'my {name} 0.0.1'\n".encode())
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p, p.read_bytes()


@pytest.mark.slow
def test_no_firmware_installs_none_of_the_firmware_tools(tmp_path):
    """Condition 3: --no-firmware -> de, but no pio / qemu / simavr / renode in $HOME/.local/bin."""
    r, home = quick_install(tmp_path, "--no-firmware")
    assert r.rc == 0, r.why("sh install.sh --no-claude --no-firmware")
    b = home / ".local" / "bin"
    assert (b / "de").exists(), f"no de in {b}"
    there = [n for n in (*FIRMWARE_TOOLS, "renode") if (b / n).exists() or (b / n).is_symlink()]
    assert not there, f"--no-firmware still put {there} into {b}"


@pytest.mark.slow
def test_stm32_extra_is_not_passed_to_uv(tmp_path):
    """Condition 2: `--extras stm32,render` -> exit 0 (uv would refuse an extra `stm32`); render is a real Python extra."""
    home = tmp_path / "home"
    renode, before = user_tool(home, "renode")              # whatever --no-firmware means for Renode, nothing is downloaded
    r, _ = quick_install(tmp_path, "--no-firmware", "--extras", "stm32,render", home=home)
    assert r.rc == 0, r.why("sh install.sh --no-claude --no-firmware --extras stm32,render")
    assert "not defined" not in r.text, r.why("stm32 reached uv as a Python extra")
    assert renode.read_bytes() == before, f"the user's own {renode} was changed"


@pytest.mark.slow
def test_install_leaves_the_users_own_tools_alone(tmp_path):
    """Condition 5: a $HOME/.local/bin/<tool> the installer did not write stays byte-identical; it says so; exit 0."""
    home = tmp_path / "home"
    mine = {n: user_tool(home, n) for n in FIRMWARE_TOOLS}
    r, _ = quick_install(tmp_path, home=home)
    assert r.rc == 0, r.why("sh install.sh --no-claude with the user's own firmware tools in ~/.local/bin")
    left = re.compile(r"left|leav|not (overwrit|touch|replac)|skip|kept|keep|not ours|yours|your own", re.I)
    for name, (p, before) in mine.items():
        assert p.exists() and not p.is_symlink(), f"{p} was removed or replaced by a link"
        assert p.read_bytes() == before, f"install.sh overwrote the user's own {p}"
        assert os.access(p, os.X_OK), f"{p} is no longer executable"
        lines = [line.replace(str(home), "~") for line in r.text.splitlines()]   # the tmp path itself holds "leav"
        said = [line for line in lines if name in line and left.search(line)]
        assert said, f"install.sh did not say it left the user's {name} alone:\n{r.text[-3000:]}"


def test_help_mentions_no_firmware_and_the_tool_extras():
    """Condition 6."""
    r = run(["sh", INSTALL, "--help"], timeout=60)
    assert r.rc == 0, r.why("sh install.sh --help")
    for word in ("--no-firmware", "stm32", "wasm"):
        assert word in r.text, f"`install.sh --help` does not mention {word}:\n{r.text}"


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
