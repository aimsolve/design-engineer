"""cad_incremental --model: a reused part file / node hands its parts back as lazy shapes. A no-change run must not read the part
STEPs back (that was most of its time on a 257-part model); anything that touches a shape still gets the real thing.
Runs on helpers.make_project (folder layout); assemble.py templates call every node's build()."""
import json
import os
import subprocess
import procgroup
import sys
from pathlib import Path

import helpers as H

SITECUSTOMIZE = '''
import os, uuid
import build123d
_real = build123d.import_step
def _counting(*a, **k):
    d = os.environ["STEP_READS_DIR"]
    os.makedirs(d, exist_ok=True)
    os.close(os.open(os.path.join(d, uuid.uuid4().hex), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    return _real(*a, **k)
build123d.import_step = _counting
'''

LIGHT = '''
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parts import {mods}
out = []
{calls}
for p in out:
    p["name"], p["process"]                      # the dict is usable; the shape is not touched
'''

TOUCH = '''
import copy, json, os, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build123d import Compound, Shape, export_step, import_step
from parts import {mods}
out = []
{calls}
res = {{}}
for p in out:
    sh = p["shape"]
    bb = sh.bounding_box()
    res[p["name"]] = dict(cls=type(sh).__name__, is_shape=isinstance(sh, Shape), label=sh.label, volume=round(sh.volume, 6),
                          bbox=[round(x, 6) for x in (bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z)],
                          color=None if sh.color is None else [round(float(x), 4) for x in sh.color],
                          moved=round(sh.moved(__import__("build123d").Location((1, 2, 3))).bounding_box().min.X, 6),
                          cp=round(copy.copy(sh).volume, 6), n_solids=len(sh.solids()), h=hash(sh) == hash(sh),
                          kids=[[type(c).__name__, c.parent is sh] for c in (getattr(sh, "children", None) or ())])
    export_step(Compound(children=[sh]), str(Path(os.environ["OUT_DIR"]) / (p["name"] + ".step")))
    assert abs(import_step(str(Path(os.environ["OUT_DIR"]) / (p["name"] + ".step"))).volume - sh.volume) < 1e-3
Path(os.environ["OUT_DIR"], "res.json").write_text(json.dumps(res, sort_keys=True))
'''

LABELS = '''
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parts import {mods}
out = []
{calls}
for p in out:
    p["shape"].label, p["shape"].color             # cheap attributes: no STEP read
'''


def _assemble(root, template):
    mods = list(H.FUNCS)
    calls = "\n".join(f"out += {m}.{H.FUNCS[m]}()" for m in mods)
    (H.cad_dir(root) / "assemble.py").write_text(template.format(mods=", ".join(mods), calls=calls))


def _run(root, reads=None, out=None):
    env = H.env_for(root)
    sc = Path(root).parent / "sc"
    sc.mkdir(exist_ok=True)
    (sc / "sitecustomize.py").write_text(SITECUSTOMIZE)
    env["PYTHONPATH"] = str(sc)
    env["STEP_READS_DIR"] = str(reads or Path(root).parent / "reads")
    if out:
        Path(out).mkdir(exist_ok=True)
        env["OUT_DIR"] = str(out)
    cp = procgroup.run([sys.executable, str(H.CAD_INC), "--model", "assemble.py"], cwd=H.cad_dir(root), env=env,
                        capture_output=True, text=True, timeout=300)
    return H.Run(cp), len(list(Path(env["STEP_READS_DIR"]).glob("*"))) if Path(env["STEP_READS_DIR"]).is_dir() else 0


def _reset(root):
    import shutil
    shutil.rmtree(Path(root).parent / "reads", ignore_errors=True)


def test_no_change_run_does_not_read_any_part_step(fresh):
    _assemble(fresh, LIGHT)
    cold, _ = _run(fresh)
    cold.ok()
    _reset(fresh)
    r, reads = _run(fresh)
    r.ok()
    assert r.summary == (0, H.N_UNITS, len(H.PART_IDS)), r.log[-600:]
    assert reads == 0, f"{reads} part STEPs were imported on a no-change run"


def test_label_and_color_of_a_reused_shape_need_no_step_read(fresh):
    _assemble(fresh, LABELS)
    _run(fresh)[0].ok()
    _reset(fresh)
    r, reads = _run(fresh)
    r.ok()
    assert reads == 0


