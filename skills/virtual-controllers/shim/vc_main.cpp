// Entry point: the Arduino "main" - setup() once, loop() forever. Runs inside ASYNCIFY, suspended by vc_js_yield.
// Every loop() pass is charged a floor of CPU time, otherwise an idle loop (no delay, no pin call) would never advance time.
#include "vc_core.h"
void setup();
void loop();
int main() {
  vc_start();
  setup();
  for (;;) {
    loop();
    vc_advance(VC_COST_LOOP);
  }
}
