"""Shop drawing set for sheet-metal parts (laser / laser + bending): per part ONE name -> <name>.dxf + <name>.pdf + <name>.step,
sorted into a kit folder per thickness + material, with spisok.csv, README.txt and a zip.

The standard (see ../SKILL.md): sheet 1 = flat pattern with a bend label along each bend line
(direction, bend angle, angle between the flanges, inner R) and «**» theoretical dims + views (overall, flange, thickness «t *»,
inner R) + isometric; sheet 2 = every hole and cut-out from the lower-left corner 0,0 + hole table (sheet_pdf.py). Weld seams on
the views for a part with "weld". DXF = laser-clean for the shop's laser software (e.g. CypCut): AC1015, closed contours on layer
0, no text / bends (laser_dxf.py). Every text a person reads is in the project's language (drawings_i18n.py; --lang overrides).

Input: a STEP of the FINISHED (folded) part, or a build123d function `module.py:func` returning a Shape, + params.
  one part:  python shop_drawing.py --step cad/out/bracket.step --material "S235JR" --out build/kit [--t 3] [--R 3] [--K 0.33]
             python shop_drawing.py --py cad/parts/bracket.py:build_bracket --name bracket --material "AISI 304" --out build/kit
  a kit:     python shop_drawing.py --parts parts.json --out build/kit
             parts.json = [{"name": "Exit bracket", "step": "out/bracket.step", "material": "S235JR", "qty": 2,
                            "t": 3, "R": 3, "K": 0.33, "finish": "zinc plated", "taps": [2.5, 4.2], "group": "steel",
                            "code": "EX_01_00_001", "weld": true, "note": "..." or ["...", "..."]}, ...]
             (paths relative to the json; "py": "parts/x.py:fn" instead of "step"; t auto-detected when omitted,
              R defaults to t, K to 0.33 steel / stainless, 0.40 alu; taps = hole diameters that are tap drills (the table says
              the thread); title = the name in the title block when it differs from "name" (name stays the file stem); weld = true
              (corner seams found on the model) or a text saying where and how to weld; group = the folder's material word)
Out: <out>/<t> <mm> <group>/<name>.{dxf,pdf,step} (folder words in the project's language), <out>/spisok.csv, <out>/README.txt,
<out>.zip. Last line:
  SHOP: n ok, m warn, k fail. Exit 1 on any fail. Then run check_drawings.py on <out>.

Needs build123d + ezdxf + matplotlib + shapely + pymupdf: run through drawings_env.sh next to this file. Heavy: on the bot run it
as a worker job (`job run`).
"""
import argparse
import csv
import glob
import importlib.util
import json
import math
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build123d import Location, Plane, Vector, export_step, import_step  # noqa: E402

import drawings_i18n as I  # noqa: E402
import laser_dxf  # noqa: E402
import sheet_pdf  # noqa: E402
import unfold  # noqa: E402

K_DEFAULT = {"steel": 0.33, "stainless": 0.33, "alu": 0.40}
TAP = {1.6: "M2", 2.05: "M2.5", 2.5: "M3", 2.6: "M3", 3.3: "M4", 4.2: "M5", 5.0: "M6", 6.8: "M8", 8.5: "M10"}   # ISO 261 drill


def gt(x):
    return str(getattr(x, "geom_type", x)).split(".")[-1].upper()


def _f(x):
    return f"{x:.2f}".rstrip("0").rstrip(".") or "0"


# ---------------------------------------------------------------- geometry
def load_shape(spec, base):
    if spec.get("step"):
        return import_step(str((base / spec["step"]).resolve()))
    mod, fn = spec["py"].rsplit(":", 1)
    p = (base / mod).resolve()
    sys.path.insert(0, str(p.parent))
    m = importlib.util.spec_from_file_location(p.stem, p)
    mm = importlib.util.module_from_spec(m)
    m.loader.exec_module(mm)
    s = getattr(mm, fn)(**spec.get("kwargs", {}))
    return s[0]["shape"] if isinstance(s, list) else s


FAR = 500.0        # mm: a part whose bbox centre is farther from the origin is moved to it before unfolding (unfold.py's cutting boxes are ~4 m)


