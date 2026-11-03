"""Done-when 2: in an empty folder `de init` -> `de build` -> `de check` reports PASS. And a broken model is reported, not hidden."""
import json
import shutil

import pytest

from conftest import copy_project, de, init_project, need_built, part_files, parts_json

pytestmark = pytest.mark.slow


def test_build_makes_the_model_and_the_viewer_site(built):
    built_ok = need_built(built)
    d = built_ok
    parts = parts_json(d)
    assert parts, "build/model/parts.json lists no parts"
    assert all(p.get("id") for p in parts), parts
    cfg = json.loads((d / "public" / "config.json").read_text())
    glb = d / "public" / "model.glb"
    assert glb.is_file() and glb.read_bytes()[:4] == b"glTF", "public/model.glb is missing or not a GLB"
    assert cfg.get("feedback"), f"public/config.json has no `feedback`: the page would have no «draw a note» ({cfg})"


def test_check_passes_on_a_fresh_project(built):
    built_ok = need_built(built)
    r = de("check", built_ok, cwd=built_ok, timeout=900)
    assert r.rc == 0, r.why("de check on a fresh init + build")
    assert "PASS:" in r.out, r.why("de check printed no PASS: summary")


def test_check_fails_when_the_project_breaks_the_standard(built, tmp_path):
    built_ok = need_built(built)
    d = copy_project(built_ok, tmp_path / "nema-mount")
    for f in (d / "aas").glob("*.aas.json"):          # the machine model (AAS) is required by the standard
        f.unlink()
    r = de("check", d, cwd=d, timeout=900)
    assert r.rc != 0, r.why("de check without the AAS must fail")
    assert "FAIL" in r.text, r.why("de check did not name a FAIL")


def test_build_of_a_broken_part_fails_and_names_the_file(tmp_path):
    d, ri = init_project(tmp_path, "broken-part")
    assert ri.rc == 0, ri.why("de init")
    part = part_files(d)[0]
    part.write_text(part.read_text() + "\n\ndef oops(:\n    pass\n")       # a Python syntax error
    r = de("build", d, cwd=d, timeout=900)
    assert r.rc != 0, r.why("de build of a part with a syntax error must fail")
    assert part.name in r.text, r.why(f"de build did not name the broken file {part.name}")
    assert not (d / "public" / "model.glb").exists(), "a failed build still published a viewer model"
