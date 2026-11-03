#!/usr/bin/env python3
"""Machine schemes (electric, pneumatic) for the download panel of the model page: a projection of the
one machine model onto a document, drawn by the framework. Nothing on a sheet is drawn by the project.

The model holds everything:
  - elements = the model's parts whose `kind` is electrical, motors or pneumatic (build/model/parts.json), one per placement,
    addressed by the placement name (the part id for a single placement) - the same id as in the viewer and the purchase list.
    A part of those kinds that is not a scheme element (cable duct, DIN rail, rod end) says `scheme=False`.
  - ports = the part's field `ports` {name: label}, given in its part file: P(..., ports={"+V": "+24 V", "PUL+": "STEP"}).
    A board of our own (KiCad) has its connector pins as ports ("J3.2").
  - connections, once, in mechanics/cad/connections.py (plain Python, may import params):
        WIRES = [{"from": "psu.+V", "to": "drv_x.+V", "net": "+24 V", "spec": "1.0 mm²"}, ...]     # the electric scheme
        HOSES = [{"from": "frl.OUT", "to": "valve_vac.P", "spec": "PU 6x4"}, ...]                   # the pneumatic scheme
    "<placement>.<port>": the placement is what comes before the first dot.

  python schemes.py KIND MANIFEST      (the router's kit unit of the button; cwd = project root; the router builds the model first)
draws build/kit/schemes/<kind>.pdf (the scheme as a drawing, scheme_draw.py: the electric one in symbols and blocks with every wire a
line, the pneumatic one in ISO 1219 symbols, laid out by ELK; then the connection table; the electric one then the harness
drawing when the model routes wires in 3D, harness_draw.py) and <kind>.svg (the drawing sheets alone, for
the model page), and writes MANIFEST {"scheme", "svg", "sheets", "elements", "connections"}.
A kind without connections: {"scheme": null} (the button says the project has none). Bad data: exit 1, no manifest.
Last log line: [schemes] <kind>: elements E, connections C, sheets N -> <pdf>

`gate(root)` is check.py's rule «schemes», both ways: every end of a connection is a port of a model part; every electric /
pneumatic element of the model is on a scheme. Plus: no PDF linked from the page by the project itself (a scheme is a button).
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))      # scheme_draw.py, next to this file
import text_i18n as T  # noqa: E402
KINDS = {'electric': 'WIRES', 'pneumatic': 'HOSES'}
ELEMENT_KINDS = ('electrical', 'motors', 'pneumatic')
CONNECTIONS = 'mechanics/cad/connections.py'
PARTS = 'build/model/parts.json'
OUT = 'build/kit/schemes'
REF = 'skill machine-project «Machine schemes»'
MARK = {'electric': 'W', 'pneumatic': 'H'}
LOADER = """import json, runpy, sys
sys.path.insert(0, 'mechanics/cad')
g = runpy.run_path('mechanics/cad/connections.py')
print(json.dumps({**{k: g.get(k, []) for k in ('WIRES', 'HOSES')}, **{k: g.get(k) or {} for k in ('SIM', 'WAYPOINTS', 'HARNESS')}},
                 ensure_ascii=False, default=str))
