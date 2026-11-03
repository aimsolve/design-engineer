// virtual-controllers: board-generic Arduino API shim (time, pins, Serial, String).
// One wasm instance = one board = one copy of this state. Virtual time is per board (microseconds);
// the JS scheduler keeps boards in step (see runtime/sim.mjs). Board flavour comes from -DVC_BOARD_ESP32 / -DVC_BOARD_AVR.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <string>
#include <algorithm>

#define ARDUINO 10819
#if defined(VC_BOARD_ESP32)
  #define ARDUINO_ARCH_ESP32 1
  #define VC_ADC_MAX 4095
  // virtual cost of one call, microseconds (rough; tunable, only matters for busy loops that never delay)
  #define VC_COST_DW 0.3
  #define VC_COST_DR 0.3
  #define VC_COST_AR 30.0
  #define VC_COST_MICROS 0.5
  #define VC_COST_MISC 0.5
  #define VC_COST_LOOP 2.0    // one pass of loop() that made no costed call (pure CPU work)
  typedef int portMUX_TYPE;
  #define portMUX_INITIALIZER_UNLOCKED 0
  #define portENTER_CRITICAL(x) ((void)(x))
  #define portEXIT_CRITICAL(x) ((void)(x))
  #define IRAM_ATTR
#elif defined(VC_BOARD_AVR)
  #define ARDUINO_ARCH_AVR 1
  #define VC_ADC_MAX 1023
  #define VC_COST_DW 4.0
  #define VC_COST_DR 4.0
  #define VC_COST_AR 112.0
  #define VC_COST_MICROS 4.0
  #define VC_COST_MISC 2.0
  #define VC_COST_LOOP 40.0
  #define IRAM_ATTR
#else
  #error "define VC_BOARD_ESP32 or VC_BOARD_AVR"
#endif

typedef bool boolean;
typedef uint8_t byte;
typedef uint16_t word;

#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define LSBFIRST 0
#define MSBFIRST 1
#define CHANGE 1
#define FALLING 2
#define RISING 3
#define DEC 10
#define HEX 16
#define BIN 2
#define PI 3.1415926535897932384626433832795
#define F(x) (x)
#define PROGMEM
#define digitalPinToInterrupt(p) (p)
#define noInterrupts() ((void)0)
#define interrupts() ((void)0)
#define bitRead(v, b) (((v) >> (b)) & 1)
#define _BV(b) (1UL << (b))
#ifdef VC_BOARD_AVR
static const uint8_t A0 = 14, A1 = 15, A2 = 16, A3 = 17, A4 = 18, A5 = 19, A6 = 20, A7 = 21;
#endif
// classic Arduino macros (defined after the std headers above)
#define min(a, b) ((a) < (b) ? (a) : (b))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define abs(x) ((x) > 0 ? (x) : -(x))
#define constrain(amt, low, high) ((amt) < (low) ? (low) : ((amt) > (high) ? (high) : (amt)))
#define sq(x) ((x) * (x))
#define radians(deg) ((deg) * 0.017453292519943295)
#define degrees(rad) ((rad) * 57.29577951308232)

inline long map(long x, long in_min, long in_max, long out_min, long out_max) { return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min; }

// ---- imports provided by shim/vc_lib.js (the JS side of this board) ----
extern "C" {
double vc_js_yield(double now_us, double target_us);  // ASYNCIFY import: suspend; returns the virtual time we were resumed at
void vc_js_pin_mode(int pin, int mode, int outval);
void vc_js_pin_write(int pin, int val, double t_us);
void vc_js_pin_clock(int pin, double toggle_hz, int on, double t_us);
void vc_js_attach_irq(int pin, int mode);
double vc_js_analog_read(int pin, double t_us);  // 0..1 fraction of full scale
void vc_js_serial(const uint8_t* p, int len, double t_us);
int vc_js_i2c_tx(int addr, const uint8_t* p, int len, double t_us);
void vc_js_i2c_register(int addr);
void vc_js_ble_notify(const char* uuid, const uint8_t* p, int len, double t_us);
void vc_js_hook(const char* name, int a, int b, double t_us);  // generic device event (servo etc)
int vc_js_i2c_rx(int addr, uint8_t* p, int n, double t_us);   // master read: bytes the addressed chip answers (0 = nobody)
void vc_js_i2c_pins(int sda, int scl);
void vc_js_spi_begin(int host, int sck, int mosi, int miso);    // the pins an SPI host was set up with
void vc_js_spi(int host, const uint8_t* p, int len, double t_us);   // bytes clocked out on that host
}

