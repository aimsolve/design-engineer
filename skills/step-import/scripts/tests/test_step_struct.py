#!/usr/bin/env python3
"""Tests for scripts/step_struct.py: reading the assembly structure out of a STEP file's TEXT with the standard
library only (no OCP, no build123d, no numpy), and diffing two STEP files as an old/new re-export report.

Everything is driven through the script's own command line, the way an engineer uses it. The fixtures next to this
file (tests/fixtures/synthetic_old.step, synthetic_new.step, synthetic_no_products.step) are hand written, a few dozen lines each: HEADER plus
PRODUCT / PRODUCT_DEFINITION_FORMATION / PRODUCT_DEFINITION entities and NEXT_ASSEMBLY_USAGE_OCCURRENCE links,
with \\X2\\-escaped Cyrillic names, a \\X\\C0 single-byte escape, a doubled '' apostrophe, one three-group and one
four-group drawing number, and one entity wrapped across two lines (as real exporters do). The Cyrillic words they
decode to are in step_names_i18n.py.

  python3 skills/step-import/scripts/tests/test_step_struct.py -v       # from the plugin root

Contract under test (from the ticket, not from the code):
  step_struct.py FILE.STEP            -> exit 0, prints a summary: number of PRODUCT_DEFINITIONs, number of distinct
                                         product names, number of NEXT_ASSEMBLY_USAGE_OCCURRENCE links, root product
                                         name. Writes no file.
  step_struct.py FILE.STEP --json OUT  -> additionally writes {"root": name,
                                         "products": {name: instance count},
                                         "tree": {"name":..., "drawing":..., "children":[...]}}
  step_struct.py A.STEP --diff B.STEP  -> re-export report of A (old) against B (new): counts per side, how many
                                         product names are in both, which are only in A ("gone"), which only in B
                                         ("new"), which are in both with a different instance count (old -> new),
                                         which pairs look like a rename (same drawing number, different name).
  names: '\\X2\\<utf-16be hex>\\X0\\' and '\\X\\<byte>' are decoded, '' inside a quoted string is one apostrophe.
  drawing number: two or three dot-separated 2-digit groups plus a 3-digit group (02.01.121 and 01.02.03.456).
  no PRODUCT_DEFINITION in the file, or --diff given an unreadable file -> non-zero exit and a clear message.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from step_names_i18n import NAMES  # noqa: E402

RU = NAMES["ru"]
FIXTURES = HERE / "fixtures"
SCRIPT = HERE.parent / "step_struct.py"

# The fixture names, as they must come out after the STEP escapes are decoded.
ROOT_NAME = "%s 02.01.000 %s" % (RU["code"], RU["machine"])
PLANKA_OLD = "%s 02.01.121 %s" % (RU["code"], RU["plate"])     # three-group drawing number, renamed in the new export
PLANKA_NEW = "%s %s" % (PLANKA_OLD, RU["bent"])                 # note: PLANKA_OLD is a prefix of this one
BRACKET = "Bracket 01.02.03.456"              # four-group drawing number, 1 instance -> 3 instances
NUT = "hex nut ISO 4034 - M16"                # bought, gone from the new export
SEAL = "O'Ring 7x2 À seal"                    # '' apostrophe + \X\C0 single byte escape
NEW_PART = "%s 02.01.122" % RU["bracket"]       # only in the new export

OLD_COUNTS = {"definitions": 5, "names": 5, "links": 5}
NEW_COUNTS = {"definitions": 5, "names": 5, "links": 7}

SECTION = re.compile(
    r"(gone|removed|only in old|only in a\b|added|only in new|only in b\b|renames?|renamed|new|both|common|"
    r"unchanged|changed)", re.I)


def run(*args, cwd=None):
    """Run step_struct.py with the interpreter running these tests. Returns the CompletedProcess."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT)] + [str(a) for a in args],
                          cwd=str(cwd) if cwd else None, env=env, timeout=120,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, encoding="utf-8", errors="replace")


def shown(p):
    """The whole run, for an assertion message."""
    return "exit %s\n--- stdout ---\n%s\n--- stderr ---\n%s" % (p.returncode, p.stdout, p.stderr)


def numbers_on_lines(out, label):
    """Every integer printed on a line that mentions `label` (case-insensitive)."""
    found = set()
    rx = re.compile(label, re.I)
    for line in out.splitlines():
        if rx.search(line):
            found.update(int(n) for n in re.findall(r"(?<![\d.])(\d+)(?![\d.])", line))
    return found


