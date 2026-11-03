"""Builders (basyx) for the machine-authored submodels of the AAS: Nameplate, SoftwareNameplate, TechnicalData, SimulationModels,
Kinematics. They are SEEDED once from `<machine>/aas/seed/machine.facts.json` + `<machine>/mechanics/kinematics/motion.measured.json`
(aas_build.py --seed-kinematics); after that the committed AAS JSON owns them and aas_build carries them over untouched.
Imported by aas_build.py only (needs basyx)."""
import copy, math, os
from basyx.aas import model
import aas_lib
from aas_lib import Bom, clone_paths, part_key, slug

DT = model.datatypes


def sem(x):
    return model.ExternalReference((model.Key(model.KeyTypes.GLOBAL_REFERENCE, x),))


def P(idshort, vt, value, s=None, source=None, unit=None, derived=None):
    q = []
    if source:
        q.append(model.Qualifier('source', DT.String, source))
    if unit:
        q.append(model.Qualifier('unit', DT.String, unit))
    if derived:
        q.append(model.Qualifier('derived', DT.String, derived))
    return model.Property(idshort, vt, value=value, semantic_id=sem(s) if s else None, qualifier=q)


def MLP(idshort, texts, s=None):
    return model.MultiLanguageProperty(idshort, value=model.MultiLanguageTextType(texts), semantic_id=sem(s) if s else None)


def SMC(idshort, s=None, items=()):
    c = model.SubmodelElementCollection(idshort, semantic_id=sem(s) if s else None)
    for i in items:
        if i is not None:
            c.value.add(i)
    return c


def new_sm(machine, name, semid, kind=model.ModellingKind.INSTANCE):
    return model.Submodel(f'{aas_lib.ID}{machine}/sm/{name}', id_short=name, semantic_id=sem(semid), kind=kind)


def fill(sm, items):
    for i in items:
        if i is not None:
            sm.submodel_element.add(i)
    return sm


# ---------------------------------------------------------------- generic encoder for Kinematics/Motion data
class Derived:
    """placeholder for a number that is computed from the drive chain at export time (aas_lib.derive_motion)"""

    def __init__(self, rule, vt=DT.Double, as_list=False):
        self.rule, self.vt, self.as_list = rule, vt, as_list


class Enc:
    PATHLISTS = ('nodes', 'carries', 'from')

    def __init__(self, bom, hs_id, sem_prefix):
        self.bom, self.hs_id, self.sp = bom, hs_id, sem_prefix

    def ref(self, idshort, path):
        keys = [model.Key(model.KeyTypes.SUBMODEL, self.hs_id)] + [model.Key(model.KeyTypes.ENTITY, k) for k in self.bom.chain_of[id(self.bom.by_path[path])]]
        return model.ReferenceElement(idshort, value=model.ModelReference(tuple(keys), model.Entity))

    def s(self, key):
        return sem(self.sp + key)

    def enc(self, key, v):
        """key = idShort (None inside lists)"""
        sid = self.s(key) if key else None
        if isinstance(v, Derived):
            q = [model.Qualifier('derived', DT.String, v.rule)]
            if v.as_list:
                return model.SubmodelElementList(key, model.Property, value_type_list_element=v.vt, semantic_id=sid, qualifier=q)
            return model.Property(key, v.vt, semantic_id=sid, qualifier=q)
        if isinstance(v, bool):
            return model.Property(key, DT.Boolean, value=v, semantic_id=sid)
        if isinstance(v, int):
            return model.Property(key, DT.Integer, value=v, semantic_id=sid)
        if isinstance(v, float):
            return model.Property(key, DT.Double, value=v, semantic_id=sid)
        if isinstance(v, str):
            return model.Property(key, DT.String, value=v, semantic_id=sid)
        if v is None:
            return model.Property(key, DT.String, semantic_id=sid)
        if isinstance(v, dict):
            if key == 'rest_deg':                                         # {node path: value}
                l = model.SubmodelElementList(key, model.SubmodelElementCollection, semantic_id=sid, qualifier=[model.Qualifier('shape', DT.String, 'pathmap')])
                for p, x in v.items():
                    l.value.add(SMC(None, None, [self.ref('Node', p), self.enc('Value', x)]))
                return l
            c = model.SubmodelElementCollection(key, semantic_id=sid)
            for k, x in v.items():
                c.value.add(self.enc(k, x))
            return c
        if isinstance(v, list):
            if key and key.lower() in self.PATHLISTS:
                l = model.SubmodelElementList(key, model.ReferenceElement, semantic_id=sid)
                for p in v:
                    l.value.add(self.ref(None, p))
                return l
            if v and all(isinstance(x, dict) for x in v):
                l = model.SubmodelElementList(key, model.SubmodelElementCollection, semantic_id=sid)
                for x in v:
                    l.value.add(self.enc(None, x))
                return l
            if v and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
                allint = all(isinstance(x, int) for x in v)
                l = model.SubmodelElementList(key, model.Property, value_type_list_element=DT.Integer if allint else DT.Double, semantic_id=sid)
                for x in v:
                    l.value.add(model.Property(None, DT.Integer if allint else DT.Double, value=x if allint else float(x)))
                return l
            l = model.SubmodelElementList(key, model.Property, value_type_list_element=DT.String, semantic_id=sid)
            for x in v:
                l.value.add(model.Property(None, DT.String, value=x))
            return l
        raise TypeError(f'{key}: {type(v)}')


