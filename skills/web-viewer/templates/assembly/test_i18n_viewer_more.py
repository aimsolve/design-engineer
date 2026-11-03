#!/usr/bin/env python3
"""Real-browser check of the viewer's languages on the surfaces test_i18n_viewer.py does not reach:
the boards on the desk (Web Serial flashing), the simulation and CAD-motion layer, the cycle bar, the APK button, the clash plaque and
the virtual device window.

  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_i18n_viewer_more.py \
      [--machine <machine checkout: build/ + sim/>] [--framework <dir>] [--swiftshader] [--shots <dir>]
(first run: `... --with playwright playwright install chromium`; --framework = build and serve the viewer of another framework checkout)

The model: --machine <dir>/build, else $VIEWER_TEST_MODEL, else the demo machine (examples/nema-bracket, built once in a temp dir).
The machine layer (sim with CAD motion and «not in CAD» clones): --machine <dir>/sim, else $VIEWER_TEST_SIM; without one [3b] and [3c]
are reported NOT COVERED (the demo has no sim) and the boards run on the model with a synthetic firmware publish.

Sites (built with build.py of the tested checkout, served like the stack by viewer_stack_server.py, removed at exit):
  machine-hw  the importer output + --sim <sim> (when there is one) + --flash (the fake router publishes boards master / slave / boxes;
              with the sim, CAD motion with «not in CAD» clones). Model data may be Russian: part / node names, clone labels and notes
              are taken out of every text before it is checked, so only what the viewer itself wrote is judged.
  model-cyc   the same importer output, no sim, --motion <a cycle file>: the cycle bar.
  dev-proj   test_device.py's tiny device model (virtual-controllers fixture demo.device.json, firmware built with emsdk, cached):
             the device window. Skipped (reported, not failed) when emsdk is not there.
No real hardware: a fake navigator.serial with three granted ports (CP2102 -> master, Arduino Uno -> slave, an ESP32 with native
USB that the model has no board for) and window.__hwSessionFor(port) = a fake session; the slave's flash fails with an English
esptool-style error, the master's goes through. The router is faked: /kit/** off, /hw/** 404, model.json, apk/latest.json.

Checks, en and pl (the number is the point of the review):
  [2] no Cyrillic in #secHw, the board windows (.hwWin: header, chip / USB class label, status line, port picker, the log lines the
      page writes), before and after a flash that works and one that fails.
  [3a] the cycle clock #cyClk: its unit;            [3b] the «not in CAD» suffix of a clone's card title and the card's labels;
  [3c] the unresolved CAD-motion warning #mvWarn;   [3d] the device window title and its key names;
  [3e] the APK button #bApk (text and title) with an apk/latest.json;
  [3f] the clash plaque: head title for a project not assembled, assembled with consent "cad", assembled without; the rows of
       #clashList apart from the part names.
  [reg] the console stays clean.
Review 2, ru / en / pl:
  [r2-1] a port another program holds (open() throws InvalidStateError «Failed to open serial port.», the page's OWN FlashSession):
         api.hw.monitor(card) rejects and the next ports event (POST /hw/<pid>/event, recorded here) reports it held: false.
  [r2-3] the board log counter: ru = i18n hw2.lines (unchanged); en «1 line» / «2 lines»; pl 1 wiersz / 2–4, 22 wiersze / 5–21 wierszy, or
         one abbreviation for every number.
  [r2-4] hw.mjs portLabel(): no Cyrillic once the page is en / pl (it may also be removed: then this check passes and says so).
Exit code 1 on any failed check.
"""
import argparse, atexit, json, os, re, shutil, subprocess, sys, tempfile, urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import SIM_ENV, build, model_dir, parts_of, serve_stack, ui_texts  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--machine", default=None, help="a machine checkout: <dir>/build (importer output) and <dir>/sim; default: $VIEWER_TEST_MODEL / $VIEWER_TEST_SIM, else the demo machine without a sim")
ap.add_argument("--framework", default=None, help="another framework checkout (the dir holding skills/, or a repo with it in agent/): its build.py, and its viewer served at /_viewer/")
ap.add_argument("--swiftshader", action="store_true")
ap.add_argument("--shots", default=None)
a = ap.parse_args()
FRAMEWORK = Path(a.framework).expanduser().resolve() if a.framework else HERE.parents[3]
if not (FRAMEWORK / "skills").is_dir() and (FRAMEWORK / "agent" / "skills").is_dir():
    FRAMEWORK = FRAMEWORK / "agent"
BUILD = FRAMEWORK / "skills/web-viewer/templates/assembly/build.py"
if a.machine:
    SRC, SIM = Path(a.machine).expanduser() / "build", Path(a.machine).expanduser() / "sim"
