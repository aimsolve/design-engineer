// Arduino SPI on the virtual board: bytes go out on the host's pins (SPI.begin(sck, miso, mosi, ss), ESP32 defaults per bus) to
// whatever chip is wired to those pins with its CS low (runtime/device.mjs). A transfer costs bytes x 8 / clock of virtual time,
// so the frame rate of a display is the one the real bus allows. MISO reads 0xFF (no chip answers reads yet).
#pragma once
#include "vc_core.h"
#define SPI_MODE0 0
#define SPI_MODE1 1
#define SPI_MODE2 2
#define SPI_MODE3 3
#define FSPI 1
#define HSPI 2
#define VSPI 3
#define SPI_CLOCK_DIV2 2
#define SPI_CLOCK_DIV4 4
#define SPI_CLOCK_DIV8 8
#define SPI_CLOCK_DIV16 16
class SPISettings {
 public:
  uint32_t clock; uint8_t bitOrder, dataMode;
  SPISettings(uint32_t c = 1000000, uint8_t o = MSBFIRST, uint8_t m = SPI_MODE0) : clock(c), bitOrder(o), dataMode(m) {}
};
class SPIClass {
  int bus;
  uint32_t hz = 1000000;
  void xfer(const void* p, uint32_t n) {
    if (!n) return;
    vc_js_spi(bus - 1, (const uint8_t*)p, (int)n, vc_now());
    vc_activity();
    vc_advance(n * 8.0e6 / (double)hz + VC_COST_MISC);
  }
 public:
  explicit SPIClass(int b = VSPI) : bus(b) {}
  void begin(int8_t sck = -1, int8_t miso = -1, int8_t mosi = -1, int8_t ss = -1) {
    (void)ss;
#ifdef VC_BOARD_AVR
    if (sck < 0) sck = 13;                            // ATmega328P: D13 / D12 / D11
    if (miso < 0) miso = 12;
    if (mosi < 0) mosi = 11;
#else
    const bool h = bus == HSPI;                       // ESP32 defaults: HSPI 14/12/13, VSPI (and FSPI here) 18/19/23
    if (sck < 0) sck = h ? 14 : 18;
    if (miso < 0) miso = h ? 12 : 19;
    if (mosi < 0) mosi = h ? 13 : 23;
#endif
    vc_js_spi_begin(bus - 1, sck, mosi, miso);
  }
  void end() { vc_js_spi_begin(bus - 1, -1, -1, -1); }
  void beginTransaction(SPISettings s) { hz = s.clock ? s.clock : hz; }
  void endTransaction() {}
  void setFrequency(uint32_t f) { if (f) hz = f; }
  void setClockDivider(uint32_t d) { if (d) hz = 80000000 / d; }
  void setDataMode(uint8_t) {}
  void setBitOrder(uint8_t) {}
  void setHwCs(bool) {}
  uint32_t getClockDivider() { return 80000000 / hz; }
  uint8_t transfer(uint8_t b) { xfer(&b, 1); return 0xFF; }
  uint16_t transfer16(uint16_t w) { uint8_t b[2] = {(uint8_t)(w >> 8), (uint8_t)w}; xfer(b, 2); return 0xFFFF; }
  uint32_t transfer32(uint32_t w) { uint8_t b[4] = {(uint8_t)(w >> 24), (uint8_t)(w >> 16), (uint8_t)(w >> 8), (uint8_t)w}; xfer(b, 4); return 0xFFFFFFFF; }
  void transfer(void* buf, uint32_t n) { xfer(buf, n); memset(buf, 0xFF, n); }
  void transferBytes(const uint8_t* d, uint8_t* out, uint32_t n) { xfer(d, n); if (out) memset(out, 0xFF, n); }
  void write(uint8_t b) { xfer(&b, 1); }
  void write16(uint16_t w) { transfer16(w); }
  void write32(uint32_t w) { transfer32(w); }
  void writeBytes(const uint8_t* d, uint32_t n) { xfer(d, n); }
  void writePixels(const void* d, uint32_t n) {        // ESP32: 16-bit pixels in memory order, sent high byte first
    const uint8_t* s = (const uint8_t*)d; uint8_t buf[256];
    for (uint32_t i = 0; i < n; i += sizeof buf) { uint32_t k = n - i < sizeof buf ? n - i : sizeof buf; for (uint32_t j = 0; j < k; j += 2) { if (j + 1 < k) { buf[j] = s[i + j + 1]; buf[j + 1] = s[i + j]; } else buf[j] = s[i + j]; } xfer(buf, k); }
  }
  void writePattern(const uint8_t* d, uint8_t size, uint32_t repeat) { for (uint32_t i = 0; i < repeat; i++) xfer(d, size); }
};
inline SPIClass SPI(VSPI);
