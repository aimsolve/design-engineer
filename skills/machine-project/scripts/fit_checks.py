#!/usr/bin/env python3
"""Fit checks of a built code model: does the screw fit the hole it is docked into, do docked
parts clash, is the bought part the product its purchase row orders, was that product checked against the maker's drawing.

  python3 fit_checks.py <project dir>             full run: the geometry too (needs build123d), writes build/model/fit.json
  python3 fit_checks.py <project dir> --cached    the geometric rules from build/model/fit.json only, no geometry

check.py calls `run(root, compute=...)` and prints the lines; a FAIL makes it exit 1. The router's fast read-only run
(`check.py --no-validate --json`) passes compute=False, so it reports what the last full run recorded and never
computes geometry; a full run (the agent's `check.py .`) computes and stores it.

Why this exists: a bracket was once drawn from one maker's 3D (a hole for M5) while the purchase list ordered another
product picked by name. The client bought that one, whose maker drawing gives a 4.6 mm hole for M4, and the M5 screw did
not go in. The model was internally consistent; there was no check. These rules are that check (below: the
mismatched-bracket case).

Input: build/model/parts.json and the part STEPs it names (written by `cad_incremental.py --model`); a mesh part (#78,
meshpart.py) is read from its stored triangles and measured on them: its holes off cross sections, its overlaps and depths by
mesh booleans (manifold3d), so a screw in a printed housing is judged by the same rules as one in a STEP part. Nothing here
imports the project's own node code. The joint graph is what makes the checks possible: every placement but the
machine's root carries `joint` {to: <the placement it is docked to>, at: <that part's joint>, by: <its own joint>, type}.

Static rules (parts.json alone, no build123d):
  bought = drawn           a bought part's 3D and the row that orders it name ONE product
  bought part verified     a bought part that mates with something carries its check against the maker's drawing
  bought fastener pairs    every thread size in the list has its other half
Geometric rules (the part STEPs; cached per pair in build/fit/, assembled into build/model/fit.json):
  fastener fit             the screw's diameter against its hole, its length against the depth that is drawn
  docked parts clash       a docked pair does not overlap, except a thread in a nut / T-nut / tapped hole

What these rules deliberately do NOT check: the lateral play of a key or a boss inside a slot (the second half of the
mismatched-bracket case: the bracket's keys rattled in the profile slot). Measuring it needs feature recognition or a clearance computation — a minimum-distance
probe cannot see it, because the parts stay in contact on their mating faces while the key floats in the slot. That is the
clearance check's ground (clearance.py). A model states such a fit the way params.py already does, with an assert on the maker's section.
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rules_i18n as R  # noqa: E402
import words_i18n as W  # noqa: E402

REF = 'skill machine-project'
RU = R.RULES['ru']['fit']        # the rules named for a person: the `ru` of check.py --json

# ---------------------------------------------------------------- tolerances (mm, mm^3)
CLEAR_MIN = 0.1       # a clearance hole must be at least this much wider than the screw
LOOSE = 1.6           # wider than screw + this: the screw rattles, it needs a washer or a smaller hole (WARN)
TAP_LO, TAP_HI = 0.15, 0.25   # a tapped hole is the tap drill (d - pitch) within this band
ENGAGE = 0.8          # a thread must engage at least this many diameters
DEPTH_TOL = 0.2       # a screw may stand this much deeper than the drawn void before it is "bottoming out"
CLASH_V = 2.0         # overlap volume of a docked pair that counts as a clash
GAP_WARN = 0.5        # a rigid docking whose parts do not touch by more than this: placed nominally (WARN)
PROBE_EXTRA = 5.0     # how far past the screw's length the depth probe looks
# ISO 261 coarse pitch: the tap drill of M<d> is d - pitch
PITCH = {1.6: 0.35, 2: 0.4, 2.5: 0.45, 3: 0.5, 3.5: 0.6, 4: 0.7, 4.5: 0.75, 5: 0.8, 6: 1.0, 7: 1.0, 8: 1.25, 10: 1.5,
         12: 1.75, 14: 2.0, 16: 2.0, 18: 2.5, 20: 2.5, 22: 2.5, 24: 3.0}

# M5, M5x12, M1.6x8 (the M and the x in any language's letters), and M12x1.5x40 where the first number after the x is the
# fine pitch and the second the length
_M, _X = W.chars('m_letter'), W.chars('x_letter')
SCREW_RE = re.compile(rf'[{_M}](\d{{1,2}}(?:[.,]\d+)?)\s*(?:[{_X}]\s*(\d+(?:[.,]\d+)?)\s*(?:[{_X}]\s*(\d+(?:[.,]\d+)?))?)?', re.I)
LEN_RE = re.compile(rf'(?:{W.alt("length_word")})\s*(\d+(?:[.,]\d+)?)\s*(?:{W.alt("mm_word")})', re.I)
FEMALE_RE = re.compile(W.alt('female'), re.I)
MALE_RE = re.compile(W.alt('male'), re.I)   # only to READ a list; _is_fastener does not depend on it
WASHER_RE = re.compile(W.alt('washer'), re.I)
# a bought part whose choice has to match something else it touches: a wrong one is the mismatched-bracket case
MATING_KINDS = ('fasteners', 'profile', 'bearings')
# a part made to a standard: the standard IS its drawing, there is no maker's document to check it against
STANDARD_RE = re.compile(rf'\b(?:{W.alt("standard")})\s*\d{{3,5}}\b', re.I)
ANALOGUE_RE = re.compile(W.alt('analogue'), re.I)
# an article as a catalogue spells it (A184, LBSBL6-2028, MGN12H, 17HS4401, KQ2H04-01A); a thread or a section is not one
ARTICLE_RE = re.compile(rf'\b(?![{_M}]\d)(?:[A-Z][A-Z0-9]*[-.]?\d[\w.-]*|\d[\w.-]*[A-Z]{{2,}}[\w.-]*)\b')
# a link the client can order by: a product page, not a search
PRODUCT_RE = re.compile(r'/(?:product|item|p|tovar|goods)/([^/?#]+)', re.I)
SEARCH_RE = re.compile(r'/search|[?&](?:text|q|query|keyword)=', re.I)
# <source>_<article>: a lower-case source name (a part site, a maker, `vendor`) and an underscore before the article; a dash
# is the <maker>-<article> split of _src
SRC_PREFIX = re.compile(r'^([0-9]?[a-z]{3,})_(?=[A-Z0-9])')


def _join(items, n=6):
    return '; '.join(items[:n]) + (f' (+{len(items) - n})' if len(items) > n else '')


def _text(p, *keys):
    """The part's human text, for reading an article or a thread size out of."""
    return ' '.join(str(p.get(k) or '') for k in (keys or ('name', 'title', 'desc', 'code', 'note', 'part')))


