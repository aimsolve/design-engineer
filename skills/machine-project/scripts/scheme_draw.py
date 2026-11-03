"""The machine schemes as drawings: the electric scheme with its elements as symbols and blocks and its wires as
lines, the pneumatic one in ISO 1219 symbols, laid out by ELK (Eclipse Layout Kernel: vendor/elk.bundled.js, run by elk_layout.cjs
on node) from the model schemes.py reads: the parts' ports and WIRES / HOSES. Nothing here knows a project: an element's symbol comes
from its ports and names (or its part's field `symbol`), its place from the layout.

Electric: a net named as a supply ("+24 V", "0 V", "GND", "3,3 V") on 3 ports or more is drawn as a supply / ground mark at each port,
not as wires; a net between two controllers (or on 5 ports or more) as a named flag at each port; every other net as lines.
Elements are put in columns by role: supply, inputs (buttons, sensors), controllers, modules (drivers), actuators (motors, coils).
Pneumatic: the air flows left to right from the supply (the element whose free port is fed from a compressor), hoses numbered H1...

  pages(kind, parts, conns, other, title, lang) -> [Page]   (one page per A3 sheet, larger for a part that does not fit one;
                                                       lang: the project's, for the texts on the sheet, text_i18n.py)
  paint(pages, pdf=None, svg=None, extra=None)         the PDF sheets (then `extra`: the connection table) and one SVG of all
                                                       the drawing sheets (the model page shows it)
"""
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import text_i18n as T  # noqa: E402
import words_i18n as W  # noqa: E402

PT = 25.4 / 72                                  # mm per point
SHEETS = [(420, 297), (594, 420), (841, 594), (1189, 841)]      # A3, A2, A1, A0 landscape, mm
MARGIN, TB_W, TB_H, GAP = 10, 185, 16, 10
F_PIN, F_TITLE, F_SUB, F_NET = 5.5, 6.5, 5.0, 5.0             # pt
INK, GREY, PAPER, BOXFILL = '#1a1a1a', '#6b6b6b', '#ffffff', '#f4f7fb'
LW, LW_WIRE = 0.8, 0.7                         # pt

# ---------------------------------------------------------------- text

_FONTS = {}


def _fp(bold):
    if bold not in _FONTS:
        from matplotlib.font_manager import FontProperties
        _FONTS[bold] = FontProperties(family='DejaVu Sans', weight='bold' if bold else 'normal')
    return _FONTS[bold]


@lru_cache(maxsize=None)
def tw(s, size, bold=False):
    """Width of a text in mm."""
    if not s.strip():
        return 0.0
    from matplotlib.textpath import TextPath
    return TextPath((0, 0), s, size=size, prop=_fp(bold)).get_extents().width * PT


def th(size, lines=1):
    return size * PT * 1.25 * lines


def wrap(s, n):
    """A title on at most two lines of about n characters."""
    import textwrap
    ls = textwrap.wrap(str(s), n) or ['']
    return ls if len(ls) <= 2 else [ls[0], cut(' '.join(ls[1:]), n)]


def cut(s, n):
    s = str(s or '')
    return s if len(s) <= n else s[:n - 1] + '…'


# ---------------------------------------------------------------- what an element is

PIN = re.compile(r'^(?:GPIO|IO|GP|P[A-H]|D|A)\d+$', re.I)
VOLT = W.alt('volt')
SUPPLY = re.compile(rf'^\s*(?:[+\-−]?\s*\d+(?:[.,]\d+)?\s*(?:{VOLT})\b|{W.alt("supply_net")}|{W.alt("ground_net")})', re.I)
GROUND = re.compile(rf'^\s*(?:0\s*(?:{VOLT})\b|{W.alt("ground_net")})', re.I)
MAINS = re.compile(r'^\s*(?:L\d?|N|PE|~\s*\d+.*)\s*$')
ROLE = [   # (tier, names): 0 supply, 1 input, 3 module (the default), 4 actuator; 2 = a controller (by its pins)
    (0, re.compile(W.alt('role_supply'), re.I)),
    (1, re.compile(W.alt('role_input'), re.I)),
    (4, re.compile(W.alt('role_actuator'), re.I)),
]
MODULE = re.compile(W.alt('role_module'), re.I)
SYMBOLS = ('box', 'motor', 'coil', 'button', 'button_nc', 'breaker', 'battery',
           'source', 'frl', 'filter', 'regulator', 'valve_2_2', 'valve_3_2', 'valve_5_2', 'ejector', 'cup', 'cylinder', 'tank',
           'silencer', 'fitting', 'tee')
TWO_TERMINAL = ('coil', 'button', 'button_nc', 'breaker', 'battery')
VALVE_PORT = {'1': 'P', '2': 'B', '4': 'A', '3': 'S', '5': 'R', 'EA': 'R', 'EB': 'S'}


def names(n, p):
    card = p.get('card') if isinstance(p.get('card'), dict) else {}
    return ' '.join(str(x) for x in (n, p.get('id'), p.get('title'), card.get('name'), p.get('desc'), p.get('code')) if x)


def display(n, p):
    """(title, sub): the name a person knows the element by, then its id and code."""
    card = p.get('card') if isinstance(p.get('card'), dict) else {}
    t = str(p.get('title') or card.get('name') or p.get('desc') or p.get('name') or n)
    sub = ' · '.join(x for x in (n, str(p.get('code') or '')) if x and x.lower() not in t.lower())
    return cut(t, 44), cut(sub, 50)


def is_mcu(p):
    sim = p.get('sim') if isinstance(p.get('sim'), dict) else {}
    return sim.get('model') in ('esp32', 'avr', 'rp2040', 'stm32') or sum(bool(PIN.match(k)) for k in p.get('ports') or {}) >= 6


def tier(n, p):
    if is_mcu(p):
        return 2
    if p.get('kind') == 'motors':
        return 4
    full, short = names(n, p), ' '.join(display(n, p))
    if ROLE[0][1].search(full):
        return 0
    if MODULE.search(short):
        return 3
    for t in (short, full):
        for k, rx in ROLE[1:]:
            if rx.search(t):
                return k
    return 3


def e_symbol(n, p, used):
    """The electric symbol of an element by the ports it shows on this sheet."""
    if p.get('symbol') in SYMBOLS:
        return p['symbol']
    t, ports = names(n, p).lower(), p.get('ports') or {}
    if used and all(k.lower().startswith('coil') for k in used):
        return 'coil'
    if (p.get('kind') == 'motors' or re.search(W.alt('motor'), t)) and len(used) <= 6:
        return 'motor'
    if len(used) == 2:
        if re.search(W.alt('button'), t):
            nc = any(re.search(W.alt('normally_closed'), str(v), re.I) for v in ports.values())
            return 'button_nc' if nc else 'button'
        if re.search(W.alt('breaker'), t):
            return 'breaker'
        if re.search(W.alt('battery'), t):
            return 'battery'
    return 'box'


def p_symbol(n, p):
    if p.get('symbol') in SYMBOLS:
        return p['symbol']
    t = names(n, p).lower()
    ports = {VALVE_PORT.get(k, k) for k in p.get('ports') or {} if not k.lower().startswith('coil')}
    if re.search(W.alt('valve'), t) and 'P' in ports and 'A' in ports and not re.search(W.alt('glue'), t):
        return 'valve_5_2' if 'B' in ports else 'valve_3_2' if 'R' in ports or 'S' in ports else 'valve_2_2'
    for sym in ('frl', 'filter', 'regulator', 'ejector', 'cup', 'silencer', 'tee', 'fitting', 'tank', 'cylinder', 'source'):
        rx = W.alt('sym_' + sym)
        if re.search(rx, t):
            return sym
    if {'open', 'close'} <= {k.lower() for k in ports}:        # an air-operated valve opened / closed by two ports
        return 'cylinder'
    return 'box'


