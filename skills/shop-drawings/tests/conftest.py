"""Shared fixtures for the shop-drawings tests.

Run (pytest only needs the stdlib; the heavy work runs in the drawings venv via drawings_env.sh / kit_probe.py), from agent/:
    uv run --isolated --no-project --with pytest pytest skills/shop-drawings/tests -q

Each generator run is done once per session and shared by the tests that read it. The texts a drawing must carry are taken
from the scripts' own table (drawings_i18n.py): `ru(key)` is what a Russian project (the default) gets.
"""
import json
import os
import re
import shutil
import sys
import procgroup
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SKILL = TESTS.parent
SCRIPTS = Path(os.environ.get("SHOP_DRAW_SCRIPTS") or SKILL / "scripts")   # override: test another copy
ENV_SH = SCRIPTS / "drawings_env.sh"
VENV_PY = Path(os.environ.get("SHOP_DRAW_VENV", "/tmp/shopdrawvenv")) / "bin" / "python"

sys.path.insert(0, str(SCRIPTS))
import drawings_i18n as I  # noqa: E402

# the synthetic U wall of kit_probe.py (make cabinet_u): two 90 deg bends down, t 1, inner R 1, 24 round holes
CABINET = dict(material="DC01 zinc-coated steel", t=1, R=1, name="Cabinet - U wall")


def ru(key, **fields):
    return I.t(key, "ru", **fields)


def en(key, **fields):
    return I.t(key, "en", **fields)


def run_env(script, *args, timeout=900, cwd=TESTS):
    """Run a script of skills/shop-drawings/scripts through drawings_env.sh (creates the venv on first use). The scripts take
    the project's language from <cwd>/.engineer/locale.json: the default cwd has none, so the texts are the Russian default."""
    return procgroup.run(["bash", str(ENV_SH), script, *map(str, args)], capture_output=True, text=True, timeout=timeout, cwd=cwd)


def probe(*args):
    if not VENV_PY.exists():
        run_env("check_drawings.py", "--help")          # drawings_env.sh builds the venv
    r = procgroup.run([str(VENV_PY), str(TESTS / "kit_probe.py"), *map(str, args)], capture_output=True, text=True,
                       timeout=300, env={**os.environ, "MPLCONFIGDIR": os.environ.get("MPLCONFIGDIR", "/tmp/mpl")})
    assert r.returncode == 0, f"kit_probe {args[0]} failed:\n{r.stderr[-3000:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def check(kit, *opts):
    r = run_env("check_drawings.py", *opts, kit, timeout=600)
    return r.returncode, r.stdout + r.stderr


def flat(text):
    return " ".join(text.split())


def num(s):
    return float(s.replace(",", "."))


class Kit:
    """One generator run: its out dir, the single part's .pdf/.dxf, their parsed contents."""

    def __init__(self, out, run):
        self.out, self.run = Path(out), run
        pdfs = sorted(self.out.rglob("*.pdf"))
        dxfs = sorted(self.out.rglob("*.dxf"))
        assert run.returncode == 0, f"generator exit {run.returncode}:\n{(run.stdout + run.stderr)[-3000:]}"
        assert len(pdfs) == 1 and len(dxfs) == 1, f"one part -> one pdf + one dxf, got {pdfs} {dxfs}"
        self.pdf_path, self.dxf_path = pdfs[0], dxfs[0]
        self.pdf = probe("pdf", self.pdf_path)
        self.dxf = probe("dxf", self.dxf_path)
        self.pages = self.pdf["pages"]
        self.p1 = flat(self.pages[0]) if self.pages else ""
        self.p2 = flat(self.pages[1]) if len(self.pages) > 1 else ""


def _generate(out, *args, cwd=TESTS):
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    run = run_env("shop_drawing.py", *args, "--out", out, "--no-zip", cwd=cwd)
    return Kit(out, run)


@pytest.fixture(scope="session")
def synth_steps(tmp_path_factory):
    d = tmp_path_factory.mktemp("synth")
    for kind in ("small_bracket", "box", "disc", "perf", "up_flange", "cabinet_u"):
        probe("make", kind, d / f"{kind}.step")
    return d


