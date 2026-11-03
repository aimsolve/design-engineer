// App panel of the assembly viewer: the machine's OWN operator app in an iframe, wired to a virtual board over postMessage.
// Loaded by sim_ui.mjs when sim/config.json has an "app" entry (site.json -> "app"). Generic: nothing here knows the machine.
//
//   site.json: "app": {"path": "app/", "title": "...", "board": "master", "transport": "ble-bridge", "open": "desktop"|"always"|"lazy"}
//   page:      docked right on desktop, bottom sheet on a phone, collapsible; the iframe is created on first open (a Flutter web build is ~40 MB).
//   bridge:    the "ble-bridge" transport (the app's docs/SIM_BRIDGE.md, machine repo electronics/interfaces/ble.md). JSON over postMessage:
//     app -> page  {src:'<tag>-app', type: hello | scan | connect{deviceId} | write{deviceId,text} | disconnect{deviceId}}
//     page -> app  {src:'<tag>-sim', type: devices{devices:[{id,name}]} | connected{deviceId} | notify{deviceId,text} | disconnected{deviceId}}
//     <tag> is the app's own: the page takes it from the app's first message (site.json app.bridge_tag fixes it) and answers under it.
//   `write` -> the named board's BLE RX characteristic (virtual-controllers Board.bleWrite); every TX notification of that board -> `notify`.
//   The device name / characteristics come from the FIRMWARE (Board.ble, reported by the BLE stub), never from this file.
//   config: latency_ms (default 40) = one-way BLE link delay in machine time, see "bridge" below.
//   URL: ?app=0 keeps the panel closed and never loads the app, ?app=1 opens it (also on a phone).
//   API: __viewer.sim.app = {open(), close(), state(), log, iframe()}

const CSS = `
:root { --app-w: 0px; --app-h: 0px; --app-top: 0px; --app-fold-h: 0px; }
#appPanel { position: fixed; z-index: 7; top: 16px; right: 16px; bottom: 16px; width: 420px; max-width: calc(100vw - 32px); display: flex; flex-direction: column; padding: 0; overflow: hidden; }
#appPanel .head { padding: 0 6px 0 12px; border-bottom: 1px solid var(--line); flex: none; }
#appPanel .head .st { font-size: 11px; color: var(--muted); white-space: nowrap; }
#appPanel .st::before { content: "●"; margin-right: 4px; color: #9aa5ae; } #appPanel .st.on::before { color: #2f9e44; }
#appPanel.collapsed { bottom: auto; width: 250px; }
#appPanel .abody { position: relative; flex: 1; min-height: 0; background: #fff; }
#appPanel.collapsed .abody { display: none; }
#appFrame { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; background: #fff; }
#appLoad { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; text-align: center; padding: 20px; background: #fff; color: var(--muted); font-size: 13px; }
#appLoad[hidden] { display: none; }
#appLoad .sp { width: 30px; height: 30px; border: 3px solid var(--line); border-top-color: var(--acc); border-radius: 50%; animation: appspin 0.9s linear infinite; }
@keyframes appspin { to { transform: rotate(360deg); } }
@media (min-width: 701px) { #info, #bcard { right: calc(16px + var(--app-w)) !important; top: calc(16px + var(--app-top)) !important; max-height: calc(100vh - 32px - var(--app-top)) !important; } }   /* folded app = a tab at the top right: cards go below it, open app = cards go left of it */
@media (max-width: 700px) {
  #info, #bcard { bottom: calc(8px + var(--app-fold-h)) !important; }   /* the folded app is a strip at the very bottom: cards sit on top of it; opening a card folds an open app */
  #appPanel { top: auto; left: 8px; right: 8px; bottom: 8px; width: auto; max-width: none; height: 62vh; border-radius: 12px; padding-bottom: env(safe-area-inset-bottom); }
  #appPanel.collapsed { height: auto; width: auto; bottom: 8px; }
}
`;

