#include "vc_core.h"
#include <emscripten.h>
#include <stdarg.h>

VcShared vc_shared;
HardwareSerial Serial;

static double g_now = 0, g_last_sync = 0;
static bool g_running = false;  // false during static constructors (before main): they run outside ASYNCIFY and must not yield
void vc_start() { g_running = true; g_last_sync = g_now; }
static int g_in_isr = 0, g_in_service = 0;
static uint32_t g_activity = 0;
static double g_q = 50;

struct Irq { void (*fn)(); int mode; uint8_t last; };
static Irq g_irq[VC_MAX_PINS];
static uint8_t g_irq_pins[VC_MAX_PINS];
static int g_irq_n = 0;
static VcMailHandler g_mail[8];
static VcService g_svc[8];
static int g_svc_n = 0;
static double g_next_due = INFINITY;

void vc_register_mail(int type, VcMailHandler h) { if (type >= 0 && type < 8) g_mail[type] = h; }
void vc_register_service(VcService fn) { if (g_svc_n < 8) g_svc[g_svc_n++] = fn; }
void vc_wake_at(double t) { if (t < g_next_due) g_next_due = t; }
double vc_now() { return g_now; }
void vc_activity() { g_activity = 1; }

static void vc_service() {
  if (g_in_service) return;
  g_in_service = 1;
  for (int i = 0; i < g_irq_n; i++) {
    int p = g_irq_pins[i];
    if (vc_shared.pin_mode[p] == OUTPUT) continue;
    uint8_t cur = vc_shared.pin_in[p];
    if (cur == g_irq[p].last) continue;
    g_irq[p].last = cur;
    int m = g_irq[p].mode;
    if (m == CHANGE || (m == FALLING && cur == 0) || (m == RISING && cur == 1)) {
      g_activity = 1;
      g_in_isr++;
      g_irq[p].fn();
      g_in_isr--;
    }
  }
  while (vc_shared.mb_tail != vc_shared.mb_head) {
    VcMsg m = vc_shared.mb[vc_shared.mb_tail % VC_MB_SIZE];
    vc_shared.mb_tail++;
    g_activity = 1;
    if (m.type < 8 && g_mail[m.type]) g_mail[m.type](m);  // may call delay(): runs like a callback on the main task
  }
  if (g_svc_n && g_now >= g_next_due) {
    double next = INFINITY;
    g_next_due = INFINITY;            // what a callback queues now (vc_wake_at) must survive the recomputation below
    g_in_isr++;
    for (int i = 0; i < g_svc_n; i++) { double n = g_svc[i](g_now); if (n < next) next = n; }
    g_in_isr--;
    if (next < g_next_due) g_next_due = next;
    g_activity = 1;
  }
  g_in_service = 0;
}
void vc_poll() { vc_service(); }

static void vc_sync() {
  vc_service();
  if (g_in_isr) return;
  double base = vc_shared.quantum_us ? vc_shared.quantum_us : 50;
  double mx = vc_shared.quantum_max_us ? vc_shared.quantum_max_us : 1000;
  if (g_activity) { g_q = base; g_activity = 0; }
  else g_q = g_q * 2 > mx ? mx : g_q * 2;   // idle (only reading): back off, fewer yields
  g_now = vc_js_yield(g_now, g_now);
  g_last_sync = g_now;
  vc_service();
}

void vc_advance(double us) {
  g_now += us;
  if (g_running && !g_in_isr && g_now - g_last_sync >= g_q) vc_sync();
}

void vc_sleep_until(double target) {
  if (!g_running) { if (target > g_now) g_now = target; return; }
  if (g_in_isr) { g_now = target > g_now ? target : g_now; return; }
  vc_service();
  while (g_now < target) {
    // a timed service due first ends the nap; one already due (inside a mail handler the services cannot run) does not: no spin
    double w = (g_next_due > g_now && g_next_due < target) ? g_next_due : target;
    g_now = vc_js_yield(g_now, w);  // returns early if poked (IRQ edge, I2C, BLE)
    vc_service();
  }
  g_last_sync = g_now;
  g_activity = 1;  // a real sleep: next quantum starts fresh
  g_q = vc_shared.quantum_us ? vc_shared.quantum_us : 50;
}

unsigned long millis() { vc_advance(VC_COST_MISC); return (unsigned long)(uint32_t)(g_now / 1000.0); }
unsigned long micros() { vc_advance(VC_COST_MICROS); return (unsigned long)(uint32_t)g_now; }
void delay(unsigned long ms) { vc_sleep_until(g_now + (double)ms * 1000.0); }
void delayMicroseconds(unsigned int us) {
  if (us >= vc_shared.quantum_us && vc_shared.quantum_us) vc_sleep_until(g_now + us);
  else vc_advance(us);
}

