// The machine layer of the assembly viewer: the virtual controllers (real firmware as wasm) paced to the wall clock, plus the UI around them:
// a thin simulation bar (clock, speed, pause, reset, the machine's physical inputs), the board list + board cards, and (site.json "app") the machine's own app panel.
// The machine's operator UI is the machine's app, not something invented here.
// Loaded by index.html ONLY when sim/config.json exists (build.py --sim). Everything machine-specific comes from sim/<entry> (META + buildMachine),
// or, for a device derived from the model (config `device`), from sim/device.json through the framework's device runtime + device_ui.mjs.
//
//   window.__viewer.sim = { ready, state(), actuators(), start(), stop(), setSpeed(x), pause(), resume(), reset(), step(ms), setTarget(g), press(inputId),
//                           setFirmware(board, name), openBoard(id), app, vm }   (start/stop/setTarget: machine-level scripting, no UI)
// actuators() -> { <id>: { kind: 'stepper'|'binary', board, label, steps, pos, vel, on } } — read it every frame to animate the CAD.
import { VM, SPEEDS, browserFirmwareLoader } from "./vc/vm.mjs";
import * as vcIndex from "./vc/index.mjs";

const CSS = `
#vmAch { font-size: 12px; color: var(--muted); white-space: nowrap; } #vmAch.lag { color: #b42318; font-weight: 700; }
#simbar { position: fixed; z-index: 4; left: 392px; right: calc(16px + var(--app-w, 0px)); bottom: 14px; display: flex; justify-content: center; pointer-events: none; }
#simbar .bar { pointer-events: auto; display: flex; align-items: center; gap: 4px; flex-wrap: wrap; justify-content: center; padding: 5px 10px; background: var(--panel); backdrop-filter: blur(6px); border: 1px solid var(--line); border-radius: 22px; }
#simbar button { padding: 3px 7px; border-radius: 14px; font-size: 12px; }
#simbar .sep { width: 1px; align-self: stretch; background: var(--line); margin: 2px 2px; }
#vmClock { font: 700 16px/1 ui-monospace, Menlo, monospace; font-variant-numeric: tabular-nums; min-width: 60px; }
#simbar .hw { display: inline-flex; align-items: center; gap: 6px; } #simbar .hw i { width: 10px; height: 10px; border-radius: 50%; background: #9aa5ae; border: 1px solid #0003; } #simbar .hw.down i { background: #2f9e44; }
#vmSec { flex: 0 1 auto; min-height: 31px; }
#vmSec .sechead .t { flex: 1; }
.brd { display: flex; align-items: center; gap: 8px; padding: 4px 8px 4px 9px; border: 1px solid var(--line); border-left: 3px solid var(--idle); border-radius: 6px; margin: 3px 0; cursor: pointer; background: #fff; flex-wrap: wrap; }
.brd:hover { border-color: #9fb0bd; } .brd .n { flex: 1; min-width: 0; } .brd .n small { display: block; color: var(--muted); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.brd .bs { font-size: 12px; color: var(--muted); }
.brd[data-s="run"] { border-left-color: var(--ok); } .brd[data-s="run"] .bs { color: var(--ok); }
.brd[data-s="crash"] { border-left-color: var(--err); } .brd[data-s="crash"] .bs { color: var(--err); font-weight: 600; }
.brd[data-s="paused"] { border-left-color: var(--warn); } .brd[data-s="paused"] .bs { color: var(--warn); }
.brd { display: grid; grid-template-columns: 1fr auto; column-gap: 8px; } .brd .brdFl { grid-column: 1 / -1; justify-self: start; margin-top: 4px; } .brd .brdFl[hidden] { display: none; } .brd .brdFl { padding: 2px 8px; font-size: 12px; color: var(--acc); border-color: var(--acc); background: #fff; } .brd .brdFl:hover:not(:disabled) { background: var(--acc); color: #fff; }
#bcard { top: 16px; right: 16px; width: 380px; max-width: calc(100vw - 32px); max-height: calc(100vh - 32px); z-index: 6; }
#bcard .body { display: flex; flex-direction: column; } #bcard.collapsed .body { display: none; }
#bcard select { font: inherit; padding: 4px 6px; border: 1px solid var(--line); border-radius: 6px; max-width: 100%; }
#bcard dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; margin: 6px 0; font-size: 12px; } #bcard dt { color: var(--muted); } #bcard dd { margin: 0; word-break: break-word; }
#bcard h4 { margin: 8px 0 3px; font-size: 12px; color: var(--muted); font-weight: 600; display: flex; gap: 8px; align-items: center; } #bcard h4 label { font-weight: 400; font-size: 11px; }
.pins { max-height: 150px; overflow: auto; border: 1px solid var(--line); border-radius: 6px; background: #fff; }
.pins table { width: 100%; border-collapse: collapse; font-size: 11px; } .pins td { padding: 1px 6px; border-bottom: 1px solid #0001; } .pins td:first-child { white-space: nowrap; font-family: ui-monospace, Menlo, monospace; }
.lv { display: inline-block; width: 8px; height: 8px; border-radius: 2px; border: 1px solid #0004; vertical-align: -1px; } .lv.h { background: #2f9e44; } .lv.l { background: #fff; }
.log { margin: 0; font: 11px/1.35 ui-monospace, Menlo, monospace; background: #14181c; color: #d7e0e8; padding: 5px 7px; border-radius: 6px; height: 150px; overflow: auto; white-space: pre-wrap; word-break: break-all; }
.log.short { height: 90px; }
@media (max-width: 700px) { #simbar { left: 8px; right: 8px; bottom: calc(8px + var(--app-h, 0px)); } #simbar .bar { border-radius: 16px; padding: 4px 8px; }
  #bcard { top: auto; bottom: 8px; left: 8px; right: 8px; width: auto; max-width: none; max-height: 55vh; padding: 6px 10px calc(8px + env(safe-area-inset-bottom)); }
  .log { height: 110px; } .pins { max-height: 110px; } }
`;

