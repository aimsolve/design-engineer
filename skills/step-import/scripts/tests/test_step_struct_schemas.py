#!/usr/bin/env python3
"""step_struct.py against inputs that real client exports contain and the hand-written fixtures of
test_step_struct.py do not: the AP214 entity spellings, several assemblies in one file, a `;` inside a quoted
product name, a sub-assembly placed more than once, CRLF line ends, and names that merely look like drawing
numbers.

Every case here was written because something was actually wrong:

* AP214 writes the product version as PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE, not
  PRODUCT_DEFINITION_FORMATION. The first implementation passed every fixture test and then read nothing at all
  out of a real 199 MB export - the counts looked right while every product name came out None.
* A client file can hold two production lines side by side. The root was whichever top-level
  product the exporter happened to write first, so the tool could name a two-part sub-assembly as the machine.
* `;` is legal inside a STEP string literal. Splitting the file on `;` deleted that record silently and the
  product reappeared as a null name.
* A sub-assembly placed twice places everything inside it twice. Counting usage links said «once» for every part
  of it, which does not agree with what step_import.py puts in parts.json.
* Three dot-separated groups is also the shape of a date: an unguarded pattern read 20.10.202 out of
  «rev 20.10.2026» and made the part a made part in module 20.10.

  python3 skills/step-import/scripts/tests/test_step_struct_schemas.py -v       # from the plugin root
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from step_names_i18n import NAMES  # noqa: E402

RU = NAMES["ru"]
SCRIPT = HERE.parent / "step_struct.py"
IMPORTER = HERE.parent / "step_import.py"

HEAD = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION (( 'STEP AP214' ), '1' );
FILE_NAME ('whatever.STEP', '2026-10-15T15:49:51', ( 'a@b.c' ), ( '' ), 'SwSTEP 2.0', 'SolidWorks 2026', '' );
FILE_SCHEMA (( 'AUTOMOTIVE_DESIGN' ));
ENDSEC;
DATA;
#3 = PRODUCT_DEFINITION_CONTEXT ( 'detailed design', #999, 'design' ) ;
#2 = PRODUCT_CONTEXT ( 'NONE', #998, 'mechanical' ) ;
"""
TAIL = "ENDSEC;\nEND-ISO-10303-21;\n"


def hexs(s):
    return s.encode("utf-16-be").hex()


def product(base, name, pid=None, formation="PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE"):
    """One product as AP214 writes it: definition first, formation after it, product last (forward references).
    PRODUCT is ( id, name, description, frame_of_reference ); `pid` sets the id apart from the name."""
    return ("#%d0 = PRODUCT_DEFINITION ( 'UNKNOWN', '', #%d1, #3 ) ;\n"
            "#%d1 = %s ( 'ANY', '', #%d2, .NOT_KNOWN. ) ;\n"
            "#%d2 = PRODUCT ( '%s', '%s', '', ( #2 ) ) ;\n"
            % (base, base, base, formation, base, base, name if pid is None else pid, name))


def nauo(eid, parent, child, descr=""):
    return "#%d = NEXT_ASSEMBLY_USAGE_OCCURRENCE ( '%d', '%s', '', #%d0, #%d0, $ ) ;\n" % (eid, eid, descr, parent, child)


def esc(cyrillic):
    return "\\X2\\%s\\X0\\" % hexs(cyrillic)


