#!/usr/bin/env python3
"""The part library of a machine project: three levels, one import name, one mirror in the project.

Not named library.py: this folder is on sys.path during a model build, and a module of that name would shadow the
`library` namespace package the projects import.

  python3 part_library.py roots [<project dir>]            # which library levels this container sees
  python3 part_library.py sync <project dir>               # mirror the groups the project imports into mechanics/cad/library
  python3 part_library.py check <project dir>              # the mirror is what its sources were, and holds what is imported

A library part is a part file of #15 (`build(**args)` -> `geom.P(...)`, build123d joints of #16) living at
`<root>/library/<group>/<part>.py`, with the maker's STEP beside it when there is one. Three levels, in three places:

  catalog     <plugin>/catalog/library               the manufacturers' catalogue, in the framework: every project
  client      /clients/<client id>/library               designed for that client: only his projects (same mount as his rules.md)
  workspace   /workspace/library                     shared inside one workspace: only its projects

A level is visible because its folder is mounted into this container, and for no other reason: what is not mounted
cannot be read, so it cannot be copied and cannot be imported. The router mounts a client's folder by its own record of
the project's clients and a workspace's by the project's workspace, which is what keeps one client's parts out of another
client's projects. Nothing here enforces that; it only works with what it was given.

`sync` copies every file of each group the project's code imports into `<project>/mechanics/cad/library/<group>/`.
That mirror, not the mounted folder, is what the model is built from, because a model build runs in a job container
on the VPS, the PC or the Mac, which gets the files of the PROJECT and nothing else. It also puts the library inside
`mechanics/cad`, where the incremental build already hashes module code and opened files: editing a library part
rebuilds exactly the project parts that import it. `.sources.json` in the mirror records each file's level and sha1,
so `check` can tell a mirror that was edited by hand (the source is the place to edit) from one that is current.
A sync sees only the levels mounted where it runs. The router runs it in the project's own container after every turn,
before the standard gate and the model build read the files; a group of a level that is not mounted there (a client
who left the chat) is refused with its name, and the router tells the agent.

Plain python3, no dependencies: it runs in the project container and in a job container.
"""
import argparse
import ast
import hashlib
import json
import os
import re
import sys

CAD = os.path.join("mechanics", "cad")
MIRROR = os.path.join(CAD, "library")
STAMP = ".sources.json"
PKG = "library"
DEFAULT_CATALOG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "catalog"))   # agent/catalog (/opt/design-engineer/catalog on the platform)
CLIENTS_DIR = "/clients"
WORKSPACE = "/workspace"
TOP_SKIP = {"library", "build", "vendor"}            # directly under mechanics/cad only: a node may be called vendor
SKIP_DIRS = {"__pycache__", ".git", ".venv", "node_modules"}
LEVEL_RE = re.compile(r"catalog|client:\d+|workspace")   # how the platform spells a level (see roots())
IMPORT_RE = re.compile(rf"^\s*(?:import\s+{PKG}\.(\w+)|from\s+{PKG}\.(\w+)|from\s+{PKG}\s+import\s+([\w\s,]+))", re.M)


# ---------------------------------------------------------------- roots

def _root(level, path):
    return {"level": level, "path": os.path.abspath(path), "library": os.path.join(os.path.abspath(path), PKG)}


def roots(catalog=None, mounts=()):
    """[{level, path, library}] of the library levels this container sees, the catalogue first. A root is a directory
    that holds a `library/`; a level it does not hold is simply not in the list. With no arguments: the catalogue
    ($ENGINEER_CATALOG or the framework's) plus what the router mounted — every /clients/<id> and /workspace."""
    if mounts or catalog:
        out = ([_root("catalog", catalog)] if catalog else []) + [_root(_level_of(m), m) for m in mounts]
    else:
        out = [_root("catalog", os.environ.get("ENGINEER_CATALOG") or DEFAULT_CATALOG)]
        for name in sorted(os.listdir(CLIENTS_DIR)) if os.path.isdir(CLIENTS_DIR) else []:
            out.append(_root(f"client:{name}", os.path.join(CLIENTS_DIR, name)))
        out.append(_root("workspace", WORKSPACE))
    return [r for r in out if os.path.isdir(r["library"])]


def _level_of(path):
    """The level a mounted root stands for, read off where it is mounted: `<...>/clients/<id>` is that client's,
    `<...>/workspace` the workspace's, `<...>/catalog` the framework's. Anything else is refused: a level guessed from a
    folder name would be written into the stamp as if the platform had mounted it."""
    p = os.path.abspath(path).rstrip(os.sep)
    head, tail = os.path.split(p)
    if tail == os.path.basename(WORKSPACE):
        return "workspace"
    if os.path.basename(head) == os.path.basename(CLIENTS_DIR):
        return f"client:{tail}"
    if p == os.path.abspath(DEFAULT_CATALOG) or tail == "catalog":
        return "catalog"
    raise ValueError(f"{path} is not a library root the platform mounts (/clients/<tg id> or /workspace)")


