// Pure logic of board flashing in the viewer (hw.mjs). Written from the spec before hw.mjs exists:
//   work_sessions/DesignEngineer/2026-10-20/flashing/CONTRACT.md  ("USB class from VID:PID", "Published firmware", "Router <-> viewer").
// Run: node --test skills/web-viewer/templates/assembly/hw.test.mjs
//
// API fixed here (hw.mjs exports, all pure, no DOM, no Web Serial):
//   usbClass(vid, pid)            -> "esp-usb" | "usb-uart" | "arduino" | "rp2" | null. vid/pid: numbers (Web Serial usbVendorId) or hex strings.
//   matchCards(port, boards)      -> {status: "assigned"|"ambiguous"|"unassigned", card: name|null, candidates: [names]}
//                                    port = {vid, pid}; boards = parseIndex(...) output. A card is a candidate when the port's class is in its `usb`.
//   assignmentKey(info)           -> "vvvv:pppp:serial" or "vvvv:pppp" without a serial (4-digit lowercase hex), the localStorage key of a remembered choice.
//   parseIndex(json)              -> boards [{card, chip, usb:[], manifest, version, built}] from fw/index.json (object or text); [] when unusable.
//   makeDispatcher({flash,reset,monitor}) -> async (cmd:{id,op,card}) -> {ok, message, chip?} (the body of the router's `result` event, minus type/viewer/cmd).
import test from "node:test";
import assert from "node:assert/strict";
import { usbClass, matchCards, assignmentKey, parseIndex, makeDispatcher } from "./hw.mjs";
import * as HW from "./hw.mjs";
import { DICT } from "./i18n.mjs";   // Amendment 1 exports are read off this namespace so a missing one fails only its own test


// ---- USB class from VID:PID (the contract table)
test("usbClass: Espressif native USB 303A:1001 and any 303A:* is esp-usb", () => {
  assert.equal(usbClass(0x303a, 0x1001), "esp-usb");
  assert.equal(usbClass(0x303a, 0x0002), "esp-usb");
  assert.equal(usbClass("303a", "1001"), "esp-usb");
  assert.equal(usbClass("303A", "1001"), "esp-usb", "hex strings are case-insensitive");
});
test("usbClass: CP210x, CH340/CH9102 and FTDI are usb-uart", () => {
  for (const [v, p] of [[0x10c4, 0xea60], [0x1a86, 0x7523], [0x1a86, 0x55d4], [0x0403, 0x6001], [0x0403, 0x6015]])
    assert.equal(usbClass(v, p), "usb-uart", v.toString(16) + ":" + p.toString(16));
});
test("usbClass: Arduino 2341:* and 2A03:* are arduino", () => {
  assert.equal(usbClass(0x2341, 0x0043), "arduino");
  assert.equal(usbClass(0x2341, 0x0010), "arduino");
  assert.equal(usbClass(0x2a03, 0x0043), "arduino");
});
test("usbClass: Raspberry Pi 2E8A:* is rp2", () => {
  assert.equal(usbClass(0x2e8a, 0x0005), "rp2");
  assert.equal(usbClass(0x2e8a, 0x000f), "rp2");
});
test("usbClass: an unknown device, or a known vendor with a PID outside the table, is null", () => {
  assert.equal(usbClass(0x1234, 0x5678), null);
  assert.equal(usbClass(0x10c4, 0x0001), null, "CP210x vendor but not EA60");
  assert.equal(usbClass(undefined, undefined), null, "a port without USB info (built-in UART)");
});

// ---- card matching
const IDX = {
  boards: [
    { card: "master", chip: "esp32", usb: ["usb-uart"], manifest: "master/manifest.json", version: "v18 a1b2c3d", built: "2026-10-20T19:40:00Z" },
    { card: "ui", chip: "esp32c3", usb: ["esp-usb"], manifest: "ui/manifest.json", version: "v3", built: "2026-10-20T19:41:00Z" },
    { card: "dosing", chip: "atmega328p", usb: ["usb-uart", "arduino"], manifest: "dosing/manifest.json", version: "v1", built: "2026-10-20T19:42:00Z" },
    { card: "pico", chip: "rp2040", usb: ["rp2"], manifest: "pico/manifest.json", version: "v2", built: "2026-10-20T19:43:00Z" },
  ],
};
const boards = parseIndex(IDX);

test("matchCards: exactly one compatible card is assigned automatically", () => {
  const m = matchCards({ vid: 0x303a, pid: 0x1001 }, boards);
  assert.deepEqual(m, { status: "assigned", card: "ui", candidates: ["ui"] });
});
test("matchCards: several compatible cards are ambiguous (the client picks), none pre-picked", () => {
  const m = matchCards({ vid: 0x10c4, pid: 0xea60 }, boards);   // usb-uart: master (esp32) and dosing (atmega328p)
  assert.equal(m.status, "ambiguous");
  assert.equal(m.card, null);
  assert.deepEqual([...m.candidates].sort(), ["dosing", "master"]);
});
test("matchCards: no compatible card is unassigned", () => {
  const only = parseIndex({ boards: [IDX.boards[1]] });   // only the esp-usb card is published
  const m = matchCards({ vid: 0x10c4, pid: 0xea60 }, only);
  assert.deepEqual(m, { status: "unassigned", card: null, candidates: [] });
});
test("matchCards: an unknown USB device or nothing published is unassigned", () => {
  assert.equal(matchCards({ vid: 0x1234, pid: 0x5678 }, boards).status, "unassigned");
  assert.equal(matchCards({ vid: 0x303a, pid: 0x1001 }, []).status, "unassigned");
});
test("matchCards: Arduino-class port matches only cards that list arduino", () => {
  const m = matchCards({ vid: 0x2341, pid: 0x0043 }, boards);
  assert.deepEqual(m, { status: "assigned", card: "dosing", candidates: ["dosing"] });
});

