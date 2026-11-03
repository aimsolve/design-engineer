#!/usr/bin/env python3
"""The virtual device of a project, as a projection of the model like the schemes (schemes.py).

  python3 device.py <project root>     -> <root>/build/model/device.json (exit 0); problems printed, exit 1

The model holds it all: a part's `sim` field says what it is in the simulation (`{"model": "esp32", "fw": "player"}` = an MCU board running
the firmware build `player`; `{"model": "button"}`, `"encoder"`, `"st7789" | "st7735" | "ili9341"`, `"ssd1306"` = a chip around it, with
its parameters), its `ports` and the WIRES of mechanics/cad/connections.py say where each pin goes. Nets are the WIRES joined at their
ends (a connector port with two wires joins them). A port reaching an MCU port GPIO<n> / IO<n> is that pin; one reaching an MCU port
GND / 3V3 / 5V / VCC, or a wire whose net is named like "GND" / "0 V" / "+3.3 V", is a rail. Per-placement settings that are not part
properties (a keyboard key, a label) go to connections.py `SIM = {"btn_play": {"key": "Space"}}`. The runtime reads device.json
(skills/virtual-controllers runtime/device.mjs), the viewer shows it (web-viewer, its device panel). A project without `sim` parts has no device.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path


def _framework(name):
    """a sibling framework module, by its path: the model build imports project modules in this process, and a project's own
    `schemes.py` / `device.py` must not stand in for the framework's"""
    key = f'_mp_{name}'
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).resolve().parent / f'{name}.py')
        sys.modules[key] = mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return sys.modules[key]


schemes = _framework('schemes')

OUT = 'build/model/device.json'
REF = 'skill machine-project «Virtual device»'
BOARDS = {'esp32', 'avr'}
DISPLAYS = {'st7789', 'st7735', 'ili9341'}
MODELS = {'button', 'encoder', 'ssd1306'} | DISPLAYS
# ports a model needs on an MCU pin / on a rail; the rest of its ports are optional (null when unwired)
NEEDS = {'encoder': (('A', 'B'), ('C',)), 'ssd1306': (('SCL', 'SDA'), ()), **{m: (('SCL', 'SDA', 'DC'), ()) for m in DISPLAYS}}
PIN = re.compile(r'^(?:GPIO|IO)(\d+)$', re.I)
AVR_PIN = re.compile(r'^(?:D(\d+)|A(\d+))$', re.I)          # a Nano / Uno: D<n> = n, A<n> = 14 + n


def _pin(model, port):
    if model == 'avr':
        m = AVR_PIN.match(port)
        return None if not m else int(m.group(1)) if m.group(1) is not None else 14 + int(m.group(2))
    m = PIN.match(port)
    return int(m.group(1)) if m else None
GND_PORT, VCC_PORT = re.compile(r'^GND\w*$', re.I), re.compile(r'^(3V3|3\.3V|5V|VCC|VDD|VIN)$', re.I)
GND_NET, VCC_NET = re.compile(r'^\s*(GND|0\s*V)\s*$', re.I), re.compile(r'^\s*\+?(3[.,]3\s*V|3V3|5\s*V|VCC|VDD)\s*$', re.I)


def _nets(wires):
    """end "<placement>.<port>" -> net id, plus the net names given on its wires"""
    up = {}

    def find(x):
        while up.setdefault(x, x) != x:
            up[x] = up[up[x]]
            x = up[x]
        return x
    for w in wires:
        a, b = find(w['from']), find(w['to'])
        up[a] = b
    names = {}
    for w in wires:
        if w.get('net'):
            names.setdefault(find(w['from']), set()).add(str(w['net']))
    return find, names


