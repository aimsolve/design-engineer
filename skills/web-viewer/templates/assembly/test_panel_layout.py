#!/usr/bin/env python3
"""Real-browser check of the left panel layout and the real-board (Web Serial) windows of an assembly site built with
`build.py --sim <dir> --flash` (a machine with several sim boards, one of them an ESP32, and an operator app #appPanel).

With --url: an already served site. Without it: the test builds the site with THIS build.py from a machine's importer output (--in, else
$VIEWER_TEST_MODEL) and its sim dir (--sim, else $VIEWER_TEST_SIM) and serves it like the stack (viewer_stack_server.py, /<id>/). The demo
machine has no sim, so with neither given the test skips saying so.
  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_panel_layout.py [--url http://localhost:8765/ | --in <importer output> --sim <sim dir>] \
      [--shots <dir>] [--swiftshader] [--plain-url <site built without --sim/--flash>] [--flash-seconds 10]
(first run: `... --with playwright playwright install chromium`)

No real hardware: a fake `navigator.serial` and `window.__hwSessionFor(port)` are installed before page load (init script).
  The boards come from the machine (`__viewer.sim.meta.boards`, read on a first page load): MAIN = the first ESP32 board (else the first
  board), its port 10c4:ea60 (usb-uart); every other board an Arduino port 2341:<pid> (localStorage "hw.assign" pre-seeded).
  fw/index.json is intercepted to add the other boards as published boards; their manifests and firmware files are faked.
  /kit/** and /hw/** answer 404 (no router), except the one page that checks the downloads section with a router that answers.
UI the test relies on (the spec): #panel with foldable sections headed (i18n.mjs) sec.view, sim.machine, sec.hw, sec.dl, sec.parts;
board windows .hwWin (header .hwH) outside #panel; floating windows #appPanel (#appHead), #bcard (#bHead). The page is in its default
language (ru); every UI text the checks expect comes from i18n.mjs.
Check names start with the done-when number they serve: [1] panel sections + tree fill, [2] nothing removed, [3] board windows,
[4] simulation rows, [5] floating windows drag/fold/remember, [6] narrow screen, [7] console clean.
LOOK at every PNG. Exit code 1 on any failed check.
"""
import argparse, json, re, shutil, sys, tempfile, urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import build, model_dir, serve_stack, sim_dir, skip, ui_texts  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--url", default=None, help="site root (built with --sim and --flash); default: build one from --in and --sim and serve it")
ap.add_argument("--in", dest="src", default=None, help="importer output dir of the machine (default: $VIEWER_TEST_MODEL)")
ap.add_argument("--sim", default=None, help="the machine's sim dir with site.json (default: $VIEWER_TEST_SIM)")
ap.add_argument("--plain-url", default=None, help="optional: a site built WITHOUT --sim/--flash, to check those sections are absent")
ap.add_argument("--shots", default=None, help="keep screenshots here (default: a temp dir; screenshots never go in the project)")
ap.add_argument("--flash-seconds", type=float, default=10, help="how long the fake flashBoard() takes")
ap.add_argument("--swiftshader", action="store_true")
a = ap.parse_args()
T = ui_texts("ru")
srv = tmp = None
if not a.url:
    sim = sim_dir(a.sim)
    src = model_dir(a.src, needs="the importer output of the machine whose sim the test runs")
    tmp = Path(tempfile.mkdtemp(prefix="panel_site_"))
    r = build(src, tmp / "demo-machine", "--sim", sim, "--flash")
    if r.returncode:
        sys.exit("build.py --sim --flash failed:\n" + (r.stderr or r.stdout)[-3000:])
    srv, base = serve_stack(tmp)
    a.url = base + "/demo-machine/"
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="panel_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
BASE = a.url.rstrip("/")
_seg = [s for s in urllib.parse.urlparse(a.url).path.split("/") if s]
PROJECT = _seg[0] if _seg else "local"
rows, ok_all = [], True