// ---- remembered assignment key
test("assignmentKey: VID:PID:serial when the port reports a serial", () => {
  assert.equal(assignmentKey({ vid: 0x303a, pid: 0x1001, serial: "AB:CD:01" }), "303a:1001:AB:CD:01");
});
test("assignmentKey: VID:PID alone without a serial, 4-digit lowercase hex, numbers and strings alike", () => {
  assert.equal(assignmentKey({ vid: 0x10c4, pid: 0xea60 }), "10c4:ea60");
  assert.equal(assignmentKey({ vid: "10C4", pid: "EA60", serial: null }), "10c4:ea60");
  assert.equal(assignmentKey({ vid: 0x403, pid: 0x6001, serial: "" }), "0403:6001", "padded to 4 digits; empty serial = no serial");
});
test("assignmentKey: two boards of the same model with different serials get different keys", () => {
  assert.notEqual(assignmentKey({ vid: 0x1a86, pid: 0x7523, serial: "A" }), assignmentKey({ vid: 0x1a86, pid: 0x7523, serial: "B" }));
});

// ---- manifest/index parsing
test("parseIndex: keeps card, chip, usb, manifest, version, built", () => {
  assert.deepEqual(parseIndex(IDX)[0], IDX.boards[0]);
  assert.equal(parseIndex(IDX).length, 4);
});
test("parseIndex: accepts JSON text", () => {
  assert.equal(parseIndex(JSON.stringify(IDX)).length, 4);
});
test("parseIndex: junk gives an empty list, not a throw", () => {
  for (const bad of [null, undefined, "", "{not json", {}, { boards: "x" }, 42])
    assert.deepEqual(parseIndex(bad), [], String(bad));
});
test("parseIndex: drops entries without a card or a chip, keeps the good ones", () => {
  const got = parseIndex({ boards: [{ chip: "esp32", usb: ["usb-uart"] }, { card: "a" }, IDX.boards[0], null] });
  assert.deepEqual(got.map((b) => b.card), ["master"]);
});
test("parseIndex: a missing usb list becomes [] so it matches no port", () => {
  const got = parseIndex({ boards: [{ card: "x", chip: "esp32", manifest: "x/manifest.json" }] });
  assert.deepEqual(got[0].usb, []);
  assert.equal(matchCards({ vid: 0x10c4, pid: 0xea60 }, got).status, "unassigned");
});

// ---- router command -> flash / reset / monitor
function fakes() {
  const calls = [];
  const mk = (name, ret) => async (...a) => { calls.push([name, ...a]); return typeof ret === "function" ? ret(...a) : ret; };
  return { calls, mk };
}
test("dispatcher: flash cmd calls flash(card) and reports ok with the function's message and chip", async () => {
  const { calls, mk } = fakes();
  const d = makeDispatcher({ flash: mk("flash", { message: "flashed v18", chip: "ESP32-C3" }), reset: mk("reset"), monitor: mk("monitor") });
  const r = await d({ id: 7, op: "flash", card: "master" });
  assert.deepEqual(calls, [["flash", "master"]]);
  assert.equal(r.ok, true);
  assert.equal(r.message, "flashed v18");
  assert.equal(r.chip, "ESP32-C3");
});
test("dispatcher: reset and monitor map to their own functions only", async () => {
  const { calls, mk } = fakes();
  const d = makeDispatcher({ flash: mk("flash"), reset: mk("reset", { message: "reset done" }), monitor: mk("monitor", { message: "monitor open" }) });
  assert.equal((await d({ id: 1, op: "reset", card: "ui" })).message, "reset done");
  assert.equal((await d({ id: 2, op: "monitor", card: "ui" })).message, "monitor open");
  assert.deepEqual(calls, [["reset", "ui"], ["monitor", "ui"]]);
});
test("dispatcher: a function that returns nothing is still ok", async () => {
  const { mk } = fakes();
  const d = makeDispatcher({ flash: mk("flash"), reset: mk("reset"), monitor: mk("monitor") });
  const r = await d({ id: 3, op: "reset", card: "master" });
  assert.equal(r.ok, true);
  assert.equal(typeof r.message, "string");
});
test("dispatcher: a throwing flash becomes ok:false with the error text, not a rejection", async () => {
  const { mk } = fakes();
  const d = makeDispatcher({ flash: mk("flash", () => { throw new Error("Port busy"); }), reset: mk("reset"), monitor: mk("monitor") });
  const r = await d({ id: 4, op: "flash", card: "master" });
  assert.equal(r.ok, false);
  assert.match(r.message, /Port busy/);
});
test("dispatcher: an unknown op is refused with ok:false and calls nothing", async () => {
  const { calls, mk } = fakes();
  const d = makeDispatcher({ flash: mk("flash"), reset: mk("reset"), monitor: mk("monitor") });
  const r = await d({ id: 5, op: "erase", card: "master" });
  assert.equal(r.ok, false);
  assert.match(r.message, /erase/);
  assert.deepEqual(calls, []);
});
test("dispatcher: a command without a card is refused", async () => {
  const { calls, mk } = fakes();
  const d = makeDispatcher({ flash: mk("flash"), reset: mk("reset"), monitor: mk("monitor") });
  const r = await d({ id: 6, op: "flash" });
  assert.equal(r.ok, false);
  assert.deepEqual(calls, []);
});

