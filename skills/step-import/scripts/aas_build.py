#!/usr/bin/env python3
"""Build the machine's Asset Administration Shell (IDTA metamodel v3) from the step_import outputs.

  uv run --python 3.12 --with basyx-python-sdk==2.1.0 --with aas-test-engines==1.0.3 \
      python aas_build.py <out dir> --machine <machine repo> [--seed-kinematics FILE] [--no-part-files] [--no-aasx]

Writes  <machine>/aas/<machine>.aas.json   canonical, committed, pretty, stable order/ids
        <out>/<machine>.aasx                 package: model.glb + per-part STEP/STL embedded (the big source STEP is only named)
Regenerated on every run: HierarchicalStructures, PartCatalog, Models3D (from parts.json + model.glb).
Machine-authored (Nameplate, SoftwareNameplate, TechnicalData, SimulationModels, Kinematics): carried over from the existing
aas/<machine>.aas.json; seeded ONCE from aas/seed/machine.facts.json + mechanics/kinematics/motion.measured.json when absent (or --reseed)."""
import argparse, io, json, os, re, struct, sys, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from basyx.aas import model
from basyx.aas.adapter import aasx
import basyx.aas.adapter.json as bjson
import aas_lib
from aas_lib import HS_SEM, Bom, Node, part_key, slug
import aas_seed as S
from aas_seed import sem, P, SMC, MLP, DT

AUTHORED = ['Nameplate', 'SoftwareNameplate', 'TechnicalData', 'SimulationModels', 'Kinematics', 'AssetInterfacesDescription']
OPTIONAL = {'AssetInterfacesDescription'}   # seeded only when facts.interfaces lists interface files (electronics/interfaces/*.json)
UNIT = [(r'_mm$', 'mm'), (r'_mm3$', 'mm3'), (r'_a$', 'A'), (r'_w$', 'W'), (r'_v(_min|_max)?$', 'V'), (r'_rad$', 'rad')]


def unit_of(k):
    return next((u for rx, u in UNIT if re.search(rx, k)), None)


def glb_json(path):
    b = open(path, 'rb').read()
    return json.loads(b[20:20 + struct.unpack('<I', b[12:16])[0]])


def node_tree(G, tree):
    """Node tree from the GLB nodes; assembly drawings from parts.json's assemblies-only tree (same order as the GLB non-mesh nodes)"""
    nodes = G['nodes']

    def mk(i, t, siblings_seen):
        n = nodes[i]
        assert 'translation' not in n and 'rotation' not in n and 'scale' not in n, 'TRS nodes not supported, expected matrix'
        part = n.get('extras', {}).get('part') if 'mesh' in n else None
        key = ('p_' + slug(part)) if part else ('a_' + slug(t['drawing'] or 'asm'))
        k = siblings_seen.get(key, 0)
        siblings_seen[key] = k + 1
        short = f'{key}_{k}'
        node = Node(short, n['name'], None if part else t['drawing'], part, n.get('matrix'))
        seen, ti = {}, [c for c in t['children']] if t else []
        it = iter(ti)
        for c in n.get('children', []):
            cn = nodes[c]
            ct = None if 'mesh' in cn else next(it)
            if ct is not None:
                assert ct['name'] == cn['name'], (ct['name'], cn['name'])
            node.add(mk(c, ct, seen))
        return node
    root = G['scenes'][G.get('scene', 0)]['nodes'][0]
    r = mk(root, tree, {})
    r.short = 'EntryNode'
    return r


