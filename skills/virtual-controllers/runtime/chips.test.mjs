// Chip models of a virtual device, tested standalone — no wasm, no firmware.
// Written from the spec before the implementation exists.
//
//   node --test agent/skills/virtual-controllers/runtime/chips.test.mjs
//
// Contract under test (runtime/chips.mjs, each class constructed with (sim, element), element = one device.json element):
//   element = {id, part, model, board, params: {<sim minus model>}, ports: {<port>: {board, pin} | {rail: "gnd"|"vcc"} | null}}
//   Button(sim, el)   down() / up() at sim.t. It forces the MCU pin through the runtime's world API Board.drive(pin, v)
//                     (SKILL.md: "board.drive(pin, val) forces an input"): pressed = the active level, released = drive(pin, undefined)
//                     (pin not driven: the firmware's pinMode default decides). Active level from the wiring: other contact on
//                     rail gnd -> 0, on rail vcc -> 1. Contact bounce ON by default (several edges within a few ms on press and
//                     on release, scheduled with sim.at), deterministic for the same element id and press time; params.bounce_ms
//                     bounds it, bounce_ms: 0 = exactly one edge.
//   Encoder(sim, el)  turn(n): n clicks, n > 0 = CW (A leads B), n < 0 = CCW (B leads A); params.detent quadrature transitions per
//                     click (default 4); contacts A/B to common C (rail gnd -> active low). down() / up() = the push contact SW/SW2.
//   MipiDisplay(sim, el)  one model for st7789 / st7735 / ili9341 (params w, h, x_off, y_off). spi(bytes, dc, t): bytes on the bus
//                     while CS is low, dc = level of DC (0 = command, 1 = data). fb = RGBA Uint8ClampedArray w*h*4: the picture the
//                     glass SHOWS (black while asleep / display off; RAM is kept). on = shows a picture. frames = counts pixel writes
//                     (grows on RAMWR data).
//   Ssd1306(sim, el)  i2cWrite(bytes): one I2C write to its address; bytes[0] = control byte 0x00 (commands) / 0x40 (data).
//                     fb RGBA w*h*4 (shown picture), on.
// Test names carry the "done when" they serve: [3] button / encoder model, [4] display model.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Sim } from './sim.mjs';

const chips = () => import('./chips.mjs');   // inside each test: a missing module fails every test, not the file load

// ---------------------------------------------------------------- a sim with one fake MCU that records what the chips force on its pins

function rig() {
  const sim = new Sim();
  const log = [];
  const board = {
    name: 'mcu', wake: Infinity, dead: false, yields: 0, irq: new Map(), mode: new Uint8Array(64), outVal: new Uint8Array(64), ext: new Array(64).fill(undefined),
    drive(pin, v) { this.ext[pin] = v === undefined ? undefined : v ? 1 : 0; log.push({ t: sim.t, pin, v: this.ext[pin] }); },
    level(pin) { return this.ext[pin]; }, refresh() {}, poke() {}, mail() {},
  };
  sim.boards.set('mcu', board);
  return { sim, log, board };
}

const pin = (p) => ({ board: 'mcu', pin: p });
const button = (extra = {}, ports = { 1: pin(27), 2: { rail: 'gnd' } }) => ({ id: 'btn_play', part: 'tact', model: 'button', board: 'mcu', params: { ...extra }, ports });
const encoder = (extra = {}, ports = { A: pin(32), B: pin(33), C: { rail: 'gnd' } }) => ({ id: 'enc', part: 'ec11', model: 'encoder', board: 'mcu', params: { ...extra }, ports });

// what the firmware reads on a pin: forced level, or the pull when released (pull-up for a contact to GND, pull-down for one to 3V3)
const eff = (v, pull) => (v === undefined ? pull : v);
// effective level changes of one pin in a slice of the drive log, starting from the level before the slice
function edges(log, p, t0, t1, start, pull) {
  let lv = start; const out = [];
  for (const e of log) if (e.pin === p && e.t >= t0 && e.t <= t1) { const v = eff(e.v, pull); if (v !== lv) { out.push({ t: e.t, v }); lv = v; } }
  return out;
}
const lastDrive = (log, p, t1 = Infinity) => { const l = log.filter((e) => e.pin === p && e.t <= t1); return l.length ? l[l.length - 1] : null; };

// ---------------------------------------------------------------- [3] Button