def section_lines(out, section):
    """The lines that belong to the section whose header matches `section` (the header line included).

    A section runs from its header until the next line that carries any other section keyword, so this works both
    for "gone: a, b" on one line and for a header with the names listed underneath it.
    """
    want = re.compile(section, re.I)
    lines, inside = [], False
    for line in out.splitlines():
        m = SECTION.search(line)
        if m:
            inside = bool(want.search(m.group(0)))
        if inside:
            lines.append(line)
    return lines


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="step_struct_"))
        shutil.copytree(str(FIXTURES), str(cls.tmp / "fx"))
        cls.old = cls.tmp / "fx" / "synthetic_old.step"
        cls.new = cls.tmp / "fx" / "synthetic_new.step"
        cls.empty = cls.tmp / "fx" / "synthetic_no_products.step"
        cls.work = cls.tmp / "work"
        cls.work.mkdir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def ok(self, *args, **kw):
        p = run(*args, **kw)
        self.assertEqual(0, p.returncode, "expected a clean run of %s\n%s" % (args, shown(p)))
        return p


class Summary(Base):
    """step_struct.py FILE.STEP -- the plain summary."""

    def test_counts_and_root_name(self):
        p = self.ok(self.old, cwd=self.work)
        out = p.stdout + p.stderr
        self.assertIn(OLD_COUNTS["definitions"], numbers_on_lines(out, r"product_definition|product definition"),
                      "summary must state the number of PRODUCT_DEFINITIONs (5)\n%s" % shown(p))
        self.assertIn(OLD_COUNTS["names"], numbers_on_lines(out, r"name|product(?!_def)"),
                      "summary must state the number of distinct product names (5)\n%s" % shown(p))
        self.assertIn(OLD_COUNTS["links"], numbers_on_lines(out, r"next_assembly|usage|link"),
                      "summary must state the number of NEXT_ASSEMBLY_USAGE_OCCURRENCE links (5)\n%s" % shown(p))
        self.assertIn(ROOT_NAME, out, "summary must name the root product, decoded\n%s" % shown(p))

    def test_summary_writes_nothing(self):
        before = {q: q.stat().st_mtime_ns for q in (self.tmp / "fx").iterdir()}
        p = self.ok(self.old, cwd=self.work)
        self.assertEqual([], sorted(q.name for q in self.work.iterdir()),
                         "a plain summary must not write a file\n%s" % shown(p))
        after = {q: q.stat().st_mtime_ns for q in (self.tmp / "fx").iterdir()}
        self.assertEqual(before, after, "the STEP files next to it must be left untouched\n%s" % shown(p))

    def test_new_export_counts(self):
        """The second variant: same products, 7 links instead of 5."""
        p = self.ok(self.new, cwd=self.work)
        out = p.stdout + p.stderr
        self.assertIn(NEW_COUNTS["links"], numbers_on_lines(out, r"next_assembly|usage|link"),
                      "the new export has 7 NEXT_ASSEMBLY_USAGE_OCCURRENCE links\n%s" % shown(p))


class Json(Base):
    """step_struct.py FILE.STEP --json OUT.json"""

    def load(self, step, name="out.json"):
        out = self.work / name
        p = self.ok(step, "--json", out, cwd=self.work)
        self.assertTrue(out.exists(), "--json must write the file it is given\n%s" % shown(p))
        with open(str(out), encoding="utf-8") as f:
            return json.load(f), p

    def test_root_and_instance_counts(self):
        d, p = self.load(self.old)
        self.assertEqual(ROOT_NAME, d["root"], "root product name\n%s" % shown(p))
        prod = d["products"]
        self.assertEqual(2, prod.get(PLANKA_OLD), "the three-group part sits twice in the assembly\n%s" % shown(p))
        self.assertEqual(1, prod.get(BRACKET), "the four-group part sits once\n%s" % shown(p))
        self.assertEqual(1, prod.get(NUT), "the bought part sits once\n%s" % shown(p))
        self.assertEqual(1, prod.get(SEAL), "the seal sits once\n%s" % shown(p))
        self.assertIn(ROOT_NAME, prod, "the root product is listed too\n%s" % shown(p))

    def test_escapes_are_decoded(self):
        d, p = self.load(self.old)
        raw = json.dumps(d, ensure_ascii=False)
        self.assertNotIn("\\X2\\", raw, "\\X2\\ escapes must be decoded, not carried through\n%s" % shown(p))
        self.assertNotIn("\\X\\", raw, "\\X\\ single-byte escapes must be decoded\n%s" % shown(p))
        self.assertIn(SEAL, d["products"], "'' is one apostrophe and \\X\\C0 is one byte\n%s" % shown(p))
        self.assertIn(PLANKA_OLD, d["products"], "Cyrillic name decoded from \\X2\\ hex\n%s" % shown(p))

    def test_tree_shape_and_drawing_numbers(self):
        d, p = self.load(self.old)
        tree = d["tree"]
        self.assertEqual(ROOT_NAME, tree["name"], "tree root\n%s" % shown(p))
        self.assertEqual("02.01.000", tree["drawing"], "three-group drawing number of the root\n%s" % shown(p))
        kids = {c["name"]: c for c in tree["children"]}
        self.assertEqual({PLANKA_OLD, BRACKET, NUT, SEAL}, set(kids),
                         "every child product hangs under the root\n%s" % shown(p))
        self.assertEqual("02.01.121", kids[PLANKA_OLD]["drawing"],
                         "a three-group number is a drawing number\n%s" % shown(p))
        self.assertEqual("01.02.03.456", kids[BRACKET]["drawing"],
                         "a four-group number still is one\n%s" % shown(p))
        self.assertIsNone(kids[NUT]["drawing"], "a bought part has no drawing number\n%s" % shown(p))
        self.assertEqual([], kids[NUT]["children"], "a leaf has an empty children list\n%s" % shown(p))


