"""Test helper (not shipped to sites): serve built project sites the way the platform's nginx does, plus small shared bits
(the demo model the browser tests default to, skipping, the viewer's texts from i18n.mjs).

Rules emulated (the platform tests the nginx side against a real nginx):
  /_viewer/<path>      -> <framework>/skills/web-viewer/templates/assembly/<path>
  /_viewer/vc/<path>   -> <framework>/skills/virtual-controllers/runtime/<path>
      <name>.js is answered by <name>.js or, if absent, <name>.mjs; .js and .mjs go out as JavaScript.
      Only browser code is reachable: *.js / *.mjs (not test files, not node.mjs), vendor/**, index.html. Everything else 404.
  /<id>/ and /<id>/index.html -> <projects>/<id>/index.html if the project has one, else the shared viewer index.html
  /<id>/<rest>         -> <projects>/<id>/<rest>
  /<id>/apk/latest.json of a project with no released APK -> 204, not 404 (the page probes it everywhere, a 404 is a console error)
Every answer carries Cache-Control: no-cache (as stack.conf does for project pages). Compression and the exact MIME types are checked only
against the real nginx.
"""
import functools, hashlib, json, os, re, shutil, socket, struct, subprocess, sys, tempfile, threading, time, urllib.parse, zlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]                       # design-engineer checkout (framework root: skills/...)
DEMO = REPO / "examples" / "nema-bracket"    # the demo machine the browser tests default to (a small build123d project)
MODEL_ENV = "VIEWER_TEST_MODEL"              # importer output dir (model.glb, parts.json, parts/) of a richer model
SIM_ENV = "VIEWER_TEST_SIM"                  # that machine's sim dir (site.json, firmware): for the tests of the machine layer
TEMPLATE_REL = Path("skills/web-viewer/templates/assembly")
RUNTIME_REL = Path("skills/virtual-controllers/runtime")
PROJECT_RE = re.compile(r"^/([a-z0-9]+(?:-[a-z0-9]+)+)/(.*)$")
NO_CONTENT = object()   # _resolve: answer 204 instead of 404 (an optional file the page probes)
TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json", ".glb": "model/gltf-binary", ".wasm": "application/wasm",
         ".html": "text/html; charset=utf-8", ".bin": "application/octet-stream", ".css": "text/css", ".step": "application/octet-stream", ".stl": "application/octet-stream"}


