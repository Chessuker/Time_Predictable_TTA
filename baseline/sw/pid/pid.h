// FixedPID of host/plant_model/model.py in C, for the soft-core baseline.
//
// The arithmetic is the TTA core's: 32-bit two's complement, products keep the
// low 32 bits (isa.md MUL), shifts are arithmetic. In the ranges the plant
// model reaches every value fits in 32 bits (docs/design_decisions.md, "no
// divide"), so this equals FixedPID; tests/test_baseline_pid.py checks it.
#ifndef PID_H
#define PID_H

#include <stdint.h>

typedef struct {
  int32_t acc;      // integrator, clamped to +-PID_ACC_MAX
  int32_t prev;     // encoder of the previous step
  int32_t started;  // 0 until the first step (then prev = that step's encoder)
} fpid_state_t;

void fpid_init(fpid_state_t *s);

// One control step: setpoint and encoder in counts, returns pwm_cmd.
int32_t fpid_step(fpid_state_t *s, int32_t ref, int32_t enc);

#endif
