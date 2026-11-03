#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["numpy"]
# ///
"""Bake CAD feature edges (crease + boundary) of every distinct part in model.glb, once, offline.

  uv run skills/step-import/scripts/bake_edges.py <out dir> [--angle 30]      (or: python3 with numpy installed)

Reads <out>/model.glb (KHR_mesh_quantization: int16 positions + a node matrix that carries the dequantisation scale),
writes <out>/edges.bin + <out>/edges.json. The web viewer (templates/assembly) loads them AFTER the model and draws them as
LINES sharing one geometry per part, as a child of every instance, so they move with animated parts.

Why offline: welding and dihedral tests over 2.6 M triangles in the browser froze a slow machine once (creased normals).

An edge is kept when
  * it belongs to one triangle only (boundary), or
  * the angle between its two face normals is > --angle (default 30 deg; the importer's tessellation angle is 0.5 rad = 28.6 deg,
    so curved surfaces do NOT light up), or
  * it is non-manifold (3+ triangles) and any pair of them is that sharp.
Angles are measured in REAL space (the per-axis dequantisation scale of the mesh's node matrix is applied first, otherwise flat
plates would look sharp on the squashed axis). Vertices are welded on their int16 position, so split normals do not cut edges.

edges.json  {"version": 1, "angle": 30, "parts": {"<part id>": [nverts, nedges, vertex_byte_offset, index_byte_offset, index_bytes(2|4)]}, "bytes": N}
edges.bin   per part: vertices int16 x4 (x, y, z, 0 - same normalized int16 space as the mesh POSITION, so the line object needs no
            transform of its own), then edge indices (uint16 or uint32) as vertex pairs. Offsets are 4-byte aligned.
"""
import argparse, json, struct, sys, time
from pathlib import Path

import numpy as np

CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read_glb(path: Path):
    b = path.read_bytes()
    if b[:4] != b"glTF":
        sys.exit(f"{path}: not a GLB")
    off, js, binc = 12, None, None
    while off < len(b):
        n, t = struct.unpack_from("<II", b, off)
        chunk = b[off + 8: off + 8 + n]
        if t == 0x4E4F534A:
            js = json.loads(chunk)
        elif t == 0x004E4942:
            binc = chunk
        off += 8 + n
    return js, binc


def accessor(js, binc, i):
    a = js["accessors"][i]
    bv = js["bufferViews"][a["bufferView"]]
    nc, dt = NC[a["type"]], CT[a["componentType"]]
    isz = np.dtype(dt).itemsize
    stride = bv.get("byteStride") or nc * isz
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    raw = np.frombuffer(binc, dtype=np.uint8, count=stride * (a["count"] - 1) + nc * isz, offset=start)
    rows = np.lib.stride_tricks.as_strided(raw, shape=(a["count"], nc * isz), strides=(stride, 1))
    return np.ascontiguousarray(rows).view(dt).reshape(a["count"], nc)


def node_scale(n):
    m = n.get("matrix")
    if m:
        return np.array([np.linalg.norm(m[0:3]), np.linalg.norm(m[4:7]), np.linalg.norm(m[8:11])])
    return np.abs(np.array(n.get("scale", [1, 1, 1]), dtype=float))


