// Hello/blink for Espressif QEMU. Prints ticks and runs a moving average over synthetic "ADC" samples
// (pseudo-random noise + a step), so the filter logic can be checked from the UART log alone.
#include <Arduino.h>

static uint32_t lcg = 12345;
static int32_t noise() {  // approx. Gaussian: sum of 12 uniforms (sigma 256) scaled to sigma ~ 100 counts
  int32_t s = 0;
  for (int i = 0; i < 12; i++) { lcg = lcg * 1664525u + 1013904223u; s += (lcg >> 16) & 0xFF; }
  return (s - 12 * 128) * 100 / 256;
}

void setup() {
  Serial.begin(115200);
  pinMode(2, OUTPUT);
  Serial.println("BOOT esp32 qemu example");
}

void loop() {
  static int n = 0;
  static int32_t win[8] = {0};
  int32_t raw = 100000 + (n >= 20 ? 2000 : 0) + noise();  // step of 2000 counts at sample 20 ("motor on")
  win[n % 8] = raw;
  int64_t sum = 0;
  for (int i = 0; i < 8; i++) sum += win[i];
  digitalWrite(2, n & 1);
  Serial.printf("tick %d raw %ld avg8 %ld led %d\n", n, (long)raw, (long)(n >= 7 ? sum / 8 : raw), n & 1);
  if (++n == 40) { Serial.println("DONE"); for (;;) delay(1000); }
  delay(20);
}