# ---------------------------------------------------------------- nets

class Net:
    def __init__(self, ends):
        self.ends, self.wires, self.name, self.cls, self.no = ends, [], '', 'wire', 0


def nets_of(conns):
    """Ports joined by connections, as nets (a port wired to two places is one net)."""
    up = {}

    def find(a):
        while up.setdefault(a, a) != a:
            up[a] = up[up[a]]
            a = up[a]
        return a
    for c in conns:
        up[find(c['from'])] = find(c['to'])
    groups = defaultdict(list)
    for a in list(up):
        groups[find(a)].append(a)
    order = {}
    for i, c in enumerate(conns):
        for e in (c['from'], c['to']):
            order.setdefault(e, i)
    nets, of = [], {}
    for ends in groups.values():
        net = Net(sorted(ends, key=lambda e: order[e]))
        nets.append(net)
        for e in ends:
            of[e] = net
    for i, c in enumerate(conns):
        of[c['from']].wires.append(i)
    for net in nets:
        named = [str(conns[i].get('net') or '') for i in net.wires if conns[i].get('net')]
        net.name = max(set(named), key=named.count) if named else ''
    nets.sort(key=lambda n: min(order[e] for e in n.ends))
    return nets, of


def colour(kind, name, spec=''):
    if kind == 'pneumatic':
        s = f'{name} {spec}'.lower()
        return '#2f9e44' if re.search(W.alt('vacuum'), s) else '#e8590c' if re.search(W.alt('medium'), s) else '#1c5fd6'
    n = name.strip()
    if re.match(rf'^({W.alt("phase_l")})$', n):
        return '#8a5a2b'
    if n == 'N':
        return '#1d3fbf'
    if re.match(r'^PE\b', n):
        return '#2f9e44'
    if GROUND.match(n):
        return INK
    if SUPPLY.match(n):
        return '#d62828' if re.search(r'24|12|36|48', n) else '#e8590c'
    if re.match(rf'^({W.alt("motor_phase")})', n, re.I):
        return '#7b3fa0'
    return '#3d3d3d'


# ---------------------------------------------------------------- the ELK graph

ROOT_OPTS = {
    'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.edgeRouting': 'ORTHOGONAL',
    'elk.layered.spacing.nodeNodeBetweenLayers': '16', 'elk.spacing.nodeNode': '9',
    'elk.layered.spacing.edgeNodeBetweenLayers': '6', 'elk.layered.spacing.edgeEdgeBetweenLayers': '2.4',
    'elk.spacing.edgeEdge': '2.4', 'elk.spacing.edgeNode': '4', 'elk.spacing.portPort': '1.6',
    'elk.spacing.labelPortHorizontal': '0.8', 'elk.spacing.labelPortVertical': '0.4', 'elk.spacing.labelNode': '1.2',
    'elk.spacing.edgeLabel': '0.6', 'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
    'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES', 'elk.layered.thoroughness': '30',
    'elk.layered.edgeLabels.sideSelection': 'ALWAYS_UP', 'elk.padding': '[top=4,left=4,bottom=4,right=4]',
}


class Elem:
    def __init__(self, n, p, sym, tier_):
        self.id, self.part, self.sym, self.tier, self.order = n, p, sym, tier_, tier_
        self.title, self.sub = display(n, p)
        self.ports = {}            # name -> {'side', 'kind' (wire|supply|ground|flag|free|source|exhaust), 'net', 'text'}


def port_label(p, port):
    lab = str((p.get('ports') or {}).get(port) or '')
    return lab.split(':')[-1].strip() if ':' in lab else lab


def side_free(e):
    w = sum(1 for q in e.ports.values() if q['side'] == 'WEST')
    return 'WEST' if w < sum(1 for q in e.ports.values() if q['side'] == 'EAST') else 'EAST'


def build(kind, parts, conns, other):
    """-> (elements {id: Elem}, nets, edges [(net, from port key, to port key)])"""
    nets, of = nets_of(conns)
    elsewhere = {e for c in other for e in (c['from'], c['to'])}
    used = defaultdict(list)
    for net in nets:
        for e in net.ends:
            n, port = e.split('.', 1)
            used[n].append(port)
    els = {}
    for n in used:
        p = parts[n]
        if kind == 'electric':
            shown = used[n] + [q for q in p.get('ports') or {} if q not in used[n] and f'{n}.{q}' not in elsewhere]
            els[n] = Elem(n, p, e_symbol(n, p, shown), tier(n, p))
        else:
            els[n] = Elem(n, p, p_symbol(n, p), 0)
    edges = []
    if kind == 'pneumatic':
        dist = flow_order(els, nets, parts)
        for n, el in els.items():
            el.order = dist.get(n, 99)
        for net in nets:
            net.no = min(net.wires) + 1          # the number of its (first) hose in the connection table
            net.name = ''
            sil = [e for e in net.ends if els[e.split('.', 1)[0]].sym == 'silencer' and len(used[e.split('.', 1)[0]]) == 1]
            if len(sil) == 1 and len(net.ends) == 2:       # a silencer screwed into a port: a mark on that port, not a hose
                net.cls, net.name = 'silencer', f"H{net.no} · {els.pop(sil[0].split('.', 1)[0]).title}"
                continue
            for i in net.wires:                    # one line per hose, numbered as in the table, from the supply side
                a, b = conns[i]['from'], conns[i]['to']
                if dist.get(b.split('.', 1)[0], 99) < dist.get(a.split('.', 1)[0], 99):
                    a, b = b, a
                edges.append((net, a, b, f'H{i + 1}'))
    else:
        mcu = {n for n, e in els.items() if e.tier == 2}
        flagged_names = defaultdict(set)
        for net in nets:
            elems = [e.split('.', 1)[0] for e in net.ends]
            if net.name and SUPPLY.match(net.name) and not MAINS.match(net.name) and len(net.ends) >= 3:
                net.cls = 'ground' if GROUND.match(net.name) else 'supply'
            elif len(set(elems) & mcu) >= 2 or len(net.ends) >= 5:
                net.cls = 'flag'
                flagged_names[net.name or net.ends[0]].add(id(net))
            else:
                froms = {conns[i]['from'] for i in net.wires}
                src = min(net.ends, key=lambda e: (els[e.split('.', 1)[0]].tier, e not in froms, net.ends.index(e)))
                if MAINS.match(net.name or '') and conns[net.wires[0]]['from'] in net.ends:
                    src = conns[net.wires[0]]['from']                    # mains run in the order they are listed
                for e in net.ends:
                    if e != src:
                        edges.append((net, src, e, None))
        clash = [net for net in nets if net.cls == 'flag' and (not net.name or len(flagged_names[net.name]) > 1)]
        for net in clash:        # two different nets never carry the same flag: the hub's own name of the pin, else the pin
            hub = max(net.ends, key=lambda e: (len(parts[e.split('.', 1)[0]].get('ports') or {}), -net.ends.index(e)))
            lab = port_label(parts[hub.split('.', 1)[0]], hub.split('.', 1)[1])
            other = [port_label(parts[o.split('.', 1)[0]], o.split('.', 1)[1]) for x in clash if x is not net
                     for o in x.ends if o == max(x.ends, key=lambda e: len(parts[e.split('.', 1)[0]].get('ports') or {}))]
            net.name = lab if lab and lab not in other else f'{net.name or "?"} · {hub}'
    # port sides: a wire leaves its source to the right and enters its target from the left
    out_ = defaultdict(int)
    for net, a, b, _ in edges:
        out_[a] += 1
        out_[b] -= 1
    for net in nets:
        for e in net.ends:
            n, port = e.split('.', 1)
            if n not in els:
                continue
            el = els[n]
            k = 'wire' if net.cls == 'wire' else net.cls
            down = ('SOUTH', 'NORTH') if kind == 'electric' else ('EAST', 'WEST')     # electric flows down, air to the right
            side = {'supply': 'WEST', 'ground': 'EAST'}.get(k) or (down[0] if out_[e] > 0 else down[1])
            el.ports[port] = {'side': side, 'kind': k, 'net': net, 'text': net.name}
    for n, el in els.items():    # flags go to the side with fewer wires; free ports are shown too (not the other scheme's)
        for port, q in el.ports.items():
            if q['kind'] == 'flag':
                q['side'] = side_free(el)
        free = [q for q in (parts[n].get('ports') or {}) if q not in el.ports and f'{n}.{q}' not in elsewhere]
        same = defaultdict(int)
        for q in free:
            same[port_label(parts[n], q)] += 1
        for port in free:
            lab = port_label(parts[n], port)
            if lab and same[lab] >= 2:       # the free terminals of a bus bar
                continue
            k = 'free'
            if kind == 'pneumatic':
                k = 'source' if re.search(W.alt('air_source_port'), lab, re.I) or port.upper() == 'IN' else \
                    'exhaust' if re.search(W.alt('air_exhaust_port'), lab, re.I) or port.upper() in ('EX', 'R', 'S', 'EA', 'EB') else 'free'
            el.ports[port] = {'side': side_free(el), 'kind': k, 'net': None, 'text': lab}
        qs = list(el.ports.values())
        if el.sym in TWO_TERMINAL and len(qs) == 2 and {q['kind'] for q in qs} == {'wire', 'free'}:
            wired, loose = sorted(qs, key=lambda q: q['kind'] != 'wire')    # the free end opposite the wired one: drawn upright
            loose['side'] = {'NORTH': 'SOUTH', 'SOUTH': 'NORTH', 'WEST': 'EAST', 'EAST': 'WEST'}[wired['side']]
    return els, nets, edges


