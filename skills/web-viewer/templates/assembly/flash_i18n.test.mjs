// Board flashing texts in en / pl: every message the flasher itself produces (friendlyError, the
// errors it throws, the log lines it writes) is in the page language once the page called setFlashTexts(DICT[lang].flash), and the
// ESP32 speed retry stops on a wrong-board error in every language (it must not depend on the Russian text).
//
//   node --test flash_i18n.test.mjs                                  (this folder's flash.mjs / i18n.mjs)
//   VIEWER_DIR=<another assembly folder> node --test flash_i18n.test.mjs
//
// No browser, no serial port, no network: navigator.serial and fetch are fakes; esptool-js (imported by flash.mjs from the CDN) is
// replaced through a module resolve hook by a fake whose ESPLoader reports the chip a test asks for and records its baud rates;
// the STK500 (AVR) path talks to a fake port that answers like a bootloader with a chosen signature, or not at all.
import test from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";
import { pathToFileURL } from "node:url";

const FAKE_ESPTOOL = `
export class Transport { constructor(port) { this.port = port; } async disconnect() {} }
export class ESPLoader {
  constructor(o) { this.o = o; (globalThis.__esp.bauds ||= []).push(o.baudrate); }
  async main() { const e = globalThis.__esp; if (e.fail) throw new Error(e.fail); return e.chip; }
  async writeFlash() {} async after() {}
}`;
register("data:text/javascript," + encodeURIComponent(`
const FAKE = ${JSON.stringify("data:text/javascript," + encodeURIComponent(FAKE_ESPTOOL))};
export async function resolve(spec, ctx, next) {
  if (spec.startsWith("https://cdn.jsdelivr.net/npm/esptool-js")) return { url: FAKE, shortCircuit: true };
  return next(spec, ctx);
}`));

const BASE = process.env.VIEWER_DIR ? pathToFileURL(process.env.VIEWER_DIR.replace(/\/?$/, "/")) : new URL("./", import.meta.url);
const FL = await import(new URL("flash.mjs", BASE).href);
const { DICT, LANGS } = await import(new URL("i18n.mjs", BASE).href);
const CYR = /[\u0400-\u04FF]/;
const LANG = ["en", "pl"];

// ---------------------------------------------------------------- fakes
const setNavigator = (v) => Object.defineProperty(globalThis, "navigator", { value: v, configurable: true, writable: true });
const withSerial = () => setNavigator({ serial: { requestPort: async () => { throw Object.assign(new Error("No port selected by the user."), { name: "NotFoundError" }); } } });
const HEX = ":0400000001020304F2\n:00000001FF\n";   // the AVR path downloads fw.hex, the ESP path app.bin: both through fetch
globalThis.fetch = async (url) => ({ ok: true, status: 200, url,
  arrayBuffer: async () => (String(url).endsWith(".hex") ? new TextEncoder().encode(HEX).buffer : new Uint8Array(64).buffer) });
function avrPort(sig) {
  // sig = null: a bootloader that never answers; else answers sync / enter / read-signature like optiboot
  let ctl;
  const readable = new ReadableStream({ start(c) { ctl = c; } });
  const writable = new WritableStream({
    write(chunk) {
      if (!sig) return;
      const b = [...chunk];
      if (b[0] === 0x75) ctl.enqueue(new Uint8Array([0x14, ...sig, 0x10]));
      else ctl.enqueue(new Uint8Array([0x14, 0x10]));
    },
  });
  return { readable, writable, open: async () => {}, close: async () => {}, setSignals: async () => {}, getInfo: () => ({}) };
}
const espPort = () => ({ open: async () => {}, close: async () => {}, setSignals: async () => {}, getInfo: () => ({}) });

function useLang(lang) {
  assert.ok(DICT[lang] && DICT[lang].flash, `i18n.mjs DICT.${lang}.flash exists`);
  FL.setFlashTexts(DICT[lang].flash);
}

async function failure(lang, session, opts) {
  useLang(lang); withSerial();
  let err = null;
  try { await session.flashBoard(opts); } catch (e) { err = e; }
  assert.ok(err, "the flash fails");
  return { msg: String(err.message || err), log: session.getLog() };
}

const noCyr = (what, s) => assert.ok(!CYR.test(s), `${what}: no Cyrillic, got ${JSON.stringify(s)}`);
const logLines = (log) => log.split("\n").filter(Boolean).join("\n");

// ---------------------------------------------------------------- friendlyError: every branch
test("the dictionaries have a flash section in every language, with the same keys", () => {
  const keys = (l) => Object.keys(DICT[l].flash || {}).sort();
  for (const l of LANGS) assert.ok(keys(l).length, `DICT.${l}.flash`);
  assert.deepEqual(keys("en"), keys("ru")); assert.deepEqual(keys("pl"), keys("ru"));
});