# ---------------------------------------------------------------- Kinematics
def _leaf_part(bom, path, parts, need_teeth=False):
    """part id of the node at `path` (or of its first descendant that satisfies need_teeth)"""
    n = bom.by_path[path]
    stack = [n]
    while stack:
        x = stack.pop(0)
        if x.part is not None:
            if not need_teeth or 'teeth' in (parts[x.part].get('params') or {}):
                return x.part
        stack += x.children
    return None


def build_kinematics(machine, facts, measured, bom, hs_id, parts, cat_id):
    """Kinematics submodel from the measured geometry (mechanics/kinematics/motion.measured.json) + the designer's facts"""
    from aas_lib import Node
    sm = new_sm(machine, 'Kinematics', aas_lib.NS + 'kinematics/1/0')
    K = aas_lib.NS + 'kinematics/'
    enc = Enc(bom, hs_id, K)
    # machine parameters (not part parameters)
    mp = SMC('MachineParameters', K + 'MachineParameters')
    for k, d in facts['parameters'].items():
        mp.value.add(P(k, DT.Integer if isinstance(d['value'], int) else DT.String, d['value'], K + k, source=d['source'], unit=d.get('unit')))
    mp.value.add(P('LineOrder', DT.String, measured['line_order'], K + 'LineOrder', source='measured from CAD + designer 2026-09-30 (answer 1)'))
    conv = SMC('Convention', K + 'Convention', [P('units', DT.String, measured['units']), P('frame', DT.String, measured['convention']['frame']),
                                                  P('pos', DT.String, measured['convention']['pos']), P('note', DT.String, measured['convention']['note'])])
    steps = facts['parameters']['StepsPerRev']['value']
    deg = 360.0 / steps
    # clones: measured paths -> source paths in the AAS (clone nodes do not exist in the CAD)
    meas_clones = measured['clones']
    cmap = clone_paths(bom, meas_clones)
    back = {}
    for c in meas_clones:
        for j, src in enumerate(c['from']):
            back[cmap[c['id']][j]] = src

    def to_src(p):
        for cp, src in back.items():
            if p == cp or p.startswith(cp + '/'):
                return src + p[len(cp):]
        return p
    clones = model.SubmodelElementList('Clones', model.SubmodelElementCollection, semantic_id=sem(K + 'Clones'))
    for c in meas_clones:
        clones.value.add(SMC(None, K + 'Clone', [P('Id', DT.String, c['id']), P('Actuator', DT.String, c['actuator']), enc.enc('From', c['from']),
                                                 enc.enc('OffsetMm', [float(x) for x in c['offset_mm']]), P('Label', DT.String, c['label']), P('Note', DT.String, c['note'])]))
    acts = model.SubmodelElementList('Actuators', model.SubmodelElementCollection, semantic_id=sem(K + 'Actuators'))
    part_ref = lambda pid: model.ModelReference((model.Key(model.KeyTypes.SUBMODEL, cat_id), model.Key(model.KeyTypes.SUBMODEL_ELEMENT_COLLECTION, 'Parts'),
                                                 model.Key(model.KeyTypes.SUBMODEL_ELEMENT_COLLECTION, part_key(pid))), model.SubmodelElementCollection)
    steps_ref = model.ModelReference((model.Key(model.KeyTypes.SUBMODEL, sm.id), model.Key(model.KeyTypes.SUBMODEL_ELEMENT_COLLECTION, 'MachineParameters'),
                                      model.Key(model.KeyTypes.PROPERTY, 'StepsPerRev')), model.Property)
    for aid, a0 in measured['actuators'].items():
        a = copy.deepcopy(a0)
        chain = a.pop('chain', None)
        f = facts['actuators'][aid]
        ac = SMC(slug(aid), K + 'Actuator', [P('Id', DT.String, aid, K + 'Id')])
        ctl = SMC('Controller', K + 'Controller', [P(k, DT.String, v, K + k) for k, v in f['controller'].items()])
        ac.value.add(ctl)
        ratio = 1.0
        tm = td = None
        r = dr = None
        if chain:
            drv = SMC('Drive', K + 'Drive')
            drv.value.add(model.ReferenceElement('StepsPerRev', value=steps_ref, semantic_id=sem(K + 'StepsPerRev')))
            drv.value.add(model.ReferenceElement('Motor', value=part_ref(_leaf_part(bom, chain['motor'], parts)), semantic_id=sem(K + 'Motor')))
            drv.value.add(enc.ref('MotorNode', chain['motor']))
            if 'driver' in f:
                drv.value.add(model.ReferenceElement('Driver', value=part_ref(f['driver']['part']), semantic_id=sem(K + 'Driver')))
                drv.value.add(P('DriverSource', DT.String, f['driver']['source'], K + 'DriverSource'))
            mpart = _leaf_part(bom, chain['motor_pulley'], parts, need_teeth=True)
            mpu = SMC('MotorPulley', K + 'MotorPulley', [model.ReferenceElement('Part', value=part_ref(mpart), semantic_id=sem(K + 'Part')), enc.ref('Node', chain['motor_pulley'])])
            tm = parts[mpart]['params']['teeth']
            dpart = _leaf_part(bom, chain['driven_pulley'], parts, need_teeth=True)
            dpu = SMC('DrivenPulley', K + 'DrivenPulley', [enc.ref('Node', chain['driven_pulley'])])
            if 'driven_pulley_teeth' in chain:
                td = chain['driven_pulley_teeth']
                dpu.value.add(P('TeethOverride', DT.Integer, td, K + 'TeethOverride', source=chain['driven_pulley_teeth_source']))
                if dpart:
                    dpu.value.add(model.ReferenceElement('Part', value=part_ref(dpart), semantic_id=sem(K + 'Part')))
            else:
                td = parts[dpart]['params']['teeth']
                dpu.value.add(model.ReferenceElement('Part', value=part_ref(dpart), semantic_id=sem(K + 'Part')))
            drv.value.add(mpu)
            drv.value.add(dpu)
            ratio = tm / td
            if 'roller_radius_mm' in chain:
                r, dr = chain['roller_radius_mm'], chain['driven_roller_radius_mm']
                drv.value.add(enc.ref('Roller', chain['roller']))
                drv.value.add(P('RollerRadiusMm', DT.Double, r, K + 'RollerRadiusMm', source='measured from the CAD mesh'))
                drv.value.add(enc.ref('DrivenRoller', chain['driven_roller']))
                drv.value.add(P('DrivenRollerRadiusMm', DT.Double, dr, K + 'DrivenRollerRadiusMm', source='measured from the CAD mesh'))
            if 'ratio' in a:
                drv.value.add(P('RatioText', DT.String, f['ratio_text'], K + 'RatioText'))
            ac.value.add(drv)
        # motion record with derived placeholders
        m = {}
        for k, v in a.items():
            if k == 'clone':
                m['clone'] = v
                m['clone_id'] = next(c['id'] for c in meas_clones if c['actuator'] == aid)
            else:
                m[k] = v
        if a.get('clone'):
            for k in ('nodes',):
                m[k] = [to_src(p) for p in m[k]]
        def rule_for(val, top, idx, kind):
            if kind == 'belt' and top:
                cands = {'belt_surface': 2 * math.pi * r / steps * ratio if r else None}
            elif kind == 'belt':
                pm = 2 * math.pi * r / steps * ratio
                cands = {'output': deg * ratio, 'driven_roller': math.degrees(pm / dr), 'motor_shaft': deg}
            else:
                cands = {'output': deg * ratio, 'output_mirrored': -deg * ratio, 'motor_shaft': deg}
            for name, c in cands.items():
                if c is not None and round(c, 5) == val:
                    return name
            raise ValueError(f'{aid}: per_step {val} matches no rule {cands}')
        if 'per_step' in m and m['per_step'] is not None:
            m['per_step'] = Derived('per_step:' + rule_for(m['per_step'], True, 0, a['kind']))
        if 'steps_per_rev' in m:
            m['steps_per_rev'] = Derived('steps_per_rev', DT.Integer)
        if 'ratio' in m:
            m['ratio'] = Derived('ratio_text', DT.String)
        if 'roller_radius_mm' in m:
            assert round(r, 2) == m['roller_radius_mm']
            m['roller_radius_mm'] = Derived('roller_radius_mm')
        if 'range_steps' in m and m.get('range') == [0, round(m['range_steps'][1] * deg, 1)]:
            m['range'] = Derived('range_from_steps', as_list=True)
        if 'also' in m:
            al = []
            for x in m['also']:
                x = dict(x)
                if a.get('clone'):
                    x['nodes'] = [to_src(p) for p in x['nodes']]
                if x.get('per_step') is not None:
                    x['per_step'] = Derived('per_step:' + rule_for(x['per_step'], False, 0, 'belt' if a['kind'] == 'belt' else 'rot'))
                al.append(x)
            m['also'] = al
        mo = SMC('Motion', K + 'Motion')
        for k, v in m.items():
            mo.value.add(enc.enc(k, v))
        ac.value.add(mo)
        acts.value.add(ac)
    fill(sm, [mp, conv, clones, acts])
    return sm


