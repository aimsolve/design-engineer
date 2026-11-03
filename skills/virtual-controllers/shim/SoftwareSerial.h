// SoftwareSerial on a shared wire: every byte written goes to JS (hook "uart.tx", a = byte, b = TX pin, t = end of its stop bit);
// the machine's sim routes it to the boards on the same bus, which get it as mail VC_MAIL_UART at that time.
// TX blocks for 10 bit-times per byte like the real bit-banged SoftwareSerial; RX ring buffer 64 bytes (AVR), overflow drops bytes.
// One instance per sketch: the last one constructed receives. Routing between boards: runtime/devices.mjs uartBus().
#pragma once
#include "vc_ext.h"
class SoftwareSerial;
inline SoftwareSerial* vc_soft_serial = nullptr;
class SoftwareSerial : public Stream {
  uint8_t rxp, txp;
  unsigned long baud = 9600;
  uint8_t buf[64];
  uint8_t head = 0, tail = 0;
  bool ovf = false;
 public:
  SoftwareSerial(uint8_t rx, uint8_t tx, bool inverse = false) : rxp(rx), txp(tx) { (void)inverse; vc_soft_serial = this; }
  void begin(unsigned long b) { baud = b; char n[48]; snprintf(n, sizeof n, "uart.begin:%d:%d", rxp, txp); vc_js_hook(n, (int)b, 0, vc_now()); }
  void end() {}
  bool listen() { return true; }
  bool isListening() { return true; }
  bool overflow() { bool o = ovf; ovf = false; return o; }
  int available() override { vc_advance(VC_COST_MISC); return (uint8_t)(head - tail) & 63; }
  int read() override { vc_advance(VC_COST_MISC); if (head == tail) return -1; uint8_t c = buf[tail]; tail = (tail + 1) & 63; return c; }
  int peek() override { return head == tail ? -1 : buf[tail]; }
  size_t write(uint8_t c) override {
    double bt = 10.0e6 / (double)baud;
    vc_js_hook("uart.tx", c, txp, vc_now() + bt);
    vc_activity();
    vc_advance(bt);
    return 1;
  }
  using Stream::write;
  void __rx(const VcMsg& m) {
    for (int i = 0; i < m.len; i++) {
      uint8_t nh = (head + 1) & 63;
      if (nh == tail) { ovf = true; continue; }
      buf[head] = m.data[i]; head = nh;
    }
  }
};
inline void vc_soft_serial_rx(const VcMsg& m) { if (vc_soft_serial) vc_soft_serial->__rx(m); }
inline int vc_soft_serial_reg = (vc_register_mail(VC_MAIL_UART, vc_soft_serial_rx), 0);
