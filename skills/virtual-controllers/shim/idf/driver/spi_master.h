#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "esp_heap_caps.h"
// The SPI bus of the virtual board: spi_bus_initialize records its pins (bytes reach a chip only on the pins it is wired to);
// transfers go through esp_lcd (shim/idf_vc.cpp). spi_device_* (a raw SPI device) is not modelled yet.
typedef enum { SPI1_HOST = 0, SPI2_HOST = 1, SPI3_HOST = 2 } spi_host_device_t;
#define HSPI_HOST SPI2_HOST
#define VSPI_HOST SPI3_HOST
typedef enum { SPI_DMA_DISABLED = 0, SPI_DMA_CH1 = 1, SPI_DMA_CH2 = 2, SPI_DMA_CH_AUTO = 3 } spi_dma_chan_t;
typedef int spi_common_dma_t;
typedef struct {
  union { int mosi_io_num; int data0_io_num; };
  union { int miso_io_num; int data1_io_num; };
  int sclk_io_num;
  union { int quadwp_io_num; int data2_io_num; };
  union { int quadhd_io_num; int data3_io_num; };
  int data4_io_num, data5_io_num, data6_io_num, data7_io_num;
  int max_transfer_sz;
  uint32_t flags;
  int isr_cpu_id;
  int intr_flags;
} spi_bus_config_t;
#ifdef __cplusplus
extern "C" {
#endif
esp_err_t spi_bus_initialize(spi_host_device_t host, const spi_bus_config_t* cfg, int dma_chan);
esp_err_t spi_bus_free(spi_host_device_t host);
#ifdef __cplusplus
}
#endif
