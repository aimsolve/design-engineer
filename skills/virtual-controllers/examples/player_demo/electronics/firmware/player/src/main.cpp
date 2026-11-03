// Player example: ESP32 + ST7789 240x320 over SPI (esp_lcd) + LVGL 9.2, three buttons to GND, an EC11 encoder.
// Pins are those of mechanics/cad/connections.py (the wiring of the model). The same file runs on the chip and on the model page.
// Serial: BTN <name> DOWN / UP <ms>, RAW <name> <edges> (contact bounce seen by the interrupt), LONG <name>, ENC <+1|-1> <pos>, FPS <n>.
#include <Arduino.h>
#include <lvgl.h>
#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_heap_caps.h"

#define PIN_SCK 18
#define PIN_MOSI 23
#define PIN_DC 2
#define PIN_CS 5
#define PIN_RST 4
#define PIN_BL 15
#define PIN_PREV 32
#define PIN_PLAY 33
#define PIN_NEXT 25
#define PIN_ENC_A 26
#define PIN_ENC_B 27
#define PIN_ENC_SW 14
#define LCD_W 240
#define LCD_H 320
#define LCD_HZ (40 * 1000 * 1000)
#define BUF_LINES 40
#define LONG_MS 800

static esp_lcd_panel_handle_t panel;
static lv_display_t* disp;
static uint32_t flushes, frames_sec, last_fps_ms;

static bool on_color_done(esp_lcd_panel_io_handle_t, esp_lcd_panel_io_event_data_t*, void* ctx) {
  lv_display_flush_ready((lv_display_t*)ctx);
  return false;
}
static void flush_cb(lv_display_t* d, const lv_area_t* a, uint8_t* px) {
  lv_draw_sw_rgb565_swap(px, lv_area_get_size(a));   // the panel wants the high byte first
  esp_lcd_panel_draw_bitmap(panel, a->x1, a->y1, a->x2 + 1, a->y2 + 1, px);
  if (lv_display_flush_is_last(d)) frames_sec++;
}
static uint32_t tick_cb() { return millis(); }

static void lcd_init() {
  spi_bus_config_t bus = {};
  bus.sclk_io_num = PIN_SCK; bus.mosi_io_num = PIN_MOSI; bus.miso_io_num = -1; bus.quadwp_io_num = -1; bus.quadhd_io_num = -1;
  bus.max_transfer_sz = LCD_W * BUF_LINES * 2;
  ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));
  esp_lcd_panel_io_handle_t io;
  esp_lcd_panel_io_spi_config_t ioc = {};
  ioc.dc_gpio_num = PIN_DC; ioc.cs_gpio_num = PIN_CS; ioc.pclk_hz = LCD_HZ; ioc.lcd_cmd_bits = 8; ioc.lcd_param_bits = 8;
  ioc.spi_mode = 0; ioc.trans_queue_depth = 10; ioc.on_color_trans_done = on_color_done; ioc.user_ctx = disp;
  ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST, &ioc, &io));
  esp_lcd_panel_dev_config_t pc = {};
  pc.reset_gpio_num = PIN_RST; pc.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB; pc.bits_per_pixel = 16;
  ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(io, &pc, &panel));
  esp_lcd_panel_reset(panel);
  esp_lcd_panel_init(panel);
  esp_lcd_panel_invert_color(panel, true);   // IPS panel: colours are right with inversion on
  esp_lcd_panel_disp_on_off(panel, true);
  pinMode(PIN_BL, OUTPUT); digitalWrite(PIN_BL, HIGH);
}

// ---- UI ----
static const char* TRACKS[] = {"Blue Monday", "Heroes", "Kids", "Midnight City", "Strobe"};
static const int NT = 5;
static int cur = 0, sel = 0;
static bool playing = false;
static lv_obj_t *title, *state, *fps, *bar, *cover, *rows[NT], *eq[8];
static lv_image_dsc_t cover_dsc;
static uint16_t cover_px[96 * 96];

