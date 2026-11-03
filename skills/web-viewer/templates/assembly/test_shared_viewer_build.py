"""build.py: «one viewer code for all projects, the project gives only data».

Default ("shared") mode writes ONLY project data and project modules into --out; the viewer code is served once by the stack at /_viewer/.
--standalone additionally writes index.html + <out>/_viewer/ (a copy of the shared code) so a plain static server works.
Everything the page used to learn from build-time edits of index.html comes from config.json (title, sim, edges, cycle, build stamp).

  python3 -m unittest test_shared_viewer_build.py -v        (from this folder; no browser, synthetic importer output)
"""
import json, os, re, shutil, subprocess, sys, tempfile, unittest, urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import build  # noqa: E402

# what a project's public/ may hold; parts/** and fw/** are data trees
DATA_FILES = {"model.glb", "parts.json", "edges.json", "edges.bin", "config.json", "motion.json", "world.js", "world_data.json"}
DATA_TREES = ("parts/", "fw/")
SIM_DATA = {"sim/config.json", "sim/motion.json", "sim/machine.js", "sim/helper.js"}       # the machine's own files listed in site.json
VIEWER_CODE = ["index.html", "vendor", "view_fx.js", "win.js", "feedback.js", "kit.js", "flash.js", "hw.js", "motion.js",
               "sim/ui.js", "sim/ui.mjs", "sim/motion.js", "sim/app.js", "sim/vc", "_viewer"]

MACHINE = """// synthetic machine module: imports the sim runtime the way a project module may (the old site layout put it at sim/vc/)
import { Sim } from './vc/devices.mjs';
import { helper } from './helper.mjs';
export const META = { id: 'm', title: 'M', boards: [] };
export function build(vc) { return { Sim, helper }; }
"""


def make_input(root: Path, edges=True) -> Path:
    src = root / "in"
    (src / "parts").mkdir(parents=True)
    (src / "model.glb").write_bytes(b"glTF-fake")
    (src / "parts.json").write_text(json.dumps({"assembly": {"root": "Synthetic assembly · Ø20"}, "parts": [
        {"id": "p1", "name": "Part 1", "kind": "made", "material": "steel", "instances": 1, "volume_mm3": 1000.0, "motion_group": "slide",
         "files": {"step": "parts/p1.step", "stl": "parts/p1.stl"}}]}, ensure_ascii=False), encoding="utf-8")
    (src / "parts" / "p1.step").write_text("ISO-10303-21;")
    (src / "parts" / "p1.stl").write_text("solid p1")
    if edges:
        (src / "edges.json").write_text('{"parts": {}}')
        (src / "edges.bin").write_bytes(b"\0" * 8)
    return src


def make_sim(root: Path) -> Path:
    sd = root / "simsrc" / "sim"
    sd.mkdir(parents=True)
    (sd / "machine.mjs").write_text(MACHINE)
    (sd / "helper.mjs").write_text("export const helper = 1;\n")
    (sd / "motion.json").write_text('{"actuators": {}}')
    (sd / "site.json").write_text(json.dumps({"entry": "machine.mjs", "files": ["machine.mjs", "helper.mjs"], "extra": ["motion.json"], "fw": "../fw"}))
    fw = root / "simsrc" / "fw"
    fw.mkdir()
    (fw / "index.json").write_text('["m"]')
    (fw / "m.mjs").write_text("export default function () {}\n")
    (fw / "m.wasm").write_bytes(b"\0asm")
    return sd


def make_cycle(root: Path) -> Path:
    mo = root / "cycle.json"
    mo.write_text(json.dumps({"cycle": {"period_s": 2, "loop": True, "groups": {"slide": {"axis": "x", "travel_mm": 10, "segments": [[0, 1, 0, 1]]}}}}))
    return mo


def files_of(out: Path):
    return sorted(str(p.relative_to(out)).replace(os.sep, "/") + ("/" if p.is_dir() else "") for p in out.rglob("*"))


def conf_of(out: Path) -> dict:
    return json.loads((out / "config.json").read_text(encoding="utf-8"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="shared_viewer_"))

    def tearDown(self):
        subprocess.run(["chmod", "-R", "u+w", str(self.tmp)], stderr=subprocess.DEVNULL)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build_ok(self, src, out, *extra):
        r = build(src, out, "--flash", "none", "--kit", "none", "--feedback", "none", *extra)
        self.assertEqual(r.returncode, 0, f"build.py {' '.join(map(str, extra))} failed:\n{r.stdout}\n{r.stderr}")
        return out

    def full_build(self, name="site", *extra):
        """edges + --sim + --motion/--world/--world-data: every kind of project data at once."""
        src = make_input(self.tmp) if not (self.tmp / "in").exists() else self.tmp / "in"
        sd = make_sim(self.tmp) if not (self.tmp / "simsrc").exists() else self.tmp / "simsrc" / "sim"
        mo = make_cycle(self.tmp)
        world = self.tmp / "world.mjs"; world.write_text("export function mount() { return { update() {} }; }\n")
        wd = self.tmp / "world_data.json"; wd.write_text('{"k": 1}')
        return self.build_ok(src, self.tmp / name, "--sim", sd, "--motion", mo, "--world", world, "--world-data", wd, "--title", "Project X — Ø 12", *extra)