// ======================================================================== Amendment 1: any connected board
// CONTRACT.md "Amendment 1". New pure exports of hw.mjs fixed here (minimal):
//   portEntries(ports, boards, assigned={}) -> [{key, class, vid, pid, serial, card}], ONE entry per granted port, none dropped.
//       ports = [{vid, pid, serial?}] (numbers or hex strings); vid/pid come out as 4-digit lowercase hex strings, serial null when absent;
//       key = assignmentKey; class = usbClass or null; card = assigned[key] if present, else the auto match (exactly one candidate), else null.
//   portLabel(entry) -> string for the viewer row of an unassigned port, e.g. «USB 303a:1001 (<i18n hw.espUsb>) — <i18n hw2.notInModel>» (ru by default).
//   chipMatches(manifestChip, detectedChipName) -> bool; "esp32c3" vs "ESP32-C3 (QFN32) rev v0.4" is true, vs "ESP32" false.
//   espBootloaderOffset(chip) -> 0x1000 for esp32 / esp32s2, 0 for esp32c3 / s3 / c6 / h2 (chip = manifest style "esp32c3" or "ESP32-C3").
//   makeDispatcher({flash,reset,monitor,identify}): cmd = {id, op, card?, port?, fw?}. A command with only a card calls fn(card) exactly as before.
//       With a port (or fw) the call is fn(card_or_null, {port, fw}), fw defaulting to card. A command needs a card OR a port; identify is a known op.
const need = (name) => { assert.equal(typeof HW[name], "function", `hw.mjs must export ${name}()`); return HW[name]; };

test("portEntries: an unknown VID:PID port is still listed, class null, card null", () => {
  const e = need("portEntries")([{ vid: 0x1234, pid: 0x5678 }], boards);
  assert.equal(e.length, 1);
  assert.deepEqual(e[0], { key: "1234:5678", class: null, vid: "1234", pid: "5678", serial: null, card: null });
});
test("portEntries: lists every port, auto-assigns a single match, leaves ambiguous ones null, honours remembered choices", () => {
  const e = need("portEntries")([
    { vid: 0x303a, pid: 0x1001, serial: "AB:01" },   // esp-usb -> only "ui"
    { vid: 0x10c4, pid: 0xea60 },                    // usb-uart -> master|dosing, ambiguous
    { vid: 0x1a86, pid: 0x7523, serial: "S2" },      // usb-uart, remembered as dosing
    { vid: 0x1234, pid: 0x5678 },
  ], boards, { "1a86:7523:S2": "dosing" });
  assert.deepEqual(e.map((x) => x.key), ["303a:1001:AB:01", "10c4:ea60", "1a86:7523:S2", "1234:5678"]);
  assert.deepEqual(e.map((x) => x.card), ["ui", null, "dosing", null]);
  assert.deepEqual(e.map((x) => x.class), ["esp-usb", "usb-uart", "usb-uart", null]);
  assert.equal(e[0].serial, "AB:01");
});
test("portEntries: with nothing published every port is listed unassigned (the 'wrong board in the model' case)", () => {
  const e = need("portEntries")([{ vid: 0x303a, pid: 0x1001 }], []);
  assert.deepEqual([e.length, e[0].card, e[0].class], [1, null, "esp-usb"]);
});
test("portLabel: the unassigned row reads «USB vid:pid (<ESP32 with USB>) — <not in the model>» in the default language", () => {
  assert.equal(need("portLabel")({ key: "303a:1001", class: "esp-usb", vid: "303a", pid: "1001", serial: null, card: null }), `USB 303a:1001 (${DICT.ru.hw.espUsb}) — ${DICT.ru.hw2.notInModel}`);
});
test("portLabel: an unknown class still names the VID:PID and says it is not in the model", () => {
  const t = need("portLabel")({ key: "1234:5678", class: null, vid: "1234", pid: "5678", serial: null, card: null });
  assert.match(t, /USB 1234:5678/); assert.ok(t.includes(DICT.ru.hw2.notInModel), t);
});

test("chipMatches: manifest esp32c3 vs detected ESP32-C3 is true, vs plain ESP32 is false", () => {
  const m = need("chipMatches");
  assert.equal(m("esp32c3", "ESP32-C3"), true);
  assert.equal(m("esp32c3", "ESP32-C3 (QFN32) rev v0.4"), true);
  assert.equal(m("esp32c3", "ESP32"), false);
  assert.equal(m("esp32c3", "esp32"), false);
});
test("chipMatches: classic esp32 matches the ESP32-D0WD variants but not C3/S3, and s2 is not s3", () => {
  const m = need("chipMatches");
  assert.equal(m("esp32", "ESP32-D0WD-V3"), true);
  assert.equal(m("esp32", "ESP32-C3"), false);
  assert.equal(m("esp32s3", "ESP32-S2"), false);
  assert.equal(m("esp32s3", "ESP32-S3"), true);
});
test("chipMatches: nothing detected never matches", () => {
  assert.equal(need("chipMatches")("esp32c3", ""), false);
  assert.equal(need("chipMatches")("esp32c3", undefined), false);
});