def _norm(s):
    """An article as it compares: case, spaces, dots and dashes dropped (LBSBL6-2028 == lbsbl6 2028)."""
    return re.sub(rf'[^0-9{W.chars("letters")}]', '', str(s or '').lower())


BAD_PARTS = []                               # why build/model/parts.json could not be read, for run() to report


def read_parts(root):
    p = os.path.join(root, 'build', 'model', 'parts.json')
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            got = json.load(f)['parts']
        assert isinstance(got, list)
        BAD_PARTS.clear()
        return got
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as e:
        BAD_PARTS[:] = [f'{type(e).__name__}: {e}'[:120]]
        return None


def parts_sha(root):
    h = hashlib.sha256()
    with open(os.path.join(root, 'build', 'model', 'parts.json'), 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ================================================================ static: bought = drawn

def _src(p):
    """The recorded source of a bought part's 3D as {file?, code?, link?, maker?}, or None when the part records none.
    A plain string is the vendor file it was imported from; its stem carries the article (acme-A184.step -> A184,
    vendor-LBSBL6-2028.stp -> LBSBL6-2028), which is how a project adopts the rule without retyping its catalogue."""
    s = p.get('src')
    if not s:
        return None
    if isinstance(s, str):
        s = {'file': s}
    if not isinstance(s, dict):
        return None
    out = dict(s)
    out['explicit'] = bool(out.get('code'))      # a code read out of a file name is a guess, and the report says so
    if not out.get('code') and out.get('file'):
        stem = os.path.splitext(os.path.basename(str(out['file'])))[0]
        m = SRC_PREFIX.match(stem)
        if m:
            out.setdefault('maker', m.group(1))
            stem = stem[m.end():]
        elif '-' in stem:                       # <maker>-<article>: the maker's own file
            out.setdefault('maker', stem.split('-', 1)[0])
            stem = stem.split('-', 1)[1]
        out['code'] = stem
    return out


def _product(link):
    """The product a link orders, as its page id ('' when the link names no single product: a search, or no link)."""
    m = PRODUCT_RE.search(str(link or ''))
    return _norm(m.group(1)) if m and not SEARCH_RE.search(str(link)) else ''


def gate_bought_is_drawn(parts):
    """A bought part is built from the 3D of the product its own row orders. The mismatched-bracket case in one rule:
    the bracket was drawn from one maker's file while its row's link ordered another maker's product.

    A FAIL needs positive evidence that the row orders a DIFFERENT article than the 3D is of - an article-shaped token
    in the row's link or code that is not the drawn one. A marketplace link whose slug simply carries no article
    (`shop.example/product/corner-bracket-20x20-steel-1634859201/`) proves nothing, and a correct model must not fail
    on it, so that is a WARN."""
    fails, warns, ok = [], [], 0
    for p in parts:
        if p.get('process') != 'bought' or p.get('fit') is False:
            continue
        if not (p.get('step') or p.get('mesh')):
            continue                            # a row with no 3D: there is nothing drawn to disagree with it
        pid = str(p.get('id') or p.get('name'))
        src, link, code = _src(p), str(p.get('link') or ''), str(p.get('code') or '')
        want = _product(link)
        other = _analogue_ordered(p, src, code, want)
        if other:
            fails.append(f'{pid}: {other}')
            continue
        if src is None:
            if not STANDARD_RE.search(_text(p)):     # a DIN / ISO / GOST part is drawn from its standard, not a 3D
                warns.append(f'{pid} ({code or "no code"}): no `src`, so nothing says which product the 3D is of')
            continue
        if ANALOGUE_RE.search(_text(p)):
            # `src` is an opt-in statement that the machine's fits are THIS maker's geometry; a purchase text that
            # offers an analogue contradicts it. One of the two has to go, and only a FAIL reaches the agent.
            fails.append(f'{pid}: it is drawn from {src.get("code")} ({src.get("file", "a recorded 3D")}) and its '
                         f'purchase text offers an analogue ({_quote(p)}). The model\'s fits are this maker\'s '
                         f'geometry: order this product, or drop `src` and draw the part from its sizes')
            continue
        art = src.get('code')
        if not art:
            warns.append(f'{pid}: `src` names a file but no article, so the row cannot be matched to it')
            continue
        if _norm(art) in want or (not want and (_norm(art) in _norm(code) or _norm(art) in _norm(link))):
            ok += 1
            continue
        # Which of the row's OWN articles does its link order? That one has to be the drawn one. The mismatched row listed
        # both articles and linked the other one, so matching the code alone would have called it fine.
        ordered = next((a for a in ARTICLE_RE.findall(code) if _norm(a) in want and _norm(a) != _norm(art)), '')
        trusted = src.get('explicit') or _norm(art) in _norm(code)   # a guess the row corroborates is good evidence
        if ordered and trusted:
            fails.append(f'{pid}: the 3D is {src.get("maker", "")} {art} ({src.get("file", "a recorded 3D")}) and the '
                         f'row orders {ordered} ({link}). One product per bought part')
        elif ordered:
            warns.append(f'{pid}: the row orders {ordered}, and {art!r} - read out of the file name of '
                         f'{src.get("file")} - is not in the row at all. Give `src` a `code` and this becomes checkable')
        else:
            warns.append(f'{pid}: 3D {art}, and the row ({code or "no code"}, {link or "no link"}) names no article it '
                         f'can be checked against' + ('' if src.get('explicit') else
                         ' - and `src.code` was read out of the file name, which is a guess'))
    out = []
    if fails:
        out.append(('FAIL', 'bought = drawn', f'{_join(fails, 4)}. A bought part is built from the 3D of the product its '
                    f'row orders; one product per part, no analogues ({REF})'))
    if warns:
        out.append(('WARN', 'bought = drawn', f'{len(warns)} bought part(s) whose 3D cannot be matched to the row: '
                    f'{_join(warns, 5)}. Record it as `src` on the part '
                    f'({{"file": ..., "code": ..., "link": ...}}) and let the row order that product'))
    if not out:
        out.append(('PASS', 'bought = drawn', f'{ok} bought part(s) with 3D: each ordered by the product it is drawn from'))
    return out


def _analogue_ordered(p, src, code, want):
    """The mismatched-bracket case in its own words: the purchase text offers an analogue of the drawn product AND
    what the row orders is that analogue, not the drawn one ("LBSBL6-2028 … (analogue: corner connector 20x20 T01)"
    with a link to T01). This is the path for a part that records no `src`; a part that does record one is held to the
    stricter rule in gate_bought_is_drawn (`src` says the fits are this maker's geometry)."""
    if not want:
        return ''                                # no product link: nothing says WHICH one was ordered
    t = _text(p)
    m = ANALOGUE_RE.search(t)
    if not m:
        return ''
    prim = (src or {}).get('code') or next(iter(ARTICLE_RE.findall(code)), None)
    if not prim or _norm(prim) in want:
        return ''                                # the drawn product is the one ordered: the note is just a note
    for a in ARTICLE_RE.findall(t[m.end():m.end() + 80]) + ARTICLE_RE.findall(code):
        if _norm(a) != _norm(prim) and len(_norm(a)) >= 3 and _norm(a) in want:
            return (f'its text offers an analogue and the row orders that analogue, {a}, not {prim}, which it is drawn '
                    f'from ({link_of(p)}). Draw the product you order')
    return ''


def link_of(p):
    return str(p.get('link') or 'no link')


def _quote(p):
    """The analogue clause as the part writes it, with the field it came from: `_text` concatenates every field, and a
    quote cut out of that concatenation reads as mangled nonsense in a report the router forwards verbatim."""
    for k in ('note', 'desc', 'part', 'title', 'name', 'code'):
        v = str(p.get(k) or '')
        m = ANALOGUE_RE.search(v)
        if m:
            return f'{k}: "{v[max(0, m.start() - 35):m.end() + 35].strip()}"'
    return ''


def gate_bought_verified(parts):
    """a bought part reaches the list only after it was checked against the maker's drawing or STEP — the size under
    the fastener, and that it fits what it touches. The check is recorded on the part (`verified`), so the next agent and
    the manager can see it was made. Only the kinds whose wrong choice costs the client money are asked for it."""
    miss = []
    for p in parts:
        if p.get('process') != 'bought' or p.get('fit') is False:
            continue
        if not (p.get('kind') in MATING_KINDS or _src(p)):
            continue
        if p.get('verified') or (STANDARD_RE.search(_text(p)) and not _src(p)):
            continue                             # a DIN / ISO / GOST part: the standard is the document
        miss.append(f'{p.get("id") or p.get("name")} ({p.get("code") or "no code"})')
    if miss:
        return [('WARN', 'bought part verified', f'{len(miss)} bought part(s) with no check against the maker\'s drawing or '
                 f'STEP on record: {_join(miss, 6)}. Check the size under the fastener and the fit to what it touches '
                 f'against the maker\'s own document, then record it as `verified` on the part '
                 f'({{"against": <drawing or STEP>, "checked": [...], "when": ...}})')]
    return [('PASS', 'bought part verified', 'every mating bought part records its check against the maker\'s document')]


# ================================================================ static: the list's thread pairs

def _thread_size(t):
    m = SCREW_RE.search(t)
    if not m:
        return None
    try:
        return float(m.group(1).replace(',', '.'))
    except ValueError:
        return None


def gate_fastener_pairs(parts):
    """a thread in the purchase list has its other half. A nut with no screw, or a screw with no nut, is not always
    a defect (tapped holes, parts bought as assemblies) — it is always a question the list cannot answer, and the manager
    answers clients out of this list as fact. Both halves of the mismatched-bracket purchase were this shape."""
    male, female, qty = {}, {}, {}
    for p in parts:
        if p.get('process') != 'bought':
            continue
        t = _text(p)
        if WASHER_RE.search(t) and not MALE_RE.search(t):
            continue
        d = _thread_size(t)
        if d is None:
            continue
        n = p.get('qty') or 0
        n = n if isinstance(n, (int, float)) else 0
        if FEMALE_RE.search(t):
            female[d] = female.get(d, 0) + n
        elif MALE_RE.search(t):
            male[d] = male.get(d, 0) + n
        qty[d] = qty.get(d, 0) + n
    odd = []
    for d in sorted(set(male) | set(female)):
        if male.get(d, 0) and not female.get(d, 0):
            odd.append(f'M{d:g}: {male[d]:g} screw(s)/stud(s), no nut, T-nut or rivet nut in the list')
        elif female.get(d, 0) and not male.get(d, 0):
            odd.append(f'M{d:g}: {female[d]:g} nut(s)/rivet nut(s), nothing in the list screws into them')
    if odd:
        return [('WARN', 'bought fastener pairs', f'{_join(odd, 6)}. Either the other half belongs in the list, or the '
                 f'thread is cut in a part of the model and the drawing has to say so')]
    return [('PASS', 'bought fastener pairs', f'{len(set(male) | set(female))} thread size(s), each with both halves')]


# ================================================================ geometry


def _b123d():
    import build123d as B           # noqa: PLC0415 - imported only on a computing run; --cached never touches it
    return B


def _matrix_loc(m):
    from OCP.gp import gp_Trsf       # noqa: PLC0415
    B = _b123d()
    t = gp_Trsf()
    t.SetValues(*list(m)[:12])
    return B.Location(t)


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return tuple(x / n for x in v) if n else (0.0, 0.0, 1.0)


def _joint_axis(loc):
    """(origin, z direction) of a joint's 4x4 row-major matrix, in the coordinates the matrix is written in."""
    m = list(loc)
    return (m[3], m[7], m[11]), _unit((m[2], m[6], m[10]))


def _xform(m, pt):
    m = list(m)
    return tuple(sum(m[r * 4 + c] * pt[c] for c in range(3)) + m[r * 4 + 3] for r in range(3))


def _rotate(m, v):
    m = list(m)
    return _unit(tuple(sum(m[r * 4 + c] * v[c] for c in range(3)) for r in range(3)))


def _inv_dir(m, v):
    """A world direction in the frame of a placement matrix (its rotation is orthonormal, so transpose = inverse)."""
    m = list(m)
    return _unit(tuple(sum(m[r * 4 + c] * v[r] for r in range(3)) for c in range(3)))


def _inv_pt(m, pt):
    m = list(m)
    d = [pt[r] - m[r * 4 + 3] for r in range(3)]
    return tuple(sum(m[r * 4 + c] * d[r] for r in range(3)) for c in range(3))


def screw_spec(p):
    """(nominal diameter, length) of a fastener part out of its own text ("M5x12 DIN 912", "Screw M5 DIN 912" with a length in words,
    "M12x1.5x40" = a fine pitch and then the length), (d, None) when only the thread is spelled, None when the part spells
    no thread at all."""
    t = _text(p)
    m = SCREW_RE.search(t)
    if not m:
        return None
    num = lambda g: float(g.replace(',', '.'))                   # noqa: E731
    d = num(m.group(1))
    L = num(m.group(3)) if m.group(3) else num(m.group(2)) if m.group(2) else None
    if L is None:
        ml = LEN_RE.search(t)
        L = num(ml.group(1)) if ml else None
    return d, L


def tap_drill(d):
    """The drill a tapped M<d> hole is made with (d minus the coarse pitch), None for a size PITCH does not know: a
    guessed pitch lands near `seat_kind`'s band edges and would judge a correct hole."""
    pitch = PITCH.get(d)
    return None if pitch is None else d - pitch


def seat_kind(hole_d, d):
    """How a hole of `hole_d` takes an M<d> screw:
      'free'     a clearance hole (at least d + CLEAR_MIN wide)
      'nominal'  exactly the screw: it goes in, but the clearance is not drawn - a common shortcut, so a WARN, not a FAIL
      'thread'   the tap drill, d - pitch
      'unknown'  PITCH does not know this size, so a tapped hole cannot be told from a wrong one
      None       it takes the screw no way at all - the mismatched-bracket case: M5 into a 4.6 mm hole is too tight to pass and
                 too wide to hold a thread
    """
    if hole_d >= d + CLEAR_MIN:
        return 'free'
    t = tap_drill(d)
    if t is None:
        return 'unknown'
    if t - TAP_LO <= hole_d <= t + TAP_HI:
        return 'thread'
    return 'nominal' if hole_d >= d - 0.05 else None


def axis_holes(shape, origin, into):
    """Every round hole of `shape` coaxial with the ray (origin, into), as [(diameter, t_start, t_end)] sorted by
    t_start, with t measured along `into` from `origin`. t may be negative: a hole is wherever it is drawn. A seat is
    often several of these in a row - a counterbore or a countersink over the shaft hole - which is why they all come
    back and `seat_of` picks the one the screw's shaft has to pass. A cone (a countersink) is reported at its WIDEST
    diameter, so it leads the shaft in without ever being mistaken for the seat."""
    if _is_mesh(shape):
        return _meshpart().axis_holes(shape, origin, into)
    B = _b123d()
    out = []
    for fc in shape.faces():
        if fc.geom_type not in (B.GeomType.CYLINDER, B.GeomType.CONE):
            continue
        try:
            r, ax = fc.radius, fc.axis_of_rotation
        except Exception:                        # noqa: BLE001 - a face OCC cannot describe is no hole
            continue
        if ax is None:
            continue
        if r is None:                            # a cone (a countersink): its widest radius, across the axis
            if fc.geom_type != B.GeomType.CONE:
                continue
            ad0 = _unit((ax.direction.X, ax.direction.Y, ax.direction.Z))
            bb = fc.bounding_box()
            span = ((bb.max.X - bb.min.X), (bb.max.Y - bb.min.Y), (bb.max.Z - bb.min.Z))
            r = max(span[i] for i in range(3) if abs(ad0[i]) < 0.9) / 2 if any(abs(x) < 0.9 for x in ad0) else 0.0
        if r <= 0:
            continue
        ad = _unit((ax.direction.X, ax.direction.Y, ax.direction.Z))
        if abs(sum(a * b for a, b in zip(ad, into))) < 0.999:
            continue                             # not along the screw
        ao = (ax.position.X, ax.position.Y, ax.position.Z)
        off = [a - o for a, o in zip(ao, origin)]
        t = sum(o * z for o, z in zip(off, into))
        if math.sqrt(max(sum(x * x for x in off) - t * t, 0.0)) > 0.05:
            continue                             # another hole, parallel but off this axis
        p = fc.position_at(0.5, 0.5)
        n = fc.normal_at(p)
        to_axis = [a - c for a, c in zip(ao, (p.X, p.Y, p.Z))]
        tt = sum(x * z for x, z in zip(to_axis, into))
        if sum((x - tt * z) * nv for x, z, nv in zip(to_axis, into, (n.X, n.Y, n.Z))) <= 0:
            continue                             # the normal points away from the axis: a boss, not a hole
        ts = [sum((v - o) * z for v, o, z in zip((vx.X, vx.Y, vx.Z), origin, into)) for vx in fc.vertices()]
        if not ts:
            continue
        out.append((2 * r, min(ts), max(ts)))
    return sorted(out, key=lambda h: (h[1], h[0]))


def axis_hole(shape, origin, into):
    """The narrowest coaxial hole on the ray, or None - what a part somewhere along a screw's axis offers it."""
    hs = axis_holes(shape, origin, into)
    return min(hs, key=lambda h: h[0]) if hs else None


def seat_of(holes, lead=0.5):
    """The hole a screw docked at the face (t = 0) actually sits in: the narrowest one the shaft can reach, i.e. the
    narrowest hole such that every millimetre between the face and its start is inside some coaxial hole. A plain
    through hole is itself; under a counterbore or a countersink it is the shaft hole below it, which is the one whose
    diameter has to take the screw. Returns (diameter, t_start, t_end) or None when nothing is reachable from the face.

    Without this a counterbored seat lost the diameter check altogether (the shaft hole does not start at the face), and
    that is the mismatched-bracket case itself: its 4.6 mm under a counterbore would have passed as a WARN."""
    reach, best = lead, None
    for d, t0, t1 in holes:                      # sorted by t_start: walk outward from the face
        if t1 <= 0.0:
            continue                             # drawn on the other side of the face, the screw never meets it
        if t0 > reach:
            break                                # solid material between the face and this hole: the shaft stops there
        reach = max(reach, t1)
        if best is None or d < best[0] - 1e-9:
            best = (d, t0, t1)
    return best


class Geo:
    """The built model's solids: each part STEP read once, and where every placement puts it. World = machine coordinates."""

    def __init__(self, root, parts):
        self.root = root
        self.pls = placements(parts)
        self._s, self._w, self._b = {}, {}, {}
        self._lb = _load_boxes(root)             # part key -> its own-frame box, from the last run
        self._lb_new = False
        self.unread = {}                         # part id -> why its STEP could not be read

    def local_box(self, p):
        """The part's box in its OWN frame, cached on disk by part key. The depth probe needs a box per placement to
        know which parts are near a screw's axis; without this cache one uncached pair imported all 345 part STEPs."""
        k = p['key']
        if k not in self._lb:
            sh = self.solid(p)
            if sh is None:
                self._lb[k] = None
            else:
                bb = sh.bounding_box()
                self._lb[k] = [[bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]]
            self._lb_new = True
        return self._lb[k]

    def flush_boxes(self):
        if self._lb_new:
            _save_boxes(self.root, self._lb)

    def solid(self, p):
        """The part in its own coordinates - the frame its STEP, and so its joints, are written in. None when that STEP
        is missing or unreadable: one bad part costs its own pairs, never the whole report. A mesh part: its triangles."""
        k = p['key']
        if k not in self._s:
            try:
                if p.get('mesh_solid'):
                    mp = _meshpart()
                    self._s[k] = mp.Placed.of(*mp.read(os.path.join(self.root, str(p['mesh']))))
                else:
                    self._s[k] = _b123d().import_step(os.path.join(self.root, str(p['step'])))
            except ImportError:
                raise
            except Exception as e:               # noqa: BLE001
                self._s[k] = None
                self.unread[str(p.get('id') or k)] = f'{type(e).__name__}: {e}'[:120]
        return self._s[k]

    def world(self, name):
        if name not in self._w:
            p, pl = self.pls[name]
            sh = self.solid(p)
            self._w[name] = None if sh is None else sh.moved(pl['loc']) if _is_mesh(sh) else _matrix_loc(pl['loc']) * sh
        return self._w[name]

    def bbox(self, name):
        """The placement's world box, or None when its STEP could not be read. Computed from the part's own-frame box
        (cached) and the placement matrix: the 8 transformed corners, which is a superset of the true world box and so
        is safe for a 'could this be in the way' test."""
        if name not in self._b:
            p, pl = self.pls[name]
            lb = self.local_box(p)
            if lb is None:
                self._b[name] = None
            else:
                (x0, y0, z0), (x1, y1, z1) = lb
                pts = [_xform(pl['loc'], (x, y, z)) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
                self._b[name] = (tuple(min(q[c] for q in pts) for c in range(3)),
                                 tuple(max(q[c] for q in pts) for c in range(3)))
        return self._b[name]

    def on_axis(self, o_w, into, t_hi, r):
        """Placement names whose world box the probe (o_w, into, length t_hi, radius r) could reach. A box test only:
        it keeps a screw's depth probe to the handful of parts around it instead of the whole machine."""
        ends = [tuple(o + t * i for o, i in zip(o_w, into)) for t in (0.0, t_hi)]
        lo = tuple(min(e[c] for e in ends) - r for c in range(3))
        hi = tuple(max(e[c] for e in ends) + r for c in range(3))
        out = []
        for name in self.pls:
            box = self.bbox(name)
            if box and all(lo[c] <= box[1][c] and box[0][c] <= hi[c] for c in range(3)):
                out.append(name)
        return out


def _probe_blocked(geo, name, o_w, into, r, t_hi):
    """How far a shaft of radius `r` reaches from `o_w` along `into` before it meets `name`'s material; t_hi when it
    never does. Bisection on a probe cylinder: the only honest way to say "the seat that is drawn gives 9.5 mm"."""
    B = _b123d()
    shape = geo.world(name)
    pl = B.Plane(origin=B.Vector(*o_w), z_dir=B.Vector(*into))

    def hits(t):
        """True = material, False = clear, None = OCC could not say (which is NOT 'clear')."""
        if t <= 0.02:
            return False
        c = B.Cylinder(r, t, align=(B.Align.CENTER, B.Align.CENTER, B.Align.MIN))
        try:
            v = _vol(_and(pl.location * c, shape))
        except Exception:                        # noqa: BLE001
            return None
        return None if v is None else v > 0.05
    first = hits(t_hi)
    if first is None:
        return None                              # the depth past this part is unknown, so no FAIL may rest on it
    if not first:
        return t_hi
    lo, hi = 0.0, t_hi
    for _ in range(10):
        mid = (lo + hi) / 2
        got = hits(mid)
        if got is None:
            return None
        if got:
            hi = mid
        else:
            lo = mid
    return lo


def _vol(shape):
    """Volume of what a boolean returned, or None when it cannot be read. `a & b` gives a Part in build123d 0.13 but a
    ShapeList in other versions, and a ShapeList carries no `volume` - reading that as 0 is how every overlap went
    unnoticed once, so both shapes are handled, and an unreadable one says so instead of saying zero."""
    if shape is None:
        return None
    v = getattr(shape, 'volume', None)
    if v is not None:
        return float(v)
    try:
        return float(sum(x.volume for x in shape))
    except (TypeError, AttributeError):
        return None


def _is_mesh(x):
    return type(x).__name__ == 'Placed' and type(x).__module__ == 'meshpart'


def _meshpart():
    import meshpart                 # noqa: PLC0415 - numpy / manifold3d, only on a computing run
    return meshpart


def _and(a, b):
    """a & b of two world solids; either may be a mesh part, which OCC cannot boolean: manifold3d does."""
    return _meshpart().common(a, b) if _is_mesh(a) or _is_mesh(b) else a & b


def _overlap(a, b):
    """Overlap volume of two world solids, or None when OCC could not boolean them. None is not 0.0: a pair that could
    not be measured is reported as unmeasured, never as clear."""
    try:
        return _vol(_and(a, b))
    except Exception:                            # noqa: BLE001
        return None


def _gap(a, b):
    """Distance between two world solids, or None when OCC could not measure it."""
    try:
        return float(b.distance_to(a) if _is_mesh(b) and not _is_mesh(a) else a.distance_to(b))
    except Exception:                            # noqa: BLE001
        return None


def _bbox_apart(geo, a, b, tol):
    (a0, a1), (b0, b1) = geo.bbox(a), geo.bbox(b)
    return any(a0[c] - b1[c] > tol or b0[c] - a1[c] > tol for c in range(3))


def _box_overlap(geo, a, b):
    """Volume shared by two placements' world boxes - an upper bound on their real overlap, used only when OCC could
    not boolean them. Touching faces give 0.0 here; one solid inside another gives its box's volume."""
    (a0, a1), (b0, b1) = geo.bbox(a), geo.bbox(b)
    v = 1.0
    for c in range(3):
        v *= max(0.0, min(a1[c], b1[c]) - max(a0[c], b0[c]))
    return v


def _readable(geo, *names):
    return all(geo.bbox(n) is not None for n in names)


def _why_unread(geo, *names):
    ids = {str(geo.pls[n][0].get('id')) for n in names if geo.bbox(n) is None}
    return _join([f'{i}: {geo.unread.get(i, "missing")}' for i in sorted(ids)], 2)


# ---------------------------------------------------------------- the model as docked pairs

def placements(parts):
    """placement name -> (part entry, placement). Parts with a STEP or closed triangles only: a row stands nowhere, a
    view-only mesh is no solid."""
    out = {}
    for p in parts:
        if not (p.get('step') or p.get('mesh_solid')):
            continue
        for pl in p.get('placements') or ():
            if isinstance(pl, dict) and pl.get('name'):
                out[str(pl['name'])] = (p, pl)
    return out


def _is_fastener(p):
    """A male fastener - what `fastener fit` checks and `docked parts clash` leaves to it. Read off the part's own fields,
    not off a vocabulary: `kind` fasteners, a thread size the part spells, and neither a nut nor a washer. A word list
    missed dowel pins, wood screws and "threaded rod", and every male fastener it missed went to the clash rule, which then
    FAILed it for the thread overlap that is its whole purpose."""
    t = _text(p)
    return (p.get('kind') == 'fasteners' and screw_spec(p) is not None
            and not FEMALE_RE.search(t) and not WASHER_RE.search(t))


def _is_female(p):
    return bool(FEMALE_RE.search(_text(p)))


CODE_SHA = None


def _code_sha():
    global CODE_SHA
    if CODE_SHA is None:
        h = hashlib.sha256()
        for p in (os.path.abspath(__file__), os.path.join(os.path.dirname(os.path.abspath(__file__)), 'meshpart.py')):
            with open(p, 'rb') as f:              # a mesh part's holes and overlaps are measured by meshpart.py
                h.update(f.read())
        CODE_SHA = h.hexdigest()[:12]
    return CODE_SHA


def _box_file(root):
    return os.path.join(root, 'build', 'fit', 'boxes.json')


def _load_boxes(root):
    try:
        with open(_box_file(root), encoding='utf-8') as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_boxes(root, boxes):
    try:
        os.makedirs(os.path.dirname(_box_file(root)), exist_ok=True)
        tmp = _box_file(root) + f'.tmp-{os.getpid()}'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(boxes, f)
        os.replace(tmp, _box_file(root))
    except OSError:
        pass


def _pair_key(*bits):
    return hashlib.sha256(json.dumps([_code_sha(), *bits], sort_keys=True, default=str).encode()).hexdigest()[:32]


def _cached(root, key, make, used):
    """One pair's verdict, cached by (the parts' keys, their relative placement, this file's code): a one-part edit
    recomputes that part's pairs only. build/fit/ is this check's own scratch - nothing else GCs it."""
    used.add(key)
    p = os.path.join(root, 'build', 'fit', key + '.json')
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        pass
    v = make()
    if any(x.get('unread') for x in v):
        return v                                 # nothing was measured: a later run with the STEP there must measure it
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + f'.tmp-{os.getpid()}'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(v, f)
        os.replace(tmp, p)
    except OSError:
        pass
    return v


def _axis_neighbours(geo, p, pl, hp, hpl, j, spec):
    """The placements a fastener verdict will measure against, as (part key, placement matrix) pairs for its cache key.
    A clash verdict depends on its two parts alone; a fastener verdict depends on everything on its axis, and leaving
    those out of the key let a real "it bottoms out" FAIL vanish as soon as one of them was edited."""
    d, L = spec
    hj = (hp.get('joints') or {}).get(str(j.get('at')))
    if not isinstance(hj, dict) or not hj.get('loc'):
        return []
    o_loc, z_loc = _joint_axis(hj['loc'])
    o_w = _xform(hpl['loc'], o_loc)
    into = _rotate(hpl['loc'], tuple(-x for x in z_loc))
    t_hi = (L if L is not None else 0.0) + PROBE_EXTRA
    r = max((tap_drill(d) or d) / 2 - 0.05, 0.2)
    out = []
    for other in sorted(geo.on_axis(o_w, into, t_hi, r)):
        op, opl = geo.pls[other]
        out.append([op['key'], opl['loc']])
    return out


def fit_report(root, parts=None):
    """Run the geometric rules and write build/model/fit.json. Needs build123d and the part STEPs."""
    parts = parts if parts is not None else read_parts(root)
    geo = Geo(root, parts)
    out = {'parts_sha': parts_sha(root), 'code_sha': _code_sha(), 'fastener': [], 'clash': [],
           'fastener_pairs': 0, 'clash_pairs': 0}
    used, t0 = set(), time.time()
    for name, (p, pl) in sorted(geo.pls.items()):
        j = pl.get('joint')
        if not isinstance(j, dict) or p.get('fit') is False:
            continue
        host = geo.pls.get(str(j.get('to')))
        if host is None:
            out['clash'].append({'level': 'WARN', 'parts': [name], 'why': f'{name} is docked to '
                                 f'{str(j.get("to"))!r}, which no placement with a STEP in the model answers to: the '
                                 f'pair could not be checked'})
            continue
        if host[0].get('fit') is False:
            continue
        hp, hpl = host
        spec = screw_spec(p) if _is_fastener(p) else None
        bits = [p['key'], hp['key'], pl['loc'], hpl['loc'], str(j.get('at')), str(j.get('by'))]
        if spec is None:
            out['clash_pairs'] += 1
            out['clash'] += _cached(root, _pair_key(*bits), lambda n=name, h=str(hpl['name']), q=p, hq=hp, jj=j:
                                    _check_clash(geo, n, h, q, hq, jj), used)
        else:
            out['fastener_pairs'] += 1
            bits.append(_axis_neighbours(geo, p, pl, hp, hpl, j, spec))
            out['fastener'] += _cached(root, _pair_key(*bits), lambda n=name, h=str(hpl['name']), hq=hp, hl=hpl, jj=j,
                                       sp=spec: _check_fastener(geo, n, h, hq, hl, jj, sp), used)
    geo.flush_boxes()
    if geo.unread:
        out['clash'].append({'level': 'WARN', 'unread': True, 'parts': sorted(geo.unread),
                             'why': f'{len(geo.unread)} part STEP(s) could not be read, so their pairs were not '
                                    f'measured: ' + _join([f'{k}: {v}' for k, v in sorted(geo.unread.items())], 3)})
    out['seconds'] = round(time.time() - t0, 1)
    _prune(root, used, t0)
    d = os.path.join(root, 'build', 'model')
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f'fit.json.tmp-{os.getpid()}')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, os.path.join(d, 'fit.json'))
    return out


