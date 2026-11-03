// ESP-IDF calls on the virtual board: gpio, the SPI bus, vTaskDelay, and the SPI panel IO of esp_lcd
// (esp_lcd_new_panel_io_spi) that the vendored lib/esp_lcd panel drivers (st7789, ...) talk through.
// The panel IO is asynchronous like the real one: tx_color queues the buffer (the DMA reads it while it sends) and returns;
// the transfer takes bytes x 8 / pclk of virtual time on a bus shared by every IO of that host; the bytes reach the display and
// on_color_trans_done runs when it is over (a timed service: an interrupt at the next sync point after that time). So a firmware that
// refills its buffer before the callback shows the tearing it would show on the chip. tx_param, like the IDF one, first waits for the
// queued colours. A firmware with LVGL gets a flush wait that yields (shim/vc_lvgl.c): LVGL's own wait is a bare spin.
#include <deque>
#include <vector>
#include "vc_core.h"   // after the std headers: it defines the Arduino min / max macros
#include "esp_err.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "freertos/task.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_io_interface.h"

extern "C" {
const char* esp_err_to_name(esp_err_t code) {
  switch (code) {
    case ESP_OK: return "ESP_OK"; case ESP_FAIL: return "ESP_FAIL"; case ESP_ERR_NO_MEM: return "ESP_ERR_NO_MEM";
    case ESP_ERR_INVALID_ARG: return "ESP_ERR_INVALID_ARG"; case ESP_ERR_INVALID_STATE: return "ESP_ERR_INVALID_STATE";
    case ESP_ERR_NOT_SUPPORTED: return "ESP_ERR_NOT_SUPPORTED"; case ESP_ERR_TIMEOUT: return "ESP_ERR_TIMEOUT";
  }
  return "ESP_ERR";
}
void vc_esp_error_check_failed(esp_err_t rc, const char* file, int line, const char* expr) {   // what the real ESP32 prints, then it aborts
  Serial.printf("ESP_ERROR_CHECK failed: esp_err_t 0x%x (%s) at %s:%d\nexpression: %s\n", rc, esp_err_to_name(rc), file, line, expr);
  abort();
}

esp_err_t gpio_config(const gpio_config_t* c) {
  if (!c) return ESP_ERR_INVALID_ARG;
  for (int p = 0; p < VC_MAX_PINS; p++) {
    if (!(c->pin_bit_mask >> p & 1)) continue;
    pinMode(p, (c->mode & GPIO_MODE_OUTPUT) ? OUTPUT : c->pull_up_en ? INPUT_PULLUP : INPUT);
  }
  return ESP_OK;
}
esp_err_t gpio_set_level(gpio_num_t pin, uint32_t level) { if (pin < 0) return ESP_ERR_INVALID_ARG; digitalWrite(pin, level ? 1 : 0); return ESP_OK; }
int gpio_get_level(gpio_num_t pin) { return pin < 0 ? 0 : digitalRead(pin); }
esp_err_t gpio_reset_pin(gpio_num_t pin) { if (pin >= 0) pinMode(pin, INPUT); return ESP_OK; }
esp_err_t gpio_set_direction(gpio_num_t pin, gpio_mode_t m) { if (pin >= 0) pinMode(pin, (m & GPIO_MODE_OUTPUT) ? OUTPUT : INPUT); return ESP_OK; }

void vTaskDelay(TickType_t ticks) { delay(ticks); }
TickType_t xTaskGetTickCount(void) { return (TickType_t)millis(); }

struct VcSpiBus { bool on = false; double busy_until = 0; };
static VcSpiBus g_bus[3];
esp_err_t spi_bus_initialize(spi_host_device_t host, const spi_bus_config_t* c, int) {
  if ((int)host < 0 || host > SPI3_HOST || !c) return ESP_ERR_INVALID_ARG;
  if (g_bus[host].on) return ESP_ERR_INVALID_STATE;
  g_bus[host].on = true;
  vc_js_spi_begin(host, c->sclk_io_num, c->mosi_io_num, c->miso_io_num);
  return ESP_OK;
}
esp_err_t spi_bus_free(spi_host_device_t host) { if (host > SPI3_HOST) return ESP_ERR_INVALID_ARG; g_bus[host].on = false; vc_js_spi_begin(host, -1, -1, -1); return ESP_OK; }
}