def flow_order(els, nets, parts):
    """Element -> hoses from the air supply (the element with a free port fed from a compressor, else the filter unit)."""
    adj = defaultdict(set)
    for net in nets:
        es = {e.split('.', 1)[0] for e in net.ends}
        for a in es:
            adj[a] |= es - {a}
    srcs = [n for n, el in els.items() if el.sym in ('source', 'frl', 'filter', 'regulator')]
    for n, el in els.items():
        for port, lab in (parts[n].get('ports') or {}).items():
            if not any(port in (e.split('.', 1)[1],) and e.startswith(n + '.') for net in nets for e in net.ends) \
                    and re.search(W.alt('air_supply'), str(lab), re.I):
                srcs.append(n)
    dist, todo = {}, list(dict.fromkeys(srcs or [next(iter(els))]))
    for s in todo:
        dist[s] = 0
    while todo:
        a = todo.pop(0)
        for b in sorted(adj[a]):
            if b not in dist:
                dist[b] = dist[a] + 1
                todo.append(b)
    return dist


# ---------------------------------------------------------------- geometry of the symbols

def sym_geom(el, kind):
    """(w, h, fixed port positions {port: (x, y, side)} or None = ELK places them on their sides)."""
    s, ports = el.sym, el.ports
    names_ = list(ports)
    if kind == 'electric':
        if s == 'motor':
            k = len(names_)
            w = max(24, 4.4 * k + 6, cap_size(el)[0] + 2)
            return w, 22 + cap_size(el)[1], {q: (w / 2 - 2.2 * (k - 1) + 4.4 * i, 0, 'NORTH') for i, q in enumerate(names_)}
        if s in TWO_TERMINAL:
            sides = sorted(q['side'] for q in ports.values())
            if sides == ['NORTH', 'SOUTH']:
                cw, ch = cap_size(el)
                return 10 + cw, max(18, ch + 2), {q: (4, 0 if ports[q]['side'] == 'NORTH' else 18, ports[q]['side']) for q in names_}
            return max(16, cap_size(el)[0] + 2), 14 + cap_size(el)[1], None
        return None
    pp = {}
    vp = {VALVE_PORT.get(q, q): q for q in names_}
    if s.startswith('valve'):
        frac = {'valve_2_2': {'A': (.5, 0), 'P': (.5, 1)}, 'valve_3_2': {'A': (.3, 0), 'P': (.3, 1), 'R': (.7, 1), 'S': (.7, 1)},
                'valve_5_2': {'A': (.3, 0), 'B': (.7, 0), 'R': (.2, 1), 'P': (.5, 1), 'S': (.8, 1)}}[s]
        x0 = 7 + 13
        for v, q in vp.items():
            if v in frac:
                fx, fy = frac[v]
                pp[q] = (x0 + 13 * fx, 0 if fy == 0 else 19, 'NORTH' if fy == 0 else 'SOUTH')
        rest = [q for q in names_ if q not in pp]
        for i, q in enumerate(rest):
            pp[q] = (46, 6 + 4 * i, 'EAST')
        return 46, 19, pp
    if s == 'ejector':
        order = {'P': (0, 6, 'WEST'), 'V': (12, 0, 'NORTH'), 'EX': (24, 6, 'EAST')}
        geo = (24, 12)
    elif s == 'cup':
        order, geo = {}, (14, 10)
        for i, q in enumerate(names_):
            pp[q] = (0, 1.5 + 3 * i, 'WEST')
        return 14, 10, pp
    elif s in ('frl', 'filter', 'regulator'):
        w = {'frl': 36, 'filter': 14, 'regulator': 16}[s]
        ins = [q for q in names_ if re.match(rf'(?i){W.alt("port_in")}', q)] or names_[:1]
        for q in names_:
            pp[q] = (0, 8, 'WEST') if q in ins else (w, 8, 'EAST')
        return w, 16, pp
    elif s == 'fitting':
        for i, q in enumerate(names_):
            pp[q] = (0 if i == 0 else 5, 2.5, 'WEST' if i == 0 else 'EAST')
        return 5, 5, pp
    elif s == 'tee':
        return 2.4, 2.4, None
    elif s == 'silencer':
        for q in names_:
            pp[q] = (3.5, 0, 'NORTH')
        return 7, 9, pp
    elif s == 'tank':
        k = len(names_)
        for i, q in enumerate(names_):
            pp[q] = (20 * (i + 1) / (k + 1), 0, 'NORTH')
        return 20, 24, pp
    elif s == 'cylinder':
        air = [q for q in names_ if re.search(rf'(?i)^({W.alt("port_air")})', q)][:2] or names_[:2]
        for i, q in enumerate(air):
            pp[q] = (4 if i == 0 else 24, 13, 'SOUTH')
        rest = [q for q in names_ if q not in pp]
        for i, q in enumerate(rest):
            pp[q] = (34, 6.5, 'EAST') if i == 0 else (30 - 4 * i, 0, 'NORTH')
        return 34, 13, pp
    elif s == 'source':
        for q in names_:
            pp[q] = (10, 5, 'EAST')
        return 10, 10, pp
    else:
        return None
    for q in names_:
        pp[q] = order.get(VALVE_PORT.get(q, q).upper(), order.get(q, (geo[0], 3 + 3 * len(pp), 'EAST')))
    return geo[0], geo[1], pp


def outside(q):
    """The extent (along, across) a port's mark takes outside its element: supply / ground marks, flags, free-port notes."""
    k, t = q['kind'], q['text'] or ''
    if k in ('supply', 'ground'):
        return max(tw(t, F_NET) + 1.5, 4.0), 8.5
    if k == 'flag':
        return 3.2, tw(t, F_NET) + 6
    if k == 'free':
        return 2.6, (tw(cut(t, 34), F_SUB) + 3) if t else 2
    if k == 'source':
        return 9, 14
    if k == 'exhaust':
        return 6, 8
    if k == 'silencer':
        return 7, 12 + tw(cut(t, 24), F_SUB * 0.9)
    return 0.01, 0.01


