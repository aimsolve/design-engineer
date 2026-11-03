"""The local viewer server (`de serve`): one project on 127.0.0.1, laid out as the platform serves it, so the standard page works unchanged.

  /                        -> 302 /<project>/
  /<project>/...           the project's public/ (built by `de build`); /<project>/ = the standard viewer page
  /_viewer/<file>          the viewer code, straight from the framework (as nginx serves /_viewer/ on the platform)
  POST /feedback/<project> a note drawn on the model -> .engineer/inbox/<time>-note.json + .jpg (the agent reads them)
  /kit/<project>/...       download kits (de/kit.py)
  /hw/<project>/event|cmd  the page's side of a board on the desk; /api/hw/* the `hw` CLI's side (de/hw.py, as the router)
Bound to 127.0.0.1, and that alone is not a guard: any web page the user opens can send requests to 127.0.0.1, and a note
written here reaches the agent as the user's own words. So every request must name this server as its Host (no DNS
rebinding), a browser request from another site is refused (Sec-Fetch-Site / Origin), every POST must be JSON (a
cross-site JSON POST needs a preflight this server never grants), and the `hw` CLI side (/api/hw/*) needs the token that
`de serve` keeps in .engineer/serve.json."""
import base64
import hmac
import json
import re
import secrets
import socket
import sys
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from de import hw, kit

ROOT = Path(__file__).resolve().parents[1]
VIEWER = ROOT / "skills" / "web-viewer" / "templates" / "assembly"
RUNTIME = ROOT / "skills" / "virtual-controllers" / "runtime"
TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json", ".glb": "model/gltf-binary",
         ".wasm": "application/wasm", ".html": "text/html; charset=utf-8", ".css": "text/css", ".svg": "image/svg+xml",
         ".png": "image/png", ".jpg": "image/jpeg", ".bin": "application/octet-stream"}
MAX_BODY = 8_000_000
EMPTY_OK = ("apk/latest.json", "locale.json")   # probed by the page; the platform answers 204 when absent


def viewer_file(rel):
    """/_viewer/<rel> -> a framework file, browser code only (as the platform's nginx: no tests, no node-only runtime)."""
    base = VIEWER
    if rel.startswith("vc/"):
        base, rel = RUNTIME, rel[3:]
    name = rel.rsplit("/", 1)[-1]
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return None
    ok = rel == "index.html" or rel.startswith("vendor/") or (name.endswith((".js", ".mjs")) and ".test." not in name
                                                               and not name.startswith("test_") and name != "node.mjs")
    if not ok:
        return None
    for cand in [rel] + ([rel[:-3] + ".mjs"] if rel.endswith(".js") else []):
        f = (base / cand).resolve()
        if f.is_relative_to(base.resolve()) and f.is_file():   # `/_viewer//abs/path.js` would leave the folder
            return f
    return None