test('[3] button: bounce is ON by default — several edges within a few ms on press, ends pressed (pin LOW for a contact to GND)', async () => {
  const { Button } = await chips();
  const { sim, log } = rig();
  const b = new Button(sim, button());
  sim.run(10_000);
  const t0 = sim.t;
  b.down();
  sim.run(t0 + 100_000);
  const e = edges(log, 27, t0, t0 + 100_000, 1, 1);
  assert.ok(e.length >= 3, `press: expected several bounce edges, got ${e.length}: ${JSON.stringify(e)}`);
  assert.equal(e.length % 2, 1, 'press: an odd number of level changes (ends opposite to where it started)');
  assert.equal(e[e.length - 1].v, 0, 'press ends with the pin LOW');
  assert.ok(e.every((x) => x.t >= t0 && x.t <= t0 + 10_000), `press: every edge within 10 ms of the press: ${JSON.stringify(e)}`);
  assert.ok(new Set(e.map((x) => x.t)).size > 1, 'bounce edges are spread in time, not all at one instant (firmware must be able to see them)');
});

test('[3] button: bounce on release too — several edges, then the pin is NOT driven (released = pinMode default)', async () => {
  const { Button } = await chips();
  const { sim, log } = rig();
  const b = new Button(sim, button());
  sim.run(10_000); b.down(); sim.run(200_000);
  const t1 = sim.t;
  b.up();
  sim.run(t1 + 100_000);
  const e = edges(log, 27, t1, t1 + 100_000, 0, 1);
  assert.ok(e.length >= 3, `release: expected several bounce edges, got ${e.length}: ${JSON.stringify(e)}`);
  assert.equal(e[e.length - 1].v, 1, 'release ends at the pull-up level');
  assert.ok(e.every((x) => x.t <= t1 + 10_000), `release: every edge within 10 ms: ${JSON.stringify(e)}`);
  const last = lastDrive(log, 27);
  assert.equal(last.v, undefined, `released = pin not driven (drive(pin, undefined)), last drive was ${JSON.stringify(last)}`);
});

test('[3] button: bounce is deterministic for the same element id and press time', async () => {
  const { Button } = await chips();
  const run = () => {
    const { sim, log } = rig();
    const b = new Button(sim, button());
    sim.run(123_000); b.down(); sim.run(400_000); b.up(); sim.run(600_000);
    return log.map((e) => [e.t, e.pin, e.v]);
  };
  const a = run(), c = run();
  assert.ok(a.length > 2, 'the press produced edges');
  assert.deepEqual(a, c);
});

test('[3] button: params.bounce_ms bounds the bounce (bounce_ms 2 -> every edge within 2 ms of the press / release)', async () => {
  const { Button } = await chips();
  const { sim, log } = rig();
  const b = new Button(sim, button({ bounce_ms: 2 }));
  sim.run(10_000); const t0 = sim.t; b.down(); sim.run(t0 + 50_000);
  const t1 = sim.t; b.up(); sim.run(t1 + 50_000);
  const p = edges(log, 27, t0, t0 + 50_000, 1, 1), r = edges(log, 27, t1, t1 + 50_000, 0, 1);
  assert.ok(p.length >= 1 && p.every((x) => x.t <= t0 + 2000), `press edges ${JSON.stringify(p)}`);
  assert.ok(r.length >= 1 && r.every((x) => x.t <= t1 + 2000), `release edges ${JSON.stringify(r)}`);
});

test('[3] button: bounce_ms 0 gives exactly one edge on press and one on release, at the press / release time', async () => {
  const { Button } = await chips();
  const { sim, log } = rig();
  const b = new Button(sim, button({ bounce_ms: 0 }));
  sim.run(10_000); const t0 = sim.t; b.down(); sim.run(t0 + 50_000);
  const p = log.filter((e) => e.pin === 27);
  assert.deepEqual(p.map((e) => [e.t, e.v]), [[t0, 0]], 'one drive LOW at the press time');
  const t1 = sim.t; b.up(); sim.run(t1 + 50_000);
  const r = log.filter((e) => e.pin === 27 && e.t >= t1);
  assert.deepEqual(r.map((e) => [e.t, e.v]), [[t1, undefined]], 'one release (not driven) at the release time');
});

test('[3] button: the other contact on 3V3 -> pressing drives the pin HIGH; released still = not driven', async () => {
  const { Button } = await chips();
  const { sim, log } = rig();
  const b = new Button(sim, button({ bounce_ms: 0 }, { 1: pin(26), 2: { rail: 'vcc' } }));
  sim.run(1000); b.down(); sim.run(20_000);
  assert.equal(lastDrive(log, 26).v, 1);
  b.up(); sim.run(40_000);
  assert.equal(lastDrive(log, 26).v, undefined);
});

