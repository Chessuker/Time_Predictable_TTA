// System smoke test for simulation (tests/test_baseline_rtl.py): the C PID run
// from the xbus_hil timer interrupt every SMOKE_PERIOD cycles against the
// plant, with the timing monitor on. Not one of the measured programs.
#include <neorv32.h>

#include "hil.h"
#include "pid.h"

#ifndef SMOKE_PERIOD
#define SMOKE_PERIOD 20000
#endif
#define SMOKE_REF 4096

static fpid_state_t pid;
static uint64_t next_release;

void __attribute__((interrupt("machine"), aligned(4))) trap_handler(void) {
  HIL->START = 1;
  int32_t enc = (int32_t)HIL->ENCODER;
  int32_t u = fpid_step(&pid, SMOKE_REF, enc);
  HIL->PWM_CMD = (uint32_t)u;
  next_release += SMOKE_PERIOD;
  hil_set_mtimecmp(next_release);
}

int main(void) {
  fpid_init(&pid);
  next_release = hil_mtime() + 2000;
  hil_set_mtimecmp(next_release);
  HIL->MON_CTRL = 1;
  neorv32_cpu_csr_write(CSR_MTVEC, (uint32_t)&trap_handler);
  neorv32_cpu_csr_write(CSR_MIE, 1u << CSR_MIE_MTIE);
  neorv32_cpu_csr_set(CSR_MSTATUS, 1u << CSR_MSTATUS_MIE);
  while (1) {
  }
  return 0;
}
