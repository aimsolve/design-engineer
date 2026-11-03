// Chip models of a virtual device: the parts around the MCU, each built from one device.json element
// {id, part, model, board, params, ports: {port: {board, pin} | {rail} | null}}. Isomorphic (node + browser), no wasm here.
//
//   Button   two contacts, one to an MCU pin, the other to a rail. Pressed = the pin is forced to the rail's level (Board.drive),
//            released = not driven (the firmware's pinMode default decides). Contact bounce on by default (params.bounce_ms, 3 ms),
//            deterministic for the element id and the press time.
//   Encoder  A / B to MCU pins, C to a rail; turn(n) = n detents (n > 0 clockwise: A leads B), params.detent transitions per click
//            (4). Optional push contact SW / SW2 = a Button.
//   MipiDisplay  st7789 / st7735 / ili9341: MIPI-DCS commands and pixels over SPI (spi(bytes, dc, t)), controller RAM, MADCTL, COLMOD,
//            sleep / display on, inversion, panel window (w, h, x_off, y_off); params.ips = an IPS panel, right colours with INVON.
//            fb = RGBA of what the glass SHOWS.
//   Ssd1306  128x64 / 128x32 OLED over I2C (i2cWrite(bytes) with the control byte first), GDDRAM pages, addressing modes.
// Every display keeps a dirty rectangle (dirty = [x0, y0, x1, y1] | null, version) so the page uploads only what changed.

const RAIL_LEVEL = { gnd: 0, vcc: 1 };