test('[3] button: which contact is the pin and which the rail does not matter', async () => {
  const { Button } = await chips();
  const { sim, log } = rig();
  const b = new Button(sim, button({ bounce_ms: 0 }, { 1: { rail: 'gnd' }, 2: pin(14) }));
  sim.run(1000); b.down(); sim.run(20_000);
  assert.equal(lastDrive(log, 14).v, 0);
});

test('[3] button: stays pressed exactly as long as it is held (no auto-release), a long press is just a long hold', async () => {
  const { Button } = await chips();
  const { sim, log } = rig();
  const b = new Button(sim, button());
  sim.run(10_000); const t0 = sim.t; b.down();
  sim.run(t0 + 3_000_000);
  const settled = log.filter((e) => e.pin === 27 && e.t > t0 + 20_000);
  assert.deepEqual(settled, [], `nothing happens on the pin while it is held: ${JSON.stringify(settled)}`);
  assert.equal(eff(lastDrive(log, 27).v, 1), 0, 'still LOW after 3 s of holding');
  const t1 = sim.t; b.up(); sim.run(t1 + 50_000);
  assert.equal(lastDrive(log, 27).v, undefined);
});

// ---------------------------------------------------------------- [3] Encoder

// standard full-state quadrature decoder, state = A<<1 | B; CW = A leads: 11 -> 01 -> 00 -> 10 -> 11 (+1 each)
const CW = { '3>1': 1, '1>0': 1, '0>2': 1, '2>3': 1, '1>3': -1, '0>1': -1, '2>0': -1, '3>2': -1 };
function decode(log, t0, t1) {
  let a = 1, b = 1, st = 3, n = 0; const order = [];
  for (const e of log) {
    if (e.t < t0 || e.t > t1 || (e.pin !== 32 && e.pin !== 33)) continue;
    const v = eff(e.v, 1);
    if (e.pin === 32) { if (v === a) continue; a = v; order.push('A'); } else { if (v === b) continue; b = v; order.push('B'); }
    const ns = (a << 1) | b; n += CW[`${st}>${ns}`] || 0; st = ns;
  }
  return { n, order, a, b };
}

test('[3] encoder: one CW click = 4 quadrature transitions (detent default 4), A leads B, back to rest', async () => {
  const { Encoder } = await chips();
  const { sim, log } = rig();
  const enc = new Encoder(sim, encoder());
  sim.run(10_000); const t0 = sim.t;
  enc.turn(1);
  sim.run(t0 + 500_000);
  const d = decode(log, t0, Infinity);
  assert.equal(d.n, 4, `decoded ${d.n} quarter-steps, order ${d.order.join('')}`);
  assert.equal(d.order[0], 'A', 'CW: A changes first');
  assert.deepEqual([d.a, d.b], [1, 1], 'a detent-4 encoder rests with both contacts open (pull-ups read 1)');
});

test('[3] encoder: CCW click = -4, B leads A', async () => {
  const { Encoder } = await chips();
  const { sim, log } = rig();
  const enc = new Encoder(sim, encoder());
  sim.run(10_000); const t0 = sim.t;
  enc.turn(-1);
  sim.run(t0 + 500_000);
  const d = decode(log, t0, Infinity);
  assert.equal(d.n, -4, `order ${d.order.join('')}`);
  assert.equal(d.order[0], 'B', 'CCW: B changes first');
});

test('[3] encoder: turn(3) then turn(-2) nets one click CW; transitions are spread in time', async () => {
  const { Encoder } = await chips();
  const { sim, log } = rig();
  const enc = new Encoder(sim, encoder());
  sim.run(10_000); const t0 = sim.t;
  enc.turn(3); sim.run(t0 + 1_000_000);
  const t1 = sim.t;
  assert.equal(decode(log, t0, t1).n, 12);
  enc.turn(-2); sim.run(t1 + 1_000_000);
  assert.equal(decode(log, t0, Infinity).n, 4);
  const times = log.filter((e) => e.t >= t0 && e.t <= t1).map((e) => e.t);
  assert.ok(new Set(times).size >= 12, 'every transition at its own instant (a polling / IRQ firmware sees each)');
  assert.ok(times[times.length - 1] - t0 <= 500_000, 'three clicks take well under half a second of machine time');
});