class SharedMode(Base):
    """By default --out holds project data and project modules only: public/ holds no viewer code."""

    def test_out_holds_no_viewer_code(self):
        out = self.full_build()
        present = [name for name in VIEWER_CODE if (out / name).exists()]
        self.assertEqual(present, [], f"viewer code written into the project's site dir: {present}")

    def test_out_holds_only_project_data_and_modules(self):
        out = self.full_build()
        allowed = DATA_FILES | SIM_DATA | {"sim/"}
        extra = [f for f in files_of(out) if f not in allowed and not f.startswith(DATA_TREES) and f.rstrip("/") not in ("parts", "fw")]
        self.assertEqual(extra, [], "files that are neither project data nor the machine's own modules")

    def test_project_data_is_all_there(self):
        out = self.full_build()
        for f in ["model.glb", "parts.json", "parts/p1.step", "parts/p1.stl", "edges.json", "edges.bin", "config.json", "motion.json",
                  "world.js", "world_data.json", "sim/config.json", "sim/machine.js", "sim/helper.js", "sim/motion.json", "fw/index.json", "fw/m.wasm"]:
            self.assertTrue((out / f).is_file(), f"missing project data {f}")

    def test_minimal_build_has_no_viewer_code_either(self):
        out = self.build_ok(make_input(self.tmp, edges=False), self.tmp / "site")
        self.assertEqual(sorted(f for f in files_of(out) if not f.startswith("parts/")), ["config.json", "model.glb", "parts.json"])

    def test_machine_module_imports_the_shared_runtime(self):
        """sim/machine.js must import the runtime served at /_viewer/vc/ (one module instance per page), never a project-local sim/vc/ copy."""
        out = self.full_build()
        js = (out / "sim" / "machine.js").read_text(encoding="utf-8")
        specs = re.findall(r"""(?:import|from)\s*['"]([^'"]+)['"]""", js)
        page = "/demo-proj/"                                   # the project page on the stack
        mod_url = page + "sim/machine.js"
        imap = {}
        m = re.search(r'<script type="importmap">(.*?)</script>', (HERE / "index.html").read_text(encoding="utf-8"), re.S)
        if m:
            imap = json.loads(m.group(1)).get("imports", {})

        def resolve(spec):
            if spec.startswith(("./", "../", "/")):
                return urllib.parse.urljoin(mod_url, spec)
            for k in sorted(imap, key=len, reverse=True):          # bare specifier through the shared page's import map
                if spec == k or (k.endswith("/") and spec.startswith(k)):
                    return urllib.parse.urljoin(page, imap[k] + spec[len(k):])
            return spec
        rt = [resolve(s) for s in specs if "devices" in s]
        self.assertTrue(rt, f"the runtime import vanished from sim/machine.js: {specs}")
        for u in rt:
            self.assertRegex(u, r"^/_viewer/vc/devices\.m?js$", f"runtime import resolves to {u}, not the shared /_viewer/vc/ runtime")
        helper = [resolve(s) for s in specs if "helper" in s]
        self.assertEqual(helper, ["/demo-proj/sim/helper.js"], "the machine's own module stays project-relative")


