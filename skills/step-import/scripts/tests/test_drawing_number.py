#!/usr/bin/env python3
"""Tests for the drawing-number recognition in scripts/step_import.py: a client whose parts are numbered with THREE
dot-separated groups ("ABC 02.01.121 Plate") must come out as a made part with that drawing and the module 02.01,
instead of being classified as bought with no drawing and the wrong module.

The module-level helpers DRAWING, classify(name, drawing) and material_of(name, drawing) are imported straight from
the file with importlib. OCP is NOT needed for that: step_import.py guards its OCP imports with try/except, and
params.py (which it imports) is pure standard library -- checked, not assumed. Its own source does use PEP 701
f-strings, so it only parses on Python 3.12+; when the interpreter running these tests cannot read it, the test
module re-executes itself under a newer python3 found on PATH.

  python3 skills/step-import/scripts/tests/test_drawing_number.py -v       # from the plugin root

Contract under test (from the ticket, not from the code):
  DRAWING                 matches two OR three dot-separated 2-digit groups followed by a 3-digit group,
                          anywhere inside a part name: 02.01.121 and 01.02.03.456 both count.
  classify(nm, drawing)   "made" once a drawing number was found; unchanged for the rest.
  module                  the first five characters of the drawing number: 02.01 / 01.02.
  material_of(nm, dr)     a part with a drawing number is steel by default; unchanged for the rest.
"""
import os
import shutil
import subprocess
import sys
import importlib.util
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from step_names_i18n import NAMES  # noqa: E402

RU = NAMES["ru"]
SCRIPT = Path(__file__).resolve().parent.parent / "step_import.py"
SOURCE = SCRIPT.read_text(encoding="utf-8") if SCRIPT.exists() else ""


def _parses_here():
    try:
        compile(SOURCE, str(SCRIPT), "exec")
        return True
    except SyntaxError:
        return False


def _parses_with(exe):
    code = "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), sys.argv[1], 'exec')"
    with open(os.devnull, "w") as null:
        return subprocess.call([exe, "-c", code, str(SCRIPT)], stdout=null, stderr=null) == 0


def _reexec_under_newer_python():
    """step_import.py needs Python 3.12+ to parse. Hand the whole test run to an interpreter that can read it."""
    if os.environ.get("STEP_IMPORT_TEST_REEXEC"):
        return
    for cand in ("python3.13", "python3.12"):
        exe = shutil.which(cand)
        if exe and _parses_with(exe):
            os.environ["STEP_IMPORT_TEST_REEXEC"] = "1"
            argv = [exe, os.path.abspath(sys.argv[0])] + sys.argv[1:]
            sys.stderr.write("re-running under %s (step_import.py needs 3.12+ to parse)\n" % cand)
            sys.stderr.flush()
            os.execv(exe, argv)


if SOURCE and not _parses_here() and __name__ == "__main__":
    _reexec_under_newer_python()


