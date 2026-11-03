/* LVGL 9.2 configuration of the player example: only what differs from LVGL's defaults (lv_conf_internal.h fills in the rest). */
#ifndef LV_CONF_H
#define LV_CONF_H
#define LV_COLOR_DEPTH 16
#define LV_USE_STDLIB_MALLOC LV_STDLIB_CLIB   /* LVGL objects from the heap: a static pool would not fit in the ESP32 DRAM */
#define LV_DEF_REFR_PERIOD 20
#define LV_USE_OS LV_OS_NONE
#define LV_USE_LOG 0
#define LV_FONT_MONTSERRAT_14 1
#define LV_FONT_MONTSERRAT_20 1
#define LV_FONT_MONTSERRAT_28 1
#define LV_FONT_DEFAULT &lv_font_montserrat_14
#endif