"""


# ---------------------------------------------------------------- the model

def load_connections(root, python=sys.executable, timeout=60):
    """{"WIRES": [...], "HOSES": [...], "SIM": {...}, "WAYPOINTS": {...}, "HARNESS": {...}} from connections.py, run in a
    subprocess (it is project code). Raises ValueError. SIM = per-placement settings of the virtual device (device.py);
    WAYPOINTS / HARNESS = the wires' routes in 3D (harness.py)."""
    try:
        r = subprocess.run([python, '-c', LOADER], cwd=root, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValueError(f'{CONNECTIONS} did not finish in {timeout} s')
    if r.returncode:
        raise ValueError(f'{CONNECTIONS} fails: {(r.stderr.strip().splitlines() or ["?"])[-1][:300]}')
    try:
        data = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ValueError(f'{CONNECTIONS} printed something that is not its WIRES / HOSES')
    for k in KINDS.values():
        v = data.get(k)
        if not isinstance(v, list) or not all(isinstance(c, dict) and isinstance(c.get('from'), str) and isinstance(c.get('to'), str)
                                              for c in v):
            raise ValueError(f'{k} in {CONNECTIONS} must be a list of {{"from": "<part>.<port>", "to": "<part>.<port>", "net", "spec"}}')
    return data


def load_parts(root):
    """{placement name: part record} of the built model, or None when it is not built."""
    try:
        ps = json.loads((Path(root) / PARTS).read_text(encoding='utf-8')).get('parts') or []
    except (OSError, ValueError, AttributeError):
        return None
    out = {}
    for p in ps:
        if not isinstance(p, dict):
            continue
        names = [pl.get('name') for pl in p.get('placements') or [] if isinstance(pl, dict) and pl.get('name')] or [p.get('id')]
        for n in names:
            if n:
                out[str(n)] = p
    return out


def model_stale(root):
    """The CAD sources (connections.py, part files, params) changed after the last model build: parts.json would judge them wrong."""
    try:
        built = (Path(root) / PARTS).stat().st_mtime
    except OSError:
        return True
    cad = Path(root) / 'mechanics' / 'cad'
    return any(f.stat().st_mtime > built for f in cad.rglob('*.py') if '__pycache__' not in f.parts and 'vendor' not in f.parts)


def elements(parts):
    """The placements that must be on a scheme."""
    return sorted(n for n, p in parts.items() if p.get('kind') in ELEMENT_KINDS and p.get('scheme') is not False)


def split(end):
    return tuple(end.split('.', 1)) if '.' in end else (end, '')


def problems(parts, conns):
    """Both ways: what does not resolve, and which elements are on no scheme. -> [sentences]"""
    bad, used = [], set()
    for n, p in parts.items():
        ports = p.get('ports')
        if ports is not None and not (isinstance(ports, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in ports.items())):
            bad.append(f'{n}: ports must be {{"name": "label"}}, not {type(ports).__name__}')
            p['ports'] = {}
    for key in KINDS.values():
        seen = set()
        for c in conns[key]:
            pair = frozenset((c['from'], c['to']))
            if c['from'] == c['to'] or pair in seen:
                bad.append(f'{key}: {c["from"]} → {c["to"]}: ' + ('a port wired to itself' if c['from'] == c['to'] else 'listed twice'))
            seen.add(pair)
            for end in (c['from'], c['to']):
                n, port = split(end)
                p = parts.get(n)
                if p is None:
                    bad.append(f'{key}: {end}: no part {n!r} in the model')
                elif port not in (p.get('ports') or {}):
                    bad.append(f'{key}: {end}: part {n!r} has no port {port!r} (its ports: {", ".join(p.get("ports") or {}) or "none"})')
                else:
                    used.add(n)
    loose = [n for n in elements(parts) if n not in used]
    if loose:
        bad.append(f'on no scheme: {", ".join(loose[:12])}{" …" if len(loose) > 12 else ""} — wire them in {CONNECTIONS} '
                   '(ports in their part file) or mark the part `scheme: false` (scheme=False in its part file)')
    return bad


# ---------------------------------------------------------------- the page's own PDF links (step 1 of #54)

LINK_PDF = re.compile(r'^(?!\s*#).*--link[\s=]+(?:"[^"\n]*\.pdf\b|\'[^\'\n]*\.pdf\b|[^"\'\s]*\.pdf\b)', re.I | re.M)   # a --link value, not a comment


def _rel_ok(f):
    return isinstance(f, str) and f.strip() != '' and not f.startswith(('/', '\\', '~')) and not re.match(r'^[A-Za-z]:', f) \
        and '..' not in re.split(r'[\\/]', f)


def site_scripts(root, c):
    """The scripts the kit.json `site` command names: the source of the page."""
    try:
        toks = shlex.split(c.get('site') or '') if isinstance(c, dict) and isinstance(c.get('site'), str) else []
    except ValueError:
        toks = []
    return [Path(root) / t for t in toks if _rel_ok(t) and t.endswith(('.sh', '.py')) and (Path(root) / t).is_file()]


def pdf_links(root, c=None):
    """The page's own links to a PDF, as labels: `--link ….pdf` in the site command / its scripts (web-viewer build.py --link), and
    public/config.json "links" of the last site build unless the scripts changed after it (the rebuild after this turn decides)."""
    src = site_scripts(root, c)
    found = [f'{f.name}: --link …pdf' for f in src if LINK_PDF.search(f.read_text(encoding='utf-8', errors='replace'))]
    if isinstance(c, dict) and isinstance(c.get('site'), str) and LINK_PDF.search(c['site']):
        found.append('kit.json site: --link …pdf')
    cfgp = Path(root) / 'public' / 'config.json'
    try:
        if src and cfgp.stat().st_mtime < max(f.stat().st_mtime for f in src):
            return found
        links = json.loads(cfgp.read_text(encoding='utf-8')).get('links') or []
    except (OSError, ValueError, AttributeError):
        return found
    return found + [str(x.get('label') or x.get('url')) for x in links if isinstance(x, dict)
                    and str(x.get('url', '')).split('?')[0].split('#')[0].lower().endswith('.pdf')]


# ---------------------------------------------------------------- check.py

def gate(root, python=None):
    root = Path(root)
    try:
        c = json.loads((root / 'kit.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        c = None                                    # cad_gates' kit.json rule reports a missing / broken kit.json
    bad, warn, note = [], None, f'no connections in the model ({CONNECTIONS}): no schemes'
    links = pdf_links(root, c)
    if links:
        bad.append(f'the page links PDFs of its own ({", ".join(repr(x) for x in links[:4])}): a machine scheme is drawn by the '
                   f'framework from the model ({CONNECTIONS}) and downloaded from the page\'s download panel; drop the --link from the site build')
    if (root / CONNECTIONS).is_file():
        try:
            conns = load_connections(root, python or sys.executable, timeout=30)
        except ValueError as e:
            bad.append(str(e))
            conns = None
        parts = load_parts(root)
        if conns is not None and parts is None:
            warn = f'{PARTS} not built yet: wires and hoses are checked against the model after the next build'
        elif conns is not None and model_stale(root):
            warn = (f'the model is rebuilt after this turn ({PARTS} is older than the CAD sources): wires and hoses are checked '
                    'against it then')
        elif conns is not None:
            bad += problems(parts, conns)
            note = f'wires {len(conns["WIRES"])}, hoses {len(conns["HOSES"])}: every end a port of the model, every element on a scheme'
    if bad:
        return [('FAIL', 'schemes', '; '.join(bad) + f' ({REF})')]
    return [('WARN', 'schemes', warn)] if warn else [('PASS', 'schemes', note)]


# ---------------------------------------------------------------- the drawing

W, H, ROW = 420, 297, 4.2                # the connection table: A3 landscape, mm


def _title():
    try:
        return str(json.loads(Path('public/config.json').read_text(encoding='utf-8')).get('title') or '').strip()
    except (OSError, ValueError, AttributeError):
        return ''


def _cut(t, n):
    t = str(t)
    return t if len(t) <= n else t[:n - 1] + '…'


def _table(kind, conns, title, lang=None):
    """The connection table after the drawings, as sheet makers f(sheet no, total) -> figure."""
    import scheme_draw as sd
    import text_i18n as T
    lang = lang or T.DEFAULT
    C = lambda k: T.t('scheme.table.' + k, lang)       # noqa: E731
    top, bottom = sd.MARGIN + 8, H - sd.MARGIN - sd.TB_H - 4
    per = int((bottom - top) / ROW) - 1
    cols = [(sd.MARGIN + 6, C('no')), (sd.MARGIN + 22, C('from')), (sd.MARGIN + 132, C('to')), (sd.MARGIN + 242, C('net')),
            (sd.MARGIN + 300, C('spec'))]

    def sheet(i):
        def make(n, total):
            plt, fig, ax = sd.figure(W, H)
            page = sd.Page(W, H)
            page.kind, page.title, page.nets, page.lang = kind, title, [], lang
            sd.frame(ax, page, n, total)
            for cx, t in cols:
                ax.text(cx, top, t, fontsize=7, weight='bold', va='center')
            for k, c in enumerate(conns[i:i + per]):
                yy = top + (k + 1) * ROW
                for (cx, _), v in zip(cols, (f'{MARK[kind]}{i + k + 1}', c['from'], c['to'], str(c.get('net') or ''),
                                             str(c.get('spec') or ''))):
                    ax.text(cx, yy, _cut(v, 60), fontsize=6, va='center')
                ax.plot([sd.MARGIN + 6, W - sd.MARGIN - 6], [yy + ROW / 2] * 2, lw=0.2, color='#bbb')
            plt.close(fig)
            return fig
        return make
    return [sheet(i) for i in range(0, len(conns), per)]


def draw(kind, parts, conns, title, pdf, svg, other=(), lang=None, harness=()):
    """The drawing sheets (scheme_draw.py), then the connection table, then `harness` (sheet makers of harness_draw.py), into
    the PDF; the drawing sheets alone into the SVG the model page shows. lang: the texts on the sheets (default: the project's,
    text_i18n.project_lang). -> (element names, sheet count)"""
    import scheme_draw
    import text_i18n as T
    lang = lang or T.project_lang('.')
    pages = scheme_draw.pages(kind, parts, conns, other, title, lang)
    tables = _table(kind, conns, title, lang) + list(harness)
    Path(pdf).parent.mkdir(parents=True, exist_ok=True)
    parts_ = [Path(str(pdf) + '.part'), Path(str(svg) + '.part')]
    try:
        scheme_draw.paint(pages, pdf=parts_[0], svg=parts_[1], extra=tables)
        os.replace(parts_[0], pdf)
        os.replace(parts_[1], svg)
    finally:
        for f in parts_:
            f.unlink(missing_ok=True)
    return sorted({split(e)[0] for c in conns for e in (c['from'], c['to'])}), len(pages) + len(tables)


def harness_sheets(root, conns):
    """The harness drawing (harness_draw.py) when the model routes wires in 3D (harness.py), else none. A routing that does
    not resolve draws no harness: check.py rule «harness» says why."""
    import harness
    import harness_draw
    try:
        parts = json.loads((Path(root) / PARTS).read_text(encoding='utf-8')).get('parts') or []
        if not harness.has_routes(parts, conns):
            return []
        wires, _ = harness.routes(parts, conns)
        return harness_draw.sheets(wires, harness.config(conns)[0], _title(), T.project_lang(root))
    except Exception as e:   # noqa: BLE001 - the scheme stays; the harness sheets wait for a wiring that resolves
        print(f'[schemes] no harness drawing: {type(e).__name__}: {e}'[:300], flush=True)
        return []


def die(msg):
    print(f'[schemes] FAILED: {msg}', flush=True)
    sys.exit(1)


def build(kind, manifest, root='.'):
    root = Path(root).resolve()
    os.chdir(root)
    if not (root / CONNECTIONS).is_file():
        die(f'no {CONNECTIONS}: the model describes no connections')
    try:
        conns = load_connections(root)
    except ValueError as e:
        die(str(e))
    parts = load_parts(root)
    if parts is None:
        die(f'{PARTS} missing: the model is not built')
    bad = [b for b in problems(parts, conns) if b.startswith(KINDS[kind] + ':') or ': ports must be' in b]   # loose elements: check.py
    if bad and model_stale(root):              # the router rebuilds the model after the turn: not a failure, the next build retries
        print(f'[schemes] the model is older than the CAD sources, waiting for its rebuild: {"; ".join(bad)[:500]}', flush=True)
        sys.exit(124)
    if bad:
        die('; '.join(bad))
    mine = conns[KINDS[kind]]
    out = {'scheme': None, 'sheets': 0, 'elements': [], 'connections': 0}
    if mine:
        pdf, svg = f'{OUT}/{kind}.pdf', f'{OUT}/{kind}.svg'
        other = [c for k, v in KINDS.items() if k != kind for c in conns[v]]
        try:
            names, sheets = draw(kind, parts, mine, _title(), root / pdf, root / svg, other, T.project_lang(root),
                                 harness_sheets(root, conns) if kind == 'electric' else ())
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:      # the layout (node + ELK) failed
            die(f'drawing the {kind} scheme failed: {e}')
        out = {'scheme': pdf, 'svg': svg, 'sheets': sheets, 'elements': names, 'connections': len(mine)}
    m = root / manifest
    m.parent.mkdir(parents=True, exist_ok=True)
    m.with_name(m.name + '.part').write_text(json.dumps(out, ensure_ascii=False))
    os.replace(m.with_name(m.name + '.part'), m)
    print(f'[schemes] {kind}: elements {len(out["elements"])}, connections {out["connections"]}, sheets {out["sheets"]} -> '
          f'{out["scheme"] or "none"}', flush=True)


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in KINDS:
        sys.exit(f'usage: schemes.py {"|".join(KINDS)} MANIFEST   (cwd = project root)')
    build(sys.argv[1], sys.argv[2])