def note(d, b):
    """A drawn note -> files in .engineer/inbox/; (code, json)."""
    if not isinstance(b, dict):
        return 400, {"error": "JSON expected"}
    comment = str(b.get("comment") or "").strip()[:4000]
    if not comment:
        return 400, {"error": "the note has no text"}
    pics = []
    for x in [b.get("image")] + (b.get("images") if isinstance(b.get("images"), list) else [])[:3]:
        if not x:
            continue
        if not str(x).startswith("data:image/jpeg;base64,"):
            return 400, {"error": "the picture is not a JPEG"}
        try:
            data = base64.b64decode(str(x).split(",", 1)[1], validate=True)
        except ValueError:
            return 400, {"error": "the picture is damaged"}
        if not data.startswith(b"\xff\xd8"):
            return 400, {"error": "the picture is not a JPEG"}
        pics.append(data)
    view = b.get("view") if isinstance(b.get("view"), dict) else {}
    inbox = d / ".engineer" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    stem = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    names = []
    for i, data in enumerate(pics):   # the view on screen at send is the first (`image`), the earlier views follow
        f = inbox / (f"{stem}-note.jpg" if i == 0 else f"{stem}-note-view{i}.jpg")
        f.write_bytes(data)
        names.append(f.name)
    parts = [str(x) for x in view.get("parts") or [] if isinstance(x, str)]
    rec = {"time": datetime.now().isoformat(timespec="seconds"), "comment": comment, "pictures": names,
           "parts": parts, "part": view.get("part"), "node": view.get("node"), "view": view}
    (inbox / f"{stem}-note.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"note -> .engineer/inbox/{stem}-note.json: {comment[:80]}", flush=True)
    return 200, {"ok": True}


class Handler(BaseHTTPRequestHandler):
    project: Path = Path(".")
    slug = "project"
    token = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def gone(self):
        return False

    def send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def file(self, p):
        if p is None or not p.is_file():
            return self.send(404, {"error": "not found"})
        self.send(200, p.read_bytes(), TYPES.get(p.suffix, "application/octet-stream"))

    def body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n < 0 or n > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(n) or b"null")
        except ValueError:
            return None

    def refused(self):
        """Why this request is not from the user's own page or `hw` CLI on this machine, or None."""
        port = self.server.server_address[1]
        own = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if (self.headers.get("Host") or "") not in own:
            return "wrong host"
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            return "cross-site request"
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc not in own:
            return "foreign origin"
        if self.command == "POST" and (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return "a POST must be application/json"
        return None

    def route(self):
        u = urllib.parse.urlsplit(self.path)
        path, q = urllib.parse.unquote(u.path), dict(urllib.parse.parse_qsl(u.query))
        seg = path.split("/")
        d, s, pid = self.project, self.slug, self.slug
        why = self.refused()
        if why:
            return self.send(403, {"error": why})
        if path.startswith("/api/") and not hmac.compare_digest(self.headers.get("Authorization") or "", f"Bearer {self.token}"):
            return self.send(401, {"error": "token"})
        if path == "/_de/ping":
            return self.send(200, {"project": str(d), "slug": s})
        if path == "/":
            return self.send(302, b"", "text/plain", {"Location": f"/{s}/"})
        if path.startswith("/_viewer/"):
            return self.file(viewer_file(path[len("/_viewer/"):]))
        if path == f"/feedback/{s}" and self.command == "POST":
            return self.send(*note(d, self.body()))
        if path.startswith(f"/kit/{s}/") and self.command in ("GET", "HEAD"):
            rest = path[len(f"/kit/{s}/"):]
            if rest == "status":
                return self.send(200, kit.status(d, q.get("scope")))
            code, out = kit.get(d, rest, d.name)
            if code == 200 and isinstance(out, tuple):
                data, name, ctype = out
                return self.send(200, data, ctype, {"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name)})
            return self.send(code, out)
        if len(seg) == 4 and seg[1] == "hw" and seg[2] == s:
            if self.command == "POST" and seg[3] == "event":
                return self.send(*hw.event(pid, self.body() or {}, time.time(), d))
            if self.command == "GET" and seg[3] == "cmd":
                after = int(q.get("after") or 0) if str(q.get("after") or "0").isdigit() else 0
                return self.send(200, hw.poll(pid, q.get("viewer", ""), after, self.gone))
        if path.startswith("/api/hw/"):
            if path == "/api/hw/status":
                return self.send(200, hw.status(pid, time.time()))
            if path == "/api/hw/cmd" and self.command == "POST":
                b = self.body() or {}
                return self.send(*hw.queue(pid, b.get("op"), b.get("card"), time.time(), b.get("port"), b.get("fw")))
            m = re.fullmatch(r"/api/hw/cmd/(\d+)/wait", path)
            if m:
                return self.send(*hw.wait(pid, int(m[1]), self.gone))
            m = re.fullmatch(r"/api/hw/log/([^/]+)", path)
            if m:
                tail = int(q.get("tail") or 50) if str(q.get("tail") or "50").isdigit() else 50
                return self.send(200, hw.log(pid, m[1], tail))
        if path.startswith(f"/{s}/") and self.command in ("GET", "HEAD"):
            rest = path[len(f"/{s}/"):]
            pub = d / "public"
            if rest in ("", "index.html"):
                if (pub / "index.html").is_file():   # a project's own page (as on the platform)
                    return self.file(pub / "index.html")
                if (pub / "config.json").is_file():
                    return self.file(VIEWER / "index.html")
                return self.send(200, WAIT_PAGE.encode(), "text/html; charset=utf-8")
            if rest in EMPTY_OK and not (pub / rest).is_file():
                return self.send(204)
            p = (pub / rest).resolve()
            if pub.resolve() not in p.parents:
                return self.send(404, {"error": "not found"})
            return self.file(p / "index.html" if p.is_dir() else p)
        return self.send(404, {"error": "no route"})

    def do_GET(self):
        self.route()

    def do_HEAD(self):
        self.route()

    def do_POST(self):
        self.route()


WAIT_PAGE = """<!doctype html><meta charset="utf-8"><title>Design engineer</title><body style="font:16px system-ui;margin:3em">
<p>No model built yet: run <code>de build</code> in the project folder. This page reloads by itself.</p>
<script>setInterval(async()=>{try{if((await fetch('config.json',{cache:'no-store'})).ok)location.reload()}catch(e){}},3000)</script>"""


def free_port_from(port):
    for p in range(port, port + 100):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return 0


def serve(d, port, slug):
    d = Path(d).resolve()
    token = secrets.token_hex(16)
    h = type("H", (Handler,), {"project": d, "slug": slug, "token": token})
    srv = ThreadingHTTPServer(("127.0.0.1", port), h)
    srv.daemon_threads = True
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/{slug}/"
    import os
    (d / ".engineer").mkdir(exist_ok=True)
    sj = d / ".engineer" / "serve.json"
    sj.touch(mode=0o600)
    sj.write_text(json.dumps({"pid": os.getpid(), "port": port, "url": url, "token": token}))
    print(f"viewer: {url}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if json.loads((d / ".engineer" / "serve.json").read_text()).get("pid") == os.getpid():
                (d / ".engineer" / "serve.json").unlink()
        except (OSError, ValueError):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(serve(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]))
