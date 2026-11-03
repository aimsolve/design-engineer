"""The model gates of check.py (SKILL «Code model», «Download kits»; PRINCIPLES §3, §4).
Static: nothing here imports build123d or runs project code. `run(root)` -> [(status, rule, detail, ru)].

Which project has which gates (cad_kind):
  build123d  mechanics/cad/params.py exists, or a .py under mechanics/cad/ (outside vendor/) imports build123d
             -> CAD layout, kit.json, bought kind, single-process kits
  step       a STEP directly in mechanics/cad/ (*.step / *.stp) and no params.py: an imported designer's STEP (skill step-import);
             python scripts next to it are tools that edit that STEP, not a build123d model
             -> kit.json, bought kind, single-process kits (no build123d layout)
  None       no model yet (no mechanics/cad/, or only a README / vendor STEP there) -> no model gates
"""
import ast
import glob
import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rules_i18n as R  # noqa: E402
import words_i18n as W  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CAD = 'mechanics/cad'
KINDS = ('fasteners', 'electrical', 'motors', 'pneumatic', 'bearings', 'profile', 'other')
PROCESSES = ('print', 'laser', 'bent', 'bought', 'other')
MODEL_PROCESSES = PROCESSES + ('mill', 'turn')
SKIP_DIRS = {'build', 'raw', 'vendor', '.engineer', '.home', 'jobs', 'inbox', 'outbox', 'public', '.git', 'node_modules',
             '.pio', '__pycache__', '.venv', 'app-src'}
METAL_RE = re.compile(W.alt('metal'), re.I)
SOFT_RE = re.compile(W.alt('soft'), re.I)
REF = 'skill machine-project'
RU = R.RULES['ru']['cad']        # the rules named for a person: the `ru` of check.py --json


def _join(items, n=6):
    return '; '.join(items[:n]) + (f' (+{len(items) - n})' if len(items) > n else '')


def _read(path, cap=1 << 20):
    """Text of a regular project file ('' for a broken link, a FIFO, a device or an unreadable file); at most `cap` bytes."""
    try:
        if not os.path.isfile(path):
            return ''
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read(cap)
    except OSError:
        return ''


def _tree(path):
    try:
        return ast.parse(_read(path))
    except (SyntaxError, ValueError):
        return None


def _imports(path, names):
    """Does the module import any of `names` (top-level package name), anywhere in its code?"""
    t = _tree(path)
    if t is None:
        src = _read(path)
        return any(re.search(rf'^\s*(?:from|import)\s+{n}\b', src, re.M) for n in names)
    for n in ast.walk(t):
        if isinstance(n, ast.Import) and any(a.name.split('.')[0] in names for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and n.level == 0 and (n.module or '').split('.')[0] in names:
            return True
    return False


def _walk(root, sub=''):
    """Project source files under root/sub (skips build, raw, vendor, runtime and hidden dirs)."""
    for dp, dn, fn in os.walk(os.path.join(root, sub)):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.startswith('.')]
        for f in fn:
            if os.path.isfile(os.path.join(dp, f)):   # regular files only (no broken links, FIFOs, devices)
                yield os.path.join(dp, f)


def b123d_elsewhere(root):
    """build123d model code outside mechanics/cad/ (e.g. an old root cad/ folder): project .py files that import build123d."""
    return [os.path.relpath(p, root).replace(os.sep, '/') for p in _walk(root)
            if p.endswith('.py') and not os.path.relpath(p, root).replace(os.sep, '/').startswith(CAD + '/') and _imports(p, {'build123d'})]


def cad_kind(root):
    d = os.path.join(root, CAD)
    if os.path.isdir(d):
        params = os.path.isfile(os.path.join(d, 'params.py'))
        own_model = params or os.path.isdir(os.path.join(d, 'parts')) or os.path.isfile(os.path.join(d, 'assemble.py'))
        if not own_model and any(f.lower().endswith(('.step', '.stp')) and os.path.isfile(os.path.join(d, f)) for f in os.listdir(d)):
            return 'step'
        if own_model or any(p.endswith('.py') and _imports(p, {'build123d'}) for p in _walk(root, CAD)):
            return 'build123d'
    return 'build123d' if b123d_elsewhere(root) else None


# ---------------------------------------------------------------- CAD layout

def _no_arg(fn):
    a = fn.args
    return len(a.posonlyargs) + len(a.args) <= len(a.defaults) and all(d is not None for d in a.kw_defaults)


