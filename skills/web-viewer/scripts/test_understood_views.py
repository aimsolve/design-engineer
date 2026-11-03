#!/usr/bin/env python3
"""understood.py without a browser (stage 2 of notes on the model): `--view N` picks one of the note's views, `--path` takes the agent's
3D sketch. Bad input must exit fast with a message naming the option, before any browser starts.

  python3 -m unittest agent/skills/web-viewer/scripts/test_understood_views.py -v

Contract (from the spec): --view N, 1-based, default the last view; a note without `views` has one view (its `camera` / hash).
--path "X,Y,Z;X,Y,Z;...[:label]" (repeatable): >= 2 points in assembly mm, projected through that view's camera; bad input exits with a message.
"""
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "understood.py"


def cam(x=0.0):
    n, f = 1.0, 1000.0
    return {"w": 200, "h": 100, "fov": 90, "near": n, "far": f, "position": [x, 0, 100], "target": [x, 0, 0], "up": [0, 1, 0],
            "view": [1, 0, 0, -x, 0, 1, 0, 0, 0, 0, 1, -100, 0, 0, 0, 1],
            "proj": [0.5, 0, 0, 0, 0, 1, 0, 0, 0, 0, -(f + n) / (f - n), -2 * f * n / (f - n), 0, 0, -1, 0]}


SHAPES = [{"type": "arrow", "view": 0, "path": [[0, 0, 0], [10, 0, 0]],
           "pts": [{"px": [100, 50], "role": "tail", "part": "A", "at": [0, 0, 0], "normal": [0, 0, 1]},
                   {"px": [110, 50], "role": "tip", "part": "B", "at": [10, 0, 0], "normal": [0, 0, 1]}]},
          {"type": "line", "view": 1, "path": [[0, 5, 0], [5, 5, 0]],
           "pts": [{"px": [90, 45], "role": "end", "part": "A", "at": [0, 5, 0], "normal": [0, 0, 1]},
                   {"px": [95, 45], "role": "end", "part": "A", "at": [5, 5, 0], "normal": [0, 0, 1]}]}]


def run(note, *args, site=True):
    """understood.py on a note in a throwaway project; site=True puts stub site files there (so the run gets past the site check)."""
    with tempfile.TemporaryDirectory() as td:
        if site:
            (Path(td) / "public").mkdir()
            for f in ("config.json", "parts.json", "model.glb"):
                (Path(td) / "public" / f).write_text("{}")
        nf = Path(td) / "n.json"
        nf.write_text(json.dumps({"msg_id": 1, "site": "public", **note}))
        try:
            return subprocess.run([sys.executable, str(SCRIPT), str(nf), "--project", td, *args], capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired as e:
            return subprocess.CompletedProcess(e.cmd, 124, "", "timeout: the bad argument was not refused before the browser started")


TWO_VIEWS = {"shapes": SHAPES, "views": [cam(0), cam(50)], "camera": cam(50)}
ONE_CAMERA = {"shapes": SHAPES, "camera": cam(0)}


class ArgError(unittest.TestCase):
    def refused(self, r, option, *words):
        self.assertNotEqual(r.returncode, 0, r.stdout[-300:])
        self.assertNotIn("unrecognized arguments", r.stderr, f"{option} must be a known option: {r.stderr[-300:]}")
        self.assertNotIn("Traceback", r.stderr, f"a message, not a crash: {r.stderr[-600:]}")
        self.assertIn(option, r.stderr, r.stderr[-300:])
        for w in words:
            self.assertIn(w, r.stderr, r.stderr[-300:])


class View(ArgError):
    def test_view_past_the_last_is_refused(self):
        self.refused(run(TWO_VIEWS, "--view", "3"), "--view", "2")      # says how many views the note has

    def test_view_is_one_based(self):
        self.refused(run(TWO_VIEWS, "--view", "0"), "--view")

    def test_negative_view_is_refused(self):
        self.refused(run(TWO_VIEWS, "--view", "-1"), "--view")

    def test_view_must_be_a_number(self):
        r = run(TWO_VIEWS, "--view", "x")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--view", r.stderr)
        self.assertNotIn("unrecognized arguments", r.stderr)

    def test_note_without_views_has_only_view_1(self):
        self.refused(run(ONE_CAMERA, "--view", "2"), "--view")

    def test_valid_view_and_path_are_accepted(self):
        # no site files: the run must get past argument parsing and stop at the site check (stage-1 message naming the missing file)
        r = run(TWO_VIEWS, "--view", "1", "--path", "0,0,0;10,0,0:gusset", "--path", "1.5,-2,3;4,5,6;7,8,9", site=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("unrecognized arguments", r.stderr, r.stderr[-300:])
        self.assertIn("public/model.glb", r.stderr, r.stderr[-300:])


class PathArg(ArgError):
    def test_one_point_is_not_a_path(self):
        self.refused(run(TWO_VIEWS, "--path", "1,2,3"), "--path")

    def test_one_point_with_a_label_is_not_a_path(self):
        self.refused(run(TWO_VIEWS, "--path", "1,2,3:gusset"), "--path")

    def test_point_with_two_coordinates_is_refused(self):
        self.refused(run(TWO_VIEWS, "--path", "1,2;3,4,5"), "--path", "X,Y,Z")

    def test_non_numbers_are_refused(self):
        self.refused(run(TWO_VIEWS, "--path", "a,b,c;1,2,3"), "--path")

    def test_nan_is_refused(self):
        self.refused(run(TWO_VIEWS, "--path", "nan,0,0;1,2,3"), "--path")

    def test_empty_is_refused(self):
        self.refused(run(TWO_VIEWS, "--path", ""), "--path")

    def test_one_bad_path_among_good_ones_is_refused(self):
        self.refused(run(TWO_VIEWS, "--path", "0,0,0;1,1,1", "--path", "0,0,0;x"), "--path")

    def test_path_needs_a_camera(self):
        self.refused(run({"shapes": SHAPES}, "--path", "0,0,0;10,0,0"), "--path", "camera")


if __name__ == "__main__":
    unittest.main()