else:
    SRC, SIM = model_dir(None), (Path(os.environ[SIM_ENV]).expanduser() if os.environ.get(SIM_ENV) else None)
if SIM is not None and not (SIM / "site.json").is_file():
    sys.exit(f"no site.json in the machine's sim dir {SIM}")
RU = ui_texts("ru")
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="i18n_more_shots_")); shots.mkdir(parents=True, exist_ok=True)
if not a.shots:
    atexit.register(shutil.rmtree, shots, True)
GPU = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
CYR_CLASS = "[\u0410-\u044f\u0401\u0451]"     # the Russian alphabet, upper and lower case
CYR = re.compile(CYR_CLASS)
rows, ok_all = [], True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)
    return ok


# ---------------------------------------------------------------- sites
root = Path(tempfile.mkdtemp(prefix="i18n_more_site_"))
atexit.register(shutil.rmtree, root, True)
HW = "machine-hw"
if SIM is not None:
    flash = ["--sim", SIM, "--flash"]   # --flash: nothing published; the fake router publishes boards beside the simulator's list
else:                                   # no sim: a firmware publish with no boards, so the site has fw/index.json for the fake router to fill
    pub = root / "fw_publish"; pub.mkdir()
    (pub / "index.json").write_text('{"boards": []}', encoding="utf-8")
    flash = ["--flash", pub]
r = build(SRC, root / HW, *flash, "--kit", "none", build_py=BUILD)
if r.returncode:
    sys.exit(f"build.py {' '.join(map(str, flash))} failed:\n" + (r.stderr or r.stdout)[-2000:])
cyc = root / "cycle.json"
cyc.write_text(json.dumps({"cycle": {"period_s": 2, "loop": True, "groups": {"slide": {"axis": "x", "travel_mm": 10, "segments": [[0, 1, 0, 1]]}}}}))
r = build(SRC, root / "model-cyc", "--motion", cyc, "--flash", "none", "--kit", "none", build_py=BUILD)
if r.returncode:
    sys.exit("build.py --motion failed:\n" + (r.stderr or r.stdout)[-2000:])


