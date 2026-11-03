// Test fixture: an ST7789 240x320 driven by raw MIPI-DCS commands over Arduino SPI.h.
// Plain Arduino-ESP32: compiles unchanged for a real ESP32 (PlatformIO, framework = arduino).
// Wiring (tests/fixtures/st7789.device.json): SCL GPIO18, SDA GPIO23 (VSPI defaults of SPI.begin()), CS GPIO5, DC GPIO16,
// RST GPIO17, BLK GPIO4.
// What it does, in machine time:
//   reset, init (COLMOD 565), fill the whole screen BLUE at 40 MHz (timed), again BLUE at 10 MHz (timed), a RED 20x10
//   rectangle at x 100..119, y 200..209, DISPON, backlight on; then a GREEN full-screen fill sent while CS is HIGH (must not
//   reach the display); "DRAWN"; 1000 ms later the backlight goes off: "BLK_OFF".
// Serial: READY, FULL_US <us at 40 MHz>, FULL10_US <us at 10 MHz>, DRAWN, BLK_OFF
#include <Arduino.h>
#include <SPI.h>

const int TFT_CS = 5, TFT_DC = 16, TFT_RST = 17, TFT_BLK = 4;
const int W = 240, H = 320;
uint8_t line[W * 2];

void cmd(uint8_t c) {
  digitalWrite(TFT_DC, LOW); digitalWrite(TFT_CS, LOW);
  SPI.transfer(c);
  digitalWrite(TFT_CS, HIGH);
}
void dat(const uint8_t* d, uint32_t n) {
  digitalWrite(TFT_DC, HIGH); digitalWrite(TFT_CS, LOW);
  SPI.writeBytes(d, n);
  digitalWrite(TFT_CS, HIGH);
}
void cmd1(uint8_t c, uint8_t a) { cmd(c); dat(&a, 1); }
void window(int x0, int y0, int x1, int y1) {
  uint8_t ca[4] = {(uint8_t)(x0 >> 8), (uint8_t)x0, (uint8_t)(x1 >> 8), (uint8_t)x1};
  uint8_t ra[4] = {(uint8_t)(y0 >> 8), (uint8_t)y0, (uint8_t)(y1 >> 8), (uint8_t)y1};
  cmd(0x2A); dat(ca, 4);
  cmd(0x2B); dat(ra, 4);
}
void setColor(uint16_t c, int n) { for (int i = 0; i < n; i++) { line[2 * i] = c >> 8; line[2 * i + 1] = c & 0xFF; } }
void fillRect(int x0, int y0, int w, int h, uint16_t c) {
  window(x0, y0, x0 + w - 1, y0 + h - 1);
  setColor(c, w);
  cmd(0x2C);
  digitalWrite(TFT_DC, HIGH); digitalWrite(TFT_CS, LOW);
  for (int y = 0; y < h; y++) SPI.writeBytes(line, w * 2);
  digitalWrite(TFT_CS, HIGH);
}

void setup() {
  Serial.begin(115200);
  pinMode(TFT_CS, OUTPUT); digitalWrite(TFT_CS, HIGH);
  pinMode(TFT_DC, OUTPUT); digitalWrite(TFT_DC, HIGH);
  pinMode(TFT_RST, OUTPUT);
  pinMode(TFT_BLK, OUTPUT); digitalWrite(TFT_BLK, LOW);
  SPI.begin();                                           // ESP32 defaults: SCK 18, MISO 19, MOSI 23, SS 5
  SPI.beginTransaction(SPISettings(40000000, MSBFIRST, SPI_MODE0));
  Serial.println("READY");

  digitalWrite(TFT_RST, HIGH); delay(5);
  digitalWrite(TFT_RST, LOW); delay(20);
  digitalWrite(TFT_RST, HIGH); delay(150);
  cmd(0x01); delay(150);                                 // SWRESET
  cmd(0x11); delay(120);                                 // SLPOUT
  cmd1(0x3A, 0x55);                                      // COLMOD 16 bit
  cmd1(0x36, 0x00);                                      // MADCTL
  cmd(0x20);                                             // INVOFF

  unsigned long t0 = micros();
  fillRect(0, 0, W, H, 0x001F);
  unsigned long t1 = micros();
  Serial.print("FULL_US "); Serial.println(t1 - t0);

  SPI.setFrequency(10000000);
  t0 = micros();
  fillRect(0, 0, W, H, 0x001F);
  t1 = micros();
  Serial.print("FULL10_US "); Serial.println(t1 - t0);
  SPI.setFrequency(40000000);

  fillRect(100, 200, 20, 10, 0xF800);
  cmd(0x29);                                             // DISPON
  digitalWrite(TFT_BLK, HIGH);

  // the same bytes with CS held HIGH: a display on this bus must ignore them
  window(0, 0, W - 1, H - 1);
  digitalWrite(TFT_CS, HIGH);
  digitalWrite(TFT_DC, LOW); SPI.transfer(0x2C);
  digitalWrite(TFT_DC, HIGH);
  setColor(0x07E0, W);
  for (int y = 0; y < H; y++) SPI.writeBytes(line, W * 2);
  // a display whose CS is not wired takes them: it shows GREEN from here on

  Serial.println("DRAWN");
  delay(1000);
  digitalWrite(TFT_BLK, LOW);
  Serial.println("BLK_OFF");
}

void loop() { delay(10); }
