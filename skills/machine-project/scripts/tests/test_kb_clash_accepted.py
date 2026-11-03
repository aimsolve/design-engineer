"""Issue #89 round 2, done-when 1: the KB lint (`check.py <project> --kb`) does not report the client's confirmed clash
pairs, wiki/client/clash_accepted.json, as «raw material inside wiki/»; any other raw file inside wiki/ still is.

Black box: runs check.py --kb on a small project and reads its report. No build123d needed.
  cd agent/skills/machine-project/scripts/tests && python3 -m pytest -q test_kb_clash_accepted.py
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

CHECK = Path(__file__).resolve().parent.parent / "check.py"
RAW_RULE = "raw material inside wiki/"
ACCEPTED = "wiki/client/clash_accepted.json"


def project(tmp_path, files):
    root = tmp_path / "proj"
    (root / "wiki").mkdir(parents=True)
    (root / "wiki" / "overview.md").write_text("---\ntitle: Overview\n---\n# Overview\n\nThe machine.\n")
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return root


def kb(root, tmp_path):
    r = subprocess.run([sys.executable, str(CHECK), str(root), "--kb", "--clients", str(tmp_path / "no-clients")],
                       capture_output=True, text=True, timeout=120)
    assert "Traceback" not in r.stderr, r.stderr
    return r


def raw_listed(out):
    """Paths listed under the «raw material inside wiki/» FAIL line (indented lines that follow it)."""
    lines = out.splitlines()
    got = []
    for i, ln in enumerate(lines):
        if RAW_RULE in ln:
            for nxt in lines[i + 1:]:
                if not nxt.startswith(" "):
                    break
                got.append(nxt.strip())
    return got


ACCEPTED_JSON = json.dumps([{"a": "rod_end", "b": "pin", "why": "meant so, the client confirmed"}], ensure_ascii=False)


def test_clash_accepted_alone_is_not_raw_material_and_kb_passes(tmp_path):
    root = project(tmp_path, {ACCEPTED: ACCEPTED_JSON})
    r = kb(root, tmp_path)
    assert RAW_RULE not in r.stdout, r.stdout
    assert ACCEPTED not in raw_listed(r.stdout), r.stdout
    assert r.returncode == 0, f"nothing else wrong in this wiki, --kb passes:\n{r.stdout}"


def test_clash_accepted_next_to_other_raw_files_only_the_others_are_reported(tmp_path):
    root = project(tmp_path, {ACCEPTED: ACCEPTED_JSON, "wiki/client/foo.json": "{}", "wiki/x.pdf": "%PDF-1.4"})
    r = kb(root, tmp_path)
    listed = raw_listed(r.stdout)
    assert "wiki/client/foo.json" in listed, r.stdout
    assert "wiki/x.pdf" in listed, r.stdout
    assert ACCEPTED not in listed, r.stdout
    assert r.returncode == 1, "the other raw files still fail --kb"


@pytest.mark.parametrize("rel", ["wiki/clash_accepted.json", "wiki/client/clash_accepted.csv",
                                 "wiki/client/sub/clash_accepted.json"])
def test_only_that_exact_path_is_exempt(tmp_path, rel):
    root = project(tmp_path, {rel: "[]"})
    r = kb(root, tmp_path)
    assert rel in raw_listed(r.stdout), f"{rel} is still raw material:\n{r.stdout}"
    assert r.returncode == 1