def test_a_lazy_shape_behaves_like_the_eager_one_on_first_touch(fresh, tmp_path):
    _assemble(fresh, TOUCH)
    cold, _ = _run(fresh, out=tmp_path / "cold")
    cold.ok()
    _reset(fresh)
    warm, reads = _run(fresh, out=tmp_path / "warm")
    warm.ok()
    assert warm.summary[0] == 0
    assert reads >= len(H.PART_IDS), "touching every shape must read every STEP (lazily)"
    a = json.loads((tmp_path / "cold/res.json").read_text())
    b = json.loads((tmp_path / "warm/res.json").read_text())
    assert set(a) == H.PART_IDS and a == b
    assert all(v["is_shape"] and v["h"] for v in b.values())


def test_a_record_without_a_shape_class_is_read_eagerly_once_then_lazy(fresh):
    _assemble(fresh, LIGHT)
    _run(fresh)[0].ok()
    nodes = fresh / "build/model/nodes.json"
    data = json.loads(nodes.read_text())
    for rec in data.values():
        for p in rec["parts"]:
            p.pop("lazy", None)
    nodes.write_text(json.dumps(data))
    _reset(fresh)
    r, reads = _run(fresh)
    r.ok()
    assert r.summary[0] == 0 and reads > 0                     # an old record still works (read eagerly) ...
    assert all("lazy" in p for rec in json.loads(nodes.read_text()).values() for p in rec["parts"] if not p.get("rowonly"))
    _reset(fresh)
    assert _run(fresh)[1] == 0                                  # ... and is upgraded for the next run


def test_a_lazy_part_that_is_edited_downstream_still_rebuilds_the_caller(fresh):
    """caller/caller_part.py reads callee.build()[0]['shape'] (a lazy shape on a no-change run); a param change must still
    propagate."""
    _assemble(fresh, LIGHT)
    _run(fresh)[0].ok()
    H.edit(H.cad_dir(fresh) / "params.py", "C_D = 3.0", "C_D = 5.0")
    r, _ = _run(fresh)
    r.ok()
    assert H.units_for("callee_part", "caller_part") <= r.rebuilt, r.rebuilt
    by = H.by_id(fresh)
    from build123d import import_step
    assert abs(import_step(str(fresh / by["caller_part"]["step"])).bounding_box().size.Z - 6.0) < 1e-6
    assert abs(import_step(str(fresh / by["callee_part"]["step"])).bounding_box().size.Z - 5.0) < 1e-6


DISPATCH = '''
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build123d import Shape
from parts import {mods}
out = []
{calls}
res = {{}}
for p in out:
    sh = p["shape"]
    cls = sh.__class__                              # first touch of the shape: answered from the record, the STEP is not read
    unread = type(sh) is not cls                    # still the stand-in: neither __class__ nor isinstance() loaded it
    inst, shape = isinstance(sh, cls), isinstance(sh, Shape)
    unread = unread and type(sh) is not cls
    sh.volume                                       # geometry: now it is read, and type() is the real class
    res[p["name"]] = dict(is_cls=cls.__name__, same=cls is sh.__class__, inst=inst, shape=shape, unread=unread,
                          exact=type(sh) is cls, lazy="__getattribute__" in vars(cls))
Path(os.environ["OUT_DIR"], "res.json").write_text(json.dumps(res, sort_keys=True))
'''

MISSING = '''
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parts import {mods}
out = []
{calls}
res = {{}}
for p in out:
    sh = p["shape"]
    lz = object.__getattribute__(sh, "__dict__").get("_lz")
    if lz is None:
        continue
    os.rename(lz[0], lz[0] + ".gone")
    errs = []
    for attr in ("volume", "volume", "bounding_box", "wrapped"):
        try:
            getattr(sh, attr)
            errs.append("none")
        except BaseException as e:
            errs.append(type(e).__name__)
    res[p["name"]] = errs
    os.rename(lz[0] + ".gone", lz[0])
Path(os.environ["OUT_DIR"], "res.json").write_text(json.dumps(res, sort_keys=True))
'''

