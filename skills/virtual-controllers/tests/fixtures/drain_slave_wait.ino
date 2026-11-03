// Review fix fixture (review 2): drain_slave.ino with delay() in the Wire handler and two bands queued at once.
// main loop keeps colour transfers in flight. Every handler must finish: "RX <n> DONE". Also: the buffer is refilled GREEN right after
// a RED tx_color, before its callback; a display that takes the bytes when the transfer ends shows GREEN (the tearing of real DMA).
#include <Arduino.h>
#include <Wire.h>
#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_panel_ops.h"
static esp_lcd_panel_handle_t panel;
static esp_lcd_panel_io_handle_t io;
static uint8_t band[240 * 40 * 2];
static volatile int cbs = 0;
static bool onDone(esp_lcd_panel_io_handle_t, esp_lcd_panel_io_event_data_t*, void*) { cbs = cbs + 1; return false; }
static void fill(uint8_t hi, uint8_t lo) { for (size_t i = 0; i < sizeof band; i += 2) { band[i] = hi; band[i + 1] = lo; } }
void onRx(int n) {
  int v = Wire.read();
  delay(5);                                            // a handler that waits (review 2)
  Serial.print("RX "); Serial.println(v);
  esp_lcd_panel_disp_on_off(panel, true);              // tx_param while a band may be in flight
  Serial.print("RX "); Serial.print(v); Serial.println(" DONE");
}
void setup() {
  Serial.begin(115200);
  spi_bus_config_t bus = {};
  bus.sclk_io_num = 18; bus.mosi_io_num = 23; bus.miso_io_num = -1; bus.quadwp_io_num = -1; bus.quadhd_io_num = -1; bus.max_transfer_sz = sizeof band;
  ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));
  esp_lcd_panel_io_spi_config_t c = {};
  c.dc_gpio_num = 16; c.cs_gpio_num = 5; c.pclk_hz = 10 * 1000 * 1000; c.lcd_cmd_bits = 8; c.lcd_param_bits = 8; c.trans_queue_depth = 4; c.on_color_trans_done = onDone;
  ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &c, &io));
  esp_lcd_panel_dev_config_t pc = {}; pc.reset_gpio_num = 17; pc.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB; pc.bits_per_pixel = 16;
  ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(io, &pc, &panel));
  esp_lcd_panel_reset(panel); esp_lcd_panel_init(panel); esp_lcd_panel_disp_on_off(panel, true);
  fill(0xF8, 0x00);
  esp_lcd_panel_draw_bitmap(panel, 0, 0, 240, 40, band);
  fill(0x07, 0xE0);                                    // refilled before the transfer is over
  while (cbs < 1) delayMicroseconds(100);
  Wire.begin(0x22);
  Wire.onReceive(onRx);
  Serial.println("SLAVE READY");
}
void loop() {
  int target = cbs + 1;
  esp_lcd_panel_draw_bitmap(panel, 0, 40, 240, 80, band);   // two bands queued back to back (~15 ms in flight at 10 MHz)
  esp_lcd_panel_draw_bitmap(panel, 0, 80, 240, 120, band);
  target++;
  while (cbs < target) delayMicroseconds(100);
}
