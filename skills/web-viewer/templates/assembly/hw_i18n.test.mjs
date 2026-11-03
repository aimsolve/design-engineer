// A board's port held by another program, in every page language.
// The viewer tells the router which granted ports it really holds (ports event, held: false = another program / tab has it open).
// That mark must not depend on the language of the error text: with the page in en / pl the REAL FlashSession throws a translated
// «port busy» message, and the core must still recognise it.
//
//   node --test hw_i18n.test.mjs                                   (this folder's hw.mjs / flash.mjs / i18n.mjs)
//   VIEWER_DIR=<another assembly folder> node --test hw_i18n.test.mjs
//
// The ports payload is held = !entry.lockedByOther || entry.session.monitoring (hw.mjs mountHw portsPayload); this file checks the
// core's side of it; test_i18n_viewer_more.py checks the payload the page really posts.
import test from "node:test";
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const BASE = process.env.VIEWER_DIR ? pathToFileURL(process.env.VIEWER_DIR.replace(/\/?$/, "/")) : new URL("./", import.meta.url);
const FL = await import(new URL("flash.mjs", BASE).href);
const HW = await import(new URL("hw.mjs", BASE).href);
const { DICT, LANGS } = await import(new URL("i18n.mjs", BASE).href);

Object.defineProperty(globalThis, "navigator", { value: { serial: { requestPort: async () => { throw new Error("no"); } } }, configurable: true, writable: true });

// a CP2102 (usb-uart) port that another program holds: open() fails exactly like Chrome's Web Serial does
function busyPort() {
  return {
    connected: true,
    getInfo: () => ({ usbVendorId: 0x10c4, usbProductId: 0xea60 }),
    open: async () => { throw Object.assign(new Error("Failed to open serial port."), { name: "InvalidStateError" }); },
    close: async () => {}, setSignals: async () => {},
  };
}
const BOARDS = [{ card: "master", chip: "esp32", usb: ["usb-uart"], manifest: "master/manifest.json", version: "b7efe4e 20261020-2003" }];
const MANIFEST = { kind: "esptool", chip: "esp32", monitor_speed: 115200, files: [{ offset: "0x10000", url: "firmware.bin" }] };

for (const lang of LANGS) {
  test(`${lang}: a port another program holds is marked held: false after a failed monitor`, async () => {
    FL.setFlashTexts(DICT[lang].flash);
    const port = busyPort();
    const core = HW.createHwCore({ serial: { getPorts: async () => [port] }, boards: BOARDS, getManifest: async () => MANIFEST,
      sessionFor: (p) => new FL.FlashSession(p), reopenDelays: [] });
    await core.refreshPorts();
    assert.equal(core.entries.length, 1, "the granted port is known");
    const e = core.entries[0];
    await assert.rejects(core.monitor(e.card, "user", { port: e.key }), "opening the monitor on a busy port fails");
    assert.equal(e.session.monitoring, false);
    const held = !e.lockedByOther || e.session.monitoring;   // what the ports event reports for this port
    assert.equal(held, false, `${lang}: the port is reported as held by another program (held: false), whatever the error's language`);
  });

  test(`${lang}: a port that opens is not marked as held by another program`, async () => {
    FL.setFlashTexts(DICT[lang].flash);
    let ctl;
    const port = { connected: true, getInfo: () => ({ usbVendorId: 0x10c4, usbProductId: 0xea60 }), open: async () => {}, close: async () => {},
      setSignals: async () => {}, readable: new ReadableStream({ start(c) { ctl = c; } }) };
    const core = HW.createHwCore({ serial: { getPorts: async () => [port] }, boards: BOARDS, getManifest: async () => MANIFEST,
      sessionFor: (p) => new FL.FlashSession(p), reopenDelays: [] });
    await core.refreshPorts();
    const e = core.entries[0];
    await core.monitor(e.card, "user", { port: e.key });
    assert.equal(!e.lockedByOther || e.session.monitoring, true, "held: true for a port this tab has open");
    await core.closeMonitor(e.card, { port: e.key }).catch(() => {});   // cancels the reader: the stream is closed by that
    try { ctl.close(); } catch {}
  });
}