def recentre(shape):
    """-> shape moved so its bbox centre is at the origin when it sits far from it (else unchanged: output identical for parts near the origin)."""
    bb = shape.bounding_box()
    c = (bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2, (bb.min.Z + bb.max.Z) / 2
    if max(abs(x) for x in c) <= FAR:
        return shape
    shape = shape.moved(Location((-c[0], -c[1], -c[2])))
    return shape.solids()[0] if len(shape.solids()) == 1 else shape            # a bare Solid: OCC booleans on the moved Compound fail


def detect_t(shape):
    """sheet thickness = area-weighted most common distance from a planar face to the nearest antiparallel planar face"""
    pf = [(f, f.normal_at(f.center()), f.center(), f.area) for f in shape.faces() if gt(f) == "PLANE"]
    votes = {}
    for f, n, c, a in pf:
        ds = [n.dot(c - cg) for g, m, cg, _ in pf if g is not f and n.dot(m) < -0.9999]
        ds = [d for d in ds if d > 0.05]
        if ds:
            k = round(min(ds), 2)
            votes[k] = votes.get(k, 0.0) + a
    if not votes:
        raise ValueError("no pair of opposite flat faces: not a sheet part")
    return max(votes, key=votes.get)


def face_plane(f):
    """plane on the face, X along its longest straight outer edge, Z = outward normal (flat pattern = DXF frame)"""
    n = f.normal_at()
    lines = [e for e in f.outer_wire().edges() if gt(e) == "LINE"]
    if lines:
        e = max(lines, key=lambda e: e.length)
        xd = (e @ 1) - (e @ 0)
    else:
        xd = Vector(1, 0, 0) if abs(n.X) < 0.9 else Vector(0, 1, 0)
        xd = xd - n * xd.dot(n)
    return Plane(origin=f.center(), x_dir=xd.normalized(), z_dir=n)


def flat_pattern(shape, t, R, K):
    """-> dict(face=top face in the DXF frame, solid=flat blank in the same frame (top at Z 0), bends=[(bend, p0, p1)], BA, checks);
    bend = {"no": 1.., "up": bool, "angle": degrees turned}
    bent parts through unfold.py; a flat part whose leg test fails (countersinks, pockets) -> its largest flat face"""
    try:
        uf = unfold.unfold(shape, t, R, K)
    except ValueError as ex:
        bb = shape.bounding_box()
        if min(bb.size.X, bb.size.Y, bb.size.Z) > t + 0.05:
            raise
        uf, why = None, str(ex)
    if uf is None:
        f = max([g for g in shape.faces() if gt(g) == "PLANE"], key=lambda g: g.area)
        pl = face_plane(f)
        return {"face": pl.to_local_coords(f), "solid": pl.to_local_coords(shape), "bends": [], "BA": 0.0,
                "checks": [f"flat part, unfold skipped ({why[:80]})"], "fail": []}
    pl = face_plane(uf["top"])
    bends = [({"no": k + 1, "up": bool(b["up"]), "angle": round(b["angle"])}, pl.to_local_coords(b["line"][0]), pl.to_local_coords(b["line"][1]))
             for k, b in enumerate(uf["bends"])]
    fail = []
    if bends and uf["v_err"] > 0.02:
        fail.append(f"refold volume off {uf['v_err'] * 100:.1f} %")
    if bends and uf["inside"] < 0.98:
        fail.append(f"refolded flat only {uf['inside'] * 100:.1f} % inside the model")
    if uf["holes_flat"] != uf["holes_model"]:
        fail.append(f"holes flat {uf['holes_flat']} vs model {uf['holes_model']}")
    return {"face": pl.to_local_coords(uf["top"]), "solid": pl.to_local_coords(uf["flat"]), "bends": bends, "BA": uf["BA"],
            "checks": [f"unfold {uf['legs']} legs, {len(bends)} bends, refold err {uf['v_err'] * 100:.2f} %"], "fail": fail}


# ---------------------------------------------------------------- DXF
def _edges(msp, edges, layer):
    at = {"layer": layer}
    for e in edges:
        p0, p1 = e @ 0, e @ 1
        k = gt(e)
        if k == "LINE":
            msp.add_line((p0.X, p0.Y), (p1.X, p1.Y), dxfattribs=at)
        elif k == "CIRCLE":
            c, r = e.arc_center, e.radius
            if (p0 - p1).length < 1e-6:
                msp.add_circle((c.X, c.Y), r, dxfattribs=at)
            else:
                pm = e @ 0.5
                a = [math.degrees(math.atan2(p.Y - c.Y, p.X - c.X)) for p in (p0, p1, pm)]
                ccw = (a[2] - a[0]) % 360 < (a[1] - a[0]) % 360
                s0, s1 = (a[0], a[1]) if ccw else (a[1], a[0])
                msp.add_arc((c.X, c.Y), r, s0, s1, dxfattribs=at)
        else:
            n = max(1, min(200, math.ceil(e.length / 0.05)))   # pieces of >= 0.05 mm: never below the laser DXF's joining tolerance
            msp.add_lwpolyline([((e @ (i / n)).X, (e @ (i / n)).Y) for i in range(n + 1)], dxfattribs=at)


# ---------------------------------------------------------------- kit
def group_of(part, lang=I.DEFAULT):
    """The kit folder's material word: the part's own `group`, else the kind of its material in the project's language."""
    if part.get("group"):
        return part["group"]
    return I.t(f"group.{I.material_kind(part['material'])}", lang)


def k_of(part):
    """K factor by the material kind; a part's own `group` decides only when it is one of the folder words."""
    kind = (I.group_kind(part["group"]) or "steel") if part.get("group") else I.material_kind(part["material"])
    return K_DEFAULT[kind]


def notes_of(note):
    """A part's `note`: a string is ONE note (iterating it once gave a note per letter), a list one note per item."""
    if not note:
        return []
    return [str(x) for x in note] if isinstance(note, (list, tuple)) else [str(note)]


def material_line(part, t, lang=I.DEFAULT):
    m = part["material"] + (f", {part['finish']}" if part.get("finish") else "")
    unit = I.t("mm", lang)
    return m if re.search(rf"\b{re.escape(_f(t))}\s*(?:{I.recognise('mm')})", m) else f"{m}, {_f(t)} {unit}"


def make_part(part, base, out, png=False, lang=I.DEFAULT):
    raw = load_shape(part, base)
    shp = recentre(raw)
    t = float(part.get("t") or detect_t(shp))
    R = float(part.get("R") or t)
    K = float(part.get("K") or k_of(part))
    fp = flat_pattern(shp, t, R, K)
    fbb = fp["face"].bounding_box()
    dx, dy = fbb.min.X, fbb.min.Y
    face = fp["face"].translate((-dx, -dy, 0))                          # blank min corner = 0,0 of the DXF and of sheet 2
    bends = [(tag, (p0.X - dx, p0.Y - dy), (p1.X - dx, p1.Y - dy)) for tag, p0, p1 in fp["bends"]]
    folder = out / I.t("kit.folder", lang, t=_f(t), group=group_of(part, lang))
    folder.mkdir(parents=True, exist_ok=True)
    name = part["name"]
    fail = list(fp["fail"])
    n_open = laser_dxf.write(face, str(folder / f"{name}.dxf"), _edges)
    if n_open:
        fail.append(f"{n_open} open contour(s) in the laser DXF")
    export_step(raw, str(folder / f"{name}.step"))                  # the kit STEP keeps the assembly position
    taps = [(d, TAP.get(round(float(d), 2), I.t("thread", lang))) for d in part.get("taps") or []]
    info = {"title": part.get("title") or name, "material_line": material_line(part, t, lang), "qty": part.get("qty", 1), "code": part.get("code", ""),
            "weld": part.get("weld") or False, "notes": notes_of(part.get("note")), "dim_d": part.get("dim_d"), "lang": lang}
    base = max([f for f in shp.faces() if gt(f) == "PLANE"], key=lambda f: f.area)
    local = face_plane(base).to_local_coords(shp)                       # views in the part's own frame (a STEP sits as in the assembly)
    sc, nh, w = sheet_pdf.draw(str(folder / f"{name}.pdf"), local, face, bends, t, R, info, _edges, taps=taps, png=png)
    return {"name": name, "folder": folder.name, "t": t, "R": R, "K": K, "bends": len(fp["bends"]), "holes": nh,
            "blank": f"{fbb.size.X:.1f} x {fbb.size.Y:.1f}", "scale": sc, "warns": w + fp["checks"][1:], "fail": fail,
            "check": fp["checks"][0], "qty": part.get("qty", 1), "material": part["material"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--parts", help="JSON list of parts (see the docstring)")
    ap.add_argument("--step")
    ap.add_argument("--py", help="module.py:function returning a build123d Shape")
    ap.add_argument("--name")
    ap.add_argument("--material", default="")
    ap.add_argument("--t", type=float)
    ap.add_argument("--R", type=float)
    ap.add_argument("--K", type=float)
    ap.add_argument("--qty", type=int, default=1)
    ap.add_argument("--finish", default="")
    ap.add_argument("--taps", default="", help="comma list of tap-drill hole diameters, e.g. 2.5,4.2")
    ap.add_argument("--code", default="", help="drawing code for the title block")
    ap.add_argument("--weld", default="", help="'1' = weld the corner seams found on the model, or a text: where and how to weld")
    ap.add_argument("--out", required=True)
    ap.add_argument("--png", action="store_true", help="PNG preview of every page")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--lang", help="language of the texts on the drawings (default: the project's .engineer/locale.json, else ru)")
    a = ap.parse_args()
    lang = I.lang_of(a.lang) if a.lang else I.project_lang()
    if a.parts:
        base = Path(a.parts).resolve().parent
        parts = json.load(open(a.parts, encoding="utf-8"))
    else:
        if not (a.step or a.py) or not a.material:
            ap.error("--parts, or --step / --py with --material")
        base = Path.cwd()
        parts = [{"name": a.name or Path(a.step or a.py.split(":")[0]).stem, "step": a.step, "py": a.py, "material": a.material,
                  "t": a.t, "R": a.R, "K": a.K, "qty": a.qty, "finish": a.finish,
                  "taps": [float(x) for x in a.taps.split(",") if x.strip()], "code": a.code,
                  "weld": True if a.weld == "1" else a.weld}]
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    rows, ok, warn, fail = [], 0, 0, 0
    for p in parts:
        if not p.get("material"):
            print(f"FAIL {p.get('name')}: no material (the title block needs it)")
            fail += 1
            continue
        try:
            r = make_part(p, base, out, a.png, lang)
        except Exception as ex:                                              # noqa: BLE001
            fail += 1
            for f in out.rglob(glob.escape(p["name"]) + ".*"):          # no half-made drawing of a failed part in the kit
                if f.suffix.lower() in (".dxf", ".pdf", ".step"):
                    f.unlink()
            print(f"FAIL {p.get('name'):32s} {type(ex).__name__}: {ex}")
            continue
        rows.append(r)
        st = "FAIL" if r["fail"] else "WARN" if r["warns"] else "ok  "
        fail += bool(r["fail"])
        warn += bool(r["warns"]) and not r["fail"]
        ok += not r["warns"] and not r["fail"]
        print(f"{st} {r['name']:32s} {r['folder']:18s} bends {r['bends']} holes {r['holes']:3d} blank {r['blank']}  {r['scale']}  {r['check']}")
        for x in r["fail"] + r["warns"]:
            print("      ", x)
    with open(out / "spisok.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(I.t("kit.list_cols", lang))                       # column 6 = the bend count (check_drawings.bends_of reads it)
        for r in sorted(rows, key=lambda r: (r["folder"], r["name"])):
            w.writerow([r["folder"], r["name"], r["qty"], r["material"], _f(r["t"]), r["bends"], _f(r["R"]) if r["bends"] else "",
                        r["blank"], f"{r['name']}.dxf {r['name']}.pdf {r['name']}.step"])
    (out / "README.txt").write_text(I.t("kit.readme", lang), encoding="utf-8")
    if not a.no_zip and rows:
        zp = out.with_suffix(".zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(out.rglob("*")):
                if f.is_file() and f.suffix.lower() in (".dxf", ".pdf", ".step", ".csv", ".txt"):
                    z.write(f, f.relative_to(out))
        print(f"-> {zp} ({zp.stat().st_size // 1024} KB)")
    print(f"SHOP: {ok} ok, {warn} warn, {fail} fail")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
