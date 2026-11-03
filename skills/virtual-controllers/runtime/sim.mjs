// virtual-controllers runtime: several boards (one wasm instance each) in one virtual clock.
//
// Scheduling: every board keeps its own virtual time (µs) inside wasm. A board runs until it calls vc_js_yield
// (delay / delayMicroseconds >= quantum / a quantum of busy CPU time), which suspends it via ASYNCIFY and hands us
// its wake-up time. run() always resumes whoever wakes earliest, so boards stay within ~one quantum of each other.
// A sleeping board is woken early ("poked") by an interrupt edge, an I2C message or a BLE write.
// All of it is synchronous JS: Asyncify's wakeUp() returns when the board yields again.
//
// Pins: nets are lists of [board, pin]; a net's value is what its OUTPUT member last wrote; undriven inputs read
// their pinMode default (INPUT_PULLUP -> 1, INPUT -> 0). A "world" can force any input pin (board.drive) and answer
// analogRead on pins that are not part of a driven net (sim.analogProvider).

const LAYOUT = ['pin_in', 'pin_out', 'pin_mode', 'quantum_us', 'quantum_max_us', 'mb_head', 'mb_tail', 'sr_head', 'sr_tail', 'sr', 'mb', 'msg_size'];
const INPUT_PULLUP = 2, OUTPUT = 1;
// Isomorphic: no Buffer / node:* here — the same file runs in node and in the browser.
const ENC = new TextEncoder(), DEC = new TextDecoder();
const latin1 = (bytes) => { let s = ''; for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]); return s; };

export class Board {
  constructor(sim, name, factory, kind) {
    this.sim = sim; this.name = name; this.factory = factory; this.kind = kind;
    this.mode = new Uint8Array(64); this.outVal = new Uint8Array(64); this.ext = new Array(64).fill(undefined);
    this.netOf = new Array(64).fill(null); this.irq = new Map();
    this.wake = Infinity; this.wakeUp = null; this.tNow = 0; this.dead = false; this.yields = 0; this.ble = { name: null, service: null, chars: {} };
    this.serialBuf = '';
    this.spiHosts = {}; this.i2cPins = null;   // what the firmware configured: SPI host -> {sck, mosi, miso}; Wire pins {sda, scl}
  }
  async init() {
    const hooks = this.makeHooks();
    this.mod = await this.factory({ vc: hooks, print: () => {}, printErr: (s) => this.sim.emit('stderr', { board: this.name, text: s }) });
    this.base = this.mod._vc_shared_addr();
    this.off = {}; LAYOUT.forEach((k, i) => (this.off[k] = this.mod._vc_layout(i)));
    this.dv = () => new DataView(this.mod.HEAPU8.buffer);
    this.u32set(this.off.quantum_us, this.sim.quantumUs); this.u32set(this.off.quantum_max_us, this.sim.quantumMaxUs);
    this.wake = 0;
    this.wakeUp = () => {  // first "resume" = start main() (returns at the first yield)
      const r = this.mod._main();
      if (r && r.catch) r.catch((e) => { this.dead = true; this.sim.emit('crash', { board: this.name, t: this.tNow, error: e }); });
    };
  }
  u32(o) { return this.dv().getUint32(this.base + o, true); }
  u32set(o, v) { this.dv().setUint32(this.base + o, v >>> 0, true); }
  heap() { return this.mod.HEAPU8; }

  makeHooks() {
    const sim = this.sim, b = this;
    return {
      yield: (now, target, wakeUp) => { b.tNow = now; b.wake = target; b.wakeUp = wakeUp; b.yields++; },
      pinMode: (pin, mode, outval) => sim.pinMode(b, pin, mode, outval),
      pinWrite: (pin, val, t) => sim.pinWrite(b, pin, val, t),
      pinClock: (pin, hz, on, t) => sim.emit('clock', { board: b.name, pin, hz, on: !!on, t }),
      attachIrq: (pin, mode) => b.irq.set(pin, mode),
      analogRead: (pin, t) => sim.analogRead(b, pin, t),
      serial: (bytes, t) => b.onSerial(bytes, t),
      i2cTx: (addr, bytes, t) => sim.i2cTx(b, addr, bytes, t),
      i2cRegister: (addr) => sim.i2cSlaves.set(addr, b),
      i2cRx: (addr, n, t) => sim.i2cRx(b, addr, n, t),
      i2cPins: (sda, scl) => { b.i2cPins = { sda, scl }; },
      spiBegin: (host, sck, mosi, miso) => { b.spiHosts[host] = { sck, mosi, miso }; },
      spi: (host, bytes, t) => sim.spiTx(b, host, bytes, t),
      bleNotify: (uuid, bytes, t) => sim.emit('ble-tx', { board: b.name, uuid, text: DEC.decode(bytes), t }),
      hook: (name, a, c, t) => {
        // BLE stub reports what the firmware advertises: Board.ble = { name, service, chars: {uuid: props} } (props bits: 1 read, 2 write, 4 notify)
        if (name.startsWith('ble.name:')) b.ble.name = name.slice(9);
        else if (name.startsWith('ble.service:')) b.ble.service = name.slice(12);
        else if (name.startsWith('ble.char:')) b.ble.chars[name.slice(9)] = a;
        sim.emit('hook', { board: b.name, name, a, b: c, t });
      },
    };
  }