def _returns(fn):
    stack = list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(n, ast.Return) and n.value is not None and not (isinstance(n.value, ast.Constant) and n.value.value is None):
            return True
        stack.extend(ast.iter_child_nodes(n))
    return False


def has_build(path):
    t = _tree(path)
    if t is None:
        return False
    return any(isinstance(n, ast.FunctionDef) and (n.name == 'build' or n.name.startswith('build_')) and _no_arg(n) and _returns(n)
               for n in t.body)


def gate_layout(root):
    d = os.path.join(root, CAD)
    bad = []
    away = b123d_elsewhere(root)
    if away and not os.path.isdir(d):
        bad.append(f'build123d model code outside mechanics/cad/ ({_join(away, 3)}): move the model to mechanics/cad/')
    if not os.path.isfile(os.path.join(d, 'params.py')):
        bad.append('no mechanics/cad/params.py: put every number of the model there (UPPER_CASE, unit suffix)')
    pdir = os.path.join(d, 'parts')
    old = sorted(f for f in os.listdir(pdir) if f.endswith('.py') and not f.startswith(('_', '.'))) if os.path.isdir(pdir) else []
    if old:
        bad.append(f'{_join([f"parts/{f}" for f in old])}: a node is a folder parts/<node>/ (__init__.py placing its parts, one '
                   f'<part>.py per part); a helper module is parts/_<name>.py')
    nodes = [f'{f}/__init__.py' for f in node_entries(pdir)]
    if not nodes:
        bad.append('no mechanics/cad/parts/<node>/: split the model into one folder per node, __init__.py with build() placing '
                   f'its parts and one <part>.py per part (assemble.py only collects the nodes; {REF} «Code model»)')
    else:
        nob = [f'parts/{f}' for f in nodes if not has_build(os.path.join(pdir, f))]
        if nob:
            bad.append(f'{_join(nob)} without a no-argument build() / build_*() returning the parts: add it, '
                       'or rename a helper folder to parts/_<name>/')
    if bad:
        return [('FAIL', 'CAD layout', '; '.join(bad))]
    return [('PASS', 'CAD layout', f'params.py + {len(nodes)} node(s) with build()')]


def gate_params_star(root):
    """`from params import *` hides which parameter a file read.

    The incremental build keys every part on the parameters it actually read, and a star import copies the values in: anything
    that cannot carry a mark back to its parameter (a string, a list, an object) is recorded as read by whoever imported it, so
    one changed number rebuilds parts that never looked at it. `import params as P` and `P.NAME` keep the key exact.
    A WARN rather than a FAIL: live projects do it in many files and would stop on every turn.
    """
    d = os.path.join(root, CAD)
    if not os.path.isdir(d):
        return []
    bad = []
    for p in _walk(d):
        rel = os.path.relpath(p, d)
        if not p.endswith('.py') or re.fullmatch(r'parts/.+/params\.py', rel.replace(os.sep, '/')):
            continue                            # a node's params.py taking the machine's in whole is keyed by value: harmless
        t = _tree(p)                            # the syntax tree, not a line match: `from .params import *` (a node's params
        if t is not None and any(               # module) counts, a docstring that mentions the words does not
                isinstance(n, ast.ImportFrom) and any(a.name == '*' for a in n.names)
                and (n.module == 'params' or (n.module or '').endswith('.params')) for n in ast.walk(t)):
            bad.append(rel)
    if not bad:
        return []
    return [('WARN', 'params import *', f'{_join(bad)}: `from params import *` makes the incremental build rebuild parts that '
                                        f'never read the changed number; use `import params as P` and `P.NAME`')]


def node_entries(pdir):
    """Node folders <node>/ (with __init__.py) under parts/, sorted; `_*` are helpers."""
    if not os.path.isdir(pdir):
        return []
    return sorted(f for f in os.listdir(pdir) if not f.startswith(('_', '.')) and os.path.isfile(os.path.join(pdir, f, '__init__.py')))


def _calls(t, name):
    return [n for n in ast.walk(t) if isinstance(n, ast.Call) and (getattr(n.func, 'id', None) == name or getattr(n.func, 'attr', None) == name)]


def _shaped_p(call):
    """A P(name, shape, ...) call whose shape is not a literal None (a part with 3D, as opposed to a purchase-list row)."""
    sh = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == 'shape'), None)
    return sh is not None and not (isinstance(sh, ast.Constant) and sh.value is None)