test("espBootloaderOffset: 0x1000 for classic esp32 and s2, 0x0 for c3 / s3 / c6 / h2", () => {
  const o = need("espBootloaderOffset");
  assert.equal(o("esp32"), 0x1000);
  assert.equal(o("esp32s2"), 0x1000);
  for (const c of ["esp32c3", "esp32s3", "esp32c6", "esp32h2"]) assert.equal(o(c), 0, c);
  assert.equal(o("ESP32-C3"), 0, "detected-name spelling is accepted too");
});

function fakes2() {
  const calls = [];
  const mk = (name, ret) => async (...a) => { calls.push([name, ...a]); return typeof ret === "function" ? ret(...a) : ret; };
  return { calls, mk };
}
const all = (mk, over = {}) => ({ flash: mk("flash"), reset: mk("reset"), monitor: mk("monitor"), identify: mk("identify"), ...over });

test("dispatcher: a flash by port and fw (no card) calls flash(null, {port, fw})", async () => {
  const { calls, mk } = fakes2();
  const r = await makeDispatcher(all(mk, { flash: mk("flash", { message: "flashed c3test", chip: "ESP32-C3" }) }))({ id: 9, op: "flash", port: "303a:1001", fw: "c3test" });
  assert.equal(r.ok, true); assert.equal(r.chip, "ESP32-C3");
  assert.equal(calls.length, 1); assert.equal(calls[0][0], "flash");
  assert.ok(!calls[0][1], "no card");
  assert.deepEqual(calls[0][2], { port: "303a:1001", fw: "c3test" });
});
test("dispatcher: port + card without fw defaults fw to the card", async () => {
  const { calls, mk } = fakes2();
  await makeDispatcher(all(mk))({ id: 10, op: "flash", card: "master", port: "10c4:ea60" });
  assert.equal(calls[0][1], "master");
  assert.deepEqual(calls[0][2], { port: "10c4:ea60", fw: "master" });
});
test("dispatcher: reset by port alone is accepted", async () => {
  const { calls, mk } = fakes2();
  const r = await makeDispatcher(all(mk))({ id: 11, op: "reset", port: "1234:5678" });
  assert.equal(r.ok, true);
  assert.equal(calls[0][0], "reset"); assert.equal(calls[0][2].port, "1234:5678");
});
test("dispatcher: identify is a known op; message and chip pass through", async () => {
  const { calls, mk } = fakes2();
  const msg = "ESP32-C3 (QFN32) rev v0.4, 4MB flash, MAC aa:bb:cc:dd:ee:ff";
  const r = await makeDispatcher(all(mk, { identify: mk("identify", { message: msg, chip: "ESP32-C3" }) }))({ id: 12, op: "identify", port: "303a:1001" });
  assert.deepEqual(r, { ok: true, message: msg, chip: "ESP32-C3" });
  assert.equal(calls[0][0], "identify"); assert.equal(calls[0][2].port, "303a:1001");
});
test("dispatcher: a failing identify becomes ok:false with the text", async () => {
  const { mk } = fakes2();
  const r = await makeDispatcher(all(mk, { identify: mk("identify", () => { throw new Error("Failed to connect with the device"); }) }))({ id: 13, op: "identify", port: "303a:1001" });
  assert.equal(r.ok, false); assert.match(r.message, /Failed to connect/);
});
test("dispatcher: a command with neither card nor port is still refused, and identify without a target too", async () => {
  const { calls, mk } = fakes2();
  const d = makeDispatcher(all(mk));
  assert.equal((await d({ id: 14, op: "flash" })).ok, false);
  assert.equal((await d({ id: 15, op: "identify" })).ok, false);
  assert.deepEqual(calls, []);
});

// ======================================================================== createHwCore: several boards, replug, reset log tail
// Spec: CONTRACT.md + the "several boards at once / replug / reset tail" brief (2026-10-20). Export fixed here:
//   createHwCore({serial, boards, getManifest, sessionFor, storage?, onChange?, onLog?, tailMs?, rememberMs?, reopenDelays?, now?}) -> core
//   core: refreshPorts(), ports() -> [{key, card, class, vid, pid, serial}], cardState(name) -> {connected, busy, lines}
//         (name = a card or "port:<key>"), flash/monitor/closeMonitor/reset/identify(card, by, opts?), runCmd(cmd) -> {ok, message, chip?}
// Two identical boards (same VID:PID, no serial) get keys "vvvv:pppp" and "vvvv:pppp#2" in getPorts order.
// Fakes below stand in for Web Serial ports and flash.mjs FlashSessions (one per port).
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const txt = (l) => (typeof l === "string" ? l : JSON.stringify(l));
const has = (lines, s) => (lines || []).some((l) => txt(l).includes(s));