// ---------------------------------------------------------------- esp_lcd SPI panel IO

struct VcLcdIo {
  esp_lcd_panel_io_t base;
  int host, dc, cs, cmd_bytes;
  double hz;
  size_t depth;
  bool dc_cmd_level, dc_data_level, dc_param_level;
  esp_lcd_panel_io_color_trans_done_cb_t cb;
  void* ctx;
  struct Trans { double done; const uint8_t* bytes; size_t size; };   // the firmware's buffer: it must stay untouched until the callback
  std::deque<Trans> inflight;    // queued colour transfers: when each finishes, and its bytes
};
static std::vector<VcLcdIo*> g_ios;

static void lcd_send(VcLcdIo* io, const void* p, size_t n, bool dc);
static double lcd_service(double now) {
  double next = INFINITY;
  for (size_t i = 0; i < g_ios.size(); i++) {
    VcLcdIo* io = g_ios[i];
    while (!io->inflight.empty() && io->inflight.front().done <= now) {
      VcLcdIo::Trans t = io->inflight.front();
      io->inflight.pop_front();
      lcd_send(io, t.bytes, t.size, io->dc_data_level);   // the transfer is over: the display has the pixels
      esp_lcd_panel_io_event_data_t ev = {};
      if (io->cb) io->cb(&io->base, &ev, io->ctx);
    }
    if (!io->inflight.empty() && io->inflight.front().done < next) next = io->inflight.front().done;
  }
  return next;
}
static int g_lcd_svc = (vc_register_service(lcd_service), 0);

// wait (the callbacks run meanwhile) until the IO has at most `keep` colour transfers queued. Completes them itself rather than through
// vc_poll(): inside an interrupt / a mail handler (a BLE or Wire command that draws) the service is not re-entered, and a poll would spin.
static void lcd_drain(VcLcdIo* io, size_t keep) {
  while (io->inflight.size() > keep) {
    double t = io->inflight.front().done;
    if (t > vc_now()) vc_sleep_until(t);
    lcd_service(vc_now());
  }
}
// every IO's queued colours done (the LVGL flush wait of shim/vc_lvgl.c)
extern "C" void vc_lcd_wait_all(void) { for (size_t i = 0; i < g_ios.size(); i++) lcd_drain(g_ios[i], 0); vc_advance(VC_COST_MISC); }
extern "C" __attribute__((weak)) void vc_lvgl_guard(void);   // shim/vc_lvgl.c, linked only with LVGL
static void lcd_send(VcLcdIo* io, const void* p, size_t n, bool dc) {
  if (!n) return;
  if (io->dc >= 0) digitalWrite(io->dc, dc);
  if (io->cs >= 0) digitalWrite(io->cs, LOW);
  vc_js_spi(io->host, (const uint8_t*)p, (int)n, vc_now());
  if (io->cs >= 0) digitalWrite(io->cs, HIGH);
}
static void lcd_cmd(VcLcdIo* io, int cmd) {
  if (cmd < 0) return;
  uint8_t b[4];
  for (int i = 0; i < io->cmd_bytes; i++) b[i] = (uint8_t)(cmd >> (8 * (io->cmd_bytes - 1 - i)));
  lcd_send(io, b, io->cmd_bytes, io->dc_cmd_level);
}
static double xfer_us(VcLcdIo* io, size_t n) { return n * 8.0e6 / io->hz; }

