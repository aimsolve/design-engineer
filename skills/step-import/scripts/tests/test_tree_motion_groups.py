"""The viewer model of a converted client tree (write_model_dir with `path`): copies of one part in different motion groups
keep their own group, on their GLB node (extras.motion_group), which motion.mjs reads before the part's (review 4).

  python3 -m pytest -q test_tree_motion_groups.py
"""
import json
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import step_import  # noqa: E402


def cube():
    v = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    t = np.array([[0, 1, 2], [0, 1, 3], [0, 2, 3], [1, 2, 3]])
    return v, t, np.zeros((4, 3), dtype=np.int8)


def item(pid, name, path, group, x):
    v, t, qn = cube()
    m = np.eye(4)
    m[0, 3] = x
    return {"id": pid, "name": name, "node": "n", "path": path, "color": None, "mesh_key": pid, "matrix": m.reshape(-1).tolist(),
            "motion_group": group, "card": None, "v": v, "t": t, "qn": qn, "volume_mm3": 1.0, "bbox_mm": [[0, 0, 0], [1, 1, 1]],
            "step_src": None, "stl_src": None}


def glb_nodes(path):
    b = Path(path).read_bytes()
    n = struct.unpack("<I", b[12:16])[0]
    return json.loads(b[20:20 + n])["nodes"]


def test_each_copy_of_a_part_keeps_its_own_motion_group(tmp_path):
    pusher, frame = [(("a",), "Pusher")], [(("b",), "Frame")]
    items = [item("screw", "Screw M6", pusher, "pusher", 0), item("screw", "Screw M6", frame, None, 10),
             item("plate", "Plate", frame, None, 20)]
    step_import.write_model_dir(str(tmp_path), items, "Machine")
    leaves = [n for n in glb_nodes(tmp_path / "model.glb") if "mesh" in n]
    groups = sorted((n["extras"]["part"], n["extras"].get("motion_group")) for n in leaves)
    assert groups == [("plate", "static"), ("screw", "pusher"), ("screw", "static")], groups
    cat = {p["id"]: p for p in json.loads((tmp_path / "parts.json").read_text())["parts"]}
    assert cat["screw"].get("motion_group") == "pusher", "the catalogue entry takes the group of a moving copy"


def test_a_tree_with_no_motion_writes_no_groups(tmp_path):
    items = [item("screw", "Screw M6", [(("a",), "Pusher")], None, 0), item("screw", "Screw M6", [(("b",), "Frame")], None, 10)]
    step_import.write_model_dir(str(tmp_path), items, "Machine")
    assert all("motion_group" not in n.get("extras", {}) for n in glb_nodes(tmp_path / "model.glb"))