def _load():
    """step_import.py as a module, with no OCP and no numpy installed. Returns (module, reason it could not load)."""
    if not SCRIPT.exists():
        return None, "%s does not exist" % SCRIPT
    if not _parses_here():
        return None, ("this interpreter (%s) cannot parse step_import.py and no python3.12+ was found on PATH"
                      % sys.version.split()[0])
    import importlib.util
    spec = importlib.util.spec_from_file_location("step_import_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # must not need OCP
    return mod, None


SI, LOAD_ERROR = _load()


@unittest.skipIf(SI is None, "step_import.py not loadable: %s" % LOAD_ERROR)
class DrawingNumber(unittest.TestCase):
    """What the module-level helpers make of a part name."""

    def pipeline(self, nm):
        """The same three steps step_import does per part: find the drawing, classify, pick the material."""
        m = SI.DRAWING.search(nm)
        drawing = m.group(0) if m else None
        module = drawing[:5] if drawing else None
        return drawing, module, SI.classify(nm, drawing), SI.material_of(nm, drawing)

    @unittest.skipIf(importlib.util.find_spec("OCP") is not None, "needs an interpreter without OCP (the router's)")
    def test_import_does_not_need_ocp(self):
        """The helpers are reachable without OCP installed (nothing of OCP is importable here)."""
        with self.assertRaises(ImportError):
            __import__("OCP.STEPCAFControl")
        for attr in ("DRAWING", "classify", "material_of"):
            self.assertTrue(hasattr(SI, attr), "step_import.%s must be importable on its own" % attr)

    def test_three_group_number_is_a_drawing(self):
        nm = "%s 02.01.121 %s" % (RU["code"], RU["plate"])
        m = SI.DRAWING.search(nm)
        self.assertIsNotNone(m, "a three-group number inside a name is a drawing number: %r" % nm)
        self.assertEqual("02.01.121", m.group(0))

    def test_three_group_part_is_made_with_module(self):
        drawing, module, kind, material = self.pipeline("ABC 02.01.121 Plate")
        self.assertEqual("02.01.121", drawing)
        self.assertEqual("made", kind, "a part with our own drawing is made, not bought")
        self.assertEqual("02.01", module, "the module is the first five characters of the drawing number")
        self.assertEqual("steel", material, "a part with a drawing number is steel by default")

    def test_three_group_number_in_punctuation(self):
        """The number is found wherever it sits in the name, not only after a prefix word."""
        drawing, module, kind, _ = self.pipeline("Plate (02.01.121)")
        self.assertEqual("02.01.121", drawing)
        self.assertEqual("02.01", module)
        self.assertEqual("made", kind)

    def test_four_group_behaviour_unchanged(self):
        """Regression guard: the client that numbers in four groups keeps working exactly as today."""
        drawing, module, kind, material = self.pipeline("Bracket 01.02.03.456")
        self.assertEqual("01.02.03.456", drawing)
        self.assertEqual("made", kind)
        self.assertEqual("01.02", module)
        self.assertEqual("steel", material)

    def test_bought_part_without_a_number_unchanged(self):
        """Regression guard: a fastener stays standard/purchased with no drawing."""
        nm = "hex nut ISO 4034 - M16"
        drawing, module, kind, material = self.pipeline(nm)
        self.assertIsNone(drawing, "there is no drawing number in %r" % nm)
        self.assertIsNone(module)
        self.assertEqual("standard", kind)
        self.assertEqual("fastener", material)

    def test_two_groups_without_a_three_digit_tail_is_not_a_drawing(self):
        """Regression guard against a too-loose pattern: a module number alone is not a drawing number."""
        drawing, _, kind, _ = self.pipeline("ABC 02.01 Plate")
        self.assertIsNone(drawing, "02.01 on its own has no 3-digit group")
        self.assertNotEqual("made", kind)


@unittest.skipIf(SI is None, "step_import.py not loadable: %s" % LOAD_ERROR)
class LookAlikes(unittest.TestCase):
    """Three dot-separated groups is also the shape of a date and of a profile size. classify() returns "made" and
    material_of() returns "steel" the moment a drawing number is found, before the STANDARD and MATERIAL tables are
    consulted, so a false positive does not just mislabel a part - it changes its id, module and material."""

    def drawing(self, nm):
        m = SI.DRAWING.search(nm)
        return m.group(0) if m else None

    def test_a_date_is_not_a_drawing_number(self):
        for nm in ("%s rev 20.10.2026" % RU["plate"], "Opora 12.05.1980", "bracket 01.01.2026 v2"):
            self.assertIsNone(self.drawing(nm), "%r must not yield a drawing number (got %r)" % (nm, self.drawing(nm)))
            self.assertNotEqual("made", SI.classify(nm, self.drawing(nm)), nm)

    def test_a_longer_digit_run_is_not_a_drawing_number(self):
        for nm in ("02.01.1210", "profile 40.40.1500", "x1.02.01.456y"):
            self.assertIsNone(self.drawing(nm), "%r must not yield a drawing number (got %r)" % (nm, self.drawing(nm)))

    def test_a_real_number_next_to_punctuation_still_counts(self):
        for nm, want in (("%s 02.01.121-01" % RU["code"], "02.01.121"), ("(01.02.03.456)", "01.02.03.456")):
            self.assertEqual(want, self.drawing(nm), nm)

    def test_a_bought_name_carrying_a_drawing_shaped_code_becomes_made(self):
        """The accepted consequence of the change, pinned on purpose: any drawing number means «ours», and it wins
        over the fastener and material tables. If a client ever ships bought parts coded like this, classify()
        needs the precedence changed - not the pattern."""
        nm = "hex nut ISO 4034 - M16 25.05.100"
        self.assertEqual("25.05.100", self.drawing(nm))
        self.assertEqual("made", SI.classify(nm, self.drawing(nm)))
        self.assertEqual("steel", SI.material_of(nm, self.drawing(nm)))


if __name__ == "__main__":
    if LOAD_ERROR:
        print("note: %s" % LOAD_ERROR, file=sys.stderr)
    unittest.main(verbosity=2)
