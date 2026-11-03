// Test fixture: an ST7789 240x320 through ESP-IDF esp_lcd over SPI (what LVGL ports use), from an Arduino sketch.
// Compiles unchanged for a real ESP32 with Arduino-ESP32 3.x (ESP-IDF 5.x headers; PlatformIO).
// Wiring (tests/fixtures/esp_lcd.device.json): SCLK GPIO18, MOSI GPIO23, CS GPIO5, DC GPIO16, RST GPIO17, BLK GPIO4.
// What it does:
//   panel init, two full frames (RED, then GREEN) drawn as 8 bands of 240x40 with esp_lcd_panel_draw_bitmap at 40 MHz, each band
//   waited for through on_color_trans_done (like an LVGL flush). Then a 4x4 WHITE square at (100..103, 100..103) through the raw
//   panel IO (tx_param CASET/RASET + tx_color RAMWR), then mirror(x) and a 10x10 BLUE square drawn at (0..9, 0..9), which the
//   mirror puts at x 230..239.
// Serial:
//   READY
//   CALL_US <us>        how long one draw_bitmap call of 19200 bytes took to RETURN (asynchronous like DMA: ~0)
//   DONE_US <us>        from that call to its on_color_trans_done (the transfer time: >= 19200*8/40 MHz = 3840 us)
//   FRAME_US <k> <us>   full frame k (1, 2): 8 bands, waited for
//   CB <n>              on_color_trans_done calls in total
//   DRAWN
#include <Arduino.h>
#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_panel_ops.h"

#define PIN_SCLK 18
#define PIN_MOSI 23
#define PIN_CS 5
#define PIN_DC 16
#define PIN_RST 17
#define PIN_BLK 4
#define BAND 40

static volatile int cbCount = 0;
static volatile unsigned long cbAt = 0;
static uint8_t buf[240 * BAND * 2];
static esp_lcd_panel_handle_t panel = NULL;
static esp_lcd_panel_io_handle_t io = NULL;

static bool onDone(esp_lcd_panel_io_handle_t h, esp_lcd_panel_io_event_data_t* e, void* ctx) {
  cbCount = cbCount + 1;
  cbAt = micros();
  return false;
}

static void fillBuf(uint8_t hi, uint8_t lo, int n) { for (int i = 0; i < n; i++) { buf[2 * i] = hi; buf[2 * i + 1] = lo; } }

static void waitCb(int target) { while (cbCount < target) delayMicroseconds(100); }

static unsigned long frame(uint8_t hi, uint8_t lo) {
  fillBuf(hi, lo, 240 * BAND);
  unsigned long t0 = micros();
  for (int b = 0; b < 320 / BAND; b++) {
    int target = cbCount + 1;
    esp_lcd_panel_draw_bitmap(panel, 0, b * BAND, 240, (b + 1) * BAND, buf);
    waitCb(target);
  }
  return micros() - t0;
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BLK, OUTPUT);
  digitalWrite(PIN_BLK, HIGH);

  spi_bus_config_t bus = {};
  bus.sclk_io_num = PIN_SCLK;
  bus.mosi_io_num = PIN_MOSI;
  bus.miso_io_num = -1;
  bus.quadwp_io_num = -1;
  bus.quadhd_io_num = -1;
  bus.max_transfer_sz = sizeof(buf);
  ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));

  esp_lcd_panel_io_spi_config_t ioc = {};
  ioc.dc_gpio_num = PIN_DC;
  ioc.cs_gpio_num = PIN_CS;
  ioc.pclk_hz = 40 * 1000 * 1000;
  ioc.lcd_cmd_bits = 8;
  ioc.lcd_param_bits = 8;
  ioc.spi_mode = 0;
  ioc.trans_queue_depth = 10;
  ioc.on_color_trans_done = onDone;
  ioc.user_ctx = NULL;
  ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &ioc, &io));

  esp_lcd_panel_dev_config_t pc = {};
  pc.reset_gpio_num = PIN_RST;
  pc.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
  pc.bits_per_pixel = 16;
  ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(io, &pc, &panel));
  ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
  ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
  ESP_ERROR_CHECK(esp_lcd_panel_set_gap(panel, 0, 0));
  ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(panel, false));
  ESP_ERROR_CHECK(esp_lcd_panel_mirror(panel, false, false));
  ESP_ERROR_CHECK(esp_lcd_panel_invert_color(panel, false));
  ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));
  Serial.println("READY");

  // one band: how long the call takes to return, and when its transfer is done
  fillBuf(0xF8, 0x00, 240 * BAND);
  int target = cbCount + 1;
  unsigned long c0 = micros();
  esp_lcd_panel_draw_bitmap(panel, 0, 0, 240, BAND, buf);
  unsigned long c1 = micros();
  waitCb(target);
  Serial.print("CALL_US "); Serial.println(c1 - c0);
  Serial.print("DONE_US "); Serial.println(cbAt - c0);

  unsigned long f1 = frame(0xF8, 0x00);                  // RED   (RGB565 0xF800, bytes as sent on the wire)
  Serial.print("FRAME_US 1 "); Serial.println(f1);
  unsigned long f2 = frame(0x07, 0xE0);                  // GREEN (0x07E0)
  Serial.print("FRAME_US 2 "); Serial.println(f2);

  // raw panel IO: a 4x4 WHITE square at (100..103, 100..103)
  uint8_t ca[4] = {0, 100, 0, 103}, ra[4] = {0, 100, 0, 103};
  ESP_ERROR_CHECK(esp_lcd_panel_io_tx_param(io, 0x2A, ca, 4));
  ESP_ERROR_CHECK(esp_lcd_panel_io_tx_param(io, 0x2B, ra, 4));
  fillBuf(0xFF, 0xFF, 16);
  target = cbCount + 1;
  ESP_ERROR_CHECK(esp_lcd_panel_io_tx_color(io, 0x2C, buf, 32));
  waitCb(target);

  // mirrored X: a 10x10 BLUE square drawn at x 0..9 shows at x 230..239
  ESP_ERROR_CHECK(esp_lcd_panel_mirror(panel, true, false));
  fillBuf(0x00, 0x1F, 100);
  target = cbCount + 1;
  esp_lcd_panel_draw_bitmap(panel, 0, 0, 10, 10, buf);
  waitCb(target);

  Serial.print("CB "); Serial.println(cbCount);
  Serial.println("DRAWN");
}

void loop() { delay(10); }
