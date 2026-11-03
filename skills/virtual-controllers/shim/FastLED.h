// FastLED for the simulator: CRGB + addLeds/show/brightness. show() reports the strip to JS:
// hook "led:<pin>:<brightness>:RRGGBB,RRGGBB,..." (colours before brightness scaling, as the sketch set them; a = count, b = brightness).
// One strip per sketch (the last addLeds wins).
#pragma once
#include "vc_ext.h"
enum EOrder { RGB = 0012, RBG = 0021, GRB = 0102, GBR = 0120, BRG = 0201, BGR = 0210 };
struct CRGB {
  uint8_t r, g, b;
  enum HTMLColorCode : uint32_t { Black = 0x000000, White = 0xFFFFFF, Red = 0xFF0000, Green = 0x008000, Blue = 0x0000FF, Yellow = 0xFFFF00, Orange = 0xFFA500, Lime = 0x00FF00 };
  CRGB() : r(0), g(0), b(0) {}
  CRGB(uint8_t R, uint8_t G, uint8_t B) : r(R), g(G), b(B) {}
  CRGB(uint32_t c) : r((c >> 16) & 255), g((c >> 8) & 255), b(c & 255) {}
  CRGB(HTMLColorCode c) : CRGB((uint32_t)c) {}
};
template <uint8_t DATA_PIN, EOrder ORDER = GRB> class WS2812 {};
template <uint8_t DATA_PIN, EOrder ORDER = GRB> class WS2812B {};
template <uint8_t DATA_PIN, EOrder ORDER = GRB> class NEOPIXEL {};   // real FastLED: one parameter; addLeds<NEOPIXEL, PIN> works the same
inline void fill_solid(CRGB* l, int n, const CRGB& c) { for (int i = 0; i < n; i++) l[i] = c; }
class CFastLED {
  CRGB* leds = nullptr;
  int n = 0, pin = 0;
  uint8_t bright = 255;
 public:
  template <template <uint8_t, EOrder> class CHIP, uint8_t PIN, EOrder ORD = GRB> CFastLED& addLeds(CRGB* d, int count, int = 0) { leds = d; n = count; pin = PIN; return *this; }
  void setBrightness(uint8_t b) { bright = b; }
  uint8_t getBrightness() { return bright; }
  void clearData() { if (leds) for (int i = 0; i < n; i++) leds[i] = CRGB(); }
  void clear(bool write = false) { clearData(); if (write) show(); }
  void show() {
    std::string s = "led:" + std::to_string(pin) + ":" + std::to_string(bright) + ":";
    char b[8];
    for (int i = 0; i < n; i++) { snprintf(b, sizeof b, "%02X%02X%02X", leds[i].r, leds[i].g, leds[i].b); s += b; if (i + 1 < n) s += ","; }
    vc_js_hook(s.c_str(), n, bright, vc_now());
    vc_advance(30.0 * n + 50);   // WS2812: 30 µs per LED + reset
  }
};
inline CFastLED FastLED;
