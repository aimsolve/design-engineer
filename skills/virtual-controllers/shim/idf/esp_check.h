#pragma once
#include "esp_err.h"
#include "esp_log.h"
#define ESP_RETURN_ON_ERROR(x, log_tag, ...) do { esp_err_t err_rc_ = (x); if (err_rc_ != ESP_OK) { (void)(log_tag); return err_rc_; } } while (0)
#define ESP_RETURN_ON_FALSE(a, err_code, log_tag, ...) do { if (!(a)) { (void)(log_tag); return err_code; } } while (0)
#define ESP_GOTO_ON_ERROR(x, goto_tag, log_tag, ...) do { esp_err_t err_rc_ = (x); if (err_rc_ != ESP_OK) { (void)(log_tag); ret = err_rc_; goto goto_tag; } } while (0)
#define ESP_GOTO_ON_FALSE(a, err_code, goto_tag, log_tag, ...) do { if (!(a)) { (void)(log_tag); ret = err_code; goto goto_tag; } } while (0)
