#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
// One heap on the virtual board: the capability bits are accepted and ignored (PSRAM, DMA and internal RAM are all plain memory).
#define MALLOC_CAP_EXEC (1 << 0)
#define MALLOC_CAP_32BIT (1 << 1)
#define MALLOC_CAP_8BIT (1 << 2)
#define MALLOC_CAP_DMA (1 << 3)
#define MALLOC_CAP_SPIRAM (1 << 10)
#define MALLOC_CAP_INTERNAL (1 << 11)
#define MALLOC_CAP_DEFAULT (1 << 12)
#define heap_caps_malloc(size, caps) malloc(size)
#define heap_caps_calloc(n, size, caps) calloc(n, size)
#define heap_caps_realloc(p, size, caps) realloc(p, size)
#define heap_caps_aligned_alloc(align, size, caps) aligned_alloc(align, ((size) + (align) - 1) / (align) * (align))
#define heap_caps_free(p) free(p)
#define heap_caps_get_free_size(caps) ((size_t)0)