def model_strings():
    """Every name the model brings (parts.json, the glb's nodes, motion.json clones, site.json labels) and the short forms the viewer
    shows of them (without a shared product-code prefix, without the drawing number: every tail after a space). Longest first, so a
    full name goes before its tail."""
    out = set()

    def walk(v):
        if isinstance(v, str):
            out.add(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(json.loads((SRC / "parts.json").read_text(encoding="utf-8")))
    g = (SRC / "model.glb").read_bytes()
    walk([n.get("name", "") for n in json.loads(g[20:20 + int.from_bytes(g[12:16], "little")]).get("nodes", [])])
    if SIM is not None:
        site = json.loads((SIM / "site.json").read_text(encoding="utf-8"))
        walk(site)
        for rel in site.get("extra", []):
            f = (SIM / rel).resolve()
            if f.suffix == ".json" and f.is_file():
                walk(json.loads(f.read_text(encoding="utf-8")))
        for f in SIM.glob("*.mjs"):   # the machine's own modules (board titles, signal names): project data, not the viewer's
            out |= set(re.findall("[\"'`]([^\"'`\n]*" + CYR_CLASS + "[^\"'`\n]*)[\"'`]", f.read_text(encoding="utf-8")))
    more = set()
    for s in out:
        w = s.split()       # a product-code prefix and / or a drawing number the page drops: the name's tail after one or two words
        for k in (1, 2):    # a tail after words with no digit only when it is long enough not to hide a viewer text
            t = " ".join(w[k:])
            if t and (all(any(c.isdigit() for c in x) for x in w[:k]) or len(w) - k >= 2 or len(t) >= 8):
                more.add(t)
        more |= set(p for p in re.split(r"[/\[\]]", s) if p)   # node paths: their segments
    return sorted((x for x in out | more if CYR.search(x)), key=len, reverse=True)


MODEL = model_strings()


def own_text(s):
    """s without the model's names: what the viewer itself wrote."""
    for m in MODEL:
        if m in s:
            s = s.replace(m, " ")
    return s


COLLECT = """(roots) => {
  const vis = (el) => { if (!el.getClientRects().length) return false; const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none'; };
  const out = [];
  for (const sel of roots) for (const r of document.querySelectorAll(sel)) {
    if (!vis(r)) continue;
    for (const el of [r, ...r.querySelectorAll('*')]) {
      if (!vis(el) && el.tagName !== 'OPTION') continue;
      const key = sel + ' ' + (el.id ? '#' + el.id : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : ''));
      let own = '';
      for (const n of el.childNodes) if (n.nodeType === 3) own += n.textContent;
      own = own.trim();
      if (own) out.push([key, own]);
      for (const at of ['title', 'aria-label', 'placeholder']) { const v = el.getAttribute(at); if (v && v.trim()) out.push([key + '@' + at, v.trim()]); }
    }
  }
  return out;
}"""


def ui(page, roots):
    return page.evaluate(COLLECT, roots)


def russian(items):
    return [(k, v) for k, v in items if CYR.search(own_text(v))]


# ---------------------------------------------------------------- fake Web Serial + router
PORTS = [{"card": "master", "vid": 0x10C4, "pid": 0xEA60, "chip": "ESP32", "desc": "ESP32-D0WD (CP2102)"},
         {"card": "slave", "vid": 0x2341, "pid": 0x0043, "chip": "ATmega328P", "desc": "Arduino Uno"},
         {"card": "stray", "vid": 0x303A, "pid": 0x1001, "chip": "ESP32-S3", "desc": "ESP32-S3 (USB JTAG/serial)"}]
INIT_JS = r"""
(() => {
  const cfg = __CFG__;
  try { localStorage.setItem('hw.assign', JSON.stringify({ '2341:0043': 'slave' })); } catch (e) {}
  const ports = cfg.ports.map((p, i) => ({ __i: i, getInfo: () => ({ usbVendorId: p.vid, usbProductId: p.pid }),
    open: async () => { if (p.busy) throw new DOMException('Failed to open serial port.', 'InvalidStateError'); },   // another program has it
    close: async () => {}, forget: async () => {}, setSignals: async () => {}, readable: null, writable: null, connected: true,
    addEventListener() {}, removeEventListener() {} }));
  const serial = { getPorts: async () => ports.slice(), requestPort: async () => ports[0],
    addEventListener() {}, removeEventListener() {}, onconnect: null, ondisconnect: null };
  try { Object.defineProperty(Navigator.prototype, 'serial', { get: () => serial, configurable: true }); }
  catch (e) { Object.defineProperty(navigator, 'serial', { value: serial, configurable: true }); }
  if (cfg.real) return;   // the page's own FlashSession on these ports
  const emitters = {};
  window.__emit = (card, n) => { for (let k = 0; k < n; k++) (emitters[card] || []).forEach((f) => f(`${card} line ${k}`)); };   // n monitor lines
  window.__hwSessionFor = (port) => {
    const p = cfg.ports[port && port.__i] || cfg.ports[0], subs = new Set();
    const emit = (line) => subs.forEach((fn) => { try { fn(line); } catch (e) {} });
    (emitters[p.card] ||= []).push(emit);
    const s = { card: p.card, monitoring: false, monitorSpeed: 115200, lastKind: null,
      subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
      async flashBoard(o) { o = o || {};
        for (let k = 0; k <= 10; k++) { if (o.onProgress) try { o.onProgress(k * 10); } catch (e) {} await new Promise((r) => setTimeout(r, 60)); }
        if (p.card === 'slave') throw new Error('Failed to connect with the device');
        emit('flash done'); return { chip: p.chip }; },
      async openMonitor() { s.monitoring = true; ['boot: ok', p.card + ' ready'].forEach(emit); },
      async closeMonitor() { s.monitoring = false; }, async resetBoard() { emit('reset'); },
      async identify() { return { chip: p.chip, description: p.desc }; } };
    return s;
  };
})();
"""
FAKE_SERIAL = INIT_JS.replace("__CFG__", json.dumps({"ports": PORTS}))
BUSY_SERIAL = INIT_JS.replace("__CFG__", json.dumps({"ports": [dict(PORTS[0], busy=True)], "real": True}))   # master's port, held elsewhere
AVR_MANIFEST = {"kind": "avr", "chip": "atmega328p", "monitor_speed": 115200, "version": "a41c09d 20261019-1712", "files": [{"offset": "0x0", "url": "firmware.hex"}]}
ESP_MANIFEST = {"kind": "esptool", "chip": "esp32", "monitor_speed": 115200, "version": "b7efe4e 20261020-2003", "files": [{"offset": "0x10000", "url": "firmware.bin"}]}


def routes(ctx, proj, clash=None, apk=False, hw_events=None):
    def fw_index(route):
        try:
            body = route.fetch().json()
        except Exception:  # noqa
            return route.continue_()
        if isinstance(body, list):   # with --sim and nothing published: the simulator's list; published boards go beside it
            body = {"sim": body, "boards": []}
        if isinstance(body, dict) and isinstance(body.get("boards"), list):
            have = {x.get("card") for x in body["boards"]}
            if "master" not in have:
                body["boards"].append({"card": "master", "chip": "esp32", "usb": ["usb-uart"], "manifest": "master/manifest.json", "version": "b7efe4e 20261020-2003"})
            body["boards"] += [{"card": c, "chip": "atmega328p", "usb": ["arduino"], "manifest": f"{c}/manifest.json", "version": "a41c09d 20261019-1712"}
                               for c in ("slave", "boxes") if c not in have]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))

    def manifest(route):
        card = urllib.parse.urlparse(route.request.url).path.rstrip("/").split("/")[-2]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(ESP_MANIFEST if card == "master" else AVR_MANIFEST))

    ctx.route(re.compile(r".*/fw/index\.json(\?.*)?$"), fw_index)
    ctx.route(re.compile(r".*/fw/[^/]+/manifest\.json(\?.*)?$"), manifest)
    ctx.route(re.compile(r".*/fw/[^/]+/firmware\.(bin|hex)(\?.*)?$"), lambda r: r.fulfill(status=200, body=b"\x00" * 64, content_type="application/octet-stream"))
    ctx.route(re.compile(r"^https?://[^/]+/kit/.*"), lambda r: r.fulfill(status=200, content_type="application/json", body='{"enabled": false}'))
    if hw_events is None:
        ctx.route(re.compile(r"^https?://[^/]+/hw/.*"), lambda r: r.fulfill(status=404, content_type="application/json", body='{"error": "no router"}'))
    else:   # a router that listens: every POST /hw/<pid>/event body is recorded
        def hw(route):
            if route.request.method == "POST":
                try:
                    hw_events.append(json.loads(route.request.post_data or "{}"))
                except ValueError:
                    pass
                return route.fulfill(status=200, content_type="application/json", body="{}")
            route.fulfill(status=200, content_type="application/json", body='{"cmds": []}')
        ctx.route(re.compile(r"^https?://[^/]+/hw/.*"), hw)
    ctx.route(re.compile(r".*/model\.json(\?.*)?$"), lambda r: r.fulfill(status=200, content_type="application/json", body='{"v": 0, "built": 0, "building": false}'))
    if apk:
        ctx.route(re.compile(r".*/apk/latest\.json(\?.*)?$"), lambda r: r.fulfill(status=200, content_type="application/json",
                                                                           body='{"version": "1.2.3", "file": "app-1.2.3.apk"}'))
    if clash is not None:
        conf = json.loads((root / proj / "config.json").read_text(encoding="utf-8"))
        conf["clearance"] = "clearance.json"
        ctx.route(f"**/{proj}/config.json*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(conf, ensure_ascii=False)))
        ctx.route(f"**/{proj}/clearance.json*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(clash)))


BENIGN = re.compile(r"/(kit|hw)/|/app/")


def open_page(b, proj, lang, clash=None, apk=False, serial=False, motion_patch=None, sim=False, hw_events=None):
    """serial: False | "fake" (three ports, fake sessions) | "busy" (master's port held by another program, the page's own sessions)."""
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    ctx.add_init_script(f"try {{ localStorage.setItem('assembly.sec.{proj}.dl', '0'); localStorage.setItem('assembly.sec.{proj}.hw', '0'); }} catch (e) {{}}")
    if serial:
        ctx.add_init_script(BUSY_SERIAL if serial == "busy" else FAKE_SERIAL)
    routes(ctx, proj, clash, apk, hw_events)
    patched = []   # what the motion.json patch did: harness evidence for [3c]
    if motion_patch:
        def mot(route):
            try:
                body = route.fetch().json()
                route.fulfill(status=200, content_type="application/json", body=json.dumps(motion_patch(body), ensure_ascii=False))
                patched.append("patched " + route.request.url.rsplit("/", 1)[-1])
            except Exception as e:  # noqa
                patched.append(f"patch failed: {e}")
                route.continue_()
        ctx.route(re.compile(rf".*/{proj}/sim/motion\.json(\?.*)?$"), mot)
    page = ctx.new_page()
    page.__dict__["_patched"] = patched
    errs, warns = [], []
    page.__dict__["_warns"] = warns
    page.on("console", lambda m: warns.append(m.text) if m.type == "warning" else None)
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" and not BENIGN.search((m.location or {}).get("url") or "") else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(f"{root_url}/{proj}/?app=0&lang={lang}", wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    if sim:
        try:
            page.wait_for_function("window.__viewer.sim && window.__viewer.sim.vm", timeout=60000)
        except Exception:  # noqa
            pass
    page.wait_for_timeout(2500)
    return ctx, page, errs


# ---------------------------------------------------------------- device site (test_device.py's fixture)
def device_site():
    """-> project id, or (None, why). Uses the virtual-controllers helper that builds and caches the demo firmware with emsdk."""
    import struct
    vc = FRAMEWORK / "skills" / "virtual-controllers"
    js = ("import { emEnv, buildFixture } from %s; const e = emEnv(); if (e.skip) console.log('SKIP ' + e.skip); "
          "else console.log('FW ' + buildFixture('demo'));") % json.dumps((vc / "tests" / "helpers.mjs").as_uri())
    try:
        out = subprocess.run(["node", "--input-type=module", "-e", js], capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"firmware build: {e}"
    last = (out.stdout.strip().splitlines() or [""])[-1]
    if not last.startswith("FW "):
        return None, (last[5:] if last.startswith("SKIP ") else (out.stderr or out.stdout)[-300:])
    fw = Path(last[3:])
    boxes = {"esp32_devkit": ((0.050, 0.028, 0.004), (-0.045, 0.0, 0.0)), "tft": ((0.034, 0.044, 0.004), (0.010, 0.0, 0.0)),
             "btn_play": ((0.012, 0.012, 0.008), (0.055, 0.012, 0.0)), "enc": ((0.014, 0.014, 0.010), (0.055, -0.014, 0.0))}
    src = root / "dev_in"; (src / "parts").mkdir(parents=True)
    blob, views, accs, meshes, nodes = bytearray(), [], [], [], []

    def add(data, target, ctype, count, typ, mn=None, mx=None):
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data), "target": target}); blob.extend(data)
        acc = {"bufferView": len(views) - 1, "componentType": ctype, "count": count, "type": typ}
        if mn is not None:
            acc["min"], acc["max"] = mn, mx
        accs.append(acc)
        return len(accs) - 1
    for pid, ((sx, sy, sz), centre) in boxes.items():
        hx, hy, hz = sx / 2, sy / 2, sz / 2
        quads = [((1, 0, 0), [(hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz), (hx, -hy, hz)]), ((-1, 0, 0), [(-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz), (-hx, -hy, -hz)]),
                 ((0, 1, 0), [(-hx, hy, -hz), (-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz)]), ((0, -1, 0), [(-hx, -hy, hz), (-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz)]),
                 ((0, 0, 1), [(-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]), ((0, 0, -1), [(hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz)])]
        pos, nrm, idx = [], [], []
        for n, q in quads:
            k = len(pos); pos += q; nrm += [n] * 4; idx += [k, k + 1, k + 2, k, k + 2, k + 3]
        pa = add(b"".join(struct.pack("<3f", *p) for p in pos), 34962, 5126, len(pos), "VEC3", [min(p[i] for p in pos) for i in range(3)], [max(p[i] for p in pos) for i in range(3)])
        na = add(b"".join(struct.pack("<3f", *n) for n in nrm), 34962, 5126, len(nrm), "VEC3")
        ia = add(b"".join(struct.pack("<H", i) for i in idx), 34963, 5123, len(idx), "SCALAR")
        meshes.append({"primitives": [{"attributes": {"POSITION": pa, "NORMAL": na}, "indices": ia, "material": 0}]})
        nodes.append({"name": pid, "mesh": len(meshes) - 1, "translation": list(centre), "extras": {"part": pid}})
    g = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"name": "Player", "children": list(range(1, len(nodes) + 1))}] + nodes,
         "meshes": meshes, "accessors": accs, "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
         "materials": [{"name": "default", "pbrMetallicRoughness": {"baseColorFactor": [0.62, 0.64, 0.67, 1]}}]}
    jb = json.dumps(g, separators=(",", ":")).encode(); jb += b" " * (-len(jb) % 4)
    while len(blob) % 4:
        blob.append(0)
    (src / "model.glb").write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(jb) + 8 + len(blob)) + struct.pack("<II", len(jb), 0x4E4F534A) + jb
                                    + struct.pack("<II", len(blob), 0x004E4942) + bytes(blob))
    names = {"esp32_devkit": "ESP32 DevKit", "tft": "TFT 240x320", "btn_play": "Button Play", "enc": "Encoder EC11"}
    (src / "parts.json").write_text(json.dumps({"assembly": {"root": "Player"}, "parts": [
        {"id": p, "name": names[p], "drawing": None, "kind": "electrical", "material": "other", "instances": 1, "files": {},
         "bbox": [[-s[0] * 500, -s[1] * 500, -s[2] * 500], [s[0] * 500, s[1] * 500, s[2] * 500]]} for p, (s, _) in boxes.items()]}), encoding="utf-8")
    sim = root / "dev_sim"; sim.mkdir()
    shutil.copyfile(vc / "tests" / "fixtures" / "demo.device.json", sim / "device.json")
    shutil.copytree(fw, sim / "fw", ignore=shutil.ignore_patterns("build"))
    (sim / "site.json").write_text(json.dumps({"device": "device.json", "fw": "fw"}), encoding="utf-8")
    r = build(src, root / "dev-proj", "--sim", sim, "--flash", "none", "--kit", "none", "--feedback", "none", build_py=BUILD)
    return ("dev-proj", "") if r.returncode == 0 else (None, f"build.py: {(r.stderr or r.stdout)[-300:]}")


