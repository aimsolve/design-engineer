// Flash + serial monitor over Web Serial (Chrome/Edge). Self-contained ES module, no bundler.
//   flashBoard({kind, files, monitorSpeed?, baud?, onProgress(pct, label), onLog(line)})
//     kind "esp32": files = [{offset: "0x1000"|number, url}], via esptool-js (also one merged bin at 0x0)
//     kind "avr":   files = [{url}] one Intel HEX, STK500v1 (optiboot 115200, old Nano bootloader 57600)
//   openMonitor({baud, onData(line)}) / closeMonitor() / resetBoard() / getLog() / webSerialSupported()   (module level = one default session)
//   new FlashSession(grantedPort) / sessionFor(port): the same, per port (hw.mjs keeps one per card). Pass {port} to the module functions too.
//   flashBoard resolves {chip}.
// After a flash the same port is reopened as a monitor at monitorSpeed (default 115200).
import { DICT } from "./i18n.mjs";

export const ESPTOOL_URL = "https://cdn.jsdelivr.net/npm/esptool-js@0.5.7/+esm";

// ---------- pure helpers (unit-tested) ----------
export function parseHex(text) {          // Intel HEX -> {start, data: Uint8Array} (contiguous, gaps filled with 0xFF)
  const chunks = []; let base = 0, min = Infinity, max = 0;
  for (const raw of text.split(/\r?\n/)) {
    const l = raw.trim(); if (!l) continue;
    if (l[0] !== ":") throw new Error("Bad HEX line: " + l);
    const b = []; for (let i = 1; i + 1 < l.length; i += 2) b.push(parseInt(l.substr(i, 2), 16));
    if (b.some(Number.isNaN) || b.length < 5 || b.length !== b[0] + 5) throw new Error("Bad HEX line: " + l);
    if (b.reduce((s, x) => s + x, 0) & 0xff) throw new Error("HEX checksum mismatch: " + l);
    const addr = (b[1] << 8) | b[2], type = b[3], d = b.slice(4, 4 + b[0]);
    if (type === 0) { const a = base + addr; chunks.push([a, d]); min = Math.min(min, a); max = Math.max(max, a + d.length); }
    else if (type === 1) break;
    else if (type === 2) base = ((d[0] << 8) | d[1]) << 4;
    else if (type === 4) base = ((d[0] << 8) | d[1]) * 65536;
  }
  if (!chunks.length) throw new Error("HEX file has no data");
  const data = new Uint8Array(max - min).fill(0xff);
  for (const [a, d] of chunks) data.set(d, a - min);
  return { start: min, data };
}
const EOP = 0x20;
export const stkLoadAddress = (byteAddr) => { const w = byteAddr >> 1; return [0x55, w & 0xff, (w >> 8) & 0xff, EOP]; };
export const stkProgPage = (bytes) => [0x64, (bytes.length >> 8) & 0xff, bytes.length & 0xff, 0x46, ...bytes, EOP];  // 'F' = flash
export function stkPages(data, start, pageSize = 128) {   // -> [{addr, bytes}] with the last page padded with 0xFF
  const out = [];
  for (let o = 0; o < data.length; o += pageSize) {
    const bytes = Array.from(data.subarray(o, o + pageSize)); while (bytes.length < pageSize) bytes.push(0xff);
    out.push({ addr: start + o, bytes });
  }
  return out;
}

// ---------- helpers ----------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ts = () => new Date().toISOString().slice(11, 23);
export const webSerialSupported = () => typeof navigator !== "undefined" && "serial" in navigator;

// "ESP32-C3 (revision v0.4)" -> "esp32c3"; "ESP32-D0WD-V3" -> "esp32"; anything else -> ""
export function chipFamily(name) {
  const m = /^\s*ESP32(?:-?(S2|S3|C2|C3|C5|C6|H2|P4))?/i.exec(String(name || "")); return m ? "esp32" + (m[1] || "").toLowerCase() : "";
}

