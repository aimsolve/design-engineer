#!/usr/bin/env python3
"""Real-browser check: a viewer tab with board flashing but no board connected must not hold a /hw/<pid>/cmd long-poll.

The bug (seen in production): 7 viewer tabs of flashing projects were open in Chrome. Each Web-Serial tab long-polled GET /hw/<pid>/cmd
(~25 s hold) in a loop even with no board connected. Over HTTP/1.1 Chrome allows 6 connections per host, so these polls held every
slot and each other request of that browser (another project's page, its scripts, a kit download) waited ~25 s.
The router only sends a command to a viewer whose reported ports include the target board, so a tab without a granted port can never
receive one.

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_hw_idle_poll.py [--hold 30] [--budget 4] [--headed]
(first run: `... --with playwright playwright install chromium`)

Harness (no real hardware, no real router process):
  - a local HTTP/1.1 keep-alive server on 127.0.0.1 serving
      /_viewer/<name>.js|.mjs   the viewer modules from this folder (hw.js -> hw.mjs, like the stack's .js -> .mjs fallback)
      /<pid>/                   a page that mounts hw.mjs exactly like index.html's initHw() does (config.json has `flash`), plus a few scripts
      /<pid>/fw/index.json      no published firmware
      /hw/<pid>/event, /hw/<pid>/cmd   the REAL agent/de/hw.py event() / poll() / queue(), POLL_S shortened ([1],[2]: --short-hold) or held --hold ([3])
      /kit/<pid>/<scope>        a 256 KB download
  - a fake `navigator.serial` (init script): `?ports=N` on the page URL = N granted ports at load (10c4:ea60, CP210x, no serial);
    window.__serialTest.plug() / unplug() add / remove a granted port and fire connect / disconnect like Chrome does;
    window.__hwSessionFor(port) is a fake session (identify() counts calls in window.__identified).
  - every tab of one check lives in ONE browser context (one profile), so they share Chrome's per-host connection pool.

Checks (the number is the done-when condition it serves):
  [0] harness: 6 held requests in this browser really starve a 7th one (otherwise [3] proves nothing)
  [1] a flashing tab with no granted port has no /hw/<pid>/cmd request open (heartbeat POSTs to /event may go on)
  [2] a port appears -> the tab long-polls /cmd, a command the router queues for that port reaches it and its result is posted back;
      the last port goes away -> the tab stops long-polling. Also: a tab loaded with a port already granted gets commands.
  [3] 7 such tabs with no boards open; an 8th tab loads its page and fetches a kit download within --budget seconds in total
Exit code 1 on any failed check.
"""
import argparse, importlib.util, json, select, socket, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / "de" / "hw.py").exists())
spec = importlib.util.spec_from_file_location("router_hw_for_viewer_test", ROOT / "de" / "hw.py")
hw = importlib.util.module_from_spec(spec); spec.loader.exec_module(hw)

ap = argparse.ArgumentParser()
ap.add_argument("--hold", type=float, default=30, help="seconds /hw/<pid>/cmd holds a request during the measurement of check [3] (prod: 25)")
ap.add_argument("--short-hold", type=float, default=4, help="seconds /hw/<pid>/cmd holds a request in checks [1] and [2]")
ap.add_argument("--budget", type=float, default=4, help="[3]: max seconds for the 8th tab's page load + kit download")
ap.add_argument("--headed", action="store_true")
a = ap.parse_args()

PORT_KEY = "10c4:ea60"   # what the fake port reports (CP210x, no serial) -> the key hw.mjs sends and the router targets
rows, ok_all = [], True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- the test server (HTTP/1.1 keep-alive, real agent/de/hw.py behind /hw/)
_lock = threading.Lock()
open_cmd, started_cmd, events = {}, {}, {}   # pid -> count of /cmd requests held right now / ever started; pid -> [event types]


def _bump(d, k, n):
    with _lock:
        d[k] = d.get(k, 0) + n


