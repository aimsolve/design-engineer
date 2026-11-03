#!/usr/bin/env python3
"""understood.py without a browser: which drawn points get markers (--points), and --at projected through the note's camera.

  python3 -m unittest agent/skills/web-viewer/scripts/test_understood_args.py -v
"""
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import understood as U  # noqa: E402

hit = lambda px, role, part="A": {"px": px, "role": role, "part": part, "at": [0, 0, 0], "normal": [0, 0, 1]}
miss = lambda px, role: {"px": px, "role": role, "miss": True}
SHAPES = [
    {"type": "arrow", "pts": [hit([10, 10], "tail"), hit([90, 10], "tip", "B")]},
    {"type": "ellipse", "pts": [hit([50, 50], "centre"), hit([70, 50], "edge"), miss([30, 50], "edge")], "inside": {"A": 3}},
    {"type": "pen", "pts": [hit([0, 0], "stroke"), hit([5, 5], "stroke"), hit([9, 9], "stroke")]},
    {"type": "line", "pts": [miss([1, 1], "end"), hit([2, 2], "along"), hit([3, 3], "end")]},
]


def cam(w=200, h=100):
    """A camera at z=100 looking down -z (identity rotation), fov 90 deg, aspect w/h: row-major view and proj."""
    n, f, a = 1.0, 1000.0, w / h
    view = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -100, 0, 0, 0, 1]
    proj = [1 / a, 0, 0, 0, 0, 1, 0, 0, 0, 0, -(f + n) / (f - n), -2 * f * n / (f - n), 0, 0, -1, 0]
    return {"w": w, "h": h, "fov": 90, "view": view, "proj": proj}


class Points(unittest.TestCase):
    def test_default_marks_every_key_point_that_hits(self):
        m = U.markers(SHAPES, U.parse_points(None))
        self.assertEqual(m, [("1", [10, 10]), ("1", [90, 10]), ("2", [50, 50]), ("3", [0, 0]), ("3", [9, 9]), ("4", [3, 3])])

    def test_area_gets_its_centre_not_its_outline(self):
        self.assertEqual(U.markers(SHAPES, U.parse_points("2")), [("2", [50, 50])])

    def test_open_stroke_gets_its_two_ends(self):
        self.assertEqual(U.markers(SHAPES, U.parse_points("3")), [("3", [0, 0]), ("3", [9, 9])])

    def test_role_filter(self):
        self.assertEqual(U.markers(SHAPES, U.parse_points("1.tip")), [("1", [90, 10])])

    def test_bare_number_wins_over_a_role_of_the_same_shape(self):
        for v in ("1,1.tip", "1.tip,1"):
            self.assertEqual(len(U.markers(SHAPES, U.parse_points(v))), 2, v)

    def test_none_marks_nothing(self):
        self.assertEqual(U.markers(SHAPES, U.parse_points("none")), [])

    def test_bad_token_exits_with_a_message(self):
        with self.assertRaises(SystemExit) as e:
            U.parse_points("x")
        self.assertIn("N or N.role", str(e.exception.code))


class PinKeepsItsNumber(unittest.TestCase):
    def test_a_pin_gets_no_figure_number_marker_over_its_own_number(self):
        arrow = {"type": "arrow", "pts": [hit([10, 10], "tail"), hit([90, 10], "tip", "B")]}
        pin = {"type": "pin", "num": 1, "pts": [hit([50, 50], "pin")]}
        for sel in (None, "2"):
            self.assertEqual([m for m in U.markers([arrow, pin], U.parse_points(sel)) if m[1] == [50, 50]], [],
                             f"--points {sel}: the pin shows its own «1», no green «2» on top")


class Project(unittest.TestCase):
    def test_target_lands_in_the_middle(self):
        p = U.project(cam(), [0, 0, 0])
        self.assertAlmostEqual(p[0], 100, places=3)
        self.assertAlmostEqual(p[1], 50, places=3)

    def test_up_is_up_on_screen(self):
        self.assertLess(U.project(cam(), [0, 10, 0])[1], 50)

    def test_behind_the_camera_is_none(self):
        self.assertIsNone(U.project(cam(), [0, 0, 200]))


class Cli(unittest.TestCase):
    def test_at_needs_the_notes_camera(self):
        with tempfile.TemporaryDirectory() as td:
            site = Path(td) / "public"
            site.mkdir()
            for f in ("config.json", "parts.json", "model.glb"):
                (site / f).write_text("{}")
            nf = Path(td) / "n.json"
            nf.write_text(json.dumps({"msg_id": 1, "site": "public", "shapes": SHAPES}))
            r = subprocess.run([sys.executable, str(HERE / "understood.py"), str(nf), "--project", td, "--at", "1,2,3"],
                               capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("camera", r.stderr)

    def test_bad_at_exits_with_a_message(self):
        with tempfile.TemporaryDirectory() as td:
            site = Path(td) / "public"
            site.mkdir()
            for f in ("config.json", "parts.json", "model.glb"):
                (site / f).write_text("{}")
            nf = Path(td) / "n.json"
            nf.write_text(json.dumps({"msg_id": 1, "site": "public", "shapes": SHAPES, "camera": cam()}))
            r = subprocess.run([sys.executable, str(HERE / "understood.py"), str(nf), "--project", td, "--at", "1,2"],
                               capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("X,Y,Z", r.stderr)

    def test_missing_site_files_name_the_job_fix(self):
        with tempfile.TemporaryDirectory() as td:
            nf = Path(td) / "n.json"
            nf.write_text(json.dumps({"msg_id": 7, "site": "public", "shapes": SHAPES}))
            r = subprocess.run([sys.executable, str(HERE / "understood.py"), str(nf), "--project", td],
                               capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("public/model.glb", r.stderr)


if __name__ == "__main__":
    unittest.main()
