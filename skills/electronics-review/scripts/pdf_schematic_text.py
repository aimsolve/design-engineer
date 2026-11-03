#!/usr/bin/env python3
"""Pull everything a vector PDF schematic knows as TEXT, before anyone looks at pixels.

  python pdf_schematic_text.py sch.pdf -o review/sch [--dpi 400] [--crop U3,U5] [--radius 160] [--no-tiles]

Writes into -o:
  words.json      every word: page, text, bbox (PDF points, origin top-left)
  labels.md       per page: raster/vector, refdes list, net-label-ish tokens (with pages + nearest refdes),
                  and labels seen on several pages (same label = same net in most EDA exports)
  pages/pN.png    full page at --dpi
  tiles/pN_rR_cC.png   ~1400 px tiles, 15 % overlap (for vision, one tile at a time)
  crops/<REF>_pN.png   square crop around each --crop refdes (radius in PDF points)

A page with no extractable words is raster: vision only, tag findings `image`.
Needs: pymupdf (`pip install pymupdf`; on the platform it is in the worker image).
"""
import argparse, json, re, sys
from collections import defaultdict
from pathlib import Path

import pymupdf  # PyMuPDF >= 1.24

REFDES = re.compile(r"^(U|IC|R|C|L|D|Q|J|P|CN|X|Y|FB|F|K|SW|T|TP|RN|LED)\d{1,4}[A-Z]?$")
# net-label-ish: power/ground names, pin-function names, bus-like names, signed rails
NETISH = re.compile(
    r"^(\+|-)?\d+(\.\d+)?V\d*$|^[+-]?V(CC|DD|SS|EE|IN|OUT|REF|BAT|MOT|M)\w*$|^A?GND\w*$|^[DAP]GND$|^(A|D)V(DD|SS)$"
    r"|^REF[PN]\d?$|^AIN\d$|^EXC\S*$|^(DRDY|CS|SCLK|SCK|MOSI|MISO|DIN|DOUT|SDA|SCL|TX|RX|EN|STEP|DIR|NRST|RESET)\w*$"
    r"|^[A-Z][A-Z0-9_]{2,}[+-]?$|^\w+\[\d+(:\d+)?\]$", re.I)


def page_words(page):
    return [dict(page=page.number + 1, text=w[4], bbox=[round(v, 1) for v in w[:4]])
            for w in page.get_text("words")]


def centre(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def nearest(ref_words, b, maxd=80):
    cx, cy = centre(b)
    best, bd = None, maxd
    for r in ref_words:
        rx, ry = centre(r["bbox"])
        d = ((rx - cx) ** 2 + (ry - cy) ** 2) ** 0.5
        if d < bd:
            best, bd = r["text"], d
    return best


def render(page, dpi, out):
    pix = page.get_pixmap(dpi=dpi)
    pix.save(out)
    return pix


def tiles(page, dpi, outdir, tile=1400, overlap=0.15):
    pix = page.get_pixmap(dpi=dpi)
    step = int(tile * (1 - overlap))
    zoom = dpi / 72
    n = 0
    for r, y in enumerate(range(0, max(pix.height - int(tile * overlap), 1), step)):
        for c, x in enumerate(range(0, max(pix.width - int(tile * overlap), 1), step)):
            clip = pymupdf.Rect(x / zoom, y / zoom, min(x + tile, pix.width) / zoom, min(y + tile, pix.height) / zoom)
            page.get_pixmap(dpi=dpi, clip=clip).save(outdir / f"p{page.number + 1}_r{r}_c{c}.png")
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--dpi", type=int, default=400)
    ap.add_argument("--crop", default="", help="comma-separated refdes to crop around, e.g. U3,U5")
    ap.add_argument("--radius", type=float, default=160, help="crop half-size in PDF points (72 pt = 1 inch)")
    ap.add_argument("--no-tiles", action="store_true")
    a = ap.parse_args()

    out = Path(a.out)
    for d in ("pages", "tiles", "crops"):
        (out / d).mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(a.pdf)
    want = {s.strip().upper() for s in a.crop.split(",") if s.strip()}

    all_words, md = [], [f"# {Path(a.pdf).name}: text extracted from the PDF\n",
                          "Evidence tag for anything below: `pdf-text`. Labels are guesses by pattern; "
                          "same label on two sheets = same net only if it is a net label, not a pin name.\n"]
    label_pages = defaultdict(set)
    ntiles = 0
    for page in doc:
        pn = page.number + 1
        words = page_words(page)
        all_words += words
        render(page, a.dpi, out / "pages" / f"p{pn}.png")
        if not a.no_tiles:
            ntiles += tiles(page, a.dpi, out / "tiles")
        n_draw = len(page.get_drawings())
        if not words:
            md.append(f"\n## Page {pn}: RASTER (no text layer, {n_draw} vector paths) — vision only, tag `image`\n")
            continue
        refs = [w for w in words if REFDES.match(w["text"])]
        nets = [w for w in words if NETISH.match(w["text"]) and not REFDES.match(w["text"])]
        md.append(f"\n## Page {pn}: vector, {len(words)} words, {n_draw} vector paths\n")
        md.append("**Refdes:** " + ", ".join(sorted({r["text"] for r in refs}, key=lambda s: (re.sub(r"\d.*", "", s),
                  int(re.sub(r"\D", "", s) or 0)))) + "\n")
        md.append("\n| label | x,y (pt) | nearest refdes |\n|---|---|---|")
        for w in sorted(nets, key=lambda w: w["text"]):
            label_pages[w["text"]].add(pn)
            cx, cy = centre(w["bbox"])
            md.append(f"| `{w['text']}` | {cx:.0f},{cy:.0f} | {nearest(refs, w['bbox']) or ''} |")
        for r in refs:
            if r["text"].upper() in want:
                cx, cy = centre(r["bbox"])
                clip = pymupdf.Rect(cx - a.radius, cy - a.radius, cx + a.radius, cy + a.radius) & page.rect
                page.get_pixmap(dpi=a.dpi, clip=clip).save(out / "crops" / f"{r['text']}_p{pn}.png")

    multi = {k: sorted(v) for k, v in label_pages.items() if len(v) > 1}
    md.append("\n## Labels on more than one page (candidate cross-sheet nets)\n")
    md += [f"- `{k}`: pages {', '.join(map(str, v))}" for k, v in sorted(multi.items())] or ["- none"]
    missing = want - {w["text"].upper() for w in all_words}
    if missing:
        md.append(f"\n**Crop refdes not found in text:** {', '.join(sorted(missing))} (raster page or different name)")

    (out / "words.json").write_text(json.dumps(all_words, ensure_ascii=False))
    (out / "labels.md").write_text("\n".join(md) + "\n")
    print(f"{len(doc)} pages, {len(all_words)} words, {ntiles} tiles, crops: {len(list((out / 'crops').glob('*.png')))}"
          f" -> {out}/labels.md")


if __name__ == "__main__":
    sys.exit(main())