test('[3] encoder: params.detent = 2 -> 2 transitions per click', async () => {
  const { Encoder } = await chips();
  const { sim, log } = rig();
  const enc = new Encoder(sim, encoder({ detent: 2 }));
  sim.run(10_000); const t0 = sim.t;
  enc.turn(1); sim.run(t0 + 500_000);
  assert.equal(decode(log, t0, Infinity).n, 2);
  enc.turn(1); sim.run(sim.t + 500_000);
  assert.equal(decode(log, t0, Infinity).n, 4);
});

test('[3] encoder: the optional push contact (SW / SW2) works like a button', async () => {
  const { Encoder } = await chips();
  const { sim, log } = rig();
  const enc = new Encoder(sim, encoder({}, { A: pin(32), B: pin(33), C: { rail: 'gnd' }, SW: pin(25), SW2: { rail: 'gnd' } }));
  sim.run(10_000); enc.down(); sim.run(100_000);
  assert.equal(eff(lastDrive(log, 25).v, 1), 0, 'pushed: SW pin LOW');
  enc.up(); sim.run(200_000);
  assert.equal(lastDrive(log, 25).v, undefined, 'released: SW pin not driven');
  assert.equal(decode(log, 0, Infinity).n, 0, 'pushing does not turn it');
});

// ---------------------------------------------------------------- [4] MipiDisplay (st7789 / st7735 / ili9341)

const RED = 0xf800, GREEN = 0x07e0, BLUE = 0x001f, WHITE = 0xffff;
async function mipi(model = 'st7789', params = { w: 240, h: 320 }) {
  const { MipiDisplay } = await chips();
  return new MipiDisplay(new Sim(), { id: 'tft', part: 'tft', model, board: null, params, ports: {} });
}
let T = 0;
const cmd = (d, c, ...data) => { d.spi(Uint8Array.of(c), 0, (T += 10)); if (data.length) d.spi(Uint8Array.from(data), 1, (T += 10)); };
const px = (c, n) => { const a = new Uint8Array(n * 2); for (let i = 0; i < n; i++) { a[2 * i] = c >> 8; a[2 * i + 1] = c & 255; } return a; };
const win = (d, x0, y0, x1, y1) => { cmd(d, 0x2a, x0 >> 8, x0 & 255, x1 >> 8, x1 & 255); cmd(d, 0x2b, y0 >> 8, y0 & 255, y1 >> 8, y1 & 255); };
const ramwr = (d, bytes, c = 0x2c) => { d.spi(Uint8Array.of(c), 0, (T += 10)); d.spi(bytes, 1, (T += 10)); };
const fill = (d, x0, y0, x1, y1, c) => { win(d, x0, y0, x1, y1); ramwr(d, px(c, (x1 - x0 + 1) * (y1 - y0 + 1))); };
const wake = (d, colmod = 0x55, madctl = 0x00) => { cmd(d, 0x01); cmd(d, 0x11); cmd(d, 0x3a, colmod); cmd(d, 0x36, madctl); cmd(d, 0x29); };
const at = (d, x, y) => { const w = d.fb.length / 4 / fbH(d), i = (y * w + x) * 4; return [d.fb[i], d.fb[i + 1], d.fb[i + 2], d.fb[i + 3]]; };
let H_OF = new WeakMap(); const fbH = (d) => H_OF.get(d);
const track = (d, h) => { H_OF.set(d, h); return d; };
const is = {
  red: ([r, g, b]) => r >= 240 && g <= 16 && b <= 16,
  green: ([r, g, b]) => r <= 16 && g >= 240 && b <= 16,
  blue: ([r, g, b]) => r <= 16 && g <= 16 && b >= 240,
  cyan: ([r, g, b]) => r <= 16 && g >= 240 && b >= 240,
  white: ([r, g, b]) => r >= 240 && g >= 240 && b >= 240,
  black: ([r, g, b]) => r <= 8 && g <= 8 && b <= 8,
};
const allBlack = (d) => { for (let i = 0; i < d.fb.length; i += 4) if (d.fb[i] > 8 || d.fb[i + 1] > 8 || d.fb[i + 2] > 8) return false; return true; };

test('[4] mipi: fb is RGBA w*h*4 of the panel; powered up the display shows nothing (on = false)', async () => {
  const d = await mipi();
  assert.ok(d.fb instanceof Uint8ClampedArray, 'fb is a Uint8ClampedArray');
  assert.equal(d.fb.length, 240 * 320 * 4);
  assert.equal(d.on, false);
  assert.ok(allBlack(d));
});