def elk_graphs(kind, els, edges):
    """One ELK graph per connected part of the sheet."""
    up = {n: n for n in els}

    def find(a):
        while up[a] != a:
            up[a] = up[up[a]]
            a = up[a]
        return a
    for net, a, b, _ in edges:
        up[find(a.split('.', 1)[0])] = find(b.split('.', 1)[0])
    comps = defaultdict(list)
    for n in els:
        comps[find(n)].append(n)
    graphs = []
    for gi, members in enumerate(sorted(comps.values(), key=lambda m: (-len(m), m))):
        children = []
        for n in sorted(members, key=lambda n: (els[n].order, n)):
            el = els[n]
            geom = sym_geom(el, kind)
            node = {'id': n, 'ports': [], 'labels': [], 'layoutOptions': {}}
            for port, q in el.ports.items():
                along, across = outside(q)
                vertical = q['side'] in ('NORTH', 'SOUTH')
                w, h = (along, across) if vertical else (across, along)
                pid = f'{n}.{port}'
                pd = {'id': pid, 'width': w, 'height': h, 'layoutOptions': {'elk.port.side': q['side']}}
                if geom and geom[2] and port in geom[2]:
                    x, y, side = geom[2][port]
                    q['side'] = side
                    vertical = side in ('NORTH', 'SOUTH')
                    w, h = (along, across) if vertical else (across, along)
                    off = {'NORTH': (x - w / 2, -h), 'SOUTH': (x - w / 2, y), 'WEST': (-w, y - h / 2), 'EAST': (x, y - h / 2)}[side]
                    pd.update(width=w, height=h, x=off[0], y=off[1], layoutOptions={'elk.port.side': side})
                if el.sym == 'box':
                    lw_, lh_ = tw(port, F_PIN), th(F_PIN)
                    pd['labels'] = [{'text': port, 'width': lh_ if vertical else lw_, 'height': lw_ if vertical else lh_}]
                    if q['kind'] in ('supply', 'ground', 'flag', 'free'):
                        pd['layoutOptions']['elk.port.borderOffset'] = '0'
                node['ports'].append(pd)
            if geom:
                node.update(width=geom[0], height=geom[1])
                if kind == 'pneumatic' and el.sym != 'tee':
                    where = 'OUTSIDE V_CENTER H_LEFT' if el.sym.startswith('valve') else \
                        'OUTSIDE V_TOP H_CENTER' if el.sym == 'cylinder' else 'OUTSIDE V_BOTTOM H_CENTER'
                    cw, ch = cap_size(el)
                    node['labels'] = [{'text': el.title, 'width': cw + 1, 'height': ch + 0.5,
                                       'layoutOptions': {'elk.nodeLabels.placement': where}}]
                    node['layoutOptions']['elk.nodeLabels.placement'] = where
                node['layoutOptions']['elk.portConstraints'] = 'FIXED_POS' if geom[2] else 'FIXED_SIDE'
                if el.sym == 'tee':
                    node['layoutOptions']['elk.portConstraints'] = 'FREE'
                    for pd in node['ports']:
                        pd['width'] = pd['height'] = 0.01
            else:                    # a block: sized by ELK around its ports and its name
                el.lines = wrap(el.title, 26)
                lw = max(max(tw(t, F_TITLE, True) for t in el.lines), tw(el.sub, F_SUB)) + 4
                node['labels'] = [{'text': el.title, 'width': lw, 'height': th(F_TITLE, len(el.lines)) + th(F_SUB) + 1.5}]
                node['layoutOptions'].update({
                    'elk.portConstraints': 'FIXED_SIDE', 'elk.nodeSize.constraints': 'PORTS PORT_LABELS NODE_LABELS MINIMUM_SIZE',
                    'elk.nodeSize.minimum': f'({max(lw, 18)}, 14)', 'elk.portLabels.placement': 'INSIDE',
                    'elk.nodeLabels.placement': 'INSIDE V_TOP H_CENTER', 'elk.spacing.portPort': '2.2',
                    'elk.nodeLabels.padding': '[top=1.5,left=2,bottom=1.5,right=2]',
                    'elk.nodeSize.options': 'DEFAULT_MINIMUM_SIZE'})
            children.append(node)
        ids = set(members)
        es = []
        for k, (net, a, b, t) in enumerate(edges):
            if a.split('.', 1)[0] not in ids:
                continue
            e = {'id': f'e{k}', 'sources': [a], 'targets': [b], 'labels': []}
            if t:                    # a hose's number; an electric net's name is painted along its wire
                e['labels'] = [{'text': t, 'width': tw(t, F_NET, True), 'height': th(F_NET),
                                'layoutOptions': {'elk.edgeLabels.placement': 'CENTER'}}]
            es.append(e)
        opts = dict(ROOT_OPTS)
        if kind == 'electric':
            opts['elk.direction'] = 'DOWN'
        if kind == 'pneumatic':
            opts.update({'elk.layered.spacing.nodeNodeBetweenLayers': '20', 'elk.spacing.nodeNode': '12'})
        graphs.append({'id': f'g{gi}', 'layoutOptions': opts, 'children': children, 'edges': es})
    return graphs


def elk(graphs, timeout=120):
    r = subprocess.run(['node', str(HERE / 'elk_layout.cjs')], input=json.dumps(graphs), capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode:
        raise RuntimeError(f'ELK layout failed: {(r.stderr.strip().splitlines() or ["?"])[-1][:300]}')
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise RuntimeError(f'ELK layout printed no JSON: {r.stdout[:200]!r}')


# ---------------------------------------------------------------- pages

class Page:
    def __init__(self, w, h):
        self.w, self.h, self.items = w, h, []     # items: (graph, x, y, scale)
        self.lang = T.DEFAULT                     # the texts on the sheet (title block, legend): text_i18n.py


def pack(graphs):
    """Shelf-pack the laid-out parts onto A3 sheets, biggest first; a part larger than a sheet's field gets a larger sheet."""
    pages, cur = [], None
    fw, fh = SHEETS[0][0] - 2 * MARGIN - 8, SHEETS[0][1] - 2 * MARGIN - TB_H - 8
    x = y = row = 0
    for g in sorted(graphs, key=lambda g: -g['width'] * g['height']):
        w, h = g['width'], g['height']
        if w > fw or h > fh:     # a sheet of its own: the smallest one it fits at 70 % or more, centred
            fit = [(a, b, min(1.0, (a - 2 * MARGIN - 8) / w, (b - 2 * MARGIN - TB_H - 8) / h)) for a, b in SHEETS]
            sw, sh, s = next((f for f in fit if f[2] >= 0.7), fit[-1])
            p = Page(sw, sh)
            p.items.append((g, (sw - w * s) / 2, MARGIN + 4 + (sh - 2 * MARGIN - TB_H - 8 - h * s) / 2, s))
            pages.append(p)
            continue
        if cur is None or (x + w > fw and y + row + 4 + h > fh) or (x + w <= fw and y + h > fh):
            cur = Page(*SHEETS[0])
            pages.append(cur)
            x = y = row = 0
        if x + w > fw:
            x, y, row = 0, y + row + 4, 0
        cur.items.append((g, MARGIN + 4 + x, MARGIN + 4 + y, 1.0))
        x += w + 6
        row = max(row, h)
    for p in pages:          # a sheet with one small drawing: drawn larger (up to 1.6 x), so it reads
        if len(p.items) == 1 and p.items[0][3] == 1.0:
            g = p.items[0][0]
            s = min(1.6, fw / g['width'], fh / g['height'])
            p.items = [(g, MARGIN + 4, MARGIN + 4, max(1.0, s))]
    for p in pages:          # the drawing in the middle of its field
        x0 = min(x for g, x, y, s in p.items)
        y0 = min(y for g, x, y, s in p.items)
        x1 = max(x + g['width'] * s for g, x, y, s in p.items)
        y1 = max(y + g['height'] * s for g, x, y, s in p.items)
        dx = (p.w - (x1 - x0)) / 2 - x0
        dy = MARGIN + 4 + (p.h - 2 * MARGIN - TB_H - 8 - (y1 - y0)) / 2 - y0
        p.items = [(g, x + dx, y + dy, s) for g, x, y, s in p.items]
    return pages


def pages(kind, parts, conns, other, title, lang=T.DEFAULT):
    els, nets, edges = build(kind, parts, conns, other)
    laid = elk(elk_graphs(kind, els, edges))
    for g in laid:
        g['_els'], g['_kind'], g['_conns'], g['_lang'] = els, kind, conns, lang
    ps = pack(laid)
    for p in ps:
        p.kind, p.title, p.nets, p.lang = kind, title, nets, lang
    return ps


# ---------------------------------------------------------------- painting

def _abs(node, p, ox, oy):
    return ox + node['x'] + p['x'], oy + node['y'] + p['y']


class Painter:
    def __init__(self, ax, s, ox, oy):
        self.ax, self.s, self.ox, self.oy = ax, s, ox, oy
        self.lang = T.DEFAULT                   # the language of the texts the painter adds itself (a mark's default label)

    def P(self, x, y):
        return self.ox + x * self.s, self.oy + y * self.s

    def line(self, pts, c=INK, lw=LW, ls='-', z=2):
        xs, ys = zip(*(self.P(*q) for q in pts))
        self.ax.plot(xs, ys, color=c, lw=lw, ls=ls, solid_capstyle='butt', zorder=z)

    def poly(self, pts, fc='none', ec=INK, lw=LW, closed=True, z=3, ls='-'):
        from matplotlib.patches import Polygon
        self.ax.add_patch(Polygon([self.P(*q) for q in pts], closed=closed, fc=fc, ec=ec, lw=lw, zorder=z, ls=ls))

    def rect(self, x, y, w, h, fc='none', ec=INK, lw=LW, z=3, ls='-'):
        self.poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], fc=fc, ec=ec, lw=lw, z=z, ls=ls)

    def circle(self, x, y, r, fc=PAPER, ec=INK, lw=LW, z=3):
        from matplotlib.patches import Circle
        self.ax.add_patch(Circle(self.P(x, y), r * self.s, fc=fc, ec=ec, lw=lw, zorder=z))

    def text(self, x, y, s, size, ha='center', va='center', c=INK, bold=False, rot=0, z=5):
        if s:
            self.ax.text(*self.P(x, y), s, fontsize=size * self.s, ha=ha, va=va, color=c, rotation=rot, zorder=z,
                         fontweight='bold' if bold else 'normal', family='DejaVu Sans')


