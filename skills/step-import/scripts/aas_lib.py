"""AAS helpers that work on the plain AAS JSON (no basyx needed): navigation, the BoM path index, and the two one-way derivations
(parts.json, motion.json). Used by aas_build / aas_export / aas_check and by machine repos (mechanics/kinematics/build_motion.py).

The AAS JSON is the single source of truth; nothing here writes it."""
import hashlib, json, math, os, re

# Our own ids: semanticIds `<BASE>semantics/<type>/<property>`, AAS / asset / submodel ids `<BASE>ids/<machine>/...`.
# `DE_AAS_BASE` sets the base for new files (a deployment that has published AAS files under its own domain sets it to
# that domain). A file that exists keeps the base it was written with: writers call `adopt(path)` before they rebuild a
# part of it (its carried submodels reference the others by id); readers take a file's own semanticIds under either base.
BASE = (os.environ.get('DE_AAS_BASE', '').strip() or 'https://design-engineer.dev').rstrip('/') + '/'
NS = BASE + 'semantics/'
ID = BASE + 'ids/'
HS_SEM = 'https://admin-shell.io/idta/HierarchicalStructures/1/1/Submodel'


# ---------------------------------------------------------------- ids
def base_of(doc):
    """The base an AAS JSON's own ids were written with (its shell id is `<base>ids/<machine>/aas`), None when there is none."""
    for a in (doc or {}).get('assetAdministrationShells', []):
        m = re.match(r'(.+/)ids/[^/]+/aas$', a.get('id') or '')
        if m:
            return m.group(1)
    return None


def adopt(doc_or_path):
    """Write with the base an existing AAS file (a dict or a path) already uses, so a rebuilt submodel keeps its id and the
    carried ones that reference it stay valid; the configured base when there is no such file. -> (NS, ID)."""
    global BASE, NS, ID
    doc = doc_or_path
    if isinstance(doc_or_path, (str, os.PathLike)):
        doc = None
        if os.path.isfile(doc_or_path):
            with open(doc_or_path, encoding='utf-8') as fh:
                doc = json.load(fh)
    base = base_of(doc) if isinstance(doc, dict) else None
    if base:
        BASE, NS, ID = base, base + 'semantics/', base + 'ids/'
    return NS, ID


def own_semantic(value, doc=None):
    """The `<type>/<property>` key of one of our own semanticIds (under the configured base or the one `doc` was written
    with), else None."""
    for ns in {NS, (base_of(doc) or BASE) + 'semantics/'}:
        if isinstance(value, str) and value.startswith(ns):
            return value[len(ns):]
    return None


def slug(s, maxlen=100):
    """idShort-safe ascii slug. Stable: depends only on s. Non-ASCII input gets a short hash so different names never collide."""
    t = re.sub(r'[^A-Za-z0-9_]+', '_', s).strip('_')
    if not s.isascii():
        t = (t[:maxlen - 9] + '_' if t else '') + hashlib.sha1(s.encode()).hexdigest()[:8]
    return (t or 'x')[:maxlen]


def machine_name(mdir):
    """Machine name = the stem of aas/*.aas.json when exactly one exists (a project folder need not be named after the machine), else the folder name."""
    import glob, os
    f = glob.glob(os.path.join(mdir, 'aas', '*.aas.json'))
    return os.path.basename(f[0])[:-len('.aas.json')] if len(f) == 1 else os.path.basename(os.path.abspath(mdir).rstrip('/'))


def part_key(part_id):
    return 'P_' + slug(part_id)


# ---------------------------------------------------------------- json navigation
def kids(e):
    mt = e.get('modelType')
    if mt == 'Submodel':
        return e.get('submodelElements', [])
    if mt == 'Entity':
        return e.get('statements', [])
    v = e.get('value')
    return v if isinstance(v, list) else []


def get(e, *names):
    for n in names:
        e = next((c for c in kids(e) if c.get('idShort') == n), None)
        if e is None:
            return None
    return e


def submodel(aas, id_short):
    return next((s for s in aas['submodels'] if s.get('idShort') == id_short), None)