class Diff(Base):
    """step_struct.py OLD.STEP --diff NEW.STEP"""

    @classmethod
    def setUpClass(cls):
        super(Diff, cls).setUpClass()
        cls.p = run(cls.old, "--diff", cls.new, cwd=cls.work)
        cls.out = cls.p.stdout + cls.p.stderr

    def test_runs_clean(self):
        self.assertEqual(0, self.p.returncode, "a diff of two readable STEPs is a clean run\n%s" % shown(self.p))

    def test_counts_for_each_side(self):
        self.assertTrue({5, 7} <= numbers_on_lines(self.out, r"next_assembly|usage|link"),
                        "the report states the link count of both sides (5 old, 7 new)\n%s" % shown(self.p))
        self.assertIn(5, numbers_on_lines(self.out, r"product|name|part"),
                      "the report states the product count of each side\n%s" % shown(self.p))

    def test_names_in_both(self):
        self.assertIn(3, numbers_on_lines(self.out, r"both|common|unchanged|same"),
                      "3 product names are in both exports (root, bracket, seal)\n%s" % shown(self.p))

    def test_gone(self):
        block = "\n".join(section_lines(self.out, r"gone|removed|only in old|only in a\b"))
        self.assertIn(NUT, block, "the bought part that disappeared is reported as gone\n%s" % shown(self.p))

    def test_new(self):
        block = "\n".join(section_lines(self.out, r"^new$|added|only in new|only in b\b|\bnew\b"))
        self.assertIn(NEW_PART, block, "the part that only the new export has is reported as new\n%s" % shown(self.p))

    def test_changed_instance_count(self):
        hit = [l for l in self.out.splitlines()
               if BRACKET in l and re.search(r"(?<!\d)1(?!\d)", l) and re.search(r"(?<!\d)3(?!\d)", l)]
        self.assertTrue(hit, "the part whose instance count changed is reported as 1 -> 3\n%s" % shown(self.p))

    def test_rename_pair(self):
        block = "\n".join(section_lines(self.out, r"renames?|renamed"))
        self.assertIn(PLANKA_NEW, block,
                      "same drawing number, different name = a rename pair\n%s" % shown(self.p))
        self.assertIn("02.01.121", block,
                      "the rename is recognised by the shared drawing number\n%s" % shown(self.p))
        self.assertNotIn(NUT, block, "a part that simply disappeared is not a rename\n%s" % shown(self.p))


class Failures(Base):
    """Non-zero exit and a clear message, not a traceback."""

    def test_no_product_definition(self):
        p = run(self.empty, cwd=self.work)
        self.assertNotEqual(0, p.returncode, "a STEP without product structure is an error\n%s" % shown(p))
        both = p.stdout + p.stderr
        self.assertNotIn("Traceback (most recent call last)", both,
                         "the error must be a message, not a traceback\n%s" % shown(p))
        self.assertRegex(both, r"(?i)product_definition|product definition",
                         "the message says what is missing\n%s" % shown(p))

    def test_diff_against_unreadable_file(self):
        missing = self.work / "does_not_exist.step"
        p = run(self.old, "--diff", missing, cwd=self.work)
        self.assertNotEqual(0, p.returncode, "--diff on a file that cannot be read is an error\n%s" % shown(p))
        both = p.stdout + p.stderr
        self.assertNotIn("Traceback (most recent call last)", both,
                         "the error must be a message, not a traceback\n%s" % shown(p))
        self.assertIn("does_not_exist.step", both, "the message names the file\n%s" % shown(p))


if __name__ == "__main__":
    if not SCRIPT.exists():
        print("note: %s does not exist yet -- every case below must fail" % SCRIPT, file=sys.stderr)
    unittest.main(verbosity=2)