def _dict_keys(n):
    return {k.value: v for k, v in zip(n.keys, n.values) if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _own_returns(fn):
    """The return statements of `fn` itself, not of the functions and lambdas defined inside it."""
    out, stack = [], list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(n, ast.Return):
            out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return out


def _kit_inputs(cfg):
    """[(where, globs)]: the inputs the router hashes for each kit of kit.json (a scope without its own uses the top-level ones)."""
    top = cfg.get('inputs') or []
    scopes = cfg.get('scopes') or {}
    out = [('inputs', top)] if 'export' in cfg or not scopes else []
    out += [(f'scopes.{k}.inputs', e.get('inputs', top)) for k, e in scopes.items() if isinstance(e, dict)]
    return [(w, [g for g in gl if isinstance(g, str)]) for w, gl in out]


def gate_part_files(root, cfg=None):
    """Folder layout: a node folder's __init__ only places parts (a shaped part comes from its own file), a part file's
    build() returns one part, and qty is never set on a placed part (it is the number of placements; only a row has qty)."""
    pdir = os.path.join(root, CAD, 'parts')
    folders = node_entries(pdir)
    if not folders:
        return []
    bad = []
    for top in folders:
        for dp, dn, fn in os.walk(os.path.join(pdir, top)):
            dn[:] = [x for x in dn if not x.startswith(('.', '_'))]
            for f in sorted(fn):
                if not f.endswith('.py') or f.startswith('_') and f != '__init__.py':
                    continue
                path = os.path.join(dp, f)
                rel = os.path.relpath(path, os.path.join(root, CAD)).replace(os.sep, '/')
                t = _tree(path)
                if t is None:
                    continue
                if f == '__init__.py':
                    for c in _calls(t, 'P'):
                        if _shaped_p(c):
                            bad.append(f'{rel}:{c.lineno} a node returns a part with a shape: move it to its own part file in the node folder')
                    for n in ast.walk(t):
                        if isinstance(n, ast.Dict):
                            keys = _dict_keys(n)
                            if 'shape' in keys and not (isinstance(keys['shape'], ast.Constant) and keys['shape'].value is None):
                                bad.append(f'{rel}:{n.lineno} a node returns a part dict with a shape: move it to its own part file')
                            if 'qty' in keys and None in n.keys:
                                bad.append(f'{rel}:{n.lineno} qty set on a placed copy: qty is the number of placements, place it again')
                    continue
                for fnode in t.body:
                    if isinstance(fnode, ast.FunctionDef) and fnode.name == 'build':
                        for r in _own_returns(fnode):
                            if isinstance(r.value, (ast.List, ast.ListComp, ast.Tuple)):
                                bad.append(f'{rel}:{r.lineno} a part file returns a list: one part per file (a node places several)')
                for c in _calls(t, 'P'):
                    bought = any(k.arg == 'process' and isinstance(k.value, ast.Constant) and k.value.value == 'bought' for k in c.keywords) or \
                        len(c.args) > 4 and isinstance(c.args[4], ast.Constant) and c.args[4].value == 'bought'
                    if _shaped_p(c) and any(k.arg == 'qty' for k in c.keywords) and not bought:
                        bad.append(f'{rel}:{c.lineno} qty on a placed part: drop it, qty is the number of placements')
                for n in ast.walk(t):
                    k_ = _dict_keys(n) if isinstance(n, ast.Dict) else {}
                    if 'qty' in k_ and 'shape' in k_ and not (isinstance(k_.get('process'), ast.Constant) and k_['process'].value == 'bought'):
                        bad.append(f'{rel}:{n.lineno} qty on a placed part: drop it, qty is the number of placements')
    if cfg is not None:                 # the router hashes kit.json inputs to tell a stale download: part files must be in them
        files = [p for top in folders for p in glob.glob(os.path.join(pdir, top, '**', '*.py'), recursive=True)]
        for where, gl in _kit_inputs(cfg):
            covered = {os.path.realpath(x) for g in gl for x in glob.glob(os.path.join(root, g), recursive=True)}
            miss = [os.path.relpath(p, root).replace(os.sep, '/') for p in files if os.path.realpath(p) not in covered]
            if miss:
                bad.append(f'kit.json {where} miss {_join(miss, 3)}: an edit there would not refresh the downloads; '
                           f'list "mechanics/cad/parts/**/*.py"')
    if bad:
        return [('FAIL', 'part files', _join(list(dict.fromkeys(bad)), 5) + f' ({REF} «Code model»)')]
    return [('PASS', 'part files', f'{len(folders)} node folder(s): one part per file, nodes only place')]


# ---------------------------------------------------------------- kit.json

def _rel_ok(f):
    return isinstance(f, str) and f.strip() != '' and not f.startswith(('/', '\\', '~')) and not re.match(r'^[A-Za-z]:', f) \
        and '..' not in re.split(r'[\\/]', f)


def gate_kit(root):
    """-> (results, cfg or None)."""
    p = os.path.join(root, 'kit.json')
    fix = f'({REF} «Download kits»)'
    if not os.path.isfile(p):
        return [('FAIL', 'kit.json', 'no kit.json at the project root: add {"export": "<command>", "inputs": [globs], '
                 f'"manifest": "build/kit/manifest.json", "timeout": 3600}} so the model page builds the download kits {fix}')], None
    try:
        with open(p, encoding='utf-8') as f:
            c = json.load(f)
    except (OSError, ValueError) as e:
        return [('FAIL', 'kit.json', f'not valid JSON ({str(e)[:80]}): fix it {fix}')], None
    if not isinstance(c, dict):
        return [('FAIL', 'kit.json', f'not a JSON object: write {{"export", "inputs", "manifest", "timeout"}} {fix}')], None
    bad = []
    scopes = c.get('scopes', {})
    if 'export' in c or 'scopes' not in c:   # the full export: required without `scopes`, optional (but valid) with them
        _kit_entry(c, 'build/kit/manifest.json', bad, '', need_inputs=True)
    if not isinstance(scopes, dict) or ('scopes' in c and not scopes and 'export' not in c):
        bad.append('"scopes" must be an object {"bought"|"step"|"print"|"laser"|"assembly": {"export", "inputs", ...}}')
        scopes = {}
    for s, e in scopes.items():
        if s not in KIT_SCOPES + ('assembly',) or not isinstance(e, dict):
            bad.append(f'scopes.{s}: unknown scope or not an object (scopes are {", ".join(KIT_SCOPES + ("assembly",))})')
            continue
        _kit_entry(dict(e, inputs=e.get('inputs', c.get('inputs'))), f'build/kit/{s}.json', bad, f'scopes.{s}.', need_inputs=True)
        for k in ('part', 'node'):
            if k in e and (s not in ('print', 'laser') or not isinstance(e[k], str) or '{' + k + '}' not in e[k]):
                bad.append(f'scopes.{s}.{k} must be a command containing {{{k}}} (print and laser only)')
        for k in ('export', 'part', 'node'):
            if isinstance(e.get(k), str) and QUOTED_PH.search(e[k]):
                bad.append(f'scopes.{s}.{k}: write {{part}} / {{node}} / {{manifest}} bare, without quotes (the router quotes them)')
    t = c.get('timeout')
    if t is not None and (isinstance(t, bool) or not isinstance(t, int) or t <= 0):
        bad.append('"timeout" must be a positive whole number of seconds')
    if bad:
        return [('FAIL', 'kit.json', '; '.join(bad) + ' ' + fix)], None
    c['manifest'] = c.get('manifest', 'build/kit/manifest.json')
    c['manifests'] = ([c['manifest']] if 'export' in c else []) + [e.get('manifest', f'build/kit/{s}.json') for s, e in scopes.items()]
    c['commands'] = ([('kit.json export', c['export'])] if 'export' in c else []) + \
        [(f'kit.json scopes.{s}.{k}', e[k]) for s, e in scopes.items() for k in ('export', 'part', 'node') if k in e]
    res = [('PASS', 'kit.json', 'export, inputs, manifest' + (f'; scopes {", ".join(scopes)}' if scopes else ''))]
    if 'export' in c and all(s in scopes for s in KIT_SCOPES):
        res.append(('WARN', 'kit.json', 'every scope has its own export: the top-level "export" is never run, drop it'))
    return res, c


KIT_SCOPES = ('bought', 'step', 'print', 'laser')   # the ones the full export builds; the router also takes a declared
# 'assembly' scope, which overrides its own assembly-drawings unit and is never part of the full export
QUOTED_PH = re.compile(r"""['"]\{(part|node|manifest)\}|\{(part|node|manifest)\}['"]""")


def _kit_entry(c, default_man, bad, where, need_inputs):
    """Check one export entry of kit.json (the top level or a scope), appending problems to `bad`."""
    if not isinstance(c.get('export'), str) or not c['export'].strip():
        bad.append(f'"{where}export" must be the shell command that writes ' + ('this scope\'s files and its manifest piece' if where
                                                                                 else 'every kit file and the manifest'))
    man = c.get('manifest', default_man)
    if not _rel_ok(man):
        bad.append(f'"{where}manifest" must be a path inside the project (no absolute path, no ..)')
        man = default_man
    inputs = c.get('inputs')
    if inputs is None and not need_inputs:
        return
    if not isinstance(inputs, list) or not inputs:
        bad.append(f'"{where}inputs" must list the globs of every file the parts come from')
        return
    odd = [str(g) for g in inputs if not _rel_ok(g)]
    if odd:
        bad.append(f'"{where}inputs" entries must be project-relative globs: ' + ', '.join(odd[:4]))
    mdir = os.path.dirname(os.path.normpath(man)).replace('\\', '/')
    own = [g for g in inputs if isinstance(g, str) and mdir and mdir not in ('.', 'build')
           and (os.path.normpath(g).replace('\\', '/') + '/').startswith(mdir + '/')]
    if own:
        bad.append(f'"{where}inputs" lists the export\'s own output ({", ".join(own[:3])}): drop it')
    t = c.get('timeout')
    if where and t is not None and (isinstance(t, bool) or not isinstance(t, int) or t <= 0):
        bad.append(f'"{where}timeout" must be a positive whole number of seconds')


# ---------------------------------------------------------------- shell commands of the kit

def _lines(text):
    """Shell lines with continuations joined and heredoc bodies dropped (`<<<` here-strings and `1<<3` shifts are not heredocs)."""
    out, it = [], iter(text.replace('\\\n', ' ').splitlines())
    for line in it:
        out.append(line)
        m = re.search(r"(?<![<\w$])<<(?!<)-?\s*['\"]?([A-Za-z_]\w*)['\"]?", line)
        if m:
            for body in it:
                if body.strip() == m[1]:
                    break
    return out


def _tokens(line):
    line = re.sub(r'(^|\s)#.*$', '', line)   # a comment starts at a word; `${F#pre}` is not one
    try:
        lx = shlex.shlex(line, posix=True, punctuation_chars=';&|()<>')
        lx.whitespace_split = True
        lx.commenters = ''
        return list(lx)
    except ValueError:
        return line.split()


def _resolve(tok, bases, root):
    if '$' in tok or '`' in tok or tok.startswith(('/', '~')):
        return None
    for b in bases:
        p = os.path.realpath(os.path.join(b, tok))
        if os.path.isfile(p) and p.startswith(os.path.realpath(root) + os.sep):
            rel = os.path.relpath(p, os.path.realpath(root)).replace(os.sep, '/')
            if not set(rel.split('/')[:-1]) & SKIP_DIRS:
                return p
    return None


OPS = {';', '&&', '||', '|', '&', '(', ')', '!', 'then', 'do', 'else', 'elif', 'exec', 'time', 'nice', 'xargs'}
NOT_RUN = {'echo', 'printf', 'print', 'log', 'say', 'cat', 'grep', 'test', '['}


def scan(text, where, root, depth=0):
    """-> the project shell scripts [paths] one shell text runs. A quoted argument that holds a command (`job run "…"`,
    `bash -c '…'`, `tg job submit "…"`) is scanned as shell text too."""
    bases = [os.path.dirname(where), root, os.path.join(root, CAD)] if where else [root, os.path.join(root, CAD)]
    shs = []
    for line in _lines(text):
        toks, seg = _tokens(line), 0
        for i, t in enumerate(toks):
            if t in OPS:
                seg = i + 1
                continue
            if (toks[seg] if seg < len(toks) else '') in NOT_RUN:   # echo / grep / test of a command is not a run
                continue
            if depth < 3 and ' ' in t and '.sh' in t:
                shs += scan(t, where, root, depth + 1)
                continue
            if t.endswith('.sh'):
                p = _resolve(t, bases, root)
                if p:
                    shs.append(p)
    return shs


# ---------------------------------------------------------------- code-model: parts and derive.py

def is_code_model(root):
    """A project already on the model/derive scheme: mechanics/cad/assemble.py + parts/ nodes AND (kit.json calls derive.py or the
    model is built, build/model/parts.json). Opt-in: an old-template project (assemble.py, parts/, cad_incremental kit) is not judged yet."""
    d = os.path.join(root, CAD)
    if not (os.path.isfile(os.path.join(d, 'assemble.py')) and os.path.isdir(os.path.join(d, 'parts'))):
        return False
    return os.path.isfile(os.path.join(root, 'build', 'model', 'parts.json')) or 'derive.py' in _read(os.path.join(root, 'kit.json'))


def _part_dicts(path):
    """[(line, name constant or None, has process key)] of every dict literal in the module that looks like a part (has a
    'name' and a 'shape' key). A dict with a `**` spread may carry process from elsewhere: counted as having it."""
    t = _tree(path)
    out = []
    for n in ast.walk(t) if t else ():
        if isinstance(n, ast.Dict):
            keys = {k.value for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if {'name', 'shape'} <= keys:
                name = next((v.value for k, v in zip(n.keys, n.values) if isinstance(k, ast.Constant) and k.value == 'name'
                             and isinstance(v, ast.Constant) and isinstance(v.value, str)), None)
                out.append((n.lineno, name, 'process' in keys or None in n.keys))
    return out


def _part_id(name):
    return re.sub(r'_+', '_', re.sub(r'[^\w]+', '_', str(name))).strip('_')


def gate_model_parts(root):
    """Every part of a code model has a unique id (its name, as the viewer spells it) and a `process`."""
    pdir = os.path.join(root, CAD, 'parts')
    bad, seen = [], {}
    mods = []                           # every node __init__ / part file in node folders
    for dp, dn, fn in os.walk(pdir):
        dn[:] = sorted(x for x in dn if not x.startswith(('.', '_')))
        for f in sorted(fn):
            if not f.endswith('.py'):
                continue
            if dp != pdir and (not f.startswith('_') or f == '__init__.py'):
                mods.append(os.path.relpath(os.path.join(dp, f), pdir).replace(os.sep, '/'))
    for f in mods:
        for line, name, has_process in _part_dicts(os.path.join(pdir, f)):
            if not has_process:
                bad.append(f'parts/{f}:{line} part {name!r} has no "process" (print|laser|bent|mill|turn|bought|other)')
            if name is not None:
                other = seen.setdefault(_part_id(name), f)
                if other != f:
                    bad.append(f'part id {_part_id(name)!r} is made by both parts/{other} and parts/{f}')
    pj = os.path.join(root, 'build', 'model', 'parts.json')
    if os.path.isfile(pj):
        try:
            parts = json.loads(_read(pj, 1 << 26))['parts']
        except (ValueError, KeyError, TypeError):
            parts = []
        ids = [p.get('id') for p in parts if isinstance(p, dict)]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            bad.append(f'build/model/parts.json: duplicate part id(s) {_join(dup, 4)}')
        wrong = [str(p.get('id')) for p in parts if isinstance(p, dict) and p.get('process') not in MODEL_PROCESSES]
        if wrong:
            bad.append(f'build/model/parts.json: part(s) {_join(wrong, 4)} without a valid process')
    if bad:
        return [('FAIL', 'model parts', _join(list(dict.fromkeys(bad)), 5) + f'. Every part dict carries a unique name and a process ({REF})')]
    return [('PASS', 'model parts', 'every part has a unique id and a process')]


def gate_joints(root):
    """every part stands where a build123d joint put it (connect_to, then place() without a Location): in build/model/parts.json
    every placement has a `joint` except one, the machine's root. A model with no joint at all is not converted yet: WARN."""
    pj = os.path.join(root, 'build', 'model', 'parts.json')
    if not os.path.isfile(pj):
        return []
    try:
        parts = json.loads(_read(pj, 1 << 26))['parts']
    except (ValueError, KeyError, TypeError):
        return []
    pls = [pl for p in parts if isinstance(p, dict) and (p.get('step') or p.get('mesh_solid'))   # a row (no 3D) stands nowhere
           for pl in (p.get('placements') or []) if isinstance(pl, dict)]
    loose = [str(pl.get('name')) for pl in pls if not pl.get('joint')]
    targets = {str(pl['joint'].get('to')) for pl in pls if isinstance(pl.get('joint'), dict)}
    loose.sort(key=lambda n: n not in targets)          # the root: the jointless placement the others are docked to
    if not pls or len(loose) <= 1:
        return [('PASS', 'joints', f'{len(pls) - len(loose)} placements docked by joints, root {loose[0] if loose else "-"}')]
    if len(loose) == len(pls):
        return [('WARN', 'joints', f'no part is docked by a joint yet ({len(pls)} placements by Location): build parts in their own '
                                   f'coordinates with joints and dock them with connect_to ({REF} «Code model»)')]
    return [('FAIL', 'joints', f'placed without a joint (only the root {loose[0]!r} may be): {_join(loose[1:], 8)}. Give the part a '
                               f'build123d joint, dock it with connect_to and place() it without a Location ({REF} «Code model»)')]


def gate_kit_derive(root, cfg):
    """Every kit.json command of a code model runs derive.py (directly or through a project shell script)."""
    bad = []
    for name, cmd in (cfg or {}).get('commands', []):
        todo, seen, ok = [(cmd, None)], set(), False
        while todo and not ok:
            text, where = todo.pop(0)
            ok = 'derive.py' in text
            for s in scan(text, where, root):
                if s not in seen and len(seen) < 50:
                    seen.add(s)
                    todo.append((_read(s), s))
        if not ok:
            bad.append(f'{name}: {cmd[:60]}')
    if bad:
        return [('FAIL', 'kit via derive.py', f'{_join(bad, 4)}. A code model exports through '
                 f'`python $S/derive.py <bought|step|print|laser|assembly|viewer> {{manifest}}` ({REF} «Download kits»)')]
    return [('PASS', 'kit via derive.py', 'every kit.json command runs derive.py')]


# ---------------------------------------------------------------- export manifest

def gate_manifest(root, cfg):
    """Every manifest kit.json names (the full export's and each scope's piece), checked on its own."""
    mans = (cfg or {}).get('manifests') or ['build/kit/manifest.json']
    built = [m for m in mans if os.path.isfile(os.path.join(root, m))]
    if not built:
        return _gate_manifest(root, mans[0])
    out = [r for m in built for r in _gate_manifest(root, m)]
    worst = {}
    for lvl, name, msg in out:   # one line per check: the worst of the pieces, with every failing piece's detail
        if name not in worst or lvl == 'FAIL' and worst[name][0] != 'FAIL':
            worst[name] = (lvl, name, msg)
        elif lvl == 'FAIL':
            worst[name] = (lvl, name, worst[name][2] + '; ' + msg)
    return list(worst.values())


def _gate_manifest(root, man):
    p = os.path.join(root, man)
    if not os.path.isfile(p):
        return [('WARN', 'export manifest', f'{man} not built yet: bought kind and single-process kits not checked '
                 '(the model page builds it on the first download)')]
    try:
        with open(p, encoding='utf-8') as f:
            m = json.load(f)
        assert isinstance(m, dict)
    except (OSError, ValueError, AssertionError):
        return [('FAIL', 'export manifest', f'{man} is not a JSON object: fix the export ({REF} «Download kits»)')]
    out = []
    rows = m.get('bought') or []
    rows = rows if isinstance(rows, list) else []
    nok = [r for r in rows if not isinstance(r, dict) or r.get('kind') not in KINDS]
    if nok:
        ex = [f"{str(r.get('name') or r.get('code') or '?')[:40]} (kind={r.get('kind')!r})" if isinstance(r, dict) else repr(r)[:40]
              for r in nok]
        out.append(('FAIL', 'bought kind', f'{len(nok)} of {len(rows)} bought row(s) without a valid kind: {_join(ex, 4)}. Set kind '
                    f'= {"|".join(KINDS)} in the model\'s BOM where the part is added (PRINCIPLES §3)'))
    else:
        out.append(('PASS', 'bought kind', f'{len(rows)} bought row(s), all with kind'))
    bad = []
    for r in m.get('parts') or []:
        if not isinstance(r, dict):
            continue
        pr, name = r.get('process'), str(r.get('name') or r.get('id') or '?')
        exts = {os.path.splitext(str(f))[1].lower() for f in r.get('files') or []}
        mat = str(r.get('material') or '')
        if pr not in PROCESSES:
            bad.append(f'{name[:40]}: process {pr!r} (want {"|".join(PROCESSES)})')
        elif pr == 'print' and ('.dxf' in exts or METAL_RE.search(mat)):
            bad.append(f'{name[:40]}: print row with ' + ('a .dxf file' if '.dxf' in exts else f'metal material {mat!r}'))
        elif pr in ('laser', 'bent') and (exts & {'.stl', '.3mf'} or SOFT_RE.search(mat) or SOFT_RE.search(name)):
            bad.append(f'{name[:40]}: {pr} row with ' + ('a print file (.stl/.3mf)' if exts & {'.stl', '.3mf'} else 'a non-metal part'))
    if bad:
        out.append(('FAIL', 'single-process kits', f'{_join(bad, 4)}. One process per kit: print = plastic parts only, laser / bent = '
                    'sheet metal only; rubber, silicone and bought parts go elsewhere (PRINCIPLES §4)'))
    else:
        out.append(('PASS', 'single-process kits', 'print rows plastic, laser / bent rows metal'))
    return out


def _library():
    """The part_library.py of this scripts folder (check.py puts it on the path; a direct import of cad_gates may not)."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import part_library
    return part_library


def gate_library(root, cfg=None):
    """The part library. The project holds a copy of the library groups it imports at mechanics/cad/library, written
    by the platform from the folders the project's container has mounted. Gates: the copy is what its sources were (a part
    is edited in the library, never in the copy), it holds every group the project's code imports, and kit.json hashes it
    (or an edited library part would not refresh the downloads). No library in use -> no rows at all."""
    lib = _library()
    mirror = os.path.join(root, CAD, 'library')
    files = sorted(glob.glob(os.path.join(mirror, '**', '*'), recursive=True)) if os.path.isdir(mirror) else []
    files = [p for p in files if os.path.isfile(p) and os.path.basename(p) != lib.STAMP and '__pycache__' not in p]
    if not files and not lib.groups_used(root):
        return []
    if files and not os.path.isfile(os.path.join(mirror, lib.STAMP)):
        return []                       # a library/ folder with no stamp is the project's own package, not our copy
    lines, rc = lib.check(root)
    bad = [x[len('FAIL '):] for x in lines if x.startswith('FAIL ')] if rc else []
    for shadow in ('library.py', os.path.join('library', '__init__.py')):
        if os.path.isfile(os.path.join(root, CAD, shadow)):
            bad.append(f'mechanics/cad/{shadow.replace(os.sep, "/")} shadows the library package the levels merge into '
                       f'(PEP 420): rename it, the library itself is only ever written by the platform')
    if cfg is not None and files:
        for where, gl in _kit_inputs(cfg):
            covered = {os.path.realpath(x) for g in gl for x in glob.glob(os.path.join(root, g), recursive=True)}
            miss = [os.path.relpath(p, root).replace(os.sep, '/') for p in files if os.path.realpath(p) not in covered]
            if miss:
                bad.append(f'kit.json {where} miss {_join(miss, 3)}: an edited library part would not refresh the '
                           f'downloads; list "mechanics/cad/library/**/*"')
    if bad:
        return [('FAIL', 'library', _join(list(dict.fromkeys(bad)), 4) + f' ({REF} «Part library»)')]
    groups = sorted({os.path.relpath(p, mirror).split(os.sep)[0] for p in files})
    return [('PASS', 'library', f'{len(files)} file(s) from {len(groups)} group(s): ' + _join(groups, 6))]


def run(root):
    root = os.path.realpath(root)   # _resolve compares real paths (/var -> /private/var on a Mac)
    kind = cad_kind(root)
    if kind is None:
        return [('PASS', 'model gates', 'no CAD model yet (mechanics/cad/ has no params.py, build123d code or STEP): skipped')]
    res = gate_layout(root) if kind == 'build123d' else \
        [('PASS', 'CAD layout', 'STEP-import project: the model is the STEP in mechanics/cad/ (skill step-import); no build123d layout')]
    kit, cfg = gate_kit(root)
    res += kit
    res += gate_params_star(root)
    res += gate_library(root, cfg)      # any kind: a client-CAD project may import library parts too
    if kind == 'build123d':
        if is_code_model(root):
            res += gate_model_parts(root)
            res += gate_part_files(root, cfg)
            res += gate_joints(root)
            if cfg:
                res += gate_kit_derive(root, cfg)
    return res + gate_manifest(root, cfg)
