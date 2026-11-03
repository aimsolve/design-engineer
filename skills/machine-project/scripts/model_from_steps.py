#!/usr/bin/env python3
"""Model of a client-CAD project (no node code): build/model/ from an existing per-part STEP set and a parts list.

  python model_from_steps.py ROOT PARTS_JSON STEP_DIR

PARTS_JSON = {"parts": [row, ...]} or a bare list; a row has at least `id` and `name`, and the STEP is STEP_DIR/<basename of
row.files.step> (or STEP_DIR/<id>.step). Writes ROOT/build/model/parts.json (+ an empty nodes.json) and step/<key>.step, the same
shape `cad_incremental.py --model` writes, so `derive.py` works unchanged. key = sha of (the source STEP bytes, id, the source row,
the override); a changed part gets a new key, an unchanged one is reused as is. Whatever the model does not reference is deleted.

Row -> model part (the decisions the client's own exporter used to make):
  kind purchased | standard                -> process bought; code = ISO/DIN article (params.standard + thread [+ length], or
                                              params.model / designation), else the name; qty = instances; unit pcs;
                                              kind = bought_kind; class = type or material
  kind made, material printed              -> process print, material PLA
  kind made, other material                -> a flat sheet (smallest bbox side <= 6 mm and the solid is that thick all over) is
                                              process laser, thickness = that side, material steel (in the project's language); everything else process other
  every part: id, name, node = module, color, qty = instances, code = drawing (the cipher), func "steps"
Bending cannot be told from the geometry in a way that is safe for a drawing batch, so `bent` (and weld, finish, a different
material or thickness, a corrected qty/code...) comes from the per-project override ROOT/mechanics/export/parts_meta.json:
{"<id>": {"process": "bent", "thickness": 3.0, "weld": "A", ...}} - its fields replace the generated ones, `name` and `node` included
(two parts with one name get distinct drawings by naming one of them here); only id/key/step/sha256 are not overridable.
A piece of a bought vendor assembly (a motor's housing, a driver's board) says `"counted_in": "<id of the leader>"`: derive bought then
lists no row and adds no qty for it - the leader's row (its `qty` = how many assemblies) is the product.
"""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cad_incremental import project_lock      # stdlib-only at import
import text_i18n as T  # noqa: E402

SHEET_MAX_T = 6.0


def _sha(*objs):
    h = hashlib.sha256()
    for o in objs:
        h.update(o if isinstance(o, bytes) else json.dumps(o, sort_keys=True, ensure_ascii=False).encode())
        h.update(b"\0")
    return h.hexdigest()


def _article(row):
    q = row.get("params") or {}
    if q.get("fastener_type") or q.get("thread"):
        a = " ".join(x for x in (q.get("standard", ""), q.get("thread", "")) if x)
        return a + (f"x{q['length_mm']:g}" if q.get("length_mm") else "")
    for k in ("model", "designation"):
        if q.get(k):
            return str(q[k])
    return row["name"]


def _flat_thickness(step):
    """Thickness of a flat sheet blank, else None: the smallest bbox side is <= 6 mm, the solid is that thick all over and it is a plain
    extrusion (the largest planar face area = volume / thickness within 3 %; a pocketed or stepped plate is machined, not cut)."""
    from build123d import import_step
    sh = import_step(str(step))
    size = sorted([sh.bounding_box().size.X, sh.bounding_box().size.Y, sh.bounding_box().size.Z])
    t = size[0]
    if t > SHEET_MAX_T or t <= 0 or sh.area <= 0 or 2 * sh.volume / sh.area < 0.8 * t:
        return None
    face = max((f.area for f in sh.faces() if str(f.geom_type).endswith("PLANE")), default=0)
    if abs(face - sh.volume / t) > 0.03 * sh.volume / t:
        return None
    return round(t, 1)




def _code_sha(lang=T.DEFAULT):
    """The classifier's own code is part of every key: changing a rule re-classifies the parts; so is the language of the
    texts it writes (the material)."""
    import inspect
    return _sha(*[inspect.getsource(f) for f in (classify, _flat_thickness, _article)], T.t("material.steel", lang), SHEET_MAX_T)