class ConfigJson(Base):
    """What index.html used to get baked in now comes from config.json."""

    def test_title_from_flag_and_default_from_assembly_root(self):
        self.assertEqual(conf_of(self.full_build())["title"], "Project X — Ø 12")
        out = self.build_ok(self.tmp / "in", self.tmp / "site2")
        self.assertEqual(conf_of(out)["title"], "Synthetic assembly · Ø20")

    def volatile_keys(self, a: dict, b: dict):
        return {k for k in set(a) | set(b) if a.get(k) != b.get(k)}

    def test_stamp_changes_on_every_build(self):
        """A stamp that cache-busts data URLs: two builds of the same input must differ in it."""
        src = make_input(self.tmp)
        c1 = conf_of(self.build_ok(src, self.tmp / "s1"))
        c2 = conf_of(self.build_ok(src, self.tmp / "s1"))
        changed = self.volatile_keys(c1, c2)
        self.assertTrue(changed, f"config.json identical across two builds: no build stamp ({sorted(c1)})")
        for k in changed:
            self.assertIn(k, c1, "the stamp is present in every build")
            self.assertTrue(str(c1[k]) and str(c2[k]), "stamp is non-empty")

    def stable(self, c: dict, volatile) -> dict:
        return {k: v for k, v in c.items() if k not in volatile}

    def _volatile(self, src):
        a = conf_of(self.build_ok(src, self.tmp / "v1"))
        b = conf_of(self.build_ok(src, self.tmp / "v2"))
        return self.volatile_keys(a, b)

    def test_edges_flag(self):
        plain = make_input(self.tmp / "a", edges=False)
        with_e = make_input(self.tmp / "b", edges=True)
        vol = self._volatile(plain)
        c0 = self.stable(conf_of(self.build_ok(plain, self.tmp / "e0")), vol)
        c1 = self.stable(conf_of(self.build_ok(with_e, self.tmp / "e1")), vol)
        self.assertNotEqual(c0, c1, "config.json does not tell the page that baked edges exist")

    def test_sim_flag(self):
        src = make_input(self.tmp)
        vol = self._volatile(src)
        c0 = self.stable(conf_of(self.build_ok(src, self.tmp / "n0")), vol)
        c1 = self.stable(conf_of(self.build_ok(src, self.tmp / "n1", "--sim", make_sim(self.tmp))), vol)
        self.assertNotEqual(c0, c1, "config.json does not tell the page there is a sim (sim/config.json)")

    def test_cycle_flag(self):
        src = make_input(self.tmp)
        vol = self._volatile(src)
        c0 = self.stable(conf_of(self.build_ok(src, self.tmp / "c0")), vol)
        c1 = self.stable(conf_of(self.build_ok(src, self.tmp / "c1", "--motion", make_cycle(self.tmp))), vol)
        self.assertNotEqual(c0, c1, "config.json does not tell the page there is a cycle (motion.json)")


class Standalone(Base):
    """--standalone = shared-mode output + index.html + <out>/_viewer/ (a copy of the shared code)."""

    def test_standalone_adds_index_and_viewer_copy(self):
        shared = self.full_build("shared")
        alone = self.full_build("alone", "--standalone")
        self.assertTrue((alone / "index.html").is_file(), "--standalone writes index.html")
        self.assertTrue((alone / "_viewer").is_dir(), "--standalone writes the shared code under _viewer/")
        self.assertTrue((alone / "_viewer" / "vendor" / "three.module.min.js").is_file(), "three.js under _viewer/vendor/")
        self.assertTrue(any((alone / "_viewer" / "vc").glob("sim.*")), "with --sim the runtime is under _viewer/vc/")
        # the page probes apk/latest.json (the machine's released Android app); a standalone copy never gets a real one, and an empty
        # one keeps a plain static server from answering 404, which the browser logs as an error (the stack's nginx answers 204)
        self.assertEqual(sorted(f for f in files_of(alone) if f.startswith("apk")), ["apk/", "apk/latest.json"])
        self.assertEqual((alone / "apk" / "latest.json").read_text(encoding="utf-8"), "{}")
        # the same for the page language: the stack answers 204 for a project without public/locale.json; a plain static server
        # would 404, so a standalone site gets an empty one ({} = the Russian default)
        self.assertEqual((alone / "locale.json").read_text(encoding="utf-8"), "{}")
        self.assertFalse((shared / "locale.json").exists(), "shared mode writes no locale.json: in production it belongs to the router")
        rest = sorted(f for f in files_of(alone) if f not in ("index.html", "locale.json") and not f.startswith(("_viewer", "apk")))
        self.assertEqual(rest, files_of(shared), "apart from index.html, _viewer/, the empty apk/latest.json and the empty locale.json, "
                                                 "a standalone site holds exactly the shared-mode files")

    def test_standalone_viewer_copy_has_every_module_the_page_imports(self):
        """A plain static server has no .js -> .mjs fallback: each /_viewer/<name>.js index.html imports (i18n.js among them)
        must be in the copy under that .js name."""
        alone = self.full_build("alone", "--standalone")
        page = (alone / "index.html").read_text(encoding="utf-8")
        wanted = sorted(set(re.findall(r"""["']/_viewer/((?!vendor/|vc/)[\w/.-]+\.js)["']""", page)))
        self.assertIn("i18n.js", wanted, "the page imports the language module")
        missing = [w for w in wanted if not (alone / "_viewer" / w).is_file()]
        self.assertEqual(missing, [], f"modules the page imports but the standalone copy lacks: {missing}")

    def test_standalone_viewer_copy_has_only_browser_code(self):
        alone = self.full_build("alone", "--standalone")
        bad = [f for f in files_of(alone / "_viewer") if f.endswith(".py") or ".test." in f or f.rsplit("/", 1)[-1].startswith("test_")
               or f.endswith("node.mjs") or f.endswith("node.js") or "__pycache__" in f]
        self.assertEqual(bad, [], "build scripts, tests and node-only runtime are not browser code")


if __name__ == "__main__":
    unittest.main()