# ---------------------------------------------------------------- IDTA-shaped identity submodels
def build_nameplate(machine, facts):
    sm = new_sm(machine, 'Nameplate', 'https://admin-shell.io/idta/nameplate/3/0/Nameplate')
    n = facts['nameplate']
    fill(sm, [
        MLP('ManufacturerProductDesignation', n['designation'], '0112/2///61987#ABA567#009'),
        P('ManufacturerProductType', DT.String, n['product_type'], '0112/2///61987#ABA300#008', source=n.get('source')),
        P('ProductArticleNumberOfManufacturer', DT.String, n['article_number'], '0112/2///61987#ABA581#007', source=n.get('source')),
        P('FirmwareVersion', DT.String, facts['firmware']['version'], '0112/2///61987#ABA302#006', source=facts['firmware']['path']),
    ])
    return sm


def build_software_nameplate(machine, facts):
    sm = new_sm(machine, 'SoftwareNameplate', 'https://admin-shell.io/idta/SoftwareNameplate/1/0')
    fw = facts['firmware']
    T = 'https://admin-shell.io/idta/SoftwareNameplate/1/0/SoftwareNameplate/'
    typ = SMC('SoftwareNameplateType', T + 'SoftwareNameplateType', [
        P('URIOfTheProduct', DT.String, f'{aas_lib.ID}{machine}/firmware/{slug(fw["name"])}', '0173-1#02-AAY811#001'),
        MLP('ManufacturerName', fw.get('manufacturer', {'en': 'unknown'}), '0173-1#02-AAO677#002'),
        MLP('ManufacturerProductDesignation', {'en': f'{fw["name"]} (ESP32 master + 2x Arduino Nano)'}, '0173-1#02-AAW338#001'),
        P('SoftwareType', DT.String, 'Firmware', T + 'SoftwareNameplateType/SoftwareType'),
        P('Version', DT.String, fw['version'], T + 'SoftwareNameplateType/Version', source=fw['version_source']),
    ])
    mods = SMC('InstalledModules', T + 'SoftwareNameplateInstance/InstalledModules')
    for i, m in enumerate(fw['modules']):
        mods.value.add(P(f'InstalledModule{i + 1}', DT.String, f'{m["file"]} -> {m["board"]}', T + 'SoftwareNameplateInstance/InstalledModule'))
    fill(sm, [typ, SMC('SoftwareNameplateInstance', T + 'SoftwareNameplateInstance', [mods])])
    return sm