def build(args):
    out = os.path.abspath(args.out)
    mdir = os.path.abspath(args.machine)
    machine = aas_lib.machine_name(mdir)
    canon = os.path.join(mdir, 'aas', f'{machine}.aas.json')
    NS, ID = aas_lib.adopt(canon)       # an existing AAS keeps the base its ids were written with (--reseed too)
    P_ = json.load(open(os.path.join(out, 'parts.json')))
    parts = {p['id']: p for p in P_['parts']}
    G = glb_json(os.path.join(out, 'model.glb'))
    root = node_tree(G, P_['assembly']['tree'])
    bom = Bom(root)
    store = model.DictIdentifiableStore()
    fs = aasx.DictSupplementaryFileContainer()
    sm_id = lambda n: f'{ID}{machine}/sm/{n}'

    def add_file(path, ct, name=None):
        if not path or not os.path.exists(path):
            return None
        with open(path, 'rb') as fh:
            return fs.add_file(name or '/files/' + os.path.basename(path).lower(), fh, ct)   # SDK lowercases relationship names: do it ourselves

    # ---- PartCatalog
    cat = S.new_sm(machine, 'PartCatalog', NS + 'partcatalog/1/0')
    a = P_['assembly']
    summ = SMC('AssemblySummary', NS + 'partcatalog/AssemblySummary', [
        P('Triangles', DT.Integer, a['triangles'], NS + 'partcatalog/Triangles'), P('Source', DT.String, a['source'], NS + 'partcatalog/Source'),
        *[model.Range(f'Bbox{ax}', DT.Double, a['bbox_mm'][0][i], a['bbox_mm'][1][i], semantic_id=sem(NS + 'partcatalog/Bbox')) for i, ax in enumerate('XYZ')],
        SMC('Tessellation', NS + 'partcatalog/Tessellation', [P('DeflectionMm', DT.Double, a['tessellation']['deflection_mm']), P('AngleRad', DT.Double, a['tessellation']['angle_rad'])])])
    plist = SMC('Parts', NS + 'partcatalog/Parts')
    seen_keys = set()
    for p in P_['parts']:
        key = part_key(p['id'])
        assert key not in seen_keys, key
        seen_keys.add(key)
        c = SMC(key, NS + 'partcatalog/Part', [
            P('PartId', DT.String, p['id']), P('Name', DT.String, p['name']), P('Drawing', DT.String, p['drawing']) if p['drawing'] else None,
            P('Kind', DT.String, p['kind']), P('Material', DT.String, p['material']), P('Module', DT.String, p['module']),
            P('InstanceCount', DT.Integer, p['instances']), P('VolumeMm3', DT.Double, p['volume_mm3'], unit='mm3'), P('Triangles', DT.Integer, p['triangles']),
            *[model.Range(f'Bbox{ax}', DT.Double, p['bbox_mm'][0][i], p['bbox_mm'][1][i], semantic_id=sem(NS + 'partcatalog/Bbox'), qualifier=[model.Qualifier('unit', DT.String, 'mm')]) for i, ax in enumerate('XYZ')],
            P('Type', DT.String, p['type'], NS + 'partcatalog/Type') if p.get('type') else None])
        if 'params' in p:
            t = p.get('type') or 'generic'
            pr = SMC('Params', NS + 'partcatalog/Params')
            for k, v in p['params'].items():
                src = p['params_source'].get(k)
                if isinstance(v, list):
                    l = model.SubmodelElementList(k, model.Property, value_type_list_element=DT.String, semantic_id=sem(f'{NS}{t}/{k}'),
                                                qualifier=[model.Qualifier('source', DT.String, src)] if src else ())
                    for x in v:
                        l.value.add(model.Property(None, DT.String, value=x))
                    pr.value.add(l)
                    continue
                vt = DT.Boolean if isinstance(v, bool) else DT.Integer if isinstance(v, int) else DT.Double if isinstance(v, float) else DT.String
                pr.value.add(P(k, vt, v, f'{NS}{t}/{k}', source=src, unit=unit_of(k)))
            c.value.add(pr)
            if 'params_check' in p:
                c.value.add(SMC('ParamsCheck', NS + 'partcatalog/ParamsCheck', [P(k, DT.String, v) for k, v in p['params_check'].items()]))
            if 'params_conflicts' in p:
                c.value.add(SMC('ParamsConflicts', NS + 'partcatalog/ParamsConflicts', [
                    SMC(k, None, [P(x, DT.Double if isinstance(y, float) else DT.Integer, y) for x, y in d.items()]) for k, d in p['params_conflicts'].items()]))
        if not args.no_part_files:
            fl = SMC('Files', NS + 'partcatalog/Files')
            for ext, ct in (('step', 'application/step'), ('stl', 'model/stl')):
                nm = add_file(os.path.join(out, p['files'][ext]), ct, f'/files/parts/{slug(p["id"]).lower()}.{ext}')
                if nm:
                    fl.value.add(model.File(ext.capitalize(), ct, value=nm, semantic_id=sem(NS + 'partcatalog/File/' + ext.capitalize())))
            c.value.add(fl)
        plist.value.add(c)
    fill = S.fill
    fill(cat, [summ, plist])
    store.add(cat)

    # ---- HierarchicalStructures
    hs = S.new_sm(machine, 'HierarchicalStructures', HS_SEM)
    HSN = 'https://admin-shell.io/idta/HierarchicalStructures/'
    hs_id = hs.id

    def ent(n, chain, is_root):
        e = model.Entity(n.short, model.EntityType.CO_MANAGED_ENTITY, semantic_id=sem(HSN + ('EntryNode/1/0' if is_root else 'Node/1/0')),
                         display_name=model.MultiLanguageNameType({'und': n.name[:250]}))
        if n.matrix:
            e.statement.add(P('Placement', DT.String, ' '.join(repr(float(x)) for x in n.matrix), NS + 'hs/PlacementGltfColumnMajor4x4'))
        if n.drawing:
            e.statement.add(P('Drawing', DT.String, n.drawing, NS + 'hs/Drawing'))
        ck = chain + (model.Key(model.KeyTypes.ENTITY, n.short),)
        for c in n.children:
            e.statement.add(ent(c, ck, False))
        if n.part:
            tgt = model.ModelReference((model.Key(model.KeyTypes.SUBMODEL, cat.id), model.Key(model.KeyTypes.SUBMODEL_ELEMENT_COLLECTION, 'Parts'),
                                        model.Key(model.KeyTypes.SUBMODEL_ELEMENT_COLLECTION, part_key(n.part))), model.SubmodelElementCollection)
            e.statement.add(model.RelationshipElement('SameAs', first=model.ModelReference(ck, model.Entity), second=tgt, semantic_id=sem(HSN + 'SameAs/1/0')))
        return e
    fill(hs, [P('ArcheType', DT.String, 'Full', HSN + 'ArcheType/1/0'), ent(root, (model.Key(model.KeyTypes.SUBMODEL, hs_id),), True)])
    store.add(hs)

    # ---- Models3D
    m3 = S.new_sm(machine, 'Models3D', 'https://admin-shell.io/idta/Models3D/1/0')
    M3 = 'https://admin-shell.io/idta/Models3D/Model3D/'
    glb = add_file(os.path.join(out, 'model.glb'), 'model/gltf-binary', '/files/model.glb')
    lst = model.SubmodelElementList('Model3D', model.SubmodelElementCollection, semantic_id=sem(M3 + '1/0'), order_relevant=False)
    lst.value.add(SMC(None, M3 + '1/0', [
        SMC('File', M3 + 'File/1/0', [model.File('Model3D_glb', 'model/gltf-binary', value=glb, semantic_id=sem(NS + 'models3d/Glb'))]),
        SMC('Geometry', M3 + 'Geometry/1/0', [P('Representation', DT.String, 'glTF 2.0 binary, one node per instance, meshes shared per part (extras.part = part id)', M3 + 'Geometry/Representation/1/0'),
                                              P('LengthUnit', DT.String, 'm', M3 + 'Geometry/LengthUnit/1/0')])]))
    step_name = args.step_name or a['source']
    lst.value.add(SMC(None, M3 + '1/0', [
        SMC('File', M3 + 'File/1/0', [model.File('Model3D_step', 'application/step', value='file:mechanics/cad/' + step_name, semantic_id=sem(NS + 'models3d/StepNotEmbedded'))]),
        SMC('Geometry', M3 + 'Geometry/1/0', [P('Representation', DT.String, 'STEP AP203 source assembly, referenced by name only (not embedded)', M3 + 'Geometry/Representation/1/0'),
                                              P('LengthUnit', DT.String, 'mm', M3 + 'Geometry/LengthUnit/1/0')])]))
    fill(m3, [lst])
    store.add(m3)

    # ---- machine-authored submodels: carry over or seed
    carried = {}
    if os.path.exists(canon) and not args.reseed:
        with open(canon, encoding='utf-8') as fh:
            old = bjson.read_aas_json_file(fh, failsafe=False)
        for o in old:
            if isinstance(o, model.Submodel) and o.id_short in AUTHORED:
                carried[o.id_short] = o
    fp = args.facts or os.path.join(mdir, 'aas/seed/machine.facts.json')
    facts = json.load(open(fp)) if os.path.exists(fp) else {}
    ifaces = [json.load(open(os.path.join(mdir, f), encoding='utf-8')) for f in facts.get('interfaces', [])]
    missing = [n for n in AUTHORED if n not in carried and (n not in OPTIONAL or ifaces)]
    measured = None
    if any(n not in OPTIONAL for n in missing):
        measured = json.load(open(args.seed_kinematics or os.path.join(mdir, 'mechanics/kinematics/motion.measured.json')))
    seeds = {'Nameplate': lambda: S.build_nameplate(machine, facts), 'SoftwareNameplate': lambda: S.build_software_nameplate(machine, facts),
             'TechnicalData': lambda: S.build_technical_data(machine, facts),
             'SimulationModels': lambda: S.build_simulation_models(machine, facts, lambda p, ct: add_file(os.path.join(mdir, p), ct) if p else None),
             'Kinematics': lambda: S.build_kinematics(machine, facts, measured, bom, hs_id, parts, cat.id),
             'AssetInterfacesDescription': lambda: S.build_asset_interfaces(machine, ifaces)}
    # the operator app of the machine (sim/site.json app.source) is a record shared with the site build: SoftwareNameplate is refreshed from it on every run
    site = os.path.join(mdir, facts.get('apps_from', 'sim/site.json'))
    app = (json.load(open(site, encoding='utf-8')).get('app') if os.path.exists(site) else None) or {}
    for n in AUTHORED:
        if n not in carried and n in OPTIONAL and not ifaces:
            continue
        sm_ = carried[n] if n in carried else seeds[n]()
        if n == 'SoftwareNameplate' and app.get('source'):
            for old in [e for e in sm_.submodel_element if e.id_short.startswith('SoftwareNameplateType_App_')]:
                sm_.submodel_element.remove(old)
            sm_.submodel_element.add(S.build_operator_app(machine, app))
        store.add(sm_)
        print(n, 'carried over' if n in carried else 'seeded')
    # a carried SimulationModels keeps its File value; re-add the embedded doc under the same name
    doc = os.path.join(mdir, 'mechanics/kinematics/MOTION.md')
    if 'SimulationModels' in carried:
        add_file(doc, 'text/markdown')

    aas = model.AssetAdministrationShell(model.AssetInformation(model.AssetKind.TYPE, global_asset_id=f'{ID}{machine}/asset'), f'{ID}{machine}/aas',
                                         id_short=slug(machine), submodel={model.ModelReference.from_referable(s) for s in list(store) if isinstance(s, model.Submodel)})
    store.add(aas)
    return store, fs, machine, mdir, out


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items() if not (k == 'specificAssetIds' and v == [])}
    if isinstance(o, list):
        return [clean(x) for x in o]
    return o


