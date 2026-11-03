// Board flashing in the viewer: USB matching, the board-card section, the router client. Loaded by index.html only when config.json has `flash` (build.py --flash).
// Contract: work_sessions/DesignEngineer/2026-10-20/flashing/CONTRACT.md
//
//   Pure (unit-tested, hw.test.mjs): usbClass, matchCards, assignmentKey, parseIndex, makeDispatcher; createHwCore (ports + sessions + operations, no DOM).
//   Runtime: mountHw({api, L, cfg}) -> api.hw = { supported, published, ports(), cardState(card), subscribe(fn), connect(), flash(card), monitor(card), reset(card), fail(card, err), mountCard(el, card) }
//     - navigator.serial.getPorts() on load + connect/disconnect events; "Connect a board" (L.hw.connect) = requestPort() (a user gesture)
//     - a port is matched to a card by USB class (one candidate = automatic, several = a select), remembered in localStorage
//     - the router: POST /hw/<pid>/event (ports on change + every 20 s, log lines batched ~1 s, command results), long-poll GET /hw/<pid>/cmd while a board is connected
//     - firmware: fw/index.json + fw/<card>/manifest.json relative to the page; the same functions serve the buttons and the router's commands
import { sessionFor, webSerialSupported, friendlyError, chipMatches, espBootloaderOffset, setFlashTexts, isBusy } from "./flash.mjs";
import { DICT } from "./i18n.mjs";
export { chipMatches, espBootloaderOffset };

// ---------------------------------------------------------------- pure
const hex4 = (n) => {
  if (n == null || n === "") return null;
  const v = typeof n === "number" ? n : parseInt(String(n), 16);
  return Number.isFinite(v) && v >= 0 ? v.toString(16).padStart(4, "0") : null;
};

export function usbClass(vid, pid) {
  const v = hex4(vid), p = hex4(pid);
  if (!v || !p) return null;
  if (v === "303a") return "esp-usb";
  if (v === "2341" || v === "2a03") return "arduino";
  if (v === "2e8a") return "rp2";
  const uart = ["10c4:ea60", "1a86:7523", "1a86:55d4", "0403:6001", "0403:6015"];
  return uart.includes(v + ":" + p) ? "usb-uart" : null;
}

export function matchCards(port, boards) {
  const cls = usbClass(port && port.vid, port && port.pid);
  const candidates = cls ? (boards || []).filter((b) => (b.usb || []).includes(cls)).map((b) => b.card) : [];
  return { status: candidates.length === 1 ? "assigned" : candidates.length ? "ambiguous" : "unassigned", card: candidates.length === 1 ? candidates[0] : null, candidates };
}

export function assignmentKey({ vid, pid, serial }) {
  return (hex4(vid) || "0000") + ":" + (hex4(pid) || "0000") + (serial ? ":" + serial : "");
}

export function parseIndex(json) {
  let j = json;
  if (typeof j === "string") { try { j = JSON.parse(j); } catch { return []; } }
  if (!j || typeof j !== "object" || !Array.isArray(j.boards)) return [];
  const out = [];
  for (const b of j.boards) {
    if (!b || typeof b.card !== "string" || !b.card || typeof b.chip !== "string" || !b.chip) continue;
    out.push({
      card: b.card, chip: b.chip, usb: Array.isArray(b.usb) ? b.usb.filter((x) => typeof x === "string") : [],
      manifest: typeof b.manifest === "string" && b.manifest ? b.manifest : b.card + "/manifest.json",
      ...(b.version != null ? { version: b.version } : {}), ...(b.built != null ? { built: b.built } : {}),
    });
  }
  return out;
}

// One entry per granted port (none dropped, unknown VID:PID too). assigned = remembered choices {key: card}.
export function portEntries(ports, boards, assigned = {}) {
  return (ports || []).map((p) => {
    const vid = hex4(p.vid), pid = hex4(p.pid), serial = p.serial || null, key = assignmentKey({ vid, pid, serial });
    const m = matchCards({ vid, pid }, boards || []);
    const card = assigned[key] && m.candidates.includes(assigned[key]) ? assigned[key] : m.status === "assigned" ? m.card : null;
    return { key, class: usbClass(vid, pid), vid: vid || "0000", pid: pid || "0000", serial, card };
  });
}
const CLASS_NAME = { "esp-usb": DICT.ru.hw.espUsb, "usb-uart": "USB-UART", arduino: "Arduino", rp2: "Raspberry Pi RP2" };
export const portLabel = (e) => `USB ${e.vid}:${e.pid} (${CLASS_NAME[e.class] || T.unknownDev}) — ${T.notInModel}`;

