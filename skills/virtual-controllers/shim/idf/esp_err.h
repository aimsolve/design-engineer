#pragma once
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_INVALID_SIZE 0x104
#define ESP_ERR_NOT_FOUND 0x105
#define ESP_ERR_NOT_SUPPORTED 0x106
#define ESP_ERR_TIMEOUT 0x107
#ifdef __cplusplus
extern "C" {
#endif
const char* esp_err_to_name(esp_err_t code);
void vc_esp_error_check_failed(esp_err_t rc, const char* file, int line, const char* expr);
#ifdef __cplusplus
}
#endif
#define ESP_ERROR_CHECK(x) do { esp_err_t err_rc_ = (x); if (err_rc_ != ESP_OK) vc_esp_error_check_failed(err_rc_, __FILE__, __LINE__, #x); } while (0)
#define ESP_ERROR_CHECK_WITHOUT_ABORT(x) (x)
