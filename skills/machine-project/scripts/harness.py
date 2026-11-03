#!/usr/bin/env python3
"""Wires in 3D (#113 stage 1): the wires of the electric scheme routed inside the machine, from the same model the scheme is
drawn from, so their routes, lengths and fit are computed and checked after every change. Nothing here is drawn by hand.

The model holds it all:
  - a port's location, in its part file next to its ports: `P(..., ports={"VCC": "+5 V"}, ports_at={"VCC": ((x, y, z), (dx, dy, dz))})`,
    the point in the part's own coordinates (mm) and the direction the wire leaves in (only the point: it leaves along +Z);
  - in mechanics/cad/connections.py, next to WIRES:
        WAYPOINTS = {"clip": {"at": (x, y, z), "on": "<placement>"}, "corner": (x, y, z)}
    a point on a placement (its own coordinates: it moves with it, over the motion cycle too) or in the machine frame;
    per wire `"via": ["clip", "corner"]` (in order), optional `"od"` (outer diameter, mm; else from the spec: mm² or AWG,
    "4x0.25 mm²" a cable), `"color"`, `"loop"` (mm of service loop a wire to a moving part keeps);
        HARNESS = {"slack": 0.1, "ends_mm": 10, "bend": 4}      (optional: the defaults)
  A wire is routed when both of its end ports have a location: straight out of each port (a lead of bend x its diameter, at
  least LEAD_MM), then through its waypoints. Wires passing the same two waypoints one after the other share that segment: a
  bundle, its diameter from theirs (bundle_d). Wire ids are W1, W2... in WIRES order, as in the electric scheme's table.

What it gives:
  - per wire: the route (corners rounded at the bend radius), its length, and the cut length = (route + loop) x (1 + slack)
    + ends_mm per end, rounded up to ROUND_MM;
  - problems, each {"rule", "text"}: `route` (a reference that does not resolve), `bend` (no room for the bend radius:
    bend x the bundle diameter), `clash` (the bundle goes through a part), `gap` (it does not fit through where a waypoint
    puts it), `stretch` (over the motion cycle the route gets longer than the rest route + loop).
  routes() needs only parts.json and the connections (the purchase list and the harness drawing use it); check() adds the
  part meshes (clash, gap): the viewer build runs it next to the overlap check (clearance.py), with the cycle it publishes,
  and writes build/harness/report.json (check.py rule «harness» reads it) and the page's wires.json.

  python3 harness.py <project> [--motion motion.json] [--groups groups.json]      exit 1 on a problem
"""
import argparse
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

try:
    import numpy as np
except ImportError:     # the platform's project container runs check.py on a plain python: gate() then reads the report only
    np = None

HERE = Path(__file__).resolve().parent


def _framework(name, where=HERE):
    """a sibling framework module, by its path: derive.py builds the model in its own process, and a project's module of
    the same name must not stand in for the framework's (device.py does the same)"""
    key = f'_mp_{name}'
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, where / f'{name}.py')
        sys.modules[key] = mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return sys.modules[key]


schemes = _framework('schemes')

OUT = 'build/harness/report.json'
REF = 'skill machine-project «Wires in 3D»'
DEFAULTS = {'slack': 0.10, 'ends_mm': 10.0, 'bend': 4.0}   # bend: fixed flexible PVC wire (IEC 60364-5-52); a flexing one wants 10
LEAD_MM = 5.0          # shortest straight lead out of a port
ROUND_MM = 5           # cut lengths rounded up to this
TOL = 0.3              # mm a bundle may press into a part: the mesh tolerance plus a wire squeezed against a wall
STEP = 1.0             # mm between the points of a route measured against the parts
STRETCH_TOL = 0.5      # mm
HAIRPIN = math.radians(170)   # a turn sharper than this is a corner of the route, not a bend
BEND_TOL = 0.02        # a bend this much under its radius still passes (how exactly a wire lies)
CYCLE_N = 96           # samples of the cycle for a wire to a moving part
CLASH_TIMES = 48       # of them, where its bundle is also measured against the parts (a 25 mm stroke: 0.5 mm a sample)
SWEEP_TIMES = 48       # of them, where a wire that stands still is measured against the moving parts that sweep its way
FRAMES = 48            # routes of a moving wire for the page
MESH_TOL = 0.1         # the overlap check's mesh tolerance: its mesh cache is shared
# a single PVC-insulated stranded wire, cross-section mm² -> outer diameter mm (hook-up wire, H05V-K class)
OD = [(0.08, 0.9), (0.14, 1.1), (0.25, 1.4), (0.35, 1.6), (0.5, 2.0), (0.75, 2.3), (1.0, 2.6), (1.5, 3.0), (2.5, 3.6),
      (4.0, 4.3), (6.0, 4.9), (10.0, 6.3), (16.0, 7.6), (25.0, 9.5)]
PACK = [1.0, 2.0, 2.155, 2.414, 2.701, 3.0, 3.0]   # n equal circles: the enclosing circle in diameters (exact up to 7)
SHEATH_MM = 0.6        # a cable's jacket wall
PALETTE = ['#1c7ed6', '#f08c00', '#2f9e44', '#ae3ec9', '#0ca678', '#e8590c', '#5c7cfa', '#c2255c', '#74b816', '#1098ad']


# ---------------------------------------------------------------- wire sizes