def pval(e):
    """typed value of a Property"""
    v = e.get('value')
    if v is None:
        return None
    vt = e.get('valueType', 'xs:string')
    if vt in ('xs:integer', 'xs:int', 'xs:long', 'xs:short', 'xs:nonNegativeInteger'):
        return int(v)
    if vt in ('xs:double', 'xs:float', 'xs:decimal'):
        return float(v)
    if vt == 'xs:boolean':
        return v == 'true'
    return v


def qual(e, typ):
    return next((q.get('value') for q in e.get('qualifiers', []) if q.get('type') == typ), None)


def display_name(e):
    dn = e.get('displayName') or []
    return dn[0]['text'] if dn else e.get('idShort')


def resolve(aas, keys):
    """element addressed by a ModelReference key list"""
    e = next(s for s in aas['submodels'] if s['id'] == keys[0]['value'])
    for k in keys[1:]:
        e = next(c for c in kids(e) if c.get('idShort') == k['value'])
    return e


# ---------------------------------------------------------------- BoM index (HierarchicalStructures)
class Node:
    __slots__ = ('short', 'name', 'drawing', 'part', 'matrix', 'children', 'parent')

    def __init__(self, short, name, drawing=None, part=None, matrix=None):
        self.short, self.name, self.drawing, self.part, self.matrix = short, name, drawing, part, matrix
        self.children, self.parent = [], None

    def add(self, c):
        c.parent = self
        self.children.append(c)
        return c


class Bom:
    """Node tree + path index. path = 'name[k]/name[k]/...' (k = 0-based index among same-named siblings; root k = 0), the
    convention of mechanics/kinematics/motion.json. chain = tuple of entity idShorts from EntryNode (what a ModelReference holds)."""

    def __init__(self, root):
        self.root = root
        self.by_path, self.by_chain, self.path_of, self.chain_of = {}, {}, {}, {}

        def walk(n, chain, path):
            self.by_path[path] = n
            self.by_chain[chain] = n
            self.path_of[id(n)], self.chain_of[id(n)] = path, chain
            seen = {}
            for c in n.children:
                k = seen.get(c.name, 0)
                seen[c.name] = k + 1
                walk(c, chain + (c.short,), f'{path}/{c.name}[{k}]')
        walk(root, (root.short,), f'{root.name}[0]')

    @classmethod
    def from_json(cls, aas):
        hs = submodel(aas, 'HierarchicalStructures')

        def mk(e):
            pl = get(e, 'Placement')
            dr = get(e, 'Drawing')
            same = get(e, 'SameAs')
            n = Node(e['idShort'], display_name(e), pval(dr) if dr else None,
                     same['second']['keys'][-1]['value'] if same else None, pval(pl) if pl else None)
            for c in e.get('statements', []):
                if c.get('modelType') == 'Entity':
                    n.add(mk(c))
            return n
        return cls(mk(get(hs, 'EntryNode')))

    def keys(self, path, submodel_id):
        chain = self.chain_of[id(self.by_path[path])]
        return [{'type': 'Submodel', 'value': submodel_id}] + [{'type': 'Entity', 'value': s} for s in chain]

    def path_from_keys(self, keys):
        return self.path_of[id(self.by_chain[tuple(k['value'] for k in keys[1:])])]

    def all(self):
        out = []

        def w(n):
            out.append(n)
            for c in n.children:
                w(c)
        w(self.root)
        return out


def clone_paths(bom, clones):
    """Paths of the viewer-side clones (motion.json `clones`): a clone is appended as the LAST same-named sibling under the same
    parent, after every existing sibling and every earlier clone. clones = [{'id','from':[path,...]}] in creation order.
    Returns {clone id: [clone path per `from` entry]}"""
    seen, out = {}, {}
    for c in clones:
        res = []
        for src in c['from']:
            n = bom.by_path[src]
            par = n.parent
            key = (id(par), n.name)
            if key not in seen:
                seen[key] = sum(1 for x in par.children if x.name == n.name)
            res.append(f'{bom.path_of[id(par)]}/{n.name}[{seen[key]}]')
            seen[key] += 1
        out[c['id']] = res
    return out


# ---------------------------------------------------------------- derivation 1: parts.json
def _decode_scalar_or_list(e):
    if e['modelType'] == 'Property':
        return pval(e)
    return [pval(c) for c in e.get('value', [])]        # SubmodelElementList of Property