// fns: {flash, reset, monitor, identify}. cmd = {id, op, card?, port?, fw?}. Card-only: fn(card). With port/fw: fn(card|null, {port, fw}), fw defaulting to card.
// Each fn may return {message, chip} (or a string / nothing) or throw.
export function makeDispatcher(fns) {
  return async (cmd) => {
    const fn = cmd && fns[cmd.op];
    if (!cmd || !["flash", "reset", "monitor", "identify"].includes(cmd.op) || typeof fn !== "function") return { ok: false, message: `unknown command: ${cmd && cmd.op}` };
    if (!cmd.card && !cmd.port) return { ok: false, message: `command ${cmd.op} names no board` };
    try {
      const r = cmd.port || cmd.fw ? await fn(cmd.card || null, { port: cmd.port, fw: cmd.fw || cmd.card }) : await fn(cmd.card);
      const o = typeof r === "string" ? { message: r } : r || {};
      return { ok: true, message: typeof o.message === "string" && o.message ? o.message : "done", ...(o.chip ? { chip: o.chip } : {}) };
    } catch (e) { return { ok: false, message: String((e && e.message) || e) }; }
  };
}

// ---------------------------------------------------------------- runtime
const plural = (tpl, n) => tpl.replace(/\{n\}/, n).replace(/([^\s|]+)\|([^\s|]+)(?:\|([^\s|]+))?/, (_, one, few, many) => {
  if (n === 1) return one;
  if (!many) return few;   // en: one | other
  const d = n % 10, h = n % 100;   // pl: one | 2-4 (not 12-14) | other
  return d >= 2 && d <= 4 && (h < 12 || h > 14) ? few : many;
});
// the page language replaces these in mountHw (L.hw); the default is ru, like the page
const T = { ...DICT.ru.hw, notInModel: DICT.ru.hw2.notInModel };
const POLL_ERR_MS = 3000, HEARTBEAT_MS = 20000, LOG_MS = 1000, LS_KEY = "hw.assign";