def _num(s):
    return float(s.replace(',', '.'))


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


LIMITS = {'slack': (0.0, False), 'ends_mm': (0.0, False), 'bend': (0.0, True), 'loop': (0.0, False), 'od': (0.0, True)}


def _bad_number(key, v):
    """why `v` is no good as `key` (None: it is): a number, never negative; bend and od above zero"""
    lo, strict = LIMITS[key]
    if not _is_num(v):
        return f'{key} must be a number, not {v!r}'
    if v < lo or (strict and v <= lo):
        return f'{key} must be {">" if strict else ">="} {lo:g}, not {v:g}'
    return None


def config(conns):
    """(HARNESS with the defaults, problems): a bad number is a `route` problem and its default is used"""
    raw = conns.get('HARNESS') or {}
    if not isinstance(raw, dict):
        return dict(DEFAULTS), [{'rule': 'route', 'text': f'HARNESS in {schemes.CONNECTIONS} must be a dict like {DEFAULTS}'}]
    cfg, bad = dict(DEFAULTS), []
    for k, v in raw.items():
        if k not in DEFAULTS:
            bad.append(f'HARNESS[{k!r}]: not a setting (the settings: {", ".join(DEFAULTS)})')
        elif _bad_number(k, v):
            bad.append(f'HARNESS: {_bad_number(k, v)}')
        else:
            cfg[k] = float(v)
    return cfg, [{'rule': 'route', 'text': t} for t in bad]


def od_of(w):
    """The outer diameter of a wire (mm): its `od`, else from its spec (mm², AWG; "4x0.25 mm²" = a 4-core cable), else None."""
    import re
    if w.get('od') is not None:
        return float(w['od'])
    s = str(w.get('spec') or '')
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*mm\s*(?:²|2|\^2)', s, re.I)
    if m:
        area = _num(m.group(1))
    else:
        m = re.search(r'(\d+)\s*AWG|AWG\s*(\d+)', s, re.I)
        if not m:
            return None
        d = 0.127 * 92 ** ((36 - int(m.group(1) or m.group(2))) / 39)
        area = math.pi / 4 * d * d
    d = next((o for a, o in OD if a >= area - 1e-9), None)
    if d is None:
        d = OD[-1][1] * math.sqrt(area / OD[-1][0])
    n = re.search(r'(\d+)\s*[x×]\s*\d', s)
    return bundle_d([d] * int(n.group(1))) + 2 * SHEATH_MM if n and int(n.group(1)) > 1 else d


def bundle_d(ods):
    """The diameter of a bundle of round wires (mm): never smaller than its thickest wire; n wires of one size take the
    enclosing circle of n equal circles (PACK), mixed sizes their root-mean-square size."""
    ods = [float(d) for d in ods]
    n = len(ods)
    if n <= 1:
        return ods[0] if ods else 0.0
    k = PACK[n - 1] if n <= len(PACK) else 1.2 * math.sqrt(n)
    return max(max(ods), math.sqrt(sum(d * d for d in ods) / n) * k)


def colours(wires):
    """{wire index: colour}: its `color`, else the scheme's colour of its net (supply red, 0 V black, ...), a signal net a
    palette colour of its own in WIRES order (ten signals before one repeats)"""
    sd = _framework('scheme_draw')
    out, signal = {}, {}
    for i, w in enumerate(wires):
        net = str(w.get('net') or w.get('from'))
        c = str(w['color']) if w.get('color') else sd.colour('electric', str(w.get('net') or ''))
        if not w.get('color') and c == '#3d3d3d':
            c = PALETTE[signal.setdefault(net, len(signal)) % len(PALETTE)]
        out[i] = c
    return out


# ---------------------------------------------------------------- the model

def placements(parts):
    """{placement name: (part, 4x4 matrix)} of parts.json's `parts` list."""
    out = {}
    for p in parts:
        if not isinstance(p, dict):
            continue
        for pl in p.get('placements') or [{'name': p.get('id'), 'loc': None}]:
            if isinstance(pl, dict) and pl.get('name'):
                M = np.array(pl['loc'], float).reshape(4, 4) if pl.get('loc') else np.eye(4)
                out[str(pl['name'])] = (p, M)
    return out


def _vec(v, n=3):
    try:
        a = np.array(v, float).reshape(-1)
    except (TypeError, ValueError):
        return None
    return a if a.shape == (n,) and np.isfinite(a).all() else None


def _port_at(p, port):
    """(point, direction) of a port in its part's own coordinates, or None (no location); raises ValueError on a bad one."""
    at = (p.get('ports_at') or {}).get(port)
    if at is None:
        return None
    pt = _vec(at)
    if pt is not None:
        return pt, np.array([0.0, 0.0, 1.0])
    if isinstance(at, (list, tuple)) and len(at) == 2:
        pt, d = _vec(at[0]), _vec(at[1])
        if pt is not None and d is not None and np.linalg.norm(d) > 1e-9:
            return pt, d / np.linalg.norm(d)
    raise ValueError('ports_at must be (x, y, z) or ((x, y, z), (dx, dy, dz))')