def paint_graph(ax, g, ox, oy, s):
    els, kind = g['_els'], g['_kind']
    d = Painter(ax, s, ox, oy)
    d.lang = g.get('_lang', T.DEFAULT)
    named = set()
    for e in g.get('edges') or []:
        if kind != 'electric':
            continue
        q = els[e['sources'][0].split('.', 1)[0]].ports[e['sources'][0].split('.', 1)[1]]
        key = (q['net'].name, e['sources'][0].split('.', 1)[0], e['targets'][0].split('.', 1)[0])
        if not q['net'].name or key in named:      # one name for the wires that run together (a motor's four phases)
            continue
        named.add(key)
        segs = [(a, b) for sec in e.get('sections') or [] for a, b in zip(
            [sec['startPoint']] + sec.get('bendPoints', []), sec.get('bendPoints', []) + [sec['endPoint']])]
        if not segs:
            continue
        a, b = max(segs, key=lambda ab: abs(ab[0]['x'] - ab[1]['x']) + abs(ab[0]['y'] - ab[1]['y']))
        mx, my, t = (a['x'] + b['x']) / 2, (a['y'] + b['y']) / 2, cut(q['net'].name, 28)
        c = colour(kind, q['net'].name)
        if abs(a['x'] - b['x']) < 0.01:
            d.text(mx - 1.0, my, t, F_NET, c=c, rot=90)
        else:
            d.text(mx, my - 1.0, t, F_NET, c=c)
    for e in g.get('edges') or []:
        net_c = None
        for sec in e.get('sections') or []:
            pts = [sec['startPoint']] + sec.get('bendPoints', []) + [sec['endPoint']]
            n_, port = e['sources'][0].split('.', 1)
            q = els[n_].ports[port]
            net = q['net']
            net_c = colour(kind, net.name if kind == 'electric' else '', hose_spec(g, net))
            d.line([(p['x'], p['y']) for p in pts], c=net_c, lw=LW_WIRE, z=2)
        for lab in e.get('labels') or []:
            d.text(lab['x'] + lab['width'] / 2, lab['y'] + lab['height'] / 2, lab['text'], F_NET, c=net_c or INK,
                   bold=kind == 'pneumatic')
    junctions(d, g)
    for node in g['children']:
        paint_node(d, node, els[node['id']], kind)


def hose_spec(g, net):
    return ' '.join(str(g['_conns'][i].get('spec') or '') for i in net.wires) if '_conns' in g and net else ''


def junctions(d, g):
    """A dot where a net's lines branch (three or more arms meet)."""
    segs = defaultdict(list)
    for e in g.get('edges') or []:
        for sec in e.get('sections') or []:
            pts = [sec['startPoint']] + sec.get('bendPoints', []) + [sec['endPoint']]
            segs[e['sources'][0]] += [((round(a['x'], 2), round(a['y'], 2)), (round(b['x'], 2), round(b['y'], 2)))
                                      for a, b in zip(pts, pts[1:])]
    for src, ss in segs.items():
        pts = {p for sgm in ss for p in sgm}
        for p in pts:
            arms = set()
            for a, b in ss:
                for u, v in ((a, b), (b, a)):
                    if u == p and v != p:
                        arms.add((math.copysign(1, v[0] - u[0]) if v[0] != u[0] else 0, math.copysign(1, v[1] - u[1]) if v[1] != u[1] else 0))
                    elif min(a[0], b[0]) < p[0] < max(a[0], b[0]) and a[1] == b[1] == p[1]:
                        arms |= {(1, 0), (-1, 0)}
                    elif min(a[1], b[1]) < p[1] < max(a[1], b[1]) and a[0] == b[0] == p[0]:
                        arms |= {(0, 1), (0, -1)}
            if len(arms) >= 3:
                d.circle(p[0], p[1], 0.75, fc=INK, ec='none', z=4)


def paint_node(d, node, el, kind):
    x, y, w, h = node['x'], node['y'], node['width'], node['height']
    ports = {p['id'].split('.', 1)[1]: p for p in node.get('ports') or []}

    def pos(port):   # where a port's line meets the element
        p = ports[port]
        side = el.ports[port]['side']
        px, py = x + p['x'], y + p['y']
        return {'NORTH': (px + p['width'] / 2, y), 'SOUTH': (px + p['width'] / 2, y + h), 'WEST': (x, py + p['height'] / 2),
                'EAST': (x + w, py + p['height'] / 2)}[side]

    sym = el.sym
    el.nx, el.ny, el.label = x, y, (node.get('labels') or [None])[0] if kind == 'pneumatic' else None
    if sym == 'box' or (kind == 'electric' and sym not in ('motor',) + TWO_TERMINAL):
        d.rect(x, y, w, h, fc=BOXFILL, lw=LW)
        lab = (node.get('labels') or [{}])[0]
        lx, ly = x + lab.get('x', 2) + lab.get('width', w) / 2, y + lab.get('y', 1.5)
        lines = getattr(el, 'lines', [el.title])
        for i, t in enumerate(lines):
            d.text(lx, ly + th(F_TITLE) * (i + 0.5), t, F_TITLE, bold=True)
        d.text(lx, ly + th(F_TITLE, len(lines)) + 0.5 + th(F_SUB) / 2, el.sub, F_SUB, c=GREY)
        for port, p in ports.items():
            for lb in p.get('labels') or []:
                d.text(x + p['x'] + lb['x'] + lb['width'] / 2, y + p['y'] + lb['y'] + lb['height'] / 2, lb['text'], F_PIN,
                       c=INK if el.ports[port]['net'] else GREY, rot=90 if el.ports[port]['side'] in ('NORTH', 'SOUTH') else 0)
    else:
        paint_symbol(d, sym, el, x, y, w, h, {q: pos(q) for q in ports})
    for port in ports:
        paint_mark(d, el.ports[port], *pos(port), kind)


