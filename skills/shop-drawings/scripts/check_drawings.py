"""Check a shop-drawing set against the sheet-metal standard (see ../SKILL.md). FAIL per part when:
  - the part has not all three files with ONE name: <name>.dxf + <name>.pdf + <name>.step;
  - the DXF is not laser-clean for the shop's laser software (laser_dxf.check: AC1015, ASCII, layer 0, closed LWPOLYLINE + CIRCLE only);
  - the PDF has no real text (text drawn as curves: nothing can be verified -> regenerate with shop_drawing.py or export with text);
  - no material grade, or no sheet thickness (a thickness word / s = N / t N / «N *» on a view), or the thickness differs from
    the kit folder "<t> <mm> ...";
  - a BENT part (DXF has entities on layer BEND, or the generator's bend count says so) without the inner bend radius (R N near a
    bend word or in the bend label), without the bend direction (up / down) or without a flat-pattern sheet;
  - the DXF blank has holes but the PDF lacks a datum (ORIGIN / 0,0) or lacks the X / Y distance from the blank's min corner
    to ANY round hole centre (= 0,0 of the DXF, ±0.06 mm);
  - a bent part without «**» theoretical flat-pattern dims; a part with holes without the hole sheet (a page after the first with
    the «X, mm» / «Y, mm» table);
  - the blank's overall size (DXF OUTLINE extents) is not on the PDF.
The words on a drawing are recognised in every language of drawings_i18n.RECOGNISE (a set drawn by hand in KOMPAS / SolidWorks
passes too); the reasons are printed in the project's language (--lang overrides).
Usage: python check_drawings.py [--lang ru|en] KIT_DIR_or_files...   Last line: CHECK: n ok, k fail. Exit 1 on any fail.
Needs ezdxf + pymupdf.
"""
import re
import sys
from pathlib import Path

import ezdxf
import pymupdf
from ezdxf import bbox as xbb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import drawings_i18n as I  # noqa: E402
import laser_dxf  # noqa: E402

LANG = I.DEFAULT         # the language of the reasons; main() sets the project's
NUM = r"(\d+(?:[.,]\d+)?)"
MATERIAL = re.compile(I.recognise("material"), re.I)
THICK = re.compile(r"(?:(?i:" + I.recognise("thickness") + r")|\b[st]\s*=|\b[st]\s(?=\d))\s*" + NUM
                   + r"|(?<![\d.,])" + NUM + r"\s?\*(?!\*)")   # s / t lower-case: S235 is a grade; «1 *» = thickness on a view
BEND_WORD = I.recognise("bend_word")
BEND_LABEL = re.compile(I.recognise("bend_label"))
BEND_R = re.compile(r"(?:(?:" + I.recognise("bend_label") + r")[\s\S]{0,60}?\bR\s*" + NUM + r")"
                    r"|(?:\bR\s*=?\s*" + NUM + r"[^\n]{0,40}(?:" + BEND_WORD + r"))"
                    r"|(?:(?:" + BEND_WORD + r")[^\n]{0,40}?\bR\s*=?\s*" + NUM + ")", re.I)
DIRECTION = re.compile(I.recognise("direction"), re.I)
FLAT = re.compile(I.recognise("flat"), re.I)
THEOR = re.compile(r"\d\s?\*\*")
TABLE = re.compile(I.recognise("table"))
DATUM = re.compile(r"\b0\s*,\s*0\b|" + I.recognise("datum"), re.I)
PERFORATION = re.compile(r"Ø" + NUM + r":\s*\d+\s*(?:" + I.recognise("perforation") + ")", re.I)
FOLDER_T = re.compile(NUM + r"\s*(?:" + I.recognise("mm") + ")")


def _f(x):
    return f"{x:.2f}".rstrip("0").rstrip(".") or "0"


def numbers(text):
    return [float(x.replace(",", ".")) for x in re.findall(r"(?<![\w.])\d+(?:[.,]\d+)?", text)]


def has_num(nums, v, tol=0.06):
    return any(abs(n - v) <= tol for n in nums)


def dxf_facts(path):
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    bend = len(msp.query('*[layer=="BEND"]')) > 0
    out = msp.query('*[layer=="OUTLINE"]') or msp.query("LINE ARC CIRCLE LWPOLYLINE POLYLINE SPLINE")
    ext = xbb.extents(out, fast=False)
    x0, y0 = ext.extmin.x, ext.extmin.y
    skip = {"BEND", "NOTE", "ENGRAVE", "MARK", "TEXT", "DIM", "DIMENSIONS"}
    holes = [(c.dxf.center.x - x0, c.dxf.center.y - y0, 2 * c.dxf.radius) for c in msp.query("CIRCLE")
             if c.dxf.layer.upper() not in skip and abs(2 * c.dxf.radius - max(ext.size.x, ext.size.y)) > 0.01]   # not a round blank
    return {"bend": bend, "w": ext.size.x, "h": ext.size.y, "holes": holes}




