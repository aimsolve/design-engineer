// VM: a machine (built by the machine's own sim/machine.mjs) paced to the wall clock. Isomorphic ES module (browser + node).
//
//   const vm = new VM({ vc, machineModule, loadFirmware }); // vc: the runtime index.mjs namespace ({Sim, StepperRecorder, ...}); machineModule: { META, buildMachine }; loadFirmware: (name) => emscripten factory
//   await vm.init();                                      // builds the machine (boards instantiated, paused)
//   each animation frame:  vm.tick(performance.now());    // advances the sim by (wall dt x speed), at most `budgetMs` of CPU
//
// Virtual time (sim.t, µs) only advances inside tick()/step(). Speed: number (x1 = real time) or 'max' (as fast as the budget allows).
// The render loop is never blocked: tick() works in slices of virtual time and stops when the per-frame CPU budget is spent.

export const SPEEDS = [0.25, 1, 5, 20, 'max'];

// Browser firmware loader: fetches <base>/<name><ext> (emscripten ES module) and <name>.wasm ONCE, then instantiates from the bytes
// (wasmBinary) — no streaming compile, so the server's Content-Type for .wasm does not matter, and Reset re-instantiates without re-downloading.
// ext: a site built by web-viewer build.py ships the module as .js (static hosts serve .mjs with a non-JavaScript type).
export function browserFirmwareLoader(base, ext = '.mjs') {
  const cache = new Map();
  return async (name) => {
    if (!cache.has(name)) {
      cache.set(name, (async () => {
        const modUrl = new URL(`${name}${ext}`, base).href;
        const [mod, bin] = await Promise.all([import(modUrl), fetch(new URL(`${name}.wasm`, base)).then((r) => { if (!r.ok) throw new Error(`${name}.wasm ${r.status}`); return r.arrayBuffer(); })]);
        return (opts) => mod.default({ ...opts, wasmBinary: bin, locateFile: (f) => new URL(f, base).href });
      })());
    }
    return cache.get(name);
  };
}

export class VM {
  constructor({ vc, machineModule, loadFirmware, budgetMs = 8, maxBudgetMs = 12, logLines = 1000, buildOpts = {} }) {
    Object.assign(this, { vc, mod: machineModule, loadFirmware, budgetMs, maxBudgetMs, logLines, buildOpts });
    this.meta = machineModule.META;
    this.fw = {};                 // board id -> firmware name override
    this.speed = 1; this.paused = false; this.m = null; this.busy = false; this.error = null;
    this.owed = 0; this.last = null; this.win = []; this.achieved = 0; this.lagging = false; this.sliceUs = 2000;
    this.resets = 0; this.wallBusyMs = 0; this.listeners = []; this.bleListeners = [];
  }
  onChange(fn) { this.listeners.push(fn); }
  onBle(fn) { this.bleListeners.push(fn); }   // every BLE TX notification of every machine incarnation (survives reset / firmware switch): fn({board, uuid, text, t})
  emit() { for (const f of this.listeners) f(this); }

  async init() {
    this.busy = true;
    try {
      const old = this.m;
      this.error = null;
      this.logs = {}; this.i2c = {}; this.ble = [];
      for (const b of this.meta.boards) { this.logs[b.id] = []; this.i2c[b.id] = []; }
      const m = await this.mod.buildMachine({ vc: this.vc, loadFirmware: this.loadFirmware, firmware: { ...this.fw }, ...this.buildOpts });
      const push = (arr, x) => { arr.push(x); if (arr.length > this.logLines) arr.splice(0, arr.length - this.logLines); };
      m.sim.on('serial', (e) => { if (this.logs[e.board]) push(this.logs[e.board], { t: e.t, line: e.line }); });
      m.sim.on('i2c', (e) => { for (const id of [e.from, e.to]) if (id && this.i2c[id]) push(this.i2c[id], { t: e.t, from: e.from, to: e.to, addr: e.addr, bytes: e.bytes }); });
      m.sim.on('ble-tx', (e) => { push(this.ble, { t: e.t, text: e.text }); for (const f of this.bleListeners) f(e); });
      m.sim.on('crash', (e) => { this.error = `${e.board}: ${e.error && e.error.message || e.error}`; this.emit(); });
      this.m = m; this.owed = 0; this.last = null; this.win = []; this.achieved = 0; this.lagging = false;
      this.resets++;
      if (old && old.dispose) old.dispose();
    } catch (e) { this.error = String(e && e.message || e); throw e; } finally { this.busy = false; this.emit(); }
  }
  async reset() { this.paused = false; await this.init(); }
  async setFirmware(boardId, name) { this.fw[boardId] = name; await this.init(); }
  fwName(boardId) { return this.fw[boardId] || this.meta.boards.find((b) => b.id === boardId).fw; }

  setSpeed(x) { this.speed = x === 'max' || x === Infinity ? 'max' : Math.max(0.01, +x); this.owed = 0; this.win = []; this.lagging = false; this.emit(); }
  pause() { this.paused = true; this.emit(); }
  resume() { this.paused = false; this.last = null; this.emit(); }

  // Advance by wall-clock time. Call once per animation frame.
  tick(nowMs) {
    if (!this.m || this.busy) { this.last = nowMs; return 0; }
    const dt = this.last == null ? 0 : Math.min(nowMs - this.last, 100);   // a hidden tab must not create a huge debt
    this.last = nowMs;
    if (this.paused || this.error) return 0;
    const max = this.speed === 'max', budget = max ? this.maxBudgetMs : this.budgetMs;
    if (!max) this.owed += dt * 1000 * this.speed;
    const cap = max ? Infinity : 250 * 1000 * this.speed;      // never owe more than 250 ms of wall time: drop the rest and report it
    if (this.owed > cap) this.owed = cap;
    const sim = this.m.sim, t0 = performance.now(), tStart = sim.t;
    while (max || this.owed > 0) {
      const now = performance.now();
      if (now - t0 >= budget) break;
      const slice = max ? this.sliceUs : Math.min(this.owed, this.sliceUs);
      const w = performance.now();
      sim.run(sim.t + slice);
      const wallMs = Math.max(performance.now() - w, 0.01);
      if (!max) this.owed -= slice;
      this.sliceUs = Math.max(500, Math.min(100000, Math.round((slice / wallMs) * 1.5)));   // aim for ~1.5 ms of CPU per slice
    }
    this.m.sample && this.m.sample();
    const adv = sim.t - tStart;
    this.wallBusyMs += performance.now() - t0;
    this.win.push([dt, adv]);
    let sw = 0, sa = 0, i = this.win.length;
    while (i > 0 && sw < 1000) { i--; sw += this.win[i][0]; sa += this.win[i][1]; }
    this.win.splice(0, i);
    if (sw > 0) this.achieved = sa / 1e6 / (sw / 1000);
    // "can't keep up": achieved (virtual s per real s over the last second) clearly below the requested speed
    this.lagging = !max && sw > 400 && this.achieved < this.speed * 0.9;
    return adv;
  }

  // Deterministic advance by ms of virtual time, ignoring pacing (tests, scripted bindings).
  step(ms) { if (!this.m) return 0; const t0 = this.m.sim.t; this.m.sim.run(t0 + ms * 1000); this.m.sample && this.m.sample(); return this.m.sim.t - t0; }

  clock() { return this.m ? this.m.sim.t / 1e6 : 0; }
  clockText() { const s = this.clock(), m = Math.floor(s / 60); return `${String(m).padStart(2, '0')}:${(s - m * 60).toFixed(1).padStart(4, '0')}`; }
}
