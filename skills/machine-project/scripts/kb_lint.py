#!/usr/bin/env python3
"""KB lint of a project's wiki (project-wiki skill, kb-critique §5.3) + regeneration of wiki/index.md.

  python3 kb_lint.py <project dir> [--clients /clients]     (check.py --kb runs the same)

Returns a list of (level, rule, detail, examples); level is FAIL or WARN. check.py --kb reports them as they are;
a normal check.py run reports every one as WARN, so the existing exit code never changes."""
import datetime as dt
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import words_i18n as W  # noqa: E402

PAGE_MAX_B, PAGE_MAX_L, ROW_MAX, REG_ROW_MAX, REG_MAX_B = 12 * 1024, 150, 300, 200, 40 * 1024
RULES_MAX_B, RULES_MAX_L, CLIENT_RULES_MAX_B, CLAUDE_MAX_B, STATE_MAX_L, HANDOVER_MAX_B = 6 * 1024, 40, 6 * 1024, 8 * 1024, 5, 8 * 1024
RAW_EXT = {'.txt', '.jsonl', '.json', '.csv', '.log', '.html', '.htm', '.pdf', '.docx', '.xlsx', '.zip'}
RAW_NAME = re.compile(r'(chat[_-]?history|export|transcript|dump)', re.I)
# the client's own confirmations the router reads (pairs of parts he confirmed as meant), data, not raw material
WIKI_DATA = {'wiki/client/clash_accepted.json'}
SKIP_DIRS = {'.obsidian', '_templates', '__pycache__'}
PATH_RE = re.compile(r'(?<![\w/(.-])((?:wiki/)?[\w-]+(?:/[\w-]+)*\.md)\b')   # a page named in plain text or backticks
LINK_RE = re.compile(r'\]\(([^)#\s]+\.md)(#[^)\s]*)?\)')
DATE_RE = re.compile(r'\b(20\d\d)-(\d\d)-(\d\d)\b|\b(\d{1,2})\.(\d{1,2})(?:\.(20\d\d))?\b')
D_ROW = re.compile(r'^(?:\||[-*])?\s*(?:<a id="d\d+"></a>)?\**D(\d+)\b')
SUPERSEDED = re.compile(W.alt('superseded'), re.I)
FIX_REF = re.compile(W.alt('fix_ref'), re.I)
EXEMPT_SIZE = {'wiki/index.md', 'wiki/log.md'}


def _rel(root, p):
    return os.path.relpath(p, root).replace(os.sep, '/')


def wiki_files(root):
    w = os.path.join(root, 'wiki')
    out = []
    for dp, dn, fn in os.walk(w):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
        out += [os.path.join(dp, f) for f in sorted(fn)]
    return out


def split_fm(text):
    """-> (frontmatter dict, body, number of frontmatter lines)"""
    lines = text.split('\n')
    if not lines or lines[0].strip() != '---':
        return {}, text, 0
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            fm = {}
            for ln in lines[1:i]:
                m = re.match(r'^(\w+):\s*(.*)$', ln)
                if m:
                    fm[m.group(1)] = m.group(2).strip().strip('"\'')
            return fm, '\n'.join(lines[i + 1:]), i + 1
    return {}, text, 0


def parse_date(s, year):
    m = DATE_RE.search(s or '')
    if not m:
        return None
    try:
        if m.group(1):
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return dt.date(int(m.group(6) or year), int(m.group(5)), int(m.group(4)))
    except ValueError:
        return None


def is_register(rel):
    return rel == 'wiki/client/decisions.md' or rel.startswith('wiki/client/decisions/')


def _words(s):
    return {w for w in re.findall(rf'[{W.chars("letters")}0-9]{{4,}}', s.lower())}