COPIES = '''
import copy, json, os, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parts import {mods}
out = []
{calls}
res = {{}}
for p in out:
    sh = p["shape"]
    unloaded = "_lz" in object.__getattribute__(sh, "__dict__")
    c = copy.copy(sh)
    res[p["name"]] = dict(unloaded=unloaded, cp=round(c.volume, 6), cls=type(c).__name__, label=c.label)
for p in out:
    sh = p["shape"]
    d = copy.deepcopy(sh)
    res[p["name"]]["dp"] = round(d.volume, 6)
    res[p["name"]]["vol"] = round(sh.volume, 6)
Path(os.environ["OUT_DIR"], "res.json").write_text(json.dumps(res, sort_keys=True))
'''

RELAY = '''
from parts import callee


def build():
    p = callee.build()[0]
    return {"name": "relay_part", "shape": p["shape"], "process": "mill"}
'''

RELAY_TOUCH = '''
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parts import callee, relay
out = relay.build() + callee.build()
res = {{}}
for p in out:
    sh = p["shape"]
    bb = sh.bounding_box()
    res[p["name"]] = dict(volume=round(sh.volume, 6), z=round(bb.size.Z, 6), cls=sh.__class__.__name__, label=sh.label)
Path(os.environ["OUT_DIR"], "res.json").write_text(json.dumps(res, sort_keys=True))
'''


def _cold_warm(fresh, tmp_path, template):
    _assemble(fresh, template)
    cold, _ = _run(fresh, out=tmp_path / "cold")
    cold.ok()
    _reset(fresh)
    warm, _ = _run(fresh, out=tmp_path / "warm")
    warm.ok()
    assert warm.summary[0] == 0
    return (json.loads((tmp_path / "cold/res.json").read_text()) if (tmp_path / "cold/res.json").exists() else None,
            json.loads((tmp_path / "warm/res.json").read_text()))


def test_class_dispatch_on_a_reused_shape_matches_a_fresh_one(fresh, tmp_path):
    """sh.__class__ is the real class from the first touch, and neither it nor isinstance() reads the STEP (a failed
    isinstance() falls back to __class__, which used to load every reused part); the first geometry access does, and from then
    on type(sh) is the real class too."""
    cold, warm = _cold_warm(fresh, tmp_path, DISPATCH)
    assert cold == warm
    assert all(v["inst"] and v["shape"] and v["same"] and v["unread"] and v["exact"] and not v["lazy"] for v in warm.values())
    assert not any(v["is_cls"] == "Shape" for v in warm.values())


def test_a_failed_step_read_raises_the_real_error_on_every_access(fresh, tmp_path):
    _assemble(fresh, MISSING)
    cold, _ = _run(fresh, out=tmp_path / "cold")
    cold.ok()
    _reset(fresh)
    warm, _ = _run(fresh, out=tmp_path / "warm")
    warm.ok()
    res = json.loads((tmp_path / "warm/res.json").read_text())
    assert set(res) == H.PART_IDS
    assert all(e == ["FileNotFoundError"] * 4 for e in res.values()), res


def test_copy_and_deepcopy_of_an_unloaded_reused_shape(fresh, tmp_path):
    cold, warm = _cold_warm(fresh, tmp_path, COPIES)
    assert all(v["unloaded"] for v in warm.values())
    # (the old layout also asserted that the cold run handed eager shapes; in the folder layout a placed part on the cold run
    # may already be the stored, lazy one, so the cold run is only the reference for the values below)
    for k, v in warm.items():
        c = cold[k]
        assert (v["cp"], v["dp"], v["vol"], v["cls"], v["label"]) == (c["cp"], c["dp"], c["vol"], c["cls"], c["label"]), k
        assert v["cp"] == v["dp"] == v["vol"] > 0


def test_a_part_file_returning_another_nodes_lazy_shape_as_its_own_part(fresh, tmp_path):
    H.write_node(fresh, "relay", {"relay_part": RELAY})
    (H.cad_dir(fresh) / "assemble.py").write_text(RELAY_TOUCH.replace("{{", "{").replace("}}", "}"))
    cold, _ = _run(fresh, out=tmp_path / "cold")
    cold.ok()
    _reset(fresh)
    warm, _ = _run(fresh, out=tmp_path / "warm")
    warm.ok()
    a = json.loads((tmp_path / "cold/res.json").read_text())
    b = json.loads((tmp_path / "warm/res.json").read_text())
    assert a == b and b["relay_part"]["volume"] == b["callee_part"]["volume"] > 0