export function mountApp({ api, vm, cfg, L, $, narrow, store, base }) {
  const A = L.app, app = cfg.app;
  const q = new URLSearchParams(location.search).get("app");
  const style = document.createElement("style"); style.textContent = CSS; document.head.appendChild(style);
  const box = document.createElement("div");
  box.id = "appPanel"; box.className = "card";
  box.innerHTML = `<div class="head" id="appHead"><div class="ttl" id="appTitle"></div><span class="st" id="appSt"></span><button class="icon fold" id="appFold"></button></div>
    <div class="abody"><div id="appLoad"><div class="sp"></div><div id="appLoadTxt"></div></div></div>`;
  document.body.appendChild(box);
  $("appTitle").textContent = app.title || A.title; $("appFold").title = L.fold;

  const log = [], say = (dir, msg) => { log.push({ dir, t: Math.round(performance.now()), msg }); if (log.length > 300) log.shift(); };
  let frame = null, loadedAt = 0, appHello = false, connected = false, userDetached = false, lastResets = vm.resets, sentName = null, timer = 0;

  const setVars = () => {
    const open = !box.classList.contains("collapsed");
    document.documentElement.style.setProperty("--app-fold-h", !open && narrow() ? box.offsetHeight + 8 + "px" : "0px");
    document.documentElement.style.setProperty("--app-top", !open && !narrow() ? box.offsetHeight + 8 + "px" : "0px");
    document.documentElement.style.setProperty("--app-w", open && !narrow() ? box.offsetWidth + 16 + "px" : "0px");
    document.documentElement.style.setProperty("--app-h", narrow() ? box.offsetHeight + 8 + "px" : "0px");
  };
  const status = () => {
    const el = $("appSt");
    el.className = "st" + (connected ? " on" : "");
    el.textContent = connected ? A.connected : appHello ? A.waiting : frame ? A.loading : "";
    $("appLoad").hidden = !!frame && appHello;
    $("appLoadTxt").textContent = !frame ? A.closedText : A.loadingText.replace("{s}", Math.round((performance.now() - loadedAt) / 1000)).replace("{mb}", app.size_mb || "40");
  };

  function ensureFrame() {
    if (frame) return;
    frame = document.createElement("iframe");
    frame.id = "appFrame"; frame.title = app.title || A.title; frame.allow = "clipboard-read; clipboard-write";
    frame.src = new URL(app.path || "app/", base).href;
    box.querySelector(".abody").appendChild(frame);
    loadedAt = performance.now(); appHello = false;
    $("appLoad").hidden = false;
  }
  function setOpen(open) {
    box.classList.toggle("collapsed", !open);
    store(narrow() ? "appM" : "appD", open ? "1" : "0");
    if (open) ensureFrame();
    if (open && narrow()) { try { api.sim.closeBoard(); } catch (e) { /* card not ready */ } }
    setVars(); status();
    api.invalidate && api.invalidate();
  }
  api.selectHooks.push((o) => { if (o && narrow() && !box.classList.contains("collapsed")) setOpen(false); });   // phone: a part card and the app are both bottom sheets
  $("appHead").addEventListener("click", () => setOpen(box.classList.contains("collapsed")));
  window.addEventListener("resize", setVars);

  // ---- bridge ----
  // A BLE link is not instant: one connection interval (7.5-50 ms) each way. The firmware relies on that, e.g. the master answers "4" with STATE:4, waits delay(50)
  // and only then clears its command register; an app that reacts to STATE:4 at once (as this page would) has its next command wiped. So both directions are delayed
  // by app.latency_ms (default 40) of MACHINE time (it freezes with the pause and scales with the speed, like the real radio would).
  const latUs = (app.latency_ms == null ? 40 : app.latency_ms) * 1000;
  const later = (fn) => { const m = vm.m; if (!m || !latUs) return fn(); m.sim.at(m.sim.t + latUs, fn); };
  const board = () => (vm.m ? vm.m.sim.boards.get(app.board) : null);
  const ready = () => { const b = board(); return !!(b && b.ble.name && b.bleRx() && vm.m.ready !== false); };
  const device = () => { const b = board(); return b && b.ble.name ? { id: "sim-" + app.board, name: b.ble.name } : null; };
  let tag = app.bridge_tag || "";   // the app's message prefix; the page speaks only after the app's hello, so it is known by then
  const send = (m) => { if (!frame || !frame.contentWindow) return; const p = { src: (tag || "machine") + "-sim", ...m }; say("page->app", p); frame.contentWindow.postMessage(p, location.origin); };
  const sendDevices = () => { const d = device(); sentName = d && d.name; send({ type: "devices", devices: d ? [d] : [] }); };
  const doConnect = () => {
    const b = board(), d = device(); if (!b || !d || connected) return false;
    b.bleConnect(); connected = true; send({ type: "connected", deviceId: d.id }); status(); return true;
  };
  const doDisconnect = (notify) => {
    const b = board(), d = device();
    if (connected && b) { try { b.bleDisconnect(); } catch (e) { /* mailbox */ } }
    const was = connected; connected = false;
    if (notify && was) send({ type: "disconnected", deviceId: d ? d.id : undefined });
    status();
  };
  window.addEventListener("message", (e) => {
    if (!frame || e.source !== frame.contentWindow) return;
    const m = e.data, t = /^(.+)-app$/.exec((m && typeof m.src === "string" && m.src) || ""); if (!t || (tag && t[1] !== tag)) return;
    tag = t[1];
    say("app->page", m);
    switch (m.type) {
      case "hello": appHello = true; sendDevices(); status(); break;    // the iframe may load before or after us: answer every hello with a fresh list
      case "scan": userDetached = false; sendDevices(); break;
      case "connect": userDetached = false; if (!doConnect()) send({ type: "disconnected", deviceId: m.deviceId }); break;
      case "disconnect": userDetached = true; doDisconnect(true); break;
      case "write": {
        const b = board();
        if (!connected || !b) { say("page", "write dropped: not connected"); break; }
        const text = String(m.text);
        later(() => { try { if (connected && board() === b) b.bleWrite(b.bleRx(), text); } catch (err) { say("page", "write failed: " + err.message); } });
        break;
      }
    }
  });
  vm.onBle((e) => { if (e.board === app.board && connected) { const d = device(); later(() => send({ type: "notify", deviceId: d ? d.id : undefined, text: e.text })); } });

  // reset / firmware switch = the machine powers down and up again: the link drops, the new firmware advertises again, we reconnect once it is up
  timer = setInterval(() => {
    if (vm.resets !== lastResets) { lastResets = vm.resets; const was = connected; connected = false; sentName = null; if (was) send({ type: "disconnected" }); userDetached = false; status(); }
    status();
    if (!appHello) return;
    const d = device();
    if (d && d.name !== sentName) sendDevices();
    if (!connected && !userDetached && ready()) doConnect();     // friendlier than making the operator pick the device every time
  }, 200);

  const openSaved = store(narrow() ? "appM" : "appD");
  const policy = app.open || "desktop";
  let open = policy === "always" || (policy === "desktop" && !narrow());
  if (openSaved !== null && openSaved !== undefined) open = openSaved === "1";
  if (q === "0") open = false; else if (q === "1") open = true;
  box.classList.toggle("collapsed", !open);
  if (open) ensureFrame();
  setVars(); status();
  if (window.__win) window.__win.register(box, { id: "app", head: $("appHead"), cls: "collapsed", keepHeight: true });   // win.mjs: drag, fold to the bottom row

  return {
    open: () => setOpen(true), close: () => setOpen(false), log, iframe: () => frame,
    state: () => ({ open: !box.classList.contains("collapsed"), loaded: !!frame, hello: appHello, connected, device: device(), userDetached }),
  };
}
