"""build.py must survive a read-only framework mount (/opt/design-engineer is :ro): the site it writes has to be rebuildable.

  python3 -m unittest test_readonly_source.py      (from this folder)
"""
import os, shutil, stat, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ro_tree(root: Path) -> None:
    for p in sorted(root.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        p.chmod(0o555 if p.is_dir() else 0o444)
    root.chmod(0o555)


def rw_tree(root: Path) -> None:
    for p in [root, *root.rglob("*")]:
        p.chmod(0o755 if p.is_dir() else 0o644)


class ReadOnlySource(unittest.TestCase):
    def test_second_build_from_read_only_template(self):
        tmp = Path(tempfile.mkdtemp(prefix="ro_src_"))
        try:
            fw = tmp / "fw"
            shutil.copytree(HERE, fw / "tpl", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(HERE.parent.parent / "templates" / "assembly" / "vendor", fw / "tpl" / "vendor", dirs_exist_ok=True)
            src = tmp / "in"
            (src / "parts").mkdir(parents=True)
            (src / "model.glb").write_bytes(b"glb")
            (src / "parts.json").write_text('{"assembly": {"root": "T"}, "parts": []}', encoding="utf-8")
            (src / "parts" / "a.glb").write_bytes(b"p")
            ro_tree(fw)
            out = tmp / "site"
            cmd = [sys.executable, str(fw / "tpl" / "build.py"), "--in", str(src), "--out", str(out), "--flash", "none", "--standalone"]   # --standalone: the build that still copies framework files
            for n in (1, 2):
                r = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                self.assertEqual(r.returncode, 0, f"build {n} failed:\n{r.stdout}\n{r.stderr}")
            self.assertTrue((out / "index.html").is_file())
            bad = [str(p) for p in [out, *out.rglob("*")] if not os.access(p, os.W_OK)]
            self.assertEqual(bad, [], "output must be user-writable")
        finally:
            for p in (tmp / "fw",):
                if p.exists():
                    rw_tree(p)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
