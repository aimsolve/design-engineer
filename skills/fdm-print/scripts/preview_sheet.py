#!/usr/bin/env python3
"""Six-view preview sheet of a mesh (front/back/left/right/top/iso) with a facts footer, headless (matplotlib).

  python preview_sheet.py piece.stl piece_sheet.png [--title "Ribbon v6"] [--density 1.27] [--strict]

Footer: bounding box, volume, weight at --density (solid; the slicer weight is lower), watertight, body count.
--strict: exit 1 if the mesh is not watertight or has more than one body. Prints a JSON line with the same facts.
Axes as in the file (mm). Look at the PNG before sending it anywhere.
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import trimesh  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

VIEWS = [("front (−Y)", 0, -90), ("back (+Y)", 0, 90), ("left (−X)", 0, 180),
         ("right (+X)", 0, 0), ("top (+Z)", 90, -90), ("iso", 25, -55)]
MAX_FACES = 60000   # matplotlib slows to a crawl above this; the sheet is for shape, not tessellation


def facts(m, density):
    parts = m.split(only_watertight=False)
    ext = m.extents
    vol = float(abs(m.volume)) if m.is_watertight else None
    return {"bbox_mm": [round(float(x), 2) for x in ext], "volume_cm3": round(vol / 1000, 2) if vol else None,
            "solid_weight_g": round(vol / 1000 * density, 1) if vol else None,
            "watertight": bool(m.is_watertight), "bodies": len(parts), "faces": len(m.faces)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mesh")
    ap.add_argument("out")
    ap.add_argument("--title", default=None)
    ap.add_argument("--density", type=float, default=1.27, help="g/cm3 (PETG 1.27, PLA 1.24)")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    m = trimesh.load(a.mesh, force="mesh")
    f = facts(m, a.density)
    show, note = m, ""
    if len(m.faces) > MAX_FACES:
        try:
            show = m.simplify_quadric_decimation(face_count=MAX_FACES)
            note = f"   |   drawn decimated to {MAX_FACES} faces"
        except Exception:  # noqa: BLE001 (optional dependency missing: draw a subsample instead)
            show = trimesh.Trimesh(m.vertices, m.faces[np.random.default_rng(0).choice(len(m.faces), MAX_FACES, False)])
            note = f"   |   DRAWN FROM A {MAX_FACES}-FACE SUBSAMPLE: holes in the picture are not in the mesh"
        f["render_note"] = note.strip(" |")
    tris = show.vertices[show.faces]
    n = show.face_normals
    light = np.array([0.4, -0.6, 0.7])
    shade = 0.35 + 0.65 * np.clip(n @ (light / np.linalg.norm(light)), 0, 1)
    colors = np.c_[0.30 * shade, 0.55 * shade, 0.85 * shade, np.ones(len(shade))]
    lo, hi = show.bounds
    c, r = (lo + hi) / 2, (hi - lo).max() / 2 * 1.05

    fig = plt.figure(figsize=(15, 10.5), dpi=90)
    for i, (label, elev, azim) in enumerate(VIEWS):
        ax = fig.add_subplot(2, 3, i + 1, projection="3d")
        ax.add_collection3d(Poly3DCollection(tris, facecolors=colors, edgecolors="none", linewidths=0, antialiased=False))
        ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=elev, azim=azim)
        ax.set_proj_type("ortho" if label != "iso" else "persp")
        ax.set_title(label, fontsize=12)
        ax.set_xlabel("X"); ax.set_ylabel("Y"); ax.set_zlabel("Z")
        ax.tick_params(labelsize=7)
    b = f["bbox_mm"]
    foot = (f"{b[0]} × {b[1]} × {b[2]} mm   |   volume {f['volume_cm3']} cm³   |   solid weight {f['solid_weight_g']} g "
            f"@ {a.density} g/cm³   |   watertight: {f['watertight']}   |   bodies: {f['bodies']}{note}")
    fig.suptitle(a.title or Path(a.mesh).name, fontsize=15)
    fig.text(0.5, 0.015, foot, ha="center", fontsize=12, family="monospace")
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    fig.savefig(a.out)
    print(json.dumps(dict(f, sheet=a.out)))
    if a.strict and (not f["watertight"] or f["bodies"] != 1):
        sys.exit(1)


if __name__ == "__main__":
    main()
