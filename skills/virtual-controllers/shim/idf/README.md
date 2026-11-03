Small stand-ins for the ESP-IDF headers that the vendored `lib/esp_lcd` and an IDF-style sketch include (C and C++). Only what they use:
types, constants and macros here; the functions (gpio, SPI bus, vTaskDelay, heap_caps) are implemented in `shim/idf_vc.cpp` over the
virtual-controllers core. Add a missing IDF call here, never edit the firmware or the vendored files.
