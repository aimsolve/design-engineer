/* LVGL on the virtual board: compiled into a build only when the firmware's libraries include LVGL (build.mjs).
 * LVGL waits for the previous flush with a bare `while (disp->flushing);` unless the display has a flush wait callback. The virtual board
 * runs an interrupt (the transfer-done callback that clears the flag) only when the firmware calls into the shim, so that spin never
 * ends. Before every esp_lcd colour transfer, a display without its own wait gets one that sleeps until the queued transfers are done
 * (their callbacks call lv_display_flush_ready): the firmware is unchanged and keeps its timing. A display with its own wait keeps it. */
#include "lvgl.h"
#if LVGL_VERSION_MAJOR >= 9   /* LVGL 8 (lv_disp_drv_t.wait_cb) is not covered: its firmware must wait with a call of its own */
#include "src/display/lv_display_private.h"

void vc_lcd_wait_all(void);

static void vc_lvgl_wait(lv_display_t* d) {
    while (d->flushing) vc_lcd_wait_all();
}

void vc_lvgl_guard(void) {
    for (lv_display_t* d = lv_display_get_next(NULL); d; d = lv_display_get_next(d))
        if (!d->flush_wait_cb) d->flush_wait_cb = vc_lvgl_wait;
}
#endif
