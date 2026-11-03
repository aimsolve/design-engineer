#!/usr/bin/env python3
"""Keep skills/step-import/semantics.csv in sync with the semanticIds our AAS uses (`aas_lib.NS` + `<type>/<property>`).
Adds a row for every own semanticId found in the AAS that is not in the csv; never touches existing rows. The csv keys are
relative to the namespace (`pulley/teeth`), so it serves an AAS written under any base (aas_lib.BASE).
The `eclass_irdi` column is left for a user with an ECLASS licence to fill: IRDIs are free to view, using them needs one.
  python3 aas_semantics.py <machine>.aas.json"""
import csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aas_lib
CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'semantics.csv')
HDR = ['semantic_key', 'eclass_irdi', 'eclass_status', 'unit', 'note']
# own properties that have an ECLASS counterpart (class context only, never asserted as binding)
KNOWN = {'pulley/teeth': 'number of teeth', 'fastener/thread': 'thread size', 'fastener/length_mm': 'nominal length',
         'belt/width_mm': 'belt width', 'pulley/belt_width_mm': 'belt width', 'generic/mass': 'mass'}
UNITS = {'_mm': 'mm', '_a': 'A', '_w': 'W', '_v_min': 'V', '_v_max': 'V', '_rad': 'rad', '_mm3': 'mm3'}


def walk(e, doc, out):
    for q in e.get('semanticId', {}).get('keys', []):
        key = aas_lib.own_semantic(q['value'], doc)
        if key:
            out.add(key)
    for k in ('submodelElements', 'value', 'statements'):
        if isinstance(e.get(k), list):
            for c in e[k]:
                walk(c, doc, out)


def row_key(r):
    """A row's key; a row of the older layout holds the full semanticId."""
    if r.get('semantic_key'):
        return r['semantic_key']
    sid = r.get('semantic_id') or ''
    return sid.split('/semantics/', 1)[1] if '/semantics/' in sid else sid


def main():
    d = json.load(open(sys.argv[1], encoding='utf-8'))
    found = set()
    for s in d['submodels']:
        walk(s, d, found)
    found |= set(KNOWN)
    rows = [dict({h: r.get(h, '') for h in HDR}, semantic_key=row_key(r))
            for r in csv.DictReader(open(CSV, encoding='utf-8'))] if os.path.exists(CSV) else []
    have = {r['semantic_key'] for r in rows}
    for key in sorted(found - have):
        unit = next((u for suf, u in UNITS.items() if key.endswith(suf)), '')
        rows.append(dict(semantic_key=key, eclass_irdi='', eclass_status='', unit=unit, note=KNOWN.get(key, '')))
    rows.sort(key=lambda r: r['semantic_key'])
    with open(CSV, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, HDR)
        w.writeheader()
        w.writerows(rows)
    print(len(rows), 'rows ->', CSV, '| with IRDI:', sum(1 for r in rows if r['eclass_irdi']))


main()
