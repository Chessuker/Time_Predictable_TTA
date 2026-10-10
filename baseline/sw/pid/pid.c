// FixedPID in C (see pid.h). Same steps, in the same order, as
// host/plant_model/model.py FixedPID.step and programs/control.tta.
#include "pid.h"
#include "pid_const.h"

// 32-bit wrap-around arithmetic without signed-overflow undefined behaviour
static inline int32_t add32(int32_t a, int32_t b) { return (int32_t)((uint32_t)a + (uint32_t)b); }
static inline int32_t sub32(int32_t a, int32_t b) { return (int32_t)((uint32_t)a - (uint32_t)b); }
static inline int32_t mul32(int32_t a, int32_t b) { return (int32_t)((uint32_t)a * (uint32_t)b); }

// arithmetic shift right (floor), written out so it does not rely on how the
// compiler shifts negative numbers
static inline int32_t sra32(int32_t a, int n) {
  return a >= 0 ? (int32_t)((uint32_t)a >> n) : (int32_t)~(~(uint32_t)a >> n);
}

static inline int32_t clamp32(int32_t x, int32_t lo, int32_t hi) {
  return x < lo ? lo : (x > hi ? hi : x);
}

void fpid_init(fpid_state_t *s) {
  s->acc = 0;
  s->prev = 0;
  s->started = 0;
}

int32_t fpid_step(fpid_state_t *s, int32_t ref, int32_t enc) {
  if (!s->started) {                 // FixedPID: prev = enc on the first step
    s->prev = enc;
    s->started = 1;
  }
  int32_t e  = clamp32(sub32(ref, enc), -PID_E_CLAMP, PID_E_CLAMP);
  int32_t de = sub32(enc, s->prev);  // derivative on the measurement
  s->prev = enc;
  int32_t p  = mul32(PID_KPQ, e);
  int32_t d  = mul32(PID_KDQ, de);
  int32_t sum = add32(sub32(p, d), s->acc);
  int32_t u  = sra32(add32(sum, 1 << (PID_SH - 1)), PID_SH);   // round half up
  int sat = (u > PID_U_MAX) || (u < -PID_U_MAX);
  if (sat) {
    u = clamp32(u, -PID_U_MAX, PID_U_MAX);
  }
  // anti-windup: integrate unless saturated with e pushing the same way
  int same_way = (e > 0 && u > 0) || (e < 0 && u < 0);
  if (!(sat && same_way)) {
    s->acc = clamp32(add32(s->acc, mul32(PID_KIQ, e)), -PID_ACC_MAX, PID_ACC_MAX);
  }
  return u;
}