// a small deterministic PRNG (mulberry32) seeded from a string and a number
function rng(str, n) {
  let h = 2166136261 ^ Math.floor(n);
  for (let i = 0; i < str.length; i++) h = Math.imul(h ^ str.charCodeAt(i), 16777619);
  return () => { h = (h + 0x6d2b79f5) | 0; let t = Math.imul(h ^ (h >>> 15), 1 | h); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

// one contact between an MCU pin and a rail: the shared part of Button and the encoder's push / channels
class Contact {
  constructor(sim, ports, a, b, id) {
    const pa = ports[a], pb = ports[b];
    const pin = pa && pa.board != null ? pa : pb && pb.board != null ? pb : null;
    const rail = pa && pa.rail ? pa.rail : pb && pb.rail ? pb.rail : 'gnd';
    this.sim = sim; this.id = id; this.pin = pin; this.active = RAIL_LEVEL[rail] ?? 0; this.gen = 0;
  }
  set(closed) { if (this.pin) this.sim.board(this.pin.board).drive(this.pin.pin, closed ? this.active : undefined); }
  // closed/open at time t, with `bounceMs` of contact bounce before it settles; a newer change cancels the pending bounce
  change(closed, t, bounceMs) {
    const g = ++this.gen;
    if (!(bounceMs > 0)) { this.set(closed); return; }
    const r = rng(this.id, t), span = bounceMs * 1000;
    const k = 1 + 2 * (1 + Math.floor(r() * 3));                  // 3, 5 or 7 level changes, ending at the new state
    const times = []; for (let i = 0; i < k; i++) times.push(i === 0 ? 0 : 100 + r() * (span - 100));
    times.sort((x, y) => x - y);
    for (let i = 1; i < k; i++) if (times[i] <= times[i - 1] + 60) times[i] = times[i - 1] + 60;   // apart enough for the firmware to see each
    times.forEach((dt, i) => {                                    // the first edge a few µs after the press: the change is never at the instant of the call
      const state = i % 2 === 0 ? closed : !closed;
      this.sim.at(t + 10 + Math.min(dt, span - 10), () => { if (this.gen === g) this.set(state); });
    });
  }
}

export class Button {
  constructor(sim, el) {
    this.sim = sim; this.el = el; this.id = el.id; this.params = el.params || {};
    const names = Object.keys(el.ports || {});
    this.c = new Contact(sim, el.ports || {}, names[0], names[1], el.id);
    this.pin = this.c.pin; this.active = this.c.active; this.pressed = false;
  }
  get bounceMs() { return this.params.bounce_ms ?? 3; }
  down() { if (this.pressed) return; this.pressed = true; this.c.change(true, this.sim.t, this.bounceMs); }
  up() { if (!this.pressed) return; this.pressed = false; this.c.change(false, this.sim.t, this.bounceMs); }
}

// quadrature states (A << 1 | B) clockwise from rest with both contacts open: 11 -> 01 -> 00 -> 10
const QUAD = [3, 1, 0, 2];
const STEP_US = 1500;                                            // one transition every 1.5 ms: a brisk turn, every edge apart

export class Encoder {
  constructor(sim, el) {
    this.sim = sim; this.el = el; this.id = el.id; this.params = el.params || {};
    const p = el.ports || {};
    this.a = new Contact(sim, { A: p.A, C: p.C }, 'A', 'C', el.id + ':A');
    this.b = new Contact(sim, { B: p.B, C: p.C }, 'B', 'C', el.id + ':B');
    this.push = p.SW ? new Button(sim, { id: el.id + ':SW', params: { bounce_ms: this.params.bounce_ms }, ports: { SW: p.SW, SW2: p.SW2 || { rail: 'gnd' } } }) : null;
    this.q = 0; this.busyUntil = 0;
  }
  get detent() { return this.params.detent || 4; }
  apply(q) { const s = QUAD[((q % 4) + 4) % 4]; this.a.set(!(s & 2)); this.b.set(!(s & 1)); }   // a closed contact reads 0 (to the rail)
  turn(n) {
    const steps = Math.round(n) * this.detent, dir = Math.sign(steps);
    let t = Math.max(this.sim.t, this.busyUntil);
    for (let i = 0; i < Math.abs(steps); i++) {
      t += STEP_US; this.q += dir; const q = this.q;
      this.sim.at(t, () => this.apply(q));
    }
    this.busyUntil = t;
  }
  down() { if (this.push) this.push.down(); }
  up() { if (this.push) this.push.up(); }
}

// ---------------------------------------------------------------- displays

const MIPI_RAM = { st7789: [240, 320], ili9341: [240, 320], st7735: [132, 162] };
const MIPI_PARAMS = { 0x2a: 4, 0x2b: 4, 0x36: 1, 0x3a: 1 };

class Screen {
  constructor(w, h) { this.w = w; this.h = h; this.fb = new Uint8ClampedArray(w * h * 4); this.dirty = null; this.version = 0; this.frames = 0; this.pixels = 0; for (let i = 3; i < this.fb.length; i += 4) this.fb[i] = 255; }
  mark(x0, y0, x1, y1) {
    const d = this.dirty;
    this.dirty = d ? [Math.min(d[0], x0), Math.min(d[1], y0), Math.max(d[2], x1), Math.max(d[3], y1)] : [x0, y0, x1, y1];
    this.version++;
  }
  markAll() { this.mark(0, 0, this.w - 1, this.h - 1); }
  takeDirty() { const d = this.dirty; this.dirty = null; return d; }
}

export class MipiDisplay extends Screen {
  constructor(sim, el) {
    const p = el.params || {}, [rw, rh] = MIPI_RAM[el.model] || MIPI_RAM.st7789;
    super(p.w || rw, p.h || rh);
    this.sim = sim; this.el = el; this.id = el.id; this.model = el.model;
    this.rw = Math.max(rw, this.w + (p.x_off || 0)); this.rh = Math.max(rh, this.h + (p.y_off || 0));
    this.xo = p.x_off || 0; this.yo = p.y_off || 0;
    this.ips = !!p.ips;                                           // an IPS panel shows the right colours with INVON (most ST7789 modules)
    this.ram = new Uint32Array(this.rw * this.rh);
    this.backlight = true;
    this.reset();
  }
  reset() {
    this.sleep = true; this.dispOn = false; this.inv = false; this.madctl = 0; this.colmod = 0x66;
    this.xs = 0; this.xe = this.rw - 1; this.ys = 0; this.ye = this.rh - 1; this.cx = 0; this.cy = 0;
    this.cmd = -1; this.args = []; this.part = []; this.render();
  }
  get on() { return !this.sleep && this.dispOn; }
  get lit() { return this.on && this.backlight; }
  setBacklight(on) { if (this.backlight !== !!on) { this.backlight = !!on; this.render(); } }

  spi(bytes, dc) {
    if (!dc) { for (const b of bytes) this.command(b); return; }
    if (this.cmd === 0x2c || this.cmd === 0x3c) { this.pixelBytes(bytes); return; }
    const need = MIPI_PARAMS[this.cmd];
    if (need === undefined) return;                                // a command we do not model: its parameters are skipped
    for (const b of bytes) { if (this.args.length < need) this.args.push(b); }
    if (this.args.length === need) { this.param(this.cmd, this.args); this.args = [0, 0, 0, 0, 0].slice(0, need + 1); }   // extra bytes ignored
  }
  command(c) {
    const was = this.lit;
    this.cmd = c; this.args = []; this.part = [];
    switch (c) {
      case 0x01: this.reset(); return;
      case 0x10: this.sleep = true; break;
      case 0x11: this.sleep = false; break;
      case 0x20: this.inv = false; this.render(); return;
      case 0x21: this.inv = true; this.render(); return;
      case 0x28: this.dispOn = false; break;
      case 0x29: this.dispOn = true; break;
      case 0x2c: this.cx = this.xs; this.cy = this.ys; this.started = false; return;
      case 0x3c: this.started = false; return;
      default: return;
    }
    if (was !== this.lit) this.render();
  }
  param(c, a) {
    if (c === 0x2a) { this.xs = (a[0] << 8) | a[1]; this.xe = (a[2] << 8) | a[3]; }
    else if (c === 0x2b) { this.ys = (a[0] << 8) | a[1]; this.ye = (a[2] << 8) | a[3]; }
    else if (c === 0x36) { const bgr = (this.madctl ^ a[0]) & 0x08; this.madctl = a[0]; if (bgr) this.render(); }
    else if (c === 0x3a) this.colmod = a[0];
  }
  // logical (column, row) of the write pointer -> RAM index (MV exchanges, MX / MY mirror)
  addr(x, y) {
    const m = this.madctl;
    let px = m & 0x20 ? y : x, py = m & 0x20 ? x : y;
    if (m & 0x40) px = this.rw - 1 - px;
    if (m & 0x80) py = this.rh - 1 - py;
    if (px < 0 || py < 0 || px >= this.rw || py >= this.rh) return -1;
    return py * this.rw + px;
  }
  pixelBytes(bytes) {
    const bpp = (this.colmod & 7) === 6 ? 3 : 2;
    if (!this.started) { this.started = true; this.frames++; }
    let part = this.part, i = 0;
    const n = bytes.length;
    let x0 = Infinity, y0 = Infinity, x1 = -1, y1 = -1, wrote = 0;
    while (i < n) {
      let r, g, b;
      if (part.length) { while (part.length < bpp && i < n) part.push(bytes[i++]); if (part.length < bpp) break; [r, g, b] = this.rgb(part, 0, bpp); part.length = 0; }
      else if (i + bpp <= n) { [r, g, b] = this.rgb(bytes, i, bpp); i += bpp; }
      else { while (i < n) part.push(bytes[i++]); break; }
      const k = this.addr(this.cx, this.cy);
      if (k >= 0) {
        const v = (r << 16) | (g << 8) | b; this.ram[k] = v;
        const px = (k % this.rw) - this.xo, py = ((k / this.rw) | 0) - this.yo;
        if (px >= 0 && py >= 0 && px < this.w && py < this.h) {
          this.paint(py * this.w + px, v);
          if (px < x0) x0 = px; if (px > x1) x1 = px; if (py < y0) y0 = py; if (py > y1) y1 = py;
        }
      }
      wrote++;
      if (++this.cx > this.xe) { this.cx = this.xs; if (++this.cy > this.ye) this.cy = this.ys; }
    }
    this.pixels += wrote;
    if (x1 >= 0 && this.lit) this.mark(x0, y0, x1, y1);
  }
  rgb(a, i, bpp) {
    if (bpp === 2) { const v = (a[i] << 8) | a[i + 1], r = v >> 11, g = (v >> 5) & 63, b = v & 31; return [(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)]; }
    const e = (x) => (x & 0xfc) | (x >> 6);
    return [e(a[i]), e(a[i + 1]), e(a[i + 2])];
  }
  paint(j, v) {
    const f = this.fb, o = j * 4;
    if (!this.lit) { f[o] = f[o + 1] = f[o + 2] = 0; return; }
    let r = v >> 16, g = (v >> 8) & 255, b = v & 255;
    if (this.madctl & 0x08) { const t = r; r = b; b = t; }
    if (this.inv !== this.ips) { r = 255 - r; g = 255 - g; b = 255 - b; }
    f[o] = r; f[o + 1] = g; f[o + 2] = b;
  }
  render() {
    for (let y = 0; y < this.h; y++) for (let x = 0; x < this.w; x++) this.paint(y * this.w + x, this.ram[(y + this.yo) * this.rw + x + this.xo]);
    this.markAll();
  }
}

export class Ssd1306 extends Screen {
  constructor(sim, el) {
    const p = el.params || {};
    super(p.w || 128, p.h || 64);
    this.sim = sim; this.el = el; this.id = el.id; this.model = 'ssd1306'; this.addr = p.addr ?? 0x3c;
    this.pages = this.h >> 3; this.ram = new Uint8Array(128 * 8);
    this.backlight = true;
    this.reset();
  }
  reset() {
    this.dispOn = false; this.inv = false; this.allOn = false; this.contrast = 0x7f; this.mode = 2; this.segRemap = false; this.comDec = false;
    this.c0 = 0; this.c1 = 127; this.p0 = 0; this.p1 = 7; this.col = 0; this.page = 0; this.pend = null; this.render();
  }
  get on() { return this.dispOn; }
  get lit() { return this.dispOn; }
  status() { return Uint8Array.of(this.dispOn ? 0x03 : 0x43); }
  i2cRead(n) { return this.status().subarray(0, Math.max(1, Math.min(n, 1))); }
  i2cWrite(bytes) {
    let i = 0;
    while (i < bytes.length) {
      const ctl = bytes[i++];
      const data = (ctl & 0x40) !== 0;
      if (ctl & 0x80) { if (i < bytes.length) { const b = bytes[i++]; data ? this.data(b) : this.command(b); } continue; }   // Co = 1: one byte, then another control byte
      for (; i < bytes.length; i++) data ? this.data(bytes[i]) : this.command(bytes[i]);
    }
    if (this.frames === 0 && this.pixels) this.frames = 1;
  }
  command(c) {
    if (this.pend) { this.pend.args.push(c); if (this.pend.args.length >= this.pend.n) { const p = this.pend; this.pend = null; this.param(p.c, p.args); } return; }
    const take = { 0x81: 1, 0x20: 1, 0x21: 2, 0x22: 2, 0xa8: 1, 0xd3: 1, 0xd5: 1, 0xd9: 1, 0xda: 1, 0xdb: 1, 0x8d: 1, 0xa3: 2, 0x26: 6, 0x27: 6, 0x29: 5, 0x2a: 5 }[c];
    if (take) { this.pend = { c, n: take, args: [] }; return; }
    if (c === 0xae || c === 0xaf) { this.dispOn = c === 0xaf; this.render(); }
    else if (c === 0xa6 || c === 0xa7) { this.inv = c === 0xa7; this.render(); }
    else if (c === 0xa4 || c === 0xa5) { this.allOn = c === 0xa5; this.render(); }
    else if (c === 0xa0 || c === 0xa1) { this.segRemap = c === 0xa1; this.render(); }
    else if (c === 0xc0 || c === 0xc8) { this.comDec = c === 0xc8; this.render(); }
    else if (c >= 0xb0 && c <= 0xb7) this.page = c & 7;
    else if (c <= 0x0f) this.col = (this.col & 0xf0) | c;
    else if (c >= 0x10 && c <= 0x1f) this.col = (this.col & 0x0f) | ((c & 0x0f) << 4);
  }
  param(c, a) {
    if (c === 0x81) { this.contrast = a[0]; this.render(); }
    else if (c === 0x20) this.mode = a[0] & 3;
    else if (c === 0x21) { this.c0 = a[0] & 127; this.c1 = a[1] & 127; this.col = this.c0; }
    else if (c === 0x22) { this.p0 = a[0] & 7; this.p1 = a[1] & 7; this.page = this.p0; }
  }
  data(b) {
    this.ram[this.page * 128 + this.col] = b; this.pixels += 8;
    this.drawByte(this.page, this.col);
    if (this.mode === 0) { if (++this.col > this.c1) { this.col = this.c0; if (++this.page > this.p1) this.page = this.p0; } }
    else if (this.mode === 1) { if (++this.page > this.p1) { this.page = this.p0; if (++this.col > this.c1) this.col = this.c0; } }
    else if (++this.col > 127) this.col = 0;
  }
  level(set) { if (!this.dispOn) return 0; const s = this.allOn || (!!set !== this.inv); return s ? Math.round(80 + (this.contrast * 175) / 255) : 0; }
  drawByte(page, col) {
    if (page >= this.pages || col >= this.w) return;
    const x = this.segRemap ? this.w - 1 - col : col, v = this.ram[page * 128 + col];
    let ya = this.h, yb = -1;
    for (let bit = 0; bit < 8; bit++) {
      const row = page * 8 + bit, y = this.comDec ? this.h - 1 - row : row, o = (y * this.w + x) * 4, l = this.level((v >> bit) & 1);
      this.fb[o] = this.fb[o + 1] = this.fb[o + 2] = l;
      if (y < ya) ya = y; if (y > yb) yb = y;
    }
    this.mark(x, ya, x, yb);
  }
  render() {
    if (!this.fb) return;
    for (let p = 0; p < this.pages; p++) for (let c = 0; c < this.w; c++) this.drawByte(p, c);
    this.markAll();
  }
}

export const DISPLAY_MODELS = new Set([...Object.keys(MIPI_RAM), 'ssd1306']);
export const MODELS = new Set(['button', 'encoder', ...DISPLAY_MODELS]);
