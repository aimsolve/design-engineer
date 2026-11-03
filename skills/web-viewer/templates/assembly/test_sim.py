#!/usr/bin/env python3
"""Real-browser check of the machine layer (site built with build.py --sim): pacing accuracy, max speed, frame time, simulation bar (speed, pause, reset,
physical inputs), board list + board card, firmware switch, and (site.json "app") the app panel + BLE bridge.

With --url: an already served site. Without it: the test builds a site with THIS build.py from a machine's importer output (--in, else
$VIEWER_TEST_MODEL) and its sim dir (--sim, else $VIEWER_TEST_SIM) and serves it like the stack (viewer_stack_server.py, /<id>/). The demo
machine has no sim, so with neither given the test skips saying so.
  env -u VIRTUAL_ENV uv run --isolated --no-project --with playwright python test_sim.py [--url http://localhost:8765/ | --in <importer output> --sim <sim dir>]
      [--shots <dir>] [--seconds 20] [--cad-part <part id>] [--swiftshader]
The machine's own UI is its app, so the page has no start / stop / target field: the timeline is driven through the scripting API (`__viewer.sim.start(g)`, `.press(id)`).
Pacing tests load the page with ?app=0 (no 40 MB app iframe competing for the CPU); the app panel is tested on its own page load with ?app=1.
Every machine-specific expectation comes from the machine's own data: its boards, inputs and CAD mapping (`__viewer.sim.meta`, the sim entry's
META), its firmware builds (fw/index.json), its motion layer (sim/config.json `motion` -> motion.json) and the I2C traffic its firmware makes.
The page is in its default language (ru); every UI text the checks expect comes from i18n.mjs.
LOOK at every PNG. Exit code 1 on any failed check.
"""
import argparse, json, shutil, tempfile, statistics, sys, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from viewer_stack_server import build, model_dir, serve_stack, sim_dir, ui_texts  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--url", default=None, help="site root; ?app=0 / ?app=1 is added by the test (default: build one from --in and --sim and serve it)")
ap.add_argument("--in", dest="src", default=None, help="importer output dir of the machine (default: $VIEWER_TEST_MODEL)")
ap.add_argument("--sim", default=None, help="the machine's sim dir with site.json (default: $VIEWER_TEST_SIM)")
ap.add_argument("--shots", default=None, help="keep screenshots here (default: a temp dir; screenshots never go in the project)")
ap.add_argument("--seconds", type=float, default=20, help="real seconds at x1")
ap.add_argument("--cad-part", default=None, help="a part mapped to a board in META.cad (default: the first mapped part of the first mapped board)")
ap.add_argument("--swiftshader", action="store_true")
a = ap.parse_args()
T = ui_texts("ru")
srv = tmp = None
if not a.url:
    sim = sim_dir(a.sim)
    src = model_dir(a.src, needs="the importer output of the machine whose sim the test runs")
    tmp = Path(tempfile.mkdtemp(prefix="sim_site_"))
    r = build(src, tmp / "machine-site", "--sim", sim, "--flash", "none", "--kit", "none", "--feedback", "none")
    if r.returncode:
        sys.exit("build.py --sim failed:\n" + (r.stderr or r.stdout)[-3000:])
    srv, base = serve_stack(tmp)
    a.url = base + "/machine-site/"
a.url = a.url.rstrip("/") + "/"


def get_json(rel):
    try:
        with urllib.request.urlopen(a.url + rel) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001 - an optional file
        return None