def export_parts(aas):
    catsm = submodel(aas, 'PartCatalog')
    cat = get(catsm, 'Parts')
    bom = Bom.from_json(aas)
    parts = []
    for c in kids(cat):
        g = lambda n: (lambda e: pval(e) if e is not None else None)(get(c, n))
        pid = g('PartId')
        bb = [[float(x) for x in (r['min'], r['max'])] for r in (get(c, 'BboxX'), get(c, 'BboxY'), get(c, 'BboxZ'))]
        p = dict(id=pid, name=g('Name'), drawing=g('Drawing'), kind=g('Kind'), material=g('Material'), module=g('Module'),
                 instances=g('InstanceCount'), bbox_mm=[[bb[0][0], bb[1][0], bb[2][0]], [bb[0][1], bb[1][1], bb[2][1]]],
                 volume_mm3=g('VolumeMm3'), triangles=g('Triangles'),
                 files={'step': f'parts/{pid}.step', 'stl': f'parts/{pid}.stl'}, type=g('Type'))
        pr = get(c, 'Params')
        if pr is not None:
            p['params'] = {q['idShort']: _decode_scalar_or_list(q) for q in kids(pr)}
            p['params_source'] = {q['idShort']: qual(q, 'source') for q in kids(pr) if qual(q, 'source') is not None}
            ck = get(c, 'ParamsCheck')
            if ck is not None:
                p['params_check'] = {q['idShort']: pval(q) for q in kids(ck)}
            cf = get(c, 'ParamsConflicts')
            if cf is not None:
                p['params_conflicts'] = {q['idShort']: {x['idShort']: pval(x) for x in kids(q)} for q in kids(cf)}
        parts.append(p)

    def tree(n):
        return dict(name=n.name, drawing=n.drawing, children=[tree(c) for c in n.children if c.part is None])
    sm = get(catsm, 'AssemblySummary')
    allnodes = bom.all()
    asm = dict(root=bom.root.name, nodes=len(allnodes), instances=sum(1 for n in allnodes if n.part is not None),
               distinct_parts=len(parts), triangles=pval(get(sm, 'Triangles')),
               bbox_mm=[[float(get(sm, a)[k]) for a in ('BboxX', 'BboxY', 'BboxZ')] for k in ('min', 'max')],
               source=pval(get(sm, 'Source')),
               tessellation=dict(deflection_mm=pval(get(get(sm, 'Tessellation'), 'DeflectionMm')), angle_rad=pval(get(get(sm, 'Tessellation'), 'AngleRad'))),
               tree=tree(bom.root))
    return {'assembly': asm, 'parts': parts}


# ---------------------------------------------------------------- derivation 2: motion.json
def _fmt_ratio(tm, td):
    g = math.gcd(tm, td)
    return f'{tm // g}:{td // g}'


def _teeth(aas, drive, role):
    """teeth of the pulley `role` (MotorPulley|DrivenPulley): TeethOverride if given, else the 'teeth' param of the referenced PartCatalog part"""
    el = get(drive, role)
    if el is None:
        return None
    ov = get(el, 'TeethOverride')
    if ov is not None and pval(ov) is not None:
        return pval(ov)
    part = resolve(aas, get(el, 'Part')['value']['keys'])
    return pval(get(get(part, 'Params'), 'teeth'))


class _Ctx:
    def __init__(self, aas, bom, clone_map, clones):
        self.aas, self.bom, self.clone_map, self.clones = aas, bom, clone_map, clones
        self.clone = None       # id of the clone the current actuator lives on

    def path(self, ref):
        p = self.bom.path_from_keys(ref['value']['keys'])
        if self.clone:                                   # actuator on a viewer-side clone: the ref names the SOURCE node
            c = next(c for c in self.clones if c['id'] == self.clone)
            for j, src in enumerate(c['from']):
                if p == src or p.startswith(src + '/'):
                    return self.clone_map[self.clone][j] + p[len(src):]
            raise KeyError(f'{p} is not under a source of clone {self.clone}')
        return p


