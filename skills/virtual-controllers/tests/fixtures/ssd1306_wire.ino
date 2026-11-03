// Test fixture: an SSD1306 128x64 at 0x3C driven by raw commands over Arduino Wire.h, plus Wire.requestFrom.
// Plain Arduino-ESP32: compiles unchanged for a real ESP32 (PlatformIO, framework = arduino).
// Wiring (tests/fixtures/ssd1306.device.json): SDA GPIO21, SCL GPIO22 (Wire defaults), nothing else on the bus.
// Draws: page 0, columns 0..63 all set (rows 0..7 of the left half lit); everything else clear.
// Serial:
//   READY
//   RQ 3C <returned> <available>   Wire.requestFrom(0x3C, 1): the display answers (1 byte)
//   RQ 50 <returned> <available>   Wire.requestFrom(0x50, 2): nobody at 0x50 -> 0 bytes
//   NACK 50 <endTransmission>      a write to 0x50: 2 = address NACK
//   DRAWN
#include <Arduino.h>
#include <Wire.h>

const uint8_t OLED = 0x3C;

void cmds(const uint8_t* c, int n) {
  Wire.beginTransmission(OLED);
  Wire.write((uint8_t)0x00);
  for (int i = 0; i < n; i++) Wire.write(c[i]);
  Wire.endTransmission();
}
void data(const uint8_t* d, int n) {   // in chunks that fit every Wire buffer
  for (int i = 0; i < n; i += 16) {
    Wire.beginTransmission(OLED);
    Wire.write((uint8_t)0x40);
    for (int k = i; k < n && k < i + 16; k++) Wire.write(d[k]);
    Wire.endTransmission();
  }
}

uint8_t frame[128 * 8];

void setup() {
  Serial.begin(115200);
  Wire.begin();
  Serial.println("READY");
  const uint8_t seq[] = {0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40, 0x8D, 0x14, 0x20, 0x00, 0xA0, 0xC0, 0xDA, 0x12,
                          0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0xAF};
  cmds(seq, sizeof(seq));
  const uint8_t win[] = {0x21, 0, 127, 0x22, 0, 7};
  cmds(win, sizeof(win));
  for (int i = 0; i < 128 * 8; i++) frame[i] = (i < 64) ? 0xFF : 0x00;
  data(frame, sizeof(frame));

  int got = Wire.requestFrom((uint8_t)0x3C, (uint8_t)1);
  int av = Wire.available();
  while (Wire.available()) Wire.read();
  Serial.print("RQ 3C "); Serial.print(got); Serial.print(" "); Serial.println(av);
  got = Wire.requestFrom((uint8_t)0x50, (uint8_t)2);
  av = Wire.available();
  Serial.print("RQ 50 "); Serial.print(got); Serial.print(" "); Serial.println(av);
  Wire.beginTransmission((uint8_t)0x50);
  Wire.write((uint8_t)0x00);
  Serial.print("NACK 50 "); Serial.println(Wire.endTransmission());
  Serial.println("DRAWN");
}

void loop() { delay(10); }
