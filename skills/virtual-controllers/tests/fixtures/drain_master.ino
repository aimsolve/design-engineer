// Review fix fixture: an I2C master that sends one byte to the drawing board at 0x22 every 20 ms, ten times.
#include <Arduino.h>
#include <Wire.h>
int sent = 0;
void setup() { Serial.begin(115200); Wire.begin(); delay(300); Serial.println("MASTER READY"); }
void loop() {
  if (sent < 10) { Wire.beginTransmission(0x22); Wire.write((uint8_t)sent); Wire.endTransmission(); sent++; }
  delay(20);
}