SCRIPTS = 6   # small scripts each test page loads (a viewer page loads several)
PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>hw idle poll test</title>
{scripts}
</head><body>
<div id="panel"><section id="secHw" hidden><div class="sb"></div></section></div><div id="hwDock"></div>
<script type="module">
// what index.html's initHw() does when config.json has `flash`
const api = window.__viewer = {{ config: {{ flash: {{}} }} }};
window.__hwReady = import("/_viewer/hw.js")
  .then((m) => m.mountHw({{ api, L: {{}}, cfg: api.config }}))
  .then(() => true, (e) => {{ window.__hwErr = String((e && e.stack) || e); return false; }});
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *x):
        pass

    def client_gone(self):   # same test as router.py: the caller hung up, its socket reads EOF
        try:
            r, _, _ = select.select([self.connection], [], [], 0)
            return bool(r) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        seg = u.path.split("/")
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path.startswith("/_viewer/"):
            name = u.path[len("/_viewer/"):]
            f = HERE / (name[:-3] + ".mjs" if name.endswith(".js") else name)
            if "/" in name or not f.is_file():
                return self.send(404, {"error": "no file"})
            return self.send(200, f.read_bytes(), "text/javascript")
        if u.path == "/hold":   # [0] harness self-check
            time.sleep(float(q.get("s", "3")))
            return self.send(200, {"ok": True})
        if u.path == "/ping":
            return self.send(200, {"ok": True})
        if len(seg) >= 4 and seg[1] == "hw" and seg[3] == "cmd":
            pid = seg[2]
            _bump(started_cmd, pid, 1); _bump(open_cmd, pid, 1)
            try:
                after = int(q.get("after") or 0) if str(q.get("after") or "0").isdigit() else 0
                out = hw.poll(pid, q.get("viewer", ""), after, self.client_gone)
                return None if self.client_gone() else self.send(200, out)
            finally:
                _bump(open_cmd, pid, -1)
        if len(seg) >= 4 and seg[1] == "kit":
            return self.send(200, b"PK" + b"\0" * (256 * 1024), "application/zip")
        if len(seg) == 3 and seg[2] == "":   # /<pid>/
            scripts = "\n".join(f'<script src="s{i}.js"></script>' for i in range(SCRIPTS))
            return self.send(200, PAGE.format(scripts=scripts), "text/html; charset=utf-8")
        if len(seg) == 3 and seg[2].startswith("s") and seg[2].endswith(".js"):
            return self.send(200, f"window.__s = (window.__s || 0) + 1; // {seg[1]}/{seg[2]}\n", "text/javascript")
        if u.path.endswith("/fw/index.json"):
            return self.send(200, {"boards": []})
        return self.send(404, {"error": "no route"})

    def do_POST(self):
        u = urllib.parse.urlsplit(self.path)
        seg = u.path.split("/")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if len(seg) >= 4 and seg[1] == "hw" and seg[3] == "event":
            try:
                b = json.loads(body or b"{}")
            except ValueError:
                return self.send(400, {"error": "json"})
            with _lock:
                events.setdefault(seg[2], []).append(b.get("type"))
            code, out = hw.event(seg[2], b, time.time(), None)
            return self.send(code, out)
        return self.send(404, {"error": "no route"})


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"

# ---------------------------------------------------------------- fake Web Serial (installed before any page script runs)
INIT_JS = r"""
(() => {
  const n = +(new URLSearchParams(location.search).get('ports') || 0);
  const listeners = {};
  const mk = (i) => ({
    __i: i, connected: true,
    getInfo: () => ({ usbVendorId: 0x10c4, usbProductId: 0xea60 }),
    open: async () => {}, close: async () => {}, forget: async () => {},
    readable: null, writable: null, addEventListener() {}, removeEventListener() {},
  });
  const ports = Array.from({ length: n }, (_, i) => mk(i));
  const serial = {
    getPorts: async () => ports.slice(),
    requestPort: async () => { if (!ports.length) throw new DOMException('No port selected by the user.', 'NotFoundError'); return ports[0]; },
    addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener: (t, fn) => { listeners[t] = (listeners[t] || []).filter((f) => f !== fn); },
    onconnect: null, ondisconnect: null,
  };
  const fire = (type, port) => {
    const ev = new Event(type); Object.defineProperty(ev, 'port', { value: port });
    for (const f of (listeners[type] || []).slice()) { try { f(ev); } catch (e) { console.error(e); } }
    const h = serial['on' + type]; if (typeof h === 'function') { try { h(ev); } catch (e) { console.error(e); } }
  };
  try { Object.defineProperty(Navigator.prototype, 'serial', { get: () => serial, configurable: true }); }
  catch (e) { Object.defineProperty(navigator, 'serial', { value: serial, configurable: true }); }
  window.__serialTest = {
    plug() { const p = mk(ports.length); ports.push(p); fire('connect', p); return ports.length; },
    unplug() { const p = ports.pop(); if (p) { p.connected = false; fire('disconnect', p); } return ports.length; },
    count: () => ports.length,
  };
  window.__identified = 0;
  window.__hwSessionFor = (port) => {
    const subs = new Set();
    const s = {
      monitoring: false, monitorSpeed: 115200, lastKind: null,
      subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
      async flashBoard() { return { chip: 'ESP32' }; },
      async openMonitor() { s.monitoring = true; },
      async closeMonitor() { s.monitoring = false; },
      async resetBoard() {},
      async identify() { window.__identified++; return { chip: 'ESP32', description: 'ESP32-D0WD-V3 (fake test port)' }; },
    };
    return s;
  };
})();
"""


