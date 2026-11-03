"""Build one self-contained viewer HTML: page.tpl.html + three.min.js + page_i18n.js + meshes.json + params.json + project.js.

Usage (paths relative to the current directory; template files are found next to this script):
  python build.py [--meshes cad/out/meshes.json] [--params cad/out/params.json] [--project project.js] [--out model.html]

Prints the page size, the heaviest meshes (triangle budget) and numeric literals in project.js that probably belong
in params (one source of numbers). Fails if a marker is missing/duplicated or an inlined piece would close the script.
"""
import argparse
import json
import pathlib
import re

HERE = pathlib.Path(__file__).parent
MARKERS = ("/*__THREE__*/", "/*__I18N__*/", "/*__PARAMS__*/", "/*__MESHES__*/", "/*__PROJECT__*/")
BUDGET_KB = 5000


def literal_report(js: str, limit: int = 12) -> list[str]:
    """Numbers in the hook other than trivial factors: each one is a second copy of a CAD number waiting to go stale."""
    code = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/', "", js, flags=re.S)
    found = []
    for m in re.finditer(r"(?<![\w.])(\d+\.\d+|\d+)(?![\w.])", code):
        if m.group(1) not in {"0", "1", "2", "3", "4", "0.5", "10", "100", "180"}:
            found.append(m.group(1))
    return found[:limit] + (["..."] if len(found) > limit else []) if found else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meshes", default="cad/out/meshes.json")
    ap.add_argument("--params", default="cad/out/params.json")
    ap.add_argument("--project", default="project.js")
    ap.add_argument("--out", default="model.html")
    a = ap.parse_args()

    tpl = (HERE / "page.tpl.html").read_text(encoding="utf-8")
    three = (HERE / "three.min.js").read_text(encoding="utf-8")
    i18n = (HERE / "page_i18n.js").read_text(encoding="utf-8")
    meshes_txt = pathlib.Path(a.meshes).read_text(encoding="utf-8")
    params_txt = pathlib.Path(a.params).read_text(encoding="utf-8")
    project = pathlib.Path(a.project).read_text(encoding="utf-8")

    for mk in MARKERS:
        assert tpl.count(mk) == 1, f"{mk} missing or duplicated in page.tpl.html"
    for name, txt in (("three.min.js", three), ("page_i18n.js", i18n), ("meshes", meshes_txt), ("params", params_txt), ("project", project)):
        assert "</script" not in txt.lower(), f"{name} contains a script close tag"
    assert "function viewerProject" in project, "project file must define function viewerProject(V)"

    meshes = json.loads(meshes_txt)
    json.loads(params_txt)
    names = [m["name"] for m in meshes]
    dup = {n for n in names if names.count(n) > 1}
    assert not dup, f"duplicate mesh names: {sorted(dup)[:10]}"

    # plain string replace (not re.sub): the inlined text is full of backslashes
    out = (tpl.replace("/*__THREE__*/", three)
              .replace("/*__I18N__*/", i18n)
              .replace("/*__PARAMS__*/", "var P = " + params_txt.strip() + ";")
              .replace("/*__MESHES__*/", "var MESHES = " + meshes_txt.strip() + ";")
              .replace("/*__PROJECT__*/", project))
    pathlib.Path(a.out).write_text(out, encoding="utf-8")

    kb = len(out.encode("utf-8")) // 1024
    tris = sorted(((len(m["idx"]) // 3, m["name"]) for m in meshes), reverse=True)
    total = sum(n for n, _ in tris)
    print(f"built {a.out}: {kb} KB, {len(meshes)} meshes, {total} tris" + ("  WARNING: over budget" if kb > BUDGET_KB else ""))
    print("heaviest:", ", ".join(f"{n} {c}" for c, n in tris[:5]))
    lits = literal_report(project)
    if lits:
        print("project.js numeric literals (move CAD numbers to a VIEWER dict in params):", " ".join(lits))


if __name__ == "__main__":
    main()
