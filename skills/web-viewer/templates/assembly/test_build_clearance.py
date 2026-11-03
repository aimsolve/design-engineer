"""Every machine's site build ends with build.py, and build.py runs the overlap check of the code model.

When the cwd (the project root, as the site build runs it) holds a code model (build/model/parts.json + nodes.json), build.py
runs machine-project's clearance.run with the --motion cycle and the motion_group of every part of the import dir's parts.json;
a clash never stops the build (a design decision): the site gets clearance.json (named in config.json) for the page's red mark,
and the output names both parts. Without a code model in the cwd, or with a client's STEP model (model_from_steps.py, func
"steps"), it behaves as before (test_shared_viewer_build.py runs without one and stays green).

The slider's group comes ONLY from the import dir's parts.json `motion_group` (not from build/model/parts.json, not from the
cycle's node_groups), so the clash below is found only if build.py hands the viewer's groups over.

    python -m pytest agent/skills/web-viewer/templates/assembly/test_build_clearance.py -q   (needs build123d 0.13.0)

World mm, boxes at their placements:
    base    root     400 x 100 x 10   z[-10,0]
    slider  -> base  10^3 at x[300,310] y[60,70] z[0,10], motion_group `slide`: +X 60 mm over t 0..1 s
    post    -> base  6 x 20 x 20 at x[328,334] y[55,75] z[0,20]: the slider passes THROUGH it mid-travel (t 0.3..0.57 s),
                     clear at t = 0 and at the end, so only a check over the cycle sees it
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build.py"

CYCLE = {"cycle": {"period_s": 1.5, "loop": True,
                   "groups": {"slide": {"kind": "linear", "axis": "x", "travel_mm": 60.0, "segments": [[0, 1, 0, 1]]}}}}
# name -> (box size, world translation, docked to, motion_group in the IMPORT dir's parts.json)
MODEL = {"base": ((400, 100, 10), (0, 0, -10), None, None),
         "slider": ((10, 10, 10), (300, 60, 0), "base", "slide"),
         "post": ((6, 20, 20), (328, 55, 0), "base", None)}


def T(x, y, z):
    return [1, 0, 0, x, 0, 1, 0, y, 0, 0, 1, z, 0, 0, 0, 1]


def write_code_model(root: Path, names):
    """build/model/parts.json + nodes.json + one own-frame STEP per part (what cad_incremental.py --model leaves)."""
    from build123d import Align, Box, export_step
    d = root / "build/model/step"
    d.mkdir(parents=True)
    parts = []
    for name in names:
        size, at, to, _ = MODEL[name]
        tmp = d / f"_tmp_{name}.step"
        export_step(Box(*size, align=(Align.MIN, Align.MIN, Align.MIN)), str(tmp))
        key = hashlib.sha256(tmp.read_bytes() + name.encode()).hexdigest()
        tmp.replace(d / f"{key}.step")
        pl = {"name": name, "node": "m", "loc": T(*at)}
        if to:
            pl["joint"] = {"to": to, "at": "top", "by": "bottom", "type": "rigid"}
        parts.append({"id": name, "name": name, "node": "m", "func": "m.build", "process": "mill", "color": "#999999",
                      "key": key, "qty": 1, "step": f"build/model/step/{key}.step", "placements": [pl],
                      **({"joints": {"top": {"type": "rigid", "loc": T(0, 0, 10)}}} if name == "base" else {})})
    (root / "build/model/parts.json").write_text(json.dumps({"parts": parts}, indent=1))
    (root / "build/model/nodes.json").write_text("{}")


def make_input(root: Path, names) -> Path:
    """The importer output dir, as test_shared_viewer_build.make_input writes it, with one catalogue entry per placement."""
    src = root / "in"
    (src / "parts").mkdir(parents=True)
    (src / "model.glb").write_bytes(b"glTF-fake")
    cat = []
    for name in names:
        e = {"id": name, "name": name, "kind": "made", "material": "steel", "instances": 1, "volume_mm3": 1000.0,
             "files": {"step": f"parts/{name}.step"}}
        if MODEL[name][3]:
            e["motion_group"] = MODEL[name][3]
        cat.append(e)
        (src / "parts" / f"{name}.step").write_text("ISO-10303-21;")
    (src / "parts.json").write_text(json.dumps({"assembly": {"root": "Clearance"}, "parts": cat}, ensure_ascii=False))
    return src


class BuildRunsClearance(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="build_clearance_"))
        self.root = self.tmp / "proj"
        self.root.mkdir()
        (self.tmp / "cycle.json").write_text(json.dumps(CYCLE))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, names):
        write_code_model(self.root, names)
        src = make_input(self.tmp, names)
        out = self.tmp / "site"
        r = subprocess.run([sys.executable, str(BUILD), "--in", str(src), "--out", str(out), "--motion", str(self.tmp / "cycle.json"),
                            "--flash", "none", "--kit", "none", "--feedback", "none"],
                           cwd=str(self.root), capture_output=True, text=True, timeout=600)
        return r, out

    def test_a_clash_in_the_cycle_is_published_with_the_site_not_stopping_it(self):
        # the check never blocks publishing: the page shows a red mark from <out>/clearance.json and the
        # platform tells the agent. (Written first as "the build fails"; changed with that decision.)
        r, out = self.build(["base", "slider", "post"])
        text = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, f"a clash does not stop the build\n{text[-3000:]}")
        conf = json.loads((out / "config.json").read_text())
        self.assertEqual(conf.get("clearance"), "clearance.json", f"config.json names the clash report: {conf}")
        clashes = json.loads((out / "clearance.json").read_text())["clashes"]
        self.assertTrue(any({c["a"], c["b"]} == {"slider", "post"} and c["t_s"] for c in clashes),
                        f"the site's clearance.json carries slider x post with its time: {clashes}")
        self.assertTrue(any("slider" in ln and "post" in ln for ln in text.splitlines()),
                        f"the output must carry the clash line naming slider and post:\n{text[-3000:]}")

    def test_a_check_that_crashes_still_publishes_the_site_and_says_so(self):
        # the check never blocks publishing, not even when it breaks: a STEP OCC cannot read makes clearance.run raise
        write_code_model(self.root, ["base", "slider", "post"])
        step = sorted((self.root / "build/model/step").glob("*.step"))[0]
        step.write_text("not a STEP file")
        src, out = make_input(self.tmp, ["base", "slider", "post"]), self.tmp / "site"
        r = subprocess.run([sys.executable, str(BUILD), "--in", str(src), "--out", str(out), "--motion", str(self.tmp / "cycle.json"),
                            "--flash", "none", "--kit", "none", "--feedback", "none"],
                           cwd=str(self.root), capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((out / "config.json").is_file())
        self.assertIn("clearance check failed", r.stdout + r.stderr)
        self.assertTrue(json.loads((out / "clearance.json").read_text()).get("error"), "the site says the check did not run")

    def test_a_client_step_model_is_not_checked(self):
        # model_from_steps.py writes the same build/model files for a client's CAD, every part `func` "steps": its overlaps
        # are the client's data
        write_code_model(self.root, ["base", "slider", "post"])
        f = self.root / "build/model/parts.json"
        m = json.loads(f.read_text())
        for p in m["parts"]:
            p["func"] = "steps"
        f.write_text(json.dumps(m))
        src, out = make_input(self.tmp, ["base", "slider", "post"]), self.tmp / "site"
        r = subprocess.run([sys.executable, str(BUILD), "--in", str(src), "--out", str(out), "--motion", str(self.tmp / "cycle.json"),
                            "--flash", "none", "--kit", "none", "--feedback", "none"],
                           cwd=str(self.root), capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("clearance", json.loads((out / "config.json").read_text()))
        self.assertFalse((self.root / "build/clearance").exists(), "a client STEP model is not checked at all")

    def test_a_clean_code_model_is_checked_and_the_site_is_built(self):
        r, out = self.build(["base", "slider"])
        text = r.stdout + r.stderr
        self.assertEqual(r.returncode, 0, f"no clash: the build goes on\n{text[-3000:]}")
        self.assertTrue((out / "config.json").is_file(), "the site is written when nothing clashes")
        rep = self.root / "build/clearance/report.json"
        self.assertTrue(rep.is_file(), "build.py must have run the overlap check on the code model in its cwd (no report.json)")
        self.assertEqual(json.loads(rep.read_text()).get("clashes"), [], "the clean model has no clash")


if __name__ == "__main__":
    unittest.main()
