// device.json -> META, and the node PNG snapshot of a display. No wasm here; the firmware runs are in
// ../tests/device_fw.test.mjs. Written from the spec before the implementation exists.
//
//   node --test agent/skills/virtual-controllers/runtime/device.test.mjs
//
// Contract under test:
//   runtime/device.mjs  deviceMeta(device) -> {boards, elements, inputs} (META-like, what sim_ui needs):
//     boards  [{id, name, mcu: <board model>, fw, cad: [{part: <its part id>}]}]  (a 3D click on the board part opens its card)
//     elements = device.elements (same ids, same order)
//     inputs  [{id, label, board, pin, active}] one per button: label = params.label or the id, active = level a press drives
//   runtime/node.mjs    pngOf(display) -> Uint8Array: PNG (RGB or RGBA, 8 bit) of display.fb at the display's w x h
// Test names carry the "done when" they serve: [1] device.json drives the device, [6] headless PNG snapshot.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inflateSync } from 'node:zlib';
import { Sim } from './sim.mjs';

const DEVICE = {
  boards: [{ id: 'mcu', part: 'esp32_devkit', model: 'esp32', fw: 'player' }],
  elements: [
    { id: 'btn_play', part: 'tact_switch', model: 'button', board: 'mcu', params: { key: 'Space', bounce_ms: 2, label: 'Play' },
      ports: { 1: { board: 'mcu', pin: 27 }, 2: { rail: 'gnd' } } },
    { id: 'btn_hi', part: 'tact_switch', model: 'button', board: 'mcu', params: {},
      ports: { 1: { board: 'mcu', pin: 26 }, 2: { rail: 'vcc' } } },
    { id: 'enc', part: 'ec11', model: 'encoder', board: 'mcu', params: { keys: ['ArrowLeft', 'ArrowRight'], push_key: 'Enter' },
      ports: { A: { board: 'mcu', pin: 32 }, B: { board: 'mcu', pin: 33 }, C: { rail: 'gnd' }, SW: { board: 'mcu', pin: 25 }, SW2: { rail: 'gnd' } } },
    { id: 'tft', part: 'tft_st7789', model: 'st7789', board: 'mcu', params: { w: 240, h: 320, x_off: 0, y_off: 0 },
      ports: { SCL: { board: 'mcu', pin: 18 }, SDA: { board: 'mcu', pin: 23 }, DC: { board: 'mcu', pin: 16 }, CS: { board: 'mcu', pin: 5 },
               RST: { board: 'mcu', pin: 17 }, BLK: { board: 'mcu', pin: 4 }, VCC: { rail: 'vcc' }, GND: { rail: 'gnd' } } },
  ],
};

test('[1] deviceMeta: boards come from device.json, mapped to their 3D part', async () => {
  const { deviceMeta } = await import('./device.mjs');
  const m = deviceMeta(DEVICE);
  assert.equal(m.boards.length, 1);
  const b = m.boards[0];
  assert.equal(b.id, 'mcu'); assert.equal(b.mcu, 'esp32'); assert.equal(b.fw, 'player');
  assert.ok(typeof b.name === 'string' && b.name.length > 0, 'a board has a display name');
  assert.deepEqual((b.cad || []).map((c) => c.part), ['esp32_devkit']);
});

test('[1] deviceMeta: elements are the device elements, in order', async () => {
  const { deviceMeta } = await import('./device.mjs');
  const m = deviceMeta(DEVICE);
  assert.deepEqual(m.elements.map((e) => e.id ?? e), ['btn_play', 'btn_hi', 'enc', 'tft']);
});

test('[1] deviceMeta: every button is an input with its pin and the active level from the wiring', async () => {
  const { deviceMeta } = await import('./device.mjs');
  const inputs = deviceMeta(DEVICE).inputs;
  const play = inputs.find((i) => i.id === 'btn_play'), hi = inputs.find((i) => i.id === 'btn_hi');
  assert.ok(play, JSON.stringify(inputs));
  assert.deepEqual({ label: play.label, board: play.board, pin: play.pin, active: play.active }, { label: 'Play', board: 'mcu', pin: 27, active: 0 });
  assert.ok(hi, JSON.stringify(inputs));
  assert.deepEqual({ label: hi.label, board: hi.board, pin: hi.pin, active: hi.active }, { label: 'btn_hi', board: 'mcu', pin: 26, active: 1 });
  assert.ok(!inputs.some((i) => i.id === 'tft'), 'a display is not an input');
});