test('[4] mipi: SWRESET + SLPOUT + COLMOD 565 + DISPON, then a full-screen RAMWR shows the colour (alpha 255)', async () => {
  const d = track(await mipi(), 320);
  wake(d);
  assert.equal(d.on, true);
  fill(d, 0, 0, 239, 319, RED);
  assert.ok(is.red(at(d, 0, 0)), `(0,0) = ${at(d, 0, 0)}`);
  assert.ok(is.red(at(d, 239, 319)), `(239,319) = ${at(d, 239, 319)}`);
  assert.equal(at(d, 120, 160)[3], 255);
});

test('[4] mipi: nothing shown before DISPON (asleep / awake but display off); RAM is kept and appears on DISPON', async () => {
  const d = track(await mipi(), 320);
  cmd(d, 0x01); cmd(d, 0x3a, 0x55);
  fill(d, 0, 0, 239, 319, RED);
  assert.equal(d.on, false); assert.ok(allBlack(d), 'asleep: black');
  cmd(d, 0x11);
  assert.equal(d.on, false, 'SLPOUT alone does not switch the display on'); assert.ok(allBlack(d), 'awake, display off: black');
  cmd(d, 0x29);
  assert.equal(d.on, true);
  assert.ok(is.red(at(d, 10, 10)), 'DISPON shows what was written to RAM before');
});

test('[4] mipi: DISPOFF and SLPIN blank the picture, DISPON / SLPOUT bring it back; SWRESET switches it off', async () => {
  const d = track(await mipi(), 320);
  wake(d); fill(d, 0, 0, 239, 319, GREEN);
  cmd(d, 0x28); assert.equal(d.on, false); assert.ok(allBlack(d), 'DISPOFF: black');
  cmd(d, 0x29); assert.ok(is.green(at(d, 5, 5)), 'DISPON again: picture back');
  cmd(d, 0x10); assert.equal(d.on, false); assert.ok(allBlack(d), 'SLPIN: black');
  cmd(d, 0x11); assert.equal(d.on, true); assert.ok(is.green(at(d, 5, 5)), 'SLPOUT (display still on): back');
  cmd(d, 0x01); assert.equal(d.on, false, 'SWRESET: sleep in, display off');
});

test('[4] mipi: CASET/RASET partial window writes only that rectangle', async () => {
  const d = track(await mipi(), 320);
  wake(d); fill(d, 0, 0, 239, 319, BLUE);
  fill(d, 20, 30, 29, 39, RED);
  for (const [x, y] of [[20, 30], [29, 30], [20, 39], [29, 39], [25, 35]]) assert.ok(is.red(at(d, x, y)), `inside (${x},${y}) = ${at(d, x, y)}`);
  for (const [x, y] of [[19, 30], [30, 30], [20, 29], [20, 40], [0, 0], [239, 319]]) assert.ok(is.blue(at(d, x, y)), `outside (${x},${y}) = ${at(d, x, y)}`);
});

test('[4] mipi: writes past the window end wrap to the window start, never outside it', async () => {
  const d = track(await mipi(), 320);
  wake(d); fill(d, 0, 0, 239, 319, BLUE);
  win(d, 0, 0, 3, 1);                                     // 4 x 2 = 8 pixels
  const bytes = new Uint8Array(24); bytes.set(px(RED, 8), 0); bytes.set(px(GREEN, 4), 16);   // 12 pixels
  ramwr(d, bytes);
  for (let x = 0; x < 4; x++) { assert.ok(is.green(at(d, x, 0)), `row 0 wrapped (${x},0) = ${at(d, x, 0)}`); assert.ok(is.red(at(d, x, 1)), `row 1 (${x},1)`); }
  assert.ok(is.blue(at(d, 4, 0)) && is.blue(at(d, 0, 2)), 'outside the window untouched');
});

test('[4] mipi: RAMWRC (0x3C) continues where the last write stopped', async () => {
  const d = track(await mipi(), 320);
  wake(d); fill(d, 0, 0, 239, 319, BLUE);
  win(d, 0, 0, 3, 1);
  ramwr(d, px(RED, 4));
  ramwr(d, px(GREEN, 4), 0x3c);
  for (let x = 0; x < 4; x++) { assert.ok(is.red(at(d, x, 0)), `(${x},0)`); assert.ok(is.green(at(d, x, 1)), `(${x},1) = ${at(d, x, 1)}`); }
});