// ---------------------------------------------------------------- core (no DOM): granted ports, one session per port, the operations. Unit-tested with mocked ports (hw.test.mjs).
// Several boards at once: each port has its own key, session, log and card; a card is on at most one port; identical boards (same VID:PID, Web Serial
// reports no serial) get keys vid:pid, vid:pid#2, ... in getPorts order. Router commands for different boards run concurrently, for one board in order.
// Replug (unplug, or a native-USB chip re-enumerating on reset): a port with the same key that comes back within rememberMs gets its card back and its
// monitor reopened if it was open. reset with the monitor closed opens it for tailMs (the `hw reset` log), then closes it again.
const TAIL_MS = 9000, REMEMBER_MS = 120000;
export function createHwCore({ serial, boards = [], getManifest, sessionFor, storage = memStore(), onChange = () => {}, onLog = () => {},
  tailMs = TAIL_MS, rememberMs = REMEMBER_MS, reopenDelays = [300, 1000, 2500], now = Date.now }) {
  const byCard = Object.fromEntries(boards.map((b) => [b.card, b]));
  const entries = [];                    // {port, vid, pid, serial, key, cls, match, card, session, wantMon, tailUntil}
  const cards = {};                      // log name (card | port:<key>) -> {busy: null|{by, text}, lines, last, err}
  const gone = {};                       // key -> {card, want, tailUntil, baud, t}: ports that just vanished
  const st = (c) => (cards[c] ||= { busy: null, lines: [], last: null, err: null });
  const entryFor = (card) => entries.find((e) => e.card === card) || null;
  const entryByKey = (key) => entries.find((e) => e.key === key) || null;
  const logName = (e) => e.card || "port:" + e.key;
  const wants = (e) => !!e.wantMon || now() < (e.tailUntil || 0);

  const watch = (e) => {   // every line of this port goes to its card, or to port:<key> (looked up per line: the assignment may change)
    e.unsub = e.session.subscribe((s) => { const name = logName(e), c = st(name); c.lines.push(s); if (c.lines.length > 2000) c.lines.splice(0, 500); onLog(name, s); });
  };
  const freeKey = (k) => { if (!entryByKey(k)) return k; for (let i = 2; ; i++) if (!entryByKey(k + "#" + i)) return k + "#" + i; };

  let chain = Promise.resolve();
  const refreshPorts = () => (chain = chain.then(doRefresh, doRefresh));
  async function doRefresh() {
    let granted = []; try { granted = await serial.getPorts(); } catch {}
    granted = granted.filter((p) => p && p.connected !== false);
    const t = now();
    for (let i = entries.length - 1; i >= 0; i--) {
      const e = entries[i]; if (granted.includes(e.port)) continue;
      gone[e.key] = { card: e.card, want: !!e.wantMon, tailUntil: e.tailUntil || 0, baud: e.session.monitorSpeed, t };
      if (e.unsub) e.unsub(); entries.splice(i, 1);
    }
    for (const k of Object.keys(gone)) if (t - gone[k].t > rememberMs) delete gone[k];
    for (const port of granted) {
      if (entries.some((x) => x.port === port)) continue;
      const info = port.getInfo ? port.getInfo() : {};
      const e = { port, vid: hex4(info.usbVendorId) || "0000", pid: hex4(info.usbProductId) || "0000", serial: null, session: sessionFor(port), lockedByOther: false, card: null };
      e.key = freeKey(assignmentKey(e)); e.cls = usbClass(e.vid, e.pid);
      const g = gone[e.key];
      if (g) { delete gone[e.key]; e.prevCard = g.card; e.wantMon = g.want; e.tailUntil = g.tailUntil; if (g.baud) e.session.monitorSpeed = g.baud; }
      entries.push(e); watch(e);
    }
    // cards: a card is on one port at most. An explicit choice (storage) first, then the card a port already had (or had before a replug), then the single auto match.
    const assign = storage.get() || {}, taken = new Set();
    for (const e of entries) { e.match = matchCards(e, boards); e.next = null; }
    const give = (e, c) => { if (c && !e.next && !taken.has(c) && e.match.candidates.includes(c)) { e.next = c; taken.add(c); } };
    for (const e of entries) give(e, assign[e.key]);
    for (const e of entries) if (e.match.status !== "ambiguous") give(e, e.card || e.prevCard);
    for (const e of entries) if (e.match.status === "assigned") give(e, e.match.card);
    for (const e of entries) { e.card = e.next; delete e.next; delete e.prevCard; }
    reviveAll();
    onChange();
  }
  // reopen the monitor of a port that came back (the OS may need a moment before the port opens)
  async function revive(e) {
    e.reviving = true;
    try {
      for (const d of reopenDelays) {
        await new Promise((ok) => setTimeout(ok, d));
        if (!entries.includes(e) || !wants(e) || e.session.monitoring) return;
        if (st(logName(e)).busy) return;
        try { await e.session.openMonitor({ baud: e.session.monitorSpeed || 115200 }); onChange(); return; } catch {}
      }
    } finally { e.reviving = false; }
  }

  // ---- operations (the buttons and the router's commands call the same ones)
  // (card, by, {port, fw}): the target is the card's port, or the port with that key; fw = the published firmware name (defaults to the card)
  const guard = async (name, by, text, fn) => {
    const c = st(name); if (c.busy) throw new Error(T.busy);
    c.busy = { by, text }; c.err = null; onChange();
    try { const r = await fn(); if (text === T.bot || text === T.flashing) c.done = { at: now(), ver: c.last }; return r; }
    catch (err) { c.err = friendlyError(err); c.done = null; throw err; }
    finally { c.busy = null; onChange(); reviveAll(); }   // a port that came back while busy (re-enumerated during a flash) gets its monitor now
  };
  const reviveAll = () => { for (const x of entries) if (wants(x) && !x.session.monitoring && !x.reviving && !st(logName(x)).busy) revive(x); };
  const resolve = (card, o) => {
    const e = o.port ? entryByKey(o.port) : entryFor(card);
    if (!e) throw new Error(o.port ? `${T.noPortKey}: ${o.port}` : T.notConnected);
    return e;
  };
  // remember that another program/tab holds the port (the ports event reports held:false then)
  const track = async (e, fn) => {
    try { const r = await fn(); e.lockedByOther = false; return r; }
    catch (err) {
      if (isBusy(err)) e.lockedByOther = true;   // told by the browser's error (flash.mjs isBusy), whatever the page language
      throw err;
    }
  };
  const op = async (card, by, o, text, fn) => {   // async: a missing board is a rejection, not a throw
    const e = resolve(card, o || {}), fwName = (o && o.fw) || card || null;
    return guard(card || logName(e), by, text, () => track(e, () => fn(e, fwName, card || logName(e))));
  };
  const want = (e) => { e.wantMon = true; const cur = entryByKey(e.key); if (cur) cur.wantMon = true; };   // also on the entry that replaced e if the port re-enumerated meanwhile
  const manifestOf = (fwName) => (fwName ? getManifest(fwName).catch(() => null) : Promise.resolve(null));
  const flash = (card, by = "user", o = {}) => op(card, by, o, by === "bot" ? T.bot : T.flashing, async (e, fwName, name) => {
    if (!fwName) throw new Error(T.noFwName);
    const m = await getManifest(fwName);
    if (m.kind === "uf2") throw new Error("rp2: " + T.uf2Hint);
    const prog = (pct) => { const c = st(name); if (c.busy && c.busy.by === "user") { c.busy.text = `${T.flashing} ${pct}%`; onChange(); } };
    const r = await e.session.flashBoard({ kind: m.kind, files: m.files, chip: m.chip, monitorSpeed: m.monitor_speed, baud: m.flash_baud, onProgress: prog });
    want(e);   // the session reopens the monitor after a flash: keep it across a replug
    const ver = m.version || (byCard[fwName] && byCard[fwName].version) || null;
    st(name).last = ver;
    return { message: `${T.flashed}${fwName !== card ? " " + fwName : ""}${ver ? " " + ver : ""}`, chip: (r && r.chip) || undefined };
  });
  const monitor = (card, by = "user", o = {}) => op(card, by, o, by === "bot" ? T.botMon : T.openingMon, async (e, fwName) => {
    e.wantMon = true;
    if (e.session.monitoring) return { message: T.monOpened };
    const m = await manifestOf(fwName);
    try { await e.session.openMonitor({ baud: (m && m.monitor_speed) || 115200 }); } catch (err) { e.wantMon = false; throw err; }
    return { message: T.monOpened };
  });
  const closeMonitor = (card, o = {}) => op(card, "user", o, "", (e) => { e.wantMon = false; e.tailUntil = 0; return e.session.closeMonitor(); });
  const endTail = (key) => {   // the reset tail is over: close the monitor it opened, unless someone wants it now (looked up by key: the port may have been replugged)
    const e = entryByKey(key);
    if (!e || wants(e) || !e.session.monitoring || st(logName(e)).busy) return;
    e.session.closeMonitor().catch(() => {}).then(onChange);
  };
  const reset = (card, by = "user", o = {}) => op(card, by, o, by === "bot" ? T.botReset : T.resetting, async (e, fwName) => {
    const m = await manifestOf(fwName);
    if (m) { e.session.monitorSpeed = m.monitor_speed || e.session.monitorSpeed; e.session.lastKind = m.kind === "avr" ? "avr" : "esp32"; }
    if (!e.session.monitoring) {   // the boot log after a reset is what the agent wants (`hw reset`): open the monitor for the tail, close it again after
      await e.session.openMonitor({ baud: e.session.monitorSpeed || 115200 });
      e.tailUntil = now() + tailMs; const key = e.key; setTimeout(() => endTail(key), tailMs + 1);
    }
    await e.session.resetBoard(); return { message: T.reseted };
  });
  const identify = (card, by = "bot", o = {}) => op(card, by, o, T.botIdent, async (e, fwName) => {
    if (e.cls !== "esp-usb" && e.cls !== "usb-uart") return { message: `USB ${e.vid}:${e.pid} (${CLASS_NAME[e.cls] || T.unknownDev}): the chip cannot be told over USB, identify works on ESP32 only` };
    const m = await manifestOf(fwName);
    const r = await e.session.identify({ monitorSpeed: m && m.monitor_speed });
    want(e);
    return { message: r.description, chip: r.chip };
  });
  const dispatch = makeDispatcher({ flash: (c, o) => flash(c, "bot", o), reset: (c, o) => reset(c, "bot", o), monitor: (c, o) => monitor(c, "bot", o), identify: (c, o) => identify(c, "bot", o) });
  // router commands: one queue per board (so flash then reset of one board run in order), boards in parallel
  const queues = {};
  const runCmd = (cmd) => {
    const e = cmd && cmd.port ? entryByKey(cmd.port) : null;
    const q = e ? logName(e) : (cmd && cmd.card) || "port:" + ((cmd && cmd.port) || "");   // by board: a --port command and a card command for one board share a queue
    const p = (queues[q] || Promise.resolve()).then(() => dispatch(cmd));
    const done = p.then(() => { if (queues[q] === done) delete queues[q]; });
    queues[q] = done;
    return p;
  };

  return {
    byCard, entries, st, entryFor, entryByKey, logName, refreshPorts, flash, monitor, closeMonitor, reset, identify, runCmd,
    ports: () => entries.map((e) => ({ key: e.key, card: e.card, class: e.cls, vid: e.vid, pid: e.pid, serial: e.serial })),
    cardState: (name) => { const c = st(name), e = name.startsWith("port:") ? entryByKey(name.slice(5)) : entryFor(name);
      return { connected: !!e, busy: !!c.busy, lines: c.lines, published: !!byCard[name], version: (byCard[name] && byCard[name].version) || null, monitoring: !!(e && e.session.monitoring) }; },
  };
}
function memStore() { let o = {}; return { get: () => o, set: (x) => { o = x; } }; }