static void make_cover() {   // a picture made by the firmware: a radial gradient disc with rings (RGB565)
  for (int y = 0; y < 96; y++) for (int x = 0; x < 96; x++) {
    int dx = x - 48, dy = y - 48, r2 = dx * dx + dy * dy;
    uint8_t r = 0, g = 0, b = 0;
    if (r2 < 47 * 47) { int r = (int)sqrtf((float)r2); bool ring = (r / 4) % 2; float t = r / 47.0f;
      uint8_t R = (uint8_t)(250 - 160 * t), G = (uint8_t)(60 + 90 * (dx > 0)), B = (uint8_t)(120 + 120 * t);
      if (ring) { R = R * 3 / 4; G = G * 3 / 4; B = B * 3 / 4; }
      if (r < 6) { R = G = B = 20; }
      cover_px[y * 96 + x] = ((R & 0xF8) << 8) | ((G & 0xFC) << 3) | (B >> 3); continue; }
    cover_px[y * 96 + x] = 0x10A3;   // the screen background (0x101418): the corners vanish while the cover turns
  }
  cover_dsc.header.magic = LV_IMAGE_HEADER_MAGIC; cover_dsc.header.cf = LV_COLOR_FORMAT_RGB565;
  cover_dsc.header.w = 96; cover_dsc.header.h = 96; cover_dsc.header.stride = 96 * 2;
  cover_dsc.data_size = sizeof cover_px; cover_dsc.data = (const uint8_t*)cover_px;
}
static void eq_anim(void* o, int32_t v) { lv_obj_set_height((lv_obj_t*)o, v); }
static void spin_anim(void* o, int32_t v) { lv_image_set_rotation((lv_obj_t*)o, v); }
static void show() {
  lv_label_set_text_fmt(title, "%d. %s", cur + 1, TRACKS[cur]);
  lv_label_set_text(state, playing ? LV_SYMBOL_PLAY " Playing" : LV_SYMBOL_PAUSE " Paused");
  for (int i = 0; i < NT; i++) {
    lv_obj_set_style_bg_opa(rows[i], i == sel ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
    lv_obj_set_style_text_color(rows[i], lv_color_hex(i == cur ? 0xffd43b : 0xe9ecef), 0);
  }
  for (int i = 0; i < 8; i++) {
    lv_anim_delete(eq[i], eq_anim);
    if (!playing) { lv_obj_set_height(eq[i], 4); continue; }
    lv_anim_t a; lv_anim_init(&a); lv_anim_set_var(&a, eq[i]); lv_anim_set_exec_cb(&a, eq_anim);
    lv_anim_set_values(&a, 4, 14 + (i * 37) % 26); lv_anim_set_duration(&a, 180 + i * 40);
    lv_anim_set_playback_duration(&a, 180 + i * 40); lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE); lv_anim_start(&a);
  }
  lv_anim_delete(cover, spin_anim);
  if (playing) {
    lv_anim_t a; lv_anim_init(&a); lv_anim_set_var(&a, cover); lv_anim_set_exec_cb(&a, spin_anim);
    lv_anim_set_values(&a, 0, 3600); lv_anim_set_duration(&a, 4000); lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE); lv_anim_start(&a);
  }
}
static void ui() {
  lv_obj_t* scr = lv_screen_active();
  lv_obj_set_style_bg_color(scr, lv_color_hex(0x101418), 0);
  title = lv_label_create(scr); lv_obj_set_style_text_font(title, &lv_font_montserrat_20, 0);
  lv_obj_set_style_text_color(title, lv_color_white(), 0); lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 8);
  make_cover();
  cover = lv_image_create(scr); lv_image_set_src(cover, &cover_dsc); lv_obj_align(cover, LV_ALIGN_TOP_LEFT, 14, 40);
  lv_image_set_pivot(cover, 48, 48);
  for (int i = 0; i < 8; i++) {
    eq[i] = lv_obj_create(scr); lv_obj_remove_style_all(eq[i]); lv_obj_set_size(eq[i], 10, 4);
    lv_obj_set_style_bg_opa(eq[i], LV_OPA_COVER, 0); lv_obj_set_style_bg_color(eq[i], lv_palette_main(LV_PALETTE_TEAL), 0);
    lv_obj_align(eq[i], LV_ALIGN_TOP_LEFT, 128 + i * 13, 120); lv_obj_set_style_align(eq[i], LV_ALIGN_TOP_LEFT, 0);
  }
  state = lv_label_create(scr); lv_obj_set_style_text_color(state, lv_color_hex(0xadb5bd), 0); lv_obj_align(state, LV_ALIGN_TOP_LEFT, 128, 48);
  bar = lv_bar_create(scr); lv_obj_set_size(bar, 212, 6); lv_obj_align(bar, LV_ALIGN_TOP_MID, 0, 146); lv_bar_set_range(bar, 0, 1000);
  for (int i = 0; i < NT; i++) {
    rows[i] = lv_label_create(scr); lv_label_set_text_fmt(rows[i], "%d  %s", i + 1, TRACKS[i]); lv_obj_set_width(rows[i], 212);
    lv_obj_set_style_pad_all(rows[i], 4, 0); lv_obj_set_style_radius(rows[i], 4, 0);
    lv_obj_set_style_bg_color(rows[i], lv_color_hex(0x343a40), 0); lv_obj_align(rows[i], LV_ALIGN_TOP_MID, 0, 162 + i * 26);
  }
  fps = lv_label_create(scr); lv_obj_set_style_text_color(fps, lv_color_hex(0x868e96), 0); lv_obj_align(fps, LV_ALIGN_BOTTOM_RIGHT, -6, -4);
  show();
}

