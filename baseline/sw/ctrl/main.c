// The control task of the Phase 6 baseline: FixedPID (pid.c) against the plant,
// released by the xbus_hil timer every CTRL_PERIOD cycles, with the setpoint
// schedule of programs/control.tta. One source, built per configuration:
//
//   -DTRIG=0  timer interrupt (mtvec vectored, entry.S stores START first)
//   -DTRIG=1  polling: main runs CHUNK background units, then reads mip.MTIP;
//             on a release it runs START and the control step with interrupts
//             off (docs/baseline_measurement.md, 3.1 and 3.2)
//   -DLOAD=0  no background load
//   -DLOAD=1  L1: D$ thrashing (read-modify-write over 16 KB, one word per 64 B line)
//   -DLOAD=2  L1 + I$: as LOAD=1, plus an 8 KB straight-line code block per sweep
//   -DLOAD=3  L2: as LOAD=1, plus a GPTMR interrupt at 7300.3 Hz (13,698 cycles, not a
//             divisor of the period) whose handler does a fixed amount of work
//             (BG_ITER loop iterations) and touches neither the plant nor the PID
//   -DACT=0   actuate as soon as pwm_cmd is computed
//   -DACT=1   actuate at release + CTRL_ACT (busy-wait on mtime)
//   -DCHUNK=N polling: background units between two polls (1..256)
//   -DBG_CODE=1 interrupt mode, LOAD=2: the code chain of polling mode instead of
//             the board's straight 2048-nop block (simulation reference for the
//             throughput comparison, docs/baseline_measurement.md 3.4)
//   -DPERIOD_CYC / -DSTART_DELAY  shorter values for simulation
//
// What is measured comes from the xbus_hil monitor, not from this program.
#include <neorv32.h>

#include "hil.h"
#include "pid.h"
#include "pid_const.h"

#ifndef TRIG
#define TRIG 0
#endif
#if TRIG != 0 && TRIG != 1
#error "TRIG: 0 (timer interrupt) or 1 (polling)"
#endif
#ifndef CHUNK
#define CHUNK 1
#endif
#if TRIG == 1 && (CHUNK < 1 || CHUNK > 256)
#error "CHUNK: 1..256 units"
#endif
#ifndef BG_CODE
#define BG_CODE 0                // TRIG=0 LOAD=2 only: 1 = the code chain of polling mode (simulation reference)
#endif
#ifndef LOAD
#define LOAD 0
#endif
#ifndef ACT
#define ACT 0
#endif
#ifndef PERIOD_CYC
#define PERIOD_CYC CTRL_PERIOD
#endif
#ifndef START_DELAY
#define START_DELAY 100000000u   // 1 s from start to the first release: time for the host to switch baud
#endif

#if LOAD == 3
// GPTMR (neorv32_gptmr.vhd): PRSC 0 ticks every 2 cycles; continuous mode counts
// 0..THR and wraps, so the period is 2 x (THR + 1) cycles
#define GPTMR_THR 6848u          // 13,698 cycles = 7300.3 Hz
#ifndef BG_ITER
#define BG_ITER 60               // handler body (MARK_IN..MARK_OUT) 502 cycles with the caches on, 2082 off
#endif
#endif

#define THRASH_BYTES 16384u      // 4 x the D$ (64 lines x 64 B, direct-mapped)
#define LINE_BYTES   64u

static fpid_state_t pid;
static uint64_t release;         // the release being served (= mtimecmp)
static uint32_t sched_i, sched_left;
static const int32_t sched_len[CTRL_SCHED_N] = CTRL_SCHED_LEN;
static const int32_t sched_ref[CTRL_SCHED_N] = CTRL_SCHED_REF;

#if LOAD >= 1
static volatile uint32_t thrash[THRASH_BYTES / 4] __attribute__((aligned(LINE_BYTES)));
#endif

extern void ctrl_vectors(void);

void ctrl_fault(void) {
  while (1) {
  }
}

// background interrupt of L2 (vector 28); MARK_IN/MARK_OUT let the xbus_hil
// monitor count the bodies and their cycles per period (record flags)
void __attribute__((interrupt("machine"))) ctrl_gptmr_isr(void) {
#if LOAD == 3
  HIL->MARK_IN = 1;
  NEORV32_GPTMR->CSR1.IRQ = 0;                               // acknowledge (slice 0)
  for (uint32_t i = BG_ITER; i != 0; --i) {
    asm volatile("");
  }
  HIL->MARK_OUT = 1;
#else
  ctrl_fault();
#endif
}

// setpoint of the current period, then advance the schedule (env.ref_at)
static inline int32_t next_ref(void) {
  int32_t ref = sched_ref[sched_i];
  if (--sched_left == 0) {
    sched_i = sched_i + 1 == CTRL_SCHED_N ? 0 : sched_i + 1;
    sched_left = (uint32_t)sched_len[sched_i];
  }
  return ref;
}

// one period, after START
static inline void control_step(void) {
  int32_t ref = next_ref();
  int32_t enc = (int32_t)HIL->ENCODER;                       // SENSE
  int32_t u = fpid_step(&pid, ref, enc);
#if ACT == 1
  while (HIL->MTIME_LO - (uint32_t)release < CTRL_ACT) {
  }
#endif
  HIL->PWM_CMD = (uint32_t)u;                                // ACT
  release += PERIOD_CYC;
  hil_set_mtimecmp(release);                                 // clears MTIP
}

void __attribute__((interrupt("machine"))) ctrl_isr(void) {  // via entry.S
  control_step();
}

