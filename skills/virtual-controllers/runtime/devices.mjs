// Generic recorders and fake devices for virtual-controllers (board-generic; machine wiring lives in the machine's sim/ folder).

// ---- stepper recorder: counts STEP rising edges (or a hardware-timer toggle clock) and the DIR level at that moment ----
// finish() turns the raw step times into segments [{t0, t1, n, dir, rate}]: 100 ms bins, a new segment whenever the
// stepper starts/stops, reverses, or its rate changes by more than 35 % (so 2500 -> 1000 -> 150 steps/s show as 3 segments).
export class StepperRecorder {
  constructor(sim, { name, board, step, dir = null, dirInvert = false, onStep = null, onSteps = null }) {
    Object.assign(this, { sim, name, board, step, dirCfg: dir, dirInvert, onStep, onSteps });
    this.times = []; this.dirs = []; this.clockRuns = []; this.total = 0; this.pos = 0; this.runs = [];
    sim.on('pin', (e) => { if (e.board === board && e.pin === step && e.val === 1 && !e.init) this.edge(e.t); });
    sim.on('clock', (e) => { if (e.board === board && e.pin === step) this.clock(e); });
  }
  dirNow() {
    if (!this.dirCfg) return 1;
    const lv = this.sim.board(this.dirCfg.board ?? this.board).level(this.dirCfg.pin);
    return (lv ? 1 : -1) * (this.dirInvert ? -1 : 1);
  }
  edge(t) {
    const dir = this.dirNow();
    this.total++; this.pos += dir; this.times.push(t); this.dirs.push(dir);
    if (this.onStep) this.onStep(t, dir);
  }
  clock(e) {
    if (e.on) this.clk = { t0: e.t, hz: e.hz };
    else if (this.clk) {
      const n = Math.round(((e.t - this.clk.t0) / 1e6) * (this.clk.hz / 2));  // toggling at hz -> hz/2 rising edges per second
      this.clockRuns.push({ t0: this.clk.t0, t1: e.t, n, dir: this.dirNow(), rate: this.clk.hz / 2, clock: true });
      this.total += n; this.pos += n * this.dirNow(); if (this.onSteps) this.onSteps(this.clk.t0, e.t, n); this.clk = null;
    }
  }
  // steps and signed position including a timer-clock run still in flight (for live displays)
  live() {
    if (!this.clk) return { total: this.total, pos: this.pos };
    const n = Math.round(((this.sim.t - this.clk.t0) / 1e6) * (this.clk.hz / 2));
    return { total: this.total + n, pos: this.pos + n * this.dirNow() };
  }
  finish() {
    if (this.clk) this.clock({ on: false, t: this.sim.t });
    const W = 100000, runs = [...this.clockRuns];
    let cur = null;
    const closeCur = () => { if (cur) { runs.push({ t0: cur.t0, t1: cur.t1, n: cur.n, dir: cur.dir, rate: cur.n / Math.max((cur.t1 - cur.t0) / 1e6, 1e-3) }); cur = null; } };
    let i = 0;
    while (i < this.times.length) {
      const bin = Math.floor(this.times[i] / W); let j = i, dsum = 0;
      while (j < this.times.length && Math.floor(this.times[j] / W) === bin) dsum += this.dirs[j++];
      const n = j - i, dir = dsum >= 0 ? 1 : -1, rate = n / (W / 1e6);
      const t0 = this.times[i], t1 = this.times[j - 1];
      if (cur && bin * W - cur.t1 < 2 * W && dir === cur.dir && Math.abs(rate - cur.wrate) / Math.max(rate, cur.wrate) < 0.35) { cur.n += n; cur.t1 = t1; cur.wrate = rate; }
      else { closeCur(); cur = { t0, t1, n, dir, wrate: rate }; }
      i = j;
    }
    closeCur();
    this.runs = runs.sort((a, b) => a.t0 - b.t0);
    return this.runs;
  }
}

// ---- one-wire UART bus (SoftwareSerial / GyverBus with all boards on one wire) ----
// Every byte a member writes (hook 'uart.tx', shim/SoftwareSerial.h) reaches all the OTHER members at the end of its stop bit
// as UART mail (VC_MAIL_UART = 5). No collisions and no line echo are modelled. members = board names, null = every board.
export function uartBus(sim, members = null) {
  const on = (name) => !members || members.includes(name);
  sim.on('hook', (e) => {
    if (e.name !== 'uart.tx' || !on(e.board)) return;
    for (const b of sim.boards.values()) if (b.name !== e.board && on(b.name)) sim.at(e.t, () => { if (!b.dead) b.mail(5, [e.a]); });
  });
}

// ---- pin change logger (handshake wires etc.) ----
export class PinLogger {
  constructor(sim, watch) {  // watch: [{name, board, pin}]
    this.log = [];
    sim.on('pin', (e) => { const w = watch.find((x) => x.board === e.board && x.pin === e.pin); if (w) this.log.push({ t: e.t, name: w.name, val: e.val, init: !!e.init }); });
  }
}

// ---- fake HX711 (24-bit load-cell ADC): the REAL bit-banged protocol against the board's DT/SCK pins ----
// The firmware's HX711 library toggles SCK and reads DOUT for real; this device answers as the chip does:
// DOUT low = conversion ready; each SCK rising edge shifts out one bit (MSB first, 24 bits, two's complement);
// 25/26/27 total pulses select gain 128/32/64 (we only count, gain doesn't change the fake sample); SCK high >= 60 us
// = power down/reset (data ready again after settleUs). Conversions run free at rateHz (10 SPS = RATE pin low).
export class FakeHx711 {
  constructor(sim, { board, dout, sck, rateHz = 10, settleUs = 400000, sample }) {
    Object.assign(this, { sim, board, dout, sck, period: 1e6 / rateHz, settleUs, sample });
    this.b = sim.board(board);
    this.ready = false; this.pulses = 0; this.raw = 0; this.rise = 0; this.fall = -1e9; this.tail = false; this.reads = 0; this.gainPulses = null;
    this.b.drive(dout, 1);
    this.schedule(this.period);
    sim.on('pin', (e) => { if (e.board === board && e.pin === sck && !e.init) this.onSck(e.val, e.t); });
  }
  schedule(t) { this.sim.at(t, (now) => { this.ready = true; this.raw = this.sample(now) & 0xffffff; this.b.drive(this.dout, 0); }); }
  nextConv(t) { return (Math.floor(t / this.period) + 1) * this.period; }
  onSck(val, t) {
    if (val === 1) {
      this.rise = t;
      if (this.tail && t - this.fall < 20) { this.pulses++; return; }  // gain-select pulses 26/27 follow the 25th within us
      if (this.tail) { this.tail = false; this.pulses = 0; }  // previous read is over
      if (!this.ready) return;
      const k = ++this.pulses;
      this.b.drive(this.dout, k <= 24 ? (this.raw >> (24 - k)) & 1 : 1);
    } else {
      if (t - this.rise >= 60) {  // power down / reset
        this.pulses = 0; this.tail = false; this.ready = false; this.b.drive(this.dout, 1); this.schedule(t + this.settleUs); return;
      }
      this.fall = t;
      if (this.tail) { this.gainPulses = this.pulses - 24; return; }
      if (this.ready && this.pulses >= 25) {
        this.gainPulses = this.pulses - 24; this.reads++; this.tail = true;
        this.ready = false; this.b.drive(this.dout, 1); this.schedule(this.nextConv(t));
      }
    }
  }
}
