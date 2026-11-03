#pragma once
#include <stdint.h>
// FreeRTOS on the virtual board (tasks): ticks are milliseconds; vTaskDelay = delay().
typedef uint32_t TickType_t;
typedef int BaseType_t;
typedef unsigned int UBaseType_t;
#define portTICK_PERIOD_MS 1
#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))
#define pdTRUE 1
#define pdFALSE 0
#define pdPASS 1
#define portMAX_DELAY ((TickType_t)0xffffffffUL)
