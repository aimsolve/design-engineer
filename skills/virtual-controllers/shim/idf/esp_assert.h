#pragma once
#include <assert.h>
#ifdef __cplusplus
#define ESP_STATIC_ASSERT static_assert
#else
#define ESP_STATIC_ASSERT _Static_assert
#endif