class Anchor:
    """A point of a route: on a placement (moves with it) or in the machine frame (on = None)."""

    def __init__(self, label, on, p, d=None, kind='waypoint'):
        self.label, self.on, self.p, self.d, self.kind = label, on, p, d, kind

    def at(self, place, poses=None):
        """(world point, world direction or None) with the placements' groups at poses {placement: 4x4} (None: at rest)."""
        M = place[self.on][1] if self.on else np.eye(4)
        if poses and self.on in poses:
            M = poses[self.on] @ M
        q = M[:3, :3] @ self.p + M[:3, 3]
        return q, (None if self.d is None else M[:3, :3] @ self.d)


def resolve(parts, conns):
    """-> (wires [{id, wire, ends: (Anchor, Anchor), via: [Anchor], od}], unrouted ids, problems). A wire is routed when both
    of its ends have a location; a reference that does not resolve is a `route` problem."""
    place = placements(parts)
    bad = []
    for n, (p, _) in place.items():
        pa = p.get('ports_at')
        if pa is None:
            continue
        if not isinstance(pa, dict):
            bad.append(f'{n}: ports_at must be {{"<port>": (x, y, z) or ((x, y, z), (dx, dy, dz))}}')
            continue
        for port in pa:
            if port not in (p.get('ports') or {}):
                bad.append(f'{n}: ports_at names port {port!r}, which is not in its ports ({", ".join(p.get("ports") or {}) or "none"})')
            else:
                try:
                    _port_at(p, port)
                except ValueError as e:
                    bad.append(f'{n}: ports_at[{port!r}]: {e}')
    bad = list(dict.fromkeys(bad))       # several placements of one part: said once
    wps, wp_bad = {}, []
    raw = conns.get('WAYPOINTS') or {}
    if not isinstance(raw, dict):
        wp_bad.append(f'WAYPOINTS in {schemes.CONNECTIONS} must be {{"<name>": (x, y, z) or {{"at": (x, y, z), "on": "<placement>"}}}}')
        raw = {}
    for name, v in raw.items():
        on, pt = (v.get('on'), _vec(v.get('at'))) if isinstance(v, dict) else (None, _vec(v))
        if pt is None:
            wp_bad.append(f'WAYPOINTS[{name!r}]: a point is (x, y, z) mm, or {{"at": (x, y, z), "on": "<placement>"}}')
        elif on is not None and str(on) not in place:
            wp_bad.append(f'WAYPOINTS[{name!r}]: on {on!r}, no such placement in the model')
        else:
            wps[str(name)] = Anchor(str(name), str(on) if on is not None else None, pt)
    out, unrouted = [], []
    for i, w in enumerate(conns.get('WIRES') or []):
        wid = f'W{i + 1}'
        ends, missing = [], False
        for end in (w['from'], w['to']):
            n, port = schemes.split(end)
            p = place.get(n, (None,))[0]
            try:
                pa = _port_at(p, port) if p is not None and port in (p.get('ports') or {}) else None
            except ValueError:
                pa = None                       # said above
            if pa is None:
                missing = True
            else:
                ends.append(Anchor(end, n, pa[0], pa[1], 'port'))
        via = w.get('via') or []
        if not isinstance(via, (list, tuple)):
            bad.append(f'{wid} ({w["from"]} → {w["to"]}): via must be a list of waypoint names')
            continue
        unknown = [str(v) for v in via if str(v) not in wps and str(v) not in raw]
        if unknown:
            bad.append(f'{wid} ({w["from"]} → {w["to"]}): via names {", ".join(map(repr, unknown))}, not in WAYPOINTS '
                       f'({", ".join(map(repr, wps)) or "none"})')
            continue
        if missing:
            if via:
                bad.append(f'{wid} ({w["from"]} → {w["to"]}) has a route (via) but its ports have no location: give both ports '
                           f'ports_at in their part files')
            unrouted.append(wid)
            continue
        nums = [_bad_number(k, w[k]) for k in ('od', 'loop', 'bend') if w.get(k) is not None]
        if any(nums):
            bad.append(f'{wid} ({w["from"]} → {w["to"]}): ' + '; '.join(x for x in nums if x))
            continue
        od = od_of(w)
        if od is None or od <= 0:
            bad.append(f'{wid} ({w["from"]} → {w["to"]}): no outer diameter: give "od" (mm) or a spec in mm² / AWG')
            continue
        if not all(str(v) in wps for v in via):
            continue                            # a broken waypoint: said in wp_bad
        out.append({'id': wid, 'wire': w, 'ends': tuple(ends), 'via': [wps[str(v)] for v in via], 'od': od})
    return out, unrouted, [{'rule': 'route', 'text': t} for t in bad + wp_bad]


# ---------------------------------------------------------------- geometry