SECTIONS = [T["sec"]["view"], T["sim"]["machine"], T["sec"]["hw"], T["sec"]["dl"], T["sec"]["parts"]]
VIEW, SIMS, HWS, DLS, PARTS = SECTIONS
CONNECT, FLASH, FLASH_TO, LOG, MON, ON = T["hw"]["connect"], T["hw"]["flash"], T["hw2"]["flashTo"], T["hw2"]["log"], T["hw2"]["mon"], T["hw2"]["on"]
BOT = T["hw"]["bot"].rstrip("…. ")   # "the bot is flashing…" without the ellipsis
# the machine's boards (set by learn_boards() from a first page load): MAIN = the ESP32 board, OTHERS = the rest
BOARDS, MAIN, OTHERS = {}, None, []
PORTS = {}       # card -> fake USB port
HW_ASSIGN = {}   # remembered port -> card choices (the Arduino ports cannot be told apart over USB)
ARDUINO_PIDS = [0x0043, 0x0058, 0x0042, 0x0010, 0x003D, 0x0036]
AVR_BOARD = lambda card: {"card": card, "chip": "atmega328p", "usb": ["arduino"], "manifest": f"{card}/manifest.json", "version": "a41c09d 20261019-1712"}
AVR_MANIFEST = {"kind": "avr", "chip": "atmega328p", "monitor_speed": 115200, "version": "a41c09d 20261019-1712", "files": [{"offset": "0x0", "url": "firmware.hex"}]}
ESP_MANIFEST = {"kind": "esptool", "chip": "esp32", "monitor_speed": 115200, "version": "b7efe4e 20261020-2003", "files": [{"offset": "0x10000", "url": "firmware.bin"}]}


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- fake Web Serial (installed before any page script runs)
INIT_JS = r"""
(() => {
  const cfg = __CFG__;
  try { localStorage.setItem('hw.assign', JSON.stringify(cfg.assign)); } catch (e) {}
  const listeners = {};
  const ports = cfg.ports.map((p, i) => ({
    __fake: p.card, __i: i,
    getInfo: () => ({ usbVendorId: p.vid, usbProductId: p.pid }),
    open: async () => {}, close: async () => {}, forget: async () => {},
    readable: null, writable: null, connected: true,
    addEventListener() {}, removeEventListener() {},
  }));
  const serial = {
    getPorts: async () => ports.slice(),
    requestPort: async () => { if (!ports.length) throw new DOMException('No port selected by the user.', 'NotFoundError'); return ports[0]; },
    addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener: (t, fn) => { listeners[t] = (listeners[t] || []).filter(f => f !== fn); },
    onconnect: null, ondisconnect: null,
  };
  try { Object.defineProperty(Navigator.prototype, 'serial', { get: () => serial, configurable: true }); }
  catch (e) { Object.defineProperty(navigator, 'serial', { value: serial, configurable: true }); }
  window.__fakeSessions = [];
  window.__hwSessionFor = (port) => {
    const p = cfg.ports[port && port.__i] || cfg.ports[0];
    const subs = new Set();
    const emit = (line) => subs.forEach(fn => { try { fn(line); } catch (e) {} });
    let timer = null;
    const s = {
      card: p.card, monitoring: false, monitorSpeed: 115200, lastKind: null, flashes: 0,
      subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
      async flashBoard(opts) {
        opts = opts || {}; s.lastKind = 'flash'; s.flashes++;
        const steps = 20, dt = cfg.flashMs / steps;
        for (let k = 0; k <= steps; k++) {
          if (opts.onProgress) try { opts.onProgress(Math.round(k * 100 / steps)); } catch (e) {}
          emit(`flash ${Math.round(k * 100 / steps)}%`);
          if (k < steps) await new Promise(r => setTimeout(r, dt));
        }
        return { chip: p.chip };
      },
      async openMonitor() {
        s.monitoring = true; s.lastKind = 'monitor'; let n = 0;
        ['ets Jun  8 2016 00:22:57', 'boot: ok', `${p.card} ready`].forEach(emit);
        clearInterval(timer); timer = setInterval(() => emit(`${p.card} tick ${++n}`), 500);
      },
      async closeMonitor() { s.monitoring = false; clearInterval(timer); timer = null; },
      async resetBoard() { s.lastKind = 'reset'; emit('reset'); },
      async identify() { return { chip: p.chip, description: p.desc }; },
    };
    window.__fakeSessions.push(s);
    return s;
  };
})();
"""


def init_script(cards):
    cfg = {"ports": [{"card": c, **PORTS[c]} for c in cards], "assign": HW_ASSIGN, "flashMs": int(a.flash_seconds * 1000)}
    return INIT_JS.replace("__CFG__", json.dumps(cfg))


def install_routes(ctx, extra_boards, kit_enabled=False, kit_calls=None):
    def fw_index(route):
        try:
            r = route.fetch(); body = r.json()
        except Exception:  # noqa  (not served / not JSON: let the page see what is there)
            return route.continue_()
        if isinstance(body, dict) and isinstance(body.get("boards"), list):
            have = {b.get("card") for b in body["boards"]}
            body["boards"] += [AVR_BOARD(c) for c in extra_boards if c not in have]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))

    def manifest(route):
        card = urllib.parse.urlparse(route.request.url).path.rstrip("/").split("/")[-2]
        if card == MAIN["id"]:
            try:
                r = route.fetch()
                if r.ok:
                    return route.fulfill(response=r)
            except Exception:  # noqa
                pass
        route.fulfill(status=200, content_type="application/json", body=json.dumps(ESP_MANIFEST if card == MAIN["id"] else AVR_MANIFEST))

    def kit(route):
        if kit_calls is not None:
            kit_calls.append(route.request.url)
        if kit_enabled and route.request.url.split("?")[0].endswith("/status"):
            body = {"enabled": True, "fresh": True, "building": False, "since": None, "parts": [], "nodes": [], "scopes": ["print", "laser", "bought", "step"]}
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
        route.fulfill(status=404, content_type="application/json", body='{"error": "no router"}')

    ctx.route(re.compile(r".*/fw/index\.json(\?.*)?$"), fw_index)
    ctx.route(re.compile(r".*/fw/[^/]+/manifest\.json(\?.*)?$"), manifest)
    ctx.route(re.compile(r".*/fw/[^/]+/firmware\.(bin|hex)(\?.*)?$"), lambda r: r.fulfill(status=200, body=b"\x00" * 64, content_type="application/octet-stream"))
    ctx.route(re.compile(r"^https?://[^/]+/kit/.*"), kit)
    ctx.route(re.compile(r"^https?://[^/]+/hw/.*"), lambda r: r.fulfill(status=404, content_type="application/json", body='{"error": "no router"}'))


def benign(msg_text, url):
    """404s we cause on purpose (no router) and the app iframe's own logs (out of scope) are not the page's errors."""
    return bool(re.search(r"/(kit|hw)/", url or "")) or "/app/" in (url or "")