def mesh_edges(P16, idx, scale, cos_thr):
    """P16 (n,3) int16, idx (m,3). Returns (verts int16 (k,3), pairs (e,2) into verts)."""
    _, inv = np.unique(P16.astype(np.int32), axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    wpos = np.zeros((inv.max() + 1, 3), dtype=np.int16)
    wpos[inv] = P16
    R = wpos.astype(np.float64) / 32767.0 * scale
    T = inv[idx]                                         # (m,3) welded vertex ids
    e1, e2 = R[T[:, 1]] - R[T[:, 0]], R[T[:, 2]] - R[T[:, 0]]
    fn = np.cross(e1, e2)
    ln = np.linalg.norm(fn, axis=1)
    ok = ln > 1e-14 * max(1.0, float(scale.max()) ** 2)  # drop degenerate triangles
    T, fn, ln = T[ok], fn[ok], ln[ok]
    fn /= ln[:, None]
    m = len(T)
    if m == 0:
        return np.zeros((0, 3), np.int16), np.zeros((0, 2), np.int64)
    E = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    F = np.tile(np.arange(m), 3)
    lo, hi = E.min(1), E.max(1)
    keep_e = lo != hi
    lo, hi, F = lo[keep_e], hi[keep_e], F[keep_e]
    N = int(wpos.shape[0])
    key = lo.astype(np.int64) * N + hi
    order = np.argsort(key, kind="stable")
    key, F, lo, hi = key[order], F[order], lo[order], hi[order]
    start = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    cnt = np.diff(np.r_[start, len(key)])
    sel = np.zeros(len(start), bool)
    sel[cnt == 1] = True                                 # boundary
    two = np.flatnonzero(cnt == 2)
    if len(two):
        f0, f1 = F[start[two]], F[start[two] + 1]
        sel[two] = (fn[f0] * fn[f1]).sum(1) < cos_thr
    for g in np.flatnonzero(cnt > 2):                    # non-manifold: any sharp pair
        fs = F[start[g]: start[g] + cnt[g]]
        d = fn[fs] @ fn[fs].T
        sel[g] = bool((d < cos_thr).any())
    pairs = np.stack([lo[start[sel]], hi[start[sel]]], 1)
    used, newid = np.unique(pairs, return_inverse=True)
    return wpos[used], newid.reshape(-1, 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", help="step-import output dir (model.glb in it; edges.bin/json are written next to it)")
    ap.add_argument("--angle", type=float, default=30.0, help="crease threshold in degrees (default 30)")
    a = ap.parse_args()
    out = Path(a.out).expanduser()
    t0 = time.time()
    js, binc = read_glb(out / "model.glb")
    cos_thr = float(np.cos(np.radians(a.angle)))
    first = {}                                           # mesh index -> (node, part id)
    for n in js["nodes"]:
        if "mesh" in n and n["mesh"] not in first:
            first[n["mesh"]] = n
    blob, parts, block, ntri, nedge = bytearray(), {}, {}, 0, 0         # block: mesh index -> its edge block
    for mi, mesh in enumerate(js["meshes"]):
        n = first.get(mi)
        if n is None:
            continue
        pid = (n.get("extras") or {}).get("part") or n.get("name") or f"mesh{mi}"
        if pid in parts:
            print(f"warning: part id {pid} used by two meshes, the later one is skipped", file=sys.stderr)
            continue
        scale = node_scale(n)
        V, E = [], []
        base = 0
        for prim in mesh["primitives"]:
            if prim.get("mode", 4) != 4 or "indices" not in prim:
                continue
            P = accessor(js, binc, prim["attributes"]["POSITION"])
            if P.dtype != np.int16:
                sys.exit(f"mesh {mi}: POSITION is {P.dtype}, expected the importer's int16 (KHR_mesh_quantization)")
            idx = accessor(js, binc, prim["indices"]).reshape(-1).astype(np.int64).reshape(-1, 3)
            ntri += len(idx)
            v, e = mesh_edges(P, idx, scale, cos_thr)
            V.append(v); E.append(e + base); base += len(v)
        if not V or base == 0:
            continue
        V, E = np.concatenate(V), np.concatenate(E)
        wide = len(V) > 65535
        v4 = np.zeros((len(V), 4), np.int16); v4[:, :3] = V
        vo = len(blob)
        blob += v4.tobytes()
        io = len(blob)
        blob += E.astype(np.uint32 if wide else np.uint16).reshape(-1).tobytes()
        while len(blob) % 4:
            blob += b"\0"
        parts[pid] = block[mi] = [len(V), len(E), vo, io, 4 if wide else 2]
        nedge += len(E)
    for n in js["nodes"]:                                # every other instance of a mesh shares its first node's edge block
        pid = (n.get("extras") or {}).get("part")
        if "mesh" in n and pid and pid not in parts and n["mesh"] in block:
            parts[pid] = block[n["mesh"]]
    (out / "edges.bin").write_bytes(bytes(blob))
    (out / "edges.json").write_text(json.dumps({"version": 1, "angle": a.angle, "parts": parts, "bytes": len(blob)}, separators=(",", ":")), encoding="utf-8")
    print(f"{len(parts)} parts, {ntri} triangles -> {nedge} edges; edges.bin {len(blob) / 1e6:.2f} MB; {time.time() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