def _cabinet_args(synth_steps):
    c = CABINET
    return ("--step", synth_steps / "cabinet_u.step", "--material", c["material"], "--t", c["t"], "--R", c["R"], "--name", c["name"])


@pytest.fixture(scope="session")
def cabinet(synth_steps, tmp_path_factory):
    return _generate(tmp_path_factory.mktemp("cabinet") / "kit", *_cabinet_args(synth_steps))


@pytest.fixture(scope="session")
def cabinet_en(synth_steps, tmp_path_factory):
    """The same part in a project whose .engineer/locale.json says English (the generator runs from the project root)."""
    proj = tmp_path_factory.mktemp("cabinet_en")
    (proj / ".engineer").mkdir()
    (proj / ".engineer" / "locale.json").write_text('{"lang": "en"}', encoding="utf-8")
    return _generate(proj / "kit", *_cabinet_args(synth_steps), cwd=proj)


@pytest.fixture(scope="session")
def small(synth_steps, tmp_path_factory):
    return _generate(tmp_path_factory.mktemp("small") / "kit", "--step", synth_steps / "small_bracket.step",
                     "--material", "S235JR steel", "--t", 2, "--R", 2, "--name", "Small bracket")


def _parts_kit(tmp_path_factory, tag, entry):
    d = tmp_path_factory.mktemp(tag)
    pj = d / "parts.json"
    pj.write_text(json.dumps([entry], ensure_ascii=False), encoding="utf-8")
    return _generate(d / "kit", "--parts", pj)


@pytest.fixture(scope="session")
def weld_box(synth_steps, tmp_path_factory):
    return _parts_kit(tmp_path_factory, "weld", {"name": "Welded tray", "step": str(synth_steps / "box.step"),
                                                 "material": "DC01 steel", "t": 1.5, "R": 1.5, "qty": 1, "weld": True})


@pytest.fixture(scope="session")
def plain_box(synth_steps, tmp_path_factory):
    return _parts_kit(tmp_path_factory, "plain", {"name": "Tray", "step": str(synth_steps / "box.step"),
                                                  "material": "DC01 steel", "t": 1.5, "R": 1.5, "qty": 1})


@pytest.fixture(scope="session")
def disc(synth_steps, tmp_path_factory):
    return _generate(tmp_path_factory.mktemp("disc") / "kit", "--step", synth_steps / "disc.step",
                     "--material", "S235JR steel", "--t", 2, "--name", "Disc")


@pytest.fixture(scope="session")
def perf(synth_steps, tmp_path_factory):
    return _generate(tmp_path_factory.mktemp("perf") / "kit", "--step", synth_steps / "perf.step",
                     "--material", "DC01 steel", "--t", 1.5, "--name", "Grille")


@pytest.fixture(scope="session")
def up_flange(synth_steps, tmp_path_factory):
    return _generate(tmp_path_factory.mktemp("upflange") / "kit", "--step", synth_steps / "up_flange.step",
                     "--material", "S235JR steel", "--t", 2, "--R", 2, "--name", "Shelf")


NUM = r"\d+(?:[,.]\d+)?"


def hole_rows(page2_text, lang="ru"):
    """Rows of the hole table: (index, X, Y, Ø) windows of four consecutive numbers after the X header."""
    i = page2_text.find(I.t("holes.cols", lang)[1])
    if i < 0:
        return []
    toks = page2_text[i:].split()
    isnum = [bool(re.fullmatch(NUM, t)) for t in toks]
    rows = []
    for k in range(len(toks) - 3):
        if all(isnum[k:k + 4]) and re.fullmatch(r"\d{1,3}", toks[k]):
            rows.append((int(toks[k]), num(toks[k + 1]), num(toks[k + 2]), num(toks[k + 3])))
    return rows


def pytest_collection_modifyitems(config, items):
    """Per-test limit for pytest-timeout (run with `--with pytest-timeout`; without the plugin no mark is added). Set here, not in
    pytest.ini, because a run over both test dirs finds no common ini. A hung test is killed with its child process groups (procgroup)."""
    if not config.pluginmanager.hasplugin("timeout"):
        return
    for it in items:
        if it.get_closest_marker("timeout") is None:
            it.add_marker(pytest.mark.timeout(1200))
