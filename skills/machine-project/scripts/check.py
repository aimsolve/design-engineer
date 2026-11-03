#!/usr/bin/env python3
"""Conformance report of a machine project against the machine-project standard. Exit 1 on any FAIL (WARN does not fail).

  python3 check.py <project dir> [--no-validate]
  python3 check.py <project dir> --kb        # only the KB lint of wiki/ (kb_lint.py): exit 1 on a KB FAIL
  python3 check.py <project dir> --no-validate --json   # the router's read-only run: one JSON object on stdout

Rules: layout; nothing generated would be committed; nothing over 20 MB would be committed; screenshots only in build/ or outbox/;
AAS present, structurally sound and schema-valid (+ round trip when build/<machine>.aasx exists); wiki names model fields instead of bare
numbers (WARN); firmware folders are PlatformIO projects; model gates (cad_gates.py: CAD layout, kit.json,
bought kind, single-process kits); machine schemes (schemes.py: wires / hoses of the model both ways, no PDF links on the page);
wires in 3D (harness.py: routes, bundles clear of the parts and through the gaps, bend radii, not stretched over the cycle); model fits (fit_checks.py: screw in its hole, docked pairs, bought = drawn,
the maker's drawing on record, the list's thread pairs); KB lint of wiki/ (sizes, raw files, stale/orphan pages; reported as WARN in a
normal run so the exit code is unchanged); wiki/index.md regenerated from page frontmatter on every run. "Would be committed" = git-tracked + untracked-not-ignored files, computed
with git even when the platform keeps the git dir outside the project. Plain python3; the schema validator runs through `uv run --python 3.12`."""
import argparse
import contextlib
import glob
import json
import os
import re
import subprocess
import procgroup
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STEP_SCRIPTS = os.path.join(HERE, '..', '..', 'step-import', 'scripts')
sys.path.insert(0, STEP_SCRIPTS)
import aas_lib  # noqa: E402
sys.path.insert(0, HERE)
import kb_lint  # noqa: E402
import cad_gates  # noqa: E402
import fit_checks  # noqa: E402
import schemes  # noqa: E402
import device  # noqa: E402
import harness  # noqa: E402
import rules_i18n as R  # noqa: E402
import words_i18n as W  # noqa: E402

MAX_BYTES = 20 * 1024 * 1024
GENERATED_EXT = {'.stl', '.3mf', '.dxf', '.glb', '.gltf', '.aasx', '.zip', '.mp4', '.mov', '.webm', '.gcode', '.npz', '.npy', '.html', '.htm'}
HTML_OK = ('docs/', 'wiki/', 'sim/')  # (also any *.tpl.html: templates are sources)        # hand-written pages for people / the sim harness are source
IMG_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
SHOT_RE = re.compile(r'(screenshot|screen[_ -]?shot|preview|render|snapshot|shot[_-]?\d|frame[_-]?\d|_f\d{2,})', re.I)
RUNTIME = ('build/', 'outbox/', 'inbox/', 'jobs/', 'public/', 'app-release/', '.engineer/', '.home/', '.pio/', 'node_modules/', '.git/', '.venv/')
LAYERS = {'raw', 'aas', 'mechanics', 'electronics', 'physics', 'sim', 'docs', 'wiki', 'build', 'outbox', 'inbox', 'jobs', 'public', 'app-release', 'tests', 'viewer'}
UNIT_RE = re.compile(r'(?<![\w`.])\d+(?:[.,]\d+)?\s?(?:' + W.alt('unit') + r')(?![\w])')
results = []


RU = {**R.RULES['ru']['check'], **cad_gates.RU, **fit_checks.RU}   # the rules named for a person: the `ru` of --json


def rec(status, rule, detail='', ru=None):
    results.append((status, rule, detail, ru or next((v for k, v in RU.items() if rule.startswith(k)), rule)))
    print(f'{status:<5} {rule}' + (f': {detail}' if detail else ''))


def run(cmd, **kw):
    return procgroup.run(cmd, capture_output=True, text=True, **kw)