def _fillet(V, need):
    """The route through vertices V (n, 3) with its corners rounded: need[i] = the bend radius wanted at vertex i (the ends
    take none). Where two corners leave too little room on the segment between them, both radii shrink in proportion.
    -> (points [(xyz, segment index)], length, radii, turn angles)."""
    n = len(V)
    seg = V[1:] - V[:-1]
    L = np.linalg.norm(seg, axis=1)
    u = seg / np.maximum(L, 1e-12)[:, None]
    th = np.zeros(n)
    for i in range(1, n - 1):
        th[i] = math.acos(float(np.clip(u[i - 1] @ u[i], -1.0, 1.0)))
    # a hairpin (a turn past HAIRPIN) cannot be rounded inside its segments: it stays a sharp corner, so the route still
    # reaches its point (and is measured there), and fails the bend rule with no room at all
    hair = th > HAIRPIN
    tan = np.where((th > 1e-6) & ~hair, np.tan(np.minimum(th, math.pi - 1e-6) / 2), 0.0)
    want = need * tan
    s = np.ones(n - 1)
    for k in range(n - 1):
        tot = (want[k] if k > 0 else 0.0) + (want[k + 1] if k + 1 < n - 1 else 0.0)
        if tot > L[k]:
            s[k] = L[k] / tot
    R = np.zeros(n)
    for i in range(1, n - 1):
        R[i] = 0.0 if hair[i] else need[i] * min(s[i - 1], s[i]) if th[i] > 1e-6 else need[i]
    t = R * tan
    pts = [(V[0], 0)]
    for i in range(1, n - 1):
        if th[i] <= 1e-6 or R[i] <= 1e-9:
            pts.append((V[i], i))
            continue
        a = V[i] - u[i - 1] * t[i]
        w = u[i] - (u[i] @ u[i - 1]) * u[i - 1]
        w /= max(np.linalg.norm(w), 1e-12)
        c = a + R[i] * w
        m = max(2, int(math.ceil(th[i] / (math.pi / 18))))
        for k in range(m + 1):
            f = th[i] * k / m
            pts.append((c + R[i] * (-math.cos(f) * w + math.sin(f) * u[i - 1]), i - 1 if k < m / 2 else i))
    pts.append((V[-1], n - 2))
    length = float(L.sum() - 2 * t.sum() + (R * th).sum())
    return pts, length, R, th


class Route:
    """One wire's vertices (ports, leads, waypoints) and what each segment carries."""

    def __init__(self, r, place, bend):
        self.r, self.place = r, place
        a, b = r['ends']
        self.lead = max(LEAD_MM, bend * r['od'])
        self.labels = [a.label, a.label + ' lead'] + [v.label for v in r['via']] + [b.label + ' lead', b.label]
        self.anchors = [a, a] + list(r['via']) + [b, b]
        n = len(self.labels)
        # a segment between two waypoints may be shared (a bundle); the others carry this wire alone
        self.keys = [frozenset((self.labels[k], self.labels[k + 1])) if 2 <= k and k + 1 <= n - 3 else (r['id'], k)
                     for k in range(n - 1)]

    def vertices(self, poses=None):
        out = []
        for k, an in enumerate(self.anchors):
            q, d = an.at(self.place, poses)
            if k == 1:
                q = q + d * self.lead
            elif k == len(self.anchors) - 2:
                q = q + d * self.lead
            out.append(q)
        return np.array(out)

    def needs(self, D, bend):
        """the bend radius wanted at each vertex: bend x the thickest bundle on its two segments"""
        n = len(self.labels)
        need = np.zeros(n)
        for i in range(1, n - 1):
            need[i] = bend * max(D[self.keys[i - 1]], D[self.keys[i]])
        return need


