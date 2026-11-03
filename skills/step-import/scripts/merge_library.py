#!/usr/bin/env python3
"""Merge the bought parts a port staged (step_to_project.py --library build/clients/<client id>) into the client's real library
(/clients/<client id>, his level), never overwriting a part another of his projects already uses.

  python merge_library.py build/clients/<client id> /clients/<client id> .

Per staged library/<group>/<module>.py with its 3D beside it (.step, or .brep for a STEP too big to commit) or none (a
stand-in drawn as code):
  - his library has the same part (a STEP of the same geometry, the same BREP file, or the same module text for a part
    drawn as code): that module is used, nothing is copied;
  - a module of the same name with another geometry: the part gets the next free name (<module>_2, ...), and the project's
    part files import it under the old name (`from library.<group> import <module>_2 as <module>`);
  - else both files are copied as they are.
Then `part_library.py sync . --mount /clients/<client id>` brings the library into the project.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from step_to_project import file_sha, step_sha  # noqa: E402


def merge(staged, client, project, log=print):
    """-> {"<group>/<module>": "reused <name>" | "copied" | "renamed <name>"}."""
    staged, client, project = Path(staged) / "library", Path(client) / "library", Path(project)
    out, renames = {}, {}
    for py in sorted(staged.glob("*/*.py")):
        group, mod = py.parent.name, py.stem
        if mod.startswith("_"):
            continue
        # its 3D: the maker's STEP, a BREP (a STEP over the 20 MB a project may commit), or none (a stand-in drawn as code)
        shape = next((py.with_suffix(x) for x in (".step", ".brep") if py.with_suffix(x).is_file()), None)
        text = py.read_text(encoding="utf-8")
        dest = client / group
        if not dest.is_dir():
            same = None
        elif shape is None:                          # code: the same module text is the same part
            same = mod if (dest / f"{mod}.py").is_file() and (dest / f"{mod}.py").read_text(encoding="utf-8") == text else None
        elif shape.suffix == ".step":
            sha = step_sha(shape)
            same = next((s.stem for s in sorted(dest.glob("*.step")) if step_sha(s) == sha), None)
        else:
            sha = file_sha(shape)
            same = next((s.stem for s in sorted(dest.glob("*.brep")) if file_sha(s) == sha), None)
        if same:
            out[f"{group}/{mod}"] = f"reused {same}"
            if same != mod:
                renames[(group, mod)] = same
            continue
        name, k = mod, 2
        while any((dest / f"{name}{x}").exists() for x in (".py", ".step", ".brep")):
            name, k = f"{mod}_{k}", k + 1
        dest.mkdir(parents=True, exist_ok=True)
        if name != mod:                               # the module's own name in its text: its VENDOR key, 3D file, part id
            if shape is not None:
                text = text.replace(json.dumps(mod + shape.suffix), json.dumps(name + shape.suffix))
            text = text.replace(json.dumps(mod), json.dumps(name))
            renames[(group, mod)] = name
        if shape is not None:
            shutil.copyfile(shape, dest / f"{name}{shape.suffix}")
        (dest / f"{name}.py").write_text(text, encoding="utf-8")
        out[f"{group}/{mod}"] = "copied" if name == mod else f"renamed {name}"
    cad = project / "mechanics" / "cad"
    ledgers = {g: json.loads(g.read_text(encoding="utf-8")) for g in sorted((cad / "migration").rglob("generated.json"))}
    for f in sorted((cad / "parts").rglob("*.py")) if renames else []:
        t = f.read_text(encoding="utf-8")
        new = t
        for (group, mod), name in renames.items():
            new = re.sub(rf"^from library\.{group} import {mod}$", f"from library.{group} import {name} as {mod}", new, flags=re.M)
        if new != t:
            f.write_text(new, encoding="utf-8")
            rel = str(f.relative_to(cad))
            for led in ledgers.values():              # still the converter's file: a re-run may rewrite it
                if rel in led:
                    led[rel] = file_sha(f)
    for g, led in ledgers.items():
        g.write_text(json.dumps(dict(sorted(led.items())), indent=1) + "\n", encoding="utf-8")
    for k, v in out.items():
        log(f"[merge_library] {k}: {v}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("staged", help="the --library folder of the port (build/clients/<client id>)")
    ap.add_argument("client", help="the client's library root (/clients/<client id>)")
    ap.add_argument("project", nargs="?", default=".")
    a = ap.parse_args()
    merge(a.staged, a.client, a.project)


if __name__ == "__main__":
    main()