test('[4] mipi: a pixel split across two SPI transfers is assembled (bytes are a stream, not per-call)', async () => {
  const d = track(await mipi(), 320);
  wake(d); win(d, 0, 0, 1, 0);
  d.spi(Uint8Array.of(0x2c), 0, (T += 10));
  d.spi(Uint8Array.of(RED >> 8), 1, (T += 10));
  d.spi(Uint8Array.of(RED & 255, GREEN >> 8, GREEN & 255), 1, (T += 10));
  assert.ok(is.red(at(d, 0, 0)), `${at(d, 0, 0)}`); assert.ok(is.green(at(d, 1, 0)), `${at(d, 1, 0)}`);
});

test('[4] mipi: COLMOD 0x66 = 18-bit, 3 bytes per pixel', async () => {
  const d = track(await mipi(), 320);
  wake(d, 0x66);
  win(d, 0, 0, 2, 0);
  ramwr(d, Uint8Array.of(0xfc, 0, 0, 0, 0xfc, 0, 0, 0, 0xfc));
  assert.ok(is.red(at(d, 0, 0)), `${at(d, 0, 0)}`); assert.ok(is.green(at(d, 1, 0)), `${at(d, 1, 0)}`); assert.ok(is.blue(at(d, 2, 0)), `${at(d, 2, 0)}`);
});

test('[4] mipi: MADCTL MX mirrors columns, MY mirrors rows', async () => {
  const d = track(await mipi(), 320);
  wake(d); fill(d, 0, 0, 239, 319, BLUE);
  cmd(d, 0x36, 0x40); fill(d, 0, 0, 0, 0, RED);
  assert.ok(is.red(at(d, 239, 0)), `MX: (0,0) lands at (239,0), got ${at(d, 239, 0)}`); assert.ok(is.blue(at(d, 0, 0)));
  cmd(d, 0x36, 0x80); fill(d, 0, 0, 0, 0, GREEN);
  assert.ok(is.green(at(d, 0, 319)), `MY: (0,0) lands at (0,319), got ${at(d, 0, 319)}`);
});

test('[4] mipi: MADCTL MV exchanges rows and columns (landscape: x runs 0..319)', async () => {
  const d = track(await mipi(), 320);
  wake(d); fill(d, 0, 0, 239, 319, BLUE);
  cmd(d, 0x36, 0x20);
  fill(d, 5, 0, 5, 0, RED);
  assert.ok(is.red(at(d, 0, 5)), `MV: logical (5,0) lands at panel (0,5), got ${at(d, 0, 5)}`);
  fill(d, 300, 10, 300, 10, GREEN);
  assert.ok(is.green(at(d, 10, 300)), `MV: logical (300,10) lands at panel (10,300), got ${at(d, 10, 300)}`);
});

test('[4] mipi: MADCTL BGR bit swaps red and blue', async () => {
  const d = track(await mipi(), 320);
  wake(d, 0x55, 0x08);
  fill(d, 0, 0, 0, 0, RED);
  assert.ok(is.blue(at(d, 0, 0)), `BGR: 0xF800 shows blue, got ${at(d, 0, 0)}`);
});

test('[4] mipi: INVON inverts the shown colours, INVOFF restores them', async () => {
  const d = track(await mipi(), 320);
  wake(d); fill(d, 0, 0, 239, 319, RED);
  cmd(d, 0x21); assert.ok(is.cyan(at(d, 3, 3)), `INVON: red shows cyan, got ${at(d, 3, 3)}`);
  cmd(d, 0x20); assert.ok(is.red(at(d, 3, 3)), `INVOFF: red again, got ${at(d, 3, 3)}`);
});

test('[4] mipi: commands it does not model (porch, gamma) are skipped with their parameters', async () => {
  const d = track(await mipi(), 320);
  cmd(d, 0x01); cmd(d, 0x11);
  cmd(d, 0xb2, 0x0c, 0x0c, 0x00, 0x33, 0x33);
  cmd(d, 0xe0, 0xd0, 0x04, 0x0d, 0x11, 0x13, 0x2b, 0x3f, 0x54, 0x4c, 0x18, 0x0d, 0x0b, 0x1f, 0x23);
  cmd(d, 0x3a, 0x55); cmd(d, 0x29);
  fill(d, 0, 0, 239, 319, WHITE);
  assert.ok(is.white(at(d, 100, 100)), `${at(d, 100, 100)}`);
});