def build_technical_data(machine, facts):
    sm = new_sm(machine, 'TechnicalData', '0173-1#01-AHX837#002')
    n = facts['nameplate']
    fill(sm, [
        SMC('GeneralInformation', '0173-1#02-ABK161#002/0173-1#01-AHX838#002', [
            MLP('ManufacturerProductDesignation', n['designation'], '0173-1#02-AAW338#003'),
            P('ManufacturerArticleNumber', DT.String, n['article_number'], '0173-1#02-AAO676#005')]),
        SMC('FurtherInformation', '0173-1#02-ABK164#002', [MLP('TextStatement', {'en': 'Machine parameters that are not part parameters live in the Kinematics submodel (MachineParameters), each with a source qualifier. Part parameters live in PartCatalog.'}, '0173-1#02-ABK134#002')]),
    ])
    return sm


def build_simulation_models(machine, facts, fs_add):
    sm = new_sm(machine, 'SimulationModels', 'https://admin-shell.io/idta/SubmodelTemplate/SimulationModels/1/1')
    S = 'https://admin-shell.io/idta/SimulationModels/'
    sim = facts['simulation']
    items = [MLP('Summary', {'en': sim['summary']}, S + 'Summary/1/0'),
             P('TypeOfModel', DT.String, sim['type'], S + 'TypeOfModel/1/0'),
             P('ScopeOfModel', DT.String, sim['scope'], S + 'ScopeOfModel/1/0'),
             P('EngineeringDomain', DT.String, 'mechatronics', S + 'EngineeringDomain/1/0')]
    doc = fs_add(sim.get('doc'), 'text/markdown')
    if doc:
        items.append(model.File('RefSimDocumentation', 'text/markdown', value=doc, semantic_id=sem(S + 'RefSimDocumentation/1/0')))
    fill(sm, [SMC('SimulationModel', S + 'SimulationModel/1/1', items)])
    return sm


