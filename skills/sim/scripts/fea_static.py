#!/usr/bin/env python3
"""Linear static FEA of ONE solid part: STEP -> gmsh (C3D10) -> CalculiX ccx -> von Mises, deflection, safety factor, PNG.

Runs in the worker's main python (gmsh, numpy, matplotlib, ccx on PATH):
    python fea_static.py part.step --list                         # faces: index, type, area, centre, normal / diameter
    python fea_static.py --spec fea.json                          # solve, write <out>/result.json + fea.png + model.inp

Spec (JSON, mm / N / MPa; torque in N*mm):
{
  "step": "build/parts/bracket.step", "out": "build/fea/bracket", "title": "Motor bracket",
  "material": "S235",                       # key of MATERIALS below, or {"name", "E", "nu", "yield"}
  "lang": "en",                             # picture language; default: .engineer/locale.json "lang", else ru
  "up": "y",                                # which model axis is up in the picture (default y, the framework's CAD frame)
  "h": 1.5,                                 # max element size; sheet: <= t/2. "refine": 1.5 reruns at h/1.5 (0 = off)
  "solver": "ITERATIVE CHOLESKY",           # optional; default = ccx's direct solver (SPOOLES): ~100k nodes max in 4 GB
  "fix":   [{"plane": "-y"}],               # selectors (below); all 3 DOF of their nodes fixed
  "loads": [{"on": {"holes": 3.4}, "force": [0, -7, 0]},                                   # total force, spread by area
            {"on": {"holes": 3.4}, "torque": 280, "axis": [1, 0, 0]}]   # pure moment (couple) about that axis direction
}
Selectors (a face matches when ALL given keys match):
  "plane": "+x|-x|+y|-y|+z|-z"  planar faces with that outward normal; "at": "extreme" (default: only the faces at the
                                bbox end of that side) | "any" | a coordinate value (+-0.05)
  "holes": d                    cylindrical faces of diameter d (+-0.1)
  "box": [[x0,y0,z0],[x1,y1,z1]] faces whose centre lies inside
  "near": [x,y,z], "r": R       faces whose centre is within R of the point
  "faces": [i, ...]             face indices from --list
  "clip": [[x0,y0,z0],[x1,y1,z1]] keep only the part of the picked faces inside this box (e.g. the ring under a bolt head)
Every selector must match >= 1 face or the run stops. Loads are applied as consistent nodal forces on the 6-node surface
triangles (uniform traction); a torque is a pure couple spread like a bolt group over the picked faces (force ~ distance from their centre); a force
that acts off the faces (crank, motor CG) = the force on the faces + its moment r x F as a torque entry.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fea_static_i18n import TEXTS  # noqa: E402

# E [MPa], nu, yield (Rp0.2) [MPa]. Printed plastics: yield is the weak (layer) direction, already knocked down.
# Display names by language: fea_static_i18n.py.
MATERIALS = {
    "S235":     {"E": 210000, "nu": 0.30, "yield": 235},
    "S355":     {"E": 210000, "nu": 0.30, "yield": 355},
    "AISI304":  {"E": 193000, "nu": 0.29, "yield": 205},
    "AL5754":   {"E": 70000, "nu": 0.33, "yield": 130},
    "AL6061T6": {"E": 69000, "nu": 0.33, "yield": 240},
    "PETG":     {"E": 1900, "nu": 0.38, "yield": 25},
    "PLA":      {"E": 3000, "nu": 0.36, "yield": 30},
    "PA-CF":    {"E": 4500, "nu": 0.35, "yield": 40},
    "POM":      {"E": 2800, "nu": 0.35, "yield": 60},
}
AXES = {"x": 0, "y": 1, "z": 2}


def faces_info(gmsh):
    """Every surface of the imported STEP: tag, type, area, centre, normal (plane), diameter (cylinder)."""
    out = []
    for i, (_, tag) in enumerate(gmsh.model.getEntities(2)):
        typ = gmsh.model.getType(2, tag)
        area = gmsh.model.occ.getMass(2, tag)
        c = np.array(gmsh.model.occ.getCenterOfMass(2, tag))
        f = {"i": i, "tag": tag, "type": typ, "area": area, "c": c}
        lo, hi = gmsh.model.getParametrizationBounds(2, tag)
        uv = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2]
        if typ == "Plane":
            n = np.array(gmsh.model.getNormal(tag, uv))
            f["n"] = n / np.linalg.norm(n)
        elif typ == "Cylinder":
            k = gmsh.model.getCurvature(2, tag, uv)[0]
            f["d"] = 2.0 / k if k else float("nan")
        out.append(f)
    return out


def select(faces, sel, bbox):
    hits = []
    for f in faces:
        ok = True
        if "plane" in sel:
            s = sel["plane"]
            ax, sign = AXES[s[-1]], (-1.0 if s[0] == "-" else 1.0)
            want = np.zeros(3); want[ax] = sign
            ok &= f["type"] == "Plane" and float(f["n"] @ want) > 0.999
            at = sel.get("at", "extreme")
            if ok and at == "extreme":
                ok &= abs(f["c"][ax] - (bbox[1][ax] if sign > 0 else bbox[0][ax])) < 0.05
            elif ok and at != "any":
                ok &= abs(f["c"][ax] - float(at)) < 0.05
        if "holes" in sel:
            ok &= f["type"] == "Cylinder" and abs(f.get("d", 0) - float(sel["holes"])) < 0.1
        if "box" in sel:
            lo, hi = np.array(sel["box"][0]), np.array(sel["box"][1])
            ok &= bool(np.all(f["c"] >= lo) and np.all(f["c"] <= hi))
        if "near" in sel:
            ok &= float(np.linalg.norm(f["c"] - np.array(sel["near"]))) <= float(sel.get("r", 5.0))
        if "faces" in sel:
            ok &= f["i"] in sel["faces"]
        if ok:
            hits.append(f)
    if not hits:
        sys.exit(f"selector {sel} matched no face: run --list and fix it")
    return hits


def mesh(step, h):
    import gmsh
    gmsh.initialize(["", "-v", "0"])
    gmsh.model.add("part")
    gmsh.model.occ.importShapes(str(step))
    gmsh.model.occ.synchronize()
    vols = gmsh.model.getEntities(3)
    if len(vols) != 1:
        sys.exit(f"{step}: {len(vols)} solids; FEA here takes exactly one solid (export the part alone)")
    bb = gmsh.model.getBoundingBox(3, vols[0][1])
    bbox = (np.array(bb[:3]), np.array(bb[3:]))
    faces = faces_info(gmsh)
    gmsh.option.setNumber("Mesh.MeshSizeMax", h)
    gmsh.option.setNumber("Mesh.MeshSizeMin", h / 4)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 12)   # holes and fillets get at least 12 elements around
    gmsh.option.setNumber("Mesh.ElementOrder", 2)
    gmsh.option.setNumber("Mesh.HighOrderOptimize", 1)
    gmsh.model.mesh.generate(3)
    tags, xyz, _ = gmsh.model.mesh.getNodes()
    xyz = xyz.reshape(-1, 3)
    et, _, en = gmsh.model.mesh.getElements(3)
    conn = en[list(et).index(11)].reshape(-1, 10).astype(np.int64)   # 11 = 10-node tetrahedron
    tris = {}
    for f in faces:   # 6-node surface triangles per face (type 9)
        t2, _, n2 = gmsh.model.mesh.getElements(2, f["tag"])
        tris[f["tag"]] = n2[list(t2).index(9)].reshape(-1, 6).astype(np.int64) if 9 in list(t2) else np.zeros((0, 6), np.int64)
    gmsh.finalize()
    return {"tags": tags.astype(np.int64), "xyz": xyz, "conn": conn, "faces": faces, "tris": tris, "bbox": bbox}


def tris_of(m, sel):
    """6-node surface triangles of the faces a selector picks, cut to its optional "clip" box (triangle centre inside)."""
    pos = {int(t): i for i, t in enumerate(m["tags"])}
    t = np.concatenate([m["tris"][f["tag"]] for f in select(m["faces"], sel, m["bbox"])])
    if "clip" in sel:
        lo, hi = np.array(sel["clip"][0]), np.array(sel["clip"][1])
        c = m["xyz"][[[pos[int(n)] for n in tri[:3]] for tri in t]].mean(1)
        t = t[np.all((c >= lo) & (c <= hi), axis=1)]
        if not len(t):
            sys.exit(f"selector {sel}: clip box keeps no surface")
    return t, pos


def nodal_weights(m, sel):
    """Consistent nodal weights of a uniform traction on 6-node triangles: corners 0, mid-sides area/3 (sum = area)."""
    tris, pos = tris_of(m, sel)
    w = {}
    for tri in tris:
        p = m["xyz"][[pos[int(n)] for n in tri[:3]]]
        a = 0.5 * np.linalg.norm(np.cross(p[1] - p[0], p[2] - p[0]))
        for n in tri[3:]:
            w[int(n)] = w.get(int(n), 0.0) + a / 3
    return w, pos


def build_inp(m, spec, mat):
    fixed = set()
    for sel in spec["fix"]:
        fixed.update(int(n) for n in tris_of(m, sel)[0].ravel())
    if not fixed:
        sys.exit("no fixed nodes")
    loads = {}
    applied = np.zeros(3)
    for ld in spec["loads"]:
        w, pos = nodal_weights(m, ld["on"])
        nodes = np.array(list(w)); ww = np.array([w[n] for n in nodes])
        if "force" in ld:
            F = np.array(ld["force"], float)
            fn = np.outer(ww / ww.sum(), F)
        else:
            a = np.array(ld["axis"], float); a /= np.linalg.norm(a)
            r = m["xyz"][[pos[n] for n in nodes]]
            r = r - (ww[:, None] * r).sum(0) / ww.sum()       # about the faces' own centre: a pure couple, no net force
            r_perp = r - np.outer(r @ a, a)
            k = float(ld["torque"]) / float((ww * (r_perp ** 2).sum(1)).sum())
            fn = k * ww[:, None] * np.cross(a, r_perp)
        for n, f in zip(nodes, fn):
            loads[int(n)] = loads.get(int(n), np.zeros(3)) + f
        applied += fn.sum(0)
    L = ["*NODE, NSET=NALL"] + [f"{int(t)},{p[0]:.6f},{p[1]:.6f},{p[2]:.6f}" for t, p in zip(m["tags"], m["xyz"])]
    L.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    for i, c in enumerate(m["conn"], 1):
        c = list(map(int, c)); c[8], c[9] = c[9], c[8]   # gmsh vs ccx order of the last two mid-edge nodes
        L.append(f"{i}," + ",".join(map(str, c)))
    L.append("*NSET, NSET=FIX"); L += [str(n) for n in sorted(fixed)]
    L += ["*MATERIAL, NAME=M", "*ELASTIC", f"{mat['E']},{mat['nu']}", "*SOLID SECTION, ELSET=EALL, MATERIAL=M",
          "*STEP", "*STATIC" + (f", SOLVER={spec['solver']}" if spec.get("solver") else ""), "*BOUNDARY", "FIX,1,3,0", "*CLOAD"]
    for n, f in loads.items():
        for d in range(3):
            if abs(f[d]) > 1e-12:
                L.append(f"{n},{d + 1},{f[d]:.9g}")
    L += ["*NODE FILE", "U", "*EL FILE", "S", "*NODE PRINT, NSET=FIX, TOTALS=ONLY", "RF", "*END STEP"]
    return "\n".join(L) + "\n", fixed, applied


def read_frd(path):
    """Nodal U and S (extrapolated) from an ASCII .frd: {'DISP': {node: [3]}, 'STRESS': {node: [6]}}."""
    res, cur = {}, None
    for line in open(path):
        if line.startswith(" -4"):
            cur = line.split()[1]; res[cur] = {}
        elif line.startswith(" -3"):
            cur = None
        elif cur and line.startswith(" -1"):
            n = int(line[3:13]); s = line[13:].rstrip("\n")
            res[cur][n] = [float(s[i:i + 12]) for i in range(0, len(s) - 11, 12)]
    return res


def solve(m, spec, mat, work):
    inp, fixed, applied = build_inp(m, spec, mat)
    work.mkdir(parents=True, exist_ok=True)
    (work / "model.inp").write_text(inp)
    env = dict(os.environ, OMP_NUM_THREADS=os.environ.get("OMP_NUM_THREADS", "2"))
    r = subprocess.run(["ccx", "-i", "model"], cwd=work, capture_output=True, text=True, env=env)
    if r.returncode != 0 or not (work / "model.frd").exists():
        oom = " (killed: most likely out of memory - the worker has 4 GB for 2 jobs; coarser h, or \"solver\": \"ITERATIVE CHOLESKY\")" \
            if r.returncode < 0 or "Static analysis was selected" in r.stdout[-200:] else ""
        sys.exit(f"ccx failed{oom}:\n{r.stdout[-1500:]}{r.stderr[-1500:]}")
    frd = read_frd(work / "model.frd")
    rf = re.findall(r"total force.*?\n\s*([-\dE.+]+)\s+([-\dE.+]+)\s+([-\dE.+]+)", (work / "model.dat").read_text())
    reaction = np.array([float(v) for v in rf[-1]]) if rf else np.full(3, np.nan)
    U, S = frd["DISP"], frd["STRESS"]
    nodes = np.array(sorted(S))
    s = np.array([S[n] for n in nodes])
    vm = np.sqrt(0.5 * ((s[:, 0] - s[:, 1]) ** 2 + (s[:, 1] - s[:, 2]) ** 2 + (s[:, 2] - s[:, 0]) ** 2)
                 + 3 * (s[:, 3] ** 2 + s[:, 4] ** 2 + s[:, 5] ** 2))
    u = np.array([U[n][:3] for n in nodes])
    pos = {int(t): i for i, t in enumerate(m["tags"])}
    xyz = m["xyz"][[pos[int(n)] for n in nodes]]
    # "away from the supports": nodes farther than 2 h from any fixed node (fixed edges carry a singular peak)
    fx = m["xyz"][[pos[n] for n in fixed]]
    far = np.ones(len(nodes), bool)
    for i0 in range(0, len(nodes), 4000):
        d = np.min(np.linalg.norm(xyz[i0:i0 + 4000, None, :] - fx[None, ::max(1, len(fx) // 3000), :], axis=2), axis=1)
        far[i0:i0 + 4000] = d > 2 * spec["h_used"]
    um = np.linalg.norm(u, axis=1)
    i_max = int(np.argmax(vm)); i_far = int(np.argmax(np.where(far, vm, -1)))
    return {
        "h": spec["h_used"], "nodes": len(m["tags"]), "elements": len(m["conn"]),
        "vm_max": float(vm[i_max]), "vm_max_at": xyz[i_max].round(2).tolist(),
        "vm_max_away_from_supports": float(vm[i_far]), "vm_away_at": xyz[i_far].round(2).tolist(),
        "u_max": float(um.max()), "u_max_at": xyz[int(np.argmax(um))].round(2).tolist(),
        "applied_total_N": applied.round(4).tolist(), "reaction_total_N": reaction.round(4).tolist(),
    }, {"nodes": nodes, "vm": vm, "u": u, "pos": pos, "fixed": fixed}


def picture(m, field, res, mat, spec, png, fixed, arrows):
    """Two views of the deformed part coloured by von Mises; supports light grey, loads as arrows. CAD Y up by default."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    up = spec.get("up", "y")
    perm = {"y": [0, 2, 1], "z": [0, 1, 2], "x": [1, 2, 0]}[up]   # screen (x, y, z-up) from model axes
    idx = {int(n): i for i, n in enumerate(field["nodes"])}
    tri = np.concatenate([t[:, :3] for t in m["tris"].values() if len(t)])
    pos = field["pos"]
    p0 = m["xyz"][[[pos[int(n)] for n in t] for t in tri]]
    uu = field["u"][[[idx[int(n)] for n in t] for t in tri]]
    diag = float(np.linalg.norm(m["bbox"][1] - m["bbox"][0]))
    scale = round(0.04 * diag / res["u_max"]) if res["u_max"] > 0 else 0
    P = (p0 + scale * uu)[:, :, perm]
    v = field["vm"][[[idx[int(n)] for n in t] for t in tri]].mean(1)
    vmax = max(res["vm_max_away_from_supports"], 1e-9)
    cmap = plt.get_cmap("turbo"); norm = matplotlib.colors.Normalize(0, vmax)
    col = cmap(norm(np.minimum(v, vmax)))
    col[np.array([all(int(n) in fixed for n in t) for t in tri])] = (0.72, 0.72, 0.72, 1)
    lo, hi = P.reshape(-1, 3).min(0), P.reshape(-1, 3).max(0)
    pad = 0.08 * (hi - lo).max(); lo, hi = lo - pad, hi + pad
    fig = plt.figure(figsize=(13, 6.6), dpi=130)
    for k, az in enumerate(spec.get("views", [-55, 125])):
        ax = fig.add_subplot(1, 2, k + 1, projection="3d")
        ax.add_collection3d(Poly3DCollection(P, facecolors=col, edgecolor="none"))
        for a0, vec, txt in arrows:
            a0, vec = np.array(a0)[perm], np.array(vec)[perm] * 0.3 * diag
            ax.quiver(*(a0 - vec), *vec, color="crimson", linewidth=2.2, arrow_length_ratio=0.25)
            ax.text(*(a0 - 1.1 * vec), txt, color="crimson", fontsize=10)
        ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(lo[2], hi[2])
        ax.set_box_aspect(hi - lo, zoom=1.35); ax.view_init(22, az); ax.set_axis_off()
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    t = texts(spec)
    fig.colorbar(sm, ax=fig.axes, shrink=0.6, label=t["colorbar"])
    sf = mat["yield"] / vmax
    fig.suptitle(f"{spec.get('title', Path(spec['step']).stem)} — {mat['name']}\n"
                 + t["summary"].format(vm=res["vm_max_away_from_supports"], yld=mat["yield"], sf=sf, u=res["u_max"], scale=scale),
                 fontsize=11)
    fig.savefig(png, bbox_inches="tight"); plt.close(fig)


