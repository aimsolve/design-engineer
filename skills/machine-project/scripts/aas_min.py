"""Plain-JSON builders for a minimal machine AAS (no basyx). Same shapes and ids as step-import's aas_build.py, so
aas_build carries the authored submodels over and rebuilds PartCatalog / HierarchicalStructures from a STEP later."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'step-import', 'scripts'))
import aas_lib as L  # noqa: E402   # L.NS / L.ID read at call time: aas_lib.adopt() may switch them to an existing file's base
from aas_lib import HS_SEM, slug, part_key  # noqa: E402

HSN = 'https://admin-shell.io/idta/HierarchicalStructures/'


def ref(x):
    return {'type': 'ExternalReference', 'keys': [{'type': 'GlobalReference', 'value': x}]}


def prop(id_short, value, vt='xs:string', sem=None, source=None, unit=None):
    e = {'idShort': id_short, 'modelType': 'Property'}
    if sem:
        e['semanticId'] = ref(sem)
    qs = [{'value': v, 'kind': 'ConceptQualifier', 'valueType': 'xs:string', 'type': t} for t, v in (('source', source), ('unit', unit)) if v]
    if qs:
        e['qualifiers'] = qs
    e['value'] = str(value).lower() if isinstance(value, bool) else str(value)
    e['valueType'] = vt
    return e


def vtype(v):
    return 'xs:boolean' if isinstance(v, bool) else 'xs:integer' if isinstance(v, int) else 'xs:double' if isinstance(v, float) else 'xs:string'


def mlp(id_short, texts, sem=None):
    e = {'idShort': id_short, 'modelType': 'MultiLanguageProperty'}
    if sem:
        e['semanticId'] = ref(sem)
    e['value'] = [{'language': k, 'text': v} for k, v in texts.items()]
    return e


def smc(id_short, sem, items):
    e = {'idShort': id_short, 'modelType': 'SubmodelElementCollection'}
    if sem:
        e['semanticId'] = ref(sem)
    if items:
        e['value'] = items      # the schema forbids empty arrays
    return e


def rng(id_short, lo, hi, unit='mm'):
    return {'idShort': id_short, 'modelType': 'Range', 'semanticId': ref(L.NS + 'partcatalog/Bbox'),
            'qualifiers': [{'value': unit, 'kind': 'ConceptQualifier', 'valueType': 'xs:string', 'type': 'unit'}],
            'valueType': 'xs:double', 'min': str(lo), 'max': str(hi)}


def submodel(machine, name, sem, items):
    return {'idShort': name, 'modelType': 'Submodel', 'id': f'{L.ID}{machine}/sm/{name}', 'semanticId': ref(sem), 'submodelElements': items}


def nameplate(machine, designation, product_type, source='scaffold'):
    return submodel(machine, 'Nameplate', 'https://admin-shell.io/idta/nameplate/3/0/Nameplate', [
        mlp('ManufacturerProductDesignation', designation, '0112/2///61987#ABA567#009'),
        prop('ManufacturerProductType', product_type, sem='0112/2///61987#ABA300#008', source=source)])


def part_catalog(machine, parts=()):
    """parts: [{id, name, kind, material, volume_mm3, bbox_mm: [[x0,y0,z0],[x1,y1,z1]]}]"""
    items = []
    for p in parts:
        c = [prop('PartId', p['id']), prop('Name', p.get('name', p['id'])), prop('Kind', p.get('kind', 'made')), prop('Material', p.get('material', 'other'))]
        if p.get('volume_mm3') is not None:
            c.append(prop('VolumeMm3', p['volume_mm3'], 'xs:double', unit='mm3'))
        if p.get('bbox_mm'):
            c += [rng(f'Bbox{ax}', p['bbox_mm'][0][i], p['bbox_mm'][1][i]) for i, ax in enumerate('XYZ')]
        items.append(smc(part_key(p['id']), L.NS + 'partcatalog/Part', c))
    return submodel(machine, 'PartCatalog', L.NS + 'partcatalog/1/0', [smc('Parts', L.NS + 'partcatalog/Parts', items)])


def hier(machine, designation, parts=()):
    """BoM: EntryNode -> node Entity per placements[].node -> leaf Entity per part placed there (BulkCount = its placements in that
    node; a part without placements sits under its own node with its qty; node "" = directly under EntryNode), SameAs -> PartCatalog.
    Parts with no placements at all (old layout): the flat BoM, one leaf per part under EntryNode. (An imported STEP replaces this.)"""
    hs_id = f'{L.ID}{machine}/sm/HierarchicalStructures'
    cat_id = f'{L.ID}{machine}/sm/PartCatalog'

    def leaf(p, path, count=None, k=0):
        short = 'p_' + slug(p['id']) + f'_{k}'
        st = [{'idShort': 'SameAs', 'modelType': 'RelationshipElement', 'semanticId': ref(HSN + 'SameAs/1/0'),
               'first': {'type': 'ModelReference', 'keys': [{'type': 'Submodel', 'value': hs_id}] + [{'type': 'Entity', 'value': k} for k in path + [short]]},
               'second': {'type': 'ModelReference', 'keys': [{'type': 'Submodel', 'value': cat_id}, {'type': 'SubmodelElementCollection', 'value': 'Parts'},
                                                              {'type': 'SubmodelElementCollection', 'value': part_key(p['id'])}]}}]
        if count is not None:
            st.append(prop('BulkCount', count, 'xs:unsignedLong' if isinstance(count, int) else 'xs:double', sem=HSN + 'BulkCount/1/0'))
        return {'idShort': short, 'displayName': [{'language': 'und', 'text': p.get('name', p['id'])[:250]}], 'modelType': 'Entity',
                'semanticId': ref(HSN + 'Node/1/0'), 'statements': st, 'entityType': 'CoManagedEntity'}

    if not any(p.get('placements') for p in parts):
        st = [leaf(p, ['EntryNode']) for p in parts]
    else:
        where = {}                                      # node -> [(part, count)], in the order the nodes first appear
        for p in parts:
            counts = {}
            for x in p.get('placements') or []:
                n = x.get('node') or p.get('node') or ''        # as derive.placed(): a placement without a node is in the part's node
                counts[n] = counts.get(n, 0) + 1
            if not p.get('placements'):
                counts[p.get('node') or ''] = p.get('qty', 1)
            for n, c in counts.items():                  # placements, not qty: a bought part's qty is what to buy (0 = comes with another)
                where.setdefault(n, []).append((p, c))
        st, seen = [], {}

        def leaves(path, items):                        # ids that slug alike: p_x_0, p_x_1
            got = {}
            for p, c in items:
                k = 'p_' + slug(p['id'])
                got[k] = got.get(k, -1) + 1
                yield leaf(p, path, c, got[k])
        for n, items in where.items():
            if n == '':
                st += list(leaves(['EntryNode'], items))
                continue
            k = 'a_' + slug(n)
            seen[k] = seen.get(k, -1) + 1                # «A B» and «A_B» slug alike: _0, _1
            short = f'{k}_{seen[k]}'
            st.append({'idShort': short, 'displayName': [{'language': 'und', 'text': n[:250]}], 'modelType': 'Entity',
                       'semanticId': ref(HSN + 'Node/1/0'), 'statements': list(leaves(['EntryNode', short], items)),
                       'entityType': 'CoManagedEntity'})
    entry = {'idShort': 'EntryNode', 'displayName': [{'language': 'und', 'text': designation[:250]}], 'modelType': 'Entity',
             'semanticId': ref(HSN + 'EntryNode/1/0'), 'entityType': 'CoManagedEntity'}
    if st:
        entry['statements'] = st
    return submodel(machine, 'HierarchicalStructures', HS_SEM, [prop('ArcheType', 'Full', sem=HSN + 'ArcheType/1/0'), entry])


def technical_data(machine, designation, params=None):
    """params: [(NAME, value, unit|None, source)] -> own collection ModelParameters, one Property per number with source + unit."""
    items = [smc('GeneralInformation', '0173-1#02-ABK161#002/0173-1#01-AHX838#002',
                 [mlp('ManufacturerProductDesignation', designation, '0173-1#02-AAW338#003')])]
    items.append(smc('ModelParameters', L.NS + 'modelparameters/1/0',
                     [prop(n, v, vtype(v), sem=f'{L.NS}modelparameters/{n}', source=s, unit=u) for n, v, u, s in (params or [])]))
    return submodel(machine, 'TechnicalData', '0173-1#01-AHX837#002', items)


SM_ORDER = ['HierarchicalStructures', 'Nameplate', 'PartCatalog', 'TechnicalData']


def shell(machine, submodels):
    return {'idShort': slug(machine), 'modelType': 'AssetAdministrationShell', 'id': f'{L.ID}{machine}/aas',
            'assetInformation': {'assetKind': 'Type', 'globalAssetId': f'{L.ID}{machine}/asset'},
            'submodels': [{'type': 'ModelReference', 'keys': [{'type': 'Submodel', 'value': s['id']}]} for s in sorted(submodels, key=lambda x: x['id'])]}


def document(machine, submodels):
    sms = sorted(submodels, key=lambda x: x['id'])
    return {'assetAdministrationShells': [shell(machine, sms)], 'submodels': sms}