# ---------------------------------------------------------------- operator app (SoftwareNameplate) and interfaces (AssetInterfacesDescription)
def build_operator_app(machine, app):
    """One more `SoftwareNameplateType` collection (IDTA 02007 shape) for the machine's operator app. `app` = sim/site.json -> app.source (+ title).
    Regenerated on every aas_build from that record, so the AAS and the site build name the same repository/commit."""
    T = 'https://admin-shell.io/idta/SoftwareNameplate/1/0/SoftwareNameplate/'
    A = aas_lib.NS + 'software/'
    src = app['source']
    return SMC('SoftwareNameplateType_App_' + slug(src.get('name', 'app')), T + 'SoftwareNameplateType', [
        P('URIOfTheProduct', DT.String, src['remote'], '0173-1#02-AAY811#001'),
        MLP('ManufacturerProductDesignation', {'en': app.get('title', src.get('name', 'app'))}, '0173-1#02-AAW338#001'),
        P('SoftwareType', DT.String, 'Application', T + 'SoftwareNameplateType/SoftwareType'),
        P('Version', DT.String, src['version'], T + 'SoftwareNameplateType/Version', source='pubspec.yaml of the app repository'),
        P('SourceRepository', DT.String, src['remote'], A + 'SourceRepository'),
        P('SourceBranch', DT.String, src['branch'], A + 'SourceBranch'),
        P('SourceCommit', DT.String, src['commit'], A + 'SourceCommit'),
        P('BuildCommand', DT.String, src.get('build_cmd', ''), A + 'BuildCommand'),
        P('Transport', DT.String, app.get('transport', ''), A + 'Transport'),
        P('ConnectsToBoard', DT.String, app.get('board', ''), A + 'ConnectsToBoard'),
    ])