test('[1] deviceMeta: a device without elements still has its board', async () => {
  const { deviceMeta } = await import('./device.mjs');
  const m = deviceMeta({ boards: DEVICE.boards, elements: [] });
  assert.equal(m.boards.length, 1);
  assert.deepEqual(m.elements, []); assert.deepEqual(m.inputs, []);
});

// ---------------------------------------------------------------- [6] pngOf: what the agent looks at

function decodePng(buf) {
  buf = Buffer.from(buf);
  assert.deepEqual([...buf.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10], 'PNG signature');
  let pos = 8, w = 0, h = 0, ch = 0; const idat = [];
  while (pos < buf.length) {
    const n = buf.readUInt32BE(pos), typ = buf.toString('latin1', pos + 4, pos + 8), body = buf.subarray(pos + 8, pos + 8 + n);
    if (typ === 'IHDR') { w = body.readUInt32BE(0); h = body.readUInt32BE(4); assert.equal(body[8], 8, '8-bit'); ch = { 2: 3, 6: 4 }[body[9]]; assert.ok(ch, 'RGB or RGBA'); }
    else if (typ === 'IDAT') idat.push(body);
    pos += 12 + n;
  }
  const raw = inflateSync(Buffer.concat(idat)), stride = w * ch, out = Buffer.alloc(h * stride);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (stride + 1)], line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let i = 0; i < stride; i++) {
      const a = i >= ch ? out[y * stride + i - ch] : 0, b = y ? out[(y - 1) * stride + i] : 0, c = i >= ch && y ? out[(y - 1) * stride + i - ch] : 0;
      let v = line[i];
      if (f === 1) v += a; else if (f === 2) v += b; else if (f === 3) v += (a + b) >> 1;
      else if (f === 4) { const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c); v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
      out[y * stride + i] = v & 255;
    }
  }
  return { w, h, ch, px: (x, y) => [...out.subarray((y * w + x) * ch, (y * w + x) * ch + 3)] };
}

test('[6] pngOf: a PNG of the display picture, same size, same pixels', async () => {
  const { MipiDisplay } = await import('./chips.mjs');
  const { pngOf } = await import('./node.mjs');
  const d = new MipiDisplay(new Sim(), { id: 'tft', part: 'tft', model: 'st7789', board: null, params: { w: 240, h: 320 }, ports: {} });
  const c = (b, ...p) => { d.spi(Uint8Array.of(b), 0, 0); if (p.length) d.spi(Uint8Array.from(p), 1, 0); };
  c(0x01); c(0x11); c(0x3a, 0x55); c(0x36, 0); c(0x29);
  c(0x2a, 0, 0, 0, 239); c(0x2b, 0, 0, 1, 63);
  const all = new Uint8Array(240 * 320 * 2); for (let i = 0; i < all.length; i += 2) { all[i] = 0x00; all[i + 1] = 0x1f; }   // blue
  d.spi(Uint8Array.of(0x2c), 0, 0); d.spi(all, 1, 0);
  c(0x2a, 0, 10, 0, 10); c(0x2b, 0, 20, 0, 20); d.spi(Uint8Array.of(0x2c), 0, 0); d.spi(Uint8Array.of(0xf8, 0x00), 1, 0);   // one red pixel
  const png = pngOf(d);
  assert.ok(png instanceof Uint8Array, 'pngOf returns bytes');
  const img = decodePng(png);
  assert.deepEqual([img.w, img.h], [240, 320]);
  const [r, g, b] = img.px(10, 20);
  assert.ok(r >= 240 && g <= 16 && b <= 16, `(10,20) red, got ${[r, g, b]}`);
  const [r2, g2, b2] = img.px(200, 300);
  assert.ok(r2 <= 16 && g2 <= 16 && b2 >= 240, `(200,300) blue, got ${[r2, g2, b2]}`);
});