def learn_boards(p):
    """Read the machine's boards from the page (META) and give each one a fake port."""
    global MAIN, OTHERS
    b = p.chromium.launch(args=GPU)
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    install_routes(ctx, ())
    page = ctx.new_page()
    page.goto(BASE + "/?app=0", wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    page.wait_for_function("window.__viewer.sim || window.__viewer.simError || !(window.__viewer.config && window.__viewer.config.sim)", timeout=60000)
    boards = page.evaluate("window.__viewer.sim ? JSON.parse(JSON.stringify(window.__viewer.sim.meta.boards || [])) : []")
    b.close()
    if len(boards) < 3:
        skip(f"needs a machine with at least 3 sim boards (one of them shown as an ESP32 board), has {len(boards)}: {[x.get('id') for x in boards]}")
    BOARDS.update((x["id"], x) for x in boards)
    MAIN = next((x for x in boards if x.get("mcu") == "esp32"), boards[0])
    OTHERS = [x for x in boards if x is not MAIN]
    PORTS[MAIN["id"]] = {"vid": 0x10C4, "pid": 0xEA60, "chip": "ESP32", "desc": "ESP32-D0WD (CP2102)"}
    for x, pid in zip(OTHERS, ARDUINO_PIDS):
        PORTS[x["id"]] = {"vid": 0x2341, "pid": pid, "chip": "ATmega328P", "desc": "Arduino"}
        HW_ASSIGN[f"2341:{pid:04x}"] = x["id"]
    print(f"boards: main {MAIN['id']} ({MAIN.get('name')}), others {[x['id'] for x in OTHERS]}", flush=True)


def open_site(p, cards=(), viewport=None, mobile=False, app=False, extra_boards=None, kit_enabled=False, url=None, path="/"):
    if extra_boards is None:
        extra_boards = [x["id"] for x in OTHERS]
    b = p.chromium.launch(args=GPU)
    ctx = b.new_context(viewport=viewport or {"width": 1440, "height": 900}, device_scale_factor=1, is_mobile=mobile, has_touch=mobile)
    ctx.add_init_script(init_script(list(cards)))
    kit_calls = []
    install_routes(ctx, extra_boards, kit_enabled, kit_calls)
    ctx.set_default_timeout(8000); ctx.set_default_navigation_timeout(120000)   # a missing control fails its check fast instead of hanging 30 s
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" and not benign(m.text, (m.location or {}).get("url")) else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.__dict__["_kit_calls"] = kit_calls
    page.__dict__["_url"] = (url or (BASE + path.rstrip("/"))) + "/?app=" + ("1" if app else "0")
    load(page)
    return b, page, errs


def load(page, reload=False):
    if reload:
        page.reload(wait_until="load")
    else:
        page.goto(page.__dict__["_url"], wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    if page.evaluate("!!(window.__viewer.config && window.__viewer.config.sim)"):   # a plain site (or a broken --sim build) has no sim layer
        try:
            page.wait_for_function("window.__viewer.sim || window.__viewer.simError", timeout=60000)
        except Exception:  # noqa
            pass
    page.wait_for_timeout(800)


# ---------------------------------------------------------------- panel sections: find a heading by its label, its body by its known content
FIND_HEAD_JS = r"""([label, mark]) => {
  const panel = document.getElementById('panel'); if (!panel) return null;
  const re = new RegExp('^\\s*' + label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?!\\p{L})', 'u');
  const own = el => Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
  const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  for (const el of panel.querySelectorAll('*')) {
    if (!vis(el)) continue;
    if (re.test(own(el)) || (el.children.length === 0 && re.test(el.textContent))) {
      document.querySelectorAll('[data-test-head="' + mark + '"]').forEach(x => x.removeAttribute('data-test-head'));
      const h = el.closest('button, summary, [role=button], h1, h2, h3, h4, h5, h6, [class*=head], [class*=Head]') || el;
      h.setAttribute('data-test-head', mark); return true;
    }
  }
  return null; }"""

BODY = {   # label -> selector of something that lives in that section's body
    VIEW: "#bColMat",   # the reset-view button sits in the toolbar above the sections (approved layout)
    SIMS: "#vmBoards",
    HWS: f"#panel :is(button, [role=button], a):has-text({json.dumps(CONNECT, ensure_ascii=False)})",
    DLS: f"#panel :is(button, [role=button], a):has-text({json.dumps(T['kit']['scopes']['print'], ensure_ascii=False)})",
    PARTS: "#tree",
}


def head(page, label):
    mark = "h" + str(SECTIONS.index(label) if label in SECTIONS else 9)
    return page.locator(f'[data-test-head="{mark}"]') if page.evaluate(FIND_HEAD_JS, [label, mark]) else None


def body_visible(page, label):
    loc = page.locator(BODY[label]).first
    return loc.count() > 0 and loc.is_visible()


def set_section(page, label, open_):
    h = head(page, label)
    if not h:
        return False
    if body_visible(page, label) != open_:
        h.click(); page.wait_for_timeout(350)
    return body_visible(page, label) == open_


def storage(page):
    return page.evaluate("(() => { const o = {}; for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); o[k] = localStorage.getItem(k); } return o; })()")


def rect(page, sel_or_handle):
    if isinstance(sel_or_handle, str):
        return page.evaluate("s => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height, r: r.right, b: r.bottom}; }", sel_or_handle)
    return sel_or_handle.evaluate("e => { const r = e.getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height, r: r.right, b: r.bottom}; }")


def overlap(r1, r2, tol=1):
    return min(r1["r"], r2["r"]) - max(r1["x"], r2["x"]) > tol and min(r1["b"], r2["b"]) - max(r1["y"], r2["y"]) > tol


def hw_windows(page, n, timeout=15000):
    try:
        page.wait_for_function(f"document.querySelectorAll('.hwWin').length >= {n}", timeout=timeout)
    except Exception:  # noqa
        pass
    if page.locator(".hwWin").count() < n:   # not auto-connected on load: press the connect button (requestPort -> the first fake port)
        set_section(page, HWS, True)
        btn = page.locator(BODY[HWS]).first
        if btn.count() and btn.is_visible():
            btn.click()
            try:
                page.wait_for_function(f"document.querySelectorAll('.hwWin').length >= {n}", timeout=5000)
            except Exception:  # noqa
                pass
    return page.locator(".hwWin")


def win_for(page, name_part):
    w = page.locator(".hwWin", has_text=name_part)
    return w.first if w.count() else None


def click_named(scope, text):
    """A control named exactly `text` inside scope (button first, then any element with that text)."""
    btn = scope.get_by_role("button", name=text, exact=True)
    if btn.count():
        btn.first.click(); return True
    t = scope.get_by_text(text, exact=True)
    if t.count():
        t.first.click(); return True
    return False


# ---------------------------------------------------------------- [1] [2] [3] [4] [7]: desktop, one board (MAIN) on the desk
def run_main(p):
    b, page, errs = open_site(p, cards=[MAIN["id"]])
    v = "window.__viewer"
    check("[0] model + sim layer load (site built with --sim)", page.evaluate(f"!{v}.error && !{v}.simError && !!{v}.sim"),
          json.dumps(page.evaluate(f"({{error: String({v}.error || ''), simError: String({v}.simError || ''), sim: !!{v}.sim, meta: !!(window.__viewer.config && window.__viewer.config.sim)}})")))
    page.screenshot(path=str(shots / "main_1_default.png"))

    # [1] sections
    present = {s: bool(head(page, s)) for s in SECTIONS}
    check(f"[1] #panel has section headings «{VIEW}», «{SIMS}», «{HWS}», «{PARTS}»", all(present[s] for s in (VIEW, SIMS, HWS, PARTS)), json.dumps(present, ensure_ascii=False))
    called = page.__dict__["_kit_calls"]
    check(f"[1] «{DLS}» absent while the download router does not answer", not present[DLS], f"kit requests: {len(called)}")
    for s in (VIEW, SIMS, HWS, PARTS):
        if not present[s]:
            check(f"[1] heading «{s}» folds and unfolds its section", False, "no heading"); continue
        set_section(page, s, True)
        was = body_visible(page, s)
        head(page, s).click(); page.wait_for_timeout(350); folded = not body_visible(page, s)
        head(page, s).click(); page.wait_for_timeout(350); back = body_visible(page, s)
        check(f"[1] heading «{s}» folds and unfolds its section", was and folded and back, f"open={was} after click folded={folded} after 2nd click open={back}")

    # [1] fold state survives a reload, stored per project
    before = storage(page)
    for s in (VIEW, SIMS, HWS):
        set_section(page, s, False)
    set_section(page, PARTS, True)
    after = storage(page)
    changed = [k for k in after if after.get(k) != before.get(k) and PROJECT in k]
    check(f"[1] fold state saved in localStorage under a key containing «{PROJECT}»", bool(changed), f"changed keys: {[k for k in after if after.get(k) != before.get(k)]}")
    load(page, reload=True)
    st = {s: body_visible(page, s) for s in (VIEW, SIMS, HWS, PARTS)}
    check(f"[1] fold state survives a reload ({VIEW} / {SIMS} / {HWS} folded, {PARTS} open)", st == {VIEW: False, SIMS: False, HWS: False, PARTS: True}, json.dumps(st, ensure_ascii=False))

    # [1] all other sections folded -> the tree fills the panel, the panel does not scroll, the tree does
    page.screenshot(path=str(shots / "main_2_only_parts.png"))
    g = page.evaluate("""() => { const p = document.getElementById('panel'), t = document.getElementById('tree'); if (!p || !t) return null;
      const pr = p.getBoundingClientRect(), tr = t.getBoundingClientRect(), bad = [];
      for (let e = t.parentElement; e; e = e.parentElement) { if (e.scrollHeight > e.clientHeight + 1 && /(auto|scroll)/.test(getComputedStyle(e).overflowY)) bad.push((e.id || e.className || e.tagName) + ' ' + e.scrollHeight + '>' + e.clientHeight); if (e === p) break; }
      return { panelBottom: Math.min(pr.bottom, innerHeight), treeBottom: tr.bottom, treeH: tr.height, panelH: pr.height,
               pScroll: p.scrollHeight, pClient: p.clientHeight, bad, tOverflow: getComputedStyle(t).overflowY, tScroll: t.scrollHeight, tClient: t.clientHeight }; }""")
    check("[1] with other sections folded, #tree reaches the panel bottom (<= 90 px: the mouse hint lines sit under it)", bool(g) and g["panelBottom"] - g["treeBottom"] <= 90 and g["treeBottom"] <= g["panelBottom"] + 1, json.dumps(g))
    check("[1] the panel body itself does not scroll", bool(g) and g["pScroll"] <= g["pClient"] + 1 and not g["bad"], json.dumps(g and {"panel": [g["pScroll"], g["pClient"]], "scrolling ancestors": g["bad"]}))
    check("[1] #tree scrolls internally", bool(g) and g["tOverflow"] in ("auto", "scroll") and (g["tScroll"] <= g["tClient"] + 1 or page.evaluate("(() => { const t = document.getElementById('tree'); t.scrollTop = 40; const s = t.scrollTop; t.scrollTop = 0; return s > 0; })()")), json.dumps(g and {"overflowY": g["tOverflow"], "scroll": [g["tScroll"], g["tClient"]]}))

    # [2] nothing removed: every control reachable once its section is open
    for s in (VIEW, SIMS, HWS, PARTS):
        set_section(page, s, True)
    page.wait_for_timeout(300)
    vis = {sel: page.locator(sel).count() > 0 and page.locator(sel).first.is_visible() for sel in ("#bReset", "#bColMat", "#bColKind", "#bEdges", "#bAO", "#bHC", "#q", "#tree")}
    check("[2] #bReset #bColMat #bColKind #bEdges #bAO #bHC #q #tree visible with sections open", all(vis.values()), json.dumps(vis))
    check(f"[2] #bReset says «{T['reset']}»", page.locator("#bReset").count() > 0 and T["reset"] in page.inner_text("#bReset"))
    conn = page.locator(BODY[HWS])
    in_sec = conn.count() > 0 and conn.first.is_visible()
    if in_sec:
        set_section(page, HWS, False); hid = not conn.first.is_visible(); set_section(page, HWS, True)
    check(f"[2] «{CONNECT}» is inside the «{HWS}» section", in_sec and hid, f"visible open={in_sec}")
    on = lambda: [s for s in ("#bColMat", "#bColKind") if page.locator(s).count() and "on" in (page.get_attribute(s, "class") or "").split()]
    o0 = on()
    other = "#bColKind" if o0 == ["#bColMat"] else "#bColMat"
    if page.locator(other).count():
        page.click(other); page.wait_for_timeout(300)
    o1 = on()
    check("[2] colour mode: exactly one of #bColMat/#bColKind is «on», clicking the other moves it", len(o0) == 1 and o1 == [other], f"before {o0}, after clicking {other}: {o1}")
    for sel in ("#bEdges", "#bAO", "#bHC"):
        if not page.locator(sel).count():
            check(f"[2] {sel} toggles class «on»", False, "missing"); continue
        c0 = "on" in (page.get_attribute(sel, "class") or "").split()
        page.click(sel); page.wait_for_timeout(250); c1 = "on" in (page.get_attribute(sel, "class") or "").split()
        page.click(sel); page.wait_for_timeout(250); c2 = "on" in (page.get_attribute(sel, "class") or "").split()
        check(f"[2] {sel} toggles class «on»", c1 != c0 and c2 == c0, f"{c0} -> {c1} -> {c2}")

    # [3] the board window
    wins = hw_windows(page, 1)
    check("[3] one connected port -> one .hwWin", wins.count() == 1, f"{wins.count()} windows")
    w = wins.first if wins.count() else None
    if w:
        page.screenshot(path=str(shots / "main_3_board_window.png"))
        check("[3] .hwWin is not inside #panel", not w.evaluate("e => !!e.closest('#panel')"))
        txt = w.inner_text()
        check(f"[3] window shows the sim board name «{MAIN['name']}»", MAIN["name"] in txt, txt[:120].replace("\n", " / "))
        chip = w.evaluate("""e => Array.from(e.querySelectorAll('*')).some(x => x.children.length === 0 && /^\\s*ESP32\\s*$/i.test(x.textContent) && x.getBoundingClientRect().width > 0)""")
        check("[3] window has a chip label «ESP32»", chip)
        status = w.evaluate("""(e, on) => Array.from(e.querySelectorAll('*')).some(x => Array.from(x.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim().startsWith(on) && x.getBoundingClientRect().width > 0)""", ON)
        check(f"[3] window status text starts with «{ON}»", status)
        prim = w.locator(".primary")
        ptxt = [prim.nth(i).inner_text().strip() for i in range(prim.count())]
        check(f"[3] exactly one primary button and it is «{FLASH}»", ptxt == [FLASH], str(ptxt))
        pre = w.locator("pre")
        check("[3] serial log (pre) hidden by default", pre.count() > 0 and not pre.first.is_visible(), f"{pre.count()} pre")
        clicked = click_named(w, LOG); page.wait_for_timeout(400)
        mon = w.get_by_role("checkbox", name=MON).or_(w.get_by_role("switch", name=MON)).or_(w.get_by_text(MON))
        shown = pre.count() > 0 and pre.first.is_visible()
        check(f"[3] «{LOG}» shows the log together with a «{MON}» switch", clicked and shown and mon.count() > 0 and mon.first.is_visible(), f"button={clicked} pre visible={shown}")
        page.screenshot(path=str(shots / "main_4_board_log.png"))
        click_named(w, LOG); page.wait_for_timeout(400)
        check(f"[3] «{LOG}» again hides the log", pre.count() > 0 and not pre.first.is_visible())
        started = page.evaluate("""(card) => { try { const r = window.__viewer.hw.flash(card, 'bot'); window.__botFlash = Promise.resolve(r).then(x => (window.__botDone = x || true), e => (window.__botErr = String(e))); return true; } catch (e) { return String(e); } }""", MAIN["id"])
        try:
            page.wait_for_function("(t) => { const w = document.querySelector('.hwWin'); return w && w.innerText.includes(t); }", arg=BOT, timeout=4000); bot = True
        except Exception:  # noqa
            bot = False
        dis = prim.count() == 1 and prim.first.is_disabled()
        page.screenshot(path=str(shots / "main_5_bot_flashing.png"))
        check(f"[3] bot flash: window shows «{T['hw']['bot']}» and «{FLASH}» is disabled", started is True and bot and dis, f"flash() -> {started}; text seen={bot}; disabled={dis}")
        try:
            page.wait_for_function("window.__botDone || window.__botErr", timeout=int(a.flash_seconds * 1000) + 15000)
        except Exception:  # noqa
            pass

    # [4] simulation rows
    set_section(page, SIMS, True)
    rowsel = "#vmBoards .brd"
    btn_vis = page.evaluate("""(t) => { const o = {}; document.querySelectorAll('#vmBoards .brd').forEach(r => {
        const b = Array.from(r.querySelectorAll('button, [role=button], a')).find(x => x.textContent.includes(t));
        const rr = b && b.getBoundingClientRect();
        o[r.dataset.b || r.textContent.trim().slice(0, 20)] = !!(b && rr.width > 0 && rr.height > 0 && getComputedStyle(b).visibility !== 'hidden'); }); return o; }""", FLASH_TO)
    check(f"[4] connected board row ({MAIN['id']}) shows «{FLASH_TO}»", btn_vis.get(MAIN["id"]) is True, json.dumps(btn_vis, ensure_ascii=False))
    check(f"[4] rows of boards not on the desk ({', '.join(x['id'] for x in OTHERS)}) hide «{FLASH_TO}»", all(btn_vis.get(x["id"]) is False for x in OTHERS), json.dumps(btn_vis, ensure_ascii=False))
    check("[4] simulation rows have no status dot (.dot)", page.locator(rowsel).count() == len(BOARDS) and page.locator(f"{rowsel} .dot").count() == 0, f"{page.locator(rowsel).count()} rows, {page.locator(f'{rowsel} .dot').count()} dots")
    page.screenshot(path=str(shots / "main_6_sim_rows.png"))
    check("[7] desktop, one board: console has no errors", not errs, "; ".join(errs[:3]))
    b.close()


# ---------------------------------------------------------------- [1] per-project key: the same site served under /demo-proj/
def run_project_key(p):
    b = p.chromium.launch(args=GPU)
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    ctx.add_init_script(init_script([]))
    ctx.set_default_timeout(8000); ctx.set_default_navigation_timeout(120000)
    install_routes(ctx, ())
    root = urllib.parse.urlparse(a.url); origin = f"{root.scheme}://{root.netloc}"
    pref = "/demo-proj"

    def rewrite(route):
        u = urllib.parse.urlparse(route.request.url)
        target = origin + (root.path.rstrip("/") + u.path[len(pref):] if u.path.startswith(pref) else u.path) + (("?" + u.query) if u.query else "")
        try:
            route.fulfill(response=route.fetch(url=target))
        except Exception:  # noqa
            route.abort()
    ctx.route(re.compile(re.escape(origin) + re.escape(pref) + r"(/.*)?$"), rewrite)
    page = ctx.new_page()
    page.__dict__["_url"] = origin + pref; page.__dict__["_kit_calls"] = []
    try:
        load(page)
    except Exception as e:  # noqa
        check("[1] fold state key contains the project segment («demo-proj»)", False, f"page did not load under {pref}/: {e}"); b.close(); return
    before = storage(page)
    set_section(page, VIEW, False)
    after = storage(page)
    changed = [k for k in after if after.get(k) != before.get(k)]
    check("[1] fold state key contains the project segment («demo-proj»)", any("demo-proj" in k for k in changed), f"changed keys: {changed}")
    page.goto(BASE + "/?app=0", wait_until="load"); page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000); page.wait_for_timeout(800)
    check(f"[1] another project («{PROJECT}») keeps its own fold state ({VIEW} still open)", body_visible(page, VIEW))
    b.close()


# ---------------------------------------------------------------- [1] the downloads section when the router answers
def run_kit(p):
    b, page, errs = open_site(p, cards=[], kit_enabled=True)
    calls = page.__dict__["_kit_calls"]
    if not calls:
        check(f"[1] «{DLS}» section when the download router answers", True, f"site makes no /kit request at path «{PROJECT}» (absent locally is allowed)")
    else:
        h = head(page, DLS)
        ok = bool(h)
        if ok:
            set_section(page, DLS, True); op = body_visible(page, DLS)
            h.click(); page.wait_for_timeout(350); fo = not body_visible(page, DLS)
            ok = op and fo
        check(f"[1] «{DLS}» section present and foldable when the download router answers", ok, f"{len(calls)} kit requests")
    b.close()


# ---------------------------------------------------------------- [1] plain site: no simulation / boards sections
def run_plain(p):
    b, page, errs = open_site(p, cards=[], url=a.plain_url.rstrip("/"))
    check(f"[1] plain site (no --sim/--flash): no «{SIMS}» / «{HWS}» sections, «{VIEW}» and «{PARTS}» present",
          not head(page, SIMS) and not head(page, HWS) and bool(head(page, VIEW)) and bool(head(page, PARTS)))
    check("[7] plain site: console has no errors", not errs, "; ".join(errs[:3]))
    b.close()


# ---------------------------------------------------------------- [3] 2 and 3 boards: windows do not overlap each other or #panel
def run_multi(p, cards):
    n = len(cards)
    b, page, errs = open_site(p, cards=cards)
    wins = hw_windows(page, n)
    page.wait_for_timeout(800)
    page.screenshot(path=str(shots / f"multi_{n}_boards.png"))
    check(f"[3] {n} connected ports -> {n} .hwWin", wins.count() == n, f"{wins.count()} windows")
    rs = [rect(page, wins.nth(i).element_handle()) for i in range(wins.count())]
    pr = rect(page, "#panel")
    pairs = [(i, j) for i in range(len(rs)) for j in range(i + 1, len(rs)) if overlap(rs[i], rs[j])]
    over_panel = [i for i, r in enumerate(rs) if pr and overlap(r, pr)]
    check(f"[3] {n} boards: windows do not overlap each other", wins.count() == n and not pairs, f"overlapping pairs {pairs}; rects {[[round(r[k]) for k in ('x', 'y', 'w', 'h')] for r in rs]}")
    check(f"[3] {n} boards: windows do not overlap #panel", wins.count() == n and not over_panel, f"panel {pr and [round(pr[k]) for k in ('x', 'y', 'w', 'h')]}; over: {over_panel}")
    check(f"[7] desktop, {n} boards: console has no errors", not errs, "; ".join(errs[:3]))
    b.close()


# ---------------------------------------------------------------- [5] floating windows: drag by header, click folds, folded line up at the bottom, remembered
SAFE_POINT_JS = r"""(h) => { const r = h.getBoundingClientRect();
  for (const fy of [0.5, 0.3, 0.7]) for (let fx = 0.35; fx < 0.98; fx += 0.04) {
    const x = r.left + r.width * fx, y = r.top + r.height * fy, e = document.elementFromPoint(x, y);
    if (e && h.contains(e) && !e.closest('button, input, select, a, label, [role=button], [role=switch]')) return [x, y]; }
  for (let fx = 0.05; fx < 0.35; fx += 0.04) { const x = r.left + r.width * fx, y = r.top + r.height / 2, e = document.elementFromPoint(x, y);
    if (e && h.contains(e) && !e.closest('button, input, select, a, label, [role=button], [role=switch]')) return [x, y]; }
  return null; }"""


def run_windows(p):
    b, page, errs = open_site(p, cards=[MAIN["id"]], app=True)
    try:
        page.wait_for_selector("#appPanel", state="visible", timeout=30000)
    except Exception:  # noqa
        pass
    hw_windows(page, 1)
    set_section(page, SIMS, True)
    brd = page.locator(f'#vmBoards .brd[data-b="{MAIN["id"]}"]')
    if brd.count():
        bb = brd.first.bounding_box(); page.mouse.click(bb["x"] + 12, bb["y"] + bb["height"] / 2); page.wait_for_timeout(700)
    W = {"#appPanel": "#appHead", "#bcard": "#bHead", ".hwWin": ".hwWin .hwH"}
    vp = page.viewport_size
    page.screenshot(path=str(shots / "win_1_before_drag.png"))

    def geom(win, hd):
        if not page.locator(win).count() or not page.locator(win).first.is_visible() or not page.locator(hd).count():
            return None, None
        return rect(page, page.locator(win).first.element_handle()), rect(page, page.locator(hd).first.element_handle())

    moved = {}
    for win, hd in W.items():
        r0, h0 = geom(win, hd)
        if not r0:
            check(f"[5] {win} dragged by {hd} moves by (dx,dy)", False, "window or header not visible"); continue
        pt = page.locator(hd).first.evaluate(SAFE_POINT_JS)
        if not pt:
            check(f"[5] {win} dragged by {hd} moves by (dx,dy)", False, "no free spot on the header"); continue
        dx = -140 if r0["x"] > vp["width"] / 2 else 140
        dy = -90 if r0["y"] > vp["height"] / 2 else 90
        page.mouse.move(*pt); page.mouse.down()
        for k in range(1, 11):
            page.mouse.move(pt[0] + dx * k / 10, pt[1] + dy * k / 10); page.wait_for_timeout(16)
        page.mouse.up(); page.wait_for_timeout(400)
        r1, _ = geom(win, hd)
        ok = r1 and abs((r1["x"] - r0["x"]) - dx) <= 8 and abs((r1["y"] - r0["y"]) - dy) <= 8
        check(f"[5] {win} dragged by {hd} moves by (dx,dy)", ok, f"({dx},{dy}) -> moved ({r1 and round(r1['x'] - r0['x'])},{r1 and round(r1['y'] - r0['y'])})")
        check(f"[5] {win}: a drag does not fold it", bool(r1) and r1["h"] > r0["h"] * 0.6, f"height {round(r0['h'])} -> {r1 and round(r1['h'])}")
        if r1:
            moved[win] = r1
    page.screenshot(path=str(shots / "win_2_dragged.png"))
    st = storage(page)
    check(f"[5] window state stored under localStorage «win.{PROJECT}»", f"win.{PROJECT}" in st, f"keys: {[k for k in st if k.startswith('win')]}")

    # positions remembered after reload
    load(page, reload=True)
    try:
        page.wait_for_selector("#appPanel", state="visible", timeout=30000)
    except Exception:  # noqa
        pass
    hw_windows(page, 1)
    set_section(page, SIMS, True)
    if brd.count() and not page.locator("#bcard").is_visible():
        bb = brd.first.bounding_box(); page.mouse.click(bb["x"] + 12, bb["y"] + bb["height"] / 2); page.wait_for_timeout(700)
    for win, r1 in moved.items():
        r2, _ = geom(win, W[win])
        check(f"[5] {win} position remembered after reload", bool(r2) and abs(r2["x"] - r1["x"]) <= 6 and abs(r2["y"] - r1["y"]) <= 6,
              f"before ({round(r1['x'])},{round(r1['y'])}) after {r2 and (round(r2['x']), round(r2['y']))}")

    # click (no drag) on the header folds to a compact header; folded windows line up along the bottom edge
    folded = {}
    for win, hd in W.items():
        r0, h0 = geom(win, hd)
        if not r0:
            check(f"[5] {win}: click on {hd} folds it to its header", False, "not visible"); continue
        pt = page.locator(hd).first.evaluate(SAFE_POINT_JS)
        if pt:
            page.mouse.click(*pt); page.wait_for_timeout(500)
        r1, h1 = geom(win, hd)
        ok = bool(pt and r1 and h1) and r1["h"] <= h1["h"] + 40 and r1["h"] < r0["h"] - 20
        check(f"[5] {win}: click on {hd} folds it to its header", ok, f"height {round(r0['h'])} -> {r1 and round(r1['h'])} (header {h1 and round(h1['h'])})")
        if r1:
            folded[win] = r1
    page.wait_for_timeout(400)
    fr = {w: geom(w, W[w])[0] for w in folded}
    page.screenshot(path=str(shots / "win_3_folded.png"))
    at_bottom = {w: bool(r) and r["b"] >= vp["height"] - 70 and r["b"] <= vp["height"] + 1 for w, r in fr.items()}
    ks = list(fr)
    pairs = [(ks[i], ks[j]) for i in range(len(ks)) for j in range(i + 1, len(ks)) if fr[ks[i]] and fr[ks[j]] and overlap(fr[ks[i]], fr[ks[j]])]
    check("[5] folded windows line up along the bottom edge", len(fr) == 3 and all(at_bottom.values()), json.dumps({w: r and [round(r["x"]), round(r["y"]), round(r["w"]), round(r["h"])] for w, r in fr.items()}))
    check("[5] folded windows do not overlap each other", len(fr) == 3 and not pairs, str(pairs))

    # fold state remembered after reload
    load(page, reload=True)
    try:
        page.wait_for_selector("#appPanel", state="visible", timeout=30000)
    except Exception:  # noqa
        pass
    hw_windows(page, 1); page.wait_for_timeout(500)
    for win in ("#appPanel", ".hwWin"):
        r, h = geom(win, W[win])
        check(f"[5] {win} stays folded after reload", bool(r and h) and r["h"] <= h["h"] + 40, f"height {r and round(r['h'])} header {h and round(h['h'])}")
    page.screenshot(path=str(shots / "win_4_folded_reloaded.png"))
    check("[7] floating windows page: console has no errors (app iframe excluded)", not errs, "; ".join(errs[:3]))
    b.close()


# ---------------------------------------------------------------- [6] narrow window 390x844
def run_narrow(p):
    b, page, errs = open_site(p, cards=[MAIN["id"], OTHERS[0]["id"]], viewport={"width": 390, "height": 844}, mobile=True)
    wins = hw_windows(page, 2)
    page.wait_for_timeout(800)
    page.screenshot(path=str(shots / "narrow_1.png"))
    ov = page.evaluate("[document.documentElement.scrollWidth, document.body.scrollWidth, innerWidth]")
    check("[6] 390x844: no horizontal page overflow", max(ov[0], ov[1]) <= ov[2] + 1, f"scrollWidth {ov[0]}/{ov[1]} vs {ov[2]}")
    rs = [rect(page, wins.nth(i).element_handle()) for i in range(wins.count())]
    full = all(r["x"] >= -1 and r["r"] <= 391 and r["w"] >= 390 - 24 for r in rs)
    bottom = bool(rs) and all(r["y"] >= 844 * 0.4 for r in rs) and max(r["b"] for r in rs) >= 844 * 0.85 and max(r["b"] for r in rs) <= 845
    check("[6] 390x844: board windows full width at the bottom", wins.count() == 2 and full and bottom, str([[round(r[k]) for k in ("x", "y", "w", "h")] for r in rs]))
    check("[7] 390x844: console has no errors", not errs, "; ".join(errs[:3]))
    b.close()


try:
    with sync_playwright() as p:
        learn_boards(p)
        two, three = [MAIN["id"], OTHERS[0]["id"]], [MAIN["id"]] + [x["id"] for x in OTHERS[:2]]
        for fn in (run_main, run_project_key, run_kit, lambda p: run_multi(p, two), lambda p: run_multi(p, three), run_windows, run_narrow) + ((run_plain,) if a.plain_url else ()):
            try:
                fn(p)
            except Exception as e:  # noqa  (one broken scenario must not hide the others)
                check(f"scenario {getattr(fn, '__name__', 'multi')} ran to the end", False, f"{type(e).__name__}: {str(e)[:300]}")
finally:
    if srv:
        srv.shutdown()
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