AID = 'https://admin-shell.io/idta/AssetInterfacesDescription/1/0/'
TD = 'https://www.w3.org/2019/wot/td#'
HYP = 'https://www.w3.org/2019/wot/hypermedia#'
JSS = 'https://www.w3.org/2019/wot/json-schema#'
SEC = 'https://www.w3.org/2019/wot/security#'


def _sml(idshort, sid, vt, values):
    l = model.SubmodelElementList(idshort, model.Property, value_type_list_element=vt, semantic_id=sem(sid))
    for i, v in enumerate(values):
        l.value.add(model.Property(None, vt, value=v))
    return l


def build_asset_interfaces(machine, ifaces):
    """IDTA 02017 v1.1 (Asset Interfaces Description). BLE is not one of its bindings (HTTP, Modbus, MQTT, OPC UA, BACnet, IO-Link), so an interface here uses the generic
    Interface structure (EndpointMetadata / InteractionMetadata / ExternalDescriptor, W3C WoT Thing Description terms and their semanticIds) with a own binding:
    base = ble:///<service uuid>/, form href = characteristic uuid, subprotocol ble-write | ble-notify, and our own semanticIds `NS/ble/*` for what WoT has no term for
    (message patterns, replies, the request that reads a property back). The template leaves actions/events empty; they follow the WoT TD affordance terms.
    `ifaces` = parsed electronics/interfaces/*.json (see the machine's ble.json)."""
    sm = new_sm(machine, 'AssetInterfacesDescription', 'https://admin-shell.io/idta/AssetInterfacesDescription/1/1/Submodel')
    sm.administration = model.AdministrativeInformation(version='1', revision='1')
    B = aas_lib.NS + 'ble/'
    for d in ifaces:
        idsh = 'Interface' + d['id']
        ch = d['characteristics']
        base = f'ble:///{d["service"]}/'
        scheme_ref = model.ModelReference((model.Key(model.KeyTypes.SUBMODEL, sm.id), model.Key(model.KeyTypes.SUBMODEL_ELEMENT_COLLECTION, idsh),
                                           model.Key(model.KeyTypes.SUBMODEL_ELEMENT_COLLECTION, 'EndpointMetadata'),
                                           model.Key(model.KeyTypes.SUBMODEL_ELEMENT_COLLECTION, 'securityDefinitions'),
                                           model.Key(model.KeyTypes.SUBMODEL_ELEMENT_COLLECTION, 'nosec_sc')), model.SubmodelElementCollection)

        def sec_list():
            l = model.SubmodelElementList('security', model.ReferenceElement, semantic_id=sem(TD + 'hasSecurityConfiguration'), order_relevant=True)
            l.value.add(model.ReferenceElement(None, value=scheme_ref))
            return l

        def form(char, sub, extra=()):
            return SMC('forms', TD + 'hasForm', [P('href', DT.String, ch[char]['uuid'], HYP + 'hasTarget'), P('contentType', DT.String, d['content_type'], HYP + 'forContentType'),
                                                 P('subprotocol', DT.String, sub, HYP + 'forSubProtocol'), sec_list(), *extra])
        props = SMC('properties', TD + 'PropertyAffordance')
        for p in d.get('properties', []):
            pd = SMC(p['id'], AID + 'PropertyDefinition', [
                P('key', DT.String, p['message'].split(':')[0], AID + 'key'), P('type', DT.String, p['type'], 'https://www.w3.org/1999/02/22-rdf-syntax-ns#type'),
                P('title', DT.String, p['title'], TD + 'title'), P('observable', DT.Boolean, True, TD + 'isObservable'),
                _sml('enum', JSS + 'enum', DT.String, [f'{k}: {v}' for k, v in p['enum'].items()]) if p.get('enum') else None,
                P('unit', DT.String, p['unit'], 'https://schema.org/unitCode') if p.get('unit') else None,
                P('messagePattern', DT.String, p['message'], B + 'messagePattern'),
                P('readWith', DT.String, p['readable_with'], B + 'readWith') if p.get('readable_with') else None,
                P('note', DT.String, p['note'], B + 'note') if p.get('note') else None,
                form('tx', 'ble-notify')])
            props.value.add(pd)
        acts = SMC('actions', TD + 'ActionAffordance')
        for a in d.get('actions', []):
            acts.value.add(SMC(a['id'], B + 'ActionDefinition', [
                P('title', DT.String, a['title'], TD + 'title'), P('writePayload', DT.String, a['write'], B + 'writePayload'),
                _sml('replies', B + 'replies', DT.String, a['replies']) if a.get('replies') else None,
                P('note', DT.String, a['note'], B + 'note') if a.get('note') else None,
                form('rx', 'ble-write')]))
        evs = SMC('events', TD + 'EventAffordance')
        for e in d.get('events', []):
            evs.value.add(SMC(e['id'], B + 'EventDefinition', [
                P('title', DT.String, e['title'], TD + 'title'), P('messagePattern', DT.String, e['message'], B + 'messagePattern'),
                P('note', DT.String, e['note'], B + 'note') if e.get('note') else None, form('tx', 'ble-notify')]))
        chars = SMC('Characteristics', B + 'Characteristics', [
            SMC(k, B + 'Characteristic', [P('uuid', DT.String, v['uuid'], B + 'uuid'), P('gattProperties', DT.String, v['properties'], B + 'gattProperties'),
                                          P('direction', DT.String, v['direction'], B + 'direction')]) for k, v in ch.items()])
        it = SMC(idsh, AID + 'Interface', [
            P('title', DT.String, d['title'], TD + 'title'),
            SMC('EndpointMetadata', AID + 'EndpointMetadata', [
                P('base', DT.AnyURI, base, TD + 'baseURI'), P('contentType', DT.String, d['content_type'], HYP + 'forContentType'), sec_list(),
                SMC('securityDefinitions', TD + 'definesSecurityScheme', [SMC('nosec_sc', SEC + 'NoSecurityScheme', [P('scheme', DT.String, 'nosec', SEC + 'SecurityScheme')])]),
                P('deviceName', DT.String, d['device_name'], B + 'deviceName'), chars]),
            SMC('InteractionMetadata', AID + 'InteractionMetadata', [props, acts, evs]),
            SMC('ExternalDescriptor', AID + 'ExternalDescriptor', [model.File('fileName', 'text/markdown', value='file:electronics/interfaces/ble.md', semantic_id=sem(AID + 'externalDescriptorName'))]),
        ])
        sm.submodel_element.add(it)
    return sm
