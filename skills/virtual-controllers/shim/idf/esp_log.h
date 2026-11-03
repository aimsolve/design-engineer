#pragma once
// Logs are dropped (a real ESP-IDF build prints them on UART0; the firmware's own Serial output is what the page shows).
typedef enum { ESP_LOG_NONE, ESP_LOG_ERROR, ESP_LOG_WARN, ESP_LOG_INFO, ESP_LOG_DEBUG, ESP_LOG_VERBOSE } esp_log_level_t;
#define esp_log_level_set(tag, level) ((void)(tag), (void)(level))
#define ESP_LOGE(tag, ...) ((void)(tag))
#define ESP_LOGW(tag, ...) ((void)(tag))
#define ESP_LOGI(tag, ...) ((void)(tag))
#define ESP_LOGD(tag, ...) ((void)(tag))
#define ESP_LOGV(tag, ...) ((void)(tag))
#define ESP_EARLY_LOGE(tag, ...) ((void)(tag))