const el = (tag, attrs = {}, html) => { const e = document.createElement(tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (html != null) e.innerHTML = html; return e; };

export async function mount(ctx) {
  const { api, L, $, fmt, esc, BYPART, narrow, store } = ctx, S = L.sim;
  const cfg = ctx.cfg, base = new URL("./", location.href);
  const simUrl = (f) => new URL(api.dataUrl ? api.dataUrl(`sim/${f}`) : `sim/${f}`, base).href;
  let machineModule;
  if (cfg.device) {   // a device from the model: no machine.mjs, the runtime builds it from device.json
    const device = await fetch(simUrl(cfg.device)).then((r) => { if (!r.ok) throw new Error(`sim/${cfg.device} ${r.status}`); return r.json(); });
    machineModule = { META: vcIndex.deviceMeta(device), buildMachine: (o) => vcIndex.buildDevice({ ...o, device }) };
  } else machineModule = await import(simUrl(cfg.entry));
  const meta = machineModule.META;
  const fwIndex = await fetch(new URL("fw/index.json", base)).then((r) => (r.ok ? r.json() : [])).then((j) => (Array.isArray(j) ? j : (j && j.sim) || [])).catch(() => []);   // fw/index.json: the simulator firmware list, or {sim, boards} once real-board firmware is published next to it (fw_publish.py)
  const vm = new VM({ vc: vcIndex, machineModule, loadFirmware: browserFirmwareLoader(new URL("fw/", base), ".js") });
  await vm.init();

  document.head.appendChild(el("style", {}, CSS));
  // ---- boards section in the panel (list only; the machine itself is operated in the app) ----
  const sec = el("section", { id: "vmSec" }, `
    <div class="sechead" id="vmHead"><span class="t">${esc(S.machine)}</span></div>
    <div class="secbody"><div id="vmBoards"></div></div>`);
  $("secHw").before(sec); sec.classList.add("sec"); sec.querySelector(".sechead").classList.add("sh");   // a section of the panel like the others (index.html)
  const svKey = "sec." + (location.pathname.split("/")[1] || "local") + ".sim", sv = store(svKey);   // per project
  sec.classList.toggle("collapsed", sv === null || sv === undefined ? narrow() : sv === "1");
  $("vmHead").addEventListener("click", () => { const c = !sec.classList.contains("collapsed"); sec.classList.toggle("collapsed", c); store(svKey, c ? "1" : "0"); });

  // ---- simulation bar: clock, speed, pause, reset, the machine's physical inputs (META.inputs) ----
  const inputs = meta.inputs || [];
  const barInputs = meta.device ? [] : inputs;   // a device's buttons are in its own window ("Device")
  const bar = el("div", { id: "simbar" }, `<div class="bar">
    <span id="vmClock" title="${esc(S.clockTip)}"></span><span id="vmAch"></span><span class="sep"></span>
    <span id="vmSpeeds" class="row" style="margin:0;gap:4px"></span><span class="sep"></span>
    <button id="vmPause"></button><button id="vmReset"></button>
    ${barInputs.length ? `<span class="sep"></span>${barInputs.map((i) => `<button class="hw" data-in="${esc(i.id)}" title="${esc(i.hint || "")}"><i></i>${esc(i.label)}</button>`).join("")}` : ""}
  </div>`);
  document.body.appendChild(bar);
  $("vmReset").textContent = S.reset;
  $("vmSpeeds").innerHTML = SPEEDS.map((x) => `<button data-sp="${x}">${x === "max" ? esc(S.max) : "×" + String(x).replace(".", L.decimal)}</button>`).join("");
  $("vmSpeeds").addEventListener("click", (e) => { const b = e.target.closest("[data-sp]"); if (b) setSpeed(b.dataset.sp === "max" ? "max" : +b.dataset.sp); });
  const setSpeed = (x) => { vm.setSpeed(x); paint(true); };
  // scripted machine-level start (no UI): BLE connect, weight, then the start button — see machine.start()
  const start = (g) => { vm.resume(); vm.m.start(typeof g === "number" ? { target: g } : {}); paint(true); };
  const stop = () => { vm.m.stop(); };
  $("vmPause").addEventListener("click", () => { vm.paused ? vm.resume() : vm.pause(); paint(true); });
  $("vmReset").addEventListener("click", () => reset());
  const reset = async () => { await vm.reset(); paint(true); renderBoards(); };
  // physical input (button): the pin is driven for HOLD_US of machine time, like a finger on the button; a paused sim resumes so the press is not lost
  const HOLD_US = 300000;
  const press = (id) => {
    if (meta.device) { if (!vm.m || !sim.device) return false; sim.device.press(id, HOLD_US / 1000); return true; }
    const inp = inputs.find((i) => i.id === id); if (!inp || !vm.m) return false;
    const b = vm.m.sim.board(inp.board), act = inp.active ? 1 : 0;
    vm.resume(); b.drive(inp.pin, act); vm.m.sim.at(vm.m.sim.t + HOLD_US, () => b.drive(inp.pin, 1 - act));
    const btn = bar.querySelector(`[data-in="${id}"]`); if (btn) { btn.classList.add("down"); setTimeout(() => btn.classList.remove("down"), 300 / Math.min(1, vm.speed === "max" ? 1 : vm.speed)); }
    return true;
  };
  bar.addEventListener("click", (e) => { const b = e.target.closest("[data-in]"); if (b) press(b.dataset.in); });

  // ---- boards list ----
  const boardStatus = (id) => { const b = vm.m && vm.m.sim.boards.get(id); return vm.paused ? "paused" : !b ? "idle" : b.dead ? "crash" : b.yields > 0 ? "run" : "idle"; };
  const boardStatusText = (id) => S.bstatus[boardStatus(id)];
  const renderBoards = () => {
    $("vmBoards").innerHTML = meta.boards.map((b) => `<div class="brd" data-b="${b.id}" data-s="${boardStatus(b.id)}"><div class="n">${esc(b.name)}<small>${esc(b.mcu.toUpperCase())} · ${esc(vm.fwName(b.id))}</small></div><span class="bs" data-bs="${b.id}">${esc(boardStatusText(b.id))}</span><button class="brdFl" data-flash="${b.id}" hidden>${esc(S.flashReal)}</button></div>`).join("");
    hwSync();
  };
  // the real board behind a sim board (hw.mjs, --flash): the row carries data-hw="on|off|busy" and a one-click flash of the card's published firmware
  const hwState = (id) => { const h = api.hw && api.hw.cardState(id); return h ? { connected: h.connected, published: h.published, busy: h.busy, version: h.version, monitoring: h.monitoring, canFlash: h.connected && h.published && !h.busy } : null; };
  const flashReal = (id) => { if (!api.hw) return Promise.reject(new Error("hw")); api.hw.fail(id, null); return api.hw.flash(id).catch((err) => { api.hw.fail(id, err); throw err; }); };
  function hwSync() {
    for (const r of $("vmBoards").querySelectorAll(".brd")) {
      const h = hwState(r.dataset.b), btn = r.querySelector("[data-flash]");
      if (h) r.dataset.hw = h.busy ? "busy" : h.connected ? "on" : "off"; else delete r.dataset.hw;
      if (btn) { btn.hidden = !(h && h.connected && h.published); btn.disabled = !h || !h.canFlash; }
    }
  }
  const hwHook = () => { hwSync(); if (api.hw && api.hw.subscribe) api.hw.subscribe(hwSync); };
  if (api.hw) hwHook(); else document.addEventListener("hw-ready", hwHook, { once: true });
  $("vmBoards").addEventListener("click", (e) => {
    const f = e.target.closest("[data-flash]"); if (f) { flashReal(f.dataset.flash).catch(() => {}); return; }
    const r = e.target.closest("[data-b]"); if (r) openBoard(r.dataset.b);
  });

  // ---- board card ----
  const card = el("div", { id: "bcard", class: "card", hidden: "" }, `
    <div class="head" id="bHead"><div class="ttl" id="bT"></div><button class="icon fold" id="bFold" title="${esc(L.fold)}"></button><button class="icon" id="bX" title="${esc(L.close)}">×</button></div>
    <div class="body">
      <dl id="bDl"></dl>
      <div class="row"><button id="bShow"></button></div>
      <div id="bHw" hidden></div>
      <h4>${esc(S.pins)}</h4><div class="pins"><table id="bPins"></table></div>
      <h4>Serial <label><input type="checkbox" id="bHideNum" checked> ${esc(S.hideNums)}</label></h4><pre class="log" id="bSerial"></pre>
      <h4>${esc(S.i2c)}</h4><pre class="log short" id="bI2c"></pre>
    </div>`);
  document.body.appendChild(card);
  if (window.__win) window.__win.register(card, { id: "simboard", head: $("bHead"), cls: "collapsed" });   // win.mjs: drag, fold to the bottom row
  let cur = null, lastPins = "";
  const fwOptions = (b) => fwIndex.filter((f) => f.mcu === b.mcu);
  function openBoard(id) {
    const b = meta.boards.find((x) => x.id === id); if (!b) return;
    cur = id; card.hidden = false; $("info").hidden = true;
    if (narrow()) { card.classList.remove("collapsed"); $("panel").classList.add("collapsed"); if (api.sim && api.sim.app) api.sim.app.close(); }   // both are bottom sheets: the card (board Serial) goes over the 3D, the app folds   // phone: the card is a bottom sheet, the panel would sit behind it
    $("bT").textContent = b.name;
    const sel = fwOptions(b).map((f) => `<option value="${esc(f.name)}"${f.name === vm.fwName(id) ? " selected" : ""}>${esc(f.name)} — ${esc(f.source)} · ${esc(String(f.built || "").slice(0, 10))}</option>`).join("");
    const cad = b.cad && b.cad.length;
    $("bDl").innerHTML = `<dt>${esc(S.mcu)}</dt><dd>${esc(b.mcu === "esp32" ? "ESP32" : "ATmega328P (Nano)")}</dd>`
      + `<dt>${esc(S.fw)}</dt><dd><select id="bFw">${sel}</select><div class="sub" style="margin:2px 0 0">${esc(S.fwNote)}</div></dd>`
      + `<dt>${esc(S.status)}</dt><dd id="bStat"></dd><dt>${esc(S.role)}</dt><dd>${esc(b.about || "—")}</dd>`
      + `<dt>${esc(S.cad)}</dt><dd>${cad ? b.cad.map((c) => esc(c.part)).join(", ") : esc(S.noCad)}</dd>`;
    $("bShow").hidden = !cad; $("bShow").textContent = S.show3d;
    $("bFw").addEventListener("change", async (e) => { $("bStat").textContent = S.reloading; await vm.setFirmware(id, e.target.value); paint(true); renderBoards(); openBoard(id); });
    lastPins = ""; $("bSerial").textContent = ""; serialShown = -1; refreshCard(true);
    hwCard();
  }
  // real board: status + Flash / Monitor / Reset (hw.mjs, only when the site was built with --flash)
  const hwCard = () => { const h = $("bHw"); if (!h || !cur) return; if (api.hw) { h.hidden = false; api.hw.mountCard(h, cur); } else h.hidden = true; };
  document.addEventListener("hw-ready", hwCard);
  const closeBoard = () => { card.hidden = true; cur = null; };
  $("bX").addEventListener("click", (e) => { e.stopPropagation(); closeBoard(); });
  $("bHead").addEventListener("click", (e) => { if (e.target.id !== "bX") card.classList.toggle("collapsed"); });
  $("bShow").addEventListener("click", () => {
    const b = meta.boards.find((x) => x.id === cur); if (!b || !b.cad) return;
    for (const c of b.cad) if (BYPART[c.part]) { api.select(c.part, true); return; }
  });
  $("bHideNum").addEventListener("change", () => { serialShown = -1; refreshCard(true); });
  let serialShown = -1;
  function refreshCard(force) {
    if (!cur || card.hidden || !vm.m) return;
    $("bStat").textContent = boardStatusText(cur) + (vm.error ? " · " + vm.error : "");
    // pins
    const rows = vm.m.pinTable(cur);
    const sig = rows.map((r) => r.level).join("");
    if (force || sig !== lastPins) {
      lastPins = sig;
      $("bPins").innerHTML = rows.map((r) => `<tr><td>${esc(r.name)}</td><td>${esc(r.label)}</td><td>${r.level == null ? "" : `<i class="lv ${r.level ? "h" : "l"}"></i> ${r.level}`}</td></tr>`).join("");
    }
    // serial (last 200 lines, auto-scroll only if the user is at the bottom)
    const hide = $("bHideNum").checked;
    let lines = vm.logs[cur]; const tot = lines.length + ":" + (lines.length ? lines[lines.length - 1].t : 0);
    if (force || tot !== serialShown) {
      serialShown = tot;
      if (hide) lines = lines.filter((l) => /[^-\d.\s]/.test(l.line));
      const pre = $("bSerial"), stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
      pre.textContent = lines.slice(-200).map((l) => (l.t / 1e6).toFixed(2).padStart(7) + "  " + l.line).join("\n");
      if (stick || force) pre.scrollTop = pre.scrollHeight;
    }
    // i2c
    const i2 = vm.i2c[cur].slice(-40), mean = machineModule.i2cMeaning;
    $("bI2c").textContent = i2.length ? i2.map((x) => `${(x.t / 1e6).toFixed(2).padStart(7)}  ${x.from} → ${x.to || "—(NACK)"} 0x${x.addr.toString(16)}  [${x.bytes.join(" ")}]${mean && x.bytes.length === 1 ? "  " + mean(x.bytes[0]) : ""}`).join("\n") : S.noI2c;
  }

  // ---- paint (10 Hz) ----
  let lastPaint = 0;
  function paint(force) {
    const now = performance.now();
    if (!force && now - lastPaint < 100) return;
    lastPaint = now;
    $("vmClock").textContent = vm.clockText();
    const ach = vm.speed === "max" ? fmt(S.maxAch, { x: num1(vm.achieved) }) : vm.lagging ? fmt(S.lag, { x: num1(vm.achieved) }) : vm.paused ? S.paused : fmt(S.ach, { x: num1(vm.achieved || 0) });
    const odd = vm.error || vm.lagging || vm.paused || vm.speed === "max";   // plain x1 needs no readout: the clock and the highlighted speed say it
    $("vmAch").textContent = vm.error ? S.crashed + " — " + vm.error : odd ? ach : ""; $("vmAch").className = vm.lagging || vm.error ? "lag" : "";
    $("vmPause").textContent = vm.paused ? S.resume : S.pause; $("vmPause").className = vm.paused ? "on" : "";
    $("vmSpeeds").querySelectorAll("button").forEach((b) => b.classList.toggle("on", (b.dataset.sp === "max" ? "max" : +b.dataset.sp) === vm.speed));
    $("vmBoards").querySelectorAll(".brd").forEach((r) => { const id = r.dataset.b, s = boardStatus(id); r.dataset.s = s; r.querySelector("[data-bs]").textContent = S.bstatus[s]; });
    refreshCard(false);
  }
  const num1 = (v) => String(Math.round(v * 10) / 10).replace(".", L.decimal);
  api.tickers.push((now) => { vm.tick(now); paint(false); });

  // ---- 3D click -> board card ----
  const boardOfMesh = (o) => {
    if (!o || !o.isMesh) return null;
    const part = o.userData.part;
    for (const b of meta.boards) for (const c of b.cad || []) if (c.part === part && (c.instance == null || (BYPART[part] || []).indexOf(o) === c.instance)) return b.id;
    if (meta.device) for (const b of meta.boards) if (b.id === part) return b.id;   // a model's viewer names a part by its placement
    return null;
  };
  api.selectHooks.push((o) => { const id = boardOfMesh(o); if (id) openBoard(id); else if (o && cur) closeBoard(); });

  // ---- public API for scripts / the CAD binding ----
  const stepVm = (ms) => vm.step(ms);
  const sim = {
    ready: true, vm, meta,
    state: () => ({
      t: vm.m.sim.t / 1e6, clock: vm.clockText(), speed: vm.speed, achieved: vm.achieved, lagging: vm.lagging, paused: vm.paused, error: vm.error, resets: vm.resets,
      booted: vm.m.status.ready, status: { ...vm.m.status },
      boards: meta.boards.map((b) => ({ id: b.id, name: b.name, mcu: b.mcu, fw: vm.fwName(b.id), status: boardStatus(b.id), cad: b.cad || null })),
    }),
    actuators: () => vm.m.actuators(),
    start, stop, setSpeed, pause: () => { vm.pause(); paint(true); }, resume: () => { vm.resume(); paint(true); },
    reset, step: stepVm, setTarget: (g) => vm.m.setTarget(g), press, inputs: () => inputs,
    setFirmware: async (id, name) => { await vm.setFirmware(id, name); paint(true); renderBoards(); if (cur === id) openBoard(id); },
    openBoard, closeBoard, logs: (id) => vm.logs[id], fwIndex: () => fwIndex,
    hw: hwState, flashReal,   // the real board of a sim board: {connected, published, busy, version, monitoring, canFlash} | null; flash its published firmware
  };
  api.sim = sim;
  renderBoards(); paint(true);
  // the machine's own app (site.json "app"): lazy iframe + BLE bridge to a virtual board, see app_panel.mjs
  if (cfg.app) {
    try { sim.app = (await import(new URL("./app_panel.js", import.meta.url).href)).mountApp({ api, vm, cfg, L, $, narrow, store, base }); }
    catch (e) { console.error("[app]", e); api.appError = String((e && e.stack) || e); }
  }
  // the device's window, its screens on the 3D glass, its buttons on the 3D parts and keys: see device_ui.mjs
  if (meta.device) {
    try { sim.device = (await import(new URL("./device_ui.js", import.meta.url).href)).mountDevice({ api, vm, meta, L, esc, BYPART }); }
    catch (e) { console.error("[device]", e); api.simError = String((e && e.stack) || e); }
  }
  // CAD motion from sim/motion.json (only when the machine ships one): see motion.mjs
  if (cfg.motion) {
    try { sim.motion = await (await import(new URL("./motion.js", import.meta.url).href)).mount({ api, sim, cfg, L }); }
    catch (e) { console.error("[motion]", e); api.motionError = String((e && e.stack) || e); }
  }
  return sim;
}