def derive(parts, conns):
    """parts = {placement: part record} (schemes.load_parts), conns = {"WIRES", "HOSES", ["SIM"]} -> (device, problems)"""
    sims = {n: p for n, p in parts.items() if isinstance(p.get('sim'), dict)}
    if not sims:
        return {'boards': [], 'elements': []}, []
    wires, overrides, bad = conns.get('WIRES') or [], conns.get('SIM') or {}, []
    find, netnames = _nets(wires)
    boards = [n for n, p in sims.items() if p['sim'].get('model') in BOARDS]
    members = {}
    for w in wires:
        for end in (w['from'], w['to']):
            members.setdefault(find(end), set()).add(end)
    for k in sorted(set(overrides) - set(sims)):
        bad.append(f'SIM[{k!r}] in {schemes.CONNECTIONS}: no part placement {k!r} with a sim field (placements: {", ".join(sorted(sims))})')

    def reach(placement, port):
        """-> ({board: pin}, rail or None) of the net of that end; a net on two pins of one board or on both rails is a problem"""
        net = find(f'{placement}.{port}')
        pins, rails = {}, set()
        for end in sorted(members.get(net, ())):
            n, pt = schemes.split(end)
            if n in boards:
                pin = _pin(sims[n]['sim'].get('model'), pt)
                if pin is not None:
                    pins.setdefault(n, set()).add(pin)
                elif GND_PORT.match(pt):
                    rails.add('gnd')
                elif VCC_PORT.match(pt):
                    rails.add('vcc')
        for name in sorted(netnames.get(net, ())):
            r = 'gnd' if GND_NET.match(name) else 'vcc' if VCC_NET.match(name) else None
            if r:
                rails.add(r)
        for b, ps in sorted(pins.items()):
            if len(ps) > 1:
                bad.append(f'{placement}.{port}: its net reaches pins {", ".join(f"GPIO{p}" for p in sorted(ps))} of board {b} at once (a short in {schemes.CONNECTIONS})')
        if len(rails) > 1:
            bad.append(f'{placement}.{port}: its net is on GND and on 3V3 at once (a short in {schemes.CONNECTIONS})')
        return {b: min(ps) for b, ps in pins.items()}, (min(rails) if rails else None)

    dev = {'boards': [], 'elements': []}
    for n in boards:
        p = sims[n]
        if not p['sim'].get('fw'):
            bad.append(f'{n}: an MCU board in the simulation needs the firmware build it runs: sim={{"model": "{p["sim"]["model"]}", "fw": "<name>"}}')
        dev['boards'].append({'id': n, 'part': p.get('id', n), 'model': p['sim']['model'], 'fw': p['sim'].get('fw')})
    for n, p in sims.items():
        model = p['sim'].get('model')
        if model in BOARDS:
            continue
        if model not in MODELS:
            bad.append(f'{n}: unknown sim model {model!r}; the simulation knows {", ".join(sorted(MODELS | BOARDS))}')
            continue
        if not boards:
            bad.append(f'{n}: a {model} in the simulation, but no part of the model is an MCU board (sim={{"model": "esp32", "fw": ...}})')
            continue
        params = {k: v for k, v in p['sim'].items() if k not in ('model', 'board')}
        params.update(overrides.get(n) or {})
        ports = list((p.get('ports') or {}).keys())
        reached = {pt: reach(n, pt) for pt in ports}
        hint = p['sim'].get('board')
        on = sorted({b for pins, _ in reached.values() for b in pins})
        if hint:
            board = hint
            if hint not in boards:
                bad.append(f'{n}: sim board {hint!r} is not an MCU board of the model ({", ".join(boards)})')
                continue
        elif len(on) > 1:
            bad.append(f'{n}: its ports reach pins of several boards ({", ".join(on)}): say which firmware owns it, '
                       f'board="<id>" in its sim field')
            continue
        else:
            board = on[0] if on else boards[0]
        out = {}
        for pt, (pins, rail) in reached.items():
            out[pt] = {'board': board, 'pin': pins[board]} if board in pins else {'rail': rail} if rail else None
        need_pin, need_rail = NEEDS.get(model, ((), ()))
        if model == 'button' and not any(v and 'pin' in v for v in out.values()):   # the other contact off a rail = pressed reads LOW
            bad.append(f'{n}: a button whose contacts reach no MCU pin: wire one contact to a GPIO in {schemes.CONNECTIONS}')
        for pt in need_pin:
            if pt not in out:
                bad.append(f'{n}: its part has no port {pt} (a {model} needs {", ".join(need_pin)})')
            elif not (out[pt] and 'pin' in out[pt]):
                bad.append(f'{n}: port {pt} of the {model} is not wired to an MCU pin')
        for pt in need_rail:
            if pt in out and not (out[pt] and 'rail' in out[pt]):
                bad.append(f'{n}: port {pt} of the {model} is not wired to GND or 3V3')
        if model == 'encoder' and out.get('SW') and 'pin' in out['SW'] and not (out.get('SW2') and 'rail' in out['SW2']):
            bad.append(f'{n}: the push switch SW is on a pin but SW2 is not on GND or 3V3')
        el = {'id': n, 'part': p.get('id', n), 'model': model, 'board': board, 'params': params, 'ports': out}
        loc = next((pl.get('loc') for pl in p.get('placements') or [] if isinstance(pl, dict) and pl.get('name') == n), None)
        if loc:
            el['loc'] = loc     # where the placement stands in the model (row-major 4x4, mm): the viewer puts a display's glass there
        dev['elements'].append(el)
    return dev, list(dict.fromkeys(bad))       # a short reached from several ports is one problem


def load_connections(root, python=sys.executable, timeout=60):
    """WIRES / HOSES / SIM of connections.py (schemes.load_connections: project code, a subprocess); none -> empty"""
    if not (Path(root) / schemes.CONNECTIONS).is_file():
        return {'WIRES': [], 'HOSES': [], 'SIM': {}}
    return schemes.load_connections(root, python, timeout)


def has_sim(parts):
    return any(isinstance(p.get('sim'), dict) for p in (parts or {}).values())


def write(root, python=sys.executable):
    """derive + write build/model/device.json -> problems. A model without sim parts: no file, connections.py not even run.
    With problems the old device.json goes away too: a page must not run yesterday's wiring."""
    root = Path(root)
    parts = schemes.load_parts(root)
    f = root / OUT
    if not has_sim(parts):
        f.unlink(missing_ok=True)
        return []
    dev, bad = derive(parts, load_connections(root, python))
    f.unlink(missing_ok=True)
    if not bad:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix('.tmp')
        tmp.write_text(json.dumps(dev, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(f)
    return bad


def gate(root, python=None):
    """check.py rule «device»: nothing for a project without sim parts"""
    root = Path(root)
    parts = schemes.load_parts(root)
    if not has_sim(parts):
        return []
    if schemes.model_stale(root):
        return [('WARN', 'device', f'the model is rebuilt after this turn ({schemes.PARTS} is older than the CAD sources): the device is checked then')]
    try:
        dev, bad = derive(parts, load_connections(root, python or sys.executable, timeout=30))
    except ValueError as e:
        dev, bad = None, [str(e)]
    if bad:
        return [('FAIL', 'device', '; '.join(bad) + f' ({REF})')]
    return [('PASS', 'device', f'boards {len(dev["boards"])}, elements {len(dev["elements"])}: every sim part wired to its board')]


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('usage: device.py <project root>')
    try:
        problems = write(sys.argv[1])
    except ValueError as e:
        problems = [str(e)]
    for p in problems:
        print(p)
    sys.exit(1 if problems else 0)
