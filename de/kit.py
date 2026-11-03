"""Download kits of the local viewer: what the router's /kit/<project>/ does on the platform, for one project on this machine.

kit.json `scopes` (and the assembly drawings, which every derive.py model has) are exported on request: a scope is fresh when its
manifest is newer than the model (build/model/parts.json) and kit.json; a stale one is exported in a thread while the page polls
/status (202 meanwhile, as on the platform). The files are packed from the manifest the way the page expects them: the model STEP,
a zip of the parts' files per process / node / part, the purchase list. Every path a manifest names must stay inside the project."""
import csv
import io
import json
import threading
import time
import zipfile
from pathlib import Path

SCOPES = ("print", "laser", "bought", "step", "assembly", "electric", "pneumatic")
ASSEMBLY = "S=${DE_ROOT:-/opt/design-engineer}/skills/machine-project/scripts; python $S/derive.py assembly build/kit/assembly.json"
_lock = threading.Lock()
_jobs = {}   # (project, scope) -> {"since": t, "thread": Thread}
_fail = {}   # (project, scope) -> the fingerprint (model mtime) that failed, with the log tail


def units(d):
    """scope -> {export, manifest, timeout} of what this project can export; {} without kit.json."""
    try:
        cfg = json.loads((d / "kit.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for s, e in (cfg.get("scopes") or {}).items():
        if s in SCOPES and isinstance(e, dict) and e.get("export"):
            out[s] = {"export": e["export"], "manifest": e.get("manifest", f"build/kit/{s}.json"), "timeout": int(e.get("timeout") or 1200)}
    if "assembly" not in out and any("derive.py" in u["export"] for u in out.values()):
        out["assembly"] = {"export": ASSEMBLY, "manifest": "build/kit/assembly.json", "timeout": 1200}
    return out


def _mtime(p):
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return 0


def model_stamp(d):
    return max(_mtime(d / "build" / "model" / "parts.json"), _mtime(d / "kit.json"))


def fresh(d, u):
    m = _mtime(d / u["manifest"])
    return bool(m) and m >= model_stamp(d)


def manifest(d, u):
    try:
        m = json.loads((d / u["manifest"]).read_text(encoding="utf-8"))
        return m if isinstance(m, dict) else None
    except (OSError, ValueError):
        return None


def inside(d, f):
    """The project file a manifest names, or None (missing, absolute, or outside the project)."""
    if not isinstance(f, str) or not f or f.startswith("/") or ".." in Path(f).parts:
        return None
    p = (d / f).resolve()
    return p if p.is_file() and d.resolve() in p.parents else None


def export(d, scope, echo=True):
    """Run one scope's export now, in the foreground: (200, manifest path) or (502, log tail)."""
    from de import cli
    u = units(d).get(scope)
    if not u:
        return 404, f"this project exports no `{scope}` (kit.json scopes: {', '.join(units(d)) or 'none'})"
    log = d / ".engineer" / "kit" / f"{scope}.log"
    with cli.model_lock(d):
        stamp = model_stamp(d)   # the model this export is of: the one on disk once the lock is ours
        code = cli.run_logged(u["export"], d, log, u["timeout"], echo)
    if code:
        tail = log.read_text(encoding="utf-8", errors="replace")[-1500:]
        _fail[(str(d), scope)] = (stamp, tail)
        return 502, tail
    _fail.pop((str(d), scope), None)
    return 200, u["manifest"]


def _start(d, scope):
    key = (str(d), scope)
    with _lock:
        j = _jobs.get(key)
        if j and j["thread"].is_alive():
            return j
        t = threading.Thread(target=export, args=(d, scope, False), daemon=True)
        _jobs[key] = {"since": time.time(), "thread": t}
        t.start()
        return _jobs[key]


def building(d, scope):
    j = _jobs.get((str(d), scope))
    return j if j and j["thread"].is_alive() else None


def failed(d, scope):
    f = _fail.get((str(d), scope))
    return f if f and f[0] == model_stamp(d) else None


def scope_of(rest, us):
    """The unit a request needs: a panel scope, or the print + laser kits for one part / node."""
    if rest in us:
        return [rest]
    if rest.startswith(("part/", "node/")):
        return [s for s in ("print", "laser", "step") if s in us]
    return None


def status(d, want=None):
    us = units(d)
    need = (scope_of(want, us) if want else list(us)) or []
    b = [building(d, s) for s in need]
    f = [s for s in need if failed(d, s)]
    view = merged(d, us)
    ps = [x for x in view.get("parts") or [] if x.get("files")]
    have = [s for s in SCOPES if s in us and (manifest(d, us[s]) is None or has(view, s))]
    return {"enabled": bool(us), "fresh": all(fresh(d, us[s]) for s in need) and not f, "failed": bool(f) and not any(b),
            "building": any(b), "since": min((x["since"] for x in b if x), default=None), "est_secs": None, "queue_pos": 0,
            "parts": sorted({x["id"] for x in ps if x.get("id")}), "nodes": sorted({x["node"] for x in ps if x.get("node")}),
            "print_parts": None, "scopes": have, "off": {s: ("none" if s in us or s in ("electric", "pneumatic") else "setup") for s in SCOPES if s not in have}}


def merged(d, us):
    out = {"parts": []}
    for s, u in us.items():
        m = manifest(d, u) or {}
        out["parts"] += [dict(x, _scope=s) for x in m.get("parts") or [] if isinstance(x, dict)]
        for k in ("model_step", "bought", "bought_xlsx", "assembly"):
            if m.get(k):
                out[k] = m[k]
    return out


def has(m, scope):
    ps = [x for x in m.get("parts") or [] if x.get("files")]
    return {"print": any(x.get("process") == "print" for x in ps), "laser": any(x.get("process") in ("laser", "bent") for x in ps),
            "bought": bool(m.get("bought") or m.get("bought_xlsx")), "step": bool(m.get("model_step")),
            "assembly": any(isinstance(e, dict) and e.get("files") for e in m.get("assembly") or [])}.get(scope, False)


def get(d, rest, title):
    """GET /kit/<project>/<rest> -> (code, json) or (200, (bytes, file name, content type))."""
    us = units(d)
    if not us:
        return 404, {"error": "this project has no kit.json"}
    if rest.startswith("3mf/"):
        return 404, {"error": "opening in Bambu Studio by link works on the platform only: download the STL with the 3D-print button"}
    need = scope_of(rest, us)
    if not need:
        return 404, {"error": "no such file set"}
    stale = [s for s in need if not fresh(d, us[s])]
    if [s for s in stale if failed(d, s)] and not any(building(d, s) for s in stale):
        return 502, {"error": "the files could not be built from the current model: " + failed(d, stale[0])[1][-300:]}
    if stale:
        js = [_start(d, s) for s in stale]
        return 202, {"building": True, "since": min(j["since"] for j in js), "est_secs": None}
    return pack(d, merged(d, {s: us[s] for s in need}), rest, title)


def pack(d, m, scope, title):
    if scope == "step":
        f = inside(d, m.get("model_step"))
        return (200, (f.read_bytes(), f"{title} - STEP{f.suffix}", "application/step")) if f else (404, {"error": "the export has no STEP"})
    if scope == "bought":
        f = inside(d, m.get("bought_xlsx"))
        if f:
            return 200, (f.read_bytes(), f"{title} - bought parts{f.suffix}", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        rows = [r for r in m.get("bought") or [] if isinstance(r, dict)]
        if not rows:
            return 404, {"error": "no bought parts"}
        buf = io.StringIO()
        keys = sorted({k for r in rows for k in r if not isinstance(r[k], (dict, list))})
        w = csv.DictWriter(buf, keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        return 200, (buf.getvalue().encode("utf-8-sig"), f"{title} - bought parts.csv", "text/csv; charset=utf-8")
    if scope == "assembly":
        groups = [(str(e.get("node") or "node"), e.get("files") or []) for e in m.get("assembly") or [] if isinstance(e, dict)]
    else:
        parts = [x for x in m.get("parts") or [] if x.get("files")]
        pick = {"print": lambda x: x.get("process") == "print", "laser": lambda x: x.get("process") in ("laser", "bent")}.get(scope)
        if scope.startswith("part/"):
            pick = lambda x: x.get("id") == scope[5:]   # noqa: E731
        elif scope.startswith("node/"):
            pick = lambda x: x.get("node") == scope[5:]   # noqa: E731
        groups = [(str(x.get("node") or x.get("process") or ""), x["files"]) for x in parts if pick and pick(x)]
    files = [(g, inside(d, f), f) for g, fs in groups for f in fs]
    if not files:
        return 404, {"error": "no files for this"}
    if any(p is None for _, p, _ in files):
        return 500, {"error": "the export is incomplete: " + ", ".join(str(f) for _, p, f in files if p is None)[:300]}
    buf, used = io.BytesIO(), set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for g, p, _ in files:
            arc, n = "/".join(filter(None, [g, p.name])), 2
            while arc in used:
                arc, n = "/".join(filter(None, [g, f"{p.stem} ({n}){p.suffix}"])), n + 1
            used.add(arc)
            z.write(p, arc)
    return 200, (buf.getvalue(), f"{title} - {scope.replace('/', ' ')}.zip", "application/zip")