def _prune(root, used, started=None):
    """Drop the pair verdicts this run did not use (an older model's, or this file's older code)."""
    started = time.time() if started is None else started
    d = os.path.join(root, 'build', 'fit')
    try:
        names = os.listdir(d)
    except OSError:
        return
    for f in names:
        if not f.endswith('.json') or f == 'boxes.json' or f[:-5] in used:
            continue
        p = os.path.join(d, f)
        try:
            if os.path.getmtime(p) > started:
                continue                         # another run wrote it while this one worked: not ours to drop
            os.remove(p)
        except OSError:
            pass


def _check_fastener(geo, name, host, hp, hpl, j, spec):
    """the screw's diameter against the hole it is docked into, its length against the depth that is drawn.

    The screw is docked by its `head` joint onto a hole joint of the part it goes into, so the joint's origin is the
    head's bearing face and the screw runs from there along -Z of that joint (templates/screw.py)."""
    d, L = spec
    pair = [name, host]
    hj = (hp.get('joints') or {}).get(str(j.get('at')))
    if not isinstance(hj, dict) or not hj.get('loc'):
        return [{'level': 'WARN', 'parts': pair, 'why': f'{name} is docked to joint {j.get("at")!r} of {host}, which '
                                                        f'the model does not describe, so its fit could not be checked'}]
    if not _readable(geo, name, host):
        return [{'level': 'WARN', 'unread': True, 'parts': pair,
                 'why': f'{name} in {host}: a part STEP could not be read ({_why_unread(geo, name, host)})'}]
    o_loc, z_loc = _joint_axis(hj['loc'])
    into_loc = tuple(-x for x in z_loc)
    seat = seat_of(axis_holes(geo.solid(hp), o_loc, into_loc))
    if seat is None:
        return [{'level': 'WARN', 'parts': pair, 'why': f'{host} draws no hole its shaft can reach on the axis of its '
                                                        f'joint {j.get("at")!r}, so the fit of {name} (M{d:g}) could '
                                                        f'not be checked'}]
    hd = seat[0]
    kind = seat_kind(hd, d)
    if kind is None:
        return [{'level': 'FAIL', 'parts': pair, 'hole_d': round(hd, 3), 'screw_d': d,
                 'why': f'{name} is M{d:g} and the hole of {host} at {j.get("at")!r} is {hd:.2f} mm: neither a '
                        f'clearance hole (needs >= {d + CLEAR_MIN:.2f} mm) nor a tapped one '
                        f'(needs {tap_drill(d):.2f} +- {TAP_HI:.2f} mm)'}]
    out = []
    if kind == 'unknown':
        out.append({'level': 'WARN', 'parts': pair, 'why': f'{name} is M{d:g}, a size with no pitch on record here, so '
                                                           f'the {hd:.2f} mm hole of {host} could not be judged'})
    elif kind == 'nominal':
        out.append({'level': 'WARN', 'parts': pair, 'why': f'{name} is M{d:g} and the hole of {host} at '
                                                           f'{j.get("at")!r} is {hd:.2f} mm - the screw itself: draw '
                                                           f'the clearance you mean (>= {d + CLEAR_MIN:.2f} mm)'})
    elif kind == 'free' and hd > d + LOOSE:
        out.append({'level': 'WARN', 'parts': pair, 'why': f'{name} is M{d:g} in a {hd:.2f} mm hole of {host}: '
                                                           f'{hd - d:.2f} mm of play - a washer, or a hole the size of '
                                                           f'the screw'})
    if L is None:
        return out + [{'level': 'WARN', 'parts': pair, 'why': f'{name} does not spell its length, so it could not be '
                                                              f'checked against the depth of its seat in {host}'}]
    # How deep the screw may stand: a shaft the size of its tap drill passes a clearance hole AND a tapped one (it cuts
    # its thread there), so whatever stops that probe is material the screw would bottom out on.
    o_w, into = _xform(hpl['loc'], o_loc), _rotate(hpl['loc'], into_loc)
    r = max((tap_drill(d) or d) / 2 - 0.05, 0.2)
    t_hi = L + PROBE_EXTRA
    avail, by, drawn, engaged, blind = t_hi, None, False, 0.0, []
    for other in geo.on_axis(o_w, into, t_hi, r):
        if other == name:
            continue
        op, opl = geo.pls[other]
        if geo.bbox(other) is None:
            blind.append(other)
            continue
        holes = axis_holes(geo.solid(op), _inv_pt(opl['loc'], o_w), _inv_dir(opl['loc'], into))
        oh = min(holes, key=lambda h: h[0]) if holes else None
        if (oh is not None and seat_kind(oh[0], d) == 'thread') or _is_female(op):
            lo, hi = (oh[1], oh[2]) if oh is not None else (0.0, L)
            engaged += max(0.0, min(hi, L) - max(lo, 0.0))
        got = _probe_blocked(geo, other, o_w, into, r, t_hi)
        if got is None:
            blind.append(other)                  # OCC could not say: no FAIL may rest on this part's depth
            continue
        if got < avail - 1e-9:
            # the depth is "drawn" only when the probe stopped inside a hole this part draws (its blind bottom), not
            # when the part happens to have a coaxial hole somewhere else and solid material where the screw is
            avail, by = got, other
            drawn = any(h[1] <= got + 0.5 and h[2] >= got - 0.5 for h in holes)
    if blind:
        out.append({'level': 'WARN', 'unread': True, 'parts': pair + blind[:2],
                    'why': f'{name}: the depth past {_join(blind, 2)} could not be measured, so its length was not '
                           f'checked against it'})
        return out
    if L > avail + DEPTH_TOL:
        where = pair + ([by] if by and by != host else [])
        if avail < 0.5:                          # material right at the head's bearing face: no seat was measured here
            return out + [{'level': 'WARN', 'parts': where,
                           'why': f'{name} is M{d:g}x{L:g} and there is material right at its head face'
                                  + (f' ({by})' if by else '') + ': the seat is not where the joint puts it, so its '
                                  f'length could not be checked'}]
        why = (f'{name} is M{d:g}x{L:g} and the void drawn from its head face gives {avail:.2f} mm'
               + (f' ({by} stops it)' if by else '') + ': it bottoms out') if drawn else \
              (f'{name} is M{d:g}x{L:g} and the model draws no void past {avail:.2f} mm'
               + (f' ({by} has no hole on the axis)' if by else '') + ': its length could not be checked')
        out.append({'level': 'FAIL' if drawn else 'WARN', 'why': why, 'parts': where,
                    'available': round(avail, 2), 'length': L})
    elif 0.0 < engaged < ENGAGE * d - DEPTH_TOL:
        out.append({'level': 'WARN', 'parts': pair, 'why': f'{name} is M{d:g}x{L:g} with {engaged:.2f} mm of thread '
                                                           f'engaged; {ENGAGE * d:.1f} mm ({ENGAGE:g} x M{d:g}) is the '
                                                           f'least that holds'})
    return out