for (const lang of LANG) {
  test(`${lang}: every friendlyError branch is in ${lang}`, () => {
    useLang(lang); withSerial();
    const cases = {
      "no port chosen (NotFoundError)": Object.assign(new Error("No port selected by the user."), { name: "NotFoundError" }),
      "port busy": new Error("Failed to open serial port."),
      "no bootloader": new Error("Failed to connect with the device"),
      "download failed": new Error("HTTP 404 https://x/fw/master/firmware.bin"),
    };
    const seen = new Set();
    for (const [what, e] of Object.entries(cases)) { const m = FL.friendlyError(e); noCyr(what, m); seen.add(m); }
    assert.equal(seen.size, Object.keys(cases).length, "each branch says something different");
    setNavigator({});   // a browser without Web Serial
    noCyr("no Web Serial", FL.friendlyError(new Error("x")));
    withSerial();
  });

  // ---------------------------------------------------------------- errors the flasher throws, and its log
  test(`${lang}: a manifest without files`, async () => {
    const { msg, log } = await failure(lang, new FL.FlashSession(espPort()), { kind: "esp32", files: [], chip: "esp32" });
    noCyr("error", msg); noCyr("log", logLines(log));
  });

  test(`${lang}: an unknown board type`, async () => {
    const { msg, log } = await failure(lang, new FL.FlashSession(espPort()), { kind: "pic16", files: [{ url: "fw.bin" }], chip: "pic16" });
    noCyr("error", msg); noCyr("log", logLines(log));
  });

  test(`${lang}: ESP32 wrong board (on the port ESP32-C3, firmware for esp32)`, async () => {
    globalThis.__esp = { chip: "ESP32-C3" };
    const { msg, log } = await failure(lang, new FL.FlashSession(espPort()), { kind: "esp32", files: [{ url: "app.bin", offset: "0x10000" }], chip: "esp32" });
    noCyr("error", msg); noCyr("log", logLines(log));
    assert.match(msg, /ESP32-C3/); assert.match(msg, /esp32\b/);
  });

  test(`${lang}: ESP32 chip family not detected`, async () => {
    globalThis.__esp = { chip: "Mystery-9000" };
    const { msg, log } = await failure(lang, new FL.FlashSession(espPort()), { kind: "esp32", files: [{ url: "app.bin", offset: "0x10000" }], chip: "esp32" });
    noCyr("error", msg); noCyr("log", logLines(log));
    assert.match(msg, /Mystery-9000/);
  });

  test(`${lang}: ESP32 manifest without a chip`, async () => {
    globalThis.__esp = { chip: "ESP32-D0WD-V3" };
    const { msg, log } = await failure(lang, new FL.FlashSession(espPort()), { kind: "esp32", files: [{ url: "app.bin", offset: "0x10000" }] });
    noCyr("error", msg); noCyr("log", logLines(log));
  });

  test(`${lang}: AVR wrong board (signature of atmega328, firmware for atmega328p)`, async () => {
    const { msg, log } = await failure(lang, new FL.FlashSession(avrPort([0x1e, 0x95, 0x14])), { kind: "avr", files: [{ url: "fw.hex" }], chip: "atmega328p", baud: 115200 });
    noCyr("error", msg); noCyr("log", logLines(log));
  });

  test(`${lang}: AVR bootloader never answers (STK500)`, { timeout: 60000 }, async () => {
    const { msg, log } = await failure(lang, new FL.FlashSession(avrPort(null)), { kind: "avr", files: [{ url: "fw.hex" }], chip: "atmega328p", baud: 115200 });
    noCyr("error", msg); noCyr("log", logLines(log));
  });

  test(`${lang}: reset with no port chosen`, async () => {
    useLang(lang); withSerial();
    await assert.rejects(new FL.FlashSession(null).resetBoard(), (e) => { noCyr("error", String(e.message)); return true; });
  });
}

// ---------------------------------------------------------------- the speed retry stops on a wrong board, in every language
for (const lang of LANGS) {
  test(`${lang}: wrong board at 460800 is not retried at 115200`, async () => {
    globalThis.__esp = { chip: "ESP32-C3", bauds: [] };
    await failure(lang, new FL.FlashSession(espPort()), { kind: "esp32", files: [{ url: "app.bin", offset: "0x10000" }], chip: "esp32", baud: 460800 });
    assert.deepEqual(globalThis.__esp.bauds, [460800], "one attempt: a wrong board is final, whatever language the text is in");
  });
}