# a machine and its unit with Cyrillic names, as a SolidWorks export escapes them (the words: step_names_i18n.py)
MACHINE = "%s-1 01.00.00.300 %s" % (esc(RU["code"]), esc(RU["machine"]))
MACHINE_TEXT = "%s-1 01.00.00.300 %s" % (RU["code"], RU["machine"])
UNIT = "%s-1 01.04.00.300 %s" % (esc(RU["code"]), esc(RU["unit"]))
UNIT_TEXT = "%s-1 01.04.00.300 %s" % (RU["code"], RU["unit"])


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="step_struct_schema_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def write(self, name, body, newline="\n"):
        p = self.tmp / name
        text = HEAD + body + TAIL
        if newline != "\n":
            text = text.replace("\n", newline)
        with open(str(p), "w", encoding="utf-8", newline="") as f:
            f.write(text)
        return p

    def run_it(self, *args, **kw):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        p = subprocess.run([sys.executable, str(SCRIPT)] + [str(a) for a in args], env=env, timeout=120,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, encoding="utf-8", errors="replace")
        if kw.get("expect_ok", True):
            self.assertEqual(0, p.returncode, "exit %s\n%s\n%s" % (p.returncode, p.stdout, p.stderr))
        return p

    def as_json(self, step, name="out.json", *args):
        out = self.tmp / name
        self.run_it(step, "--json", out, *args)
        with open(str(out), encoding="utf-8") as f:
            return json.load(f)


class Ap214(Base):
    """The entity spellings and the header stamp."""

    @classmethod
    def setUpClass(cls):
        super(Ap214, cls).setUpClass()
        cls.step = None

    def machine(self):
        if self.step is None:
            Ap214.step = self.write("ap214.STEP",
                                    product(11, MACHINE) + product(12, UNIT) + nauo(8001, 11, 12))
        return self.step

    def test_ap214_formation_still_yields_names(self):
        """…_FORMATION_WITH_SPECIFIED_SOURCE is the formation entity: the root must be named, not None."""
        out = self.run_it(self.machine()).stdout
        self.assertIn(MACHINE_TEXT, out, "the AP214 root product must be named:\n%s" % out)
        self.assertNotIn("root: None", out, "names must not come out None on an AP214 export:\n%s" % out)

    def test_products_are_counted_not_dropped(self):
        d = self.as_json(self.machine())
        self.assertEqual(MACHINE_TEXT, d["root"])
        self.assertEqual("01.00.00.300", d["tree"]["drawing"])
        self.assertNotIn(None, d["products"], "no product may be counted under a None name: %r" % (d["products"],))
        self.assertEqual({MACHINE_TEXT: 1, UNIT_TEXT: 1}, d["products"])
        self.assertEqual(1, len(d["tree"]["children"]), "the child product hangs under the root")

    def test_header_identity_is_reported(self):
        """Two exports of one machine have the same file name, so the summary has to say which bytes they are."""
        out = self.run_it(self.machine()).stdout
        self.assertIn("2026-10-15T15:49:51", out, "the exporter's own timestamp:\n%s" % out)
        self.assertIn("SolidWorks 2026", out, "the originating system:\n%s" % out)
        self.assertRegex(out, r"sha256 [0-9a-f]{8}", "a hash of the bytes:\n%s" % out)

    def test_crlf_is_read_the_same(self):
        """Real exports are CRLF."""
        crlf = self.write("crlf.STEP", product(11, MACHINE) + product(12, UNIT) + nauo(8001, 11, 12),
                          newline="\r\n")
        self.assertEqual(self.as_json(self.machine(), "lf.json")["products"],
                         self.as_json(crlf, "crlf.json")["products"])


class SeveralAssemblies(Base):
    """A client file with two machines in it (two production lines): the smaller one is written FIRST."""

    @classmethod
    def setUpClass(cls):
        super(SeveralAssemblies, cls).setUpClass()

    def two_lines(self):
        body = (product(21, "Line B 04.01.000 Sub") + product(22, "Line B 04.01.001 Child") + nauo(8101, 21, 22)
                + product(11, MACHINE) + product(12, UNIT) + product(13, "%s-1 01.04.01.300 %s"
                                                                            % (esc(RU["code"]), esc(RU["frame"])))
                + nauo(8001, 11, 12) + nauo(8002, 12, 13))
        return self.write("two.STEP", body)

    def test_the_largest_assembly_is_the_root(self):
        """Not whichever top-level product the exporter wrote first."""
        step = self.two_lines()
        out = self.run_it(step).stdout
        self.assertIn("root: %s" % MACHINE_TEXT, out,
                      "the three-product machine is the root, not the two-product sub-assembly:\n%s" % out)
        self.assertEqual(MACHINE_TEXT, self.as_json(step, "two.json")["root"])

    def test_every_top_level_product_is_named(self):
        step = self.two_lines()
        out = self.run_it(step).stdout
        self.assertIn("Line B 04.01.000 Sub", out, "the other top-level assembly must be named too:\n%s" % out)
        self.assertIn("2 top-level products", out, "the count is of top-level products, not of orphans:\n%s" % out)
        self.assertEqual([MACHINE_TEXT, "Line B 04.01.000 Sub"], self.as_json(step, "two2.json")["roots"],
                         "roots lists every top-level product, largest first")

    def test_tree_of_the_root_only(self):
        d = self.as_json(self.two_lines(), "two3.json")
        self.assertEqual(MACHINE_TEXT, d["tree"]["name"])
        self.assertEqual([UNIT_TEXT], [c["name"] for c in d["tree"]["children"]])