SIMCFG = get_json("sim/config.json") or {}
MOTION_FILE = (SIMCFG.get("motion") or {}).get("file")
MOTION = (get_json("sim/" + MOTION_FILE) or {}).get("actuators", {}) if MOTION_FILE else {}
LAG_TAIL = T["sim"]["lag"].split("{x}", 1)[-1].strip()   # the static part of the "falling behind" readout
shots = Path(a.shots) if a.shots else Path(tempfile.mkdtemp(prefix="site_shots_")); shots.mkdir(parents=True, exist_ok=True); print("screenshots:", shots)
GPU = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if a.swiftshader else ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]
rows, ok_all, info = [], True, {}
M = {}   # the machine as the page knows it (set on the first page load): boards, inputs, the board roles the checks use


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    rows.append((name, "PASS" if ok else "FAIL", detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}", flush=True)


FRAMES_JS = """() => { const w = window.__fr = { d: [], on: true, last: performance.now() };
  (function loop(t) { if (!w.on) return; w.d.push(t - w.last); w.last = t; requestAnimationFrame(loop); })(performance.now()); }"""


def frames(page):
    d = page.evaluate("() => { window.__fr.on = false; const d = window.__fr.d.slice(3); return d; }")
    d.sort()
    return {"n": len(d), "mean": statistics.mean(d), "p99": d[int(len(d) * 0.99) - 1] if d else 0, "max": d[-1] if d else 0}


def motion_diff(page):
    """{id: max |world - home|} over the bound nodes of each motion.json actuator (0 = at home). None if the machine has no motion layer."""
    return page.evaluate("""() => { const m = window.__viewer.sim.motion; if (!m) return null;
      const now = m.probe(), out = {};
      now.forEach((n, i) => { const h = m.homeProbe[i].world; let d = 0; for (let k = 0; k < 16; k++) d = Math.max(d, Math.abs(n.world[k] - h[k])); out[n.id] = Math.max(out[n.id] || 0, d); });
      return out; }""")


def learn_machine(page):
    """The machine's boards / inputs from the page (META) and fw/index.json, and which board plays which role in the checks."""
    v = "window.__viewer"
    meta = page.evaluate(f"JSON.parse(JSON.stringify({{ boards: {v}.sim.meta.boards || [], inputs: {v}.sim.meta.inputs || [] }}))")
    fw = page.evaluate(f"JSON.parse(JSON.stringify({v}.sim.fwIndex() || []))")
    parts = set(page.evaluate(f"Object.keys({v}.byPart)"))
    boards = meta["boards"]
    mapped = [b for b in boards if b.get("cad") and any(c["part"] in parts for c in b["cad"])]
    main = mapped[0] if mapped else boards[0]
    M.update(boards=boards, inputs=meta["inputs"], fw=fw, main=main, mapped=mapped,
             unmapped=next((b for b in boards if not b.get("cad")), None),
             cad_part=a.cad_part or next((c["part"] for b in mapped for c in b["cad"] if c["part"] in parts), None),
             switch=next((b for b in boards if len([f for f in fw if f["mcu"] == b["mcu"]]) >= 2), None))
    info["machine"] = {"boards": [b["id"] for b in boards], "main": main["id"], "cad_part": M["cad_part"],
                       "unmapped": M["unmapped"] and M["unmapped"]["id"], "fw switch": M["switch"] and M["switch"]["id"], "motion actuators": len(MOTION)}


def open_page(p, viewport, mobile, app=False):
    b = p.chromium.launch(args=GPU)
    ctx = b.new_context(viewport=viewport, device_scale_factor=1, is_mobile=mobile, has_touch=mobile)
    page = ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append(str(e)))
    page.goto(a.url.rstrip("/") + "/?app=" + ("1" if app else "0"), wait_until="load")
    page.wait_for_function("window.__viewer && (window.__viewer.ready || window.__viewer.error)", timeout=120000)
    page.wait_for_function("window.__viewer.sim || window.__viewer.simError", timeout=60000)
    return b, page, errs


