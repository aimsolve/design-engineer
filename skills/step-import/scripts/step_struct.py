#!/usr/bin/env python3
"""Assembly structure of a STEP file, read from its TEXT: products, how many times each sits in the machine, the
assembly tree, and a re-export report between two exports of the same machine. Standard library only - no OCP,
no meshing.

  python3 step_struct.py FILE.STEP                 # summary: counts, top-level products, how the export identifies itself
  python3 step_struct.py FILE.STEP --json OUT.json # {"root":…, "roots": […], "products": {name: placements}, "tree": {…}}
  python3 step_struct.py OLD.STEP --diff NEW.STEP  # what changed: gone / new / placement counts / renames
                                                   #   (with --json too: that report as JSON)

`step_import.py` answers the same question but needs OCP, ~7 GB of RAM and a minute per import because it meshes
every solid; this reads a 199 MB machine STEP in ~8 s, so it is the thing to run when the client sends a new
export and the question is only «what moved». Names, drawing numbers and the placement counts come out
the way `step_import.py` sees them, so the two agree on what a part is called and how many of it there are - with
one exception: `step_import.py` imports the FIRST top-level shape of the file, so on a file holding several
assemblies (two production lines) the counts agree only for the one it picked. This tool reports them all.

Names: `\\X2\\<utf-16be hex>\\X0\\`, `\\X\\<byte>` (ISO 8859-1) and `''` for one apostrophe are decoded. `\\S\\`
(alternate code page) is not. `/* … */` comments are not stripped, and an apostrophe inside one - or any unbalanced
apostrophe - makes the records after it unreadable; that is counted against the entity starts found in the raw text
and printed as a WARNING, never passed off as a complete report.
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sys

CHUNK = 1 << 22
PENDING_MAX = 512            # `;`-fragments one record may absorb before we give up on it and warn
TREE_BUDGET = 50000          # nodes; the tree enumerates paths, so a heavily shared DAG can blow up
# A product is a drawing number plus a name: two or three dot-separated 2-digit groups and a 3-digit group.
# Four groups: ABC-1 01.02.03.456; three: ABC 02.01.121 (makers number both ways). The digit guards keep
# a date (20.10.2026) and a longer run (02.01.1210) from passing as a drawing number.
DRAWING = re.compile(r"(?<![\d.])\d{2}\.\d{2}(?:\.\d{2})?\.\d{3}(?!\d)(?!\.\d)")
ENTITY = re.compile(rb"^#(\d+)\s*=\s*([A-Z_0-9]+)\s*\((.*)\)\s*$", re.S)
# The entity starts we must end up having read, counted in the raw text: the integrity check against a parse that
# silently lost records. Both sides are keyed by the entity's own spelling, so a definition variant cannot be
# counted as found under one name and read under another (that masked a real loss). `PRODUCT` must not match
# PRODUCT_DEFINITION_SHAPE, PRODUCT_CONTEXT and friends.
START = re.compile(rb"#\d+\s*=\s*(PRODUCT_DEFINITION_WITH_ASSOCIATED_DOCUMENTS|PRODUCT_DEFINITION(?![_A-Z])"
                   rb"|PRODUCT(?![_A-Z])|NEXT_ASSEMBLY_USAGE_OCCURRENCE)\s*\(")
KEEP = (b"PRODUCT", b"NEXT_ASSEMBLY_USAGE_OCCURRENCE")
DEFINITION_KINDS = ("PRODUCT_DEFINITION", "PRODUCT_DEFINITION_WITH_ASSOCIATED_DOCUMENTS")
ESCAPE = re.compile(rb"\\X2\\([0-9A-Fa-f]+)\\X0\\|\\X\\([0-9A-Fa-f]{2})")
# A comment is legal anywhere between tokens and is stripped from a complete record. Quoted strings are matched
# first, so a `/*` inside a product name is left alone.
RECORD_TOKEN = re.compile(rb"'(?:[^']|'')*'|/\*.*?\*/", re.S)


def _strip_comments(rec):
    return RECORD_TOKEN.sub(lambda m: b"" if m.group(0)[:2] == b"/*" else m.group(0), rec)


def _open_string(b):
    """True if `b` ends inside a quoted string: a complete record holds an even number of quotes, and `''` (an
    escaped apostrophe) contributes none."""
    return (b.count(b"'") - 2 * b.count(b"''")) % 2 == 1


def records(path):
    """Every `#id = ENTITY(...)` record of interest, as bytes, then a final `{kind: how many entity starts the raw
    text holds}` so the caller can prove it read them all.

    A record may span lines, so the file is split on `;` - but a `;` inside a quoted product name is not a record
    end, so a fragment that leaves a string open is joined with the next one."""
    buf, seen = b"", collections.Counter()
    pending, pend_open = None, False      # fragments of a record whose quoted string is still open
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            parts = (buf + chunk).split(b";")
            buf = parts.pop()
            for part in parts:
                for m in START.finditer(part):
                    seen[m.group(1).decode()] += 1
                if pending is not None:
                    pending.append(part)
                    pend_open ^= _open_string(part)    # parity of the join is the parity of the parts
                    if pend_open and len(pending) < PENDING_MAX:
                        continue
                    rec, gave_up, pending, pend_open = b";".join(pending), pend_open, None, False
                    if gave_up:
                        continue            # unbalanced apostrophe: the START counts report what was lost
                elif _open_string(part):
                    pending, pend_open = [part], True
                    continue
                else:
                    rec = part
                rec = _strip_comments(rec).strip() if b"/*" in rec else rec.strip()
                if rec.startswith(b"#") and any(k in rec for k in KEEP):
                    if ENTITY.match(rec):
                        yield rec
                    # a record that does not match is accounted for by the START count, not silently dropped
    for m in START.finditer(buf):
        seen[m.group(1).decode()] += 1
    yield dict(seen)


def unescape(raw):
    """A STEP string literal (without its quotes) as text."""
    out, pos = [], 0
    for m in ESCAPE.finditer(raw):
        out.append(raw[pos:m.start()].decode("utf-8", "replace"))
        try:
            if m.group(1) is not None:
                out.append(bytes.fromhex(m.group(1).decode()).decode("utf-16-be", "replace"))
            else:
                out.append(bytes.fromhex(m.group(2).decode()).decode("latin-1"))
        except ValueError:
            out.append(m.group(0).decode("utf-8", "replace"))      # a malformed escape stays as it was written
        pos = m.end()
    out.append(raw[pos:].decode("utf-8", "replace"))
    return "".join(out).replace("''", "'")


def split_args(args):
    """The top-level arguments of a record, as bytes, honouring quotes (where `''` is an escaped apostrophe) and
    nested parentheses."""
    out, depth, cur, quoted = [], 0, bytearray(), False
    i = 0
    while i < len(args):
        c = args[i:i + 1]
        cur += c
        if quoted:
            if c == b"'":
                if args[i + 1:i + 2] == b"'":
                    cur += b"'"
                    i += 1
                else:
                    quoted = False
        elif c == b"'":
            quoted = True
        elif c == b"(":
            depth += 1
        elif c == b")":
            depth -= 1
        elif c == b"," and depth == 0:
            out.append(bytes(cur[:-1]).strip())
            cur = bytearray()
        i += 1
    out.append(bytes(cur).strip())
    return out


def _text(arg):
    return unescape(arg[1:-1]) if arg.startswith(b"'") and arg.endswith(b"'") else None


def _ref(arg):
    return int(arg[1:]) if arg.startswith(b"#") else None


def _product_name(args):
    """PRODUCT ( id, name, description, frame_of_reference ): the name, falling back to the id when it is empty,
    which is the order STEPCAFControl_Reader (and so step_import.py) resolves it in."""
    name = _text(args[1]) if len(args) > 1 else None
    return (name or _text(args[0])) or None


def parse(path):
    """(name by product-definition id, [(parent id, child id), ...], {kind: entity starts read of those found})."""
    product, formation, definition, links = {}, {}, {}, []
    read = collections.Counter()
    found = {}
    for r in records(path):
        if isinstance(r, dict):
            found = r
            break
        m = ENTITY.match(r)
        kind, args = m.group(2).decode(), split_args(m.group(3))
        eid = int(m.group(1))
        if kind == "PRODUCT":
            product[eid] = _product_name(args)
            read[kind] += 1
        elif kind.startswith("PRODUCT_DEFINITION_FORMATION") and len(args) > 2:
            formation[eid] = _ref(args[2])          # AP214 writes …_FORMATION_WITH_SPECIFIED_SOURCE
        elif kind in DEFINITION_KINDS and len(args) > 2:
            definition[eid] = _ref(args[2])
            read[kind] += 1
        elif kind == "NEXT_ASSEMBLY_USAGE_OCCURRENCE" and len(args) > 4:
            links.append((_ref(args[3]), _ref(args[4])))
            read[kind] += 1
    names = {d: product.get(formation.get(f)) for d, f in definition.items()}
    missed = {k: (v, read.get(k, 0)) for k, v in found.items() if v != read.get(k, 0)}
    return names, links, missed


class Struct(object):
    """What one export says about itself: names, placement counts and the assembly tree."""

    def __init__(self, path):
        self.path = path
        self.names, self.links, self.missed = parse(path)
        if not self.names:
            raise ValueError("%s: no PRODUCT_DEFINITION in the file - it carries geometry but no product structure"
                             % os.path.basename(path))
        if not any(self.names.values()):
            raise ValueError("%s: %d product definitions but not one product name could be resolved - the "
                             "PRODUCT_DEFINITION -> FORMATION -> PRODUCT chain was not recognised"
                             % (os.path.basename(path), len(self.names)))
        self.children = collections.OrderedDict()
        child_ids = set()
        for a, b in self.links:
            self.children.setdefault(a, []).append(b)
            child_ids.add(b)
        self.orphan_links = sum(1 for a, _ in self.links if a not in self.names)
        tops = [d for d in self.names if d not in child_ids]
        self.roots = sorted((d for d in tops if self.children.get(d)),
                            key=lambda d: (-self._reach(d), str(self.names.get(d))))
        # A definition with no parent and no children is in the file but in no machine (a suppressed or library
        # component the CAD left behind). step_import.py never sees it; it is listed, not counted as placed.
        self.loose = sorted((self.names.get(d) for d in tops if not self.children.get(d)), key=str)
        if not self.roots and tops:          # only loose products: the largest of them is all there is
            self.roots = sorted(tops, key=lambda d: str(self.names.get(d)))[:1]
            self.loose = [n for n in self.loose if n != self.names.get(self.roots[0])]
        self.nameless = sum(1 for v in self.names.values() if not v)
        self.placements = self._placements()
        self.truncated = False
        self.instances = collections.Counter()
        for d, mult in self.placements.items():
            if mult:                      # 0 = in the file but in no machine (loose, or cut out by a cycle)
                self.instances[self.names.get(d)] += mult
        for blank in (None, ""):
            self.instances.pop(blank, None)

    def _reach(self, start):
        """How many product definitions hang under `start`, itself included: the size of an assembly."""
        seen, stack = {start}, [start]
        while stack:
            for c in self.children.get(stack.pop(), ()):
                if c not in seen:
                    seen.add(c)
                    stack.append(c)
        return len(seen)

    def _placements(self):
        """How many times each product definition sits in the machine. A sub-assembly placed twice places
        everything inside it twice, which is what `step_import.py` counts as instances; a bare usage-link count
        would say «once» for every part of it."""
        edges = collections.Counter(self.links)
        succ, indeg = collections.defaultdict(list), collections.Counter()
        for (a, b), k in edges.items():
            succ[a].append((b, k))
            indeg[b] += 1
        mult = dict.fromkeys(self.names, 0)
        for d in self.roots:
            mult[d] = 1
        queue = collections.deque(self.roots)
        done = 0
        while queue:
            a = queue.popleft()
            done += 1
            for b, k in succ.get(a, ()):
                if b in mult:
                    mult[b] += mult.get(a, 0) * k
                indeg[b] -= 1
                if indeg[b] == 0:
                    queue.append(b)
        # Everything the walk could not reach: a real reference cycle, or a child whose parent is not in the file.
        self.cyclic = done < len(self.names) and not self.orphan_links
        return mult

    @property
    def root(self):
        """The largest assembly in the file. A client file can hold several (two production lines side by side),
        so `roots` is the whole list."""
        return self.names.get(self.roots[0]) if self.roots else None

    def tree(self, did=None):
        """One node per distinct product under each parent; a sub-assembly reached by two paths is expanded twice,
        and a reference cycle is cut at the repeat. Capped at TREE_BUDGET nodes, because enumerating paths through
        a heavily shared assembly is unbounded; the cap sets `truncated` and marks the node it stopped at."""
        budget = [TREE_BUDGET]

        def node(d, seen):
            budget[0] -= 1                   # checked before a child is made, so the cap is exact
            name = self.names.get(d)
            out = {"name": name, "drawing": drawing_of(name), "children": []}
            if d in seen:
                out["cut"] = True            # a reference cycle: the repeat is not expanded again
                return out
            for child in dict.fromkeys(self.children.get(d, ())):
                if budget[0] <= 0:
                    self.truncated = True
                    out["truncated"] = True
                    break
                out["children"].append(node(child, seen | {d}))
            return out

        if did is None:
            if not self.roots:
                return None
            did = self.roots[0]
        try:
            return node(did, frozenset())
        except RecursionError:               # a chain thousands of levels deep; the counts above still stand
            self.truncated = True
            return {"name": self.names.get(did), "drawing": drawing_of(self.names.get(did)), "children": [],
                    "truncated": True}

    def as_json(self):
        return {"root": self.root, "roots": [self.names.get(d) for d in self.roots], "loose": self.loose,
                "placements": sum(self.instances.values()), "products": dict(self.instances), "tree": self.tree()}

    def summary(self, label="", tree_done=False):
        lines = ["%s%s" % (label, os.path.basename(self.path)),
                 "  product_definitions %d | distinct product names %d | next_assembly_usage_occurrence links %d"
                 % (len(self.names), len(set(v for v in self.names.values() if v)), len(self.links)),
                 "  root: %s" % self.root]
        if len(self.roots) > 1:
            shown = [str(self.names.get(d)) for d in self.roots[:8]]
            lines.append("  %d top-level products (no parent assembly), largest first: %s%s"
                         % (len(self.roots), ", ".join(shown), " …" if len(self.roots) > 8 else ""))
            lines.append("  NOTE step_import.py imports only the first top-level shape, so its instance counts "
                         "cover one of these, not all")
        for kind, (found, got) in sorted(self.missed.items()):
            lines.append("  WARNING only %d of the %d %s records in the file could be read - this report is "
                         "incomplete (an unbalanced apostrophe or a /* */ comment containing one does this)"
                         % (got, found, kind))
        if self.loose:
            lines.append("  %d product definitions are in the file but in no assembly: %s%s"
                         % (len(self.loose), ", ".join(str(n) for n in self.loose[:8]),
                            " …" if len(self.loose) > 8 else ""))
        if self.orphan_links:
            lines.append("  WARNING %d usage links point at a parent product definition that is not in the file"
                         % self.orphan_links)
        if self.nameless:
            lines.append("  WARNING %d product definitions have no resolvable product name" % self.nameless)
        if self.cyclic:
            lines.append("  WARNING the assembly references form a cycle: some products are counted 0 times")
        if not self.roots:
            lines.append("  WARNING every product is a child of another: there is no top-level assembly and no tree")
        if tree_done and self.truncated:
            lines.append("  WARNING the tree was cut at %d nodes" % TREE_BUDGET)
        lines.append("  %s" % identity(self.path))
        return lines


def drawing_of(name):
    m = DRAWING.search(name or "")
    return m.group(0) if m else None


def identity(path):
    """How this export identifies itself: the sha256 of the bytes and what the exporter wrote in the header. The
    client's file name never changes, so this is the only way to tell two exports apart."""
    h = hashlib.sha256()
    head = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            if len(head) < CHUNK:
                head += chunk
            h.update(chunk)
    stamp = re.search(rb"FILE_NAME\s*\((.{0,100000}?)\)\s*;", head, re.S)
    when = who = "?"
    if stamp:
        args = split_args(stamp.group(1))
        when = (_text(args[1]) if len(args) > 1 else None) or "?"
        who = " ".join(x for x in (_text(args[4]) if len(args) > 4 else None,
                                   _text(args[5]) if len(args) > 5 else None) if x) or "?"
    out = "sha256 %s | %d bytes | exported %s by %s" % (h.hexdigest()[:16], os.path.getsize(path), when, who)
    return out if when != "?" or who != "?" else out + " (no readable FILE_NAME in the header)"


