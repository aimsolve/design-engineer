// GyverTimers (AVR hardware timers) emulated as a pin-toggle clock in virtual time: setFrequency(f) + outputEnable(CHANNEL_A, TOGGLE_PIN)
// makes OC2A (D11) toggle at f Hz -> JS gets "clock on/off" events instead of millions of edges.
#pragma once
#include "vc_core.h"
#define CHANNEL_A 0
#define CHANNEL_B 1
#define TOGGLE_PIN 1
#define CLEAR_PIN 2
#define SET_PIN 3
#define DISCONNECT_PIN 0
struct VcTimer {
  int pinA, pinB; double hz = 0;
  VcTimer(int a, int b) : pinA(a), pinB(b) {}
  uint32_t setFrequency(float f) { hz = f; return (uint32_t)f; }
  uint32_t setFrequencyFloat(float f) { hz = f; return 0; }
  void setPeriod(uint32_t us) { hz = 1e6 / us; }
  void outputEnable(uint8_t ch, uint8_t mode) { (void)mode; vc_js_pin_clock(ch == CHANNEL_A ? pinA : pinB, hz, 1, vc_now()); vc_activity(); }
  void outputDisable(uint8_t ch) { vc_js_pin_clock(ch == CHANNEL_A ? pinA : pinB, hz, 0, vc_now()); vc_activity(); }
  void enableISR(uint8_t = 0) {}
  void disableISR(uint8_t = 0) {}
  void stop() {}
  void restart() {}
  void resume() {}
  void pause() {}
};
inline VcTimer Timer2(11, 3);  // Nano/Uno: OC2A = D11, OC2B = D3
inline VcTimer Timer1(9, 10);