// does the connected chip (esptool's name) match the manifest chip ("esp32c3")? Nothing detected never matches.
export const chipMatches = (manifestChip, detected) => { const h = chipFamily(detected); return !!h && h === String(manifestChip || "").toLowerCase(); };
// first-stage bootloader offset: 0x1000 for classic esp32 / s2, 0x0 for the newer families (c3, s3, c6, h2, ...)
export const espBootloaderOffset = (chip) => { const f = chipFamily(chip) || String(chip || "").toLowerCase(); return f === "esp32" || f === "esp32s2" ? 0x1000 : 0; };
// AVR signatures (STK500 READ_SIGN 0x75 -> 3 bytes)
export const AVR_SIGNATURES = { atmega328p: [0x1e, 0x95, 0x0f], atmega328: [0x1e, 0x95, 0x14], atmega2560: [0x1e, 0x98, 0x01] };
export function avrChipFromSignature(sig) { for (const [n, s] of Object.entries(AVR_SIGNATURES)) if (s.every((x, i) => x === sig[i])) return n; return ""; }
const hexSig = (sig) => sig.map((x) => x.toString(16).padStart(2, "0")).join(" ");

// What a person reads when flashing fails; the page swaps in its language (hw.mjs mountHw -> i18n.mjs `flash`). Default: ru, like the page.
const FT = { ...DICT.ru.flash };
export const setFlashTexts = (t) => Object.assign(FT, t);
const tx = (k, o = {}) => String(FT[k]).replace(/\{(\w+)\}/g, (_, n) => o[n]);
// A chip that is not the one the firmware is for (or cannot be told): never retried at another speed, whatever the language.
const fatal = (k, o) => Object.assign(new Error(tx(k, o)), { fatal: true, code: k });

// Another program or tab holds the port: told by the browser's error, never by the (translated) text shown to the person.
export const isBusy = (e) => !!e && (e.busy === true || e.name === "InvalidStateError" || e.name === "NetworkError" ||
  /Failed to open serial port|already open|locked/i.test(String(e.message || e)) || (!!e.cause && e.cause !== e && isBusy(e.cause)));

export function friendlyError(e) {
  const m = String((e && e.message) || e), n = e && e.name;
  if (!webSerialSupported() || m === "NO_WEB_SERIAL") return FT.noSerial;
  if (n === "NotFoundError" || /No port selected/i.test(m)) return FT.noPort;
  if (n === "InvalidStateError" || n === "NetworkError" || /Failed to open serial port|already open|locked/i.test(m))
    return FT.busy;
  if (/sync|Failed to connect|No serial data|timed out|timeout/i.test(m))
    return FT.noBoot;
  if (/Failed to fetch|HTTP \d+/.test(m)) return FT.noFile + m;
  return m;
}

async function fetchBuf(url) {
  const r = await fetch(url); if (!r.ok) throw new Error(`HTTP ${r.status} ${url}`);
  return r.arrayBuffer();
}
// flash speeds to try: the manifest's (if faster) first, then the safe 115200
export const STUB_BAUDS = [460800];   // speeds a stub left running by an earlier failed flash may sit at
export const espBauds = (baud) => { const b = Number(baud) || 115200; return b > 115200 ? [b, 115200] : [115200]; };

const toBinStr = (ab) => { const u = new Uint8Array(ab); let s = ""; for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000)); return s; };

// ---------- one session per port ----------
// new FlashSession(port) works on an already granted SerialPort (navigator.serial.getPorts()); with no port it asks with requestPort() on first use.
// Log lines: getLog(), or subscribe(fn) -> unsubscribe; fn gets "[hh:mm:ss.mmm] text" for notes and for every monitor line.
export class FlashSession {
  constructor(port = null) {
    this.port = port; this.lastKind = "esp32"; this.monitorSpeed = 115200; this.log = []; this.mon = null; this.sink = null; this.subs = new Set(); this.busy = false;
  }
  subscribe(fn) { this.subs.add(fn); return () => this.subs.delete(fn); }
  push(s) { this.log.push(s); if (this.log.length > 5000) this.log.splice(0, this.log.length - 5000); for (const f of this.subs) { try { f(s); } catch {} } }
  note(line) { const s = `[${ts()}] ${line}`; this.push(s); if (this.sink) this.sink(line); }
  getLog() { return this.log.join("\n") + "\n"; }
  clearLog() { this.log = []; }
  get monitoring() { return !!this.mon; }

  async openPort(baud) {
    if (!webSerialSupported()) throw new Error("NO_WEB_SERIAL");
    if (!this.port) this.port = await navigator.serial.requestPort();
    await this.port.open({ baudRate: baud, bufferSize: 4096 });
  }
  async closePort() { try { await this.port.close(); } catch {} }

