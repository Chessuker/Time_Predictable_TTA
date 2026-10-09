// Formal harness for tta_core (Phase 3).
//
// The code and data memories are replaced by tta_sram_1r1w_any.sv, so the
// core runs an arbitrary instruction stream. Next to it runs a reference
// model of the timing rules written the way timing_model.md states them:
// absolute 64-bit times (now, anchor, deadline, target) and "the next move
// is due at cycle exp_at with PC exp_pc". The model only looks at what the
// core reports (rv_*, tr_*, ht_*), never at its internals.
//
// Assertions, in spec terms:
//   A1 now counts every cycle from 0                          (sec. 1)
//   A2 the next move runs exactly when due, at the right PC:  (R1, R4, R5, R6, R)
//      nothing retires before exp_at, something retires or
//      traps at exp_at, and trap.epc is the move that was due
//   A3 deadline trap iff armed and now >= deadline            (R6)
//   A4 tmr.elapsed and tmr.flags read the model's values      (R5)
//   A5 halt: no event after it, and it happens exactly when   (isa.md sec. 9)
//      t_halt retires or a trap finds handler = 0 / trapped
//   A6 FU outputs change only after their own trigger         (R2)
// Invariants (I*) tie the model to the core's state; k-induction needs them.
//
// Assumptions are the program rules the assembler already enforces:
//   S3  t_advance never executes while armed (timing_model.md sec. 4)
//   R7  now does not wrap (bounded at 2^62, i.e. ~1,461 years at 100 MHz)
module tta_formal import tta_pkg::*; (
  input logic clk
);
  // ---------------------------------------------------------------- DUT
  logic rst = 1'b1;                      // reset in the first cycle only
  always_ff @(posedge clk) rst <= 1'b0;

  (* anyseq *) logic [31:0] io_encoder;

  logic [31:0] io_pwm_cmd, telem_tx_data, rv_pc, rv_word, rv_value, tr_epc;
  logic        telem_tx_start, halted, rv_valid, rv_imm, tr_valid, ht_valid;
  logic [63:0] dbg_now;
  logic [7:0]  rv_src, rv_dst;
  logic [2:0]  tr_cause;
  logic [1:0]  ht_reason;

  tta_core u_core (
    .clk, .rst, .io_encoder, .io_pwm_cmd, .telem_tx_start, .telem_tx_data, .halted,
    .dbg_now, .rv_valid, .rv_pc, .rv_word, .rv_imm, .rv_src, .rv_dst, .rv_value,
    .tr_valid, .tr_epc, .tr_cause, .ht_valid, .ht_reason
  );

  // ---------------------------------------------------------------- reference model
  logic [63:0] now_r, anchor_r, deadline_r, target_r, exp_at;
  logic [31:0] exp_pc, handler_r, cond_r;
  logic        armed_r, late_r, trapped_r, halted_r, stall_r, exp_on;

  logic        ret, trp, is_timing, taken;
  logic [63:0] v64, tgt, wake;
  logic        dl_due, hold_ref, halts;
  logic [63:0] el_ref;
  logic [31:0] elapsed_ref;

  assign ret       = rv_valid;
  assign trp       = tr_valid;
  assign v64       = {32'b0, rv_value};
  assign tgt       = anchor_r + v64;                         // target T / new deadline
  assign wake      = ((now_r > tgt) ? now_r : tgt) + 64'(T_D); // max(c, T) + D
  assign is_timing = rv_dst == P_TMR_T_ADVANCE || rv_dst == P_TMR_T_WAIT;
  assign taken     = rv_dst == P_PC_T_JUMP || rv_dst == P_PC_T_CALL
                  || (rv_dst == P_PC_T_JZ  && cond_r == '0)
                  || (rv_dst == P_PC_T_JNZ && cond_r != '0);
  assign dl_due    = !halted_r && armed_r && now_r >= deadline_r;
  assign hold_ref  = stall_r && now_r <= target_r;
  assign halts     = trapped_r || handler_r == '0;
  assign el_ref    = now_r - anchor_r;
  assign elapsed_ref = el_ref[63:32] != '0 ? 32'hFFFF_FFFF : el_ref[31:0];

  always_ff @(posedge clk) begin
    if (rst) begin
      now_r <= '0; anchor_r <= '0; deadline_r <= '0; target_r <= '0;
      armed_r <= 1'b0; late_r <= 1'b0; trapped_r <= 1'b0; halted_r <= 1'b0; stall_r <= 1'b0;
      handler_r <= '0; cond_r <= '0;
      exp_on <= 1'b1; exp_at <= 64'(T_R); exp_pc <= '0;      // reset = jump to 0 at cycle -1
    end else begin
      now_r <= now_r + 64'd1;
      if (trp) begin
        armed_r   <= 1'b0;
        trapped_r <= 1'b1;
        stall_r   <= 1'b0;
        if (halts) begin
          halted_r <= 1'b1;
          exp_on   <= 1'b0;
        end else begin
          exp_at <= now_r + 64'(1 + T_H);
          exp_pc <= handler_r;
        end
      end else if (ret) begin
        exp_at <= now_r + 64'd1;
        exp_pc <= rv_pc + 32'd1;
        unique case (rv_dst)
          P_TMR_T_SYNC:    anchor_r <= now_r;
          P_TMR_T_ARM:     begin deadline_r <= tgt; armed_r <= 1'b1; end
          P_TMR_T_CLEAR:   armed_r <= 1'b0;
          P_TRAP_T_HALT:   begin halted_r <= 1'b1; exp_on <= 1'b0; end
          P_PC_COND:       cond_r <= rv_value;
          P_TRAP_HANDLER:  handler_r <= rv_value;
          default: ;
        endcase
        if (is_timing) begin
          if (rv_dst == P_TMR_T_ADVANCE) anchor_r <= tgt;
          late_r   <= now_r > tgt;
          target_r <= tgt;
          stall_r  <= now_r < tgt;
          exp_at   <= wake;
        end
        if (taken) begin
          exp_at <= now_r + 64'(1 + T_P);
          exp_pc <= rv_value;
        end
      end
    end
  end

  // ---------------------------------------------------------------- assumptions
  always_comb begin
    if (!rst) begin
      assume (!(ret && rv_dst == P_TMR_T_ADVANCE && armed_r));   // S3
      assume (now_r[63:62] == 2'b00);                            // R7
    end
  end

  // ---------------------------------------------------------------- A1..A5
  always_comb begin
    if (!rst) begin
      assert (dbg_now == now_r);                                             // A1

      if (!exp_on || now_r < exp_at)                                         // A2: not before it is due
        assert (!ret && !(trp && tr_cause != CAUSE_DEADLINE));
      if (exp_on && now_r == exp_at) assert (ret || trp);                    // A2: exactly when due
      assert (!exp_on || now_r <= exp_at);
      if (ret) assert (rv_pc == exp_pc);                                     // A2: right PC
      if (trp) assert (tr_epc == exp_pc);                                    // A2: trap.epc (R6)

      assert ((trp && tr_cause == CAUSE_DEADLINE) == dl_due);                // A3

      if (ret && !rv_imm && rv_src == P_TMR_ELAPSED)                         // A4
        assert (rv_value == elapsed_ref);
      if (ret && !rv_imm && rv_src == P_TMR_FLAGS)
        assert (rv_value == {29'b0, trapped_r, armed_r, late_r});

      assert (halted == halted_r);                                           // A5
      if (halted_r) assert (!ret && !trp && !ht_valid);
      assert (ht_valid == ((ret && rv_dst == P_TRAP_T_HALT) || (trp && halts)));
    end
  end

  // ---------------------------------------------------------------- A6: FU outputs hold (R2)
  logic        past_ok;
  logic        trig_alu, trig_cmp, trig_mul, trig_mul2, trig_load, trig_link;
  logic [31:0] alu_out_q, mul_out_q, mem_out_q, link_q;
  logic [2:0]  cmp_q;

  always_ff @(posedge clk) begin
    past_ok   <= !rst;
    trig_alu  <= ret && rv_dst == P_ALU_T_B;
    trig_cmp  <= ret && rv_dst == P_CMP_T_B;
    trig_mul  <= ret && rv_dst == P_MUL_T_B;
    trig_mul2 <= trig_mul;
    trig_load <= ret && rv_dst == P_MEM_T_LOAD;
    trig_link <= ret && (rv_dst == P_PC_T_CALL || rv_dst == P_PC_LINK);
    alu_out_q <= u_core.alu_out;
    mul_out_q <= u_core.mul_out;
    mem_out_q <= u_core.mem_data_out;
    link_q    <= u_core.link;
    cmp_q     <= {u_core.cmp_eq, u_core.cmp_lt, u_core.cmp_ltu};
  end

  always_comb begin
    if (past_ok && !rst) begin
      if (u_core.alu_out != alu_out_q)    assert (trig_alu);
      if ({u_core.cmp_eq, u_core.cmp_lt, u_core.cmp_ltu} != cmp_q) assert (trig_cmp);
      if (u_core.mul_out != mul_out_q)    assert (trig_mul2);              // L = 2
      if (u_core.mem_data_out != mem_out_q) assert (trig_load);
      if (u_core.link != link_q)          assert (trig_link);
    end
  end

  // ---------------------------------------------------------------- invariants for k-induction
  localparam int SEL_IMM = 0, SEL_ELAPSED = 9, SEL_FLAGS = 10;   // S_IMM, S_ELAPSED, S_FLAGS
  always_comb begin
    if (!rst) begin
      assert (u_core.now == now_r);
      assert (u_core.u_tmr.el == el_ref);
      assert (u_core.u_tmr.hold   == hold_ref);
      assert (u_core.u_tmr.armed  == armed_r);
      assert (u_core.u_tmr.late   == late_r);
      assert (u_core.u_tmr.dl_hit == (armed_r && now_r >= deadline_r));
      assert (u_core.trapped == trapped_r);
      assert (u_core.handler == handler_r);
      assert (u_core.cond    == cond_r);
      if (exp_on) assert (u_core.arch_pc == exp_pc);
      if (hold_ref) begin
        assert (target_r - now_r < 64'h1_0000_0000);
        assert (u_core.u_tmr.wrem == 32'(target_r - now_r));
        assert (exp_at == target_r + 64'(T_D));
      end
      if (armed_r && now_r < deadline_r) begin
        assert (deadline_r - now_r < 64'h1_0000_0000);
        assert (u_core.u_tmr.rem == 32'(deadline_r - now_r));
      end
      // anchor is ahead of now only inside a t_advance stall, where it is the target
      assert (anchor_r <= now_r || (hold_ref && anchor_r == target_r));
      assert (anchor_r <= now_r || !armed_r);                    // S3 holds through the stall

      // pipeline fill against the due cycle: a redirect at c makes the move
      // due at c + 3, and F, D, X fill one stage per cycle until then
      assert (exp_on == !halted_r);
      if (exp_on) begin
        assert (exp_at <= now_r + 64'd2 || hold_ref);
        if (exp_at == now_r + 64'd2 && !hold_ref) begin
          assert (!u_core.x_valid && !u_core.ir_valid);
          assert (u_core.fetch_pc == exp_pc);
        end
        if (exp_at == now_r + 64'd1 && !hold_ref) begin
          assert (!u_core.x_valid && u_core.ir_valid);
          assert (u_core.ir_pc == exp_pc && u_core.fetch_pc == exp_pc + 32'd1);
        end
        if (exp_at == now_r || hold_ref) begin
          assert (u_core.x_valid && u_core.ir_valid);
          assert (u_core.x_pc == exp_pc && u_core.ir_pc == exp_pc + 32'd1);
          assert (u_core.fetch_pc == exp_pc + 32'd2);
        end
      end

      // the X fields come from one word; A4 relies on the source select
      // (bit numbers of tta_core's src_sel_e; slang cannot read the enum
      // through the hierarchy)
      if (u_core.x_valid) begin
        assert (u_core.x_sel[SEL_IMM] == u_core.x_imm);
        assert (u_core.x_sel[SEL_ELAPSED] == (!u_core.x_imm && u_core.x_src == P_TMR_ELAPSED));
        assert (u_core.x_sel[SEL_FLAGS]   == (!u_core.x_imm && u_core.x_src == P_TMR_FLAGS));
        if (u_core.x_sel[SEL_ELAPSED] || u_core.x_sel[SEL_FLAGS])
          assert ($onehot(u_core.x_sel));
      end
    end
  end

  // ---------------------------------------------------------------- covers
  logic [63:0] last_ret_at;
  logic        last_was_timing;
  always_ff @(posedge clk) begin
    if (rst) begin
      last_ret_at <= '0; last_was_timing <= 1'b0;
    end else if (ret) begin
      last_ret_at <= now_r; last_was_timing <= is_timing;
    end
  end

  always_comb begin
    if (!rst) begin
      cover (trp && tr_cause == CAUSE_DEADLINE);                     // C1 deadline trap
      cover (trp && tr_cause == CAUSE_DEADLINE && hold_ref);         // C2 trap inside a t_wait stall
      cover (ret && last_was_timing && now_r >= last_ret_at + 3);    // C3 a stall of 2+ cycles, then wake
      cover (u_core.u_tmr.late);                                     // C4 late
      cover (ret && rv_dst == P_TMR_T_CLEAR && armed_r && now_r + 1 == deadline_r);  // C5 clear just in time
      cover (ret && trapped_r && rv_pc == handler_r);                // C6 first move of a handler
    end
  end
endmodule
