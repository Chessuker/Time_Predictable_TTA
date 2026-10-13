// Trap entry of the baseline control program: mtvec in VECTORED mode, so the
// timer interrupt reaches its own entry without software dispatch, and the
// first store there is START (xbus_hil), before any register is saved.
// Uncompressed, so every vector slot is one 4-byte jump.
  .section .text.ctrl_vectors, "ax"
  .option push
  .option norvc
  .balign 128
  .global ctrl_vectors
  .type ctrl_vectors, @function
ctrl_vectors:
  j ctrl_fault            // 0: exceptions
  .rept 6
  j ctrl_fault            // 1..6
  .endr
  j ctrl_mti_entry        // 7: machine timer (xbus_hil MTIP)
  .rept 20
  j ctrl_fault            // 8..27
  .endr
  j ctrl_gptmr_isr        // 28: FIRQ 12, GPTMR (background load L2)
  .rept 3
  j ctrl_fault            // 29..31
  .endr
  .size ctrl_vectors, . - ctrl_vectors

  .type ctrl_mti_entry, @function
ctrl_mti_entry:
  csrw mscratch, t0
  li   t0, 0xF0000000     // xbus_hil
  sw   zero, 0x1C(t0)     // START
  csrr t0, mscratch
  j    ctrl_isr           // C, interrupt("machine"): saves registers, ends in mret
  .size ctrl_mti_entry, . - ctrl_mti_entry
  .option pop