# ---------------------------------------------------------------- what the project imports

def groups_used(project):
    """{group: [project-relative file that imports it]} for every `library.<group>` the project's own code names.
    Static imports only (`import library.x`, `from library.x import ...`, `from library import x`): a name built at run
    time and passed to importlib is not seen, and its group is neither copied nor checked."""
    out = {}
    cad = os.path.join(project, CAD)
    for dp, dn, fn in os.walk(cad):
        top = os.path.abspath(dp) == os.path.abspath(cad)
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith(".") and not (top and d in TOP_SKIP)]
        for f in sorted(fn):
            if not f.endswith(".py"):
                continue
            path = os.path.join(dp, f)
            rel = os.path.relpath(path, project).replace(os.sep, "/")
            for g in _groups_in(path):
                out.setdefault(g, []).append(rel)
    return out


def _groups_in(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError:
        return set()
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):   # a file being written: fall back to the text
        return {g for m in IMPORT_RE.finditer(src) for g in _re_groups(m)}
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                parts = a.name.split(".")
                if parts[0] == PKG and len(parts) > 1:
                    out.add(parts[1])
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            parts = n.module.split(".")
            if parts[0] != PKG:
                continue
            if len(parts) > 1:
                out.add(parts[1])
            else:
                out.update(a.name for a in n.names if a.name != "*")
    return out


def _re_groups(m):
    if m.group(1) or m.group(2):
        return [m.group(1) or m.group(2)]
    return [x.strip().split(" as ")[0].strip() for x in (m.group(3) or "").split(",") if x.strip()]


# ---------------------------------------------------------------- the mirror

def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def open_read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _put(dst, data, mode=0o644):
    """Write `data` at `dst` atomically. A turn of the agent may be reading the mirror while this runs, and a half-written
    part file is a SyntaxError in the middle of a model build; os.replace on the same filesystem cannot be seen half done."""
    tmp = f"{dst}.tmp-{os.getpid()}"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, dst)


def _files_of(group_dir):
    """{relative path inside the group: absolute path} of every file of a library group."""
    out = {}
    for dp, dn, fn in os.walk(group_dir):
        dn[:] = [d for d in dn if d != "__pycache__" and not d.startswith(".")]
        for f in fn:
            if f.startswith(".") or f.endswith(".pyc"):
                continue
            p = os.path.join(dp, f)
            if os.path.isfile(p) and not os.path.islink(p):
                out[os.path.relpath(p, group_dir).replace(os.sep, "/")] = p
    return out


def plan(project, rs):
    """-> ({mirror-relative path: (level, source path)}, [problems]). What the mirror must hold for this project, from
    the levels this container can see now: every group the project imports, and every group a library file it gets
    imports in turn (a reworked part reading the catalogue part's sizes), until nothing new comes in. A level that is
    not mounted contributes nothing: a group only it held is a problem to report (to the agent, by the router), never
    a file to keep on trust — the stamp lives in the project, so a level recorded there proves nothing."""
    want, problems = {}, []
    used = groups_used(project)
    todo, done = sorted(used), set()
    while todo:
        group = todo.pop(0)
        if group in done:
            continue
        done.add(group)
        found = [r for r in rs if os.path.isdir(os.path.join(r["library"], group))]
        if not found:
            where = ", ".join(f"{r['level']} ({r['library']})" for r in rs) or "nothing is mounted"
            problems.append(f"library.{group} is in no library this project can see ({where}); it is imported by "
                            f"{', '.join(used[group][:3])}")
            continue
        for r in found:
            for rel, src in sorted(_files_of(os.path.join(r["library"], group)).items()):
                key = f"{group}/{rel}"
                if key in want:
                    problems.append(f"{key} is offered by two levels: {want[key][1]} and {src}; a part reworked for a "
                                    f"client gets its own name instead of shadowing the catalogue one")
                    continue
                want[key] = (r["level"], src)
                for g in _groups_in(src) if src.endswith(".py") else ():
                    if g not in done:
                        used.setdefault(g, []).append(f"mechanics/cad/library/{key}")
                        todo.append(g)
    return want, problems


def read_stamp(project):
    try:
        with open(os.path.join(project, MIRROR, STAMP), encoding="utf-8") as fh:
            d = json.load(fh)
        return d.get("files") or {} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def mirror_files(project):
    """{mirror-relative path: absolute} of what lies in the mirror now (the stamp itself aside)."""
    base = os.path.join(project, MIRROR)
    out = _files_of(base) if os.path.isdir(base) else {}
    out.pop(STAMP, None)
    return out