def classify(row, step, lang=T.DEFAULT):
    base = {"qty": row.get("instances", 1)}
    if row.get("kind") in ("purchased", "standard"):
        base.update(process="bought", code=_article(row), unit="pcs", kind=row.get("bought_kind", ""),
                    **{"class": row.get("type") or row.get("material") or ""})
        return base
    if row.get("drawing"):
        base["code"] = row["drawing"]
    if row.get("material") == "printed":
        return dict(base, process="print", material="PLA")
    steel = T.t("material.steel", lang)
    mat = {"steel": steel}.get(row.get("material"), row.get("material")) or steel      # the row's own material (steel: in the project's language)
    t = _flat_thickness(step)
    if t is None:
        return dict(base, process="other", material=mat)
    return dict(base, process="laser", material=mat, thickness=t)


def main(root, parts_json, step_dir):
    root, step_dir = Path(root), Path(step_dir)
    src = json.loads(Path(parts_json).read_text(encoding="utf-8"))
    rows = src["parts"] if isinstance(src, dict) else src
    meta_f = root / "mechanics" / "export" / "parts_meta.json"
    meta = json.loads(meta_f.read_text(encoding="utf-8")) if meta_f.is_file() else {}
    model = root / "build" / "model"
    (model / "step").mkdir(parents=True, exist_ok=True)
    try:
        old = {p["key"]: p for p in json.loads((model / "parts.json").read_text())["parts"]}
    except (OSError, ValueError, KeyError):
        old = {}
    parts, ids, used = [], set(), {}
    lang = T.project_lang(root)
    code = _code_sha(lang)
    for row in rows:
        pid = row["id"]
        if pid in ids:
            sys.exit(f"[model_from_steps] duplicate part id {pid!r}")
        ids.add(pid)
        f = step_dir / (Path((row.get("files") or {}).get("step") or f"{pid}.step").name)
        if f in used:
            sys.exit(f"[model_from_steps] parts {used[f]!r} and {pid!r} both point at the STEP {f.name}")
        used[f] = pid
        if not f.is_file():
            sys.exit(f"[model_from_steps] part {pid!r}: STEP {f} not found")
        data = f.read_bytes()
        key = _sha("model-from-steps-v1", code, data, pid, row, meta.get(pid, {}))
        if key in old:
            part = old[key]
        else:
            part = dict(classify(row, f, lang))
            part.update(id=pid, name=row["name"], node=row.get("module") or "parts", func="steps", color=row.get("color"))
            part.update(meta.get(pid, {}))                        # the override wins, name and node included
            part.update(id=pid, key=key, sha256=hashlib.sha256(data).hexdigest(), step=f"build/model/step/{key}.step")
        dst = root / part["step"]
        if not dst.is_file():
            tmp = dst.with_name(f".tmp-{os.getpid()}.step")
            tmp.write_bytes(data)
            os.replace(tmp, dst)
        parts.append(part)
    by_id = {p["id"]: p for p in parts}
    for p in parts:
        if p.get("counted_in") is not None:
            lead = by_id.get(p["counted_in"])
            if lead is None or lead["process"] != "bought" or p["process"] != "bought" or lead.get("counted_in") is not None:
                sys.exit(f"[model_from_steps] part {p['id']!r}: counted_in {p['counted_in']!r} must be the id of another bought part "
                         "that is not itself counted_in")
    for fn, obj in (("parts.json", {"parts": parts}), ("nodes.json", {})):
        tmp = model / f"{fn}.tmp-{os.getpid()}"
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, model / fn)
    keep = {p["key"] + ".step" for p in parts}
    for f in (model / "step").iterdir():
        if f.name not in keep and not f.name.startswith(".tmp-"):
            f.unlink()
    print(f"[model_from_steps] parts {len(parts)}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    with project_lock(os.path.abspath(sys.argv[1])):
        main(*sys.argv[1:])
