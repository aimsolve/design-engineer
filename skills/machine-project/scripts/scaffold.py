#!/usr/bin/env python3
"""Create the machine-project layout in a project folder. Idempotent: never overwrites, reports what it added.

  python3 scaffold.py <project dir> --name <machine> [--title "Human name"] [--cad build123d|step] [--lang ru|en]

Always: README.md, .gitignore, aas/README.md + aas/<machine>.aas.json (minimal valid AAS: Nameplate, TechnicalData, empty
PartCatalog and HierarchicalStructures). With --cad: mechanics/README.md; build123d adds mechanics/cad/params.py + parts/example/ (node __init__.py placing parts, block.py one part) + geom.py (P, place) + assemble.py + kit.json (derive.py scopes) + sim/site.sh,
step adds mechanics/cad/README.md. Other layers (electronics/, physics/, sim/, docs/) are created by whoever adds their first file
(with a README). Plain python3, no dependencies. If aas/<x>.aas.json already exists its name wins over --name."""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, '..', 'templates')
sys.path.insert(0, HERE)
import aas_min as A  # noqa: E402
import text_i18n as T  # noqa: E402

added, kept = [], []


def put(path, text, root):
    rel = os.path.relpath(path, root)
    if os.path.exists(path):
        kept.append(rel)
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    added.append(rel)
    return True


def tpl(name):
    with open(os.path.join(TPL, name), encoding='utf-8') as f:
        return f.read()


def fill(text, **kw):
    for k, v in kw.items():
        text = text.replace('{' + k + '}', v)
    return text


def localize(text, kind, lang):
    """A fastener template with its purchase-list names (module-level NAMES / WORD) in `lang` (text_i18n.FASTENERS); the
    template's own English ones when the language has none."""
    names = T.FASTENERS.get(T.lang_of(lang), {}).get(kind)
    if not names:
        return text
    if 'NAMES' in names:
        body = ''.join(f'    {k!r}: {v!r},\n'.replace("'", '"') for k, v in names['NAMES'].items())
        text = re.sub(r'^NAMES = \{\n.*?^\}\n', lambda m: 'NAMES = {\n' + body + '}\n', text, count=1, flags=re.M | re.S)
    return re.sub(r'^WORD = ".*"$', lambda m: f'WORD = "{names["WORD"]}"', text, count=1, flags=re.M)