  onSerial(bytes, t) {
    this.serialBuf += latin1(bytes);
    let i;
    while ((i = this.serialBuf.indexOf('\n')) >= 0) {
      const line = this.serialBuf.slice(0, i).replace(/\r$/, ''); this.serialBuf = this.serialBuf.slice(i + 1);
      this.sim.emit('serial', { board: this.name, line, t });
    }
  }

  // ---- inputs into the board ----
  refresh(pin) {  // recompute what digitalRead(pin) returns
    if (this.mode[pin] === OUTPUT) return;
    let v;
    if (this.ext[pin] !== undefined) v = this.ext[pin];
    else { const nv = this.sim.netValue(this.netOf[pin]); v = nv !== undefined ? nv : this.mode[pin] === INPUT_PULLUP ? 1 : 0; }
    const a = this.base + this.off.pin_in + pin, h = this.heap();
    if (h[a] !== v) { h[a] = v; if (this.irq.has(pin)) this.poke(this.sim.t); }
  }
  drive(pin, val) { this.ext[pin] = val === undefined ? undefined : val ? 1 : 0; this.refresh(pin); }  // world forcing a pin
  level(pin) { return this.mode[pin] === OUTPUT ? this.outVal[pin] : this.heap()[this.base + this.off.pin_in + pin]; }
  poke(t) { if (this.wakeUp && this.wake > t) this.wake = Math.max(t, this.tNow); }
  mail(type, data = [], uuid = '') {
    const head = this.u32(this.off.mb_head), tail = this.u32(this.off.mb_tail);
    if (head - tail >= 32) throw new Error(`${this.name}: mailbox full`);
    if (data.length > this.off.msg_size - 44) throw new Error(`${this.name}: mail of ${data.length} bytes > ${this.off.msg_size - 44}`);
    const slot = this.base + this.off.mb + (head % 32) * this.off.msg_size, h = this.heap();
    h[slot] = type; new DataView(h.buffer).setUint16(slot + 2, data.length, true);
    h.fill(0, slot + 4, slot + 44); h.set(ENC.encode(uuid), slot + 4); h.set(data, slot + 44);
    this.u32set(this.off.mb_head, head + 1);
    this.poke(this.sim.t);
  }
  // BLE / serial front-ends for the harness
  bleRx() { return Object.keys(this.ble.chars).find((u) => this.ble.chars[u] & 18); }   // first writable characteristic (WRITE | WRITE_NR)
  bleTx() { return Object.keys(this.ble.chars).find((u) => this.ble.chars[u] & 12); }   // first notify/indicate characteristic
  bleConnect() { this.mail(3); }
  bleDisconnect() { this.mail(4); }
  bleWrite(uuid, text) {   // a write longer than one mail slot arrives as several writes (like BLE without long-write)
    const d = ENC.encode(text), n = this.off.msg_size - 44;
    for (let i = 0; i < Math.max(d.length, 1); i += n) this.mail(2, d.subarray(i, i + n), uuid);
  }
  serialWrite(text) {
    const h = this.heap(); let head = this.u32(this.off.sr_head);
    for (const c of ENC.encode(text)) h[this.base + this.off.sr + (head++ & 255)] = c;
    this.u32set(this.off.sr_head, head); this.poke(this.sim.t);
  }

  resume(t) {
    const w = this.wakeUp; this.wakeUp = null; this.wake = Infinity; this.tNow = t;
    try { w(t); } catch (e) { this.dead = true; this.sim.emit('crash', { board: this.name, t, error: e }); }
  }
}

export class Sim {
  constructor({ quantumUs = 50, quantumMaxUs = 1000 } = {}) {
    this.quantumUs = quantumUs; this.quantumMaxUs = quantumMaxUs;
    this.boards = new Map(); this.nets = []; this.i2cSlaves = new Map(); this.timers = []; this.listeners = new Map();
    this.t = 0; this.analogProvider = null; this.stats = { yields: 0, slices: 0 };
    this.spiDevs = []; this.i2cDevs = [];   // chips on a board's buses (device.mjs): {board, spi(host, bytes, t)} / {board, addr, reachable(), write(bytes, t), read(n, t)}
  }
  async addBoard(name, factory, kind) { const b = new Board(this, name, factory, kind); await b.init(); this.boards.set(name, b); return b; }
  board(n) { const b = this.boards.get(n); if (!b) throw new Error('no board ' + n); return b; }