def lint(root, clients_dir='/clients'):
    res = []

    def add(level, rule, items, detail=''):
        if items:
            res.append((level, rule, detail or f'{len(items)}', items))

    if not os.path.isdir(os.path.join(root, 'wiki')):
        return res
    pages = {}
    raw_inside = []
    for p in wiki_files(root):
        rel = _rel(root, p)
        ext = os.path.splitext(p)[1].lower()
        if (ext in RAW_EXT and rel not in WIKI_DATA) or (ext == '.md' and RAW_NAME.search(os.path.basename(p))):
            raw_inside.append(rel)
        if ext == '.md':
            pages[rel] = open(p, encoding='utf-8', errors='replace').read()
    today = dt.date.today()

    # ---- FAIL rules
    big, long_rows, reg_rows, reg_big = [], [], [], []
    for rel, text in pages.items():
        nb, nl = len(text.encode('utf-8')), text.count('\n') + 1
        if is_register(rel):
            if nb > REG_MAX_B:
                reg_big.append(f'{rel}: {nb // 1024} KB (split by month: wiki/client/decisions/YYYY-MM.md)')
        elif rel not in EXEMPT_SIZE and rel != 'wiki/client/rules.md' and (nb > PAGE_MAX_B or nl > PAGE_MAX_L):
            big.append(f'{rel}: {nb // 1024} KB, {nl} lines')
        if rel == 'wiki/log.md':
            continue
        lim = REG_ROW_MAX if is_register(rel) else ROW_MAX
        for i, ln in enumerate(text.split('\n'), 1):
            if ln.lstrip().startswith('|') and len(ln) > lim:
                (reg_rows if is_register(rel) else long_rows).append(f'{rel}:{i}: {len(ln)} chars')
    add('FAIL', f'kb: page over {PAGE_MAX_B // 1024} KB or {PAGE_MAX_L} lines (split into sub-pages)', big)
    add('FAIL', f'kb: decision register over {REG_MAX_B // 1024} KB', reg_big)
    add('FAIL', f'kb: table row over {ROW_MAX} chars', long_rows)
    add('FAIL', f'kb: decision register row over {REG_ROW_MAX} chars (one line per D; the detail lives on the topic page)', reg_rows)

    rules = pages.get('wiki/client/rules.md')
    if rules is not None:
        _, body, _ = split_fm(rules)
        nb, nl = len(rules.encode('utf-8')), sum(1 for ln in body.split('\n') if ln.strip())
        if nb > RULES_MAX_B or nl > RULES_MAX_L:
            add('FAIL', f'kb: wiki/client/rules.md over {RULES_MAX_B // 1024} KB / {RULES_MAX_L} lines', [f'{nb // 1024} KB, {nl} lines (client-wide rows → /clients/<id>/rules.md)'])
    if os.path.isdir(clients_dir):
        cb = [f'{_rel(clients_dir, f)}: {os.path.getsize(f) // 1024} KB' for f in glob.glob(os.path.join(clients_dir, '*', 'rules.md'))
              if os.path.getsize(f) > CLIENT_RULES_MAX_B]
        add('FAIL', f'kb: client rules over {CLIENT_RULES_MAX_B // 1024} KB', cb)

    cm = os.path.join(root, 'CLAUDE.md')
    if os.path.isfile(cm):
        t = open(cm, encoding='utf-8', errors='replace').read()
        bad = []
        if len(t.encode('utf-8')) > CLAUDE_MAX_B:
            bad.append(f'{len(t.encode("utf-8"))} B > {CLAUDE_MAX_B} B (keep only this project; PRINCIPLES.md covers the rest)')
        m = re.search(r'^#+\s*Current state.*?$(.*?)(?=^#+\s|\Z)', t, re.M | re.S | re.I)
        if m:
            n = sum(1 for ln in m.group(1).split('\n') if ln.strip())
            if n > STATE_MAX_L:
                bad.append(f'«Current state» {n} lines > {STATE_MAX_L} (rewrite it; history goes to wiki/log.md)')
        add('FAIL', 'kb: CLAUDE.md size / state', bad)
    ho = pages.get('wiki/handover.md')
    if ho is not None and len(ho.encode('utf-8')) > HANDOVER_MAX_B:
        add('FAIL', f'kb: wiki/handover.md over {HANDOVER_MAX_B // 1024} KB (overwrite, never append)', [f'{len(ho.encode("utf-8")) // 1024} KB'])
    add('FAIL', 'kb: raw material inside wiki/ (move to raw/)', raw_inside)

    # ---- links graph
    incoming = {rel: set() for rel in pages}
    outgoing = {rel: set() for rel in pages}
    sources = dict(pages)
    if os.path.isfile(cm):
        sources['CLAUDE.md'] = open(cm, encoding='utf-8', errors='replace').read()
    d_refs = {}   # page -> latest date of a D row that links to it
    for src, text in sources.items():
        base = os.path.dirname(src)
        year = today.year
        mm = re.search(r'decisions/(20\d\d)-\d\d', src)
        fm, _, _ = split_fm(text)
        if mm:
            year = int(mm.group(1))
        elif fm.get('updated', '')[:4].isdigit():
            year = int(fm['updated'][:4])
        for ln in text.split('\n'):
            links = LINK_RE.findall(ln)
            dd = parse_date(ln, year) if D_ROW.match(ln.strip()) else None
            mentions = [(t, '') for t in PATH_RE.findall(ln)] if src not in ('wiki/index.md', 'wiki/log.md') else []
            for target, _anchor in links + mentions:
                if '://' in target:
                    continue
                tgt = os.path.normpath(os.path.join(base, target)).replace(os.sep, '/')
                if tgt not in pages:   # a path written from the project root or from wiki/
                    for b in ('', 'wiki'):
                        alt = os.path.normpath(os.path.join(b, target)).replace(os.sep, '/')
                        if alt in pages:
                            tgt = alt
                            break
                if tgt in pages and tgt != src:
                    if src in outgoing:
                        outgoing[src].add(tgt)
                    if src not in ('wiki/index.md', 'wiki/log.md'):
                        incoming[tgt].add(src)
                    if dd:
                        d_refs[tgt] = max(dd, d_refs.get(tgt, dd))
    nav_exempt = {'wiki/index.md', 'wiki/log.md'}
    add('WARN', 'kb: orphan page (no incoming link from another page or CLAUDE.md)',
        sorted(r for r in pages if not incoming[r] and r not in nav_exempt and r != 'wiki/handover.md'))
    add('WARN', 'kb: page without outgoing links', sorted(r for r in pages if not outgoing[r] and r not in nav_exempt))

    # ---- staleness: page older than the latest D row that points at it (register or its own Decisions section)
    stale, nofm = [], []
    for rel, text in pages.items():
        fm, body, _ = split_fm(text)
        if rel in nav_exempt:
            continue
        if not fm.get('updated'):
            nofm.append(rel)
            continue
        up = parse_date(fm['updated'], today.year)
        latest = d_refs.get(rel)
        if not is_register(rel):
            for ln in body.split('\n'):
                if D_ROW.match(ln.strip()):
                    d = parse_date(ln, up.year if up else today.year)
                    if d and d <= today:
                        latest = max(d, latest or d)
        if up and latest and latest > up:
            stale.append(f'{rel}: updated {up}, a decision of {latest} points at it (rewrite CURRENT)')
    add('WARN', 'kb: page older than the latest decision that points at it', sorted(stale))
    add('WARN', 'kb: page without frontmatter (title / summary / updated / status)', sorted(nofm))

    sup = []
    for rel, text in pages.items():
        if not (is_register(rel) or rel.startswith('wiki/client/rules')):
            continue
        for i, ln in enumerate(text.split('\n'), 1):
            if ln.lstrip().startswith('|') and SUPERSEDED.search(ln) and len(ln) > 120:
                sup.append(f'{rel}:{i}')
    add('WARN', 'kb: superseded row not collapsed (one short line: «D12 · superseded by D40»)', sup)

    fb = pages.get('wiki/client/feedback.md')
    if fb is not None:
        _, body, off = split_fm(fb)
        entries, cur = [], None
        for i, ln in enumerate(body.split('\n'), off + 1):
            if ln.startswith('## '):
                cur = [i, ln[3:60], []]
                entries.append(cur)
            elif cur is not None:
                cur[2].append(ln)
        if not entries:   # a table or bullet list: one row per entry
            entries = [[i, ln[:60], [ln]] for i, ln in enumerate(body.split('\n'), off + 1) if re.match(r'^\s*([-*]|\|\s*\d)', ln)]
        nolink = [f'wiki/client/feedback.md:{e[0]}: {e[1].strip()}' for e in entries if not FIX_REF.search(e[1] + '\n'.join(e[2]))]
        add('WARN', 'kb: feedback entry without a rule / gate / fix reference', nolink)

    # R rows repeating the client layer or the principles
    upper = []
    for cand in ('/etc/claude-code/CLAUDE.md', os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'PRINCIPLES.md')):
        if os.path.isfile(cand):
            upper += [ln for ln in open(cand, encoding='utf-8').read().split('\n') if len(_words(ln)) >= 4]
            break
    if os.path.isdir(clients_dir):
        for f in glob.glob(os.path.join(clients_dir, '*', 'rules.md')):
            upper += [ln for ln in open(f, encoding='utf-8', errors='replace').read().split('\n') if len(_words(ln)) >= 4]
    dup = []
    if rules and upper:
        uw = [_words(u) for u in upper]
        for ln in rules.split('\n'):
            m = re.match(r'^\|\s*(?:<a id="r\d+"></a>)?(R\d+)\s*\|\s*([^|]+)', ln)
            if not m:
                continue
            w = _words(m.group(2))
            if len(w) >= 4 and any(len(w & u) / len(w | u) >= 0.45 for u in uw):
                dup.append(f'{m.group(1)}: {m.group(2).strip()[:80]}')
    add('WARN', 'kb: project R row repeats a client rule / principle (replace with a link)', dup)
    return res