  // ---------- serial monitor ----------
  async openMonitor({ baud, onData } = {}) {
    if (this.mon) await this.closeMonitor();
    try {
      this.monitorSpeed = baud || this.monitorSpeed;
      await this.openPort(this.monitorSpeed);
      // Web Serial opens with DTR+RTS asserted; on ESP32 USB-Serial/JTAG (C3/S3) that holds the chip in reset/download mode.
      if (this.lastKind !== "avr") await this.port.setSignals({ dataTerminalReady: false, requestToSend: false });
    } catch (e) { throw Object.assign(new Error(friendlyError(e)), { cause: e, busy: isBusy(e) }); }
    this.note(tx("monOpen", { b: this.monitorSpeed }));
    const dec = new TextDecoder(), reader = this.port.readable.getReader(); let buf = "";
    const m = this.mon = { reader, done: null };
    m.done = (async () => {
      try {
        for (;;) {
          const { value, done } = await reader.read(); if (done) break;
          buf += dec.decode(value, { stream: true });
          let i; while ((i = buf.search(/\r?\n/)) >= 0) {
            const line = buf.slice(0, i); buf = buf.slice(buf[i] === "\r" && buf[i + 1] === "\n" ? i + 2 : i + 1);
            const s = `[${ts()}] ${line}`; this.push(s); if (onData) onData(line, s);
          }
        }
      } catch (e) { this.note(tx("monStop", { e: e.message || e })); }
      try { reader.releaseLock(); } catch {}
      if (this.mon === m) { this.mon = null; await this.closePort(); }   // the loop ended by itself (unplug, error): the monitor is dead, don't report it as open
    })();
  }
  async closeMonitor() {
    if (!this.mon) return; const m = this.mon; this.mon = null;
    try { await m.reader.cancel(); } catch {}
    await m.done; await this.closePort();
    this.note(FT.monClosed);
  }
  async resetBoard() {
    if (!this.port) throw new Error(FT.noPortSel);
    const wasOpen = !!this.mon; if (!wasOpen) await this.openPort(this.monitorSpeed);
    const p = this.port;
    if (this.lastKind === "avr") { await p.setSignals({ dataTerminalReady: false, requestToSend: false }); await sleep(250); await p.setSignals({ dataTerminalReady: true, requestToSend: true }); }
    else { await p.setSignals({ dataTerminalReady: false, requestToSend: true }); await sleep(120); await p.setSignals({ dataTerminalReady: false, requestToSend: false }); }
    this.note(FT.resetNote);
    if (!wasOpen) await this.closePort();
  }

  // ---------- flashing ----------
  // -> {chip} (the chip name esptool reported, for esp32; "" otherwise). onLog = the monitor line callback kept after the flash.
  async flashBoard({ kind, files, chip: expectChip, monitorSpeed: ms, baud, onProgress = () => {}, onLog = null }) {
    this.sink = onLog; this.lastKind = kind; this.monitorSpeed = ms || 115200; let chip = "";
    try {
      if (this.mon) await this.closeMonitor();
      if (!webSerialSupported()) throw new Error("NO_WEB_SERIAL");
      if (!files || !files.length) throw new Error(FT.noFiles);
      if (kind === "avr" && expectChip === "atmega2560") throw new Error(FT.avr2560);
      this.note(tx("flashStart", { k: kind }));
      const blobs = []; for (const f of files) blobs.push(await fetchBuf(f.url));   // download before touching the port
      if (!this.port) this.port = await navigator.serial.requestPort();
      if (kind === "esp32") chip = await this.flashEsp(files, blobs, baud, onProgress, expectChip);
      else if (kind === "avr") chip = await this.flashAvr(new TextDecoder().decode(blobs[0]), baud, onProgress, expectChip);
      else throw new Error(tx("unknownKind", { k: kind }));
      onProgress(100, FT.done); this.note(FT.flashDone);
      await this.openMonitor({ baud: this.monitorSpeed, onData: onLog });
      if (kind === "esp32") await this.resetBoard();   // esptool-js hard_reset leaves USB-JTAG chips in download mode
      return { chip };
    } catch (e) {
      const msg = friendlyError(e); this.note(tx("error", { m: msg + (msg !== String(e.message) ? ` (${e.message || e})` : "") }));
      try { await this.closePort(); } catch {}
      const err = new Error(msg); err.cause = e; err.busy = isBusy(e); throw err;
    }
  }