def cap_lines(el):
    """A symbol's caption: its name on one or two lines, then its id and code."""
    return [(t, True) for t in wrap(el.title, 26)] + ([(cut(el.sub, 34), False)] if el.sub else [])


def cap_size(el):
    ls = cap_lines(el)
    return max(tw(t, F_SUB if b else F_SUB * 0.9, b) for t, b in ls), th(F_SUB, len(ls))


def caption(d, el, cx, y, ha='center'):
    lab = getattr(el, 'label', None)
    if lab:               # placed by ELK outside the symbol
        cx, y, ha = el.nx + lab['x'] + lab['width'] / 2, el.ny + lab['y'], 'center'
    for i, (t, bold) in enumerate(cap_lines(el)):
        d.text(cx, y + th(F_SUB) * (i + 0.5), t, F_SUB if bold else F_SUB * 0.9, bold=bold, c=INK if bold else GREY, ha=ha)


def _two_terminal(d, sym, T):
    """A two-terminal symbol between T(-4, 0) and T(4, 0); T maps (along, across) to the sheet (across < 0: the actuator side)."""
    def ln(*pts, **kw):
        d.line([T(*q) for q in pts], **kw)
    if sym == 'coil':
        d.poly([T(-4, -2.6), T(4, -2.6), T(4, 2.6), T(-4, 2.6)], fc=PAPER)
        ln((-4, 2.6), (4, -2.6))
    elif sym in ('button', 'button_nc'):
        for u in (-4, 4):
            d.circle(*T(u, 0), 0.5, fc=INK, ec=INK)
        if sym == 'button':
            ln((-4.4, -2.2), (4.4, -2.2))
            ln((0, -2.2), (0, -4.6), ls=(0, (1, 1)))
        else:
            ln((-4, 0), (4.4, 1.2))
            ln((0, 0.6), (0, -4.6), ls=(0, (1, 1)))
        ln((-1.5, -4.6), (1.5, -4.6))
    elif sym == 'breaker':
        ln((-4, 0), (3, -3))
        ln((2.4, -1), (4.6, 1))
        ln((2.4, 1), (4.6, -1))
    elif sym == 'battery':
        ln((-1, -3), (-1, 3), lw=1.2)
        ln((1, -1.6), (1, 1.6), lw=2)
        ln((-4, 0), (-1, 0))
        ln((1, 0), (4, 0))