def _check_clash(geo, name, host, p, hp, j):
    """a docked pair stands against its mate, not inside it. The thread of a screw in a nut, a T-nut or a tapped
    hole is the one overlap a model draws on purpose, and `fastener fit` is what checks that one."""
    if _is_fastener(p) or _is_fastener(hp) or _is_female(p) or _is_female(hp):
        return []
    how = f'docked {j.get("by")!r} -> {j.get("at")!r}'
    pair = [name, host]
    if not _readable(geo, name, host):
        return [{'level': 'WARN', 'unread': True, 'parts': pair,
                 'why': f'{name} / {host}, {how}: a part STEP could not be read ({_why_unread(geo, name, host)})'}]

    def unmeasured(what):
        return [{'level': 'WARN', 'unread': True, 'parts': pair,
                 'why': f'{name} and {host}, {how}: OCC could not {what} them, so the pair was not checked'}]

    def apart():
        g = _gap(geo.world(name), geo.world(host))
        if g is None:
            return unmeasured('measure the distance between')
        return [{'level': 'WARN', 'parts': pair, 'gap': round(g, 3),
                 'why': f'{name} and {host}, {how}, do not touch: a gap of {g:.2f} mm. A joint that is a datum and not '
                        f'a mating face: `fit: false` on the part says so'}]
    if _bbox_apart(geo, name, host, GAP_WARN):
        return apart()                           # boxes that far apart cannot touch: measure the gap, skip the boolean
    v = _overlap(geo.world(name), geo.world(host))
    if v is None:
        # OCC fails on coincident faces, which is the commonest correct case in a docked model, so fall back to the
        # boxes: parts that merely touch share a flat box, parts that interpenetrate share one with volume.
        if _box_overlap(geo, name, host) <= CLASH_V:
            return []
        return unmeasured('intersect')
    if v > CLASH_V:
        return [{'level': 'FAIL', 'parts': pair, 'volume': round(v, 2),
                 'why': f'{name} and {host}, {how}, overlap by {v:.1f} mm3'}]
    if v <= 0.0:
        g = _gap(geo.world(name), geo.world(host))
        if g is None:
            return unmeasured('measure the distance between')
        if g > GAP_WARN:
            return apart()
    return []