def _decode(e, cx, drv):
    """generic inverse of aas_build.encode (+ placeholders for derived numbers)"""
    mt = e['modelType']
    d = qual(e, 'derived')
    if d is not None:
        return drv(d)
    if mt == 'Property':
        return pval(e)
    if mt == 'Range':
        return [float(e['min']), float(e['max'])]
    if mt == 'ReferenceElement':
        return cx.path(e)
    if mt == 'SubmodelElementList':
        if qual(e, 'shape') == 'pathmap':               # {node path: value}
            return {cx.path(get(c, 'Node')): pval(get(c, 'Value')) for c in e.get('value', [])}
        return [_decode(c, cx, drv) for c in e.get('value', [])]
    if mt == 'SubmodelElementCollection':
        return {c['idShort']: _decode(c, cx, drv) for c in e.get('value', [])}
    raise ValueError(mt)


def derive_motion(aas):
    kin = submodel(aas, 'Kinematics')
    bom = Bom.from_json(aas)
    mp = get(kin, 'MachineParameters')
    steps = pval(get(mp, 'StepsPerRev'))
    clones = []
    for c in get(kin, 'Clones')['value']:
        clones.append(dict(id=pval(get(c, 'Id')), actuator=pval(get(c, 'Actuator')),
                           **{'from': [bom.path_from_keys(r['value']['keys']) for r in get(c, 'From')['value']]},
                           offset_mm=[float(x['value']) for x in get(c, 'OffsetMm')['value']],
                           label=pval(get(c, 'Label')), note=pval(get(c, 'Note'))))
    cmap = clone_paths(bom, clones)
    cx = _Ctx(aas, bom, cmap, clones)
    conv = get(kin, 'Convention')
    actuators = {}
    for a in get(kin, 'Actuators')['value']:
        aid = pval(get(a, 'Id'))
        drive = get(a, 'Drive')
        cx.clone = pval(get(get(a, 'Motion'), 'clone_id')) if get(get(a, 'Motion'), 'clone_id') is not None else None
        deg = 360.0 / steps
        tm, td = (_teeth(aas, drive, 'MotorPulley'), _teeth(aas, drive, 'DrivenPulley')) if drive is not None else (None, None)
        ratio = tm / td if tm and td else 1.0
        r = pval(get(drive, 'RollerRadiusMm')) if drive is not None and get(drive, 'RollerRadiusMm') is not None else None
        dr = pval(get(drive, 'DrivenRollerRadiusMm')) if drive is not None and get(drive, 'DrivenRollerRadiusMm') is not None else None

        def drv(d_, deg=deg, ratio=ratio, r=r, dr=dr, tm=tm, td=td, aid=aid, mot=None):
            if d_ == 'steps_per_rev':
                return steps
            if d_ == 'ratio_text':
                return get(drive, 'RatioText')['value'].replace('{Tm}', str(tm)).replace('{Td}', str(td)).replace('{ratio_text}', _fmt_ratio(tm, td))
            if d_ == 'roller_radius_mm':
                return round(r, 2)
            if d_.startswith('per_step:'):
                rule = d_.split(':', 1)[1]
                if rule == 'belt_surface':
                    return round(2 * math.pi * r / steps * ratio, 5)
                if rule == 'output':
                    return round(deg * ratio, 5)
                if rule == 'output_mirrored':
                    return -round(deg * ratio, 5)
                if rule == 'motor_shaft':
                    return round(deg, 5)
                if rule == 'driven_roller':
                    return round(math.degrees(2 * math.pi * r / steps * ratio / dr), 5)
            raise ValueError(d_)

        def drv2(d_, **kw):
            if d_ == 'range_from_steps':
                rs = rec_steps
                return [0 if rs[0] == 0 else rs[0] * deg, round(rs[1] * deg, 1)]
            return drv(d_)
        motion = get(a, 'Motion')
        rec_steps = None
        rs = get(motion, 'range_steps')
        if rs is not None:
            rec_steps = [int(pval(x)) for x in rs['value']]
        rec = {c['idShort']: _decode(c, cx, drv2) for c in motion['value'] if c['idShort'] != 'clone_id'}
        actuators[aid] = rec
    doc = dict(units=pval(get(conv, 'units')), convention=dict(frame=pval(get(conv, 'frame')), pos=pval(get(conv, 'pos')), note=pval(get(conv, 'note'))),
               line_order=pval(get(mp, 'LineOrder')),
               clones=[dict(id=c['id'], actuator=c['actuator'], **{'from': c['from']}, offset_mm=c['offset_mm'], label=c['label'], note=c['note']) for c in clones],
               actuators=actuators)
    return doc