def sync(project, rs):
    """-> (lines, rc). Copies what the project imports, drops what it no longer does, writes the stamp. Refuses, and
    leaves the mirror exactly as it was, when the plan has a problem."""
    base = os.path.join(project, MIRROR)
    have, lines, files = mirror_files(project), [], {}
    if have and not os.path.isfile(os.path.join(base, STAMP)):
        return [f"FAIL mechanics/cad/library/ holds the project's own files ({', '.join(sorted(have)[:3])}) and no "
                f"{STAMP}: that folder name is the part library's, so nothing was copied and nothing was touched; "
                f"move those files elsewhere (parts/_<name>.py for shared code)"], 1
    want, problems = plan(project, rs)
    stamped = read_stamp(project)
    for key in sorted(have):                 # edited by hand since the last sync: overwriting it would lose it unseen
        if key in stamped and sha1(have[key]) != stamped[key].get("sha1"):
            src = want[key][1] if key in want else f"its {stamped[key].get('level')} library"
            problems.append(f"mechanics/cad/library/{key} was edited in the project's copy: make that change in {src} "
                            f"(the source), or delete the copy to drop it; the next sync then brings the source back")
    if problems:
        return [f"FAIL {p}" for p in problems], 1
    added = changed = kept = 0
    for key, (level, src) in sorted(want.items()):
        dst = os.path.join(base, *key.split("/"))
        h = sha1(src)
        if key in have and sha1(have[key]) == h:
            kept += 1
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            _put(dst, open_read(src), 0o644)
            if key in have:
                changed += 1
            else:
                added += 1
        files[key] = {"level": level, "sha1": h}
    removed = [k for k in have if k not in want and k in stamped]   # never a file the platform did not write
    for key in removed:
        os.remove(have[key])
    if want or os.path.isdir(base):
        os.makedirs(base, exist_ok=True)
        text = json.dumps({"version": 1, "files": {k: files[k] for k in sorted(files)}}, indent=1, ensure_ascii=False) + "\n"
        path = os.path.join(base, STAMP)
        if not (os.path.isfile(path) and open_read(path) == text.encode()):
            _put(path, text.encode(), 0o644)
        _prune(base)
    if not want and not removed:
        return ["library: nothing to sync"], 0
    by_level = {}
    for key, (level, _) in sorted(want.items()):
        by_level.setdefault(key.split("/")[0], level)
    lines.append(("library: " + ", ".join(f"{g} ({lv})" for g, lv in sorted(by_level.items()))) if by_level else "library: empty")
    lines.append(f"  {added} new, {changed} updated, {kept} unchanged" + (f", {len(removed)} removed" if removed else ""))
    for key in sorted(removed):
        lines.append(f"  removed {key} (no longer imported)")
    return lines, 0


def _prune(base):
    for dp, dn, fn in os.walk(base, topdown=False):
        if dp != base and not dn and not fn:
            os.rmdir(dp)


def check(project):
    """-> (lines, rc). The mirror against its stamp, and against what the project imports. Needs no library root, so it
    runs on any worker: it compares the project with itself."""
    stamp, have, bad = read_stamp(project), mirror_files(project), []
    for key in sorted(have):
        if key not in stamp:
            bad.append(f"mechanics/cad/library/{key} is not recorded in {STAMP}: a library part is edited in the library "
                       f"(/clients/<id>/library/... or the framework catalogue), the copy is written by the platform")
        elif sha1(have[key]) != stamp[key].get("sha1"):
            bad.append(f"mechanics/cad/library/{key} differs from its source ({stamp[key].get('level')}): an edit of the "
                       f"copy is lost at the next sync, edit the source")
    for key in sorted(stamp):
        if key not in have:
            bad.append(f"mechanics/cad/library/{key} is gone while {STAMP} still records it: run part_library.py sync")
        elif not LEVEL_RE.fullmatch(str(stamp[key].get("level") or "")):
            bad.append(f"{STAMP} records {key} at level {stamp[key].get('level')!r}, which is not a level the platform "
                       f"writes (catalog, client:<tg id>, workspace): run part_library.py sync")
    needed = groups_used(project)
    for key, path in sorted(have.items()):          # a library file's own imports must be in the copy too
        for g in _groups_in(path) if key.endswith(".py") else ():
            needed.setdefault(g, []).append(f"mechanics/cad/library/{key}")
    for group, users in sorted(needed.items()):
        if not any(k.split("/")[0] == group for k in have):
            bad.append(f"library.{group} is imported by {', '.join(users[:3])} but is not in the copy: run "
                       f"part_library.py sync (or the group is in no library this project can see)")
    if bad:
        return [f"FAIL {b}" for b in bad], 1
    return [f"library: ok ({len(have)} files)"], 0


# ---------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["roots", "sync", "check"])
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--catalog", help="the catalogue root (a directory holding library/); default $ENGINEER_CATALOG")
    ap.add_argument("--mount", action="append", default=[], help="a mounted root, as it is mounted: /clients/<id> or /workspace")
    a = ap.parse_args(argv)
    project = os.path.abspath(a.project)
    if a.action == "check":
        lines, rc = check(project)
    else:
        try:
            rs = roots(a.catalog, a.mount)
        except ValueError as e:
            print(f"FAIL {e}")
            return 2
        if a.action == "roots":
            lines, rc = [f"{r['level']} {r['path']}" for r in rs] or ["no library roots"], 0
        else:
            lines, rc = sync(project, rs)
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main())
