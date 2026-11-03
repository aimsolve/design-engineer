#!/usr/bin/env python3
"""Tests for the new check.py gates of the machine-project standard (CAD layout, kit.json, export manifest, --json). Written from
the spec before the implementation; each test names the spec condition (A1..A11) it serves. Layout (L1..L3): nodes are folders
mechanics/cad/parts/<node>/ only (an old parts/<node>.py with a build() FAILs the layout gate) and the 'incremental build' gate is gone.

  python3 skills/machine-project/scripts/test_check_gates.py -v

Plain unittest + system python3: no build123d, no network (check.py always runs with --no-validate). Every project is a
throwaway temp dir that starts from `scaffold.py <dir> --name m` (so the old gates pass) and then gets the files under test.
check.py is run as a subprocess, exactly as an agent runs it.

Reading the human report: a FAIL line is `FAIL  <rule>: <detail>`; the final summary line `FAIL: n fail, ...` is not a FAIL line.
"""
import json
import os
import re
import shutil
import subprocess
import procgroup
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'tests'))
from samples_i18n import SAMPLES  # noqa: E402

RU = SAMPLES['ru']          # materials and names as a Russian project writes them
CHECK = os.path.join(HERE, 'check.py')
SCAFFOLD = os.path.join(HERE, 'scaffold.py')
# cad_incremental.py as the platform mounts it: OUTSIDE the project, so never a project script
INC = '/opt/design-engineer/skills/machine-project/scripts/cad_incremental.py'
DERIVE = '/opt/design-engineer/skills/machine-project/scripts/derive.py'
CYR = re.compile(r'[\u0400-\u04ff]')     # the `ru` field the platform reads is Russian

FRAME_NODE = '''from build123d import Pos
from geom import place
from . import beam


def build():
    return [place(beam.build(), Pos(0, 0, 0))]
'''
BEAM_PY = '''from build123d import Box
import params as P


def build():
    return {"name": "frame", "shape": Box(P.WIDTH_MM, P.WIDTH_MM, P.WIDTH_MM), "color": "#888888", "process": "mill"}
'''
GEOM_PY = open(os.path.join(HERE, '..', 'templates', 'geom.py'), encoding='utf-8').read().replace('{machine}', 'm')
OLD_NODE_PY = '''from build123d import Box


def build():
    return [{"name": "drive", "shape": Box(1, 1, 1), "process": "mill"}]
'''
EXPORT_PY = '''import json
import sys
sys.path.insert(0, ".")
from parts import frame

print(json.dumps([p["name"] for p in frame.build()]))
'''
ASSEMBLE_B123D = '''from build123d import Box, Compound, export_step
import params as P

export_step(Compound([Box(P.WIDTH_MM, 1, 1)]), "../../build/m.step")
'''
GOOD_KIT = {'export': f'python3 {INC} mechanics/cad/export.py',
            'inputs': ['mechanics/cad/*.py', 'mechanics/cad/parts/**/*.py']}
DERIVE_KIT = {'inputs': ['mechanics/cad/*.py', 'mechanics/cad/parts/**/*.py', 'build/model/parts.json'],
              'scopes': {'laser': {'export': f'python3 {DERIVE} laser build/kit/laser.json',
                                   'part': f'python3 {DERIVE} laser {{manifest}} {{part}}'},
                         'bought': {'export': f'python3 {DERIVE} bought build/kit/bought.json'}}}


# ---------------------------------------------------------------- helpers

def write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    return p


def write_json(root, rel, obj):
    return write(root, rel, json.dumps(obj, ensure_ascii=False, indent=1))


class Run:
    def __init__(self, r):
        self.code, self.out, self.err = r.returncode, r.stdout, r.stderr

    @property
    def text(self):
        return self.out + '\n' + self.err

    def fail_lines(self):
        """`FAIL  rule: detail` lines of the human report (stdout or stderr); the summary `FAIL: ...` line excluded."""
        return [ln.strip() for ln in self.text.splitlines() if re.match(r'^FAIL\s+(?!:)\S', ln) and not ln.startswith('FAIL:')]

    def warn_lines(self):
        return [ln.strip() for ln in self.text.splitlines() if re.match(r'^WARN\s+\S', ln)]


