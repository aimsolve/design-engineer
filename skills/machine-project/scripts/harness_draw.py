"""The harness drawing: the sheets schemes.py adds to the electric scheme's PDF when the model routes wires in 3D (harness.py).
One sheet draws the harness flat - a projection of the routes onto their own best plane, every segment between two
connectors or waypoints a line labelled with its true length (and its bundle's diameter where wires share it), each
connector named with its pins - and the next ones list every wire with its route, loop and cut length.

  sheets(wires, cfg, title, lang) -> [make(sheet no, total) -> figure]      (as schemes._table)
"""
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import text_i18n as T  # noqa: E402

W, H, ROW = 420, 297, 4.2          # A3 landscape, mm


def graph(wires):
    """(nodes {label: (xyz, kind, placement)}, segments {key: {a, b, length_mm, wires}}) of the routed wires: a wire's lead out
    of its port belongs to the segment from that port to the first waypoint."""
    nodes, segs = {}, {}
    for w in wires:
        vs = w['vertices']
        keep = [0] + list(range(2, len(vs) - 2)) + [len(vs) - 1]
        for j, i in enumerate(keep):
            lb = vs[i]['label']
            kind = 'port' if i in (0, len(vs) - 1) else 'waypoint'
            nodes.setdefault(lb, (np.array(vs[i]['at'], float), kind, lb.split('.', 1)[0] if kind == 'port' else None))
            if j:
                a = keep[j - 1]
                L = sum(np.linalg.norm(np.subtract(vs[k + 1]['at'], vs[k]['at'])) for k in range(a, i))
                key = frozenset((vs[a]['label'], lb))
                s = segs.setdefault(key, {'a': vs[a]['label'], 'b': lb, 'length_mm': L, 'wires': []})
                s['wires'].append(w)
    return nodes, segs


def _plane(P):
    """the two axes of the points' best plane (largest spread first)"""
    c = P.mean(0)
    if len(P) < 3:
        return c, np.eye(3)[:2]
    _, _, vt = np.linalg.svd(P - c)
    return c, vt[:2]