def browser_code(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if rel == "index.html" or rel.startswith("vendor/"):
        return True
    if not name.endswith((".js", ".mjs")):
        return False
    return ".test." not in name and not name.startswith("test_") and name != "node.mjs"


class StackHandler(SimpleHTTPRequestHandler):
    framework: Path = REPO
    projects: Path = Path(".")

    def log_message(self, *a):
        pass

    def _send_file(self, p: Path, head_only=False):
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(p.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def _resolve(self):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if ".." in path.split("/"):
            return None
        if path.startswith("/_viewer/"):
            rel = path[len("/_viewer/"):]
            base = self.framework / TEMPLATE_REL
            if rel.startswith("vc/"):
                base, rel = self.framework / RUNTIME_REL, rel[3:]
                if rel.startswith("vendor/") or rel == "index.html":
                    return None
            if not browser_code(rel):
                return None
            for cand in [rel] + ([rel[:-3] + ".mjs"] if rel.endswith(".js") else []):
                if (base / cand).is_file():
                    return base / cand
            return None
        m = PROJECT_RE.match(path)
        if not m:
            return None
        proj, rest = m.group(1), m.group(2)
        root = self.projects / proj
        if rest in ("apk/latest.json", "locale.json") and not (root / rest).is_file():
            return NO_CONTENT   # stack.conf: try_files ... =204, whatever the project is
        if not root.is_dir():
            return None
        if rest in ("", "index.html"):
            own = root / "index.html"
            if own.is_file():
                return own
            return self.framework / TEMPLATE_REL / "index.html" if (root / "config.json").is_file() else None   # nginx: shared page only for a built model
        p = root / rest
        if p.is_dir():
            p = p / "index.html"
        return p if p.is_file() else None

    def _send_204(self):
        self.send_response(204)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def do_GET(self):
        p = self._resolve()
        if p is NO_CONTENT:
            self._send_204()
        elif p is None:
            self.send_error(404)
        else:
            self._send_file(p)

    def do_HEAD(self):
        p = self._resolve()
        if p is NO_CONTENT:
            self._send_204()
        elif p is None:
            self.send_error(404)
        else:
            self._send_file(p, head_only=True)


def serve_stack(projects: Path, framework: Path = REPO):
    """Start the emulated stack web server in a thread. Returns (server, base_url). Stop with server.shutdown()."""
    h = type("H", (StackHandler,), {"projects": Path(projects), "framework": Path(framework)})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve_plain(root: Path):
    """`python3 -m http.server` on <root> (a --standalone site): returns (Popen, base_url). Terminate it when done."""
    port = free_port()
    pr = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "-d", str(root)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return pr, f"http://127.0.0.1:{port}"


def build(src, out, *extra, build_py: Path = HERE / "build.py"):
    """Run build.py; returns CompletedProcess (never raises)."""
    cmd = [sys.executable, str(build_py), "--in", str(src), "--out", str(out), *map(str, extra)]
    return subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def link_input(src: Path, dst: Path, parts_json=None) -> Path:
    """An importer-output dir that symlinks the big files of <src> (model.glb, parts/, edges.*) and optionally carries its own parts.json."""
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("model.glb", "parts", "edges.json", "edges.bin"):
        if (src / name).exists():
            (dst / name).symlink_to(src / name)
    if parts_json is None:
        (dst / "parts.json").symlink_to(src / "parts.json")
    else:
        import json
        (dst / "parts.json").write_text(json.dumps(parts_json, ensure_ascii=False), encoding="utf-8")
    return dst


def png_pixels(png: bytes):
    """Minimal PNG decoder (8-bit RGB/RGBA, non-interlaced: what Chromium screenshots are). Returns (w, h, channels, rows of bytes)."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w = 8, b"", 0
    while pos < len(png):
        n, typ = struct.unpack(">I4s", png[pos:pos + 8])
        body = png[pos + 8:pos + 8 + n]
        if typ == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            assert depth == 8 and ctype in (2, 6), (depth, ctype)
            ch = 3 if ctype == 2 else 4
        elif typ == b"IDAT":
            idat += body
        pos += 12 + n
    raw, stride, rows, prev = zlib.decompress(idat), w * ch, [], bytearray(w * ch)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - ch] if i >= ch else 0
            b = prev[i]
            c = prev[i - ch] if i >= ch else 0
            if f == 1: line[i] = (line[i] + a) & 255
            elif f == 2: line[i] = (line[i] + b) & 255
            elif f == 3: line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line)); prev = line
    return w, h, ch, rows


def non_background_share(png: bytes, step: int = 4) -> float:
    """Share of sampled pixels that differ (by > 24 in any channel) from the most common colour: ~0 = blank canvas."""
    w, h, ch, rows = png_pixels(png)
    px = [rows[y][x * ch:x * ch + 3] for y in range(0, h, step) for x in range(0, w, step)]
    from collections import Counter
    bg = Counter(px).most_common(1)[0][0]
    diff = sum(1 for p in px if max(abs(p[i] - bg[i]) for i in range(3)) > 24)
    return diff / max(1, len(px))


# ---------------------------------------------------------------- what a browser test runs on

def skip(reason: str):
    """The test cannot run here: say why and exit 0 (a skip is not a failure)."""
    print(f"SKIP: {reason}", flush=True)
    sys.exit(0)


def demo_input() -> Path:
    """Importer output (model.glb, parts.json, parts/, edges.*) of the demo machine: a temp copy of examples/nema-bracket built with
    `de build` (about a minute), cached per content of the example in <tmp>/de-viewer-demo-<hash>/. Skips the test if it cannot be built."""
    import fcntl
    h = hashlib.sha256()
    for p in sorted(DEMO.rglob("*")):
        if p.is_file() and not ({"__pycache__", "build", "public", ".engineer"} & set(p.relative_to(DEMO).parts)):
            h.update(p.relative_to(DEMO).as_posix().encode() + b"\0" + p.read_bytes())
    root = Path(tempfile.gettempdir()) / f"de-viewer-demo-{h.hexdigest()[:12]}"
    proj = root / DEMO.name
    out = proj / "build" / "import"
    root.mkdir(parents=True, exist_ok=True)
    with open(root / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)                 # tests running in parallel build it once
        if (root / "ok").is_file() and (out / "parts.json").is_file():
            return out
        shutil.rmtree(proj, ignore_errors=True)
        shutil.copytree(DEMO, proj, ignore=shutil.ignore_patterns("__pycache__", "build", "public", ".engineer"))
        de = Path(os.environ.get("DE") or REPO / "local" / "bin" / "de")
        print(f"building the demo machine in {proj} ({de.name} build)...", flush=True)
        try:
            r = subprocess.run([str(de), "build", str(proj)], capture_output=True, text=True, timeout=1800,
                               env={k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"})
            rc, log = r.returncode, (r.stdout + r.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            rc, log = -1, str(e)
        if rc or not (out / "parts.json").is_file():
            skip(f"the demo machine {DEMO.name} did not build (de build: rc {rc}): {log[-400:]}")
        (root / "ok").write_text("ok")
    return out


def model_dir(given=None, needs: str = "") -> Path:
    """The importer output a browser test runs on: --in, else $VIEWER_TEST_MODEL, else the demo machine.
    needs = what the test needs that the demo machine has not (drawings, a sim, ...): then, with neither given, the test skips saying so."""
    if given:
        return Path(given).expanduser()
    if os.environ.get(MODEL_ENV):
        return Path(os.environ[MODEL_ENV]).expanduser()
    if needs:
        skip(f"needs {needs}, which the demo machine has not: point ${MODEL_ENV} (or --in) at an importer output dir that has")
    return demo_input()


def sim_dir(given=None, needs: str = "a machine sim dir (site.json, boards, firmware)") -> Path:
    """The machine sim dir a test of the machine layer runs on: --sim, else $VIEWER_TEST_SIM; the demo machine has none, so else a skip."""
    if given:
        return Path(given).expanduser()
    if os.environ.get(SIM_ENV):
        return Path(os.environ[SIM_ENV]).expanduser()
    skip(f"needs {needs}: point ${SIM_ENV} (or --sim) at a machine's sim/ dir, and ${MODEL_ENV} at its importer output")


def parts_of(src) -> list:
    """parts.json `parts` of an importer output dir."""
    return json.loads((Path(src) / "parts.json").read_text(encoding="utf-8"))["parts"]


@functools.lru_cache(maxsize=None)
def ui_texts(lang: str = "ru", template: Path = HERE) -> dict:
    """The viewer's own texts in one language, read from <template>/i18n.mjs with node: tests compare the page with these, not with
    literals, so a reworded text in i18n.mjs never breaks a test."""
    js = f"const m = await import({json.dumps(Path(template, 'i18n.mjs').resolve().as_uri())}); process.stdout.write(JSON.stringify(m.DICT));"
    r = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(f"node could not read i18n.mjs: {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)[lang]
