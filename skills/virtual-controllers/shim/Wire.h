// I2C. Master: endTransmission() sends through JS (routed to a chip model on the bus, or to a slave board); requestFrom() reads from a
// chip model (a slave board does not answer reads: no onRequest). Slave: JS drops the message into the mailbox; it is delivered at the
// next sync point and onReceive() is called (like a TWI interrupt). Bus time: 9 bit-times per byte incl. the address, at setClock().
#pragma once
#include "vc_core.h"
class TwoWire {
  uint8_t txa = 0, tx[128];
  int txn = 0;
  uint8_t rx[128];
  int rxn = 0, rxp = 0;
  double bit_us = 10.0;   // 100 kHz
  void (*onRecv)(int) = nullptr;
 public:
  void begin() {}
  void begin(uint8_t addr) { vc_js_i2c_register(addr); }
  void begin(int addr) { vc_js_i2c_register(addr); }
  bool begin(int sda, int scl, uint32_t hz = 0) { vc_js_i2c_pins(sda, scl); if (hz) setClock(hz); return true; }   // ESP32: Wire.begin(SDA, SCL)
  void setClock(uint32_t hz) { if (hz) bit_us = 1e6 / (double)hz; }
  void onReceive(void (*f)(int)) { onRecv = f; }
  void beginTransmission(uint8_t a) { txa = a; txn = 0; }
  void beginTransmission(int a) { beginTransmission((uint8_t)a); }
  size_t write(uint8_t b) { if (txn < (int)sizeof tx) tx[txn++] = b; return 1; }
  size_t write(const uint8_t* p, size_t n) { for (size_t i = 0; i < n; i++) write(p[i]); return n; }
  uint8_t endTransmission(bool stop = true) {
    (void)stop;
    int st = vc_js_i2c_tx(txa, tx, txn, vc_now());
    vc_activity();
    vc_advance(bit_us * 9 * (txn + 1));
    return st;
  }
  // one signature for every integer form the cores offer (uint8_t / int / uint16_t + size_t, optional sendStop)
  uint8_t requestFrom(int addr, int n, int stop = 1) {
    (void)stop;
    if (n > (int)sizeof rx) n = sizeof rx;
    rxn = n > 0 ? vc_js_i2c_rx(addr, rx, n, vc_now()) : 0; rxp = 0;
    vc_activity();
    vc_advance(bit_us * 9 * (rxn + 1));
    return (uint8_t)rxn;
  }
  int available() { return rxn - rxp; }
  int read() { return rxp < rxn ? rx[rxp++] : -1; }
  int peek() { return rxp < rxn ? rx[rxp] : -1; }
  void __deliver(const VcMsg& m) {
    rxn = m.len > 128 ? 128 : m.len; rxp = 0; memcpy(rx, m.data, rxn);
    if (onRecv) onRecv(rxn);
  }
};
inline TwoWire Wire;
inline void vc_wire_rx(const VcMsg& m) { Wire.__deliver(m); }
inline int vc_wire_reg = (vc_register_mail(1, vc_wire_rx), 0);
