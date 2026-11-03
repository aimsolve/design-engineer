#pragma once
// newlib's sys/cdefs.h as the vendored IDF files use it
#include <stddef.h>
#ifndef __containerof
#define __containerof(ptr, type, member) ((type*)((char*)(ptr) - offsetof(type, member)))
#endif
#ifndef __unused
#define __unused __attribute__((unused))
#endif
