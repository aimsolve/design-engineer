#!/usr/bin/env python3
"""Round-trip check: AAS JSON -> AASX -> read back -> same model, same embedded files, same derived parts.json + motion.json.
  uv run --python 3.12 --with basyx-python-sdk==2.1.0 --with aas-test-engines==1.0.3 python aas_check.py <out dir> --machine <machine repo>
Exit 1 on any mismatch. Also runs the IDTA validator (aas-test-engines) on the AASX."""
import argparse, hashlib, io, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from basyx.aas import model
from basyx.aas.adapter import aasx
import basyx.aas.adapter.json as bjson
import aas_lib, aas_build, aas_export

bad = []


def has_drive_chains(aas):
    try:
        kin = aas_lib.submodel(aas, 'Kinematics')
        acts = aas_lib.get(kin, 'Actuators')
        return bool(acts and acts.get('value')) and aas_lib.get(kin, 'Clones') is not None and aas_lib.get(kin, 'MachineParameters') is not None
    except Exception:
        return False


def check(name, ok, extra=''):
    print(('ok   ' if ok else 'FAIL ') + name + (' ' + extra if extra else ''))
    if not ok:
        bad.append(name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--machine', required=True)
    a = ap.parse_args()
    mdir = os.path.abspath(a.machine)
    machine = aas_lib.machine_name(mdir)
    jp = os.path.join(mdir, 'aas', f'{machine}.aas.json')
    xp = os.path.join(a.out, f'{machine}.aasx')
    committed = json.load(open(jp, encoding='utf-8'))
    # 1. AASX read back == committed JSON
    store, fs = model.DictIdentifiableStore(), aasx.DictSupplementaryFileContainer()
    with aasx.AASXReader(xp) as r:
        r.read_into(store, fs)
    back = aas_build.canonical_json(store)
    check('AASX content == committed JSON', back == committed)
    with open(jp, encoding='utf-8') as fh:
        s0 = bjson.read_aas_json_file(fh, failsafe=False)
    check('JSON re-serialises byte-identically', aas_build.dumps(aas_build.canonical_json(s0)) == open(jp, encoding='utf-8').read())
    # 2. embedded files byte-identical to the sources
    n = mism = 0
    for name in fs:
        b = io.BytesIO()
        fs.write_file(name, b)
        src = os.path.join(a.out, 'model.glb') if name == '/files/model.glb' else None
        if name.startswith('/files/parts/'):
            base = name[len('/files/parts/'):]
            pid = next((p['id'] for p in json.load(open(os.path.join(a.out, 'parts.json')))['parts'] if aas_lib.slug(p['id']).lower() == os.path.splitext(base)[0]), None)
            src = os.path.join(a.out, 'parts', pid + os.path.splitext(base)[1]) if pid else None
        elif name == '/files/motion.md':
            src = os.path.join(mdir, 'mechanics/kinematics/MOTION.md')
        if src and os.path.exists(src):
            n += 1
            mism += hashlib.sha256(open(src, 'rb').read()).digest() != hashlib.sha256(b.getvalue()).digest()
        else:
            mism += 1
    check(f'embedded files byte-identical ({n} compared, {len(list(fs))} in package)', mism == 0, f'mismatches={mism}')
    # 3. derivations from the AASX == from the JSON == files on disk
    d = aas_export.load(xp)
    p_aasx = aas_lib.export_parts(d)
    check('parts.json from AASX == from JSON', p_aasx == aas_lib.export_parts(committed))
    pp = os.path.join(a.out, 'parts.json')
    check('parts.json on disk == derived', json.load(open(pp)) == p_aasx)
    if has_drive_chains(d):
        m_aasx = aas_lib.derive_motion(d)
        check('motion.json from AASX == from JSON', m_aasx == aas_lib.derive_motion(committed))
        mp = os.path.join(mdir, 'mechanics/kinematics/motion.json')
        check('mechanics/kinematics/motion.json on disk == derived', json.load(open(mp)) == m_aasx)
    else:
        print('n/a  motion.json derivation (Kinematics has no drive chains: no Actuators/Clones, e.g. code-CAD MovingGroups)')
    # 4. validator
    r = aas_build.validate(xp)
    check('aas-test-engines (IDTA) on the AASX', r.ok())
    if not r.ok():
        print('\n'.join(r.to_lines()[:40]))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