  // esptool connect -> {chip, description}; then the app is restarted (monitor reopened + reset), like after a flash. Only on an explicit command.
  async identify({ monitorSpeed: ms } = {}) {
    this.lastKind = "esp32"; if (ms) this.monitorSpeed = ms;
    try {
      if (this.mon) await this.closeMonitor();
      if (!webSerialSupported()) throw new Error("NO_WEB_SERIAL");
      if (!this.port) this.port = await navigator.serial.requestPort();
      const { ESPLoader, Transport } = await import(ESPTOOL_URL);
      const transport = new Transport(this.port, false), terminal = { clean() {}, write: (s) => this.note(s), writeLine: (s) => this.note(s) };
      const loader = new ESPLoader({ transport, baudrate: 115200, romBaudrate: 115200, terminal });
      let chip, extra = [];
      try {
        this.note(FT.identify); chip = String(await loader.main()); this.note(tx("chip", { c: chip }));
        try { extra.push(`${await loader.detectFlashSize()} flash`); } catch {}
        try { extra.push("MAC " + (await loader.chip.readMac(loader))); } catch {}
        await loader.after("hard_reset");
      } finally { try { await transport.disconnect(); } catch {} }
      await this.openMonitor({ baud: this.monitorSpeed }); await this.resetBoard();
      return { chip: chip.replace(/\s*\(.*$/, ""), description: [chip, ...extra].join(", ") };
    } catch (e) {
      const msg = friendlyError(e); this.note(tx("error", { m: msg })); try { await this.closePort(); } catch {}
      const err = new Error(msg); err.cause = e; err.busy = isBusy(e); throw err;
    }
  }

  async flashEsp(files, blobs, baud, onProgress, expectChip) {
    const bauds = espBauds(baud); let lastErr;
    for (const b of bauds) {
      try { return await this.flashEspAt(b, files, blobs, onProgress, expectChip); }
      catch (e) {
        lastErr = e; const m = String((e && e.message) || e);
        if (b === 115200 || e.fatal || /sync|Failed to connect|No serial data/i.test(m)) throw e;   // fatal (wrong / unknown chip, whatever the language) or no sync: never retried
        this.note(tx("retryBaud", { b, m })); await sleep(300);
      }
    }
    throw lastErr;
  }
  async flashEspAt(baud, files, blobs, onProgress, expectChip) {
    const { ESPLoader, Transport } = await import(ESPTOOL_URL);
    let transport = new Transport(this.port, false);
    const terminal = { clean() {}, write: (s) => this.note(s), writeLine: (s) => this.note(s) };
    let loader = new ESPLoader({ transport, baudrate: baud, romBaudrate: 115200, terminal });
    try {
      this.note(FT.connecting);
      let chip;
      try { chip = await loader.main(); }
      catch (e) {
        // a flash that died after "Changing baudrate" leaves esptool's stub running at that speed, and on some boards
        // DTR/RTS do not reset the chip out of it: talk to that stub without a reset, at the speeds a flash switches to
        if (!/Failed to connect/i.test(String((e && e.message) || e))) throw e;
        for (const sb of STUB_BAUDS) {
          try { await transport.disconnect(); } catch {}
          this.note(tx("stubSearch", { b: sb }));
          transport = new Transport(this.port, false);
          loader = new ESPLoader({ transport, baudrate: baud, romBaudrate: sb, terminal });
          try { chip = await loader.main("no_reset"); loader.IS_STUB = loader.IS_STUB || loader.syncStubDetected; break; } catch { chip = undefined; }
        }
        if (!chip) throw e;
      }
      this.note(tx("chip", { c: chip }));
      // refuse before writing (a wrong-chip image can brick the board), including when the family cannot be determined
      const have = chipFamily(chip);
      if (!have) throw fatal("noFamily", { c: chip });
      if (!expectChip) throw fatal("noChip");
      if (!chipMatches(expectChip, chip)) throw fatal("wrongBoard", { have: chip, want: expectChip });
      const fileArray = files.map((f, i) => ({ data: toBinStr(blobs[i]),
        address: /(^|\/)bootloader\.bin$/.test(f.url || "") ? espBootloaderOffset(have) : typeof f.offset === "number" ? f.offset : parseInt(f.offset || "0", 16) }));
      await loader.writeFlash({
        fileArray, flashSize: "keep", flashMode: "keep", flashFreq: "keep", eraseAll: false, compress: true,
        reportProgress: (i, w, t) => onProgress(Math.round(((i + w / t) / fileArray.length) * 100), tx("fileN", { i: i + 1, n: fileArray.length })),
      });
      await loader.after("hard_reset");
      return String(chip);
    } finally { try { await transport.disconnect(); } catch {} }   // closes the port; the monitor reopens it
  }

  // STK500v1 over Web Serial
  async flashAvr(hexText, baud, onProgress, expectChip) {
    const { start, data } = parseHex(hexText);
    const pages = stkPages(data, start, 128);
    let lastErr;
    for (const b of baud ? [baud] : [115200, 57600]) {
      try { return await this.stkSession(b, pages, onProgress, expectChip); }
      catch (e) { lastErr = e; if (!/sync/i.test(e.message)) throw e; this.note(tx("noAnswerBaud", { b })); }
    }
    throw lastErr;
  }
  async stkSession(baud, pages, onProgress, expectChip) {
    await this.openPort(baud);
    const port = this.port, reader = port.readable.getReader(), writer = port.writable.getWriter(); let rx = [], pump;
    pump = (async () => { try { for (;;) { const { value, done } = await reader.read(); if (done) break; rx.push(...value); } } catch {} })();
    const send = (bytes) => writer.write(new Uint8Array(bytes));
    const take = async (n, ms = 500) => { const t0 = Date.now(); while (rx.length < n) { if (Date.now() - t0 > ms) throw new Error(FT.stkNoAnswer); await sleep(5); } return rx.splice(0, n); };
    const cmd = async (bytes, extra = 0) => {                 // reply = INSYNC(0x14) [extra bytes] OK(0x10)
      rx = []; await send(bytes); const r = await take(2 + extra);
      if (r[0] !== 0x14 || r[r.length - 1] !== 0x10) throw new Error(tx("stkBad", { r: r.map((x) => x.toString(16)).join(" ") }));
      return r;
    };
    try {
      this.note(tx("stkSync", { b: baud }));
      let ok = false;
      for (let i = 0; i < 3 && !ok; i++) {
        await port.setSignals({ dataTerminalReady: false, requestToSend: false }); await sleep(250);
        await port.setSignals({ dataTerminalReady: true, requestToSend: true }); await sleep(60);
        for (let k = 0; k < 8 && !ok; k++) { try { await cmd([0x30, EOP]); ok = true; } catch {} }
      }
      if (!ok) throw new Error("STK500: no sync");
      await cmd([0x50, EOP]);                                  // enter programming mode
      const sig = (await cmd([0x75, EOP], 3)).slice(1, 4), have = avrChipFromSignature(sig);   // READ_SIGN: refuse before writing
      this.note(tx("sig", { s: hexSig(sig) + (have ? ` (${have})` : "") }));
      if (!expectChip || !AVR_SIGNATURES[expectChip]) throw fatal("noSigKnown", { c: expectChip || "?" });
      if (!have) throw fatal("noSigDetect", { s: hexSig(sig) });
      if (have !== expectChip) throw fatal("wrongBoard", { have, want: expectChip });
      for (let i = 0; i < pages.length; i++) {
        await cmd(stkLoadAddress(pages[i].addr)); await cmd(stkProgPage(pages[i].bytes));
        onProgress(Math.round(((i + 1) / pages.length) * 100), tx("pageN", { i: i + 1, n: pages.length }));
      }
      await cmd([0x51, EOP]);                                  // leave programming mode
      return have;
    } finally {
      try { await reader.cancel(); } catch {} await pump;
      try { reader.releaseLock(); writer.releaseLock(); } catch {}
      await this.closePort();
    }
  }
}

// ---------- module-level API (one default session; it asks for its port with requestPort) ----------
const WS = new WeakMap();
export const sessionFor = (port) => { let s = WS.get(port); if (!s) WS.set(port, (s = new FlashSession(port))); return s; };   // one session per granted port
let def = new FlashSession();
const sess = (opts) => (opts && opts.port ? sessionFor(opts.port) : def);
export const getLog = () => def.getLog();
export const clearLog = () => def.clearLog();
export const openMonitor = (o) => sess(o).openMonitor(o);
export const closeMonitor = (o) => sess(o).closeMonitor();
export const resetBoard = (o) => sess(o).resetBoard();
export const flashBoard = (o) => sess(o).flashBoard(o);
