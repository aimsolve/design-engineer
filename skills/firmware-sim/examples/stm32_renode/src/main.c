// STM32F407 blinky (PD12 LED) + USART2 log + reading a synthetic ADC that Renode provides as a Python peripheral
// at FAKE_ADC_ADDR (fake_adc.py). On real hardware replace adc_read() with the ADS1220 SPI read.
#include "stm32f4xx_hal.h"
#include <stdio.h>
#include <string.h>

static UART_HandleTypeDef huart2;

static void uart_puts(const char *s) { HAL_UART_Transmit(&huart2, (uint8_t *)s, strlen(s), 100); }

static int32_t adc_read(void) {  // 24-bit two's complement, sign-extended
  uint32_t v = *(volatile uint32_t *)FAKE_ADC_ADDR & 0xFFFFFF;
  return (v & 0x800000) ? (int32_t)(v | 0xFF000000) : (int32_t)v;
}

int main(void) {
  HAL_Init();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_USART2_CLK_ENABLE();
  GPIO_InitTypeDef g = {0};
  g.Pin = GPIO_PIN_2 | GPIO_PIN_3; g.Mode = GPIO_MODE_AF_PP; g.Pull = GPIO_PULLUP;
  g.Speed = GPIO_SPEED_FREQ_HIGH; g.Alternate = GPIO_AF7_USART2;
  HAL_GPIO_Init(GPIOA, &g);
  g.Pin = GPIO_PIN_12; g.Mode = GPIO_MODE_OUTPUT_PP; g.Pull = GPIO_NOPULL; g.Alternate = 0;
  HAL_GPIO_Init(GPIOD, &g);
  huart2.Instance = USART2; huart2.Init.BaudRate = 115200; huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1; huart2.Init.Parity = UART_PARITY_NONE; huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE; huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  HAL_UART_Init(&huart2);
  uart_puts("BOOT stm32 renode example\r\n");

  int32_t win[8] = {0};
  char buf[96];
  for (int n = 0; n < 40; n++) {
    int32_t raw = adc_read();
    win[n % 8] = raw;
    int64_t sum = 0;
    for (int i = 0; i < 8; i++) sum += win[i];
    HAL_GPIO_TogglePin(GPIOD, GPIO_PIN_12);
    snprintf(buf, sizeof buf, "tick %d raw %ld avg8 %ld led %d\r\n", n, (long)raw, (long)(n >= 7 ? sum / 8 : raw),
             (int)HAL_GPIO_ReadPin(GPIOD, GPIO_PIN_12));
    uart_puts(buf);
    HAL_Delay(20);
  }
  uart_puts("DONE\r\n");
  for (;;) {}
}

void SysTick_Handler(void) { HAL_IncTick(); }