def would_commit(root):
    """(files that git would commit or has tracked, how)"""
    if os.path.isdir(os.path.join(root, '.git')):
        r = run(['git', '-C', root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard'])
        how = 'git ls-files'
    else:
        tmp = tempfile.mkdtemp()
        run(['git', 'init', '-q', '--bare', tmp])
        r = run(['git', f'--git-dir={tmp}', f'--work-tree={root}', 'ls-files', '-z', '--others', '--exclude-standard'])
        how = 'git, .gitignore applied'
    if r.returncode != 0:
        return None, r.stderr.strip() or 'git failed'
    return sorted({f for f in r.stdout.split('\0') if f and os.path.exists(os.path.join(root, f))}), how


def find_aas(root):
    fs = glob.glob(os.path.join(root, 'aas', '*.aas.json'))
    return fs[0] if len(fs) == 1 else None, fs


def validate_json(path):
    code = ("import sys\nfrom aas_test_engines import file as F\n"
            "r = F.check_json_file(open(sys.argv[1], encoding='utf-8'))\n"
            "print('OK' if r.ok() else 'FAIL'); print('\\n'.join(list(r.to_lines())[:15]) if not r.ok() else '')\n")
    try:
        import aas_test_engines  # noqa: F401
        cmd = [sys.executable, '-c', code, path]
    except ImportError:
        cmd = ['uv', 'run', '--quiet', '--python', '3.12', '--with', 'aas-test-engines==1.0.3', 'python', '-c', code, path]
    try:
        r = run(cmd, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return None, f'validator not runnable ({type(e).__name__})'
    out = (r.stdout or '').strip()
    if out.startswith('OK'):
        return True, ''
    if out.startswith('FAIL'):
        return False, re.sub(r'\x1b\[[0-9;]*m', '', out[4:]).strip()
    return None, (r.stderr or out)[-300:]


def structure_problems(d):
    bad = []
    ids = {s['id'] for s in d.get('submodels', [])}
    shells = d.get('assetAdministrationShells', [])
    if len(shells) != 1:
        bad.append(f'{len(shells)} shells, want 1')
    for sh in shells:
        for r in sh.get('submodels', []):
            if r['keys'][0]['value'] not in ids:
                bad.append('shell references missing submodel ' + r['keys'][0]['value'])
    names = {s['idShort'] for s in d.get('submodels', [])}
    for need in ('Nameplate', 'PartCatalog', 'HierarchicalStructures'):
        if need not in names:
            bad.append('submodel ' + need + ' missing')
    return bad


def check(root, do_validate=True, write_index=True):
    name = os.path.basename(root.rstrip('/'))
    print(f'machine-project check: {root}\n')
    # 1. layout
    miss = [f for f in ('README.md', '.gitignore') if not os.path.isfile(os.path.join(root, f))]
    aas_path, aas_all = find_aas(root)
    if not os.path.isdir(os.path.join(root, 'aas')):
        miss.append('aas/')
    if miss:
        rec('FAIL', 'layout', 'missing ' + ', '.join(miss))
    else:
        rec('PASS', 'layout', 'README.md, .gitignore, aas/')
    unknown = sorted(e for e in os.listdir(root) if os.path.isdir(os.path.join(root, e)) and not e.startswith('.') and e not in LAYERS and e != '__pycache__')
    if unknown:
        rec('WARN', 'layout: folders outside the layer set', ', '.join(unknown) + '  (layers: aas mechanics electronics physics sim docs wiki build)')
    for layer in ('mechanics', 'electronics', 'physics', 'sim', 'docs', 'aas'):
        d = os.path.join(root, layer)
        if os.path.isdir(d) and not (os.path.isfile(os.path.join(d, 'README.md')) or os.path.isfile(os.path.join(d, 'README'))):
            rec('WARN', f'layout: {layer}/ has no README.md')
    if not os.path.isfile(os.path.join(root, 'wiki', 'index.md')):
        rec('WARN', 'layout: wiki/index.md missing', 'decisions have no home yet')
    gi = open(os.path.join(root, '.gitignore'), encoding='utf-8').read().split() if os.path.isfile(os.path.join(root, '.gitignore')) else []
    lack = [x for x in ('build/', '.engineer/', '.home/', 'jobs/', 'inbox/', 'outbox/', 'public/', '.pio/') if x not in gi]
    if lack:
        rec('WARN', '.gitignore lacks standard entries', ', '.join(lack))

    files, how = would_commit(root)
    if files is None:
        rec('FAIL', 'git file list', how)
        files = []
    files = [f for f in files if not f.startswith('app-release/')]   # platform-managed (release sources + web output), never the project's own
    # 2. generated files
    gen = []
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext in GENERATED_EXT and not (ext in ('.html', '.htm') and (f.startswith(HTML_OK) or '.tpl.' in os.path.basename(f))):
            gen.append(f)
    rec('FAIL' if gen else 'PASS', 'no generated file types committed', ', '.join(gen[:12]) + (f' (+{len(gen) - 12})' if len(gen) > 12 else '') if gen else f'{len(files)} files, {how}')
    # 3. size
    big = [f'{f} ({os.path.getsize(os.path.join(root, f)) // 1048576} MB)' for f in files if os.path.getsize(os.path.join(root, f)) > MAX_BYTES]
    rec('FAIL' if big else 'PASS', 'no committed file over 20 MB', ', '.join(big))
    # 4. screenshots
    shots = []
    for dp, dn, fn in os.walk(root):
        rel_dir = os.path.relpath(dp, root).replace(os.sep, '/') + '/'
        if rel_dir == './':
            rel_dir = ''
        dn[:] = [d for d in dn if not (rel_dir + d + '/').startswith(RUNTIME) and d not in ('.git', 'node_modules', '__pycache__')]
        for f in fn:
            rel = rel_dir + f
            if os.path.splitext(f)[1].lower() in IMG_EXT and SHOT_RE.search(f) and not rel.startswith(RUNTIME):
                shots.append(rel)
    for f in files:
        if os.path.splitext(f)[1].lower() in IMG_EXT and not f.startswith(RUNTIME) and f not in shots and SHOT_RE.search(os.path.basename(f)):
            shots.append(f)
    rec('FAIL' if shots else 'PASS', 'screenshots/previews only in build/ or outbox/', ', '.join(shots[:12]))
    # 5. AAS
    if aas_path is None:
        rec('FAIL', 'AAS present', 'no aas/<machine>.aas.json' if not aas_all else 'more than one aas/*.aas.json')
    else:
        try:
            d = json.load(open(aas_path, encoding='utf-8'))
        except ValueError as e:
            d = None
            rec('FAIL', 'AAS is valid JSON', str(e))
        if d is not None:
            bad = structure_problems(d)
            rec('FAIL' if bad else 'PASS', 'AAS structure (one shell, submodels resolve, Nameplate/PartCatalog/HierarchicalStructures)', '; '.join(bad))
            if do_validate:
                ok, msg = validate_json(aas_path)
                rec('PASS' if ok else 'WARN' if ok is None else 'FAIL', 'AAS schema (aas-test-engines, JSON)', msg)
            machine = aas_lib.machine_name(root)
            aasx = os.path.join(root, 'build', machine + '.aasx')
            if do_validate and os.path.isfile(aasx):
                cmd = ['uv', 'run', '--quiet', '--python', '3.12', '--with', 'basyx-python-sdk==2.1.0', '--with', 'aas-test-engines==1.0.3',
                       'python', os.path.join(STEP_SCRIPTS, 'aas_check.py'), os.path.join(root, 'build'), '--machine', root]
                try:
                    r = run(cmd, timeout=900)
                    tail = '\n'.join((r.stdout + r.stderr).strip().splitlines()[-6:])
                    rec('PASS' if r.returncode == 0 else 'FAIL', 'AAS round trip + derived files (aas_check.py)', '' if r.returncode == 0 else tail)
                except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                    rec('WARN', 'AAS round trip (aas_check.py) not runnable', type(e).__name__)
            else:
                rec('WARN' if not os.path.isfile(aasx) else 'PASS', 'AAS package build/<machine>.aasx', 'not built yet: schema-only check' if not os.path.isfile(aasx) else 'validated with aas_check')
    # 6. firmware
    fw = os.path.join(root, 'electronics', 'firmware')
    if os.path.isdir(fw):
        nopio = [b for b in sorted(os.listdir(fw)) if os.path.isdir(os.path.join(fw, b)) and not os.path.isfile(os.path.join(fw, b, 'platformio.ini'))]
        rec('FAIL' if nopio else 'PASS', 'firmware: one PlatformIO project per board folder', 'no platformio.ini in: ' + ', '.join(nopio) if nopio else '')
    else:
        rec('PASS', 'firmware: one PlatformIO project per board folder', 'no firmware layer yet')
    # 6a. the model: CAD layout, kit.json, export manifest (cad_gates.py)
    for st, rule, detail in ((r[0], r[1], r[2]) for r in cad_gates.run(root)):
        rec(st, rule, detail)
    # 6a'. machine schemes: connections ↔ the model both ways, no PDF links of the project's own on the page (schemes.py)
    for st, rule, detail in schemes.gate(root):
        rec(st, rule, detail)
    # 6a''. the virtual device: sim parts wired to their MCU board (device.py)
    for st, rule, detail in device.gate(root):
        rec(st, rule, detail)
    # 6a'''. wires in 3D: routes through their waypoints, bundles clear of the parts and through the gaps, bends, motion (harness.py)
    for st, rule, detail in harness.gate(root):
        rec(st, rule, detail)
    # 6b. the model's fits: screw in its hole, docked pairs, bought = drawn (fit_checks.py). The geometric
    # rules read the part STEPs, so a full run computes them (and stores build/model/fit.json) while the router's
    # --no-validate run reports what the last full run recorded.
    for st, rule, detail, ru in fit_checks.run(root, compute=do_validate):
        rec(st, rule, detail, ru)
    # 7. wiki numbers
    wiki_hits = []
    wdir = os.path.join(root, 'wiki')
    for dp, dn, fn in os.walk(wdir):
        dn[:] = [d for d in dn if d != '_templates']
        for f in fn:
            rel = os.path.relpath(os.path.join(dp, f), root).replace(os.sep, '/')
            if not f.endswith('.md') or rel in ('wiki/log.md', 'wiki/index.md') or rel.startswith('wiki/client/'):
                continue          # client quotes are decisions in the original words
            in_fm = False
            for i, line in enumerate(open(os.path.join(dp, f), encoding='utf-8', errors='replace'), 1):
                if i == 1 and line.strip() == '---':
                    in_fm = True
                    continue
                if in_fm:
                    in_fm = line.strip() != '---'
                    continue
                if '«' in line or line.lstrip().startswith(('#', '>')):
                    continue
                m = UNIT_RE.search(re.sub(r'`[^`]*`', lambda x: '`' + 'x' * (len(x.group(0)) - 2) + '`', line))
                # a number is fine when the same line names a model field in backticks (`WIDTH_MM`, `params.x`)
                if m and not re.search(r'`[A-Za-z_][\w.:/-]*`', line):
                    wiki_hits.append(f'{rel}:{i}: {line.strip()[:90]}')
    if os.path.isdir(wdir):
        rec('WARN' if wiki_hits else 'PASS', 'wiki has no bare numbers with units (name the model field instead)', f'{len(wiki_hits)} line(s)' if wiki_hits else '')
        for h in wiki_hits[:20]:
            print('        ' + h)
    kb_report(root, as_warn=True, write_index=write_index)
    fails = [r for r in results if r[0] == 'FAIL']
    warns = [r for r in results if r[0] == 'WARN']
    print(f'\n{"PASS" if not fails else "FAIL"}: {len(fails)} fail, {len(warns)} warn, {sum(1 for r in results if r[0] == "PASS")} pass')
    return 1 if fails else 0


def kb_report(root, as_warn, clients='/clients', write_index=True):
    """KB lint lines; in a normal run every level is reported as WARN (the exit code stays the conformance one)."""
    for lv, rule, detail, items in kb_lint.lint(root, clients):
        rec('WARN' if as_warn else lv, rule + (' [FAIL with --kb]' if as_warn and lv == 'FAIL' else ''), detail)
        for x in items[:10]:
            print('        ' + x)
        if len(items) > 10:
            print(f'        … +{len(items) - 10}')
    idx = write_index and kb_lint.build_index(root)
    if idx:
        print(f'INFO  wiki/index.md regenerated from page frontmatter: {idx}')


def check_kb(root, clients):
    print(f'KB lint: {root}\n')
    kb_report(root, as_warn=False, clients=clients)
    fails = [r for r in results if r[0] == 'FAIL']
    print(f'\n{"PASS" if not fails else "FAIL"}: {len(fails)} fail, {sum(1 for r in results if r[0] == "WARN")} warn (kb)')
    return 1 if fails else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('project')
    ap.add_argument('--no-validate', action='store_true', help='skip the aas-test-engines / aas_check runs (offline)')
    ap.add_argument('--kb', action='store_true', help='run only the KB lint of wiki/ (FAIL rules fail) and regenerate wiki/index.md')
    ap.add_argument('--clients', default='/clients', help='the per-client rules folder (mounted by the router)')
    ap.add_argument('--json', action='store_true', help='read-only run for the router: the report goes to stderr, stdout gets one JSON '
                    'object {result, fails: [{rule, detail, ru}], warns}; wiki/index.md is not rewritten')
    a = ap.parse_args()
    if a.kb:
        sys.exit(check_kb(os.path.abspath(a.project), a.clients))
    if a.json:
        with contextlib.redirect_stdout(sys.stderr):
            code = check(os.path.abspath(a.project), not a.no_validate, write_index=False)
        print(json.dumps({'result': 'FAIL' if code else 'PASS', 'warns': sum(1 for r in results if r[0] == 'WARN'),
                          'fails': [{'rule': r[1], 'detail': r[2], 'ru': r[3]} for r in results if r[0] == 'FAIL']}, ensure_ascii=False))
        sys.exit(code)
    sys.exit(check(os.path.abspath(a.project), not a.no_validate))