def paint_symbol(d, sym, el, x, y, w, h, at):
    """at: port -> where its line meets the element."""
    if sym == 'motor':
        r = 7
        cx, cy = x + w / 2, y + 13
        for port, (px, py) in at.items():
            dy = cy - math.sqrt(max(r * r - (px - cx) ** 2, 0))
            d.line([(px, py), (px, dy)], lw=LW_WIRE, c='#7b3fa0')
            d.text(px + 0.5, py + 1.2, port, F_PIN * 0.8, ha='left', va='top')
        d.circle(cx, cy, r)
        d.text(cx, cy, 'M', 10, bold=True)
        caption(d, el, x + w / 2, y + 22)
        return
    if sym in TWO_TERMINAL:
        vert = sorted(el.ports[q]['side'] for q in at) == ['NORTH', 'SOUTH']
        cx, cy = (x + 4, y + 9) if vert else (x + w / 2, y + 7)
        T = (lambda u, v: (cx + v, cy + u)) if vert else (lambda u, v: (cx + u, cy + v))
        pts = sorted(at.values(), key=lambda q: (q[1], q[0]) if vert else (q[0], q[1]))
        for (px, py), (tx, ty) in zip(pts, (T(-4, 0), T(4, 0))):
            d.line([(px, py), (tx, ty)] if abs(px - tx) < 0.1 or abs(py - ty) < 0.1 else [(px, py), (px, ty), (tx, ty)], lw=LW_WIRE)
        _two_terminal(d, sym, T)
        for port, (px, py) in at.items():
            if vert:
                d.text(px - 1, py + (1.2 if py <= y + 0.1 else -1.2), port, F_PIN * 0.85, ha='right')
            else:
                d.text(px + (1 if px < cx else -1), py - 1.1, port, F_PIN * 0.85, ha='left' if px < cx else 'right')
        if vert:
            caption(d, el, x + 9, y + 9 - cap_size(el)[1] / 2, ha='left')
        else:
            caption(d, el, cx, y + 14)
        return
    # pneumatic: ISO 1219
    if sym.startswith('valve'):
        sq = 13
        x0 = x + 7
        top = y + 3
        d.rect(x0, top, sq, sq, fc=PAPER)
        d.rect(x0 + sq, top, sq, sq, fc=PAPER)
        coil = any(k.lower().startswith('coil') for k in el.part.get('ports') or {})
        if coil:          # solenoid
            d.rect(x, top + 4, 7, 5, fc=PAPER)
            d.line([(x, top + 9), (x + 7, top + 4)])
        else:
            d.line([(x + 1, top + 6.5), (x0, top + 6.5)])
        zz = [(x0 + 2 * sq + 0.8 * i, top + 6.5 + (1.6 if i % 2 else -1.6)) for i in range(8)]
        d.line([(x0 + 2 * sq, top + 6.5)] + zz, lw=0.6)          # spring
        R, L = x0 + sq, x0

        def arrow(xa, ya, xb, yb):
            d.line([(xa, ya), (xb, yb)], lw=0.7)
            ang = math.atan2(yb - ya, xb - xa)
            hx, hy = 1.6 * math.cos(ang), 1.6 * math.sin(ang)
            d.poly([(xb, yb), (xb - hx - 0.6 * math.sin(ang), yb - hy + 0.6 * math.cos(ang)),
                    (xb - hx + 0.6 * math.sin(ang), yb - hy - 0.6 * math.cos(ang))], fc=INK, ec=INK, lw=0.3)

        def block(xa, ya, down):
            d.line([(xa, ya), (xa, ya + (2.2 if down else -2.2))], lw=0.7)
            d.line([(xa - 1.4, ya + (2.2 if down else -2.2)), (xa + 1.4, ya + (2.2 if down else -2.2))], lw=0.7)
        b = top + sq
        if sym == 'valve_2_2':
            block(R + sq * .5, top, True)
            block(R + sq * .5, b, False)
            arrow(L + sq * .5, b, L + sq * .5, top)
        elif sym == 'valve_3_2':
            block(R + sq * .3, b, False)
            arrow(R + sq * .3, top, R + sq * .7, b)
            arrow(L + sq * .3, b, L + sq * .3, top)
            block(L + sq * .7, b, False)
        else:
            arrow(R + sq * .5, b, R + sq * .7, top)
            arrow(R + sq * .3, top, R + sq * .2, b)
            arrow(L + sq * .5, b, L + sq * .3, top)
            arrow(L + sq * .7, top, L + sq * .8, b)
        for port, (px, py) in at.items():
            d.text(px + 0.8, py + (-1.2 if py <= y + 0.1 else 1.2), VALVE_PORT.get(port, port), F_PIN * 0.85, ha='left')
        caption(d, el, x + w / 2, y + h + 1.5)
    elif sym == 'ejector':
        d.rect(x, y, w, h, fc=PAPER)
        cy = y + h / 2
        d.line([(x + 2, cy - 3.5), (x + w / 2 - 1, cy - 0.6), (x + w / 2 + 1, cy - 0.6), (x + w - 2, cy - 3.5)], lw=0.6)
        d.line([(x + 2, cy + 3.5), (x + w / 2 - 1, cy + 0.6), (x + w / 2 + 1, cy + 0.6), (x + w - 2, cy + 3.5)], lw=0.6)
        d.line([(x + w / 2, y), (x + w / 2, cy - 0.6)], lw=0.6)
        for port, (px, py) in at.items():
            d.text(px + 0.8, py - 1.2, port, F_PIN * 0.85, ha='left')
        caption(d, el, x + w / 2, y + h + 1.5)
    elif sym == 'cup':
        d.poly([(x + 4, y + 3), (x + 10, y + 3), (x + 13, y + 9), (x + 1, y + 9)], fc='#e9ecef')
        for port, (px, py) in at.items():
            d.line([(px, py), (x + 7, py), (x + 7, y + 3)], lw=LW_WIRE, c='#2f9e44')
        caption(d, el, x + w / 2, y + h + 1.5)
    elif sym in ('frl', 'filter', 'regulator'):
        cy = y + 8
        if sym == 'frl':
            d.rect(x + 1, y + 1, w - 2, h - 2, ls=(0, (2, 1.5)), lw=0.6)
        d.line([(x, cy), (x + w, cy)], lw=LW_WIRE, c='#1c5fd6', z=1)
        if sym in ('frl', 'filter'):
            fx = x + (9 if sym == 'frl' else w / 2)
            d.poly([(fx - 5, cy), (fx, cy - 5), (fx + 5, cy), (fx, cy + 5)], fc=PAPER)
            d.line([(fx, cy - 5), (fx, cy + 5)], ls=(0, (1, 1)), lw=0.5)
        if sym in ('frl', 'regulator'):
            rx = x + (21 if sym == 'frl' else w / 2)
            d.rect(rx - 4.5, cy - 4.5, 9, 9, fc=PAPER)
            d.line([(rx - 3.5, cy + 3.5), (rx + 3.5, cy - 3.5)], lw=0.6)
            d.poly([(rx + 3.5, cy - 3.5), (rx + 1.4, cy - 3.1), (rx + 3.1, cy - 1.4)], fc=INK, lw=0.3)
            if sym == 'frl':
                d.line([(x + 30, cy), (x + 30, y + 4.8)], lw=0.6)
                d.circle(x + 30, y + 2.6, 2.2, fc=PAPER, lw=0.6)
                d.line([(x + 29, y + 3.5), (x + 31.2, y + 1.6)], lw=0.5)
        caption(d, el, x + w / 2, y + h + 1.5)
    elif sym == 'fitting':
        d.rect(x, y, w, h, fc='#d0d5db')
        caption(d, el, x + w / 2, y + h + 1.5)
    elif sym == 'tee':
        cx, cy = x + w / 2, y + h / 2
        for port, (px, py) in at.items():
            d.line([(px, py), (cx, cy)], lw=LW_WIRE, c='#1c5fd6')
        d.circle(cx, cy, 1.0, fc=INK, ec=INK)
        d.text(cx + 1.6, cy - 1.8, cut(el.title, 18), F_SUB * 0.9, ha='left', c=GREY)
    elif sym == 'silencer':
        d.rect(x + 1, y + 2, w - 2, h - 2, fc=PAPER)
        for k in range(3):
            d.line([(x + 1.8, y + 4 + 2 * k), (x + w - 1.8, y + 4 + 2 * k)], lw=0.4, ls=(0, (1, 0.8)))
        for port, (px, py) in at.items():
            d.line([(px, py), (px, y + 2)], lw=LW_WIRE, c='#1c5fd6')
        d.text(x + w + 1, y + h / 2, cut(el.title, 20), F_SUB * 0.9, ha='left', c=GREY)
    elif sym == 'tank':
        from matplotlib.patches import FancyBboxPatch
        d.ax.add_patch(FancyBboxPatch(d.P(x + 1, y + 2), (w - 2) * d.s, (h - 2) * d.s, boxstyle=f'round,pad=0,rounding_size={2 * d.s}',
                                      fc=PAPER, ec=INK, lw=LW, zorder=3))
        d.rect(x + 1.6, y + h * .55, w - 3.2, h * .45 - .6, fc='#f5deb3', ec='none', z=3)
        for port, (px, py) in at.items():
            d.line([(px, py), (px, y + 2)], lw=LW_WIRE)
            d.text(px, py + 4, port_label(el.part, port) or port, F_PIN * 0.85, rot=90, va='top')
        caption(d, el, x + w / 2, y + h + 1.5)
    elif sym == 'cylinder':
        bw = 26
        d.rect(x, y + 1, bw, h - 2, fc=PAPER)
        d.line([(x + 10, y + 1), (x + 10, y + h - 1)], lw=1.4)
        d.line([(x + 10, y + h / 2), (x + w, y + h / 2)], lw=1.4)
        for port, (px, py) in at.items():
            lab = port_label(el.part, port) or port
            d.text(px, py + 1.4, cut(lab, 14), F_PIN * 0.85, va='top')
        caption(d, el, x + w / 2, y - cap_size(el)[1] - 1.5)
    elif sym == 'source':
        d.circle(x + 5, y + 5, 4.5)
        d.poly([(x + 3.5, y + 2.6), (x + 7.3, y + 5), (x + 3.5, y + 7.4)], fc=INK, lw=0.3)
        caption(d, el, x + w / 2, y + h + 1.5)