function fakePort(vid, pid) {
  return { connected: true, getInfo: () => ({ usbVendorId: vid, usbProductId: pid }) };
}
// A FlashSession stand-in. events: [{op, t, ...}] in call order; emit(line) feeds subscribers.
function fakeSession({ chipName = "ESP32-C3", flashMs = 0, failOpens = 0 } = {}) {
  const subs = new Set();
  const s = {
    events: [], monitoring: false, monitorSpeed: undefined, lastKind: undefined,
    emit(line) { for (const f of [...subs]) f(line); },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
    count(op) { return s.events.filter((e) => e.op === op).length; },
    idx(op) { return s.events.findIndex((e) => e.op === op); },
    async openMonitor({ baud } = {}) {
      s.events.push({ op: "openMonitor", baud, t: Date.now() });
      if (failOpens-- > 0) throw new Error("Failed to open serial port (not ready)");
      s.monitoring = true; s.monitorSpeed = baud;
    },
    async closeMonitor() { s.events.push({ op: "closeMonitor", t: Date.now() }); s.monitoring = false; },
    async resetBoard() { s.events.push({ op: "resetBoard", t: Date.now() }); s.emit("rst:0x1 (POWERON) " + chipName); },
    async flashBoard({ kind, monitorSpeed } = {}) {
      s.events.push({ op: "flash:start", t: Date.now() });
      s.emit("flashing " + chipName);
      await sleep(flashMs);
      s.emit("flashed " + chipName);
      s.lastKind = kind; s.monitoring = true; s.monitorSpeed = monitorSpeed;   // a real session leaves the monitor open
      s.events.push({ op: "flash:end", t: Date.now() });
      return { chip: chipName };
    },
    async identify() { s.events.push({ op: "identify", t: Date.now() }); return { chip: chipName, description: chipName }; },
  };
  return s;
}

const CORE_IDX = {
  boards: [
    { card: "ui", chip: "esp32c3", usb: ["esp-usb"], manifest: "ui/manifest.json", version: "v3", built: "2026-10-20T19:41:00Z" },
    { card: "dosing", chip: "atmega328p", usb: ["arduino"], manifest: "dosing/manifest.json", version: "v1", built: "2026-10-20T19:42:00Z" },
  ],
};
const MANIFESTS = {
  ui: { kind: "esp32", chip: "esp32c3", monitor_speed: 115200, version: "v3", files: [{ offset: "0x0", url: "bootloader.bin" }, { offset: "0x10000", url: "firmware.bin" }] },
  dosing: { kind: "avr", chip: "atmega328p", monitor_speed: 9600, version: "v1", files: [{ url: "firmware.hex" }] },
  master: { kind: "esp32", chip: "esp32", monitor_speed: 115200, version: "v18", files: [{ offset: "0x1000", url: "bootloader.bin" }] },
};
const CHIP_NAME = { "303a": "ESP32-C3", "2341": "ATmega328P", "10c4": "ESP32-D0WD-V3", "1234": "unknown" };

// rig: a core over a mutable port list. opts.sessionOpts(port) -> fakeSession options for that port.
function rig({ ports, idx = CORE_IDX, storage, sessionOpts = () => ({}), ...opts }) {
  let list = ports;
  const sessions = new Map();
  const sess = (p) => {
    if (!sessions.has(p)) {
      const v = p.getInfo().usbVendorId.toString(16).padStart(4, "0");
      sessions.set(p, fakeSession({ chipName: CHIP_NAME[v], ...sessionOpts(p) }));
    }
    return sessions.get(p);
  };
  const logs = [];
  const core = need("createHwCore")({
    serial: { getPorts: async () => list },
    boards: parseIndex(idx),
    getManifest: async (name) => { if (!MANIFESTS[name]) throw new Error("no manifest " + name); return MANIFESTS[name]; },
    sessionFor: sess,
    storage,
    onLog: (name, line) => logs.push([name, txt(line)]),
    onChange: () => {},
    tailMs: 30,
    reopenDelays: [0, 5, 10],
    ...opts,
  });
  return { core, sess, logs, setPorts: (l) => { list = l; } };
}
const memStorage = (init = {}) => { let o = { ...init }; return { get: () => ({ ...o }), set: (x) => { o = { ...x }; } }; };

// ---- 1. several boards at once, no cross-talk
test("core: three boards plugged at once get their own key, card and session; different classes auto-assign different cards", async () => {
  const A = fakePort(0x303a, 0x1001), B = fakePort(0x2341, 0x0043), C = fakePort(0x1234, 0x5678);
  const { core } = rig({ ports: [A, B, C] });
  await core.refreshPorts();
  const ps = core.ports();
  assert.deepEqual(ps.map((p) => p.key), ["303a:1001", "2341:0043", "1234:5678"]);
  assert.deepEqual(ps.map((p) => p.card), ["ui", "dosing", null]);
  assert.deepEqual(ps.map((p) => p.class), ["esp-usb", "arduino", null]);
  assert.equal(core.cardState("ui").connected, true);
  assert.equal(core.cardState("dosing").connected, true);
  assert.equal(core.cardState("port:1234:5678").connected, true);
});