#if LOAD == 2 && (TRIG == 1 || BG_CODE == 1)
// The code block as a chain of 256 pieces, 32 B (8 instructions) each, line
// aligned: 5 nops, then count down t0 and fall through into the next piece
// (the taken branch skips the return) until t0 reaches 0. The last piece is
// followed by a jump to the first, so a call may start at any piece and run
// any number of them: one call per chunk in polling mode, one call of all 256
// per sweep in the interrupt-mode reference (BG_CODE = 1).
asm(".section .text.ctrl_pieces, \"ax\"\n"
    " .option push\n .option norvc\n .balign 64\n"
    " .global ctrl_code_pieces\n .type ctrl_code_pieces, @function\n"
    "ctrl_code_pieces:\n"
    " .rept 256\n nop\n nop\n nop\n nop\n nop\n addi t0, t0, -1\n bnez t0, 1f\n ret\n1:\n .endr\n"
    " j ctrl_code_pieces\n"
    " .size ctrl_code_pieces, . - ctrl_code_pieces\n .option pop\n .previous");
extern void ctrl_code_pieces(void);

// run pieces unit, unit + 1, ... (mod 256), n of them (n >= 1), in one call
static inline void code_pieces(uint32_t unit, uint32_t n) {
  register uint32_t cnt asm("t0") = n;
  asm volatile("jalr ra, 0(%1)" : "+r"(cnt) : "r"((uint32_t)&ctrl_code_pieces + unit * 32u) : "ra", "memory");
}
#endif

#if TRIG == 0
#if LOAD == 2 && BG_CODE == 0
// the board's L1di (6343d5e): 2048 nops in one function, wherever it lands
static void __attribute__((noinline)) code_block(void) {
  asm volatile(".option push\n .option norvc\n .rept 2048\n nop\n .endr\n .option pop");
}
#endif

// one pass of the background load
static inline void load_sweep(void) {
#if LOAD >= 1
  for (uint32_t i = 0; i < THRASH_BYTES / 4; i += LINE_BYTES / 4) {
    thrash[i] = thrash[i] + 1;
  }
#endif
#if LOAD == 2 && BG_CODE == 0
  code_block();
#elif LOAD == 2
  code_pieces(0, 256);
#endif
}
#endif

int main(void) {
  fpid_init(&pid);
  sched_i = 0;
  sched_left = (uint32_t)sched_len[0];

  HIL->MON_CTRL = 1;                                         // the monitor takes the UART TX line
  release = hil_mtime() + START_DELAY;
  // main never reads these again, so without a barrier GCC drops the stores
  // and the ISR reads zeros
  asm volatile("" ::: "memory");
  hil_set_mtimecmp(release);

#if LOAD == 3
  neorv32_gptmr_setup(CLK_PRSC_2);
  neorv32_gptmr_configure(0, 0, GPTMR_THR, 1);               // continuous
  neorv32_gptmr_enable_single(0);
#define BG_MIE (1u << GPTMR_FIRQ_ENABLE)
#else
#define BG_MIE 0u
#endif

#if TRIG == 0
  neorv32_cpu_csr_write(CSR_MTVEC, (uint32_t)&ctrl_vectors | 1u);   // vectored
  neorv32_cpu_csr_write(CSR_MIE, (1u << CSR_MIE_MTIE) | BG_MIE);
  neorv32_cpu_csr_set(CSR_MSTATUS, 1u << CSR_MSTATUS_MIE);
  while (1) {
    load_sweep();
  }
#else
  // the timer interrupt is never enabled: MTIP is only read from mip
  neorv32_cpu_csr_write(CSR_MTVEC, (uint32_t)&ctrl_vectors | 1u);
  neorv32_cpu_csr_write(CSR_MIE, BG_MIE);
  if (BG_MIE) {
    neorv32_cpu_csr_set(CSR_MSTATUS, 1u << CSR_MSTATUS_MIE);
  }
#if LOAD >= 1
  uint32_t w = 0;                  // word index of the next unit's thrash line: 0, 16, ..., 4080
#endif
  while (1) {
#if LOAD >= 1
    // one chunk: CHUNK units, the same loop as a sweep of interrupt mode (one
    // read-modify-write per 64 B line)
    uint32_t first = w, end = w + CHUNK * (LINE_BYTES / 4);
#if 256 % CHUNK != 0
    if (end > THRASH_BYTES / 4) {  // the chunk runs past line 255: finish the sweep, go on from line 0
      for (; w != THRASH_BYTES / 4; w += LINE_BYTES / 4) {
        thrash[w] = thrash[w] + 1;
      }
      w = 0;
      end -= THRASH_BYTES / 4;
    }
#endif
    for (; w != end; w += LINE_BYTES / 4) {
      thrash[w] = thrash[w] + 1;
    }
#if LOAD == 2
    code_pieces(first / (LINE_BYTES / 4), CHUNK);   // the same units' code, wrapping with them
#endif
    (void)first;
    w &= THRASH_BYTES / 4 - 1;     // 4096 -> 0 (w is in [16, 4096])
#endif
    if (neorv32_cpu_csr_read(CSR_MIP) & (1u << CSR_MIP_MTIP)) {       // the poll
      uint32_t s;
      asm volatile("csrrci %0, mstatus, 8" : "=r"(s) : : "memory");    // critical section begins
      HIL->START = 1;                                                  // START
      control_step();                                                  // ... MTIMECMP (clears MTIP)
      if (s & (1u << CSR_MSTATUS_MIE)) {
        asm volatile("csrsi mstatus, 8" : : : "memory");               // ends; a pending GPTMR
      }                                                                // request is taken here
    }
  }
#endif
  return 0;
}