void pinMode(uint8_t pin, uint8_t mode) {
  if (pin >= VC_MAX_PINS) return;
  vc_shared.pin_mode[pin] = mode;
  vc_js_pin_mode(pin, mode, vc_shared.pin_out[pin]);
  vc_advance(VC_COST_DW);
}
void digitalWrite(uint8_t pin, uint8_t val) {
  if (pin >= VC_MAX_PINS) return;
  val = val ? 1 : 0;
  if (vc_shared.pin_out[pin] != val || vc_shared.pin_mode[pin] != OUTPUT) {
    vc_shared.pin_out[pin] = val;
    if (vc_shared.pin_mode[pin] == OUTPUT) { vc_js_pin_write(pin, val, g_now); g_activity = 1; }
  }
  vc_advance(VC_COST_DW);
}
int digitalRead(uint8_t pin) {
  vc_advance(VC_COST_DR);  // advance first: a yield inside refreshes pin_in
  if (pin >= VC_MAX_PINS) return 0;
  return vc_shared.pin_mode[pin] == OUTPUT ? vc_shared.pin_out[pin] : vc_shared.pin_in[pin];
}
int analogRead(uint8_t pin) {
  vc_advance(VC_COST_AR);
  double f = vc_js_analog_read(pin, g_now);
  int v = (int)(f * VC_ADC_MAX + 0.5);
  return v < 0 ? 0 : v > VC_ADC_MAX ? VC_ADC_MAX : v;
}
void attachInterrupt(uint8_t pin, void (*fn)(), int mode) {
  if (pin >= VC_MAX_PINS) return;
  if (!g_irq[pin].fn) g_irq_pins[g_irq_n++] = pin;
  g_irq[pin].fn = fn; g_irq[pin].mode = mode; g_irq[pin].last = vc_shared.pin_in[pin];
  vc_js_attach_irq(pin, mode);
}
uint8_t shiftIn(uint8_t d, uint8_t c, uint8_t order) {
  uint8_t v = 0;
  for (int i = 0; i < 8; i++) {
    digitalWrite(c, HIGH);
    int b = digitalRead(d);
    if (order == LSBFIRST) v |= b << i; else v |= b << (7 - i);
    digitalWrite(c, LOW);
  }
  return v;
}

// ---- Serial ----
void HardwareSerial::out(const char* p, size_t n) {
  vc_js_serial((const uint8_t*)p, (int)n, g_now);
  g_activity = 1;
  // UART model: 10 bit-times per char, a TX ring buffer in front of it; the sketch only blocks when the buffer is full.
  const double ct = 10.0e6 / (double)baud;
#ifdef VC_BOARD_AVR
  const double cap = 64;
#else
  const double cap = 256;
#endif
  double occ = tx_free_at > g_now ? (tx_free_at - g_now) / ct : 0;
  if (occ + n > cap) vc_sleep_until(g_now + (occ + n - cap) * ct);
  tx_free_at = (tx_free_at > g_now ? tx_free_at : g_now) + n * ct;
  vc_advance(n * 1.0);
}
size_t HardwareSerial::printf(const char* fmt, ...) {
  char small[256];
  va_list ap; va_start(ap, fmt);
  int n = vsnprintf(small, sizeof small, fmt, ap);
  va_end(ap);
  if (n < 0) return 0;
  if (n < (int)sizeof small) { out(small, n); return n; }
  std::string big(n + 1, '\0');
  va_start(ap, fmt); vsnprintf(&big[0], n + 1, fmt, ap); va_end(ap);
  out(big.data(), n); return n;
}
int HardwareSerial::available() {
  vc_advance(VC_COST_MISC);
  return (int)(vc_shared.sr_head - vc_shared.sr_tail);
}
int HardwareSerial::read() {
  vc_advance(VC_COST_MISC);
  if (vc_shared.sr_head == vc_shared.sr_tail) return -1;
  return vc_shared.sr[vc_shared.sr_tail++ & 255];
}
int HardwareSerial::peek() {
  if (vc_shared.sr_head == vc_shared.sr_tail) return -1;
  return vc_shared.sr[vc_shared.sr_tail & 255];
}
float HardwareSerial::parseFloat() {
  std::string t;
  for (int c = peek(); c >= 0 && (isdigit(c) || c == '-' || c == '.' || c == ' ' || c == '\n' || c == '\r'); c = peek()) { t += (char)read(); }
  return strtof(t.c_str(), nullptr);
}
long HardwareSerial::parseInt() { return (long)parseFloat(); }

// ---- exports for the JS side ----
extern "C" {
EMSCRIPTEN_KEEPALIVE uint32_t vc_shared_addr() { return (uint32_t)(size_t)&vc_shared; }
EMSCRIPTEN_KEEPALIVE uint32_t vc_layout(int i) {
  switch (i) {
    case 0: return offsetof(VcShared, pin_in);
    case 1: return offsetof(VcShared, pin_out);
    case 2: return offsetof(VcShared, pin_mode);
    case 3: return offsetof(VcShared, quantum_us);
    case 4: return offsetof(VcShared, quantum_max_us);
    case 5: return offsetof(VcShared, mb_head);
    case 6: return offsetof(VcShared, mb_tail);
    case 7: return offsetof(VcShared, sr_head);
    case 8: return offsetof(VcShared, sr_tail);
    case 9: return offsetof(VcShared, sr);
    case 10: return offsetof(VcShared, mb);
    case 11: return sizeof(VcMsg);
  }
  return 0;
}
}
