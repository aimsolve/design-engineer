"""Done-when 2 (first step) and 5: `de init` in an empty folder makes a machine project that works locally and on the platform.
No model build here: these run in seconds."""
import json
import re
import subprocess

import pytest

from conftest import AGENT, de, init_project, need_init, part_files

SKELETON = ["mechanics/cad/params.py", "mechanics/cad/assemble.py", "kit.json", "sim/site.sh", "wiki/index.md", ".engineer/mode",
            "CLAUDE.md", ".gitignore"]
BARE_OPT = re.compile(r"/opt/design-engineer")
FALLBACK = "${DE_ROOT:-/opt/design-engineer}"


def bare_platform_paths(text):
    """Lines that name /opt/design-engineer other than as the ${DE_ROOT:-...} fallback (they only work on the platform)."""
    return [ln for ln in text.splitlines() if BARE_OPT.search(ln.replace(FALLBACK, ""))]


def is_git_repo(d):
    return subprocess.run(["git", "-C", str(d), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True).stdout.strip() == "true"


@pytest.fixture
def fresh_init(tmp_path):
    return init_project(tmp_path, "nema-mount")


def test_init_creates_the_machine_project_skeleton(fresh_init):
    fresh = need_init(fresh_init)
    missing = [f for f in SKELETON if not (fresh / f).is_file()]
    assert not missing, f"de init did not create: {missing}"
    assert part_files(fresh), "no part file under mechanics/cad/parts/"
    assert (fresh / ".engineer" / "mode").read_text().strip() == "local"
    assert is_git_repo(fresh), "the project folder is not a git repo after de init"
    assert (fresh / "CLAUDE.md").read_text().strip(), "the project CLAUDE.md is empty"


def test_init_name_defaults_to_the_folder_name(fresh_init):
    fresh = need_init(fresh_init)
    aas = sorted(p.name for p in (fresh / "aas").glob("*.aas.json"))
    assert aas == ["nema-mount.aas.json"], f"aas/ holds {aas}"


def test_init_with_name_and_title(tmp_path):
    d, r = init_project(tmp_path, "folder-x", "--name", "bracket", "--title", "Wspornik NEMA 17 (żółty)")
    assert r.rc == 0, r.why("de init --name --title")
    assert [p.name for p in (d / "aas").glob("*.aas.json")] == ["bracket.aas.json"]
    assert "Wspornik NEMA 17 (żółty)" in (d / "aas" / "bracket.aas.json").read_text(encoding="utf-8")


def test_init_in_the_current_directory_without_an_argument(tmp_path):
    d = tmp_path / "here-proj"
    d.mkdir()
    r = de("init", cwd=d, timeout=300)
    assert r.rc == 0, r.why("de init (no DIR, cwd = the folder)")
    assert (d / "mechanics" / "cad" / "assemble.py").is_file()
    assert [p.name for p in (d / "aas").glob("*.aas.json")] == ["here-proj.aas.json"]


def test_second_init_keeps_what_the_user_changed(fresh_init):
    fresh = need_init(fresh_init)
    params, claude, part = fresh / "mechanics/cad/params.py", fresh / "CLAUDE.md", part_files(fresh)[0]
    for f in (params, claude, part):
        f.write_text(f.read_text() + "\n# changed by the user\n")
    before = {f: f.read_text() for f in (params, claude, part)}
    r = de("init", fresh, cwd=fresh, timeout=300)
    assert r.rc == 0, r.why("second de init")
    changed = [str(f.relative_to(fresh)) for f, t in before.items() if f.read_text() != t]
    assert not changed, f"the second de init overwrote {changed}"
    assert (fresh / ".engineer" / "mode").read_text().strip() == "local"


def test_init_in_an_existing_git_repo_keeps_its_history(tmp_path):
    d = tmp_path / "old-repo"
    d.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, check=True).stdout.strip()
    g("init", "-q")
    (d / "notes.txt").write_text("mine\n")
    g("add", "notes.txt")
    g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "first")
    head = g("rev-parse", "HEAD")
    r = de("init", d, cwd=d, timeout=300)
    assert r.rc == 0, r.why("de init in an existing repo")
    assert g("rev-parse", "HEAD") == head, "de init rewrote the existing repo's history"
    assert (d / "notes.txt").read_text() == "mine\n"
    assert (d / "kit.json").is_file()


def test_generated_kit_and_site_work_off_the_platform(fresh_init):
    fresh = need_init(fresh_init)
    """kit.json + sim/site.sh: /opt/design-engineer only as the ${DE_ROOT:-...} fallback (done-when 5: the platform keeps working)."""
    for f in ("kit.json", "sim/site.sh"):
        text = (fresh / f).read_text()
        assert not bare_platform_paths(text), f"{f} has platform-only paths:\n" + "\n".join(bare_platform_paths(text))
    json.loads((fresh / "kit.json").read_text())
    assert FALLBACK in (fresh / "sim" / "site.sh").read_text(), "sim/site.sh does not fall back to /opt/design-engineer when DE_ROOT is unset"


def test_framework_templates_fall_back_to_the_platform_path():
    """The templates every project (platform or local) is made from: same rule as the generated files."""
    tpl = AGENT / "skills" / "machine-project" / "templates"
    for f in ("kit.json", "site.sh"):
        text = (tpl / f).read_text()
        assert not bare_platform_paths(text), f"templates/{f} has platform-only paths:\n" + "\n".join(bare_platform_paths(text))
    assert FALLBACK in (tpl / "site.sh").read_text()


def test_init_writes_the_parts_texts_in_the_projects_language(tmp_path):
    """Review M3: the scaffold's fastener names follow `de init --lang` (default en), not the platform's Russian."""
    cyr = re.compile("[\u0400-\u04FF]")
    d, r = init_project(tmp_path, "lang-en")
    assert r.rc == 0, r.why("de init")
    fast = sorted((d / "mechanics" / "cad" / "parts" / "fasteners").glob("*.py"))
    assert fast, "no fastener templates"
    assert not [p.name for p in fast if cyr.search(p.read_text(encoding="utf-8"))], "Russian words in an English project"
    d, r = init_project(tmp_path, "lang-ru", "--lang", "ru")
    assert r.rc == 0, r.why("de init --lang ru")
    assert any(cyr.search(p.read_text(encoding="utf-8")) for p in (d / "mechanics/cad/parts/fasteners").glob("*.py"))