// ---- buttons: polled with a 15 ms debounce; an interrupt counts the raw edges (contact bounce) of each press ----
struct Btn { const char* name; int pin; bool down; uint32_t since, changed; bool raw, longsent; volatile uint32_t edges; };
static Btn btns[] = {{"prev", PIN_PREV}, {"play", PIN_PLAY}, {"next", PIN_NEXT}, {"enc", PIN_ENC_SW}};
static void IRAM_ATTR edge0() { btns[0].edges++; }
static void IRAM_ATTR edge1() { btns[1].edges++; }
static void IRAM_ATTR edge2() { btns[2].edges++; }
static void IRAM_ATTR edge3() { btns[3].edges++; }
static void on_press(int i) {
  if (i == 0) { cur = (cur + NT - 1) % NT; sel = cur; }
  else if (i == 2) { cur = (cur + 1) % NT; sel = cur; }
  else if (i == 1) playing = !playing;
  else { cur = sel; playing = true; }
  show();
}
static void poll_buttons() {
  uint32_t now = millis();
  for (int i = 0; i < 4; i++) {
    Btn& b = btns[i];
    bool raw = digitalRead(b.pin) == LOW;
    if (raw != b.raw) { b.raw = raw; b.changed = now; }
    if (raw != b.down && now - b.changed >= 15) {
      b.down = raw;
      if (raw) { b.since = now; b.longsent = false; b.edges = 0; Serial.printf("BTN %s DOWN\n", b.name); on_press(i); }
      else { Serial.printf("BTN %s UP %lu\n", b.name, (unsigned long)(now - b.since)); Serial.printf("RAW %s %lu\n", b.name, (unsigned long)b.edges); }
    }
    if (b.down && !b.longsent && now - b.since >= LONG_MS) {
      b.longsent = true; Serial.printf("LONG %s\n", b.name);
      if (i == 1) { playing = false; lv_bar_set_value(bar, 0, LV_ANIM_OFF); show(); lv_label_set_text(state, LV_SYMBOL_STOP " Stopped"); }
    }
  }
}

// ---- encoder: quadrature state table on both edges, 4 transitions per detent ----
static volatile int enc_steps;
static volatile uint8_t enc_ab;
static void IRAM_ATTR enc_isr() {
  static const int8_t T[16] = {0, -1, 1, 0, 1, 0, 0, -1, -1, 0, 0, 1, 0, 1, -1, 0};
  enc_ab = ((enc_ab << 2) | (digitalRead(PIN_ENC_A) << 1) | digitalRead(PIN_ENC_B)) & 0x0F;
  enc_steps += T[enc_ab];
}
static int enc_pos;
static void poll_encoder() {
  int d = enc_steps / 4;
  if (!d) return;
  enc_steps -= d * 4; enc_pos += d;
  sel = ((sel + d) % NT + NT) % NT;
  Serial.printf("ENC %+d %d\n", d, enc_pos);
  show();
}

void setup() {
  Serial.begin(115200);
  Serial.println("BOOT player demo");
  for (auto& b : btns) pinMode(b.pin, INPUT_PULLUP);
  attachInterrupt(PIN_PREV, edge0, CHANGE); attachInterrupt(PIN_PLAY, edge1, CHANGE);
  attachInterrupt(PIN_NEXT, edge2, CHANGE); attachInterrupt(PIN_ENC_SW, edge3, CHANGE);
  pinMode(PIN_ENC_A, INPUT_PULLUP); pinMode(PIN_ENC_B, INPUT_PULLUP);
  enc_ab = (digitalRead(PIN_ENC_A) << 1) | digitalRead(PIN_ENC_B);
  attachInterrupt(PIN_ENC_A, enc_isr, CHANGE); attachInterrupt(PIN_ENC_B, enc_isr, CHANGE);

  lv_init();
  lv_tick_set_cb(tick_cb);
  disp = lv_display_create(LCD_W, LCD_H);
  lcd_init();
  size_t n = LCD_W * BUF_LINES * 2;
  void* b1 = heap_caps_malloc(n, MALLOC_CAP_DMA);
  void* b2 = heap_caps_malloc(n, MALLOC_CAP_DMA);
  lv_display_set_buffers(disp, b1, b2, n, LV_DISPLAY_RENDER_MODE_PARTIAL);
  lv_display_set_flush_cb(disp, flush_cb);
  ui();
  Serial.println("READY");
}

void loop() {
  poll_buttons();
  poll_encoder();
  uint32_t now = millis();
  if (playing) lv_bar_set_value(bar, (now / 30) % 1000, LV_ANIM_OFF);
  if (now - last_fps_ms >= 1000) {
    if (frames_sec) Serial.printf("FPS %lu\n", (unsigned long)frames_sec);
    lv_label_set_text_fmt(fps, "%lu fps", (unsigned long)frames_sec);
    frames_sec = 0; last_fps_ms = now;
  }
  lv_timer_handler();
  delay(5);
}