class Semicolons(Base):
    """`;` is legal inside a STEP string literal; it must not end the record."""

    def test_semicolon_in_a_product_name(self):
        step = self.write("semi.STEP",
                          product(11, MACHINE) + product(12, "hex nut; ISO 4034 - M16") + nauo(8001, 11, 12))
        d = self.as_json(step, "semi.json")
        self.assertNotIn(None, d["products"], "the record must not be lost: %r" % (d["products"],))
        self.assertIn("hex nut; ISO 4034 - M16", d["products"],
                      "a `;` inside the name is part of the name: %r" % (d["products"],))
        self.assertEqual(2, len(d["products"]))

    def test_semicolon_in_a_link_description(self):
        step = self.write("semilink.STEP",
                          product(11, MACHINE) + product(12, UNIT) + nauo(8001, 11, 12, descr="pos 1; left"))
        out = self.run_it(step).stdout
        self.assertRegex(out, r"links 1\b", "the link must still be counted:\n%s" % out)
        self.assertEqual({MACHINE_TEXT: 1, UNIT_TEXT: 1}, self.as_json(step, "semilink.json")["products"])

    def test_an_unreadable_record_is_reported_not_hidden(self):
        """Whatever the splitter does, a record it cannot read has to be said out loud."""
        body = product(11, MACHINE) + "#999 = PRODUCT ( 'broken'\n"      # no closing paren, no `;`
        step = self.write("broken.STEP", body)
        out = self.run_it(step).stdout
        self.assertIn("WARNING", out, "an unreadable record must be reported, not skipped:\n%s" % out)
        self.assertRegex(out, r"only 1 of the 2 PRODUCT records",
                         "the warning says how many of how many were read:\n%s" % out)


class RepeatedSubAssembly(Base):
    """A sub-assembly placed twice places everything inside it twice."""

    def machine(self):
        return self.write("repeat.STEP",
                          product(11, "Machine 02.01.000") + product(12, "Sub 02.01.100")
                          + product(13, "Leaf 02.01.121")
                          + nauo(8001, 11, 12) + nauo(8002, 11, 12) + nauo(8003, 12, 13))

    def test_a_part_inside_a_doubled_sub_assembly_is_counted_twice(self):
        d = self.as_json(self.machine(), "repeat.json")
        self.assertEqual({"Machine 02.01.000": 1, "Sub 02.01.100": 2, "Leaf 02.01.121": 2}, d["products"],
                         "the leaf physically sits twice, once per placement of its sub-assembly")

    def test_the_diff_reports_placements_not_links(self):
        once = self.write("once.STEP",
                          product(11, "Machine 02.01.000") + product(12, "Sub 02.01.100")
                          + product(13, "Leaf 02.01.121") + nauo(8001, 11, 12) + nauo(8003, 12, 13))
        out = self.run_it(once, "--diff", self.machine()).stdout
        hit = [l for l in out.splitlines() if "Leaf 02.01.121" in l and "1 -> 2" in l]
        self.assertTrue(hit, "adding a second placement of the sub-assembly doubles the leaf:\n%s" % out)


