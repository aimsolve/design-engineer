// Mail type for the UART bus; used by SoftwareSerial.h, WiFi.h, FastLED.h.

#pragma once
#include "vc_core.h"

// mail types used here (vc_core uses 1..4): 5 = UART bytes for this board's SoftwareSerial
#define VC_MAIL_UART 5
