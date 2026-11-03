// Servo stub: forwards to the JS hook "servo" (a=pin or angle). Not used by the current firmware (no attach()).
#pragma once
#include "vc_core.h"
class Servo {
  int pin = -1;
 public:
  int attach(int p, int minUs = 500, int maxUs = 2500) { (void)minUs; (void)maxUs; pin = p; vc_js_hook("servo.attach", p, 0, vc_now()); return 1; }
  void detach() {}
  void setPeriodHertz(int) {}
  void write(int angle) { vc_js_hook("servo.write", pin, angle, vc_now()); }
  void writeMicroseconds(int us) { vc_js_hook("servo.us", pin, us, vc_now()); }
};