  net(name, members) {
    const net = { name, members: members.map(([bn, pin]) => ({ board: this.board(bn), pin })), last: null };
    for (const m of net.members) m.board.netOf[m.pin] = net;
    this.nets.push(net); return net;
  }
  netValue(net) {
    if (!net) return undefined;
    const drivers = net.members.filter((m) => m.board.mode[m.pin] === OUTPUT);
    if (!drivers.length) return undefined;
    const d = drivers.find((m) => m === net.last) || drivers[0];
    return d.board.outVal[d.pin];
  }
  pinMode(b, pin, mode, outval) {
    b.mode[pin] = mode; if (mode === OUTPUT) b.outVal[pin] = outval;
    const net = b.netOf[pin];
    if (net) { net.last = mode === OUTPUT ? net.members.find((m) => m.board === b && m.pin === pin) : net.last; for (const m of net.members) m.board.refresh(m.pin); }
    else b.refresh(pin);
    if (mode === OUTPUT) this.emit('pin', { board: b.name, pin, val: outval, t: this.t, init: true });
  }
  pinWrite(b, pin, val, t) {
    b.outVal[pin] = val;
    const net = b.netOf[pin];
    if (net) { net.last = net.members.find((m) => m.board === b && m.pin === pin); for (const m of net.members) if (m.board !== b || m.pin !== pin) m.board.refresh(m.pin); }
    this.emit('pin', { board: b.name, pin, val, t });
  }
  analogRead(b, pin, t) {
    if (this.analogProvider) { const v = this.analogProvider(b.name, pin, t); if (v !== undefined) return v; }
    if (b.ext[pin] !== undefined) return b.ext[pin];
    const nv = this.netValue(b.netOf[pin]);
    if (nv !== undefined) return nv;
    return b.mode[pin] === INPUT_PULLUP ? 1 : 0;
  }
  // bytes clocked out on an SPI host of board b: every chip on that board decides by its own pins (SCK / MOSI / CS / DC) whether it takes them
  spiTx(b, host, bytes, t) {
    const h = b.spiHosts[host] || {};
    for (const d of this.spiDevs) if (d.board === b) d.spi(h, bytes, t);
  }
  i2cDev(b, addr) { return this.i2cDevs.find((d) => d.board === b && d.addr === addr && d.reachable()); }
  // master read (Wire.requestFrom): answered by a chip at that address; another board as an I2C slave does not answer reads (no onRequest)
  i2cRx(from, addr, n, t) {
    const d = this.i2cDev(from, addr), bytes = d ? d.read(n, t) : null;
    this.emit('i2c', { from: from.name, to: d ? d.id : null, addr, bytes: bytes ? [...bytes] : [], read: true, t });
    return bytes;
  }
  i2cTx(from, addr, bytes, t) {
    const d = this.i2cDev(from, addr);
    if (d) { d.write(bytes, t); this.emit('i2c', { from: from.name, to: d.id, addr, bytes: [...bytes], t }); return 0; }
    const to = this.i2cSlaves.get(addr);
    this.emit('i2c', { from: from.name, to: to ? to.name : null, addr, bytes: [...bytes], t });
    if (!to) return 2;  // NACK on address
    to.mail(1, bytes); to.poke(t + 250);
    return 0;
  }

  on(evt, fn) { (this.listeners.get(evt) || this.listeners.set(evt, []).get(evt)).push(fn); }
  emit(evt, data) { const l = this.listeners.get(evt); if (l) for (const f of l) f(data); }
  at(tUs, fn) { this.timers.push({ t: tUs, fn }); this.timers.sort((a, b) => a.t - b.t); }

  // Advance virtual time to untilUs. Returns wall-clock seconds spent.
  run(untilUs) {
    const w0 = performance.now();
    const boards = [...this.boards.values()];
    for (;;) {
      let bb = null, bt = Infinity;
      for (const b of boards) if (!b.dead && b.wake < bt) { bt = b.wake; bb = b; }
      const tt = this.timers.length ? this.timers[0].t : Infinity;
      if (Math.min(bt, tt) > untilUs) break;
      if (tt <= bt) { const tm = this.timers.shift(); this.t = Math.max(this.t, tm.t); tm.fn(this.t); continue; }
      this.t = Math.max(this.t, bt); this.stats.slices++;
      bb.resume(bt);
    }
    this.t = untilUs;
    return (performance.now() - w0) / 1000;
  }
}