# ================================================================ the report

def _lines(items, rule, what, pairs=None):
    """The rule's lines. A PASS says how many pairs it examined: "nothing to report" read the same whether 37 screws
    all fit or no screw was ever looked at, and WARNs do not reach the router, so the count is the honest part."""
    bad = [x for x in items if x.get('level') == 'FAIL']
    warn = [x for x in items if x.get('level') == 'WARN']
    out = []
    if bad:
        out.append(('FAIL', rule, _join([x['why'] for x in bad], 4) + f'. {what}'))
    if warn:
        out.append(('WARN', rule, _join([x['why'] for x in warn], 4)))
    if not out:
        how = f'{pairs} pair(s) checked' if pairs else 'no pair of this kind in the model'
        out.append(('PASS', rule, how))
    return out


def run(root, compute=True):
    """[(status, rule, detail, ru)] of the fit rules. [] when the project has no built model. With compute=False nothing
    here imports build123d: the geometric rules are reported from build/model/fit.json, and WARN when it is not current."""
    parts = read_parts(root)
    if parts is None:
        if not BAD_PARTS:
            return []                            # no model built yet: there is nothing to judge
        bad = BAD_PARTS[0]
        return [(st, rule, detail, RU.get(rule, rule)) for st, rule, detail in
                ((('WARN', r, f'not checked: build/model/parts.json could not be read ({bad})')) for r in RU)]
    res = list(gate_bought_is_drawn(parts)) + list(gate_bought_verified(parts)) + list(gate_fastener_pairs(parts))
    sha = parts_sha(root)
    rep, why = None, ''
    fp = os.path.join(root, 'build', 'model', 'fit.json')
    try:
        with open(fp, encoding='utf-8') as f:
            got = json.load(f)
        # the model AND the rules: a corrected tolerance or a fixed hole finder must not keep reporting the old verdicts
        if got.get('parts_sha') != sha:
            why = 'the stored fit report is of an older model'
        elif got.get('code_sha') != _code_sha():
            why = 'the stored fit report was made by another version of the fit rules'
        else:
            rep = got
    except (OSError, ValueError, AttributeError):
        why = 'no fit report stored yet'
    if rep is None and compute:
        try:
            rep, why = fit_report(root, parts), ''
        except ImportError as e:
            why = f'build123d is not importable here ({e})'
        except Exception as e:                   # noqa: BLE001 - a broken STEP must not take the whole conformance run down
            why = f'the geometric run failed: {type(e).__name__}: {e}'[:200]
    if rep is None:
        res.append(('WARN', 'fastener fit', f'not checked: {why}. Run `python3 {os.path.basename(__file__)} <project>` '
                                           f'(or check.py without --no-validate) where build123d is available'))
        res.append(('WARN', 'docked parts clash', f'not checked: {why}'))
    else:
        res += _lines(rep.get('fastener') or [], 'fastener fit',
                      f'A screw goes into a hole that takes it, in a seat deep enough to hold it ({REF})',
                      rep.get('fastener_pairs'))
        res += _lines(rep.get('clash') or [], 'docked parts clash', f'Dock a part onto its mate, not into it ({REF})',
                      rep.get('clash_pairs'))
    return [(st, rule, detail, RU.get(rule, rule)) for st, rule, detail in res]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('project')
    ap.add_argument('--cached', action='store_true', help='report the geometric rules from build/model/fit.json, compute nothing')
    a = ap.parse_args(argv)
    root = os.path.abspath(a.project)
    res = run(root, compute=not a.cached)
    if not res:
        print('fit checks: no built model (build/model/parts.json): nothing to check')
        return 0
    for st, rule, detail, _ru in res:
        print(f'{st:<5} {rule}' + (f': {detail}' if detail else ''))
    fails = sum(1 for r in res if r[0] == 'FAIL')
    print(f'\n{"FAIL" if fails else "PASS"}: {fails} fail, {sum(1 for r in res if r[0] == "WARN")} warn, '
          f'{sum(1 for r in res if r[0] == "PASS")} pass')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