class DiffJson(Base):
    """--json together with --diff writes the diff, not only the old side."""

    def test_diff_json_is_the_report(self):
        a = self.write("a.STEP", product(11, MACHINE) + product(12, UNIT) + nauo(8001, 11, 12))
        b = self.write("b.STEP", product(11, MACHINE) + product(12, UNIT)
                       + nauo(8001, 11, 12) + nauo(8002, 11, 12))
        d = self.as_json(a, "diff.json", "--diff", b)
        self.assertEqual(2, d["in_both"])
        self.assertEqual([], d["gone"])
        self.assertEqual([], d["new_names"])
        self.assertEqual([{"name": UNIT_TEXT, "old": 1, "new": 2}], d["placements_changed"])
        self.assertEqual(MACHINE_TEXT, d["old"]["root"])


class ProductNaming(Base):
    """PRODUCT is ( id, name, … ): step_import.py takes the name and falls back to the id, so this must too."""

    def test_the_name_wins_over_the_id(self):
        step = self.write("named.STEP",
                          product(11, MACHINE, pid="0001") + product(12, UNIT, pid="0002")
                          + nauo(8001, 11, 12))
        d = self.as_json(step, "named.json")
        self.assertEqual(MACHINE_TEXT, d["root"], "the root is the PRODUCT name, not its id")
        self.assertEqual("01.00.00.300", d["tree"]["drawing"], "the drawing number comes out of the name")
        self.assertNotIn("0001", d["products"], "the id is not a product name: %r" % (d["products"],))

    def test_an_empty_name_falls_back_to_the_id(self):
        step = self.write("idonly.STEP", product(11, "", pid="Machine 02.01.000") + product(12, "", pid="Part 02.01.121")
                          + nauo(8001, 11, 12))
        d = self.as_json(step, "idonly.json")
        self.assertEqual("Machine 02.01.000", d["root"], "with an empty name the id is the name")
        self.assertEqual({"Machine 02.01.000": 1, "Part 02.01.121": 1}, d["products"])


class Truncation(Base):
    """A parse that lost records must say so; silence is the one unacceptable answer."""

    def body(self, extra_record):
        return (product(11, MACHINE) + extra_record + product(12, UNIT) + product(13, "Part 02.01.121")
                + nauo(8001, 11, 12) + nauo(8002, 11, 13))

    def test_a_stray_apostrophe_is_reported(self):
        """One un-doubled apostrophe anywhere makes everything after it unreadable - loudly."""
        step = self.write("stray.STEP", self.body("#110 = PRODUCT_DEFINITION_SHAPE ( 'x', 'the maker's bracket', #110 ) ;\n"))
        out = self.run_it(step).stdout
        self.assertIn("WARNING", out, "a truncated parse must not look clean:\n%s" % out)
        self.assertIn("could be read", out, "the warning counts what was read of what the file holds:\n%s" % out)
        self.assertRegex(out, r"only \d+ of the \d+ (PRODUCT|NEXT_ASSEMBLY)", out)

    def test_a_comment_with_an_apostrophe_is_reported(self):
        """`/* … */` is legal anywhere; an apostrophe inside one is the realistic trigger."""
        step = self.write("comment.STEP", self.body("/* the maker's export, rev 2 */\n"))
        out = self.run_it(step).stdout
        self.assertIn("WARNING", out, "a comment must not be able to silence the report:\n%s" % out)
        self.assertRegex(out, r"only \d+ of the \d+ ", out)

    def test_a_clean_file_carries_no_warning(self):
        step = self.write("clean.STEP", self.body(""))
        out = self.run_it(step).stdout
        self.assertNotIn("WARNING", out, "nothing was lost here:\n%s" % out)


