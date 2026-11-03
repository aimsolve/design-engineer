#!/usr/bin/env python3
"""Code-CAD machines (build123d): copy the model's numbers and parts into the AAS. One way: params.py / build/parts.json -> AAS.

  python3 aas_sync.py <project dir> [--params mechanics/cad/params.py] [--parts build/model/parts.json] [--validate]

TechnicalData.ModelParameters = every UPPER_CASE int/float/bool constant of params.py (Property per number, qualifiers `source`
= params.py path and `unit` from the name suffix). PartCatalog + HierarchicalStructures = the parts listed in build/parts.json
(assemble.py writes it): EntryNode -> one node per placements[].node -> the parts placed there with BulkCount = their placements
in that node (flat BoM when no part has placements: the old layout), SameAs -> PartCatalog. Other submodels are left as they are. Idempotent; plain python3.
Imported-STEP machines do NOT use this: step-import's aas_build.py builds the same submodels from the STEP."""
import argparse
import glob
import json
import os
import runpy
import subprocess
import procgroup
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aas_min as A  # noqa: E402

SUFFIX = [('_MM2', 'mm2'), ('_MM3', 'mm3'), ('_MM', 'mm'), ('_CM', 'cm'), ('_M', 'm'), ('_KG', 'kg'), ('_G', 'g'), ('_NM', 'N.m'), ('_N', 'N'), ('_MPA', 'MPa'),
          ('_DEG', 'deg'), ('_RAD', 'rad'), ('_RPM', 'rpm'), ('_MS', 'ms'), ('_S', 's'), ('_V', 'V'), ('_A', 'A'), ('_W', 'W')]


def unit_of(name):
    return next((u for s, u in SUFFIX if name.endswith(s)), None)


def load_params(path):
    ns = runpy.run_path(path)
    return [(k, v, unit_of(k)) for k, v in ns.items() if k.isupper() and not k.startswith('_') and isinstance(v, (int, float, bool))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('project')
    ap.add_argument('--params', default='mechanics/cad/params.py')
    ap.add_argument('--parts', help='parts list (default build/model/parts.json of the cached model, else build/parts.json)')
    ap.add_argument('--validate', action='store_true')
    a = ap.parse_args()
    root = os.path.abspath(a.project)
    fs = glob.glob(os.path.join(root, 'aas', '*.aas.json'))
    if len(fs) != 1:
        sys.exit('need exactly one aas/*.aas.json (run scaffold.py first)')
    path = fs[0]
    machine = os.path.basename(path)[:-len('.aas.json')]
    d = json.load(open(path, encoding='utf-8'))
    A.L.adopt(d)                  # rebuild with the base this file was written with: its ids and references stay valid
    old = {s['idShort']: s for s in d['submodels']}
    npl = old.get('Nameplate')
    designation = {m['language']: m['text'] for m in next((e for e in npl['submodelElements'] if e['idShort'] == 'ManufacturerProductDesignation'), {}).get('value', [])} or {'en': machine}
    title = next(iter(designation.values()))
    pp = os.path.join(root, a.params)
    params = [(n, v, u, a.params) for n, v, u in load_params(pp)] if os.path.isfile(pp) else []
    pj = a.parts or ('build/model/parts.json' if os.path.isfile(os.path.join(root, 'build/model/parts.json')) else 'build/parts.json')
    parts = json.load(open(os.path.join(root, pj)))['parts'] if os.path.isfile(os.path.join(root, pj)) else None
    if parts is not None and pj.startswith('build/model/'):      # the model's `kind` is the bought kind; the catalogue's Kind is made | bought
        parts = [dict(p, kind='bought' if p.get('process') == 'bought' else 'made') for p in parts]
    new = dict(old)
    new['TechnicalData'] = A.technical_data(machine, designation, params)
    if parts is not None:
        new['PartCatalog'] = A.part_catalog(machine, parts)
        new['HierarchicalStructures'] = A.hier(machine, title, parts)
    doc = A.document(machine, list(new.values()))
    text = json.dumps(doc, indent=1, ensure_ascii=False) + '\n'
    if text != open(path, encoding='utf-8').read():
        open(path, 'w', encoding='utf-8').write(text)
        print(f'updated {os.path.relpath(path, root)}: {len(params)} parameters, {len(parts) if parts is not None else "kept"} parts')
    else:
        print('AAS already in sync')
    if a.validate:
        sys.exit(procgroup.call([sys.executable, os.path.join(HERE, 'check.py'), root]))


if __name__ == '__main__':
    main()