def lang_of(spec):
    """The language of the picture: the spec's "lang", else the project's .engineer/locale.json "lang", else ru."""
    lang = spec.get("lang")
    if not lang:
        try:
            lang = json.loads(Path(".engineer/locale.json").read_text(encoding="utf-8")).get("lang")
        except (OSError, ValueError, AttributeError):
            lang = None
    return lang if lang in TEXTS else "ru"


def texts(spec):
    return TEXTS[lang_of(spec)]


def material(spec):
    """The spec's material: a MATERIALS key (named in the picture's language) or an explicit dict."""
    if not isinstance(spec["material"], str):
        return spec["material"]
    key = spec["material"]
    return {"name": texts(spec)["materials"].get(key, key), **MATERIALS[key]}


def load_arrows(m, spec):
    """(point, unit direction, label) per force load at the centre of its faces; torques are not drawn (say them in the caption)."""
    out = []
    for ld in spec["loads"]:
        if "force" not in ld:
            continue
        fs = select(m["faces"], ld["on"], m["bbox"])
        c = sum(f["c"] * f["area"] for f in fs) / sum(f["area"] for f in fs)
        F = np.array(ld["force"], float); n = np.linalg.norm(F)
        out.append((c, F / n, ld.get("label", texts(spec)["force"].format(n=n))))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", nargs="?")
    ap.add_argument("--list", action="store_true", help="list faces and exit")
    ap.add_argument("--spec")
    a = ap.parse_args()
    if a.list:
        import gmsh
        gmsh.initialize(["", "-v", "0"]); gmsh.model.occ.importShapes(a.step); gmsh.model.occ.synchronize()
        bb = gmsh.model.getBoundingBox(-1, -1)
        print(f"bbox {np.round(bb[:3], 2).tolist()} .. {np.round(bb[3:], 2).tolist()}  solids {len(gmsh.model.getEntities(3))}")
        for f in faces_info(gmsh):
            extra = f"n {np.round(f['n'], 3).tolist()}" if "n" in f else (f"d {f['d']:.2f}" if "d" in f else "")
            print(f"{f['i']:4d} {f['type']:<16} area {f['area']:9.1f}  c {np.round(f['c'], 2).tolist()}  {extra}")
        gmsh.finalize(); return
    spec = json.loads(Path(a.spec).read_text())
    mat = material(spec)
    out = Path(spec.get("out", "build/fea/" + Path(spec["step"]).stem)); out.mkdir(parents=True, exist_ok=True)
    runs, keep = [], None
    hs = [spec["h"]] + ([spec["h"] / spec["refine"]] if spec.get("refine", 1.5) else [])
    for k, h in enumerate(hs):
        spec["h_used"] = h
        m = mesh(spec["step"], h)
        res, field = solve(m, spec, mat, out / f"h{k}")
        print(f"h {h:.3g}: {res['nodes']} nodes, {res['elements']} C3D10; vm max {res['vm_max']:.2f} MPa at {res['vm_max_at']}, "
              f"away from supports {res['vm_max_away_from_supports']:.2f} MPa at {res['vm_away_at']}; u max {res['u_max']:.4f} mm; "
              f"load {res['applied_total_N']} N, reaction {res['reaction_total_N']} N")
        runs.append(res); keep = (m, field, res)
    m, field, res = keep
    conv = {}
    if len(runs) == 2:
        conv = {q: abs(runs[1][q] - runs[0][q]) / max(abs(runs[1][q]), 1e-12)
                for q in ("u_max", "vm_max_away_from_supports", "vm_max")}
    sf = mat["yield"] / res["vm_max_away_from_supports"] if res["vm_max_away_from_supports"] > 0 else None
    eq = float(np.max(np.abs(np.array(res["applied_total_N"]) + np.array(res["reaction_total_N"]))))
    summary = {"step": spec["step"], "material": mat, "fix": spec["fix"], "loads": spec["loads"], "runs": runs,
               "change_on_refine": conv, "safety_factor_yield": sf, "equilibrium_error_N": eq,
               "converged": bool(conv) and conv["u_max"] < 0.05 and conv["vm_max_away_from_supports"] < 0.10}
    (out / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    picture(m, field, res, mat, spec, out / "fea.png", field["fixed"], load_arrows(m, spec))
    print(f"{'ok' if summary['converged'] else 'WARN not converged'}: stress {res['vm_max_away_from_supports']:.2f} MPa "
          f"(peak at supports {res['vm_max']:.2f}), yield {mat['yield']} -> safety {sf:.2f}, deflection {res['u_max']:.4f} mm; "
          f"change on refine {', '.join(f'{k} {v:.1%}' for k, v in conv.items())}; equilibrium error {eq:.3g} N")
    print(f"-> {out}/fea.png, {out}/result.json")


if __name__ == "__main__":
    main()