MECH_README = """# mechanics

Source of the mechanical layer. {what}

- `cad/`: {cad}
- `kinematics/`: appears when parts move (which nodes each actuator moves; derived from the AAS, never a second copy of numbers).
- Part designs (drawings, notes per part) go here too, one folder per part when needed.
- Generated from this layer, always into `../build/`: STL/3MF (print), DXF (laser), glTF (web), renders. Never commit them.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('project')
    ap.add_argument('--name', required=True)
    ap.add_argument('--title')
    ap.add_argument('--cad', choices=['build123d', 'step'])
    ap.add_argument('--lang', help="the language of the texts the project's purchase list shows (default: the project's "
                                   ".engineer/locale.json, else ru)")
    a = ap.parse_args()
    root = os.path.abspath(a.project)
    lang = a.lang or T.project_lang(root)
    os.makedirs(root, exist_ok=True)
    existing = glob.glob(os.path.join(root, 'aas', '*.aas.json'))
    machine = os.path.basename(existing[0])[:-len('.aas.json')] if len(existing) == 1 else re.sub(r'[^A-Za-z0-9_-]+', '-', a.name).strip('-') or 'machine'
    title = a.title or a.name

    cad_line = {'build123d': 'build123d code, numbers in `mechanics/cad/params.py`', 'step': 'STEP from the designer\'s CAD tool (AP242 preferred)'}.get(a.cad, 'STEP from a CAD tool, or build123d code with `params.py`')
    build_line = {'build123d': 'Code model (build123d): numbers in `mechanics/cad/params.py`, one folder `mechanics/cad/parts/<node>/` per node: one file per part `<part>.py` with `build(**args)` returning '
                               'one part in its own coordinates, `geom.P(name, shape, color, process=..., material=..., thickness=..., code=..., weld=...)`, '
                               'with build123d joints, and `__init__.py` whose `build()` docks them (`a["shape"].joints["x"].connect_to(b["shape"].joints["y"])`, then `geom.place(b)`; qty = number of placements); screws/nuts/washers from `parts/fasteners/`. '
                               'Rebuild + viewer: the router runs kit.json `site` (`sh sim/site.sh`) after every turn that changed the model (the cache re-runs only the parts whose params or code changed; one STEP per part in `build/model/`). '
                               'Download kits (STEP, laser/bent drawings, print STL, purchase list) are `derive.py` scopes declared in `kit.json`: nothing is exported by hand. '
                               '`python3 <plugin>/skills/machine-project/scripts/aas_sync.py .` copies params and parts into the AAS (site.sh runs it).',
                  'step': 'see the `step-import` skill: `step_import.py` then `aas_build.py` (README of `mechanics/cad/`).'}.get(a.cad, 'Exports and builds write only into `build/`; the AAS is the index (see `machine-project`).')
    put(os.path.join(root, 'README.md'), fill(tpl('README.md'), title=title, machine=machine, cad_line=cad_line, build_line=build_line), root)
    put(os.path.join(root, '.gitignore'), tpl('gitignore'), root)

    # aas/
    put(os.path.join(root, 'aas', 'README.md'),
        f"# aas\n\n`{machine}.aas.json` is the machine model (Asset Administration Shell, IEC 63278, metamodel v3): the index of every layer and the home of every number.\n"
        "Edit it through the layer that owns the number (`aas_sync.py` for build123d params, `aas_build.py` for an imported STEP), then check it:\n"
        "`python3 <plugin>/skills/machine-project/scripts/check.py .`. Details: `step-import/AAS.md`.\n", root)
    designation = {'en': title}
    sms = [A.nameplate(machine, designation, machine), A.technical_data(machine, designation), A.part_catalog(machine), A.hier(machine, title)]
    put(os.path.join(root, 'aas', f'{machine}.aas.json'), json.dumps(A.document(machine, sms), indent=1, ensure_ascii=False) + '\n', root)

    # mechanics/
    if a.cad:
        what = 'Numbers are in `cad/params.py`; `cad/assemble.py` builds the model from it.' if a.cad == 'build123d' else 'The STEP is the source; the AAS is rebuilt from it.'
        put(os.path.join(root, 'mechanics', 'README.md'), fill(MECH_README.format(what=what, cad=cad_line), machine=machine), root)
        if a.cad == 'build123d':
            put(os.path.join(root, 'mechanics', 'cad', 'params.py'), fill(tpl('params.py'), machine=machine), root)
            put(os.path.join(root, 'mechanics', 'cad', 'assemble.py'), fill(tpl('assemble.py'), machine=machine), root)
            put(os.path.join(root, 'mechanics', 'cad', 'geom.py'), fill(tpl('geom.py'), machine=machine), root)
            put(os.path.join(root, 'sim', 'site.sh'), fill(tpl('site.sh'), title=title), root)
            put(os.path.join(root, 'sim', 'README.md'), '# sim\n\nGlue between the layers only. `site.sh`: model -> standard viewer at `public/` '
                '(kit.json `site`: the router runs it after a turn that changed the model); `derive.py` brings the model up to date first.\n', root)
            put(os.path.join(root, 'mechanics', 'cad', 'parts', '__init__.py'),
                '"""One folder per node: parts/<node>/__init__.py places its parts, parts/<node>/<part>.py builds one part."""\n', root)
            put(os.path.join(root, 'mechanics', 'cad', 'parts', 'example', '__init__.py'), fill(tpl('node.py'), machine=machine), root)
            put(os.path.join(root, 'mechanics', 'cad', 'parts', 'example', 'block.py'), fill(tpl('part.py'), machine=machine), root)
            put(os.path.join(root, 'mechanics', 'cad', 'parts', 'fasteners', '__init__.py'),
                '"""Bought fasteners (framework templates): nodes import these part files and dock them into hole joints; qty = placements."""\n\n\n'
                'def build():\n    return []\n', root)
            for f in ('screw.py', 'nut.py', 'washer.py'):
                put(os.path.join(root, 'mechanics', 'cad', 'parts', 'fasteners', f),
                    localize(fill(tpl(f), machine=machine), f[:-3], lang), root)
            put(os.path.join(root, 'kit.json'), tpl('kit.json'), root)
        else:
            put(os.path.join(root, 'mechanics', 'cad', 'README.md'),
                f"# mechanics/cad\n\nPut the designer's STEP here (large STEP files over 20 MB are not committed: add `mechanics/cad/*.STEP` to .gitignore and name the file in the AAS Models3D).\n"
                "Then: `step_import.py <file>.step --out build/`, `aas_build.py build --machine .` (skill `step-import`).\n", root)
    print('added:', ', '.join(added) or '-')
    print('kept (already there):', ', '.join(kept) or '-')
    print('machine:', machine)


if __name__ == '__main__':
    main()
