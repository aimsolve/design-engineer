#!/usr/bin/env python3
"""One-way derivations from the machine AAS (JSON or AASX): the files the viewer and the simulator read.
  python3 aas_export.py <machine>.aas.json|.aasx [--parts build/parts.json] [--motion mechanics/kinematics/motion.json]
Plain python3, no dependencies."""
import argparse, json, os, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aas_lib


def load(path):
    if path.endswith('.aasx'):
        with zipfile.ZipFile(path) as z:
            return json.loads(z.read('aasx/data.json'))
    return json.load(open(path, encoding='utf-8'))


def dump(o, path, indent):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(o, f, ensure_ascii=False, indent=indent)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('aas')
    ap.add_argument('--parts')
    ap.add_argument('--motion')
    a = ap.parse_args()
    d = load(a.aas)
    if a.parts:
        dump(aas_lib.export_parts(d), a.parts, 1)
        print('wrote', a.parts)
    if a.motion:
        dump(aas_lib.derive_motion(d), a.motion, 1)
        print('wrote', a.motion)


if __name__ == '__main__':
    main()
