"""Client-CAD migration fixes: parts_meta.json may set `name`/`node`; `counted_in` ties a vendor-assembly piece to its leader's bought
row; `derive.py print` lays the STL flat (largest planar face, or `print_rot`) and ships the part STEP beside it."""
import json
import re
import struct
import subprocess
import procgroup
import sys
from pathlib import Path

import helpers as H
from test_model_from_steps import MFS, _run, _src

sys.path.insert(0, str(H.SCRIPTS))


def _meta(root, meta):
    (root / "mechanics/export").mkdir(parents=True, exist_ok=True)
    (root / "mechanics/export/parts_meta.json").write_text(json.dumps(meta, ensure_ascii=False))


def test_meta_sets_name_and_node(tmp_path):
    root = _src(tmp_path / "p")
    _meta(root, {"03.01.01.301": {"name": "Head cheek 2", "node": "M1-1 03.04 Head"}})
    assert _run(root).returncode == 0
    p = H.by_id(root)["03.01.01.301"]
    assert p["name"] == "Head cheek 2" and p["node"] == "M1-1 03.04 Head"


def test_meta_never_replaces_id_or_key(tmp_path):
    root = _src(tmp_path / "p")
    _meta(root, {"03.01.01.301": {"id": "x", "key": "y", "step": "z"}})
    assert _run(root).returncode == 0
    p = H.by_id(root)["03.01.01.301"]
    assert p["key"] != "y" and p["step"].startswith("build/model/step/") and p["step"] != "z"


def _nut_project(tmp_path):
    from build123d import Cylinder, Pos, export_step
    root = _src(tmp_path / "p")
    rows = json.loads((root / "build/parts.json").read_text())
    nut = [r for r in rows["parts"] if r["id"] == "hex_nut_M16"][0]
    for pid in ("hex_nut_M16_b", "motor_cover"):
        export_step(Pos(0, 0, 0) * Cylinder(8, 6 + len(pid)), str(root / "build/parts" / f"{pid}.step"))
        rows["parts"].append(dict(nut, id=pid, name="Motor cover" if pid == "motor_cover" else nut["name"],
                                  files={"step": f"parts/{pid}.step"}, instances=8))
    (root / "build/parts.json").write_text(json.dumps(rows, ensure_ascii=False))
    return root


def test_counted_in_part_adds_no_qty_and_no_row(tmp_path):
    root = _nut_project(tmp_path)
    _meta(root, {"hex_nut_M16_b": {"counted_in": "hex_nut_M16"}, "motor_cover": {"counted_in": "hex_nut_M16"}})
    assert _run(root).returncode == 0
    cp = procgroup.run([sys.executable, str(H.DERIVE), "--no-rebuild", "bought", str(root / "m.json")], cwd=root,
                        env=H.env_for(root), capture_output=True, text=True)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    rows = json.loads((root / "m.json").read_text())["bought"]
    assert len(rows) == 1 and rows[0]["qty"] == 8 and rows[0]["name"] == "hex nut M16"


def test_counted_in_must_name_an_existing_bought_part(tmp_path):
    root = _nut_project(tmp_path)
    _meta(root, {"motor_cover": {"counted_in": "nope"}})
    cp = _run(root)
    assert cp.returncode != 0 and "counted_in" in cp.stdout + cp.stderr
    _meta(root, {"motor_cover": {"counted_in": "03.01.01.301"}})           # exists, but is a laser sheet
    cp = _run(root)
    assert cp.returncode != 0 and "counted_in" in cp.stdout + cp.stderr


def _bbox(stl):
    b = Path(stl).read_bytes()
    if b[:5] == b"solid" and b"facet" in b[:400]:
        v = [tuple(map(float, m)) for m in re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", b)]
    else:
        n = struct.unpack("<I", b[80:84])[0]
        v = []
        for i in range(n):
            f = struct.unpack("<12fH", b[84 + 50 * i:134 + 50 * i])
            v += [f[3:6], f[6:9], f[9:12]]
    return [min(c[i] for c in v) for i in range(3)], [max(c[i] for c in v) for i in range(3)]


def _print_proj(tmp_path, **fields):
    from build123d import Box, Pos, Rot, export_step
    root = H.make_derived_model(tmp_path / "p")
    H.replace_part(root, "knob", shape=Pos(100, 0, 0) * Rot(20, 30, 40) * Box(10, 20, 30), **fields)   # a tilted 10x20x30 block
    return root


def test_print_lays_the_part_flat_on_its_largest_planar_face(tmp_path):
    root = _print_proj(tmp_path)
    d = H.run_derive(root, "print").ok()
    stl = [f for f in H.file_list(H.entry(d, "knob"), root) if f.suffix == ".stl"][0]
    lo, hi = _bbox(stl)
    assert all(abs(c) < 1e-3 for c in lo), lo
    assert abs(hi[2] - 10) < 0.05, (lo, hi)                       # the 20x30 face is down, so it stands 10 high


def test_print_rot_overrides_the_lay_flat_choice(tmp_path):
    root = _print_proj(tmp_path)
    H.replace_part(root, "knob", print_rot=[0, 0, 0])             # as modelled (tilted), only dropped to z=0
    d = H.run_derive(root, "print").ok()
    stl = [f for f in H.file_list(H.entry(d, "knob"), root) if f.suffix == ".stl"][0]
    lo, hi = _bbox(stl)
    assert abs(lo[2]) < 1e-3 and hi[2] > 20, (lo, hi)


def test_print_ships_the_part_step_and_second_run_computes_nothing(tmp_path):
    root = _print_proj(tmp_path)
    d = H.run_derive(root, "print").ok()
    files = H.file_list(H.entry(d, "knob"), root)
    assert sorted(f.suffix for f in files) == [".step", ".stl"] and all(f.is_file() for f in files)
    d2 = H.run_derive(root, "print").ok()
    assert d2.computed == 0, d2.log