static esp_err_t lcd_tx_param(esp_lcd_panel_io_t* base, int cmd, const void* param, size_t size) {
  VcLcdIo* io = (VcLcdIo*)base;
  lcd_drain(io, 0);                                    // the IDF polls the bus: queued colour transfers finish first
  lcd_cmd(io, cmd);
  lcd_send(io, param, size, io->dc_param_level);
  vc_activity();
  vc_advance(xfer_us(io, (cmd >= 0 ? io->cmd_bytes : 0) + size) + VC_COST_MISC);
  return ESP_OK;
}
static esp_err_t lcd_tx_color(esp_lcd_panel_io_t* base, int cmd, const void* color, size_t size) {
  VcLcdIo* io = (VcLcdIo*)base;
  lcd_drain(io, cmd >= 0 ? 0 : (io->depth ? io->depth - 1 : 0));   // a command goes out by polling after the queued colours, like
  if (vc_lvgl_guard) vc_lvgl_guard();                                // the IDF; without one, a full queue blocks (xQueueSend)
  lcd_cmd(io, cmd);
  if (cmd >= 0) vc_advance(xfer_us(io, io->cmd_bytes));
  VcSpiBus& bus = g_bus[io->host];
  double start = bus.busy_until > vc_now() ? bus.busy_until : vc_now(), done = start + xfer_us(io, size);
  bus.busy_until = done;
  io->inflight.push_back({done, (const uint8_t*)color, size});
  vc_wake_at(done);
  vc_activity();
  vc_advance(VC_COST_MISC);
  return ESP_OK;
}
static esp_err_t lcd_rx_param(esp_lcd_panel_io_t* base, int cmd, void* param, size_t size) {
  VcLcdIo* io = (VcLcdIo*)base;
  lcd_drain(io, 0);
  lcd_cmd(io, cmd);
  if (param) memset(param, 0, size);                   // no chip answers reads yet
  return ESP_OK;
}
static esp_err_t lcd_register_cbs(esp_lcd_panel_io_t* base, const esp_lcd_panel_io_callbacks_t* cbs, void* ctx) {
  VcLcdIo* io = (VcLcdIo*)base; io->cb = cbs ? cbs->on_color_trans_done : nullptr; io->ctx = ctx; return ESP_OK;
}
static esp_err_t lcd_del(esp_lcd_panel_io_t* base) {
  VcLcdIo* io = (VcLcdIo*)base;
  lcd_drain(io, 0);
  for (size_t i = 0; i < g_ios.size(); i++) if (g_ios[i] == io) { g_ios.erase(g_ios.begin() + i); break; }
  delete io;
  return ESP_OK;
}

extern "C" esp_err_t esp_lcd_new_panel_io_spi(esp_lcd_spi_bus_handle_t bus, const esp_lcd_panel_io_spi_config_t* c, esp_lcd_panel_io_handle_t* ret) {
  if (!c || !ret || bus < 0 || bus > SPI3_HOST || !c->pclk_hz) return ESP_ERR_INVALID_ARG;
  if (!g_bus[bus].on) return ESP_ERR_INVALID_STATE;
  VcLcdIo* io = new VcLcdIo();
  io->base.rx_param = lcd_rx_param; io->base.tx_param = lcd_tx_param; io->base.tx_color = lcd_tx_color;
  io->base.del = lcd_del; io->base.register_event_callbacks = lcd_register_cbs;
  io->host = bus; io->dc = c->dc_gpio_num; io->cs = c->cs_gpio_num; io->hz = c->pclk_hz;
  io->cmd_bytes = c->lcd_cmd_bits > 8 ? (c->lcd_cmd_bits + 7) / 8 : 1;
  io->depth = c->trans_queue_depth ? c->trans_queue_depth : 1;
  io->dc_cmd_level = c->flags.dc_high_on_cmd; io->dc_data_level = !c->flags.dc_low_on_data; io->dc_param_level = !c->flags.dc_low_on_param;
  io->cb = c->on_color_trans_done; io->ctx = c->user_ctx;
  if (io->dc >= 0) pinMode(io->dc, OUTPUT);
  if (io->cs >= 0) { pinMode(io->cs, OUTPUT); digitalWrite(io->cs, HIGH); }
  g_ios.push_back(io);
  *ret = &io->base;
  return ESP_OK;
}