// ---- memory shared with JS (JS reads/writes it directly through the wasm heap; no export calls while suspended) ----
struct VcMsg {
  uint8_t type;  // 1 = i2c rx, 2 = BLE write, 3 = BLE connect, 4 = BLE disconnect
  uint8_t pad;
  uint16_t len;
  char uuid[40];
  uint8_t data[128];
};
#define VC_MAX_PINS 64
#define VC_MB_SIZE 32
struct VcShared {
  uint8_t pin_in[VC_MAX_PINS];    // what the pin reads (JS resolves nets / world / pullups)
  uint8_t pin_out[VC_MAX_PINS];   // last value written by the sketch
  uint8_t pin_mode[VC_MAX_PINS];
  uint32_t quantum_us;            // base sync quantum (JS sets before main)
  uint32_t quantum_max_us;        // adaptive quantum ceiling while the board is idle (no writes)
  volatile uint32_t mb_head, mb_tail;
  volatile uint32_t sr_head, sr_tail;
  uint8_t sr[256];
  VcMsg mb[VC_MB_SIZE];
};
extern VcShared vc_shared;

// ---- virtual time core ----
double vc_now();                  // µs
void vc_advance(double us);       // spend CPU time; yields to the scheduler when a quantum has elapsed
void vc_sleep_until(double t_us); // suspend until t (or until poked by an interrupt/mail)
void vc_start();                  // called by main(): from here on the board may yield
void vc_activity();               // mark observable activity (keeps the quantum small)
typedef void (*VcMailHandler)(const VcMsg&);
void vc_register_mail(int type, VcMailHandler h);
// timed services (DMA-like transfers finishing, their callbacks): run at every sync point like an interrupt; fn(now) does what is
// due and returns when it is next due (INFINITY = nothing pending). A sleeping board wakes at that time.
typedef double (*VcService)(double now);
void vc_register_service(VcService fn);
void vc_wake_at(double t_us);     // something is due at t: a sleep ends no later than that
void vc_poll();                   // run the services / interrupts now (busy-wait helpers)

unsigned long millis();
unsigned long micros();
void delay(unsigned long ms);
void delayMicroseconds(unsigned int us);
inline void yield() { vc_advance(VC_COST_MISC); }

// ---- GPIO ----
void pinMode(uint8_t pin, uint8_t mode);
void digitalWrite(uint8_t pin, uint8_t val);
int digitalRead(uint8_t pin);
int analogRead(uint8_t pin);
void attachInterrupt(uint8_t pin, void (*fn)(), int mode);
uint8_t shiftIn(uint8_t dataPin, uint8_t clockPin, uint8_t bitOrder);

// ---- String ----
class String {
  std::string s;
  static std::string num(long long v, int base) {
    char b[70]; if (base == 16) snprintf(b, sizeof b, "%llx", (unsigned long long)v); else snprintf(b, sizeof b, "%lld", v); return b;
  }
  static std::string unum(unsigned long long v, int base) {
    char b[70]; if (base == 16) snprintf(b, sizeof b, "%llx", v); else snprintf(b, sizeof b, "%llu", v); return b;
  }
  static std::string flt(double v, int dp) { char b[80]; snprintf(b, sizeof b, "%.*f", dp, v); return b; }
 public:
  String() {}
  String(const char* c) : s(c ? c : "") {}
  String(const std::string& x) : s(x) {}
  String(char c) : s(1, c) {}
  String(unsigned char v, unsigned char base = 10) : s(unum(v, base)) {}
  String(int v, unsigned char base = 10) : s(num(v, base)) {}
  String(unsigned int v, unsigned char base = 10) : s(unum(v, base)) {}
  String(long v, unsigned char base = 10) : s(num(v, base)) {}
  String(unsigned long v, unsigned char base = 10) : s(unum(v, base)) {}
  String(float v, unsigned char dp = 2) : s(flt(v, dp)) {}
  String(double v, unsigned char dp = 2) : s(flt(v, dp)) {}
  unsigned int length() const { return s.size(); }
  const char* c_str() const { return s.c_str(); }
  const std::string& str() const { return s; }
  bool startsWith(const String& p) const { return s.compare(0, p.s.size(), p.s) == 0; }
  bool endsWith(const String& p) const { return s.size() >= p.s.size() && s.compare(s.size() - p.s.size(), p.s.size(), p.s) == 0; }
  String substring(unsigned int from) const { return from >= s.size() ? String() : String(s.substr(from)); }
  String substring(unsigned int from, unsigned int to) const { return from >= to || from >= s.size() ? String() : String(s.substr(from, to - from)); }
  long toInt() const { return strtol(s.c_str(), nullptr, 10); }
  float toFloat() const { return strtof(s.c_str(), nullptr); }
  int indexOf(const String& p) const { size_t i = s.find(p.s); return i == std::string::npos ? -1 : (int)i; }
  int lastIndexOf(char c) const { size_t i = s.rfind(c); return i == std::string::npos ? -1 : (int)i; }
  int lastIndexOf(const String& p) const { size_t i = s.rfind(p.s); return i == std::string::npos ? -1 : (int)i; }
  int indexOf(char c) const { size_t i = s.find(c); return i == std::string::npos ? -1 : (int)i; }
  void remove(unsigned int idx) { if (idx < s.size()) s.erase(idx); }
  void remove(unsigned int idx, unsigned int n) { if (idx < s.size()) s.erase(idx, n); }
  void trim() { size_t a = s.find_first_not_of(" \t\r\n"); if (a == std::string::npos) { s.clear(); return; } s = s.substr(a, s.find_last_not_of(" \t\r\n") - a + 1); }
  bool equals(const String& o) const { return s == o.s; }
  char operator[](unsigned int i) const { return i < s.size() ? s[i] : 0; }
  String& operator+=(const String& o) { s += o.s; return *this; }
  String& operator+=(const char* o) { s += o; return *this; }
  String& operator+=(char c) { s += c; return *this; }
  bool concat(const String& o) { s += o.s; return true; }
  bool operator==(const String& o) const { return s == o.s; }
  bool operator==(const char* o) const { return s == o; }
  bool operator!=(const String& o) const { return s != o.s; }
  bool operator!=(const char* o) const { return s != o; }
  friend String operator+(const String& a, const String& b) { return String(a.s + b.s); }
  friend String operator+(const char* a, const String& b) { return String(std::string(a) + b.s); }
  friend String operator+(const String& a, const char* b) { return String(a.s + b); }
  friend String operator+(const String& a, char b) { return String(a.s + b); }
};

