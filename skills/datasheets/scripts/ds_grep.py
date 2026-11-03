#!/usr/bin/env python3
"""Grep a datasheet PDF and print hits ready to cite: [doc-id rev p.N §section] + the line.

  python ds_grep.py datasheets/ads1220_SBAS501D.pdf "noise|µVrms" [--context 1] [--max 40]
  python ds_grep.py datasheets/ads1220_SBAS501D.pdf --toc          # section → page map

doc-id / revision are guessed from page 1 (TI "SBAS501D", ADI "Rev. B", ST "DS12345 Rev 7"); check them once by eye.
Section = the last PDF bookmark (TOC entry) starting on or before that page — approximate on pages with two sections.
Page numbers are PDF pages (1-based); if the printed page number differs, say which one you cite.
Needs pymupdf.
"""
import argparse, re, sys

import pymupdf

ID_PATTERNS = [
    r"\b(S[A-Z]{3}\d{3}[A-Z]?)\b",            # TI literature number: SBAS501D, SLYT499, SBAA532A
    r"\b(DS\d{4,6})\b.*?\b(Rev\.?\s*\d+)",      # ST
    r"\b(Rev(?:ision)?\.?\s*[A-Z0-9.]+)\b",     # ADI / generic revision
]


def guess_id(doc):
    first = doc[0].get_text() + (doc[len(doc) - 1].get_text() if len(doc) > 1 else "")
    found = []
    for p in ID_PATTERNS:
        m = re.search(p, first)
        if m:
            found.append(" ".join(g for g in m.groups() if g))
    return " / ".join(dict.fromkeys(found)) or "doc-id?"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("regex", nargs="?")
    ap.add_argument("--context", type=int, default=0, help="lines of context around each hit")
    ap.add_argument("--max", type=int, default=60)
    ap.add_argument("--toc", action="store_true")
    a = ap.parse_args()
    doc = pymupdf.open(a.pdf)
    toc = doc.get_toc()  # [level, title, page]
    did = guess_id(doc)
    print(f"# {a.pdf}: {len(doc)} pages, id guess: {did}, {len(toc)} bookmarks")
    if a.toc:
        for lvl, title, page in toc:
            print(f"{'  ' * (lvl - 1)}{title}  p.{page}")
        return
    if not a.regex:
        ap.error("regex required (or --toc)")
    rx = re.compile(a.regex, re.I)
    n = 0
    for page in doc:
        pn = page.number + 1
        sec = next((t for l, t, p in reversed(toc) if p <= pn), "")
        lines = page.get_text().split("\n")
        for i, line in enumerate(lines):
            if rx.search(line):
                ctx = " ⏎ ".join(l.strip() for l in lines[max(0, i - a.context): i + a.context + 1])
                print(f"[{did} p.{pn}{' §' + sec if sec else ''}] {ctx}")
                n += 1
                if n >= a.max:
                    print(f"... stopped at --max {a.max}")
                    return
    if not n:
        print("no hits (scanned PDF? try OCR, or a different spelling: µ vs u, − vs -)")


if __name__ == "__main__":
    sys.exit(main())