class NoRootAndCycles(Base):
    """Every product a child of another: no top-level assembly."""

    def test_a_cycle_is_a_message_not_a_traceback(self):
        step = self.write("cycle.STEP",
                          product(11, MACHINE) + product(12, UNIT)
                          + nauo(8001, 11, 12) + nauo(8002, 12, 11))
        p = self.run_it(step)
        self.assertNotIn("Traceback (most recent call last)", p.stdout + p.stderr,
                         "a cyclic assembly must not crash:\n%s\n%s" % (p.stdout, p.stderr))
        self.assertIn("WARNING", p.stdout, "it must say what is wrong:\n%s" % p.stdout)

    def test_a_cycle_with_json_writes_a_null_tree(self):
        step = self.write("cycle2.STEP",
                          product(11, MACHINE) + product(12, UNIT)
                          + nauo(8001, 11, 12) + nauo(8002, 12, 11))
        d = self.as_json(step, "cycle.json")
        self.assertIsNone(d["tree"], "there is no tree without a top-level assembly")
        self.assertEqual([], d["roots"])

    def test_an_unknown_formation_spelling_is_an_error(self):
        """Not `root: None` with exit 0 - that is the bug that cost a debugging round on the real export."""
        step = self.write("formation.STEP", product(11, MACHINE, formation="SOME_OTHER_FORMATION"))
        p = self.run_it(step, expect_ok=False)
        self.assertNotEqual(0, p.returncode, "a file whose names cannot be resolved is an error:\n%s" % p.stdout)
        both = p.stdout + p.stderr
        self.assertNotIn("Traceback (most recent call last)", both, both)
        self.assertIn("not one product name could be resolved", both, both)


class NeverOverwriteAnInput(Base):
    """The real inputs are 160-200 MB client exports that exist nowhere else."""

    def test_json_refuses_to_write_over_the_file_it_reads(self):
        step = self.write("victim.STEP", product(11, MACHINE) + product(12, UNIT) + nauo(8001, 11, 12))
        before = step.read_bytes()
        p = self.run_it(step, "--json", step, expect_ok=False)
        self.assertNotEqual(0, p.returncode, "it must refuse:\n%s\n%s" % (p.stdout, p.stderr))
        self.assertEqual(before, step.read_bytes(), "the input must be byte-identical afterwards")

    def test_json_refuses_to_write_over_the_diff_input(self):
        a = self.write("in_a.STEP", product(11, MACHINE) + product(12, UNIT) + nauo(8001, 11, 12))
        b = self.write("in_b.STEP", product(11, MACHINE) + product(12, UNIT) + nauo(8001, 11, 12))
        before = b.read_bytes()
        p = self.run_it(a, "--diff", b, "--json", b, expect_ok=False)
        self.assertNotEqual(0, p.returncode, "the newer export is an input too:\n%s\n%s" % (p.stdout, p.stderr))
        self.assertEqual(before, b.read_bytes())


class BothDefinitionSpellings(Base):
    """AP214 writes PRODUCT_DEFINITION_WITH_ASSOCIATED_DOCUMENTS for a part that carries documents. The integrity
    check must count the same population it reads, or it warns about a file that parsed perfectly and - worse -
    cancels out a real loss."""

    DOCS = "PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE"

    def mixed(self, name="mixed.STEP", extra_record=""):
        body = (product(11, MACHINE)
                + product(12, UNIT).replace("PRODUCT_DEFINITION (", "PRODUCT_DEFINITION_WITH_ASSOCIATED_DOCUMENTS (")
                + product(13, "Part 02.01.121").replace("PRODUCT_DEFINITION (", "PRODUCT_DEFINITION_WITH_ASSOCIATED_DOCUMENTS (")
                + extra_record + nauo(8001, 11, 12) + nauo(8002, 11, 13))
        return self.write(name, body)

    def test_a_clean_mixed_file_carries_no_warning(self):
        out = self.run_it(self.mixed()).stdout
        self.assertNotIn("WARNING", out, "nothing is wrong with this file:\n%s" % out)
        self.assertEqual(3, len(self.as_json(self.mixed("mixed2.STEP"), "mixed.json")["products"]))

    def test_a_loss_is_not_cancelled_out_by_the_other_spelling(self):
        """The masking case: one bare definition destroyed while a WITH_ASSOCIATED_DOCUMENTS one is read."""
        step = self.mixed("masked.STEP", "#110 = PRODUCT_DEFINITION_SHAPE ( 'x', 'the maker's part', #110 ) ;\n")
        out = self.run_it(step).stdout
        self.assertIn("WARNING", out, "a destroyed record must be reported whatever its neighbours are:\n%s" % out)