// ---- Serial ----
class HardwareSerial {
  unsigned long baud = 9600;
  double tx_free_at = 0;  // when the (modelled) TX buffer drains
  void out(const char* p, size_t n);
 public:
  void begin(unsigned long b, uint32_t cfg = 0) { (void)cfg; baud = b; }
  int available();
  int read();
  int peek();
  size_t write(uint8_t c) { out((const char*)&c, 1); return 1; }
  size_t write(const uint8_t* p, size_t n) { out((const char*)p, n); return n; }
  float parseFloat();
  long parseInt();
  size_t print(const char* s) { size_t n = strlen(s); out(s, n); return n; }
  size_t print(const String& s) { out(s.c_str(), s.length()); return s.length(); }
  size_t print(char c) { out(&c, 1); return 1; }
  size_t print(unsigned char v, int base = DEC) { return print((unsigned long)v, base); }
  size_t print(int v, int base = DEC) { return print((long)v, base); }
  size_t print(unsigned int v, int base = DEC) { return print((unsigned long)v, base); }
  size_t print(long v, int base = DEC) { char b[40]; int n = base == 16 ? snprintf(b, sizeof b, "%lx", v) : snprintf(b, sizeof b, "%ld", v); out(b, n); return n; }
  size_t print(unsigned long v, int base = DEC) { char b[40]; int n = base == 16 ? snprintf(b, sizeof b, "%lx", v) : snprintf(b, sizeof b, "%lu", v); out(b, n); return n; }
  size_t print(double v, int digits = 2) { char b[64]; int n = snprintf(b, sizeof b, "%.*f", digits, v); out(b, n); return n; }
  size_t println() { out("\r\n", 2); return 2; }
  size_t printf(const char* fmt, ...) __attribute__((format(printf, 2, 3)));
  template <class T> size_t println(T v) { size_t n = print(v); return n + println(); }
  template <class T> size_t println(T v, int base) { size_t n = print(v, base); return n + println(); }
  void flush() {}
};
extern HardwareSerial Serial;

// Stream base for SoftwareSerial (and libraries taking a Stream*, e.g. GyverBus). HardwareSerial is not a Stream here.
class Stream {
 public:
  virtual int available() = 0;
  virtual int read() = 0;
  virtual int peek() = 0;
  virtual size_t write(uint8_t c) = 0;
  virtual size_t write(const uint8_t* p, size_t n) { for (size_t i = 0; i < n; i++) write(p[i]); return n; }
  virtual ~Stream() {}
};

#ifndef INPUT_PULLDOWN
#define INPUT_PULLDOWN 0   // reads 0 when undriven, like INPUT
#endif
#define log_e(...) ((void)0)
#define log_w(...) ((void)0)
#define log_i(...) ((void)0)
#define log_d(...) ((void)0)
#define log_v(...) ((void)0)