test('[4] mipi: x_off / y_off — a 135x240 panel on the 240x320 controller RAM shows RAM from (52,40)', async () => {
  const d = track(await mipi('st7789', { w: 135, h: 240, x_off: 52, y_off: 40 }), 240);
  assert.equal(d.fb.length, 135 * 240 * 4);
  wake(d);
  fill(d, 0, 0, 239, 319, BLUE);
  fill(d, 52, 40, 52, 40, RED);
  fill(d, 51, 40, 51, 40, GREEN);
  fill(d, 186, 279, 186, 279, WHITE);
  assert.ok(is.red(at(d, 0, 0)), `RAM (52,40) is panel (0,0), got ${at(d, 0, 0)}`);
  assert.ok(is.blue(at(d, 1, 0)));
  assert.ok(is.white(at(d, 134, 239)), `RAM (186,279) is panel (134,239), got ${at(d, 134, 239)}`);
  let green = 0; for (let i = 0; i < d.fb.length; i += 4) if (d.fb[i + 1] > 200 && d.fb[i] < 50 && d.fb[i + 2] < 50) green++;
  assert.equal(green, 0, 'RAM column 51 is outside the panel');
});

for (const [model, w, h] of [['st7789', 240, 320], ['st7735', 128, 160], ['ili9341', 240, 320]]) {
  test(`[4] mipi: one model for ${model} (${w}x${h}) — the same command set draws`, async () => {
    const d = track(await mipi(model, { w, h }), h);
    assert.equal(d.fb.length, w * h * 4);
    wake(d);
    fill(d, 0, 0, w - 1, h - 1, BLUE);
    fill(d, 1, 2, 1, 2, RED);
    assert.ok(is.red(at(d, 1, 2)), `${model}: (1,2) = ${at(d, 1, 2)}`);
    assert.ok(is.blue(at(d, w - 1, h - 1)));
  });
}

test('[4] mipi: frames grows with pixel writes, not with other commands', async () => {
  const d = track(await mipi(), 320);
  wake(d);
  const f0 = d.frames;
  assert.equal(typeof f0, 'number');
  win(d, 0, 0, 9, 9); cmd(d, 0x36, 0x00);
  assert.equal(d.frames, f0, 'CASET / RASET / MADCTL are not frames');
  ramwr(d, px(RED, 100));
  const f1 = d.frames;
  assert.ok(f1 > f0, 'a RAMWR with data counts');
  fill(d, 0, 0, 9, 9, GREEN);
  assert.ok(d.frames > f1);
});

// ---------------------------------------------------------------- [4] SSD1306

async function oled(params = { w: 128, h: 64 }) {
  const { Ssd1306 } = await chips();
  const d = new Ssd1306(new Sim(), { id: 'oled', part: 'oled', model: 'ssd1306', board: null, params, ports: {} });
  H_OF.set(d, params.h);
  return d;
}
const oc = (d, ...c) => d.i2cWrite(Uint8Array.of(0x00, ...c));
const od = (d, ...b) => d.i2cWrite(Uint8Array.of(0x40, ...b));
const lum = (p) => Math.max(p[0], p[1], p[2]);
const lit = (d, x, y) => lum(at(d, x, y)) > 100;
const dark = (d, x, y) => lum(at(d, x, y)) < 40;
const oinit = (d) => oc(d, 0xae, 0x20, 0x00, 0x21, 0, 127, 0x22, 0, (d.fb.length / 4 / 128 / 8) - 1, 0xaf);

test('[4] ssd1306: fb RGBA 128x64; off at power-up, data written while off is not shown', async () => {
  const d = await oled();
  assert.ok(d.fb instanceof Uint8ClampedArray);
  assert.equal(d.fb.length, 128 * 64 * 4);
  assert.equal(d.on, false);
  oc(d, 0x20, 0x00, 0x21, 0, 127, 0x22, 0, 7);
  od(d, 0xff, 0xff);
  assert.ok(dark(d, 0, 0) && dark(d, 1, 7), 'display off: dark');
  oc(d, 0xaf);
  assert.equal(d.on, true);
  assert.ok(lit(d, 0, 0) && lit(d, 1, 7), 'display on: the GDDRAM content shows');
});

test('[4] ssd1306: a data byte is 8 vertical pixels of a page, LSB on top', async () => {
  const d = await oled();
  oinit(d);
  od(d, 0x01);
  assert.ok(lit(d, 0, 0)); assert.ok(dark(d, 0, 1)); assert.ok(dark(d, 1, 0));
});