def new_ctx(b):
    ctx = b.new_context()
    ctx.add_init_script(INIT_JS)
    return ctx


def open_tab(ctx, pid, ports=0, timeout=120_000):
    page = ctx.new_page()
    page.on("pageerror", lambda e: print(f"  pageerror [{pid}]: {e}", flush=True))
    page.goto(f"{BASE}/{pid}/?ports={ports}", wait_until="load", timeout=timeout)
    mounted = page.evaluate("() => window.__hwReady")
    err = page.evaluate("() => window.__hwErr || null")
    if not mounted or err:
        raise RuntimeError(f"hw.mjs did not mount on /{pid}/: {err}")
    sup = page.evaluate("() => !!(window.__viewer.hw && window.__viewer.hw.supported)")
    if not sup:
        raise RuntimeError(f"/{pid}/: hw.mjs does not see the fake navigator.serial (api.hw.supported is false)")
    return page


def watch_open(pid, seconds, step=0.05):
    """Max number of /hw/<pid>/cmd requests held at once during `seconds`."""
    mx, end = 0, time.monotonic() + seconds
    while time.monotonic() < end:
        with _lock:
            mx = max(mx, open_cmd.get(pid, 0))
        time.sleep(step)
    return mx


def wait_until(fn, timeout, step=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


def n_open(pid):
    with _lock:
        return open_cmd.get(pid, 0)


def n_started(pid):
    with _lock:
        return started_cmd.get(pid, 0)


def queue_identify(pid, timeout=8):
    """Queue `identify --port 10c4:ea60` through the real router code; retried until the tab's ports event has arrived."""
    out = {}

    def go():
        code, o = hw.queue(pid, "identify", None, time.time(), port=PORT_KEY)
        out.update(code=code, body=o)
        return code == 200
    wait_until(go, timeout, 0.2)
    return out


def result_of(pid, cid):
    with hw._cv:
        c = hw._p(pid)["cmds"].get(cid)
        return c and c["result"]


with sync_playwright() as pw:
    b = pw.chromium.launch(headless=not a.headed)

    # ---- [0] the harness reproduces Chrome's 6-connections-per-host limit (if not, [3] cannot fail and proves nothing)
    ctx = new_ctx(b)
    pg = ctx.new_page()
    pg.goto(f"{BASE}/p0/?ports=0", wait_until="load")
    took = pg.evaluate("""async () => {
      const held = Array.from({ length: 6 }, (_, i) => fetch('/hold?s=3&i=' + i).catch(() => null));
      await new Promise((r) => setTimeout(r, 300));
      const t = performance.now(); await fetch('/ping?x=' + Math.random()); const dt = performance.now() - t;
      await Promise.all(held); return dt;
    }""")
    check("[0] harness: 6 held requests starve a 7th one in this browser (HTTP/1.1 per-host limit)", took >= 2000, f"7th request took {took:.0f} ms")
    ctx.close()

    # ---- [1] no board -> no /cmd request open
    hw.POLL_S = a.short_hold
    ctx = new_ctx(b)
    pg = open_tab(ctx, "p1", ports=0)
    mx = watch_open("p1", a.short_hold + 1)
    check("[1] tab with flashing and NO granted port holds no /hw/p1/cmd request",
          mx == 0 and n_started("p1") == 0, f"max open at once {mx}, started {n_started('p1')} in {a.short_hold + 1:.0f} s; events posted: {events.get('p1', [])}")
    ctx.close()

    # ---- [2a] a port appears -> long-poll, a queued command reaches the tab, its result is posted; last port gone -> polling stops
    ctx = new_ctx(b)
    pg = open_tab(ctx, "p2", ports=0)
    time.sleep(1)
    before = n_started("p2")
    pg.evaluate("() => window.__serialTest.plug()")
    opened = wait_until(lambda: n_open("p2") >= 1, 6)
    check("[2] a granted port appears -> the tab long-polls /hw/p2/cmd", opened, f"open now {n_open('p2')}, started {n_started('p2') - before} since plug")
    q = queue_identify("p2")
    check("[2] the router accepts a command for that port (the tab reported it)", q.get("code") == 200, json.dumps(q, ensure_ascii=False))
    if q.get("code") == 200:
        cid = q["body"]["id"]
        res = wait_until(lambda: result_of("p2", cid), max(8, a.short_hold + 4))
        ran = pg.evaluate("() => window.__identified")
        check("[2] the command reaches the tab and its result is posted back to the router",
              bool(res) and res.get("ok") is True and ran >= 1, f"result {res}, identify() ran {ran}x in the tab")
    pg.evaluate("() => window.__serialTest.unplug()")
    time.sleep(a.short_hold + 2)   # a poll already held when the port went runs out; no new one may start
    s0, mx = n_started("p2"), watch_open("p2", 3)
    check("[2] the last port goes away -> the tab stops long-polling /hw/p2/cmd",
          mx == 0 and n_started("p2") == s0, f"max open at once {mx} and {n_started('p2') - s0} new /cmd requests in the 3 s after the poll window")
    ctx.close()

    # ---- [2b] a tab loaded with a port already granted gets commands as before
    ctx = new_ctx(b)
    pg = open_tab(ctx, "p2b", ports=1)
    opened = wait_until(lambda: n_open("p2b") >= 1, 6)
    check("[2] tab loaded with a granted port long-polls /hw/p2b/cmd", opened, f"open now {n_open('p2b')}")
    q = queue_identify("p2b")
    ok = q.get("code") == 200
    res = ok and wait_until(lambda: result_of("p2b", q["body"]["id"]), max(8, a.short_hold + 4))
    check("[2] a command queued for its port reaches it and the result is posted back",
          bool(res) and res.get("ok") is True, f"queue {json.dumps(q, ensure_ascii=False)}, result {res}")
    ctx.close()

    # ---- [3] 7 board-less flashing tabs in one browser do not starve an 8th tab's page load and kit download
    # Deterministic: the 7 tabs are opened while /cmd holds only 1 s (so the setup itself is not starved), then every /cmd request
    # started from now on is held --hold seconds; after 2 s all polls that exist are long ones, none returns during the measurement.
    hw.POLL_S = 1
    ctx = new_ctx(b)
    t_setup = time.monotonic()
    tabs = [open_tab(ctx, f"p3{c}", ports=0, timeout=180_000) for c in "abcdefg"]
    hw.POLL_S = a.hold
    time.sleep(2)   # let every tab settle into whatever it does when idle
    held = sum(n_open(f"p3{c}") for c in "abcdefg")
    print(f"  [3] 7 tabs open after {time.monotonic() - t_setup:.1f} s; /cmd requests held by them now: {held}", flush=True)
    t0 = time.monotonic()
    p8 = ctx.new_page()
    p8.goto(f"{BASE}/p3h/?ports=0", wait_until="load", timeout=180_000)
    t_page = time.monotonic() - t0
    t_kit = p8.evaluate("""async () => { const t = performance.now(); const r = await fetch('/kit/p3h/full');
                                         const n = (await r.arrayBuffer()).byteLength; return [performance.now() - t, r.status, n]; }""")
    total = time.monotonic() - t0
    slow = p8.evaluate("""() => performance.getEntriesByType('resource').concat(performance.getEntriesByType('navigation'))
                              .map((e) => [e.name.replace(location.origin, ''), Math.round(e.duration)]).sort((x, y) => y[1] - x[1]).slice(0, 3)""")
    check(f"[3] with 7 board-less flashing tabs open, an 8th tab loads its page + a kit download within {a.budget:g} s",
          total <= a.budget and t_kit[1] == 200,
          f"page {t_page:.1f} s, kit {t_kit[0] / 1000:.1f} s (HTTP {t_kit[1]}, {t_kit[2]} B), total {total:.1f} s; slowest: {slow}; /cmd hold {a.hold:g} s")
    ctx.close()
    b.close()

srv.shutdown()
print()
for n, s, d in rows:
    print(f"{s:4}  {n}")
sys.exit(0 if ok_all else 1)