test("core: lines of each port land only in that port's card log (cardState and onLog), never in another's", async () => {
  const A = fakePort(0x303a, 0x1001), B = fakePort(0x2341, 0x0043), C = fakePort(0x1234, 0x5678);
  const { core, sess, logs } = rig({ ports: [A, B, C] });
  await core.refreshPorts();
  await core.monitor("ui", "user");
  await core.monitor("dosing", "user");
  assert.equal((await core.runCmd({ id: 1, op: "monitor", port: "1234:5678" })).ok, true);
  sess(A).emit("LINE-FROM-A"); sess(B).emit("LINE-FROM-B"); sess(C).emit("LINE-FROM-C");
  const ui = core.cardState("ui").lines, dz = core.cardState("dosing").lines, pc = core.cardState("port:1234:5678").lines;
  assert.ok(has(ui, "LINE-FROM-A") && !has(ui, "LINE-FROM-B") && !has(ui, "LINE-FROM-C"), "ui log: " + JSON.stringify(ui));
  assert.ok(has(dz, "LINE-FROM-B") && !has(dz, "LINE-FROM-A") && !has(dz, "LINE-FROM-C"), "dosing log: " + JSON.stringify(dz));
  assert.ok(has(pc, "LINE-FROM-C") && !has(pc, "LINE-FROM-A") && !has(pc, "LINE-FROM-B"), "port log: " + JSON.stringify(pc));
  const routed = (s) => logs.filter(([, l]) => l.includes(s)).map(([n]) => n);
  assert.deepEqual(routed("LINE-FROM-A"), ["ui"]);
  assert.deepEqual(routed("LINE-FROM-B"), ["dosing"]);
  assert.deepEqual(routed("LINE-FROM-C"), ["port:1234:5678"]);
});

test("core: monitor on board B completes while board A is still flashing (not queued); only A is busy; logs stay separate", async () => {
  const A = fakePort(0x303a, 0x1001), B = fakePort(0x2341, 0x0043);
  const { core, sess } = rig({ ports: [A, B], sessionOpts: (p) => (p === A ? { flashMs: 100 } : {}) });
  await core.refreshPorts();
  let flashDone = false, monitorDoneWhileFlashing = null;
  const f = core.flash("ui", "user").then((r) => { flashDone = true; return r; });
  await sleep(15);
  assert.equal(core.cardState("ui").busy, true, "A is busy while flashing");
  assert.equal(core.cardState("dosing").busy, false, "B is not busy");
  await core.monitor("dosing", "user");
  monitorDoneWhileFlashing = !flashDone;
  sess(B).emit("B-MONITOR-LINE");
  assert.equal(monitorDoneWhileFlashing, true, "monitor on B waited for A's flash");
  assert.equal(sess(B).count("openMonitor"), 1);
  await f;
  assert.equal(sess(A).count("flash:end"), 1);
  assert.equal(core.cardState("ui").busy, false);
  assert.ok(has(core.cardState("ui").lines, "flashed ESP32-C3"));
  assert.ok(!has(core.cardState("ui").lines, "B-MONITOR-LINE"));
  assert.ok(has(core.cardState("dosing").lines, "B-MONITOR-LINE"));
  assert.ok(!has(core.cardState("dosing").lines, "ESP32-C3"));
});

test("core.runCmd: monitor on board B resolves before an in-flight flash on board A; both ok", async () => {
  const A = fakePort(0x303a, 0x1001), B = fakePort(0x2341, 0x0043);
  const { core } = rig({ ports: [A, B], sessionOpts: (p) => (p === A ? { flashMs: 100 } : {}) });
  await core.refreshPorts();
  const order = [];
  const pf = core.runCmd({ id: 1, op: "flash", card: "ui" }).then((r) => { order.push("flash"); return r; });
  await sleep(10);
  const pm = core.runCmd({ id: 2, op: "monitor", card: "dosing" }).then((r) => { order.push("monitor"); return r; });
  const [rf, rm] = await Promise.all([pf, pm]);
  assert.equal(rf.ok, true, rf.message); assert.equal(rm.ok, true, rm.message);
  assert.deepEqual(order, ["monitor", "flash"]);
});

test("core.runCmd: two commands for the SAME board run in order: reset waits for the flash, is not refused as busy", async () => {
  const A = fakePort(0x303a, 0x1001);
  const { core, sess } = rig({ ports: [A], sessionOpts: () => ({ flashMs: 60 }) });
  await core.refreshPorts();
  const pf = core.runCmd({ id: 1, op: "flash", card: "ui" });
  const pr = core.runCmd({ id: 2, op: "reset", card: "ui" });
  const [rf, rr] = await Promise.all([pf, pr]);
  assert.equal(rf.ok, true, rf.message);
  assert.equal(rr.ok, true, "reset refused: " + rr.message);
  const s = sess(A);
  assert.ok(s.idx("resetBoard") > s.idx("flash:end"), "reset ran after the flash finished: " + JSON.stringify(s.events.map((e) => e.op)));
});