def _frame(u):
    ref = np.array([0.0, 0.0, 1.0]) if abs(u[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    e1 = np.cross(u, ref)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(u, e1)


def _offsets(route, V, D, members, od):
    """Each segment's offset of this wire from the bundle's centre line (its place in the bundle's cross section)."""
    out = []
    for k, key in enumerate(route.keys):
        ws = members.get(key) or [route.r['id']]
        if len(ws) < 2:
            out.append(np.zeros(3))
            continue
        u = V[k + 1] - V[k]
        u = u / max(np.linalg.norm(u), 1e-12)
        e1, e2 = _frame(u)
        j, rho = ws.index(route.r['id']), max(0.0, (D[key] - od) / 2)
        a = 2 * math.pi * j / len(ws)
        out.append(rho * (math.cos(a) * e1 + math.sin(a) * e2))
    return out


def _drawn(pts, offs):
    """the route's points moved to this wire's place in its bundles, an arc blending from one segment's place to the next"""
    out = []
    for q, k in pts:
        out.append((np.asarray(q) + offs[min(k, len(offs) - 1)]).round(2).tolist())
    return out


def _cycle_ctx(parts, place, cycle, groups):
    """{times, poses {time: {placement: 4x4}}, moving {placement: group}, groups} of the placements the cycle moves; None
    without a cycle or with nothing moving."""
    cg = (cycle or {}).get('groups') or {}
    if not cg:
        return None
    cc = _framework('cycle_clearance', HERE.parent.parent / 'sim' / 'scripts')
    names = list(place)
    pids = [place[n][0].get('id') or place[n][0].get('name') for n in names]
    own = {**cc.motion_groups(parts), **{str(k): v for k, v in (groups or {}).items()}}
    grp = dict(zip(names, cc.assign_groups(names, pids, own, cycle.get('node_groups') or {}, cg)))
    moving = {n: g for n, g in grp.items() if g != 'static'}
    if not moving:
        return None
    period = float(cycle.get('period_s') or 0) or 1.0
    ts = {round(period * k / CYCLE_N, 6) for k in range(CYCLE_N)}
    for G in cg.values():
        for s in G.get('segments') or []:
            ts.update(round(float(x), 6) for x in s[:2] if 0 <= float(x) <= period)
    ts = sorted(ts)
    poses = {}
    for g in set(moving.values()):
        Ms = cc.group_poses(cg, g, np.array(ts))
        for k, t in enumerate(ts):
            poses.setdefault(t, {})
            for n in (n for n, gg in moving.items() if gg == g):
                poses[t][n] = Ms[k]
    return {'times': ts, 'poses': poses, 'moving': moving, 'groups': grp}


def routes(parts, conns, cycle=None, groups=None):
    """parts = build/model/parts.json `parts`, conns = load_connections() -> (wires, problems). A wire: {id, from, to, net,
    spec, od_mm, color, length_mm, loop_mm, cut_mm, moving, vertices [{label, at}], path (drawn points), bundles,
    frames? {times, paths} (a moving wire, for the page)}. cycle = a motion.json `cycle` block: the stretch rule."""
    wires, problems, _ = _routes(parts, conns, cycle, groups)
    return wires, problems


def _routes(parts, conns, cycle, groups):
    """routes() and what check() measures with: {route, members, D, ctx, cfg}"""
    cfg, problems = config(conns)
    bend, slack, ends = cfg['bend'], cfg['slack'], cfg['ends_mm']
    place = placements(parts)
    rs, unrouted, more = resolve(parts, conns)
    problems += more
    col = colours(conns.get('WIRES') or [])
    route = {r['id']: Route(r, place, float(r['wire'].get('bend', bend))) for r in rs}
    members = {}
    for wid, ro in route.items():
        for key in ro.keys:
            ws = members.setdefault(key, [])
            if wid not in ws:       # a wire that passes a segment there and back is one wire in its bundle
                ws.append(wid)
    od = {r['id']: r['od'] for r in rs}
    D = {key: bundle_d([od[w] for w in ws]) for key, ws in members.items()}
    ctx = _cycle_ctx(parts, place, cycle, groups) if rs else None
    out = []
    for r in rs:
        ro, w, wid = route[r['id']], r['wire'], r['id']
        name = f'{wid} ({w["from"]} → {w["to"]})'
        need = ro.needs(D, float(w.get('bend', bend)))
        V = ro.vertices()
        L = np.linalg.norm(V[1:] - V[:-1], axis=1)
        z = [k for k in range(len(L)) if L[k] < 1e-6]
        if z:
            problems.append({'rule': 'route', 'text': f'{name}: {ro.labels[z[0]]} and {ro.labels[z[0] + 1]} are one point'})
            continue
        pts, length, R, th = _fillet(V, need)
        tight = [i for i in range(1, len(V) - 1) if th[i] > 1e-3 and R[i] < need[i] * (1 - BEND_TOL)]
        for i in tight:
            key = ro.keys[i] if D[ro.keys[i]] >= D[ro.keys[i - 1]] else ro.keys[i - 1]
            problems.append({'rule': 'bend', 'text':
                             f'{name}: the bend at {ro.labels[i]} ({math.degrees(th[i]):.0f}°) has room for a radius of '
                             f'{R[i]:.1f} mm, it needs {need[i]:.1f} mm ({float(w.get("bend", bend)):g} x Ø {D[key]:.1f} mm): '
                             'move the waypoints apart or soften the turn'})
        loop = float(w.get('loop') or 0)
        cut = int(math.ceil(((length + loop) * (1 + slack) + 2 * ends) / ROUND_MM - 1e-9) * ROUND_MM)   # 220.000001 is 220
        moving = bool(ctx) and any(a.on in ctx['moving'] for a in ro.anchors)
        rec = {'id': wid, 'from': w['from'], 'to': w['to'], 'net': str(w.get('net') or ''), 'spec': str(w.get('spec') or ''),
               'od_mm': round(r['od'], 2), 'color': col[int(wid[1:]) - 1], 'length_mm': round(length, 1), 'loop_mm': loop, 'cut_mm': cut,
               'moving': moving, 'vertices': [{'label': lb, 'at': q.round(2).tolist()} for lb, q in zip(ro.labels, V)],
               'bundles': [{'key': sorted(k) if isinstance(k, frozenset) else None, 'd_mm': round(D[k], 2),
                            'wires': members.get(k, [wid])} for k in ro.keys],
               'path': _drawn(pts, _offsets(ro, V, D, members, r['od']))}
        if moving:
            worst, frames = None, []
            pick = set(ctx['times'][::max(1, len(ctx['times']) // FRAMES)])
            for t in ctx['times']:
                Vt = ro.vertices(ctx['poses'][t])
                pt, Lt, Rt, tht = _fillet(Vt, need)
                if worst is None or Lt > worst[1]:
                    worst = (t, Lt)
                if t in pick:
                    frames.append((t, _drawn(pt, _offsets(ro, Vt, D, members, r['od']))))
                if not tight and any(tht[i] > 1e-3 and Rt[i] < need[i] * (1 - BEND_TOL) for i in range(1, len(Vt) - 1)):
                    i = next(i for i in range(1, len(Vt) - 1) if tht[i] > 1e-3 and Rt[i] < need[i] * (1 - BEND_TOL))
                    problems.append({'rule': 'bend', 'text': f'{name}: at t {t:.3g} s of the cycle the bend at {ro.labels[i]} has '
                                     f'room for a radius of {Rt[i]:.1f} mm, it needs {need[i]:.1f} mm'})
                    tight = [i]
            rec['max_mm'] = round(worst[1], 1)
            rec['frames'] = {'times': [f[0] for f in frames], 'paths': [f[1] for f in frames]}
            if worst[1] > length + loop + STRETCH_TOL:
                more = int(math.ceil(worst[1] - length))
                problems.append({'rule': 'stretch', 'text':
                                 f'{name} is stretched: at t {worst[0]:.3g} s of the cycle its route is {worst[1]:.0f} mm, the wire '
                                 f'has {length + loop:.0f} mm ({length:.0f} at rest + loop {loop:g}): give it "loop": {more} or '
                                 'more, or route it along the moving part'})
        out.append(rec)
    for wid in unrouted:
        out.append({'id': wid, 'unrouted': True})
    return out, problems, {'route': route, 'members': members, 'D': D, 'ctx': ctx, 'cfg': cfg}


# ---------------------------------------------------------------- the parts in the way

def _winding(V, F, pts):
    """|generalised winding number| of a triangle soup around each point: about 1 inside a closed surface, 0 outside
    (robust to the unshared vertices and cracks of a tessellated STEP)."""
    out = np.zeros(len(pts))
    A0, B0, C0 = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    step = max(1, 2_000_000 // max(1, len(F)))
    for s in range(0, len(pts), step):
        p = pts[s:s + step, None, :]
        a, b, c = A0[None] - p, B0[None] - p, C0[None] - p
        la, lb, lc = (np.linalg.norm(x, axis=2) for x in (a, b, c))
        det = np.einsum('ijk,ijk->ij', a, np.cross(b, c))
        den = la * lb * lc + np.einsum('ijk,ijk->ij', a, b) * lc + np.einsum('ijk,ijk->ij', b, c) * la + \
            np.einsum('ijk,ijk->ij', c, a) * lb
        out[s:s + step] = np.abs(2 * np.arctan2(det, den).sum(1)) / (4 * math.pi)
    return out


def _samples(route_pts, dia):
    """points every STEP mm along a wire's centre line, dia = the bundle diameter per segment: (xyz (m, 3), radius (m,),
    segment index (m,), arc length (m,))"""
    P = np.array([q for q, _ in route_pts])
    K = [k for _, k in route_pts]
    xs, rs, ks, ss, s0 = [], [], [], [], 0.0
    for j in range(len(P) - 1):
        d = np.linalg.norm(P[j + 1] - P[j])
        m = max(1, int(math.ceil(d / STEP)))
        for f in np.arange(m) / m:
            xs.append(P[j] + (P[j + 1] - P[j]) * f)
            rs.append(dia[K[j]] / 2)
            ks.append(K[j])
            ss.append(s0 + d * f)
        s0 += d
    xs.append(P[-1]); rs.append(dia[K[-1]] / 2); ks.append(K[-1]); ss.append(s0)
    return np.array(xs), np.array(rs), np.array(ks), np.array(ss)


def check(root, parts, conns, cycle=None, groups=None):
    """routes() + every routed bundle against the part meshes (clash, gap): at rest; over the cycle, with the parts where
    the cycle puts them, a wire to a moving part at CLASH_TIMES against every part, and any other wire whose bundle a moving
    part's sweep reaches at SWEEP_TIMES against the moving parts. -> (wires, problems)"""
    import trimesh
    cc = _framework('cycle_clearance', HERE.parent.parent / 'sim' / 'scripts')
    wires, problems, st = _routes(parts, conns, cycle, groups)
    route, members, D, ctx, cfg = st['route'], st['members'], st['D'], st['ctx'], st['cfg']
    if not route:
        return wires, problems
    root = Path(root)
    model = root / schemes.PARTS
    names, verts, tris, _, _, _ = cc.load_model(model, cc.mesh_cache(model, root / 'build' / 'clearance'), MESH_TOL)
    product = {n for n, (p, _) in placements(parts).items() if p.get('product')}
    lo = np.array([v.min(0) for v in verts]).reshape(-1, 3)
    hi = np.array([v.max(0) for v in verts]).reshape(-1, 3)
    meshes = {}
    hits = {}

    def mesh(i, M):
        key = (i, None if M is None else M.round(6).tobytes())
        if key not in meshes:
            v = verts[i] if M is None else verts[i] @ M[:3, :3].T + M[:3, 3]
            meshes[key] = (v, trimesh.Trimesh(v, tris[i], process=False))
        return meshes[key]

    def measure(wid, t, only=None):
        ro = route[wid]
        poses = ctx['poses'][t] if t is not None else None
        V = ro.vertices(poses)
        pts, _, _, _ = _fillet(V, ro.needs(D, float(ro.r['wire'].get('bend', cfg['bend']))))
        X, rad, ks, ss = _samples(pts, [D[k] for k in ro.keys])
        total = ss[-1]
        ends = (ro.r['ends'][0].on, ro.r['ends'][1].on)
        for i, n in enumerate(names):
            if n in product or (only is not None and n not in only):
                continue
            M = poses.get(n) if poses else None
            l, h = (lo[i], hi[i]) if M is None else _box(lo[i], hi[i], M)
            near = np.all(X + rad[:, None] + TOL >= l, 1) & np.all(X - rad[:, None] - TOL <= h, 1)
            # a wire leaves its own part at its port: the lead (and a bundle's width past it) is not measured against it
            zone = ro.lead + 2 * rad.max()
            if n == ends[0]:
                near &= ss > zone
            if n == ends[1]:
                near &= ss < total - zone
            if not near.any():
                continue
            v, tm = mesh(i, M)
            idx = np.nonzero(near)[0]
            _, dist, _ = trimesh.proximity.closest_point(tm, X[idx])
            # inside or out matters only near the surface: a route into a part crosses its surface, and its points there,
            # STEP apart, are what decide (the winding number costs points x triangles)
            close = dist < rad[idx] + TOL + STEP
            inside = np.zeros(len(idx), bool)
            if close.any():
                inside[close] = _winding(v, tris[i], X[idx[close]]) > 0.5
            pen = np.where(inside, rad[idx] + dist, rad[idx] - dist)
            for j in np.nonzero(pen > TOL)[0]:
                g = idx[j]
                k = int(ks[g])
                key = (ro.keys[k], n)
                h = hits.get(key)    # a hit at rest speaks for the pair: a time of the cycle only when it is the only one
                if h is None or (t is None) > (h['t'] is None) or ((t is None) == (h['t'] is None) and pen[j] > h['pen']):
                    hits[key] = {'pen': float(pen[j]), 'at': X[g], 'k': k, 'wid': wid, 't': t, 'V': V, 'd': D[ro.keys[k]],
                                 'labels': ro.labels}

    swept = {}
    if ctx:      # where each moving part goes over the cycle (its boxes at the samples)
        at = {n: i for i, n in enumerate(names)}
        for n in ctx['moving']:
            if n in at:
                bs = [_box(lo[at[n]], hi[at[n]], ctx['poses'][t][n]) for t in ctx['times']]
                swept[n] = (np.min([b[0] for b in bs], 0), np.max([b[1] for b in bs], 0))
    for wid, ro in route.items():
        measure(wid, None)
        if not ctx:
            continue
        if any(a.on in ctx['moving'] for a in ro.anchors):
            for t in ctx['times'][::max(1, len(ctx['times']) // CLASH_TIMES)]:
                measure(wid, t)
            continue
        V = ro.vertices()     # a wire that stands still: the moving parts whose sweep reaches its bundle cross it?
        r = max(D[k] for k in ro.keys) / 2 + TOL
        wl, wh = V.min(0) - r, V.max(0) + r
        reach = {n for n, (sl, sh) in swept.items() if np.all(sl <= wh) and np.all(wl <= sh)}
        if reach:
            for t in ctx['times'][::max(1, len(ctx['times']) // SWEEP_TIMES)]:
                measure(wid, t, reach)
    for (key, part), h in sorted(hits.items(), key=lambda kv: -kv[1]['pen']):
        ws = members.get(key, [h['wid']])
        who = (f'bundle {", ".join(ws)}' if len(ws) > 1 else ws[0]) + f' (Ø {h["d"]:.1f} mm)'
        when = f' at t {h["t"]:.3g} s of the cycle' if h['t'] is not None else ''
        at = ', '.join(f'{x:.1f}' for x in h['at'])
        V, lb, k = h['V'], h['labels'], h['k']
        wp = [i for i in (k, k + 1) if 2 <= i <= len(lb) - 3 and np.linalg.norm(V[i] - h['at']) <= h['d'] + 2]
        if wp:
            problems.append({'rule': 'gap', 'text': f'{who} does not fit through at waypoint {lb[wp[0]]}{when}: it goes '
                             f'{h["pen"]:.1f} mm into {part} at ({at}) mm: widen the opening or move the waypoint'})
        else:
            problems.append({'rule': 'clash', 'text': f'{who} goes through {part} between {lb[k]} and {lb[k + 1]}{when}: '
                             f'{h["pen"]:.1f} mm deep at ({at}) mm: add a waypoint around it'})
    return wires, problems


def _box(lo, hi, M):
    c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    w = c @ M[:3, :3].T + M[:3, 3]
    return w.min(0), w.max(0)


# ---------------------------------------------------------------- the report

def has_routes(parts, conns):
    return any(isinstance(p, dict) and p.get('ports_at') for p in parts or []) or bool(conns.get('WAYPOINTS')) or \
        any(w.get('via') for w in conns.get('WIRES') or [])


def load(root):
    """(parts list, conns) of a built project, or (None, None)."""
    try:
        parts = json.loads((Path(root) / schemes.PARTS).read_text(encoding='utf-8')).get('parts') or []
    except (OSError, ValueError, AttributeError):
        return None, None
    if not (Path(root) / schemes.CONNECTIONS).is_file():
        return parts, {'WIRES': [], 'HOSES': []}
    return parts, schemes.load_connections(root)


def inputs(root):
    """what a report was made from: the built model, the connections, the cycle the site build publishes (build/motion.json,
    the framework's place for it) and this code (by content: a kit export rewrites parts.json unchanged, so its time says
    nothing)"""
    import hashlib
    h = hashlib.sha256()
    for f in (Path(root) / schemes.PARTS, Path(root) / schemes.CONNECTIONS, Path(root) / 'build' / 'motion.json',
              Path(__file__), HERE.parent.parent / 'sim' / 'scripts' / 'cycle_clearance.py'):
        try:
            h.update(f.read_bytes())
        except OSError:
            h.update(b'-')
    return h.hexdigest()[:16]


def ports_located(root):
    """does the built model give any port a location (parts.json alone: no project code runs)"""
    try:
        parts = json.loads((Path(root) / schemes.PARTS).read_text(encoding='utf-8')).get('parts') or []
    except (OSError, ValueError, AttributeError):
        return False
    return any(isinstance(p, dict) and p.get('ports_at') for p in parts)


def run(root, cycle=None, groups=None):
    """The report of a built project, written to build/harness/report.json, or None when it routes no wire (a model
    without port locations whose connections.py does not load routes none either: rule «schemes» says why)."""
    t0 = time.time()
    root = Path(root).resolve()
    try:
        parts, conns = load(root)
    except ValueError:
        if not ports_located(root):
            (root / OUT).unlink(missing_ok=True)
            return None
        raise
    if parts is None or not has_routes(parts, conns):
        (root / OUT).unlink(missing_ok=True)
        return None
    wires, problems = check(root, parts, conns, cycle, groups)
    rep = {'inputs': inputs(root), 'cycle': bool((cycle or {}).get('groups')), 'wires': wires, 'problems': problems,
           'seconds': round(time.time() - t0, 1)}
    f = root / OUT
    f.parent.mkdir(parents=True, exist_ok=True)
    f.with_suffix('.tmp').write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding='utf-8')
    f.with_suffix('.tmp').replace(f)
    return rep


def site(rep):
    """What the model page draws (wires.json): the routed wires as tubes, a moving one's routes over the cycle."""
    keep = ('id', 'from', 'to', 'net', 'spec', 'od_mm', 'color', 'cut_mm', 'path', 'frames')
    return {'wires': [{k: w[k] for k in keep if k in w} for w in rep['wires'] if not w.get('unrouted')],
            'problems': [p['text'] for p in rep['problems']]}


def lines(rep):
    routed = [w for w in rep['wires'] if not w.get('unrouted')]
    out = [f'harness: {len(routed)} wire(s) routed, cut {sum(w["cut_mm"] for w in routed) / 1000:.2f} m in all, '
           f'{rep["seconds"]:g} s']
    for p in rep['problems'][:8]:
        out.append(f'WIRE  {p["rule"]}: {p["text"]}')
    if len(rep['problems']) > 8:
        out.append(f'... and {len(rep["problems"]) - 8} more in {OUT}')
    return out


def gate(root):
    """check.py rule «harness»: silent for a project that routes no wire."""
    root = Path(root)
    try:
        parts = json.loads((root / schemes.PARTS).read_text(encoding='utf-8')).get('parts') or []
        conns = schemes.load_connections(root, timeout=30) if (root / schemes.CONNECTIONS).is_file() else {'WIRES': []}
    except (OSError, ValueError, AttributeError):
        return []                   # no model yet, or connections.py does not load: rule «schemes» says why
    if not has_routes(parts, conns):
        return []
    if schemes.model_stale(root):
        return [('WARN', 'harness', f'the model is rebuilt after this turn ({schemes.PARTS} is older than the CAD sources): '
                 'the wires are checked then')]
    try:
        wires, problems = routes(parts, conns) if np is not None else (None, [])
    except Exception as e:   # noqa: BLE001 - a wiring the check cannot read is a failed check, never a silent one
        return [('FAIL', 'harness', f'the wires check failed: {type(e).__name__}: {e} ({REF})'[:600])]
    try:
        rep = json.loads((root / OUT).read_text(encoding='utf-8'))
        fresh = rep.get('inputs') == inputs(root)
    except (OSError, ValueError, AttributeError):
        fresh = False
    if fresh:
        problems = rep.get('problems') or []
        if rep.get('error'):
            problems = [{'rule': 'error', 'text': f'the wires check did not run in the last build: {rep["error"]}'}] + problems
    if problems:
        return [('FAIL', 'harness', '; '.join(f'[{p["rule"]}] {p["text"]}' for p in problems) + f' ({REF})')]
    if wires is None:
        wires = rep.get('wires') or [] if fresh else []
    routed = [w for w in wires if not w.get('unrouted')]
    if not fresh:
        return [('WARN', 'harness', f'{OUT} is not of this model: the wires are checked by the next build') if np is None else
                ('WARN', 'harness', f'{len(routed)} wire(s) routed, lengths and bends fine; {OUT} is not of this model: '
                 'the clearance to the parts and the motion are checked by the next build')]
    unrouted = [w['id'] for w in wires if w.get('unrouted')]
    return [('PASS', 'harness', f'{len(routed)} wire(s) routed, cut {sum(w["cut_mm"] for w in routed) / 1000:.2f} m: clear of the '
             'parts, through the gaps, bends and motion fine' + (f'; not routed (ports without ports_at): {", ".join(unrouted)}'
                                                                 if unrouted else ''))]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('project')
    ap.add_argument('--motion', type=Path, help='motion.json with a `cycle` block')
    ap.add_argument('--groups', type=Path, help='{placement name: group} (a machine\'s motion_groups.json)')
    a = ap.parse_args(argv)
    cyc = json.loads(a.motion.read_text()) if a.motion else None
    rep = run(a.project, cyc.get('cycle', cyc) if cyc else None, json.loads(a.groups.read_text()) if a.groups else None)
    if rep is None:
        print('harness: no wire is routed (no ports_at / WAYPOINTS / via in the model)')
        return 0
    print('\n'.join(lines(rep)))
    return 1 if rep['problems'] else 0


if __name__ == '__main__':
    sys.exit(main())