def sheets(wires, cfg, title, lang=None):
    import scheme_draw as sd
    import harness
    lang = lang or T.DEFAULT
    wires = [w for w in wires if not w.get('unrouted')]
    if not wires:
        return []
    nodes, segs = graph(wires)

    def page():
        p = sd.Page(W, H)
        p.kind, p.title, p.nets, p.lang = 'harness', title, [], lang
        return p

    def drawing(n, total):
        plt, fig, ax = sd.figure(W, H)
        sd.frame(ax, page(), n, total)
        names = list(nodes)
        P = np.array([nodes[k][0] for k in names])
        c, ax2 = _plane(P)
        Q = (P - c) @ ax2.T
        x0, x1, y0, y1 = sd.MARGIN + 20, W - sd.MARGIN - 20, sd.MARGIN + 24, H - sd.MARGIN - sd.TB_H - 22
        span = np.maximum(Q.max(0) - Q.min(0), 1e-6)
        s = min((x1 - x0) / span[0], (y1 - y0) / span[1])
        mid = (Q.max(0) + Q.min(0)) / 2
        at = {k: ((x0 + x1) / 2 + (q[0] - mid[0]) * s, (y0 + y1) / 2 - (q[1] - mid[1]) * s) for k, q in zip(names, Q)}
        ax.text(sd.MARGIN + 6, sd.MARGIN + 7, T.t('harness.note', lang), fontsize=6.5, color=sd.GREY, va='center')
        for seg in segs.values():
            (xa, ya), (xb, yb) = at[seg['a']], at[seg['b']]
            k = len(seg['wires'])
            ax.plot([xa, xb], [ya, yb], color=sd.INK, lw=min(3.5, 0.7 + 0.35 * k), solid_capstyle='round', zorder=2)
            d = harness.bundle_d([w['od_mm'] for w in seg['wires']])
            lab = f'{seg["length_mm"]:.0f}' + (f' · Ø{d:.1f}' if k > 1 else '')
            ang = math.degrees(math.atan2(-(yb - ya), xb - xa))
            ang = ang - 180 if ang > 90 else ang + 180 if ang < -90 else ang
            ax.text((xa + xb) / 2, (ya + yb) / 2 - 1.8, lab, fontsize=6, ha='center', va='center', rotation=ang,
                    rotation_mode='anchor', color=sd.INK, zorder=4,
                    bbox=dict(boxstyle='square,pad=0.1', fc=sd.PAPER, ec='none'))
        conn, wid = {}, {}
        for w in wires:
            for end in (w['from'], w['to']):
                wid.setdefault(end, []).append(w['id'])
        for k, (_, kind, pl) in nodes.items():
            x, y = at[k]
            if kind == 'waypoint':
                ax.plot([x], [y], 'o', ms=3, mfc=sd.PAPER, mec=sd.INK, mew=0.7, zorder=5)
                ax.text(x + 2, y + 3.2, k, fontsize=5.5, color=sd.GREY, zorder=5)
            else:
                ax.plot([x], [y], 's', ms=2.6, color=sd.INK, zorder=5)
                conn.setdefault(pl, []).append(k)
        for pl, ports in conn.items():      # each pin: its name and the wires on it, on the side away from the harness
            cx = sum(at[k][0] for k in ports) / len(ports)
            out = 1 if cx >= np.mean([at[k][0] for k in nodes if nodes[k][1] == 'waypoint'] or [cx - 1]) else -1
            for k in ports:
                x, y = at[k]
                ax.text(x + out * 6.5, y, f'{k.split(".", 1)[1]} {", ".join(wid[k])}', fontsize=5, color=sd.GREY,
                        ha='left' if out > 0 else 'right', va='center', zorder=6)
        for pl, ports in conn.items():
            xs, ys = zip(*(at[k] for k in ports))
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            pad = 3
            ax.add_patch(plt.Rectangle((min(xs) - pad, min(ys) - pad), max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad,
                                       fill=False, lw=0.8, ec=sd.INK, zorder=3))
            pins = ', '.join(k.split('.', 1)[1] for k in ports)
            ax.text(cx, min(ys) - pad - 2, sd.cut(f'{pl}: {pins}', 60), fontsize=6.5, weight='bold', ha='center', va='bottom',
                    zorder=6, bbox=dict(boxstyle='square,pad=0.15', fc=sd.PAPER, ec='none'))
        plt.close(fig)
        return fig

    C = lambda k: T.t('scheme.table.' + k, lang)       # noqa: E731
    Hc = lambda k: T.t('harness.table.' + k, lang)     # noqa: E731
    cols = [(sd.MARGIN + 6, C('no')), (sd.MARGIN + 20, C('from')), (sd.MARGIN + 95, C('to')), (sd.MARGIN + 170, C('net')),
            (sd.MARGIN + 225, C('spec')), (sd.MARGIN + 270, Hc('od')), (sd.MARGIN + 290, Hc('route')),
            (sd.MARGIN + 320, Hc('loop')), (sd.MARGIN + 350, Hc('cut'))]
    top, bottom = sd.MARGIN + 8, H - sd.MARGIN - sd.TB_H - 10
    per = int((bottom - top) / ROW) - 2

    def table(i):
        def make(n, total):
            plt, fig, ax = sd.figure(W, H)
            sd.frame(ax, page(), n, total)
            for cx, t in cols:
                ax.text(cx, top, t, fontsize=7, weight='bold', va='center')
            rows = wires[i:i + per]
            for k, w in enumerate(rows):
                yy = top + (k + 1) * ROW
                vals = (w['id'], w['from'], w['to'], w['net'], w['spec'], f'{w["od_mm"]:g}', f'{w["length_mm"]:.0f}',
                        f'{w["loop_mm"]:g}' if w['loop_mm'] else '', str(w['cut_mm']))
                for (cx, _), v in zip(cols, vals):
                    ax.text(cx, yy, sd.cut(v, 40), fontsize=6, va='center', weight='bold' if v == vals[-1] else 'normal')
                ax.plot([sd.MARGIN + 6, W - sd.MARGIN - 6], [yy + ROW / 2] * 2, lw=0.2, color='#bbb')
            if i + per >= len(wires):
                yy = top + (len(rows) + 2) * ROW
                ax.text(sd.MARGIN + 6, yy, T.t('harness.total', lang, n=len(wires), m=sum(w['cut_mm'] for w in wires) / 1000,
                                               slack=float(cfg['slack']), ends=float(cfg['ends_mm'])), fontsize=6.5, va='center')
            plt.close(fig)
            return fig
        return make
    return [drawing] + [table(i) for i in range(0, len(wires), per)]