def bends_of(name, folder, text):
    """Bend count of a part from the unfold the generator ran: spisok.csv column 6 (kit root or the folder above), else the number
    of bend labels («<direction>, <bend> N° (…)») on the PDF. Never from the STEP's face normals (a flat 6 x 40 strip has two big
    non-parallel faces)."""
    for d in (folder, folder.parent):
        sp = d / "spisok.csv"
        if sp.is_file():
            try:
                rows = [r.split(";") for r in sp.read_text(encoding="utf-8-sig").splitlines()[1:]]
                for r in rows:
                    if len(r) > 5 and r[1] == name and r[5].strip().isdigit():
                        return int(r[5])
            except OSError:
                pass
    return len(BEND_LABEL.findall(text))


def check(name, files, folder_t, folder=None):
    errs = []
    lack = [s for s in (".dxf", ".pdf", ".step") if s not in files]
    if lack:
        errs.append(I.t("chk.missing", LANG, files=" ".join(name + s for s in lack)))
    if ".pdf" not in files:
        return errs
    pages = [pg.get_text() for pg in pymupdf.open(str(files[".pdf"]))]
    text = "\n".join(pages)
    if len(text.strip()) < 20:
        return errs + [I.t("chk.no_text", LANG)]
    nums = numbers(text)
    if ".dxf" in files:
        errs += laser_dxf.check(str(files[".dxf"]), LANG)
    if not MATERIAL.search(text):
        errs.append(I.t("chk.no_material", LANG))
    th = [float((m.group(1) or m.group(2)).replace(",", ".")) for m in THICK.finditer(text)]
    if not th:
        errs.append(I.t("chk.no_thickness", LANG))
    elif folder_t is not None and not any(abs(t - folder_t) < 0.01 for t in th):
        errs.append(I.t("chk.thickness_folder", LANG, found=sorted(set(th)), t=_f(folder_t)))
    fx = dxf_facts(files[".dxf"]) if ".dxf" in files else None
    bent = bool(fx and fx["bend"]) or bends_of(name, folder or files[".pdf"].parent, text) > 0
    if bent:
        if not BEND_R.search(text):
            errs.append(I.t("chk.no_radius", LANG))
        if not DIRECTION.search(text):
            errs.append(I.t("chk.no_direction", LANG))
        if not FLAT.search(text):
            errs.append(I.t("chk.no_flat", LANG))
        if not THEOR.search(pages[0]):
            errs.append(I.t("chk.no_theor", LANG))
    if fx:
        if not (has_num(nums, fx["w"], 0.1) and has_num(nums, fx["h"], 0.1)):
            errs.append(I.t("chk.no_blank", LANG, w=_f(fx["w"]), h=_f(fx["h"])))
        if fx["holes"]:
            perf = [float(m.group(1).replace(",", ".")) for m in PERFORATION.finditer(text)]
            dimmed = [h for h in fx["holes"] if not any(abs(h[2] - p) < 0.05 for p in perf)]
            if dimmed and not any(TABLE.search(p) for p in pages[1:]):
                errs.append(I.t("chk.no_hole_sheet", LANG))
            if not DATUM.search(text):
                errs.append(I.t("chk.no_datum", LANG))
            miss = [(x, y, d) for x, y, d in dimmed
                    if not ((abs(x) < 0.06 or has_num(nums, x)) and (abs(y) < 0.06 or has_num(nums, y)))]
            if miss:
                errs.append(I.t("chk.hole_dims", LANG, n=len(miss), of=len(fx["holes"]),
                                examples=", ".join(f"Ø{_f(d)} X {_f(x)} Y {_f(y)}" for x, y, d in miss[:3])))
    return errs


def main():
    global LANG
    argv = sys.argv[1:]
    if argv[:1] == ["--lang"] and len(argv) > 1:
        LANG, argv = I.lang_of(argv[1]), argv[2:]
    else:
        LANG = I.project_lang()
    args = [Path(a) for a in argv] or [Path(".")]
    files = []
    for a in args:
        files += [p for p in a.rglob("*") if p.is_file()] if a.is_dir() else [a]
    parts = {}
    for p in files:
        if p.suffix.lower() in (".dxf", ".pdf", ".step", ".stp"):
            parts.setdefault((p.parent, p.stem), {})[".step" if p.suffix.lower() == ".stp" else p.suffix.lower()] = p
    ok = fail = 0
    for (folder, name), fs in sorted(parts.items()):
        m = FOLDER_T.match(folder.name)
        errs = check(name, fs, float(m.group(1).replace(",", ".")) if m else None, folder)
        if errs:
            fail += 1
            print(f"FAIL {folder.name}/{name}")
            for e in errs:
                print("      " + e)
        else:
            ok += 1
            print(f"ok   {folder.name}/{name}")
    print(f"CHECK: {ok} ok, {fail} fail")
    sys.exit(1 if fail or not parts else 0)


if __name__ == "__main__":
    main()