def canonical_json(store):
    s = io.StringIO()
    bjson.write_aas_json_file(s, store)
    d = clean(json.loads(s.getvalue()))
    d['submodels'].sort(key=lambda x: x['id'])
    for a in d['assetAdministrationShells']:
        a['submodels'].sort(key=lambda r: r['keys'][0]['value'])
    return {k: d[k] for k in ('assetAdministrationShells', 'submodels', 'conceptDescriptions') if k in d}


def dumps(d):
    return json.dumps(d, indent=1, ensure_ascii=False) + '\n'


def write_aasx(store, fs, path):
    with aasx.AASXWriter(path + '.tmp') as w:
        w.write_aas([o.id for o in store if isinstance(o, model.AssetAdministrationShell)], store, fs, write_json=True)
    zi = zipfile.ZipFile(path + '.tmp')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zo:
        for i in zi.infolist():
            d = zi.read(i.filename)
            if i.filename == 'aasx/data.json':
                d = json.dumps(clean(json.loads(d)), ensure_ascii=False).encode()
            zo.writestr(i, d)
    zi.close()
    os.remove(path + '.tmp')


def validate(path):
    from aas_test_engines import file as F
    with open(path, 'rb') as fh:
        r = F.check_aasx_file(fh)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--machine', required=True)
    ap.add_argument('--seed-kinematics')
    ap.add_argument('--facts')
    ap.add_argument('--reseed', action='store_true', help='rebuild the machine-authored submodels from the seed files (discards edits in the AAS)')
    ap.add_argument('--no-part-files', action='store_true')
    ap.add_argument('--no-aasx', action='store_true')
    ap.add_argument('--step-name')
    args = ap.parse_args()
    store, fs, machine, mdir, out = build(args)
    d = canonical_json(store)
    os.makedirs(os.path.join(mdir, 'aas'), exist_ok=True)
    jp = os.path.join(mdir, 'aas', f'{machine}.aas.json')
    open(jp, 'w', encoding='utf-8').write(dumps(d))
    print('wrote', jp, round(os.path.getsize(jp) / 1e6, 2), 'MB')
    if not args.no_aasx:
        # AASX from the committed JSON, so the package cannot drift from it
        with open(jp, encoding='utf-8') as fh:
            store2 = bjson.read_aas_json_file(fh, failsafe=False)
        ap_ = os.path.join(out, f'{machine}.aasx')
        write_aasx(store2, fs, ap_)
        print('wrote', ap_, round(os.path.getsize(ap_) / 1e6, 1), 'MB')
        r = validate(ap_)
        print('aas-test-engines:', 'PASS' if r.ok() else 'FAIL')
        if not r.ok():
            print('\n'.join(r.to_lines()[:60]))
            sys.exit(1)


if __name__ == '__main__':
    main()