def _renames(gone, added):
    """(drawing number, old name, new name) for a number that one name left and one name arrived under. Several on
    either side cannot be paired up, so they are reported as a group instead of as a cross product."""
    out = []
    by_drawing = {}
    for n in added:
        d = drawing_of(n)
        if d:
            by_drawing.setdefault(d, []).append(n)
    left = {}
    for n in gone:
        d = drawing_of(n)
        if d and d in by_drawing:
            left.setdefault(d, []).append(n)
    for d in sorted(left):
        olds, news = left[d], by_drawing[d]
        if len(olds) == 1 and len(news) == 1:
            out.append((d, olds[0], news[0]))
        else:
            out.append((d, " / ".join(olds), " / ".join(news) + "   (ambiguous: %d -> %d)" % (len(olds), len(news))))
    return out


def diff(old, new):
    """(lines of the re-export report, the same report as a dict)."""
    a, b = old.instances, new.instances
    kept = sorted(set(a) & set(b), key=str)
    gone = sorted(set(a) - set(b), key=str)
    added = sorted(set(b) - set(a), key=str)
    moved = sorted(((n, a[n], b[n]) for n in kept if a[n] != b[n]), key=lambda t: str(t[0]))
    renames = _renames(gone, added)

    lines = old.summary("old  ") + new.summary("new  ")
    lines.append("")
    lines.append("product names in both exports: %d (old %d, new %d); placements in all: old %d, new %d"
                 % (len(kept), len(a), len(b), sum(a.values()), sum(b.values())))
    lines.append("gone from the new export (%d):" % len(gone))
    lines += ["  - %s" % n for n in gone] or ["  (none)"]
    lines.append("new in it (%d):" % len(added))
    lines += ["  + %s" % n for n in added] or ["  (none)"]
    lines.append("kept, placed a different number of times (%d):" % len(moved))
    lines += ["  %d -> %d  %s" % (x, y, n) for n, x, y in moved] or ["  (none)"]
    lines.append("renamed under one drawing number (%d):" % len(renames))
    lines += ["  %s  %s -> %s" % (d, n, m) for d, n, m in renames] or ["  (none)"]
    report = {"old": {"file": os.path.basename(old.path), "root": old.root, "products": len(a),
                      "placements": sum(a.values())},
              "new": {"file": os.path.basename(new.path), "root": new.root, "products": len(b),
                      "placements": sum(b.values())},
              "in_both": len(kept), "gone": gone, "new_names": added,
              "placements_changed": [{"name": n, "old": x, "new": y} for n, x, y in moved],
              "renamed": [{"drawing": d, "old": n, "new": m} for d, n, m in renames]}
    return lines, report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("step", help="the STEP file to read (the old export when --diff is given)")
    ap.add_argument("--diff", metavar="NEW.STEP", help="report what changed in this newer export of the same machine")
    ap.add_argument("--json", metavar="OUT.json",
                    help="also write the report as JSON: the structure of STEP, or the diff when --diff is given")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")       # product names are Cyrillic whatever the locale says
    except (AttributeError, ValueError):
        pass
    if a.json:                                         # a client export cannot be regenerated: never write over one
        inputs = {os.path.realpath(p) for p in (a.step, a.diff) if p}
        if os.path.realpath(a.json) in inputs:
            sys.exit("[step_struct] --json %s is one of the files being read; refusing to overwrite it" % a.json)
        where = os.path.dirname(os.path.abspath(a.json))
        if not os.path.isdir(where):                   # before the parse, not after the report is printed
            sys.exit("[step_struct] --json %s: %s is not a directory" % (a.json, where))
    try:
        here = Struct(a.step)
        other = Struct(a.diff) if a.diff else None
    except (IOError, OSError) as e:
        sys.exit("[step_struct] cannot read %s: %s" % (e.filename or a.step, e.strerror or e))
    except ValueError as e:
        sys.exit("[step_struct] %s" % e)
    if other:
        lines, payload = diff(here, other)
    else:
        payload = here.as_json() if a.json else None   # the tree is only built when something asks for it
        lines = here.summary(tree_done=bool(a.json))
    print("\n".join(lines))
    if a.json:
        try:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
        except (IOError, OSError) as e:
            sys.exit("[step_struct] cannot write %s: %s" % (a.json, e.strerror or e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