test("core: two identical boards (same VID:PID, no serial) get keys 303a:1001 and 303a:1001#2; only the first gets the card", async () => {
  const A = fakePort(0x303a, 0x1001), A2 = fakePort(0x303a, 0x1001);
  const { core, sess, logs } = rig({ ports: [A, A2] });
  await core.refreshPorts();
  assert.deepEqual(core.ports().map((p) => [p.key, p.card]), [["303a:1001", "ui"], ["303a:1001#2", null]]);
  assert.equal((await core.runCmd({ id: 1, op: "monitor", port: "303a:1001#2" })).ok, true);
  assert.equal(sess(A2).count("openMonitor"), 1);
  assert.equal(sess(A).count("openMonitor"), 0, "the first board's monitor was not touched");
  sess(A2).emit("SECOND-BOARD-LINE");
  assert.ok(has(core.cardState("port:303a:1001#2").lines, "SECOND-BOARD-LINE"));
  assert.ok(!has(core.cardState("ui").lines, "SECOND-BOARD-LINE"));
  assert.deepEqual(logs.filter(([, l]) => l.includes("SECOND-BOARD-LINE")).map(([n]) => n), ["port:303a:1001#2"]);
});

test("core.runCmd: a port-targeted reset {port:'303a:1001#2'} reaches only the second identical board", async () => {
  const A = fakePort(0x303a, 0x1001), A2 = fakePort(0x303a, 0x1001);
  const { core, sess } = rig({ ports: [A, A2] });
  await core.refreshPorts();
  const r = await core.runCmd({ id: 1, op: "reset", port: "303a:1001#2" });
  assert.equal(r.ok, true, r.message);
  assert.equal(sess(A2).count("resetBoard"), 1);
  assert.equal(sess(A).count("resetBoard"), 0);
});

// ---- 2. replug
// a2: fakeSession options for the re-enumerated port object (e.g. {failOpens: 1}).
async function unplugReplug({ variant = "gone", beforeUnplug = async () => {}, rigOpts = {}, afterUnplug = () => {}, a2 = {} } = {}) {
  const A = fakePort(0x303a, 0x1001), B = fakePort(0x2341, 0x0043), A2 = fakePort(0x303a, 0x1001);
  const r = rig({ ports: [A, B], sessionOpts: (p) => (p === A2 ? a2 : {}), ...rigOpts });
  await r.core.refreshPorts();
  await beforeUnplug(r, A);
  if (variant === "gone") r.setPorts([B]);
  else { A.connected = false; r.setPorts([A, B]); }
  await r.core.refreshPorts();
  const unplugged = { ui: r.core.cardState("ui").connected, dosing: r.core.cardState("dosing").connected };
  afterUnplug(r);
  r.setPorts([A2, B]);
  await r.core.refreshPorts();
  await sleep(60);
  return { ...r, A, A2, B, unplugged };
}

for (const variant of ["gone", "connected:false"]) {
  test(`core replug (${variant}): the card goes not connected, the other board is unaffected, then the card returns on the new port object`, async () => {
    const { core, unplugged } = await unplugReplug({ variant });
    assert.equal(unplugged.ui, false, "ui not connected after unplug");
    assert.equal(unplugged.dosing, true, "dosing unaffected");
    const e = core.ports().find((p) => p.card === "ui");
    assert.ok(e, "ui assigned again");
    assert.equal(e.key, "303a:1001");
    assert.equal(core.cardState("ui").connected, true);
    assert.equal(core.cardState("dosing").connected, true);
  });
  test(`core replug (${variant}): an open monitor is reopened on the new port at the same baud`, async () => {
    let baud;
    const { sess, A2 } = await unplugReplug({ variant, beforeUnplug: async (r, A) => {
      await r.core.monitor("ui", "user");
      baud = r.sess(A).events.find((e) => e.op === "openMonitor").baud;
    } });
    assert.equal(sess(A2).count("openMonitor") >= 1, true, "monitor reopened on A2");
    assert.equal(sess(A2).events.find((e) => e.op === "openMonitor").baud, baud);
    assert.equal(sess(A2).monitoring, true);
  });
}

test("core replug: a monitor closed explicitly before the unplug is NOT reopened", async () => {
  const { sess, A2, core } = await unplugReplug({ beforeUnplug: async (r) => { await r.core.monitor("ui", "user"); await r.core.closeMonitor("ui"); } });
  assert.equal(core.cardState("ui").connected, true);
  assert.equal(sess(A2).count("openMonitor"), 0);
});

test("core replug: an ambiguous card choice remembered in storage survives the replug", async () => {
  const idx = { boards: [
    { card: "master", chip: "esp32", usb: ["usb-uart"], manifest: "master/manifest.json", version: "v18", built: "x" },
    { card: "dosing", chip: "atmega328p", usb: ["usb-uart"], manifest: "dosing/manifest.json", version: "v1", built: "x" },
  ] };
  const P = fakePort(0x10c4, 0xea60), P2 = fakePort(0x10c4, 0xea60);
  const r = rig({ ports: [P], idx, storage: memStorage({ "10c4:ea60": "dosing" }) });
  await r.core.refreshPorts();
  assert.equal(r.core.ports()[0].card, "dosing");
  r.setPorts([]); await r.core.refreshPorts();
  assert.equal(r.core.cardState("dosing").connected, false);
  r.setPorts([P2]); await r.core.refreshPorts();
  assert.equal(r.core.ports()[0].card, "dosing");
  assert.equal(r.core.cardState("dosing").connected, true);
});