test('[4] ssd1306: horizontal addressing inside a column / page window wraps to the next page', async () => {
  const d = await oled();
  oinit(d);
  oc(d, 0x21, 0, 1, 0x22, 0, 1);
  od(d, 0x01, 0x02, 0x80, 0x00);
  assert.ok(lit(d, 0, 0), 'byte 0 -> col 0, page 0, bit 0');
  assert.ok(lit(d, 1, 1), 'byte 1 -> col 1, page 0, bit 1');
  assert.ok(lit(d, 0, 15), 'byte 2 -> col 0, page 1, bit 7');
  for (let y = 8; y < 16; y++) assert.ok(dark(d, 1, y), `byte 3 = 0 at (1,${y})`);
  assert.ok(dark(d, 2, 0), 'column 2 is outside the window');
});

test('[4] ssd1306: page addressing — page from 0xB0|p, column from 0x0n / 0x1n; wraps within the page', async () => {
  const d = await oled();
  oinit(d);
  oc(d, 0x20, 0x02, 0xb2, 0x05, 0x10);
  od(d, 0xff);
  for (let y = 16; y < 24; y++) assert.ok(lit(d, 5, y), `(5,${y})`);
  assert.ok(dark(d, 5, 15) && dark(d, 5, 24) && dark(d, 6, 16));
  oc(d, 0xb2, 0x0f, 0x17);                                // column 127
  od(d, 0x01, 0x01);
  assert.ok(lit(d, 127, 16), 'column 127');
  assert.ok(lit(d, 0, 16), 'past column 127 the pointer wraps to column 0 of the SAME page');
  assert.ok(dark(d, 0, 24), 'page did not advance');
});

test('[4] ssd1306: display off (0xAE) darkens, on (0xAF) restores; invert (0xA7 / 0xA6)', async () => {
  const d = await oled();
  oinit(d); od(d, 0x01);
  oc(d, 0xae); assert.equal(d.on, false); assert.ok(dark(d, 0, 0));
  oc(d, 0xaf); assert.ok(lit(d, 0, 0));
  oc(d, 0xa7); assert.ok(dark(d, 0, 0), 'inverted: set pixel dark'); assert.ok(lit(d, 0, 1), 'inverted: clear pixel lit');
  oc(d, 0xa6); assert.ok(lit(d, 0, 0) && dark(d, 0, 1));
});

test('[4] ssd1306: contrast (0x81 n) dims lit pixels without switching them off', async () => {
  const d = await oled();
  oinit(d); od(d, 0x01);
  oc(d, 0x81, 0xff); const hi = lum(at(d, 0, 0));
  oc(d, 0x81, 0x10); const lo = lum(at(d, 0, 0));
  assert.ok(lo < hi, `contrast 0x10 (${lo}) dimmer than 0xFF (${hi})`);
  assert.ok(lo > lum(at(d, 0, 1)), 'still brighter than an unlit pixel');
});

test('[4] ssd1306: segment remap (0xA1) mirrors columns, COM scan decrement (0xC8) mirrors rows', async () => {
  const d = await oled();
  oinit(d);
  oc(d, 0xa1); oc(d, 0x21, 0, 127, 0x22, 0, 7); od(d, 0x01);
  assert.ok(lit(d, 127, 0), 'A1: column 0 is drawn at x 127'); assert.ok(dark(d, 0, 0));
  oc(d, 0xa0, 0xc8);
  assert.ok(lit(d, 0, 63), 'A0 + C8: column 0 back at x 0, row 0 drawn at y 63');
});

test('[4] ssd1306: a 128x32 panel has 4 pages', async () => {
  const d = await oled({ w: 128, h: 32 });
  assert.equal(d.fb.length, 128 * 32 * 4);
  oc(d, 0x20, 0x00, 0x21, 0, 127, 0x22, 3, 3, 0xaf);
  od(d, 0x80);
  assert.ok(lit(d, 0, 31));
});

// ---------------------------------------------------------------- review 2: a reset of the controller (what RST low does, device.mjs watchPins)

test('[4] mipi: reset() = power-up state: off, asleep, 18-bit colour, full window; RAM kept', async () => {
  const d = track(await mipi(), 320);
  wake(d); fill(d, 0, 0, 239, 319, GREEN);
  d.reset();
  assert.equal(d.on, false); assert.ok(allBlack(d));
  cmd(d, 0x11); cmd(d, 0x29);
  assert.ok(is.green(at(d, 9, 9)), 'RAM kept through a reset');
  assert.equal(d.colmod & 7, 6, 'COLMOD back to 18-bit');
});