class CommentsWithoutApostrophes(Base):
    """A `/* … */` comment is legal anywhere between tokens; it must not cost a record on its own."""

    def test_a_plain_comment_keeps_the_record(self):
        step = self.write("plaincomment.STEP",
                          product(11, MACHINE) + "/* part 2 of 3 */ " + product(12, UNIT)
                          + nauo(8001, 11, 12))
        out = self.run_it(step).stdout
        self.assertNotIn("WARNING", out, "a comment is not a loss:\n%s" % out)
        self.assertEqual({MACHINE_TEXT: 1, UNIT_TEXT: 1},
                         self.as_json(step, "plaincomment.json")["products"])

    def test_a_comment_inside_a_name_is_left_alone(self):
        step = self.write("nameslash.STEP",
                          product(11, MACHINE) + product(12, "Plate /* not a comment */ 02.01.121")
                          + nauo(8001, 11, 12))
        d = self.as_json(step, "nameslash.json")
        self.assertIn("Plate /* not a comment */ 02.01.121", d["products"],
                      "a comment inside a quoted name is part of the name: %r" % (d["products"],))


class StrayApostropheIsCheap(Base):
    """One un-doubled apostrophe used to make the parse quadratic: the documented WARNING was unreachable on a
    real 160-200 MB export because the run never finished."""

    def test_a_multi_megabyte_file_with_a_stray_apostrophe_still_finishes(self):
        filler = "".join("#%d = CARTESIAN_POINT ( 'NONE', ( %f, 0.0, 0.0 ) ) ;\n" % (900000 + i, i)
                         for i in range(60000))                       # ~3 MB
        step = self.write("quad.STEP",
                          product(11, MACHINE)
                          + "#110 = PRODUCT_DEFINITION_SHAPE ( 'x', 'the maker's part', #110 ) ;\n"
                          + filler + product(12, UNIT) + nauo(8001, 11, 12))
        self.assertGreater(step.stat().st_size, 2 << 20, "the fixture has to be big enough to show the cost")
        t0 = time.time()
        out = self.run_it(step).stdout                                 # run_it already caps at 120 s
        took = time.time() - t0
        self.assertLess(took, 20.0, "a stray apostrophe must cost one record, not the file (took %.1f s)" % took)
        self.assertNotIn("Traceback (most recent call last)", out, out)
        # The cap confines the damage: the record holding the apostrophe is lost, the products are not.
        self.assertIn(MACHINE_TEXT, out, "the products before and after it still parse:\n%s" % out)
        self.assertEqual({MACHINE_TEXT: 1, UNIT_TEXT: 1},
                         self.as_json(step, "quad.json")["products"])


class LooseAndOrphans(Base):
    """A definition nothing places, and a usage link whose parent is missing."""

    def test_an_unplaced_definition_is_listed_not_counted(self):
        step = self.write("loose.STEP",
                          product(11, MACHINE) + product(12, UNIT) + product(13, "NOT placed 02.01.900")
                          + nauo(8001, 11, 12))
        out = self.run_it(step).stdout
        self.assertIn("in no assembly", out, "it must be named as unplaced:\n%s" % out)
        d = self.as_json(step, "loose.json")
        self.assertEqual([MACHINE_TEXT], d["roots"], "the only assembly is the machine")
        self.assertEqual(["NOT placed 02.01.900"], d["loose"])
        self.assertNotIn("NOT placed 02.01.900", d["products"], "it sits in no machine: %r" % (d["products"],))

    def test_an_orphaned_link_parent_is_not_called_a_cycle(self):
        step = self.write("orphan.STEP",
                          product(11, MACHINE) + product(12, UNIT) + product(13, "Part B 02.01.122")
                          + nauo(8001, 11, 12) + nauo(8002, 99, 13))
        out = self.run_it(step).stdout
        self.assertIn("not in the file", out, "say what is actually wrong:\n%s" % out)
        self.assertNotIn("form a cycle", out, "there is no cycle here:\n%s" % out)

    def test_a_nameless_definition_is_reported_and_not_a_product(self):
        step = self.write("nameless.STEP",
                          product(11, MACHINE) + product(12, "") + nauo(8001, 11, 12))
        out = self.run_it(step).stdout
        self.assertIn("no resolvable product name", out, out)
        self.assertEqual({MACHINE_TEXT: 1}, self.as_json(step, "nameless.json")["products"],
                         "an empty name is not a product row")


