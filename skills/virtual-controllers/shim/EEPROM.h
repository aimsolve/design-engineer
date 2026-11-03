// ESP32-style emulated EEPROM in RAM, erased flash = 0xFF (so sketches see "never written" defaults).
#pragma once
#include "vc_core.h"
class EEPROMClass {
  uint8_t d[4096];
  size_t n = 0;
 public:
  EEPROMClass() { memset(d, 0xFF, sizeof d); }
  bool begin(size_t size) { n = size > 4096 ? 4096 : size; return true; }
  uint8_t read(int a) { return a >= 0 && a < (int)n ? d[a] : 0; }
  void write(int a, uint8_t v) { if (a >= 0 && a < (int)n) d[a] = v; }
  template <class T> T& get(int a, T& t) { if (a >= 0 && a + (int)sizeof(T) <= (int)n) memcpy(&t, d + a, sizeof(T)); return t; }
  template <class T> const T& put(int a, const T& t) { if (a >= 0 && a + (int)sizeof(T) <= (int)n) memcpy(d + a, &t, sizeof(T)); return t; }
  bool commit() { vc_advance(100); return true; }
  size_t length() { return n; }
};
inline EEPROMClass EEPROM;
