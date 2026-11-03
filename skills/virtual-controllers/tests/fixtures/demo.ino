// Test fixture for the model page: an ST7789 240x320 over Arduino SPI, one button, one encoder with a push.
// Plain Arduino-ESP32: compiles unchanged for a real ESP32 (PlatformIO, framework = arduino).
// Wiring (tests/fixtures/demo.device.json): TFT SCL 18, SDA 23, CS 5, DC 16, RST 17, BLK 4; btn_play GPIO27-GND; enc A 32, B 33,
// C GND, SW 25, SW2 GND.
// Screen: BLUE everywhere, a RED 40x40 square at (100..139, 140..179); every press of btn_play repaints a 30x30 square at
// (10..39, 10..39) in the next colour (YELLOW, WHITE, MAGENTA, ...).
// Serial: READY, DRAWN, PRESS 27 <ms> (debounced press length), ENC <clicks>, PUSH (encoder push released).
#include <Arduino.h>
#include <SPI.h>

const int TFT_CS = 5, TFT_DC = 16, TFT_RST = 17, TFT_BLK = 4;
const int W = 240, H = 320;
uint8_t line[W * 2];

void cmd(uint8_t c) { digitalWrite(TFT_DC, LOW); digitalWrite(TFT_CS, LOW); SPI.transfer(c); digitalWrite(TFT_CS, HIGH); }
void dat(const uint8_t* d, uint32_t n) { digitalWrite(TFT_DC, HIGH); digitalWrite(TFT_CS, LOW); SPI.writeBytes(d, n); digitalWrite(TFT_CS, HIGH); }
void cmd1(uint8_t c, uint8_t a) { cmd(c); dat(&a, 1); }
void fillRect(int x0, int y0, int w, int h, uint16_t c) {
  uint8_t ca[4] = {(uint8_t)(x0 >> 8), (uint8_t)x0, (uint8_t)((x0 + w - 1) >> 8), (uint8_t)(x0 + w - 1)};
  uint8_t ra[4] = {(uint8_t)(y0 >> 8), (uint8_t)y0, (uint8_t)((y0 + h - 1) >> 8), (uint8_t)(y0 + h - 1)};
  cmd(0x2A); dat(ca, 4); cmd(0x2B); dat(ra, 4);
  for (int i = 0; i < w; i++) { line[2 * i] = c >> 8; line[2 * i + 1] = c & 0xFF; }
  cmd(0x2C);
  digitalWrite(TFT_DC, HIGH); digitalWrite(TFT_CS, LOW);
  for (int y = 0; y < h; y++) SPI.writeBytes(line, w * 2);
  digitalWrite(TFT_CS, HIGH);
}

volatile unsigned long downAt = 0, upAt = 0;
volatile long pressMs = -1;
void IRAM_ATTR isrPlay() {
  unsigned long now = millis();
  int v = digitalRead(27);
  if (v == 0 && downAt == 0 && (upAt == 0 || now - upAt > 20)) downAt = now;
  else if (v == 1 && downAt != 0 && now - downAt > 20) { pressMs = (long)(now - downAt); downAt = 0; upAt = now; }
}

volatile unsigned long swDown = 0, swUp = 0;
volatile int pushes = 0;
void IRAM_ATTR isrPush() {
  unsigned long now = millis();
  int v = digitalRead(25);
  if (v == 0 && swDown == 0 && (swUp == 0 || now - swUp > 20)) swDown = now;
  else if (v == 1 && swDown != 0 && now - swDown > 20) { pushes++; swDown = 0; swUp = now; }
}

const int8_t QDEC[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};
volatile long qpos = 0;
volatile int qstate = 3;
volatile unsigned long encLast = 0;
long encReported = 0;
void IRAM_ATTR isrEnc() { int s = (digitalRead(32) << 1) | digitalRead(33); qpos += QDEC[qstate * 4 + s]; qstate = s; encLast = millis(); }

const uint16_t COLORS[4] = {0xFFE0, 0xFFFF, 0xF81F, 0x07FF};
int presses = 0, pushesShown = 0;

void setup() {
  Serial.begin(115200);
  pinMode(27, INPUT_PULLUP); pinMode(25, INPUT_PULLUP); pinMode(32, INPUT_PULLUP); pinMode(33, INPUT_PULLUP);
  qstate = (digitalRead(32) << 1) | digitalRead(33);
  attachInterrupt(digitalPinToInterrupt(27), isrPlay, CHANGE);
  attachInterrupt(digitalPinToInterrupt(25), isrPush, CHANGE);
  attachInterrupt(digitalPinToInterrupt(32), isrEnc, CHANGE);
  attachInterrupt(digitalPinToInterrupt(33), isrEnc, CHANGE);
  pinMode(TFT_CS, OUTPUT); digitalWrite(TFT_CS, HIGH);
  pinMode(TFT_DC, OUTPUT); pinMode(TFT_RST, OUTPUT); pinMode(TFT_BLK, OUTPUT);
  SPI.begin();
  SPI.beginTransaction(SPISettings(40000000, MSBFIRST, SPI_MODE0));
  Serial.println("READY");
  digitalWrite(TFT_RST, LOW); delay(20); digitalWrite(TFT_RST, HIGH); delay(150);
  cmd(0x01); delay(150);
  cmd(0x11); delay(120);
  cmd1(0x3A, 0x55); cmd1(0x36, 0x00); cmd(0x20);
  fillRect(0, 0, W, H, 0x001F);
  fillRect(100, 140, 40, 40, 0xF800);
  cmd(0x29);
  digitalWrite(TFT_BLK, HIGH);
  Serial.println("DRAWN");
}

void loop() {
  unsigned long now = millis();
  if (pressMs >= 0) {
    Serial.print("PRESS 27 "); Serial.println(pressMs);
    pressMs = -1;
    fillRect(10, 10, 30, 30, COLORS[presses++ % 4]);
  }
  if (pushes != pushesShown) { pushesShown = pushes; Serial.println("PUSH"); }
  if (encLast && now - encLast >= 30 && qpos / 4 != encReported) {
    encReported = qpos / 4;
    Serial.print("ENC "); Serial.println(encReported);
  }
  delay(2);
}
