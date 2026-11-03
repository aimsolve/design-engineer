// Test fixture: counts the level changes of four contacts and decodes a quadrature encoder.
// Plain Arduino-ESP32: compiles unchanged for a real ESP32 (PlatformIO, framework = arduino).
// Wiring (tests/fixtures/buttons.device.json): btn_play GPIO27-GND, btn_hi GPIO26-3V3, btn_clean GPIO14-GND (bounce_ms 0),
// enc A GPIO32 / B GPIO33 / C GND, push SW GPIO25 / SW2 GND.
// Serial (what the tests read):
//   READY
//   EDGES <pin> <n> LEVEL <level>   a contact quiet for 30 ms: n level changes since its last report, its level now
//   PRESS 27 <ms>                   btn_play: first falling edge to the first rising edge > 20 ms later (debounced)
//   ENC <clicks>                    encoder position / 4, after 30 ms without a transition, when it changed
#include <Arduino.h>

const int NP = 4;
const int PINS[NP] = {27, 26, 14, 25};
volatile int nEdges[NP] = {0, 0, 0, 0};
volatile unsigned long lastEdge[NP] = {0, 0, 0, 0};

volatile unsigned long downAt = 0, upAt = 0;
volatile long pressMs = -1;

const int ENC_A = 32, ENC_B = 33;
const int8_t QDEC[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};   // state = A<<1|B; CW: 11->01->00->10->11
volatile long qpos = 0;
volatile int qstate = 3;
volatile unsigned long encLast = 0;
long encReported = 0;

void countEdge(int i) {
  unsigned long now = millis();
  nEdges[i]++;
  lastEdge[i] = now;
  if (i != 0) return;
  int v = digitalRead(27);
  if (v == 0 && downAt == 0 && (upAt == 0 || now - upAt > 20)) downAt = now;           // a press starts
  else if (v == 1 && downAt != 0 && now - downAt > 20) {                               // released (press bounce is over)
    pressMs = (long)(now - downAt); downAt = 0; upAt = now;
  }
}
void IRAM_ATTR isr27() { countEdge(0); }
void IRAM_ATTR isr26() { countEdge(1); }
void IRAM_ATTR isr14() { countEdge(2); }
void IRAM_ATTR isr25() { countEdge(3); }
void IRAM_ATTR isrEnc() {
  int s = (digitalRead(ENC_A) << 1) | digitalRead(ENC_B);
  qpos += QDEC[qstate * 4 + s];
  qstate = s;
  encLast = millis();
}

void setup() {
  Serial.begin(115200);
  pinMode(27, INPUT_PULLUP);
  pinMode(26, INPUT_PULLDOWN);
  pinMode(14, INPUT_PULLUP);
  pinMode(25, INPUT_PULLUP);
  pinMode(ENC_A, INPUT_PULLUP);
  pinMode(ENC_B, INPUT_PULLUP);
  qstate = (digitalRead(ENC_A) << 1) | digitalRead(ENC_B);
  attachInterrupt(digitalPinToInterrupt(27), isr27, CHANGE);
  attachInterrupt(digitalPinToInterrupt(26), isr26, CHANGE);
  attachInterrupt(digitalPinToInterrupt(14), isr14, CHANGE);
  attachInterrupt(digitalPinToInterrupt(25), isr25, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_A), isrEnc, CHANGE);
  attachInterrupt(digitalPinToInterrupt(ENC_B), isrEnc, CHANGE);
  Serial.println("READY");
}

void loop() {
  unsigned long now = millis();
  for (int i = 0; i < NP; i++) {
    if (nEdges[i] && now - lastEdge[i] >= 30) {
      int n = nEdges[i];
      nEdges[i] = 0;
      Serial.print("EDGES "); Serial.print(PINS[i]); Serial.print(" "); Serial.print(n);
      Serial.print(" LEVEL "); Serial.println(digitalRead(PINS[i]));
    }
  }
  if (pressMs >= 0) {
    Serial.print("PRESS 27 "); Serial.println(pressMs);
    pressMs = -1;
  }
  if (encLast && now - encLast >= 30 && qpos / 4 != encReported) {
    encReported = qpos / 4;
    Serial.print("ENC "); Serial.println(encReported);
  }
  delay(1);
}