def run_desktop(p):
    b, page, errs = open_page(p, {"width": 1440, "height": 900}, False)
    v = "window.__viewer"
    check("desktop: model + sim load", not page.evaluate(f"{v}.error || {v}.simError"), str(page.evaluate(f"{v}.simError")))
    if page.evaluate(f"!{v}.sim"):
        b.close(); return
    learn_machine(page)
    main = M["main"]
    check("desktop: boards section + simulation bar present, no invented machine UI", page.is_visible("#vmSec") and page.is_visible("#simbar") and not page.query_selector("#vmStart, #vmTarget, #vmSet, #vmStop"))
    page.wait_for_timeout(500)
    st = page.evaluate(f"{v}.sim.state()")
    check("sim.state() shape (one entry per META board)", all(k in st for k in ("t", "clock", "speed", "achieved", "paused", "status", "boards"))
          and [x["id"] for x in st["boards"]] == [x["id"] for x in M["boards"]], json.dumps({k: st[k] for k in ("clock", "speed", "paused")}))
    act = page.evaluate(f"{v}.sim.actuators()")
    info["actuators"] = {k: act[k] for k in list(act)[:3]}
    id_map = (SIMCFG.get("motion") or {}).get("id_map") or {}
    bound = {id_map.get(k, k) for k in MOTION}
    check("sim.actuators() shape {id:{kind,steps,pos,vel,on}}, every motion.json actuator among them",
          len(act) > 0 and all(set(x) >= {"kind", "steps", "pos", "vel", "on"} for x in act.values()) and bound <= set(act),
          f"{len(act)} actuators: {sorted(act)[:8]}...; motion.json ids missing: {sorted(bound - set(act))}")

    # ---- x1: accuracy over N real seconds after the start ----
    page.evaluate(FRAMES_JS)
    page.evaluate(f"{v}.sim.start(500)")
    t0 = page.evaluate("[performance.now(), window.__viewer.sim.state().t]")
    page.wait_for_timeout(int(a.seconds * 1000 / 2))
    page.screenshot(path=str(shots / "desktop_1_running.png"))
    page.wait_for_timeout(int(a.seconds * 1000 / 2))
    t1 = page.evaluate("[performance.now(), window.__viewer.sim.state().t]")
    wall = (t1[0] - t0[0]) / 1000; virt = t1[1] - t0[1]
    fr = frames(page)
    info["x1"] = {"wall": wall, "virt": virt, **fr}
    check(f"x1: clock advanced ~{a.seconds:.0f} s in {wall:.1f} real s (+-10%)", abs(virt / wall - 1) <= 0.10, f"{virt:.2f} machine s / {wall:.2f} real s = x{virt / wall:.3f}")
    act = page.evaluate(f"{v}.sim.actuators()"); st = page.evaluate(f"{v}.sim.state()")
    steppers = {k: x for k, x in act.items() if x["kind"] == "stepper"}
    ran = sorted(k for k, x in steppers.items() if x["steps"] > 0)
    check("x1: the started timeline drives the machine's steppers", not steppers or ran,
          f"status='{st['status'].get('text')}' ({st['status'].get('code')}), {len(ran)} of {len(steppers)} steppers stepped: {ran[:6]}, clock {st['clock']}")
    check("x1: frame time <= 33 ms while running (p99)", fr["p99"] <= 33.5, f"mean {fr['mean']:.1f} p99 {fr['p99']:.1f} max {fr['max']:.1f} ms over {fr['n']} frames")
    check("x1: achieved speed reported ~x1, not lagging", not st["lagging"] and 0.9 <= st["achieved"] <= 1.1, f"achieved {st['achieved']:.2f}")

    # ---- CAD motion (motion.json binder): nodes moved while running, frozen on pause ----
    if MOTION:
        mo = page.evaluate(f"{v}.sim.motion && {{bound: {v}.sim.motion.bound, unresolved: {v}.sim.motion.unresolved}}")
        check("motion: binder mounted, every motion.json path resolved", bool(mo) and mo["bound"] > 0 and not mo["unresolved"], json.dumps(mo, ensure_ascii=False)[:200])
        md = motion_diff(page)
        moved = sorted(k for k, d in (md or {}).items() if d > 1e-4)
        info["motion_moved"] = moved
        kinds = {x.get("kind") for x in MOTION.values()}
        still = sorted(k for k in kinds if not any(MOTION.get(i, {}).get("kind") == k for i in moved))
        check(f"motion: actuators of every motion.json kind ({', '.join(sorted(map(str, kinds)))}) moved while running", not still,
              f"{len(moved)} of {len(md or {})} actuators moved: {moved}; kinds that did not move: {still}")
        page.evaluate(f"{v}.sim.pause()"); page.wait_for_timeout(300)
        m1 = motion_diff(page); page.wait_for_timeout(600); m2 = motion_diff(page)
        check("motion: nothing moves while the sim is paused", m1 == m2, "")
        page.evaluate(f"{v}.sim.resume()")
    else:
        print("[info] the machine has no motion layer (sim/config.json `motion`): motion checks skipped", flush=True)

    # ---- simulation bar: physical input (the machine's own buttons, META.inputs) ----
    inp = page.evaluate(f"{v}.sim.inputs()")
    check("simulation bar lists the machine's physical inputs (META.inputs)", len(inp) == len(M["inputs"]) and page.locator("#simbar [data-in]").count() == len(inp),
          json.dumps(inp, ensure_ascii=False)[:160])
    if inp:
        i0 = inp[0]; lvl = f"{v}.sim.vm.m.sim.board('{i0['board']}').level({i0['pin']})"
        page.evaluate(f"{v}.sim.resume()")
        before = page.evaluate(lvl)
        page.click(f'#simbar [data-in="{i0["id"]}"]')
        during = page.evaluate(lvl)
        page.evaluate(f"{v}.sim.step(400)")
        after = page.evaluate(lvl)
        check("pressing the physical button drives its pin, released after ~300 ms of machine time", (before, during, after) == (1 - i0["active"], i0["active"], 1 - i0["active"]), f"level before/during/after = {before}/{during}/{after}")

    # ---- board card (the first board mapped to CAD, else the first board) ----
    page.click("#vmSec .sechead") if not page.is_visible("#vmBoards") else None
    page.click(f'.brd[data-b="{main["id"]}"]')
    page.wait_for_timeout(700)
    check(f"board card of {main['id']} opens from the list", page.is_visible("#bcard") and page.inner_text("#bT") == main["name"], page.inner_text("#bT"))
    ser = page.inner_text("#bSerial")
    check("board card: live Serial lines", len(ser.strip().splitlines()) >= 3, f"{len(ser.splitlines())} lines; last: {ser.splitlines()[-1] if ser.strip() else ''}")
    page.uncheck("#bHideNum"); page.wait_for_timeout(400)   # with rows of plain numbers shown too, a running board's log must move
    l1 = page.inner_text("#bSerial").splitlines()
    page.wait_for_timeout(3000)
    l2 = page.inner_text("#bSerial").splitlines()
    check("board card: Serial keeps updating", l1 != l2 or len(l2) >= 200, f"{len(l1)} -> {len(l2)} lines")
    npins = page.evaluate(f"{v}.sim.vm.m.pinTable('{main['id']}').length")
    check("board card: pin table with levels (a row per pin of the board's pin table)", npins > 0 and page.locator("#bPins tr").count() == npins,
          f"{page.locator('#bPins tr').count()} rows of {npins}; first: {page.locator('#bPins tr').first.inner_text()[:60] if npins else ''}")
    if M["mapped"]:
        check(f"board card: «{T['sim']['show3d']}» shown for a board mapped to CAD", page.is_visible("#bShow"))
    page.screenshot(path=str(shots / "desktop_2_board_main.png"))
    i2c = page.evaluate(f"JSON.parse(JSON.stringify(Object.fromEntries(Object.entries({v}.sim.vm.i2c).map(([k, l]) => [k, l.slice(-1)]))))")
    other = next((x for x in M["boards"] if x["id"] != main["id"] and i2c.get(x["id"])), None)
    if other:
        last = i2c[other["id"]][-1]
        page.click('#bX'); page.click(f'.brd[data-b="{other["id"]}"]'); page.wait_for_timeout(600)
        i2 = page.inner_text("#bI2c")
        check(f"{other['id']} card: its I2C traffic ({last['from']} -> 0x{last['addr']:x})", f"{last['from']} → " in i2 and f"0x{last['addr']:x}" in i2, i2.splitlines()[-1][:90] if i2 else "")
    else:
        print("[info] no other board has I2C traffic: the I2C card check skipped", flush=True)
    if M["unmapped"]:
        page.click('#bX'); page.click(f'.brd[data-b="{M["unmapped"]["id"]}"]'); page.wait_for_timeout(600)
        check(f"{M['unmapped']['id']} card: no CAD mapping shown (META.cad null)", not page.is_visible("#bShow"))
        page.screenshot(path=str(shots / "desktop_3_board_unmapped.png"))

    # ---- 3D click on the mapped board's part -> board card ----
    cp = M["cad_part"]
    if cp:
        page.click("#bX")
        # the board may sit inside a cabinet: isolate the assembly that holds it, look from the front, frame it
        page.evaluate(f"""(() => {{ const v = {v}, id = {json.dumps(cp)}; v.showAll(); const o = v.nodes.find((x) => x.isMesh && x.userData.part === id);
            if (o && o.parent && o.parent.userData.n != null) v.isolate(o.parent.userData.n); v.setCam({{yaw: 2.5}}); v.select(id, true); }})()""")
        page.wait_for_function(f"!{v}.state().flying", timeout=30000)
        for _ in range(3):   # whatever else is picked at the screen centre covers the board: hide it until the board is reachable
            c = page.evaluate("(() => { const p = window.__viewer.pick(innerWidth / 2, innerHeight / 2); return p && p.part; })()")
            if not c or c == cp: break
            page.evaluate(f"{v}.select({v}.pick(innerWidth / 2, innerHeight / 2).n)"); page.click("#infoHide"); page.wait_for_timeout(150)
        page.evaluate(f"{v}.select(null)"); page.wait_for_timeout(200)
        hit = page.evaluate("""(id) => { const v = window.__viewer;
          for (let y = 20; y < innerHeight - 20; y += 8) for (let x = 20; x < innerWidth - 20; x += 8) { const r = v.pick(x, y); if (r && r.part === id) return [x, y]; }
          return null; }""", cp)
        if hit:
            page.mouse.click(*hit); page.wait_for_timeout(500)
            check(f"3D click on {cp} opens the card of its board", page.is_visible("#bcard") and page.inner_text("#bT") == main["name"] and not page.is_visible("#info"), f"click {hit}")
            page.screenshot(path=str(shots / "desktop_4_click3d.png"))
            page.click("#bX"); page.wait_for_timeout(100)
            page.click(f'.brd[data-b="{main["id"]}"]'); page.evaluate(f"{v}.select(null)")
            page.click("#bShow"); page.wait_for_timeout(900)
            sel = page.evaluate(f"{v}.state().selected && {v}.state().selected.part")
            check(f"«{T['sim']['show3d']}» selects + frames a part of the board", sel in {c["part"] for c in main.get("cad") or []}, str(sel))
        else:
            check(f"3D click on {cp} opens the card of its board", False, "part not pickable on screen")
    else:
        print("[info] no board is mapped to a CAD part: the 3D click check skipped", flush=True)

    # ---- firmware switch (a board whose MCU has more than one build in fw/index.json) ----
    sw = M["switch"]
    if sw:
        names = [f["name"] for f in M["fw"] if f["mcu"] == sw["mcu"]]
        own = page.evaluate(f"{v}.sim.state().boards.find(b => b.id === {json.dumps(sw['id'])}).fw")
        alt = next(n for n in names if n != own)
        page.click("#bX"); page.click(f'.brd[data-b="{sw["id"]}"]'); page.wait_for_timeout(300)
        opts = page.eval_on_selector_all("#bFw option", "o => o.map(x => x.value)")
        check(f"firmware selector of {sw['id']} lists the builds for its MCU (fw/index.json)", set(opts) == set(names), f"{opts} vs {names}")
        r0 = page.evaluate(f"{v}.sim.state().resets")
        page.select_option("#bFw", alt)
        page.wait_for_function(f"{v}.sim.state().resets > {r0}", timeout=30000); page.wait_for_timeout(800)
        st = page.evaluate(f"{v}.sim.state()"); fwb = {b['id']: b['fw'] for b in st["boards"]}
        check("firmware switch reloads the board and resets the sim", fwb[sw["id"]] == alt and st["t"] < 3 and not st["error"], f"fw={fwb} clock={st['clock']}")
        page.select_option("#bFw", own); page.wait_for_function(f"{v}.sim.state().resets > {r0 + 1}", timeout=30000)
        check("firmware switched back", page.evaluate(f"{v}.sim.state().boards.find(b => b.id === {json.dumps(sw['id'])}).fw") == own)
    else:
        print("[info] no MCU has two firmware builds: the firmware switch check skipped", flush=True)

    # ---- max speed: a full machine cycle (when the machine counts them in its status: `discharges`) ----
    page.click("#bX"); page.click("#vmReset"); page.wait_for_timeout(700)
    if MOTION:
        md = motion_diff(page)
        check("motion: Reset (#vmReset) puts every moved node back to its home matrix", md is not None and max(md.values()) < 1e-6, f"max deviation {max(md.values()) if md else None:.2e}")
    page.evaluate(f"{v}.sim.setSpeed('max')"); page.evaluate(FRAMES_JS)
    page.evaluate(f"{v}.sim.start(500)")
    w0 = page.evaluate("performance.now()")
    counts = "discharges" in page.evaluate(f"{v}.sim.state().status")
    if counts:
        try:
            page.wait_for_function(f"{v}.sim.state().status.discharges > 0", timeout=45000, polling=200)
            got = True
        except Exception:  # noqa
            got = False
    else:
        page.wait_for_timeout(5000); got = True
    w1 = page.evaluate("performance.now()"); st = page.evaluate(f"{v}.sim.state()"); fr = frames(page)
    wall = (w1 - w0) / 1000
    info["max"] = {"wall": wall, "machine_s": st["t"], "achieved": st["achieved"], **fr}
    if counts:
        check("max: a full machine cycle (status.discharges > 0) within ~30 s wall", got and wall <= 30,
              f"{wall:.1f} s wall for {st['t']:.1f} machine s (avg x{st['t'] / wall:.0f}, now x{st['achieved']:.0f}), status {st['status'].get('code')}")
    else:
        check("max: machine time runs well ahead of real time", st["t"] / wall >= 2, f"{st['t']:.1f} machine s in {wall:.1f} s wall")
    check("max: frame time <= 33 ms (p99) while running flat out", fr["p99"] <= 33.5, f"mean {fr['mean']:.1f} p99 {fr['p99']:.1f} max {fr['max']:.1f} ms over {fr['n']} frames")
    if MOTION:
        md = motion_diff(page)
        away = sum(1 for d in (md or {}).values() if d > 1e-4)
        check("motion: at max speed the moved nodes follow (frame budget above includes them)", md is not None and away >= max(1, len(md) // 2), f"{away} of {len(md or {})} actuators away from home")
    page.screenshot(path=str(shots / "desktop_5_max.png"))
    # ---- pause / speed buttons / lag readout ----
    page.evaluate(f"{v}.sim.setSpeed(1)"); page.click("#vmPause"); c = page.evaluate(f"{v}.sim.state().t"); page.wait_for_timeout(500)
    check(f"pause freezes the clock, the button says «{T['sim']['resume']}»", page.evaluate(f"{v}.sim.state().t") == c and page.inner_text("#vmPause") == T["sim"]["resume"])
    page.click("#vmPause")
    page.click('[data-sp="0.25"]'); c0 = page.evaluate(f"{v}.sim.state().t"); page.wait_for_timeout(2000); c1 = page.evaluate(f"{v}.sim.state().t")
    check("x0.25 button: quarter speed", 0.15 <= (c1 - c0) / 2 <= 0.35, f"x{(c1 - c0) / 2:.2f}")
    check("step(ms) advances deterministically", abs(page.evaluate(f"(() => {{ const a = {v}.sim.state().t; {v}.sim.step(500); return {v}.sim.state().t - a; }})()") - 0.5) < 0.01)
    # ---- can't keep up: throttle the CPU 20x at x20 -> the page must say so with the real achieved speed ----
    cdp = page.context.new_cdp_session(page)
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": 20})
    page.click('[data-sp="20"]'); page.wait_for_timeout(3500)
    lag = page.inner_text("#vmAch"); st = page.evaluate(f"{v}.sim.state()")
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": 1})
    check(f"x20 on a 20x throttled CPU: shows real speed + «{LAG_TAIL}»", st["lagging"] and LAG_TAIL in lag and st["achieved"] < 18, f"'{lag}' (achieved {st['achieved']:.1f})")
    check("desktop: console has no errors", not errs, "; ".join(errs[:3]))
    b.close()


def run_phone(p):
    b, page, errs = open_page(p, {"width": 390, "height": 844}, True)
    v = "window.__viewer"
    page.wait_for_timeout(1500)
    page.screenshot(path=str(shots / "phone_1_default.png"))
    st = page.evaluate(f"{v}.state()")
    check("phone: panel collapsed by default (3D not buried)", st["panelCollapsed"])
    page.click("#pHead"); page.wait_for_timeout(300)
    check(f"phone: «{T['sim']['machine']}» section (#vmSec) collapsed inside the open panel", page.evaluate("document.getElementById('vmSec').classList.contains('collapsed')"))
    page.screenshot(path=str(shots / "phone_2_panel_open.png"))
    page.click("#vmHead"); page.wait_for_timeout(300)
    page.evaluate(f"{v}.sim.start(500)"); page.wait_for_timeout(2500)
    page.screenshot(path=str(shots / "phone_3_machine_open.png"))
    check("phone: simulation bar visible and inside the screen", page.evaluate("(() => { const r = document.getElementById('simbar').getBoundingClientRect(); return r.width > 0 && r.left >= 0 && r.right <= innerWidth + 1 && r.bottom <= innerHeight; })()"))
    page.tap(f'.brd[data-b="{M["main"]["id"]}"]'); page.wait_for_timeout(700)
    page.screenshot(path=str(shots / "phone_4_board_card.png"))
    box = page.evaluate("(() => { const r = document.getElementById('bcard').getBoundingClientRect(); return [r.top, r.height, innerHeight]; })()")
    check("phone: board card is a bottom sheet <= 60% of the screen", box[1] <= box[2] * 0.6, f"top {box[0]:.0f}, height {box[1]:.0f} of {box[2]}")
    check("phone: console has no errors", not errs, "; ".join(errs[:3]))
    b.close()


def run_app(p):
    """App panel + BLE bridge, generic: needs a site built with site.json "app". The app's own UI is driven by the machine repo's own e2e script."""
    b, page, errs = open_page(p, {"width": 1440, "height": 900}, False, app=True)
    v = "window.__viewer"
    try: page.wait_for_function(f"{v}.sim.app || {v}.appError", timeout=30000)
    except Exception: pass   # no app in this site
    if not page.evaluate(f"!!{v}.sim.app"):
        print("no app in this site: skipped"); b.close(); return
    check("app: panel docked, iframe created (desktop default = open)", page.is_visible("#appPanel") and page.is_visible("#appFrame"))
    try:
        page.wait_for_function(f"{v}.sim.app.state().connected", timeout=120000); ok = True
    except Exception:  # noqa
        ok = False
    st = page.evaluate(f"{v}.sim.app.state()"); log = page.evaluate(f"{v}.sim.app.log")
    fwname = page.evaluate(f"{v}.sim.vm.m.sim.board({v}.sim.app.state().device && {v}.sim.app.state().device.id.replace('sim-', '')).ble.name")
    check("app: hello answered with the device advertised by the FIRMWARE, then auto-connected", ok and st["device"]["name"] == fwname, json.dumps(st, ensure_ascii=False))
    page.wait_for_timeout(3000)
    log = page.evaluate(f"{v}.sim.app.log")
    kinds = [(m["dir"], m["msg"].get("type")) for m in log if isinstance(m["msg"], dict)]
    check("app: writes reach the board and its TX notifications come back", ("app->page", "write") in kinds and ("page->app", "notify") in kinds, str(kinds[:12]))
    check("app: panel does not cover the 3D toolbar/cards (--app-w set, board card left of the app)", page.evaluate("parseInt(getComputedStyle(document.documentElement).getPropertyValue('--app-w')) > 300"))
    page.click(f'.brd[data-b="{M["main"]["id"]}"]'); page.wait_for_timeout(500)
    r = page.evaluate("(() => { const c = document.getElementById('bcard').getBoundingClientRect(), a = document.getElementById('appPanel').getBoundingClientRect(); return [c.right, a.left]; })()")
    check("app: board card opens over the 3D, not over the app", r[0] <= r[1] + 1, f"card right {r[0]:.0f} <= app left {r[1]:.0f}")
    page.screenshot(path=str(shots / "desktop_6_app_panel.png"))
    b.close()


try:
    with sync_playwright() as p:
        run_desktop(p)
        if M:
            run_phone(p)
            run_app(p)
finally:
    if srv:
        srv.shutdown()
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
print("\n" + json.dumps(info, ensure_ascii=False, indent=1))
print("\n| check | result | detail |\n|---|---|---|")
for r in rows:
    print("| " + " | ".join(x.replace("|", "/") for x in r) + " |")
sys.exit(0 if ok_all else 1)