class TreeBudget(Base):
    """The tree enumerates paths, so a shared assembly is unbounded; the cap must be visible, not silent."""

    def test_a_shared_assembly_is_cut_and_said_so(self):
        """A diamond at every level: two distinct sub-assemblies both place the next level, so the number of
        root-to-leaf PATHS doubles per level while the number of products grows by two. 17 levels = 131072 paths
        out of 36 products - which is why the tree needs a budget and the counts do not."""
        n = 17
        body = "".join(product(20 + i, "Level %02d 02.01.%03d" % (i, i)) for i in range(n + 1))
        body += "".join(product(60 + 2 * i, "Left %02d 02.02.%03d" % (i, i))
                        + product(61 + 2 * i, "Right %02d 02.03.%03d" % (i, i)) for i in range(n))
        for i in range(n):
            body += (nauo(9000 + 4 * i, 20 + i, 60 + 2 * i) + nauo(9001 + 4 * i, 20 + i, 61 + 2 * i)
                     + nauo(9002 + 4 * i, 60 + 2 * i, 21 + i) + nauo(9003 + 4 * i, 61 + 2 * i, 21 + i))
        step = self.write("budget.STEP", body)
        d = self.as_json(step, "budget.json")
        out = self.run_it(step, "--json", self.tmp / "budget2.json").stdout
        self.assertIn("tree was cut", out, "the cap must be announced:\n%s" % out)

        def count(node):
            return 1 + sum(count(c) for c in node["children"])
        self.assertLessEqual(count(d["tree"]), 50000, "the cap is exact, not approximate")
        self.assertEqual(2 ** n, d["products"]["Level %02d 02.01.%03d" % (n, n)],
                         "the placement count is arithmetic over the graph, so it is exact even when the tree is cut")


class DrawingPattern(unittest.TestCase):
    """What may and may not pass as a drawing number, and that the two copies of the pattern stay equal."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("step_struct_under_test", str(SCRIPT))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_real_drawing_numbers_match(self):
        for name, want in (("%s 02.01.121 %s" % (RU["code"], RU["plate"]), "02.01.121"),
                           ("Bracket 01.02.03.456", "01.02.03.456"),
                           ("ABC 02.01.121-01 Plate", "02.01.121"),
                           ("Plate (02.01.121)", "02.01.121")):
            self.assertEqual(want, self.mod.drawing_of(name), name)

    def test_names_that_only_look_like_one(self):
        for name in ("%s rev 20.10.2026" % RU["plate"], "Opora 12.05.1980", "profile 40.40.1500",
                     "02.01.1210", "ABC 02.01 Plate", "x1.02.01.456y"):
            self.assertIsNone(self.mod.drawing_of(name),
                              "%r must not pass as a drawing number (got %r)" % (name, self.mod.drawing_of(name)))

    def test_the_two_copies_of_the_pattern_are_identical(self):
        """step_import.py cannot be imported here (it needs 3.12+ to parse), so its pattern is read from the source."""
        src = IMPORTER.read_text(encoding="utf-8")
        m = re.search(r'^DRAWING = re\.compile\(r"([^"]+)"\)', src, re.M)
        self.assertIsNotNone(m, "DRAWING not found in step_import.py as a single-line re.compile(r\"…\")")
        self.assertEqual(self.mod.DRAWING.pattern, m.group(1),
                         "the drawing-number pattern must be the same in step_struct.py and step_import.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