def check(root, *extra):
    return Run(procgroup.run([sys.executable, CHECK, root, '--no-validate', *extra], capture_output=True, text=True, timeout=300))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='check_gates_')
        self.root = os.path.join(self.tmp, 'proj')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # project builders ----------------------------------------------------
    def scaffold(self, *extra):
        r = procgroup.run([sys.executable, SCAFFOLD, self.root, '--name', 'm', *extra], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def b123d(self, kit=GOOD_KIT, parts=True):
        """A conforming build123d project: params.py, geom.py, parts/__init__.py + node folder parts/frame/ (__init__.py
        placing the part file parts/frame/beam.py), a model export script, kit.json whose export runs through cad_incremental.py.
        Each test then breaks exactly one thing."""
        self.scaffold()
        write(self.root, 'mechanics/README.md', '# mechanics\n')
        write(self.root, 'mechanics/cad/params.py', 'WIDTH_MM = 10\n')
        if parts:
            write(self.root, 'mechanics/cad/parts/__init__.py', '')
            write(self.root, 'mechanics/cad/parts/frame/__init__.py', FRAME_NODE)
            write(self.root, 'mechanics/cad/parts/frame/beam.py', BEAM_PY)
        write(self.root, 'mechanics/cad/geom.py', GEOM_PY)
        write(self.root, 'mechanics/cad/export.py', EXPORT_PY)
        if kit is not None:
            write_json(self.root, 'kit.json', kit)

    def step_project(self, step_name='Machine.STEP', kit=GOOD_KIT):
        """A STEP-import project: the designer's STEP directly in mechanics/cad/, no params.py."""
        self.scaffold()
        write(self.root, 'mechanics/README.md', '# mechanics\n')
        write(self.root, f'mechanics/cad/{step_name}', 'ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n')
        write(self.root, 'tools/export_kit.py', 'print("export")\n')
        if kit is not None:
            write_json(self.root, 'kit.json', kit)

    def manifest(self, obj, rel='build/kit/manifest.json'):
        write_json(self.root, rel, obj)

    # assertions ----------------------------------------------------------
    def assertPasses(self, msg=''):
        r = check(self.root)
        self.assertEqual(r.code, 0, f'{msg}\nexpected exit 0, FAIL lines: {r.fail_lines()}\n{r.text[-3000:]}')
        return r

    def assertFails(self, *names, msg=''):
        """exit 1, at least one FAIL line, every FAIL line has a non-empty detail (what to do), and each of `names`
        (a module / script / file the spec says the FAIL must name) appears in some FAIL line."""
        r = check(self.root)
        fl = r.fail_lines()
        self.assertEqual(r.code, 1, f'{msg}\nexpected exit 1 (FAIL), got {r.code}\n{r.text[-3000:]}')
        self.assertTrue(fl, f'{msg}\nexit 1 but no FAIL line\n{r.text[-3000:]}')
        for ln in fl:
            self.assertRegex(ln, r':\s*\S', f'{msg}\nFAIL line without a detail (what to do): {ln!r}')
        for n in names:
            self.assertTrue(any(n in ln for ln in fl), f'{msg}\nno FAIL line names {n!r}: {fl}')
        return r


# ---------------------------------------------------------------- platform-managed folders

class PlatformFolders(Base):
    def test_app_release_folder_is_ignored(self):
        """The platform creates app-release/ (release sources + web output) in app projects: never a finding."""
        self.b123d()
        write(self.root, 'app-release/web/index.html', '<html></html>\n')
        write(self.root, 'app-release/web/shot_preview.png', 'x')
        write(self.root, 'app-release/src/bundle.zip', 'x')
        r = self.assertPasses()
        self.assertNotIn('app-release', r.text)


# ---------------------------------------------------------------- A1 CAD layout

class A1Layout(Base):
    def test_conforming_build123d_project_passes(self):
        """A1+A2 baseline: params.py, node folder parts/frame/ with build(), wrapped export -> exit 0."""
        self.b123d()
        self.assertPasses()

    def test_no_parts_dir_fails(self):
        self.b123d(parts=False)
        self.assertFails('parts')

    def test_parts_with_only_helpers_fails(self):
        """__init__.py and _*.py are helpers, not node modules: a parts/ with only those has no node module."""
        self.b123d(parts=False)
        write(self.root, 'mechanics/cad/parts/__init__.py', '')
        write(self.root, 'mechanics/cad/parts/_util.py', 'def build():\n    return [1]\n')
        self.assertFails('parts')

    def test_node_module_without_build_fails_naming_it(self):
        self.b123d()
        write(self.root, 'mechanics/cad/parts/drive/__init__.py', 'from build123d import Box\n\ndef make_drive():\n    return [Box(1, 1, 1)]\n')
        self.assertFails('drive')

    def test_build_with_required_argument_fails_naming_it(self):
        self.b123d()
        write(self.root, 'mechanics/cad/parts/drive/__init__.py', 'from build123d import Box\n\ndef build(scale):\n    return [Box(scale, 1, 1)]\n')
        self.assertFails('drive')

    def test_build_that_returns_nothing_fails_naming_it(self):
        self.b123d()
        write(self.root, 'mechanics/cad/parts/drive/__init__.py', 'from build123d import Box\n\ndef build():\n    shape = Box(1, 1, 1)\n')
        self.assertFails('drive')

    def test_build_star_with_default_args_passes(self):
        """build_*() is fine, and parameters with defaults are fine."""
        self.b123d()
        write(self.root, 'mechanics/cad/parts/drive/__init__.py',
              'from build123d import Pos\nfrom geom import place\nfrom parts.frame import beam\n\n'
              'def build_drive(scale=1.0, *, n=2):\n    return [place(beam.build(), Pos(scale, n, 0))]\n')
        self.assertPasses()

    def test_underscore_helper_without_build_is_exempt(self):
        self.b123d()
        write(self.root, 'mechanics/cad/parts/_geometry.py', 'def rib(w):\n    return w * 2\n')
        self.assertPasses()

    def test_layout_is_checked_statically(self):
        """A node module importing something not installed still passes: check.py must not import it."""
        self.b123d()
        write(self.root, 'mechanics/cad/parts/drive/__init__.py',
              'import module_that_does_not_exist_xyz\nfrom build123d import Pos\nfrom geom import place\nfrom parts.frame import beam\n\n'
              'def build():\n    return [place(beam.build(), Pos(1, 1, 1))]\n')
        self.assertPasses()

    def test_l1_old_layout_node_file_with_build_fails_naming_it(self):
        """a node is a folder parts/<node>/; an old parts/<node>.py with a build() FAILs the layout gate, naming the file."""
        self.b123d()
        write(self.root, 'mechanics/cad/parts/drive.py', OLD_NODE_PY)
        r = self.assertFails('drive.py')
        self.assertTrue(any('parts/<node>/' in ln or 'parts/drive/' in ln for ln in r.fail_lines()),
                        f'the FAIL must say nodes are folders parts/<node>/: {r.fail_lines()}')

    def test_l1_old_layout_build_star_node_file_fails(self):
        self.b123d()
        write(self.root, 'mechanics/cad/parts/drive.py', OLD_NODE_PY.replace('def build():', 'def build_drive():'))
        self.assertFails('drive.py')

    def test_l1_project_of_only_old_layout_nodes_fails(self):
        self.b123d(parts=False)
        write(self.root, 'mechanics/cad/parts/__init__.py', '')
        write(self.root, 'mechanics/cad/parts/frame.py', OLD_NODE_PY.replace('drive', 'frame'))
        self.assertFails('frame.py')

    def test_l2_underscore_helper_with_build_is_still_fine(self):
        """private helpers parts/_<name>.py stay allowed, even with a build()."""
        self.b123d()
        write(self.root, 'mechanics/cad/parts/_shared.py', 'SIDE = 2\n\ndef build():\n    return []\n')
        self.assertPasses()

    def test_from_params_import_star_warns_naming_the_file(self):
        """`from params import *` hides which parameter a file read, so the incremental build cannot attribute a param read
        to the part that read it. Any .py under mechanics/cad/ doing it is a WARN (not a FAIL: live projects use it) naming the
        file, and `import params as P` next to it stays silent."""
        self.b123d()
        write(self.root, 'mechanics/cad/parts/frame/beam.py',
              'from build123d import Box\nfrom params import *\n\n\n'
              'def build():\n    return {"name": "frame", "shape": Box(WIDTH_MM, 1, 1), "color": "#888888", "process": "mill"}\n')
        r = self.assertPasses('`from params import *` must be a WARN, not a FAIL')
        wl = r.warn_lines()
        self.assertTrue(any('beam.py' in ln for ln in wl), f'no WARN line names beam.py: {wl}\n{r.text[-2000:]}')
        self.assertTrue(any('params' in ln and '*' in ln for ln in wl),
                        f'the WARN must say what is wrong (from params import *): {wl}')
        write(self.root, 'mechanics/cad/parts/frame/beam.py', BEAM_PY)          # `import params as P`: silent again
        self.assertFalse([ln for ln in self.assertPasses().warn_lines() if 'params' in ln and '*' in ln],
                         'importing params normally must not warn')

    def test_star_import_of_a_nodes_params_and_without_a_space_warns_too(self):
        """a node's own params module imported with `from .params import *`, and `from params import*` (no space), hide the
        reads just the same: the same WARN, naming the file."""
        cases = {
            'relative, node params': ('mechanics/cad/parts/frame/beam.py', 'beam.py', 'from .params import *'),
            'no space': ('mechanics/cad/parts/frame/beam.py', 'beam.py', 'from params import*'),
        }
        for label, (rel, name, line) in cases.items():
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                self.b123d()
                write(self.root, 'mechanics/cad/parts/frame/params.py', 'WIDTH_MM = 10\n')
                write(self.root, rel,
                      f'from build123d import Box\n{line}\n\n\n'
                      'def build():\n    return {"name": "frame", "shape": Box(WIDTH_MM, 1, 1), "color": "#888888", '
                      '"process": "mill"}\n')
                r = self.assertPasses(f'`{line}` must be a WARN, not a FAIL')
                wl = r.warn_lines()
                self.assertTrue(any(name in ln and '*' in ln for ln in wl),
                                f'`{line}`: no WARN line names {name} and the star import: {wl}\n{r.text[-2000:]}')

    def test_star_import_words_in_a_docstring_or_comment_do_not_warn(self):
        """only a real import statement counts; the words in a docstring or a comment are not one."""
        self.b123d()
        write(self.root, 'mechanics/cad/parts/frame/beam.py',
              '"""Never write `from params import *` here: the build cannot tell which parameter you read.\n\n'
              'from params import *\n"""\n'
              '# from params import *   (do not)\n'
              'from build123d import Box\nimport params as P\n\n\n'
              'def build():\n    return {"name": "frame", "shape": Box(P.WIDTH_MM, 1, 1), "color": "#888888", "process": "mill"}\n')
        r = self.assertPasses()
        self.assertFalse([ln for ln in r.warn_lines() if 'params' in ln and '*' in ln],
                         f'a docstring or a comment is not an import: {r.warn_lines()}')

    def test_star_import_in_a_nodes_own_params_module_does_not_warn_but_elsewhere_still_does(self):
        """a node's own parts/<node>/params.py may do `from params import *` (to extend the machine's values): its values
        are keyed by value, so it is not warned about. Any other file doing it (here beam.py next to it) still is."""
        self.b123d()
        write(self.root, 'mechanics/cad/parts/frame/params.py', 'from params import *\n\nLOCAL_MM = WIDTH_MM * 2\n')
        r = self.assertPasses()
        star = [ln for ln in r.warn_lines() if 'params' in ln and '*' in ln]
        self.assertFalse([ln for ln in star if 'frame/params.py' in ln or 'frame' + os.sep + 'params.py' in ln],
                         f"a node's own params.py doing `from params import *` must not be warned about: {star}")
        write(self.root, 'mechanics/cad/parts/frame/beam.py',
              'from build123d import Box\nfrom params import *\n\n\n'
              'def build():\n    return {"name": "frame", "shape": Box(WIDTH_MM, 1, 1), "color": "#888888", "process": "mill"}\n')
        r = self.assertPasses()
        star = [ln for ln in r.warn_lines() if 'params' in ln and '*' in ln]
        self.assertTrue(any('beam.py' in ln for ln in star), f'beam.py doing `from params import *` must still warn: {star}')
        self.assertFalse([ln for ln in star if 'frame/params.py' in ln],
                         f"the node's params.py must stay out of the WARN next to beam.py: {star}")

    def test_build123d_code_without_params_fails(self):
        """build123d code under mechanics/cad (outside vendor/) and no params.py = FAIL."""
        self.b123d()
        os.remove(os.path.join(self.root, 'mechanics/cad/params.py'))
        write(self.root, 'mechanics/cad/model.py', 'from build123d import Box\nprint(Box(1, 1, 1))\n')
        self.assertFails('params')

    def test_build123d_only_in_vendor_is_not_a_build123d_project(self):
        """A6 side: a vendor/ .py importing build123d does not make it a build123d project (no params.py, no kit.json -> exit 0)."""
        self.scaffold()
        write(self.root, 'mechanics/README.md', '# mechanics\n')
        write(self.root, 'mechanics/cad/README.md', '# cad\n')
        write(self.root, 'mechanics/cad/vendor/lib.py', 'from build123d import Box\n')
        self.assertPasses()


# ---------------------------------------------------------------- A2 kit.json

class A2KitJson(Base):
    def test_build123d_project_without_kit_json_fails(self):
        self.b123d(kit=None)
        self.assertFails('kit.json')

    def test_step_project_without_kit_json_fails(self):
        self.step_project(kit=None)
        self.assertFails('kit.json')

    def test_valid_kit_json_with_all_fields_passes(self):
        self.b123d(kit=dict(GOOD_KIT, manifest='build/kit/manifest.json', timeout=3600))
        self.assertPasses()

    def test_invalid_kit_json_fails(self):
        bad = {
            'not JSON': '{"export": "x",',
            'not an object': json.dumps([GOOD_KIT]),
            'export missing': json.dumps({'inputs': GOOD_KIT['inputs']}),
            'export empty': json.dumps(dict(GOOD_KIT, export='')),
            'inputs missing': json.dumps({'export': GOOD_KIT['export']}),
            'inputs empty': json.dumps(dict(GOOD_KIT, inputs=[])),
            'inputs not a list': json.dumps(dict(GOOD_KIT, inputs='mechanics/cad/*.py')),
            'inputs absolute': json.dumps(dict(GOOD_KIT, inputs=['mechanics/cad/*.py', '/work/mechanics/cad/params.py'])),
            'inputs with ..': json.dumps(dict(GOOD_KIT, inputs=['mechanics/cad/*.py', '../other/params.py'])),
            'manifest absolute': json.dumps(dict(GOOD_KIT, manifest='/work/build/kit/manifest.json')),
            'manifest with ..': json.dumps(dict(GOOD_KIT, manifest='../build/kit/manifest.json')),
            'inputs glob = export output (default manifest folder)': json.dumps(dict(GOOD_KIT, inputs=GOOD_KIT['inputs'] + ['build/kit/*'])),
            'inputs glob = export output (custom manifest folder)': json.dumps(dict(GOOD_KIT, manifest='out/kit/manifest.json',
                                                                                    inputs=GOOD_KIT['inputs'] + ['out/kit/*.stl'])),
            'timeout 0': json.dumps(dict(GOOD_KIT, timeout=0)),
            'timeout negative': json.dumps(dict(GOOD_KIT, timeout=-5)),
            'timeout string': json.dumps(dict(GOOD_KIT, timeout='3600')),
            'timeout float': json.dumps(dict(GOOD_KIT, timeout=1.5)),
        }
        for case, text in bad.items():
            with self.subTest(case=case):
                shutil.rmtree(self.root, ignore_errors=True)
                self.b123d(kit=None)
                write(self.root, 'kit.json', text)
                self.assertFails(msg=f'kit.json {case}')


# ---------------------------------------------------------------- L3 no 'incremental build' gate

class L3NoIncrementalGate(Base):
    """cad_incremental.py's plain run no longer caches anything, so check.py has no 'incremental build' gate: running a
    model script without cad_incremental.py (kit export, project *.sh, quoted command strings) is not a FAIL."""

    def assertNoIncrementalGate(self, r, msg=''):
        gate = [ln for ln in r.text.splitlines() if re.match(r'^(FAIL|WARN|PASS)\s+incremental build\b', ln.strip(), re.I)]
        self.assertFalse(gate, f'{msg}\nthe incremental build gate must be gone: {gate}')

    def test_conforming_project_reports_no_incremental_build_gate(self):
        """Not even a PASS line: the gate does not exist any more."""
        self.b123d()
        self.assertNoIncrementalGate(self.assertPasses())

    def test_kit_export_with_plain_python_passes(self):
        for cmd in ('python3 mechanics/cad/export.py',
                    'python -u mechanics/cad/export.py --all',
                    'uv run --with build123d python -u mechanics/cad/export.py'):
            with self.subTest(cmd=cmd):
                shutil.rmtree(self.root, ignore_errors=True)
                self.b123d(kit=dict(GOOD_KIT, export=cmd))
                self.assertNoIncrementalGate(self.assertPasses(msg=cmd), cmd)

    def test_project_shell_scripts_running_model_scripts_plainly_pass(self):
        cases = {
            'kit export via sh': ('tools/build.sh', '#!/bin/sh\nset -e\npython3 mechanics/cad/export.py\n', 'bash tools/build.sh'),
            'any *.sh running assemble.py': ('tools/dev.sh', '#!/bin/sh\npython3 mechanics/cad/assemble.py\n', None),
            'quoted command': ('tools/rebuild.sh', '#!/bin/sh\ntg job submit "cd mechanics/cad && python assemble.py"\n', None),
            'uv run': ('tools/rebuild.sh', '#!/bin/sh\nuv run mechanics/cad/assemble.py\n', None),
        }
        for case, (rel, src, export) in cases.items():
            with self.subTest(case=case):
                shutil.rmtree(self.root, ignore_errors=True)
                self.b123d(kit=dict(GOOD_KIT, export=export) if export else GOOD_KIT)
                write(self.root, 'mechanics/cad/assemble.py', ASSEMBLE_B123D)
                write(self.root, rel, src)
                self.assertNoIncrementalGate(self.assertPasses(msg=case), case)

    def test_no_gate_named_incremental_build_in_json(self):
        self.b123d(kit=dict(GOOD_KIT, export='python3 mechanics/cad/export.py'))
        r = check(self.root, '--json')
        d = json.loads(r.out)
        self.assertEqual(d['result'], 'PASS', d)
        self.assertNotIn('incremental build', r.text.lower())


# ---------------------------------------------------------------- A4 bought kind

def good_manifest():
    return {
        'parts': [
            {'id': 'p1', 'name': 'Housing', 'node': 'N1', 'process': 'print', 'qty': 1, 'material': 'PETG', 'files': ['build/kit/p1.stl']},
            {'id': 'p2', 'name': 'Cover', 'node': 'N1', 'process': 'print', 'qty': 2, 'files': ['build/kit/p2.3mf']},
            {'id': 'p3', 'name': 'Plate', 'node': 'N2', 'process': 'laser', 'qty': 2, 'thickness': 1.5, 'material': RU['steel_08ps'],
             'files': ['build/kit/p3.dxf', 'build/kit/p3.pdf', 'build/kit/p3.step']},
            {'id': 'p4', 'name': 'Bracket', 'node': 'N2', 'process': 'bent', 'qty': 1, 'thickness': 2.0, 'material': 'AISI 304',
             'files': ['build/kit/p4.dxf', 'build/kit/p4.pdf']},
            {'id': 'p5', 'name': 'Bearing 608', 'node': 'N2', 'process': 'bought', 'qty': 2, 'files': []},
            {'id': 'p6', 'name': 'Name plate', 'node': 'N1', 'process': 'other', 'qty': 1, 'files': []},
        ],
        'bought': [
            {'name': 'Screw M6×20', 'code': 'DIN912', 'qty': 8, 'unit': 'pcs', 'kind': 'fasteners'},
            {'name': 'Power supply 24 V', 'qty': 1, 'unit': 'pcs', 'kind': 'electrical'},
            {'name': 'Motor NEMA17', 'qty': 1, 'unit': 'pcs', 'kind': 'motors'},
            {'name': 'Cylinder SC32', 'qty': 1, 'unit': 'pcs', 'kind': 'pneumatic'},
            {'name': 'Bearing 608', 'qty': 2, 'unit': 'pcs', 'kind': 'bearings'},
            {'name': 'Profile 20×20', 'qty': 4, 'unit': 'pcs', 'kind': 'profile'},
            {'name': 'Sticker', 'qty': 1, 'unit': 'pcs', 'kind': 'other'},
        ],
        'bought_xlsx': 'build/kit/bought.xlsx', 'model_step': 'build/kit/model.step',
    }


class A4BoughtKind(Base):
    def test_all_kinds_valid_passes(self):
        self.b123d()
        self.manifest(good_manifest())
        self.assertPasses()

    def test_bought_rows_without_valid_kind_fail(self):
        self.b123d()
        m = good_manifest()
        m['bought'] += [{'name': 'Bolt M8×40', 'qty': 4, 'unit': 'pcs'},                      # kind missing
                        {'name': 'Washer M8', 'qty': 4, 'unit': 'pcs', 'kind': 'screws'}]     # kind outside the set
        self.manifest(m)
        r = self.assertFails()
        self.assertTrue(any(('2' in ln) or ('Bolt' in ln) or ('Washer' in ln) for ln in r.fail_lines()),
                        f'detail must name how many rows or examples: {r.fail_lines()}')

    def test_manifest_path_from_kit_json_is_used(self):
        self.b123d(kit=dict(GOOD_KIT, manifest='out/kit/manifest.json'))
        m = good_manifest()
        m['bought'].append({'name': 'Bolt M8×40', 'qty': 4, 'unit': 'pcs', 'kind': 'bolts'})
        self.manifest(m, rel='out/kit/manifest.json')
        self.assertFails()

    def test_missing_manifest_is_warn_only(self):
        self.b123d()
        r = self.assertPasses()
        self.assertTrue(any('manifest' in ln.lower() for ln in r.warn_lines()), f'expected a WARN about the missing manifest: {r.warn_lines()}')


# ---------------------------------------------------------------- A5 single-process kits

class A5SingleProcess(Base):
    def broken(self, mutate):
        m = good_manifest()
        mutate(m)
        return m

    def test_clean_mixed_manifest_passes(self):
        self.b123d()
        self.manifest(good_manifest())
        self.assertPasses()

    def test_mixed_process_rows_fail(self):
        cases = {
            'unknown process': lambda m: m['parts'][0].update(process='cnc'),
            'print row lists a dxf': lambda m: m['parts'][0]['files'].append('build/kit/p1.dxf'),
            'print row material steel': lambda m: m['parts'][0].update(material='Steel S235'),
            'print row material steel (ru)': lambda m: m['parts'][0].update(material=RU['steel_08ps']),
            'print row material aluminium (ru)': lambda m: m['parts'][0].update(material=RU['alu_amg3']),
            'laser row lists an stl': lambda m: m['parts'][2]['files'].append('build/kit/p3.stl'),
            'bent row lists a 3mf': lambda m: m['parts'][3]['files'].append('build/kit/p4.3mf'),
            'laser row material silicone (ru)': lambda m: m['parts'][2].update(material=RU['silicone_3mm']),
            'laser row material PETG': lambda m: m['parts'][2].update(material='PETG'),
            'laser row material TPU': lambda m: m['parts'][2].update(material='TPU 95A'),
            'bent row material plastic (ru)': lambda m: m['parts'][3].update(material=RU['plastic']),
            'laser row name says rubber (ru)': lambda m: m['parts'][2].update(name=RU['gasket_rubber'], material=None),
        }
        for case, mutate in cases.items():
            with self.subTest(case=case):
                shutil.rmtree(self.root, ignore_errors=True)
                self.b123d()
                m = self.broken(mutate)
                for p in m['parts']:
                    if p.get('material', '') is None:
                        del p['material']
                self.manifest(m)
                self.assertFails(msg=case)


# ---------------------------------------------------------------- A6 no CAD / A7 STEP import

class A6NoCad(Base):
    def test_fresh_scaffold_passes(self):
        self.scaffold()
        self.assertPasses()

    def test_cad_folder_with_only_readme_and_vendor_step_passes(self):
        self.scaffold()
        write(self.root, 'mechanics/README.md', '# mechanics\n')
        write(self.root, 'mechanics/cad/README.md', '# cad\n')
        write(self.root, 'mechanics/cad/vendor/motor.step', 'ISO-10303-21;\n')
        self.assertPasses()


class A7StepImport(Base):
    def test_step_project_with_plain_python_and_no_parts_passes(self):
        """A1/A3 never FAIL on a STEP-import project: no parts/, extra scripts may import build123d and run plainly."""
        self.step_project(kit=dict(GOOD_KIT, export='python3 tools/export_kit.py'))
        write(self.root, 'mechanics/cad/inspect_step.py', 'from build123d import import_step\nimport_step("Machine.STEP")\n')
        write(self.root, 'tools/dev.sh', '#!/bin/sh\npython3 mechanics/cad/inspect_step.py\n')
        self.assertPasses()

    def test_step_extension_any_case_is_detected(self):
        for name in ('machine.step', 'Machine.STEP', 'machine.stp', 'Machine.Stp'):
            with self.subTest(step=name):
                shutil.rmtree(self.root, ignore_errors=True)
                self.step_project(step_name=name, kit=None)
                self.assertFails('kit.json', msg=name)

    def test_step_project_manifest_rules_still_apply(self):
        self.step_project(kit=dict(GOOD_KIT, export='python3 tools/export_kit.py'))
        m = good_manifest()
        m['bought'].append({'name': 'Bolt', 'qty': 1, 'unit': 'pcs', 'kind': 'bolts'})
        self.manifest(m)
        self.assertFails(msg='A4 on a STEP project')
        m = good_manifest()
        m['parts'][0]['files'].append('build/kit/p1.dxf')
        self.manifest(m)
        self.assertFails(msg='A5 on a STEP project')


# ---------------------------------------------------------------- A8 --json

def tree(root):
    out = {}
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            with open(p, 'rb') as fh:
                out[os.path.relpath(p, root)] = fh.read()
    return out


class A8Json(Base):
    def assert_shape(self, r):
        try:
            d = json.loads(r.out)
        except ValueError as e:
            self.fail(f'--json stdout is not exactly one JSON object ({e}):\n{r.out[:1500]}')
        self.assertIsInstance(d, dict)
        self.assertEqual(set(d), {'result', 'fails', 'warns'}, d)
        self.assertIn(d['result'], ('PASS', 'FAIL'))
        self.assertIsInstance(d['warns'], int)
        self.assertNotIsInstance(d['warns'], bool)
        self.assertIsInstance(d['fails'], list)
        for f in d['fails']:
            self.assertEqual(set(f), {'rule', 'detail', 'ru'}, f)
            for k in ('rule', 'detail', 'ru'):
                self.assertIsInstance(f[k], str)
                self.assertTrue(f[k].strip(), f'empty {k}: {f}')
            self.assertRegex(f['ru'], CYR, f'ru must be Russian: {f}')
        return d

    def test_json_on_fail(self):
        self.b123d(parts=False)
        r = check(self.root, '--json')
        d = self.assert_shape(r)
        self.assertEqual(d['result'], 'FAIL')
        self.assertTrue(d['fails'])
        self.assertEqual(r.code, 1)

    def test_json_on_pass(self):
        self.b123d()
        r = check(self.root, '--json')
        d = self.assert_shape(r)
        self.assertEqual(d['result'], 'PASS', d)
        self.assertEqual(d['fails'], [])
        self.assertEqual(r.code, 0)

    def test_json_does_not_create_wiki_index(self):
        self.b123d()
        write(self.root, 'wiki/decisions.md', '---\ntitle: Decisions\n---\n# Decisions\n')
        before = tree(self.root)
        self.assert_shape(check(self.root, '--json'))   # the run really happened (not an argparse rejection)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'wiki', 'index.md')), '--json created wiki/index.md')
        self.assertEqual(tree(self.root), before, '--json modified the project')

    def test_json_does_not_rewrite_wiki_index(self):
        self.b123d()
        write(self.root, 'wiki/decisions.md', '---\ntitle: Decisions\n---\n# Decisions\n')
        write(self.root, 'wiki/index.md', '# hand-made, stale\n')
        before = tree(self.root)
        self.assert_shape(check(self.root, '--json'))
        self.assertEqual(tree(self.root), before, '--json modified the project')


# ---------------------------------------------------------------- A9 e2e

class A9EndToEnd(Base):
    def test_scaffold_build123d_project_passes_all_gates(self):
        self.scaffold('--cad', 'build123d')
        cad = os.path.join(self.root, 'mechanics', 'cad')
        self.assertTrue(os.path.isfile(os.path.join(cad, 'params.py')), 'no params.py')
        nodes = [f for f in os.listdir(os.path.join(cad, 'parts'))
                 if os.path.isfile(os.path.join(cad, 'parts', f, '__init__.py')) and not f.startswith('_')] \
            if os.path.isdir(os.path.join(cad, 'parts')) else []
        self.assertTrue(nodes, 'scaffold made no node folder mechanics/cad/parts/<node>/')   # node = folder
        src = open(os.path.join(cad, 'parts', nodes[0], '__init__.py'), encoding='utf-8').read()
        self.assertRegex(src, r'(?m)^def build(_\w+)?\(\s*\)', 'node module has no build()')
        kit_path = os.path.join(self.root, 'kit.json')
        self.assertTrue(os.path.isfile(kit_path), 'scaffold made no kit.json')
        kit = json.load(open(kit_path, encoding='utf-8'))
        exp = json.dumps(kit)
        scripts = ''.join(open(os.path.join(dp, f), encoding='utf-8').read()
                          for dp, _, fn in os.walk(self.root) for f in fn if f.endswith('.sh'))
        self.assertIn('derive.py', exp, 'kit.json scopes do not run derive.py')
        self.assertTrue(os.path.isfile(os.path.join(cad, 'geom.py')), 'no geom.py with the P() helper')
        self.assertIn('derive.py', scripts, 'sim/site.sh does not rebuild through derive.py')
        self.assertPasses()
        r = check(self.root, '--json')
        self.assertEqual(json.loads(r.out)['result'], 'PASS')

    def test_real_offender_shape_fails_layout_not_incremental(self):
        """build123d assemble.py + params.py, no parts/, export `bash tools/build_model.sh` running plain python: the layout gate
        FAILs; since #30 there is no incremental FAIL."""
        self.scaffold()
        write(self.root, 'mechanics/README.md', '# mechanics\n')
        write(self.root, 'mechanics/cad/params.py', 'WIDTH_MM = 10\n')
        write(self.root, 'mechanics/cad/assemble.py', ASSEMBLE_B123D)
        write(self.root, 'tools/build_model.sh', '#!/bin/bash\nset -e\npython mechanics/cad/assemble.py\n')
        write_json(self.root, 'kit.json', {'export': 'bash tools/build_model.sh', 'inputs': ['mechanics/cad/*.py']})
        r = self.assertFails()
        fl = [ln.lower() for ln in r.fail_lines()]
        self.assertTrue(any('parts' in ln for ln in fl), f'no layout FAIL about parts/: {fl}')
        self.assertFalse([ln for ln in fl if re.match(r'^fail\s+incremental build\b', ln)], f'the incremental build gate must be gone: {fl}')


class A10ReviewFindings(Base):
    """Cases found by the code review: command strings, shell oddities, CAD outside mechanics/cad, a loose STEP, robustness."""

    def test_echo_of_a_command_is_not_a_run(self):
        """Still true without the incremental gate: an echo is never a FAIL."""
        self.b123d()
        write(self.root, 'mechanics/cad/assemble.py', ASSEMBLE_B123D)
        write(self.root, 'tools/hint.sh', '#!/bin/sh\necho "hint: python mechanics/cad/assemble.py"\necho hint: python mechanics/cad/assemble.py\n')
        self.assertPasses()

    def test_build123d_model_outside_mechanics_cad_fails_layout(self):
        self.scaffold()
        write(self.root, 'cad/params.py', 'W_MM = 1\n')
        write(self.root, 'cad/assemble.py', ASSEMBLE_B123D)
        self.assertFails('mechanics/cad')

    def test_loose_step_next_to_build123d_assemble_is_not_step_import(self):
        self.scaffold()
        write(self.root, 'mechanics/cad/assemble.py', ASSEMBLE_B123D)
        write(self.root, 'mechanics/cad/nema17.step', 'ISO-10303-21;\n')
        write_json(self.root, 'kit.json', GOOD_KIT)
        self.assertFails('params.py')

    def test_broken_symlink_and_fifo_named_sh_still_give_json(self):
        self.b123d()
        os.makedirs(os.path.join(self.root, 'tools'), exist_ok=True)
        os.symlink('/nonexistent/run.sh', os.path.join(self.root, 'tools', 'run.sh'))
        os.mkfifo(os.path.join(self.root, 'tools', 'pipe.sh'))
        r = procgroup.run([sys.executable, CHECK, self.root, '--no-validate', '--json'], capture_output=True, text=True, timeout=60)
        self.assertEqual(json.loads(r.stdout)['result'], 'PASS', r.stderr[-2000:])


# ---------------------------------------------------------------- A11 code model: parts + derive.py (rollout of issue #2)

class A11CodeModel(Base):
    def code_model(self, kit=DERIVE_KIT):
        self.b123d(kit=kit, parts=False)               # the tests write their own nodes with node()
        write(self.root, 'mechanics/cad/parts/__init__.py', '')
        write(self.root, 'mechanics/cad/assemble.py', 'from parts import frame\nitems = frame.build()\n')

    def node(self, name, part):
        """Node folder parts/<name>/: a part file <name>.py returning `part` (a dict literal) and an __init__.py that places it
        (a row without a shape is returned as is). Replaces the old parts/<name>.py."""
        shaped = '"shape"' in part
        write(self.root, f'mechanics/cad/parts/{name}/{name}.py',
              f'from build123d import Box\n\ndef build():\n    return {part}\n')
        write(self.root, f'mechanics/cad/parts/{name}/__init__.py',
              'from build123d import Pos\nfrom geom import place\nfrom . import ' + name + '\n\ndef build():\n'
              + (f'    return [place({name}.build(), Pos(0, 0, 0))]\n' if shaped else f'    return [{name}.build()]\n'))

    def test_code_model_with_derive_kit_and_process_passes(self):
        self.code_model()
        self.node('frame', '{"name": "frame", "shape": Box(1, 1, 1), "process": "mill"}')
        r = self.assertPasses()
        self.assertIn('model parts', r.text)
        self.assertIn('kit via derive.py', r.text)

    def test_part_without_process_fails_naming_file_and_part(self):
        self.code_model()
        self.node('frame', '{"name": "frame", "shape": Box(1, 1, 1)}')
        self.assertFails('frame.py', 'process')

    def test_a_bought_row_without_a_shape_needs_no_shape_but_the_dict_with_one_needs_process(self):
        self.code_model()
        self.node('frame', '{"name": "pipe", "process": "bought", "code": "T", "qty": 1}')
        self.assertPasses()

    def test_same_part_name_in_two_nodes_fails(self):
        self.code_model()
        self.node('frame', '{"name": "Bolt 1", "shape": Box(1, 1, 1), "process": "mill"}')
        self.node('drive', '{"name": "Bolt-1", "shape": Box(1, 1, 1), "process": "mill"}')
        self.assertFails('Bolt_1')

    def test_built_model_with_duplicate_ids_or_no_process_fails(self):
        self.code_model()
        self.node('frame', '{"name": "frame", "shape": Box(1, 1, 1), "process": "mill"}')
        write_json(self.root, 'build/model/parts.json', {'parts': [{'id': 'a', 'process': 'mill'}, {'id': 'a', 'process': 'mill'}]})
        self.assertFails('duplicate')
        write_json(self.root, 'build/model/parts.json', {'parts': [{'id': 'a'}]})
        self.assertFails('process')

    def test_kit_command_not_through_derive_fails(self):
        """Once one scope calls derive.py, every command must."""
        self.code_model(kit={'inputs': ['mechanics/cad/*.py'], 'scopes': {
            'laser': {'export': f'python3 {DERIVE} laser build/kit/laser.json'}, 'bought': {'export': f'python3 {INC} mechanics/cad/export.py'}}})
        self.node('frame', '{"name": "frame", "shape": Box(1, 1, 1), "process": "mill"}')
        self.assertFails('derive.py')

    def test_assembly_scope_passes_the_kit_json_gate(self):
        """the assembly drawings are a kit scope of their own, `scopes.assembly`, exported through derive.py like the
        others. Before #19 the gate FAILed it as an unknown scope."""
        kit = json.loads(json.dumps(DERIVE_KIT))
        kit['scopes']['assembly'] = {
            'export': 'S=/opt/design-engineer/skills/machine-project/scripts; python $S/derive.py assembly build/kit/assembly.json',
            'inputs': ['mechanics/cad/*.py', 'mechanics/cad/parts/**/*.py', 'build/model/parts.json']}
        self.code_model(kit=kit)
        self.node('frame', '{"name": "frame", "shape": Box(1, 1, 1), "process": "mill"}')
        r = check(self.root)
        kit_fails = [ln for ln in r.fail_lines() if 'kit' in ln]
        self.assertEqual(kit_fails, [], f'scopes.assembly must pass the kit.json gate: {kit_fails}')
        self.assertEqual(r.code, 0, f'expected exit 0, FAIL lines: {r.fail_lines()}\n{r.text[-3000:]}')
        self.assertTrue(any(re.match(r'^PASS\s+kit\.json\b', ln.strip()) and 'assembly' in ln for ln in r.text.splitlines()),
                        f'the kit.json PASS line must list the assembly scope:\n{r.text[-3000:]}')

    def test_kit_command_through_a_project_shell_script_running_derive_passes(self):
        self.code_model(kit={'inputs': ['mechanics/cad/*.py'], 'scopes': {'laser': {'export': 'bash tools/laser.sh'}}})
        self.node('frame', '{"name": "frame", "shape": Box(1, 1, 1), "process": "mill"}')
        write(self.root, 'tools/laser.sh', f'#!/bin/sh\npython3 {DERIVE} laser build/kit/laser.json\n')
        self.assertPasses()

    def test_old_template_project_is_not_judged_yet(self):
        """assemble.py + parts/ + a cad_incremental kit and parts without `process`: still PASS (the gates are opt-in)."""
        self.code_model(kit=GOOD_KIT)
        self.node('frame', '{"name": "frame", "shape": Box(1, 1, 1)}')
        r = self.assertPasses()
        self.assertNotIn('model parts', r.text)
        self.assertNotIn('kit via derive.py', r.text)

    def test_a_built_model_opts_in_even_with_a_legacy_kit(self):
        self.code_model(kit=GOOD_KIT)
        self.node('frame', '{"name": "frame", "shape": Box(1, 1, 1), "process": "mill"}')
        write_json(self.root, 'build/model/parts.json', {'parts': [{'id': 'frame', 'process': 'mill'}]})
        self.assertFails('derive.py')

    def test_project_without_assemble_py_keeps_the_old_kit_rule(self):
        self.b123d()
        self.assertPasses()


if __name__ == '__main__':
    unittest.main()
