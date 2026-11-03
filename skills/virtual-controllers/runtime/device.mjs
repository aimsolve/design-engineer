// A virtual device from build/model/device.json: boards running their real firmware + the chips wired to them
// (runtime/chips.mjs), with no hand-written machine.mjs. Isomorphic (node + browser).
//
//   deviceMeta(device)  -> META for sim_ui / VM: {device: true, boards: [{id, name, mcu, fw, about, cad}], elements, inputs}
//   buildDevice({vc, loadFirmware, device, firmware}) -> the machine VM runs: {sim, status, elements, chips, down(id), up(id),
//       press(id, ms), turn(id, n), display(id), pinTable(board), actuators(), sample(), start(), stop(), setTarget(), dispose()}
// device.json is derived from the model (machine-project scripts/device.py): element ports are {board, pin} | {rail} | null.
import { Button, Encoder, MipiDisplay, Ssd1306, DISPLAY_MODELS } from './chips.mjs';

// The texts table is optional: a site built without device_i18n.mjs gets the English fallback, never a failed import.
let TEXTS = null;
try { ({ TEXTS } = await import('./device_i18n.mjs')); } catch { TEXTS = null; }
const FALLBACK = { keyHint: 'key {key}' };
function texts() {
  const lang = (globalThis.document && globalThis.document.documentElement && globalThis.document.documentElement.lang) || 'ru';
  return (TEXTS && (TEXTS[lang] || TEXTS.ru)) || FALLBACK;
}

const MCU_NAME = { esp32: 'ESP32', avr: 'ATmega328P' };
// Wire.begin() without pins: the core's defaults (ESP32 SDA 21 / SCL 22, AVR A4 / A5)
const WIRE_DEFAULT = { esp32: { sda: 21, scl: 22 }, avr: { sda: 18, scl: 19 } };

const pinOf = (port) => (port && port.board != null ? port : null);
const label = (el) => (el.params && el.params.label) || el.id;

export function deviceMeta(device) {
  const elements = device.elements || [];
  const boards = (device.boards || []).map((b) => ({
    id: b.id, name: `${MCU_NAME[b.model] || b.model} — ${b.id}`, mcu: b.model, fw: b.fw,
    about: elements.filter((e) => e.board === b.id).map(label).join(', '), cad: b.part ? [{ part: b.part }] : null,
  }));
  const inputs = [];
  for (const e of elements) {
    if (e.model !== 'button') continue;
    const c = new Button({ board: () => null, at() {}, t: 0 }, e);
    if (c.pin) inputs.push({ id: e.id, label: label(e), board: c.pin.board, pin: c.pin.pin, active: c.active, hint: e.params && e.params.key ? texts().keyHint.replace('{key}', e.params.key) : '' });
  }
  return { id: 'device', device: true, boards, elements, inputs };
}

// a display on a board: takes the SPI bytes on its own pins (SCK / MOSI as the firmware configured them, CS low, DC level), resets on RST,
// darkens while BLK is driven LOW
function wireMipi(sim, el, d) {
  const p = el.ports || {}, scl = pinOf(p.SCL), sda = pinOf(p.SDA), dc = pinOf(p.DC), cs = pinOf(p.CS), rst = pinOf(p.RST), blk = pinOf(p.BLK);
  if (!scl || !sda) return;
  const board = sim.board(scl.board);
  sim.spiDevs.push({
    board, id: el.id,
    spi(h, bytes, t) {
      if (h.sck !== scl.pin || h.mosi !== sda.pin) return;
      if (cs && board.level(cs.pin) !== 0) return;
      d.spi(bytes, dc ? board.level(dc.pin) : 1, t);
    },
  });
  watchPins(sim, board, rst, blk, d);
}

function wireSsd1306(sim, el, d) {
  const p = el.ports || {}, scl = pinOf(p.SCL), sda = pinOf(p.SDA);
  if (!scl || !sda) return;
  const board = sim.board(scl.board);
  sim.i2cDevs.push({
    board, id: el.id, addr: d.addr,
    reachable() { const w = board.i2cPins || WIRE_DEFAULT[board.kind] || WIRE_DEFAULT.esp32; return w.sda === sda.pin && w.scl === scl.pin; },
    write(bytes) { d.i2cWrite(bytes); },
    read(n) { return d.i2cRead(n); },
  });
}

function watchPins(sim, board, rst, blk, d) {
  if (!rst && !blk) return;
  sim.on('pin', (e) => {
    if (e.board !== board.name) return;
    if (rst && e.pin === rst.pin && e.val === 0 && !e.init) d.reset();
    if (blk && e.pin === blk.pin) d.setBacklight(e.val !== 0);    // lit unless the firmware drives BLK low (modules pull it up)
  });
}

export async function buildDevice({ vc, loadFirmware, device, firmware = {}, quantumUs = 50, quantumMaxUs = 1000 }) {
  const sim = new vc.Sim({ quantumUs, quantumMaxUs });
  for (const b of device.boards || []) {
    const fw = firmware[b.id] || b.fw;
    const bd = await sim.addBoard(b.id, await loadFirmware(fw), b.model === 'avr' ? 'avr' : 'esp32');
    bd.fw = fw;
  }
  const chips = {};
  for (const el of device.elements || []) {
    if (el.model === 'button') chips[el.id] = new Button(sim, el);
    else if (el.model === 'encoder') chips[el.id] = new Encoder(sim, el);
    else if (el.model === 'ssd1306') { chips[el.id] = new Ssd1306(sim, el); wireSsd1306(sim, el, chips[el.id]); }
    else if (DISPLAY_MODELS.has(el.model)) { chips[el.id] = new MipiDisplay(sim, el); wireMipi(sim, el, chips[el.id]); }
  }
  const chip = (id) => { const c = chips[id]; if (!c) throw new Error(`no device element ${id}`); return c; };
  const status = { ready: true };
  return {
    sim, status, chips, elements: device.elements || [],
    down: (id) => chip(id).down(), up: (id) => chip(id).up(),
    press(id, ms = 150) { chip(id).down(); sim.at(sim.t + ms * 1000, () => chip(id).up()); },
    turn: (id, n) => chip(id).turn(n),
    display: (id) => chip(id),
    displays: () => Object.values(chips).filter((c) => c.fb),
    // the board card's pin table: every element port wired to this board, with the level the firmware reads / drives
    pinTable(boardId) {
      const b = sim.board(boardId), rows = [];
      for (const el of device.elements || []) for (const [port, p] of Object.entries(el.ports || {})) {
        if (p && p.board === boardId) rows.push({ pin: p.pin, name: b.kind === 'avr' ? `D${p.pin}` : `GPIO${p.pin}`, label: `${label(el)} · ${port}`, level: b.level(p.pin) });
      }
      return rows.sort((x, y) => x.pin - y.pin);
    },
    actuators: () => ({}), sample() {}, start() {}, stop() {}, setTarget() {}, dispose() {},
  };
}