def paint_mark(d, q, px, py, kind):
    """What is drawn outside an element at a port: a supply / ground mark, a flag, a free port's note, an air supply, an exhaust."""
    k, t, side = q['kind'], q['text'] or '', q['side']
    sx, sy = {'NORTH': (0, -1), 'SOUTH': (0, 1), 'WEST': (-1, 0), 'EAST': (1, 0)}[side]
    if k in ('supply', 'ground'):
        c = colour('electric', t)
        ex, ey = px + sx * 3.5, py + sy * 3.5
        d.line([(px, py), (ex, ey)], c=c, lw=LW_WIRE)
        if k == 'ground':
            for i, half in enumerate((2.2, 1.4, 0.6)):
                gy = ey + sy * 0.9 * i
                d.line([(ex - half, gy), (ex + half, gy)], c=c, lw=0.8) if sy else d.line([(ex + sx * 0.9 * i, py - half), (ex + sx * 0.9 * i, py + half)], c=c, lw=0.8)
            if t and not re.match(rf'^\s*(0\s*({VOLT})|GND)\s*$', t):
                d.text(ex + 2.6, ey + sy * 1.2, cut(t, 20), F_NET * 0.9, ha='left', c=c)
        else:
            if sy:
                d.line([(ex - 2, ey), (ex + 2, ey)], c=c, lw=1.0)
                d.text(ex, ey + sy * 2.2, t, F_NET, c=c, bold=True)
            else:
                d.line([(ex, ey - 2), (ex, ey + 2)], c=c, lw=1.0)
                d.text(ex + sx * 1, ey, t, F_NET, c=c, bold=True, ha='left' if sx > 0 else 'right')
        return
    if k == 'flag':
        c = colour('electric', t)
        L = tw(t, F_NET) + 3.2
        d.line([(px, py), (px + sx * 1.6, py + sy * 1.6)], c=c, lw=LW_WIRE)
        if sx:
            x0 = px + sx * 1.6
            pts = [(x0, py), (x0 + sx * 1.4, py - 1.5), (x0 + sx * L, py - 1.5), (x0 + sx * L, py + 1.5), (x0 + sx * 1.4, py + 1.5)]
            d.poly(pts, fc=PAPER, ec=c, lw=0.6)
            d.text(x0 + sx * (1.4 + (L - 1.4) / 2), py, t, F_NET, c=c)
        else:
            y0 = py + sy * 1.6
            d.poly([(px, y0), (px - 1.5, y0 + sy * 1.4), (px - 1.5, y0 + sy * L), (px + 1.5, y0 + sy * L), (px + 1.5, y0 + sy * 1.4)],
                   fc=PAPER, ec=c, lw=0.6)
            d.text(px, y0 + sy * (1.4 + (L - 1.4) / 2), t, F_NET, c=c, rot=90)
        return
    if k == 'free':
        d.line([(px, py), (px + sx * 1.5, py + sy * 1.5)], c=GREY, lw=0.5)
        if t:
            if sx:
                d.text(px + sx * 2.2, py, cut(t, 34), F_SUB, ha='left' if sx > 0 else 'right', c=GREY)
            else:
                d.text(px, py + sy * 2.2, cut(t, 34), F_SUB, va='bottom' if sy < 0 else 'top', c=GREY, rot=90)
        return
    if k == 'source':
        cx, cy = px + sx * 7, py + sy * 7
        d.line([(px, py), (px + sx * 2.5, py + sy * 2.5)], c='#1c5fd6', lw=LW_WIRE)
        d.circle(cx, cy, 4.5)
        d.poly([(cx - 1.5, cy - 2.4), (cx + 2.3, cy), (cx - 1.5, cy + 2.4)], fc=INK, lw=0.3)
        d.text(cx, cy + 6.2, cut(t or T.t('scheme.air', d.lang), 22), F_SUB, c=GREY)
        return
    if k == 'silencer':
        ex, ey = px + sx * 2.5, py + sy * 2.5
        d.line([(px, py), (ex, ey)], c='#1c5fd6', lw=LW_WIRE)
        x0, y0, w, h = (ex - 2.5, ey if sy > 0 else ey - 7, 5, 7) if sy else (ex if sx > 0 else ex - 7, ey - 2.5, 7, 5)
        d.rect(x0, y0, w, h, fc=PAPER)
        lx, ly = (ex, y0 + h + 1 if sy > 0 else y0 - 1) if sy else (x0 + w + 1 if sx > 0 else x0 - 1, ey)
        d.text(lx, ly, cut(t, 24), F_SUB * 0.9, c=GREY, rot=90 if sy else 0,
               ha='center' if sy else ('left' if sx > 0 else 'right'), va=('top' if sy > 0 else 'bottom') if sy else 'center')
        for i in range(1, 3):
            if sy:
                d.line([(x0 + 0.7, y0 + h * i / 3), (x0 + w - 0.7, y0 + h * i / 3)], lw=0.4, ls=(0, (1, 0.8)))
            else:
                d.line([(x0 + w * i / 3, y0 + 0.7), (x0 + w * i / 3, y0 + h - 0.7)], lw=0.4, ls=(0, (1, 0.8)))
        return
    if k == 'exhaust':
        ex, ey = px + sx * 2, py + sy * 2
        d.line([(px, py), (ex, ey)], c='#1c5fd6', lw=LW_WIRE)
        if sx:
            d.poly([(ex, ey), (ex + sx * 3.5, ey - 2.2), (ex + sx * 3.5, ey + 2.2)], fc=PAPER)
        else:
            d.poly([(ex, ey), (ex - 2.2, ey + sy * 3.5), (ex + 2.2, ey + sy * 3.5)], fc=PAPER)
        return


def frame(ax, p, n, total, oy=0):
    from matplotlib.patches import Rectangle
    ax.add_patch(Rectangle((MARGIN, oy + MARGIN), p.w - 2 * MARGIN, p.h - 2 * MARGIN, fill=False, lw=0.9, ec=INK, zorder=1))
    x0, y0 = p.w - MARGIN - TB_W, oy + p.h - MARGIN - TB_H
    ax.add_patch(Rectangle((x0, y0), TB_W, TB_H, fc=PAPER, lw=0.9, ec=INK, zorder=6))
    lang = getattr(p, 'lang', T.DEFAULT)
    ax.text(x0 + 3, y0 + 5, p.title or T.t('scheme.machine', lang), fontsize=8, weight='bold', va='center', zorder=7,
            family='DejaVu Sans')
    ax.text(x0 + 3, y0 + 11, T.t('scheme.' + p.kind, lang) + T.t('scheme.from_model', lang),
            fontsize=7.5, va='center', zorder=7, family='DejaVu Sans')
    ax.text(x0 + TB_W - 3, y0 + 11, T.t('scheme.sheet', lang, n=n, total=total), fontsize=7.5, va='center', ha='right', zorder=7,
            family='DejaVu Sans')
    if p.items:          # a drawing sheet, not the connection table
        legend(ax, p, oy)


def legend(ax, p, oy):
    lang = getattr(p, 'lang', T.DEFAULT)
    L = lambda k: T.t('scheme.legend.' + k, lang)       # noqa: E731
    if p.kind == 'electric':
        seen, rows = set(), []
        for net in p.nets:
            if net.cls == 'wire' and net.name:
                c = colour('electric', net.name)
                key = {'#8a5a2b': L('phase_l'), '#1d3fbf': L('neutral'), '#2f9e44': L('earth'), INK: L('zero'),
                       '#7b3fa0': L('motor_phases'), '#3d3d3d': L('signals')}.get(c, net.name)
                if (c, key) not in seen:
                    seen.add((c, key))
                    rows.append((c, key))
        rows.append(('mark', L('marks')))
        rows.append(('flag', L('flag')))
    else:
        rows = [('#1c5fd6', L('air')), ('#2f9e44', L('vacuum')), ('#e8590c', L('medium')), ('mark', L('hoses'))]
    x0, y0 = MARGIN + 4, oy + p.h - MARGIN - TB_H + 2
    for i, (c, t) in enumerate(rows[:12]):
        x, y = x0 + 62 * (i // 3), y0 + 4.2 * (i % 3) + 1.5
        if c not in ('mark', 'flag'):
            ax.plot([x, x + 7], [y, y], color=c, lw=1.6, zorder=6)
        ax.text(x + (9 if c not in ('mark', 'flag') else 0), y, t, fontsize=5.5, va='center', zorder=7, family='DejaVu Sans',
                color=INK)


def figure(w, h):
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams.update({'pdf.fonttype': 42, 'svg.fonttype': 'none', 'font.family': 'DejaVu Sans'})
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(w / 25.4, h / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis('off')
    ax.set_aspect('equal')
    return plt, fig, ax


def paint_page(ax, p, n, total, oy=0):
    frame(ax, p, n, total, oy)
    for g, x, y, s in p.items:
        paint_graph(ax, g, x, oy + y, s)


def paint(ps, pdf=None, svg=None, extra=None):
    """The sheets into a PDF (then `extra` figures: the connection table) and one SVG of all of them, one under another."""
    total = len(ps) + len(extra or [])
    if pdf:
        from matplotlib.backends.backend_pdf import PdfPages
        with PdfPages(pdf) as out:
            for i, p in enumerate(ps, 1):
                plt, fig, ax = figure(p.w, p.h)
                paint_page(ax, p, i, total)
                out.savefig(fig)
                plt.close(fig)
            for i, f in enumerate(extra or [], len(ps) + 1):
                out.savefig(f(i, total))
    if svg:
        w, h = max(p.w for p in ps), sum(p.h for p in ps) + GAP * (len(ps) - 1)
        plt, fig, ax = figure(w, h)
        oy = 0
        for i, p in enumerate(ps, 1):
            paint_page(ax, p, i, total, oy)
            oy += p.h + GAP
        fig.savefig(svg, format='svg')
        plt.close(fig)
        t = Path(svg).read_text(encoding='utf-8')         # a browser without DejaVu: Verdana has its widths, then any sans
        Path(svg).write_text(t.replace("font-family: 'DejaVu Sans'", "font-family: 'DejaVu Sans', Verdana, Arial, sans-serif"),
                             encoding='utf-8')