export async function mountHw({ api, L, cfg }) {
  Object.assign(T, (L && L.hw) || {}); setFlashTexts((L && L.flash) || {});   // page language
  if (T.espUsb) CLASS_NAME["esp-usb"] = T.espUsb;
  const base = new URL("./", location.href), pid = location.pathname.split("/")[1] || "";
  const supported = webSerialSupported(), viewer = Math.random().toString(36).slice(2, 10);
  const ls = { get() { try { return JSON.parse(localStorage.getItem(LS_KEY) || "{}") || {}; } catch { return {}; } },
               set(o) { try { localStorage.setItem(LS_KEY, JSON.stringify(o)); } catch {} } };
  const boards = cfg && cfg.flash && cfg.flash.fw === false ? [] : await fetch(new URL("fw/index.json", base)).then((r) => (r.ok ? r.json() : null)).then(parseIndex).catch(() => []);
  const published = boards.length > 0, byCard = Object.fromEntries(boards.map((b) => [b.card, b]));

  const listeners = new Set(), notify = () => { for (const f of listeners) { try { f(); } catch (e) { console.error(e); } } renderRow(); };

  // ---- manifests (fw/<name>/manifest.json; a name missing from index.json is tried directly: ad-hoc firmware published after the page loaded)
  const manifests = {};
  const getManifest = (name) => (manifests[name] ||= (async () => {
    const b = byCard[name], url = new URL(b ? b.manifest : name + "/manifest.json", new URL("fw/", base));
    const m = await fetch(url).then((r) => { if (!r.ok) throw new Error(b ? `HTTP ${r.status} ${url}` : T.noFw + `: ${name}`); return r.json(); });
    m.files = (m.files || []).map((f) => ({ ...f, url: new URL(f.url, url).href })); return m;
  })().catch((e) => { delete manifests[name]; throw e; }));

  // ---- ports, sessions, operations: the core (the buttons and the router's commands call the same functions)
  const core = createHwCore({ serial: supported ? navigator.serial : { getPorts: async () => [] }, boards, getManifest, sessionFor: (typeof window !== "undefined" && window.__hwSessionFor) || sessionFor, storage: ls,
    onChange: () => notify(), onLog: (name, s) => { queueLog(name, s); notifyLog(name); } });
  const { entries, st, entryFor, entryByKey, logName, flash, monitor, reset, identify } = core, closeMon = core.closeMonitor;
  const refreshPorts = async () => { await core.refreshPorts(); sendPorts(); syncPoll(); };
  async function connect() {
    if (!supported) return;
    try { await navigator.serial.requestPort(); } catch (err) { if (err && err.name !== "NotFoundError") console.error(err); return; }
    await refreshPorts();
  }

  // ---- router client
  let routerOff = !supported || !pid, lastCmd = 0, lastFocus = Date.now();
  const FOCUS_RECENT_MS = 60000;
  const isActive = () => document.visibilityState === "visible" || Date.now() - lastFocus < FOCUS_RECENT_MS;   // only an active tab reports card assignments
  const post = async (body) => {
    if (routerOff) return;
    try {
      const r = await fetch(`/hw/${pid}/event`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ viewer, ...body }), keepalive: true });
      // 404 = no router behind this page (a local static server), 401/403 = not signed in / not a member: stop calling. Never for a result: there 404 means "unknown command id" (the router restarted)
      if (body.type !== "result" && [401, 403, 404, 405, 501].includes(r.status)) routerOff = true;
    } catch {}
  };
  const portsPayload = () => { const active = isActive(); return entries.map((e) => ({ key: e.key, card: active ? e.card : null, class: e.cls, vid: e.vid, pid: e.pid, serial: e.serial, held: !e.lockedByOther || e.session.monitoring })); };
  const sendPorts = () => post({ type: "ports", visible: document.visibilityState === "visible", ports: portsPayload() });
  const logBuf = {};
  const queueLog = (card, s) => { (logBuf[card] ||= []).push(s); };
  const flushLog = () => { for (const card of Object.keys(logBuf)) { const lines = logBuf[card]; delete logBuf[card]; if (lines.length) post({ type: "log", card, lines }); } };
  // The long-poll holds one of the browser's 6 HTTP/1.1 connections to this host for 25 s. The router sends a command only to a viewer whose
  // ports hold that board, so only a tab with a granted port polls (seven board-less tabs once held all six, and every page and download waited).
  // The last board gone: the open poll runs out (a command queued meanwhile fails at once, never waits for a later board), no new one starts.
  let polling = false;
  const syncPoll = () => { if (entries.length && !routerOff && !polling) pollLoop(); };
  async function pollLoop() {
    polling = true;
    let wait = POLL_ERR_MS;
    try {
      while (!routerOff && entries.length) {
        try {
          const r = await fetch(`/hw/${pid}/cmd?viewer=${viewer}&after=${lastCmd}`);
          if ([401, 403, 404, 405, 501].includes(r.status)) { routerOff = true; return; }
          if (!r.ok) throw new Error("HTTP " + r.status);
          const { cmds = [] } = await r.json(); wait = POLL_ERR_MS;
          for (const c of cmds) {
            if (c.id < lastCmd) lastCmd = 0;   // the router restarted and counts from a lower id again
            lastCmd = Math.max(lastCmd, c.id);
            core.runCmd(c).then((res) => post({ type: "result", cmd: c.id, ...res }));   // not awaited: a long flash on one board must not hold another board's commands
          }
        } catch { await new Promise((ok) => setTimeout(ok, wait)); wait = Math.min(wait * 2, 30000); }
      }
    } finally { polling = false; }
  }

  // ---- UI: "Boards on the desk" (L.hw2.section) in the panel (connect + a count); every connected board gets its own window over the 3D (#hwDock)
  const T2 = { ...DICT.ru.hw2 };
  Object.assign(T2, (L && L.hw2) || {});
  T.notInModel = T2.notInModel;   // portLabel (module level) follows the page too
  const CHIP = { esp32: "ESP32", esp32c3: "ESP32-C3", esp32s3: "ESP32-S3", atmega328p: "ATmega328P", rp2040: "RP2040" };
  const chipName = (c) => CHIP[c] || String(c || "").toUpperCase();
  const verText = (v) => { const m = /^(\S+)\s+(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)$/.exec(String(v || "")); return m ? `${m[1]} · ${m[4]}.${m[3]} ${m[5]}:${m[6]}` : String(v || ""); };
  const hhmm = (t) => new Date(t).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const nameOf = (card) => { const sb = api.sim && api.sim.meta && (api.sim.meta.boards || []).find((b) => b.id === card); return sb ? sb.name : card; };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pctOf = (t) => { const m = /(\d+)%/.exec(t || ""); return m ? +m[1] : null; };
  // the one status line every board view shows: [class, html, progress (null | number | "unk")]
  const statusOf = (card, e) => {
    const c = st(card);
    if (c.busy && c.busy.by === "bot") return ["bot", `<b>${esc(c.busy.text)}</b>`, "unk"];
    if (c.busy) return ["", `<b>${esc(c.busy.text || "…")}</b>`, pctOf(c.busy.text) ?? "unk"];
    if (c.err) return ["err", esc(c.err), null];
    if (c.done) return ["ok", `<b>${T2.done}</b> · ${hhmm(c.done.at)}${c.done.ver ? " · " + esc(verText(c.done.ver)) : ""}`, null];
    const b = byCard[card];
    return ["", `${T2.on}${b && b.version ? ` · <span class="hwV" title="${T2.fw}">${esc(verText(b.version))}</span>` : ""}`, null];
  };

  const sec = document.getElementById("secHw"), secBody = sec && sec.querySelector(".sb");
  const row = document.createElement("div"); row.id = "hwRow";
  if (secBody) { secBody.appendChild(row); sec.hidden = false; }
  const dock = document.getElementById("hwDock") || document.body;
  let rowSig = "";
  function renderRow() {
    if (!supported) { if (!row.dataset.done) { row.dataset.done = "1"; row.innerHTML = `<span class="sub" style="margin:0">${esc(T.noSerial)}</span>`; } return; }
    if (!row.dataset.done) { row.dataset.done = "1"; row.innerHTML = `<button id="hwConnect">${esc(T.connect)}</button><span class="hwCount"></span>`; row.querySelector("#hwConnect").addEventListener("click", connect); }
    row.querySelector(".hwCount").textContent = entries.length ? T2.count.replace("{n}", entries.length) : T2.none;
    renderWins();
  }

  // ---- board windows
  const wins = new Map();                      // port key -> {el, sig, logOpen}
  function renderWins() {
    for (const [key, w] of wins) if (!entryByKey(key)) { if (window.__win) window.__win.forget(w.el); w.el.remove(); wins.delete(key); }
    for (const e of entries) {
      let w = wins.get(e.key);
      if (!w) {
        const el = document.createElement("div"); const appW = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--app-w")) || 0;
        el.className = "hwWin" + (window.innerWidth <= 700 || window.innerWidth - 392 - 16 - appW < 300 ? " min" : "");   // no room beside the panels: start folded
        el.innerHTML = `<div class="hwH"><span class="hwN"></span><span class="hwChip"></span><button class="icon hwFold" title="${T2.fold}"></button></div>
          <div class="hwS"></div><div class="hwP" hidden><i></i></div><div class="hwSel"></div><div class="hwA"></div>
          <div class="hwL" hidden><div class="hwLB"><button class="mon"></button><span class="hwLN"></span></div><pre class="hwLog"></pre></div>`;
        dock.appendChild(el);
        w = { el, sig: "", logOpen: false };
        el.querySelector(".hwH").addEventListener("click", () => el.classList.toggle("min"));
        el.querySelector(".mon").addEventListener("click", () => { const en = entryByKey(e.key); if (!en) return; const n = en.card || logName(en); (en.session.monitoring ? closeMon(n) : monitor(n)).catch((err) => { st(n).err = friendlyError(err); notify(); }); });
        wins.set(e.key, w);
        if (window.__win) window.__win.register(el, { id: "hw-" + e.key, head: el.querySelector(".hwH"), cls: "min", flow: true });
      }
      paintWin(e, w);
    }
  }
  function paintWin(e, w) {
    const el = w.el, name = e.card || logName(e), b = e.card ? byCard[e.card] : null, c = st(name);
    el.dataset.hwwin = name;
    el.classList.toggle("unknown", !e.card);
    el.querySelector(".hwN").textContent = e.card ? nameOf(e.card) : T2.unknown;
    el.querySelector(".hwChip").textContent = b ? chipName(b.chip) : (CLASS_NAME[e.cls] || "USB");
    const [cls, html, prog] = e.card ? statusOf(name, e) : ["", `USB ${e.vid}:${e.pid} · ${T2.notInModel}`, null];
    const s = el.querySelector(".hwS"); s.className = "hwS " + cls; s.innerHTML = html;
    const p = el.querySelector(".hwP"); p.hidden = prog == null; p.className = "hwP" + (prog === "unk" ? " unk" : ""); p.firstChild.style.width = typeof prog === "number" ? prog + "%" : "";
    const sig = [e.card, e.match && e.match.status, !!c.busy, e.session.monitoring, w.logOpen].join("|");
    if (sig !== w.sig) {
      w.sig = sig;
      const sel = el.querySelector(".hwSel"); sel.innerHTML = "";
      if (e.match && e.match.status === "ambiguous" && !e.card) {
        const x = document.createElement("select"); x.innerHTML = `<option value="">${esc(T2.which)}</option>` + e.match.candidates.map((cc) => `<option value="${esc(cc)}"${cc === e.card ? " selected" : ""}>${esc(nameOf(cc))}</option>`).join("");
        x.addEventListener("change", () => { const a = ls.get(); if (x.value) a[e.key] = x.value; else delete a[e.key]; ls.set(a); refreshPorts(); });
        sel.appendChild(x);
      }
      const act = el.querySelector(".hwA"); act.innerHTML = "";
      const mk = (label, klass, fn) => { const bt = document.createElement("button"); bt.textContent = label; bt.className = klass; bt.addEventListener("click", async () => { try { await fn(); } catch (err) { st(name).err = friendlyError(err); notify(); } }); act.appendChild(bt); return bt; };
      if (b && b.chip.startsWith("rp2")) {
        const a = document.createElement("a"); a.textContent = T.uf2; a.className = "btn"; a.href = "#"; getManifest(e.card).then((m) => { a.href = m.files[0].url; a.download = ""; }).catch(() => {}); act.appendChild(a);
      } else if (e.card) {
        mk(T.flash, "primary", () => { st(name).err = null; return flash(e.card); }).disabled = !!c.busy;
        mk(T2.reset, "ghost hwRst", () => reset(e.card)).disabled = !!c.busy;
      }
      const lt = mk(T2.log, "ghost hwLogT", () => { w.logOpen = !w.logOpen; paintWin(e, w); paintLog(name); });
      lt.setAttribute("aria-expanded", String(w.logOpen));
      el.classList.toggle("logOpen", w.logOpen);
      el.querySelector(".hwL").hidden = !w.logOpen;
      const mon = el.querySelector(".mon"); mon.textContent = T2.mon; mon.className = "mon" + (e.session.monitoring ? " on" : ""); mon.setAttribute("aria-pressed", String(!!e.session.monitoring));
    }
  }
  const paintLog = (name) => {
    for (const [, w] of wins) {
      if (w.el.dataset.hwwin !== name || !w.logOpen) continue;
      const pre = w.el.querySelector(".hwLog"), lines = st(name).lines, stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
      pre.textContent = lines.slice(-200).join("\n"); if (stick) pre.scrollTop = pre.scrollHeight;
      w.el.querySelector(".hwLN").textContent = lines.length ? plural(T2.lines, lines.length) : "";
    }
  };
  const logSubs = new Set([paintLog]), notifyLog = (card) => { for (const f of logSubs) f(card); };
  const openLog = (card) => { for (const [key, w] of wins) if (w.el.dataset.hwwin === card) { w.el.classList.remove("min"); w.logOpen = true; paintWin(entryByKey(key), w); paintLog(card); } };

  // ---- the sim board card: one line about the real board + "Flash to the board" (L.hw2.flashTo)
  function mountCard(el, card) {
    if (el.__hwOff) el.__hwOff();
    el.className = "hwLine"; el.innerHTML = "";
    const render = () => {
      const e = entryFor(card), b = byCard[card], c = st(card);
      if (!b) { el.textContent = T.noFw; return; }
      if (!supported) { el.textContent = T.noSerial; return; }
      if (!e) { el.textContent = T2.realOff; return; }
      const [cls, html] = statusOf(card, e);
      el.innerHTML = `<span class="${cls}">${c.busy || c.err || c.done ? html : T2.realOn}</span>`;
      const bt = document.createElement("button"); bt.className = "primary"; bt.textContent = T2.flashTo; bt.disabled = !!c.busy;
      bt.addEventListener("click", () => { c.err = null; flash(card).catch((err) => { c.err = friendlyError(err); notify(); }); });
      const lg = document.createElement("button"); lg.className = "ghost"; lg.textContent = T2.log; lg.addEventListener("click", () => openLog(card));
      el.append(bt, lg);
    };
    listeners.add(render); render();
    el.__hwOff = () => { listeners.delete(render); el.__hwOff = null; };
    return el.__hwOff;
  }

  api.hw = {
    supported, published, boards, viewer, ports: () => entries.map((e) => ({ key: e.key, card: e.card, class: e.cls, vid: e.vid, pid: e.pid, serial: e.serial })),
    cardState: core.cardState,   // (card | "port:<key>") -> {connected, busy, lines, published, version, monitoring}
    subscribe: (fn) => { listeners.add(fn); return () => listeners.delete(fn); },   // fn() on any state change (connect, busy, monitor)
    fail: (card, err) => { st(card).err = err ? friendlyError(err) : null; notify(); },   // show (or clear, err = null) an error from a caller outside the card (sim board list)
    connect, flash, monitor, reset, identify, mountCard, refreshPorts, openLog,
  };
  renderRow();
  (async () => { while (!api.sim && !api.simError && (api.config && api.config.sim)) await new Promise((r) => setTimeout(r, 300)); notify(); })();   // board names come from the machine
  if (supported) {
    navigator.serial.addEventListener("connect", () => refreshPorts());
    navigator.serial.addEventListener("disconnect", () => refreshPorts());
    await refreshPorts();
    setInterval(sendPorts, HEARTBEAT_MS); setInterval(flushLog, LOG_MS);
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") lastFocus = Date.now(); sendPorts(); });
    window.addEventListener("focus", () => { lastFocus = Date.now(); sendPorts(); });
  }
  document.dispatchEvent(new Event("hw-ready"));
  return api.hw;
}
