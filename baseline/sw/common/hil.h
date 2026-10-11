// Registers of baseline/rtl/xbus_hil.sv (XBUS 0xF000_0000, uncached).
#ifndef HIL_H
#define HIL_H

#include <stdint.h>

typedef struct {
  volatile uint32_t MTIME_LO;     // 0x00 cycles since reset
  volatile uint32_t MTIME_HI;     // 0x04
  volatile uint32_t MTIMECMP_LO;  // 0x08 a write commits {MTIMECMP_HI, value}
  volatile uint32_t MTIMECMP_HI;  // 0x0C write first
  volatile uint32_t ENCODER;      // 0x10 read = SENSE event
  volatile uint32_t PWM_CMD;      // 0x14 write = ACT event
  volatile uint32_t DIN;          // 0x18
  volatile uint32_t START;        // 0x1C write = START event
  volatile uint32_t MON_CTRL;     // 0x20 bit 0: monitor on (owns the UART TX line)
  volatile uint32_t MON_DROPS;    // 0x24
} hil_regs_t;

#define HIL ((hil_regs_t *)0xF0000000u)

static inline uint64_t hil_mtime(void) {
  uint32_t hi, lo;
  do {
    hi = HIL->MTIME_HI;
    lo = HIL->MTIME_LO;
  } while (hi != HIL->MTIME_HI);
  return ((uint64_t)hi << 32) | lo;
}

static inline void hil_set_mtimecmp(uint64_t t) {
  HIL->MTIMECMP_HI = (uint32_t)(t >> 32);
  HIL->MTIMECMP_LO = (uint32_t)t;           // commits both halves at once
}

#endif
