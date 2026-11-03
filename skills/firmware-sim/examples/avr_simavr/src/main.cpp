// Arduino Uno blink + UART log for simavr. Same moving-average check as the other examples.
#include <Arduino.h>

static uint32_t lcg = 12345;
static long noise() {
  long s = 0;
  for (int i = 0; i < 12; i++) { lcg = lcg * 1664525UL + 1013904223UL; s += (lcg >> 16) & 0xFF; }
  return (s - 12L * 128) * 100 / 256;
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.println(F("BOOT avr simavr example"));
}

void loop() {
  static int n = 0;
  static long win[8] = {0};
  long raw = 100000L + (n >= 20 ? 2000 : 0) + noise();
  win[n % 8] = raw;
  long long sum = 0;
  for (int i = 0; i < 8; i++) sum += win[i];
  digitalWrite(LED_BUILTIN, n & 1);
  Serial.print(F("tick ")); Serial.print(n); Serial.print(F(" raw ")); Serial.print(raw);
  Serial.print(F(" avg8 ")); Serial.print(n >= 7 ? (long)(sum / 8) : raw); Serial.print(F(" led ")); Serial.println(n & 1);
  if (++n == 40) { Serial.println(F("DONE")); Serial.flush(); for (;;) {} }
  delay(20);
}
