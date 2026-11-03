// Emscripten JS library: the imports of vc_core.h. Everything forwards to Module.vc, the per-board hook object
// created by runtime/board.mjs. Only vc_js_yield is asynchronous (ASYNCIFY).
mergeInto(LibraryManager.library, {
  vc_js_yield__deps: ['$Asyncify'],
  vc_js_yield: function (now, target) {
    return Asyncify.handleSleep(function (wakeUp) { Module['vc'].yield(now, target, wakeUp); });
  },
  vc_js_pin_mode: function (pin, mode, outval) { Module['vc'].pinMode(pin, mode, outval); },
  vc_js_pin_write: function (pin, val, t) { Module['vc'].pinWrite(pin, val, t); },
  vc_js_pin_clock: function (pin, hz, on, t) { Module['vc'].pinClock(pin, hz, on, t); },
  vc_js_attach_irq: function (pin, mode) { Module['vc'].attachIrq(pin, mode); },
  vc_js_analog_read: function (pin, t) { return Module['vc'].analogRead(pin, t); },
  vc_js_serial: function (p, len, t) { Module['vc'].serial(HEAPU8.slice(p, p + len), t); },
  vc_js_i2c_tx: function (addr, p, len, t) { return Module['vc'].i2cTx(addr, HEAPU8.slice(p, p + len), t); },
  vc_js_i2c_register: function (addr) { Module['vc'].i2cRegister(addr); },
  vc_js_ble_notify: function (uuid, p, len, t) { Module['vc'].bleNotify(UTF8ToString(uuid), HEAPU8.slice(p, p + len), t); },
  vc_js_i2c_rx: function (addr, p, n, t) {
    var r = Module['vc'].i2cRx(addr, n, t); if (!r || !r.length) return 0;
    var k = Math.min(r.length, n); HEAPU8.set(r.subarray ? r.subarray(0, k) : r.slice(0, k), p); return k;
  },
  vc_js_i2c_pins: function (sda, scl) { Module['vc'].i2cPins(sda, scl); },
  vc_js_spi_begin: function (host, sck, mosi, miso) { Module['vc'].spiBegin(host, sck, mosi, miso); },
  vc_js_spi: function (host, p, len, t) { Module['vc'].spi(host, HEAPU8.subarray(p, p + len), t); },
  vc_js_hook: function (name, a, b, t) { Module['vc'].hook(UTF8ToString(name), a, b, t); },
});