srv, root_url = serve_stack(root, framework=FRAMEWORK)
_two = [p["id"] for p in sorted((p for p in parts_of(SRC) if not p.get("part_id")), key=lambda p: -(p.get("volume_mm3") or 0))][:2]
CLASH = {"clashes": [{"a": _two[0], "b": _two[-1], "volume_mm3": 12.5, "t_s": 0.75}]}   # two distinct parts of the model


# ---------------------------------------------------------------- the checks
def run(p):
    b = p.chromium.launch(args=GPU)
    dev, why = device_site()
    for lang in ("en", "pl"):
        # [2] boards on the desk
        ctx, page, errs = open_page(b, HW, lang, serial=True, sim=SIM is not None)
        try:
            page.wait_for_function("document.querySelectorAll('.hwWin').length >= 1", timeout=15000)
        except Exception:  # noqa
            if page.query_selector("#hwConnect"):
                page.click("#hwConnect"); page.wait_for_timeout(1500)
        n = page.locator(".hwWin").count()
        check(f"[2] {lang}: board windows open for the granted ports", n >= 2, f"{n} windows")
        items = ui(page, ["#secHw", ".hwWin"])
        check(f"[2] {lang}: #secHw and the board windows have no Cyrillic (before flashing)", items and not russian(items), str(russian(items)[:4]))
        labels = page.evaluate("[...document.querySelectorAll('.hwWin .hwChip')].map((e) => e.textContent.trim())")
        check(f"[2] {lang}: the USB class label of the ESP32 with native USB is not Russian", labels and not any(CYR.search(x) for x in labels), json.dumps(labels, ensure_ascii=False))
        pressed = page.evaluate("""[...document.querySelectorAll('.hwWin')].map((w) => {   // «Flash» in every window: master goes through, slave fails
            const b = w.querySelector('.hwA button'); if (!b || b.disabled) return null; b.click(); return b.textContent.trim(); })""")
        page.wait_for_timeout(3000)
        check(f"[2] {lang}: a flash button was pressed in the board windows", any(pressed), json.dumps(pressed, ensure_ascii=False))
        items = ui(page, ["#secHw", ".hwWin"])
        logs = page.evaluate("[...document.querySelectorAll('.hwWin .hwLog')].map((e) => e.textContent)")
        check(f"[2] {lang}: after a good and a failed flash: status lines have no Cyrillic", items and not russian(items), str(russian(items)[:4]))
        bad_log = [ln for t in logs for ln in t.splitlines() if CYR.search(own_text(ln))]
        check(f"[2] {lang}: the log lines the page writes have no Cyrillic", not bad_log, json.dumps(bad_log[:4], ensure_ascii=False))
        page.screenshot(path=str(shots / f"{lang}_boards.png"))

        # [3b] a «not in CAD» clone's card (needs the machine's sim with CAD motion)
        n_clone = page.evaluate("(window.__viewer.nodes || []).findIndex((o) => o && o.userData && o.userData.notInCad)") if SIM is not None else -1
        if SIM is not None and check(f"[3b] {lang}: the model has a «not in CAD» clone (motion.json clones)", n_clone >= 0, str(n_clone)):
            try:
                page.evaluate(f"window.__viewer.select({n_clone}, true)")
                sel_err = ""
            except Exception as e:  # noqa  (a page error while the card opens is a failure, not a crash of this test)
                sel_err = str(e).splitlines()[0][:200]
            check(f"[3b] {lang}: opening the clone's card raises no error", not sel_err, sel_err)
            page.wait_for_timeout(700)
            title = page.evaluate("(document.getElementById('infoT') || {}).textContent || ''")
            suffix = title.rsplit(" — ", 1)[1] if " — " in title else ""
            check(f"[3b] {lang}: the card title carries a «not in CAD» suffix, not Russian", suffix and not CYR.search(suffix), repr(title))
            dts = page.evaluate("[...document.querySelectorAll('#infoP dt')].map((e) => e.textContent.trim())")
            check(f"[3b] {lang}: the card's labels (dt) have no Cyrillic", dts and not any(CYR.search(x) for x in dts), json.dumps(dts, ensure_ascii=False))
        check(f"[reg] {lang} boards page: console clean", not errs, "; ".join(errs[:3]))
        ctx.close()

        # [3c] an unresolved CAD-motion path -> the warning (needs the machine's sim with CAD motion)
        def broken(m):
            m = dict(m)
            m["clones"] = list(m.get("clones") or []) + [{"id": "ghost_test", "actuator": "none", "from": ["No such node[0]/Nothing[0]"], "offset_mm": [0, 0, 0],
                                                          "label": "x", "note": "x"}]
            return m
        if SIM is not None:
            ctx, page, _ = open_page(b, HW, lang, motion_patch=broken, sim=True)
            w = page.evaluate("(document.getElementById('mvWarn') || {}).textContent || ''")
            mw = [x[:80] for x in page.__dict__["_warns"] if "[motion]" in x]
            has_body = page.evaluate("!!document.querySelector('#vmSec .secbody')")
            check(f"[3c] {lang}: the unresolved CAD-motion warning is shown without Cyrillic", w and not CYR.search(w),
                  f"{w!r}; motion.json: {page.__dict__['_patched']}; console: {mw}; #vmSec .secbody: {has_body}")
            ctx.close()

        # [3a] the cycle clock; [3e] the APK button
        ctx, page, errs = open_page(b, "model-cyc", lang, apk=True)
        clk = page.evaluate("(document.getElementById('cyClk') || {}).textContent || ''")
        check(f"[3a] {lang}: the cycle clock #cyClk shows a unit that is not Russian", clk and re.search(r"\d", clk) and not CYR.search(clk), repr(clk))
        bar = ui(page, ["#cyBar", "#cycleBar", "#cyc"]) if page.query_selector("#cyBar, #cycleBar, #cyc") else []
        check(f"[3a] {lang}: the cycle bar's controls have no Cyrillic", not russian(bar), str(russian(bar)[:3]))
        apk = page.evaluate("(() => { const e = document.getElementById('bApk'); return e ? [e.textContent.trim(), e.title || ''] : null; })()")
        check(f"[3e] {lang}: the APK button (text and title) is not Russian", apk and apk[0] and not any(CYR.search(x) for x in apk), json.dumps(apk, ensure_ascii=False))
        row = ui(page, ["#apkRow"])
        check(f"[3e] {lang}: the APK row has no Cyrillic", row and not russian(row), str(russian(row)[:3]))
        check(f"[reg] {lang} cycle page: console clean", not errs, "; ".join(errs[:3]))
        ctx.close()

        # [3f] the clash plaque, three kinds of project
        titles = {}
        for kind, extra in (("not assembled", {}), ("assembled, consent cad", {"assembled": True, "consent": "cad"}), ("assembled", {"assembled": True})):
            ctx, page, _ = open_page(b, "model-cyc", lang, clash=dict(CLASH, **extra))
            try:
                page.wait_for_selector("#clashHead", timeout=15000)
            except Exception:  # noqa
                pass
            head = page.evaluate("(() => { const h = document.getElementById('clashHead'); return h ? [h.textContent.trim(), h.title || ''] : null; })()")
            check(f"[3f] {lang} {kind}: clash head text and title are not Russian", head and not any(CYR.search(x) for x in head), json.dumps(head, ensure_ascii=False))
            titles[kind] = head and head[1]
            if page.query_selector("#clashHead"):
                page.click("#clashHead"); page.wait_for_timeout(400)
            rws = page.evaluate("[...document.querySelectorAll('#clashList > *')].map((e) => [e.innerText, e.title || ''])")
            bad = [x for row_ in rws for x in row_ if CYR.search(own_text(x))]
            check(f"[3f] {lang} {kind}: the clash rows have no Cyrillic apart from the part names", rws and not bad, json.dumps(bad[:3] or rws[:1], ensure_ascii=False))
            ctx.close()
        check(f"[3f] {lang}: the three head titles differ (who fixes it: us / with the client from CAD / with the client)",
              len({t for t in titles.values() if t}) == 3, json.dumps(titles, ensure_ascii=False))

        # [3d] the device window
        if dev:
            ctx, page, errs = open_page(b, dev, lang, sim=True)
            try:
                page.wait_for_selector("#devHead", timeout=30000)
            except Exception:  # noqa
                pass
            items = ui(page, ["#devHead", "#devHead ~ *", "[id^=dev]"])
            check(f"[3d] {lang}: the device window (title, buttons, key names) has no Cyrillic", items and not russian(items), str(russian(items)[:4] or items[:2]))
            check(f"[reg] {lang} device page: console clean", not errs, "; ".join(errs[:3]))
            ctx.close()
    if not dev:
        print(f"NOT COVERED [3d] the device window: {why}")
    if SIM is None:
        print(f"NOT COVERED [3b] [3c] the «not in CAD» clone card and the CAD-motion warning: no machine sim (pass --machine or set ${SIM_ENV})")

    # ---- review 2: busy port, line counter, portLabel; ru included (the ru texts must stay as they are)
    WANT_LINES = {   # n -> what the counter says, per language; pl may instead use one abbreviation for every n
        "ru": {n: RU["hw2"]["lines"].replace("{n}", str(n)) for n in (1, 2, 5, 12, 22)},
        "en": {1: "1 line", 2: "2 lines", 5: "5 lines", 12: "12 lines", 22: "22 lines"},
        "pl": {1: "1 wiersz", 2: "2 wiersze", 5: "5 wierszy", 12: "12 wierszy", 22: "22 wiersze"},
    }
    for lang in ("ru", "en", "pl"):
        # [r2-1] another program holds master's port: the page's own FlashSession fails to open it, the ports event says held: false
        events = []
        ctx, page, _ = open_page(b, HW, lang, serial="busy", sim=SIM is not None, hw_events=events)
        try:
            page.wait_for_function("window.__viewer.hw && window.__viewer.hw.ports().length >= 1", timeout=15000)
        except Exception:  # noqa
            pass
        ports = page.evaluate("window.__viewer.hw ? window.__viewer.hw.ports() : []")
        card = (ports[0].get("card") if ports else None) or "master"
        err = page.evaluate("""(c) => window.__viewer.hw.monitor(c).then(() => '', (e) => String(e && e.message || e))""", card) if ports else "no port"
        check(f"[r2-1] {lang}: opening the monitor on a port another program holds fails", bool(err), repr(err))
        page.evaluate("window.dispatchEvent(new Event('focus'))")   # the page re-sends its ports on focus
        page.wait_for_timeout(800)
        last = [e for e in events if e.get("type") == "ports"]
        held = [p.get("held") for p in (last[-1].get("ports") if last else [])]
        check(f"[r2-1] {lang}: the ports event reports that port as held: false (held by another program)", held == [False],
              f"{len(last)} ports events, last held: {held}")
        ctx.close()

        # [r2-3] the log line counter of a board window
        ctx, page, _ = open_page(b, HW, lang, serial="fake", sim=SIM is not None)
        try:
            page.wait_for_function("document.querySelectorAll('.hwWin').length >= 1", timeout=15000)
        except Exception:  # noqa
            pass
        page.evaluate("window.__viewer.hw && window.__viewer.hw.openLog('master')")   # the counter is in the log header: shown while the log is open
        page.wait_for_timeout(300)
        got, total = {}, 0
        for n in (1, 2, 5, 12, 22):
            page.evaluate(f"window.__emit && window.__emit('master', {n - total})")
            total = n
            page.wait_for_timeout(500)
            texts = [t for t in page.evaluate("[...document.querySelectorAll('.hwWin .hwLN')].map((e) => e.textContent.trim())") if t]
            got[n] = texts[0] if len(texts) == 1 else texts
        want = WANT_LINES[lang]
        exact = all(got[n] == want[n] for n in want)
        tails = {re.sub(r"^\d+\s*", "", str(got[n])) for n in want}
        abbrev = lang == "pl" and len(tails) == 1 and all(str(got[n]).startswith(f"{n} ") for n in want) and "." in next(iter(tails)) and not CYR.search(next(iter(tails)))
        check(f"[r2-3] {lang}: the board log counter agrees with the number" + (" (or a Polish abbreviation)" if lang == "pl" else ""),
              exact or abbrev, json.dumps(got, ensure_ascii=False))

        # [r2-4] portLabel (exported by hw.mjs; the page itself does not call it) once the page language is set
        lab = page.evaluate("""async () => { const m = await import('/_viewer/hw.js'); if (!m.portLabel) return null;
            return m.portLabel({ key: '303a:1001', class: 'esp-usb', vid: '303a', pid: '1001', serial: null, card: null }); }""")
        if lang == "ru":
            check(f"[r2-4] ru: portLabel stays Russian («{RU['hw2']['notInModel']}»)", lab is None or RU["hw2"]["notInModel"] in lab, repr(lab))
        else:
            check(f"[r2-4] {lang}: portLabel has no Cyrillic (or is no longer exported)", lab is None or not CYR.search(lab), repr(lab))
        ctx.close()
    b.close()


with sync_playwright() as p:
    run(p)
srv.shutdown()
print("\n| check | result | detail |\n|---|---|---|")
for r_ in rows:
    print("| " + " | ".join(str(x).replace("|", "/") for x in r_) + " |")
sys.exit(0 if ok_all else 1)
