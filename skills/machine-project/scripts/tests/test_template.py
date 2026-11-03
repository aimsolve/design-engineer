"""The scaffolded build123d project (templates/) builds with --model and exports through derive.py."""
import subprocess
import procgroup
import sys

import helpers as H


def test_scaffolded_project_builds_and_derives(tmp_path):
    root = tmp_path / "proj"
    cp = procgroup.run([sys.executable, str(H.SCRIPTS / "scaffold.py"), str(root), "--name", "m", "--cad", "build123d"],
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    H.run_model(root).ok()
    parts = H.by_id(root)
    assert parts["Example_part"]["process"] == "print" and parts["Example_part"]["material"] == "PETG"
    d = H.run_derive(root, "print").ok()
    assert [e["id"] for e in d.manifest["parts"]] == ["Example_part"]
    assert H.run_derive(root, "step").ok().manifest["model_step"]