# ---- generated index
def page_meta(text, rel):
    fm, body, _ = split_fm(text)
    title = fm.get('title')
    summary = fm.get('summary')
    first = None
    for ln in body.split('\n'):
        s = ln.strip()
        if not s:
            continue
        if s.startswith('# ') and not title:
            title = s[2:].strip()
            continue
        if not s.startswith(('#', '|', '<', '---', '```')) and first is None:
            first = s
        if title and first:
            break
    title = title or os.path.splitext(os.path.basename(rel))[0]
    summary = summary or (first or '')
    summary = re.sub(r'\s+', ' ', summary).lstrip('-* ')
    if len(summary) > 140:
        summary = summary[:137].rstrip() + '…'
    return title, summary, fm.get('updated', ''), fm.get('status', '')


def last_id(text, letter):
    ids = [int(x) for x in re.findall(r'(?:^|\|)\s*(?:<a id="%s\d+"></a>)?\**%s(\d+)\b' % (letter.lower(), letter), text, re.M)]
    return max(ids) if ids else None


def build_index(root):
    w = os.path.join(root, 'wiki')
    if not os.path.isdir(w):
        return None
    pages = {}
    for p in wiki_files(root):
        rel = _rel(root, p)
        if p.endswith('.md') and rel != 'wiki/index.md':
            pages[rel] = open(p, encoding='utf-8', errors='replace').read()
    reg = '\n'.join(t for r, t in pages.items() if is_register(r))
    ranges = []
    for letter, src, name in (('R', pages.get('wiki/client/rules.md', ''), 'rules'), ('D', reg, 'decisions'),
                              ('Q', pages.get('wiki/client/open_questions.md', ''), 'questions')):
        n = last_id(src, letter)
        if n:
            ranges.append(f'{name} {letter}1..{letter}{n}')
    groups = {}
    for rel in sorted(pages):
        parts = rel.split('/')
        g = parts[1] if len(parts) > 2 else ''
        groups.setdefault(g, []).append(rel)
    out = ['# Wiki index', '',
           '<!-- generated by machine-project/scripts/check.py on every run: edit the pages\' frontmatter, not this file -->', '',
           'Live state: [handover.md](handover.md) · change log: [log.md](log.md)' + (' · ' + ', '.join(ranges) if ranges else ''), '']
    order = ['client'] + sorted(g for g in groups if g not in ('client', '')) + ['']
    for g in order:
        if g not in groups:
            continue
        out.append(f'## {g or "reference"}')
        for rel in groups[g]:
            title, summary, up, st = page_meta(pages[rel], rel)
            meta = ' · '.join(x for x in (up, st) if x)
            line = f'- [{title}]({rel[len("wiki/"):]})' + (f': {summary}' if summary else '') + (f' ({meta})' if meta else '')
            out.append(line)
        out.append('')
    text = '\n'.join(out).rstrip() + '\n'
    ip = os.path.join(w, 'index.md')
    old = open(ip, encoding='utf-8', errors='replace').read() if os.path.isfile(ip) else None
    if old != text:
        with open(ip, 'w', encoding='utf-8') as f:
            f.write(text)
        return 'rewritten'
    return 'unchanged'


if __name__ == '__main__':
    import argparse
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument('project')
    ap.add_argument('--clients', default='/clients')
    a = ap.parse_args()
    r = lint(os.path.abspath(a.project), a.clients)
    for lv, rule, d, items in r:
        print(f'{lv:<5} {rule}: {d}')
        for x in items[:15]:
            print('        ' + x)
    print('index:', build_index(os.path.abspath(a.project)))
    sys.exit(1 if any(x[0] == 'FAIL' for x in r) else 0)