test("core replug: after rememberMs the monitor is not reopened (state forgotten), but the card is still auto-matched", async () => {
  let t = 1_000_000;
  const { sess, A2, core } = await unplugReplug({
    rigOpts: { now: () => t, rememberMs: 1000 },
    beforeUnplug: async (r) => { await r.core.monitor("ui", "user"); },
    afterUnplug: () => { t += 5000; },
  });
  assert.equal(core.ports().find((p) => p.key === "303a:1001")?.card, "ui");
  assert.equal(core.cardState("ui").connected, true);
  assert.equal(sess(A2).count("openMonitor"), 0);
});

test("core replug: a reopen that throws once (port not ready) is retried and succeeds", async () => {
  const { sess, A2 } = await unplugReplug({
    a2: { failOpens: 1 },
    beforeUnplug: async (r) => { await r.core.monitor("ui", "user"); },
  });
  assert.ok(sess(A2).count("openMonitor") >= 2, "retried: " + JSON.stringify(sess(A2).events.map((e) => e.op)));
  assert.equal(sess(A2).monitoring, true);
});

// ---- 3. reset log tail
test("core.reset with the monitor CLOSED: opens it before the reset, keeps it open for tailMs, then closes it", async () => {
  const A = fakePort(0x303a, 0x1001);
  const { core, sess } = rig({ ports: [A], tailMs: 30 });
  await core.refreshPorts();
  await core.reset("ui", "user");
  await sleep(80);
  const s = sess(A), ops = s.events.map((e) => e.op);
  assert.ok(s.idx("openMonitor") >= 0 && s.idx("openMonitor") < s.idx("resetBoard"), "openMonitor before resetBoard: " + ops);
  assert.ok(s.idx("closeMonitor") > s.idx("resetBoard"), "closed after the tail: " + ops);
  const held = s.events[s.idx("closeMonitor")].t - s.events[s.idx("resetBoard")].t;
  assert.ok(held >= 25, `monitor held open ${held} ms, tailMs is 30`);
  assert.equal(s.monitoring, false, "previous state (closed) restored");
  assert.ok(has(core.cardState("ui").lines, "POWERON"), "the boot line is in the card log");
});

test("core.reset with the monitor already OPEN: neither closes nor reopens it; still open after tailMs", async () => {
  const A = fakePort(0x303a, 0x1001);
  const { core, sess } = rig({ ports: [A], tailMs: 30 });
  await core.refreshPorts();
  await core.monitor("ui", "user");
  await core.reset("ui", "user");
  await sleep(80);
  const s = sess(A);
  assert.equal(s.count("resetBoard"), 1);
  assert.equal(s.count("openMonitor"), 1, "not reopened: " + s.events.map((e) => e.op));
  assert.equal(s.count("closeMonitor"), 0);
  assert.equal(s.monitoring, true);
});

test("core.reset tail: if the monitor is opened (core.monitor) during the tail, it stays open after the tail", async () => {
  const A = fakePort(0x303a, 0x1001);
  const { core, sess } = rig({ ports: [A], tailMs: 40 });
  await core.refreshPorts();
  const pr = core.reset("ui", "user");
  await sleep(10);
  assert.equal(sess(A).count("resetBoard"), 1, "reset happened");
  await core.monitor("ui", "user");
  await pr;
  await sleep(80);
  assert.equal(sess(A).monitoring, true, "user's monitor survived the tail: " + sess(A).events.map((e) => e.op));
});

// ---- review 2026-10-20: re-enumeration during a flash; one queue per board for port and card commands
test("core replug during a flash: the port that came back gets its monitor once the flash ends", async () => {
  const A = fakePort(0x303a, 0x1001), A2 = fakePort(0x303a, 0x1001);
  const r = rig({ ports: [A], sessionOpts: (p) => (p === A ? { flashMs: 60 } : {}) });
  await r.core.refreshPorts();
  await r.core.monitor("ui", "user");
  const f = r.core.flash("ui", "user");
  await sleep(10);
  r.setPorts([]); await r.core.refreshPorts();
  r.setPorts([A2]); await r.core.refreshPorts();
  await f; await sleep(40);
  assert.equal(r.core.ports()[0].card, "ui");
  assert.equal(r.sess(A2).monitoring, true, "monitor reopened on the re-enumerated port: " + JSON.stringify(r.sess(A2).events.map((e) => e.op)));
});
test("core.runCmd: a --port command and a card command for the same board share one queue (the second waits, is not refused as busy)", async () => {
  const A = fakePort(0x303a, 0x1001);
  const { core, sess } = rig({ ports: [A], sessionOpts: () => ({ flashMs: 50 }) });
  await core.refreshPorts();
  const [rf, rr] = await Promise.all([core.runCmd({ id: 1, op: "flash", card: "ui" }), core.runCmd({ id: 2, op: "reset", port: "303a:1001" })]);
  assert.equal(rf.ok, true, rf.message); assert.equal(rr.ok, true, rr.message);
  assert.ok(sess(A).idx("resetBoard") > sess(A).idx("flash:end"));
});
test("core: an operation on a board that is not connected rejects (never throws synchronously)", async () => {
  const { core } = rig({ ports: [] });
  await core.refreshPorts();
  let p; assert.doesNotThrow(() => { p = core.flash("ui", "user"); });
  await assert.rejects(p);
});
