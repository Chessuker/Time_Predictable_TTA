`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/06/2026 07:17:12 PM
// Design Name: 
// Module Name: tta_core.sv
// Project Name: Time_Predictable_TTA
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments:
// 
//////////////////////////////////////////////////////////////////////////////////

// Time-predictable TTA core: one move per cycle, three-stage pipeline.
//
//   F  fetch_pc addresses the code SRAM (synchronous read)
//   D  the SRAM output word is decoded into the X registers
//   X  the move executes: src mux -> bus value -> dst write, all in cycle c
//
// The timing constants of timing_model.md fall out of this structure:
//   R = 2  address 0 at cycle 0, word in D at 1, executes at 2
//   P = 2  a taken jump writes fetch_pc (registered), and the two words already
//          fetched are dropped, so the target executes at c + 3
//   D = 1  fu_tmr.hold freezes F, D and X while now <= target
//   H = 2  a trap redirects exactly like a taken jump
//
// arch_pc always holds the PC of the next move in program order that has not
// executed (the ISS's self.pc), which is trap.epc by definition (R6).
//
// The rv_*/tr_*/ht_* outputs report every retired move, trap and halt, in the
// spirit of RISC-V RVFI. The lockstep testbench turns them into the trace of
// toolchain_formats.md sec. 6; synthesis leaves them unconnected.
module tta_core import tta_pkg::*; #(
  parameter int    IMEM_W     = IMEM_WORDS,
  parameter int    DMEM_W     = DMEM_WORDS,
  parameter string IMEM_INIT  = "",
  parameter string DMEM_INIT  = ""
) (
  input  logic        clk,
  input  logic        rst,
  // IO (isa.md sec. 3)
  input  logic [31:0] io_encoder,
  input  logic [31:0] io_din,          // already synchronised by the board (isa.md sec. 3)
  output logic [31:0] io_pwm_cmd,
  // telemetry towards the UART
  output logic        telem_tx_start,
  output logic [31:0] telem_tx_data,
  output logic        halted,
  // retire/trap/halt report
  output logic [63:0] dbg_now,
  output logic        rv_valid,
  output logic [31:0] rv_pc,
  output logic [31:0] rv_word,
  output logic        rv_imm,
  output logic [7:0]  rv_src,
  output logic [7:0]  rv_dst,
  output logic [31:0] rv_value,
  output logic        tr_valid,
  output logic [31:0] tr_epc,
  output logic [2:0]  tr_cause,
  output logic        ht_valid,
  output logic [1:0]  ht_reason      // 0 halt, 1 nohandler, 2 double
);
  localparam int IAW = $clog2(IMEM_W);
  localparam int DAW = $clog2(DMEM_W);

  // ---------------------------------------------------------------- state
  logic [63:0] now;
  logic [31:0] fetch_pc, ir_pc, arch_pc;
  logic        ir_valid;
  logic [31:0] ir;

  // One-hot source select, decoded in D so X does no port-ID decoding
  // (this shortens every path that starts at the source mux).
  typedef enum int {
    S_IMM, S_REG, S_ALU, S_MUL, S_EQ, S_LT, S_LTU, S_LINK, S_MEM,
    S_ELAPSED, S_FLAGS, S_HANDLER, S_CAUSE, S_EPC, S_ENC, S_DIN, S_DROPS, S_N
  } src_sel_e;

  logic        x_valid, x_imm, x_illegal;
  logic [31:0] x_pc, x_word, x_immval;
  logic [7:0]  x_dst, x_src;
  logic [S_N-1:0] x_sel;

  logic [31:0] regs [16];
  logic [31:0] alu_a, alu_out, mul_a, cmp_a;
  logic [3:0]  alu_op;
  logic        cmp_eq, cmp_lt, cmp_ltu;
  logic [31:0] cond, link, mem_data_in;
  logic [31:0] handler, epc;
  logic [2:0]  cause;
  logic        trapped;

  // ---------------------------------------------------------------- F: code memory
  logic advance, flush;
  logic [31:0] redirect;

  // The read enable does not wait for flush: on a redirect the word read
  // this cycle is dropped through ir_valid anyway, and keeping the bus value
  // (jump target, taken?) off the enable is a timing win.
  tta_sram_1r1w #(.WORDS(IMEM_W), .INIT_FILE(IMEM_INIT), .SIM_PLUSARG("code")) u_imem (
    .clk, .rst,
    .re(advance), .raddr(fetch_pc[IAW-1:0]), .rdata(ir),
    .we(1'b0), .waddr('0), .wdata('0)
  );

  // ---------------------------------------------------------------- D: decode
  logic [7:0]  d_dst, d_src;
  logic        d_imm, d_illegal;
  logic [31:0] d_immval;
  assign d_dst    = ir[31:24];
  assign d_imm    = ir[23];
  assign d_src    = ir[7:0];
  assign d_immval = {{9{ir[22]}}, ir[22:0]};
  assign d_illegal = !port_writable(d_dst)
                   || (!d_imm && (ir[22:8] != '0 || !port_readable(d_src)));

  logic [S_N-1:0] d_sel;
  always_comb begin
    d_sel = '0;
    if (d_imm)                   d_sel[S_IMM] = 1'b1;
    else if (d_src[7:4] == 4'h1) d_sel[S_REG] = 1'b1;
    else unique case (d_src)
      P_ALU_OUT:      d_sel[S_ALU]     = 1'b1;
      P_MUL_OUT:      d_sel[S_MUL]     = 1'b1;
      P_CMP_EQ:       d_sel[S_EQ]      = 1'b1;
      P_CMP_LT:       d_sel[S_LT]      = 1'b1;
      P_CMP_LTU:      d_sel[S_LTU]     = 1'b1;
      P_PC_LINK:      d_sel[S_LINK]    = 1'b1;
      P_MEM_DATA_OUT: d_sel[S_MEM]     = 1'b1;
      P_TMR_ELAPSED:  d_sel[S_ELAPSED] = 1'b1;
      P_TMR_FLAGS:    d_sel[S_FLAGS]   = 1'b1;
      P_TRAP_HANDLER: d_sel[S_HANDLER] = 1'b1;
      P_TRAP_CAUSE:   d_sel[S_CAUSE]   = 1'b1;
      P_TRAP_EPC:     d_sel[S_EPC]     = 1'b1;
      P_IO_ENCODER:   d_sel[S_ENC]     = 1'b1;
      P_IO_DIN:       d_sel[S_DIN]     = 1'b1;
      P_TELEM_DROPS:  d_sel[S_DROPS]   = 1'b1;
      default: ;                         // illegal src: traps, value unused
    endcase
  end

  // ---------------------------------------------------------------- X: source read
  logic        hold, dl_hit, tmr_late, tmr_armed;
  logic [31:0] tmr_elapsed, mul_out, mem_data_out, drops;
  logic [31:0] value;

  // AND-OR of the one-hot selected source
  always_comb begin
    value = '0;
    if (x_sel[S_IMM])     value |= x_immval;
    if (x_sel[S_REG])     value |= regs[x_src[3:0]];
    if (x_sel[S_ALU])     value |= alu_out;
    if (x_sel[S_MUL])     value |= mul_out;
    if (x_sel[S_EQ])      value |= {31'b0, cmp_eq};
    if (x_sel[S_LT])      value |= {31'b0, cmp_lt};
    if (x_sel[S_LTU])     value |= {31'b0, cmp_ltu};
    if (x_sel[S_LINK])    value |= link;
    if (x_sel[S_MEM])     value |= mem_data_out;
    if (x_sel[S_ELAPSED]) value |= tmr_elapsed;
    if (x_sel[S_FLAGS])   value |= {29'b0, trapped, tmr_armed, tmr_late};
    if (x_sel[S_HANDLER]) value |= handler;
    if (x_sel[S_CAUSE])   value |= {29'b0, cause};
    if (x_sel[S_EPC])     value |= epc;
    if (x_sel[S_ENC])     value |= io_encoder;
    if (x_sel[S_DIN])     value |= io_din;
    if (x_sel[S_DROPS])   value |= drops;
  end

  // ---------------------------------------------------------------- X: decision (R6 order)
  logic exec_ok, exec_base, taken, trap_dl, trap_enc, trap_val, trap, exec, halt_now;
  logic bad_alu_op, bad_mem, bad_jump;
  logic [2:0] trap_cause;

  assign exec_ok    = x_valid && !hold && !halted;
  assign taken      = x_dst == P_PC_T_JUMP || x_dst == P_PC_T_CALL
                   || (x_dst == P_PC_T_JZ  && cond == '0)
                   || (x_dst == P_PC_T_JNZ && cond != '0);
  assign bad_alu_op = x_dst == P_ALU_OP && value > 32'(ALU_OP_MAX);
  assign bad_mem    = (x_dst == P_MEM_T_LOAD || x_dst == P_MEM_T_STORE) && value >= 32'(DMEM_W);
  assign bad_jump   = taken && value >= 32'(IMEM_W);

  assign trap_dl  = dl_hit && !halted;                           // step 2: deadline wins
  assign trap_enc = !trap_dl && exec_ok && x_illegal;            // step 3: legality
  assign trap_val = !trap_dl && exec_ok && !x_illegal && (bad_alu_op || bad_mem || bad_jump);
  assign trap     = trap_dl || trap_enc || trap_val;
  assign exec     = exec_ok && !trap;

  // exec_base: the move executes unless its own bus value is out of range.
  // Each bad_* can only be true for its own dst (alu.op, mem.t_*, taken jumps),
  // so every other dst enables on exec_base, which does not wait for the
  // value compares. This keeps src mux -> compare -> trap off the write-enable
  // path of every FU (it was the critical path at 100 MHz).
  assign exec_base = exec_ok && !trap_dl && !x_illegal;

  always_comb begin
    if      (trap_dl)    trap_cause = CAUSE_DEADLINE;
    else if (trap_enc)   trap_cause = CAUSE_ILLEGAL_PORT;
    else if (bad_alu_op) trap_cause = CAUSE_ILLEGAL_ALU_OP;
    else if (bad_mem)    trap_cause = CAUSE_DATA_ADDR;
    else                 trap_cause = CAUSE_JUMP_TARGET;
  end

  logic do_halt, trap_halts;
  assign do_halt    = exec_base && x_dst == P_TRAP_T_HALT;
  assign trap_halts = trap && (trapped || handler == '0);
  assign halt_now   = do_halt || trap_halts;

  assign flush    = (exec && taken) || (trap && !trap_halts);
  assign redirect = trap ? handler : value;
  assign advance  = !hold && !halted;

  // ---------------------------------------------------------------- function units
  logic [31:0] alu_y;
  fu_alu u_alu (.op(alu_op), .a(alu_a), .b(value), .y(alu_y));

  fu_mul u_mul (.clk, .rst, .trig(exec_base && x_dst == P_MUL_T_B), .a(mul_a), .b(value), .out(mul_out));

  tta_sram_1r1w #(.WORDS(DMEM_W), .INIT_FILE(DMEM_INIT), .SIM_PLUSARG("data")) u_dmem (
    .clk, .rst,
    .re(exec_base && x_dst == P_MEM_T_LOAD  && !bad_mem), .raddr(value[DAW-1:0]), .rdata(mem_data_out),
    .we(exec_base && x_dst == P_MEM_T_STORE && !bad_mem), .waddr(value[DAW-1:0]), .wdata(mem_data_in)
  );

  fu_tmr u_tmr (
    .clk, .rst,
    .do_sync   (exec_base && x_dst == P_TMR_T_SYNC),
    .do_advance(exec_base && x_dst == P_TMR_T_ADVANCE),
    .do_wait   (exec_base && x_dst == P_TMR_T_WAIT),
    .do_arm    (exec_base && x_dst == P_TMR_T_ARM),
    .do_clear  (exec_base && x_dst == P_TMR_T_CLEAR),
    .v(value), .trap_take(trap),
    .hold, .dl_hit, .elapsed(tmr_elapsed), .late(tmr_late), .armed(tmr_armed)
  );

  fu_telem #(.DEPTH(TELEM_FIFO_WORDS), .CYCLES_PER_WORD(TELEM_CYCLES_PER_WORD)) u_telem (
    .clk, .rst, .push(exec_base && x_dst == P_TELEM_T_PUSH), .data(value),
    .drops, .tx_start(telem_tx_start), .tx_data(telem_tx_data)
  );

  // ---------------------------------------------------------------- sequential
  always_ff @(posedge clk) begin
    if (rst) begin
      now <= '0;
      fetch_pc <= '0; ir_pc <= '0; ir_valid <= 1'b0; arch_pc <= '0;
      x_valid <= 1'b0; x_imm <= 1'b0; x_illegal <= 1'b0;
      x_pc <= '0; x_word <= '0; x_immval <= '0; x_dst <= '0; x_src <= '0; x_sel <= '0;
      for (int i = 0; i < 16; i++) regs[i] <= '0;
      alu_a <= '0; alu_op <= '0; alu_out <= '0; mul_a <= '0; cmp_a <= '0;
      cmp_eq <= 1'b0; cmp_lt <= 1'b0; cmp_ltu <= 1'b0;
      cond <= '0; link <= '0; mem_data_in <= '0;
      handler <= '0; epc <= '0; cause <= '0; trapped <= 1'b0;
      io_pwm_cmd <= '0; halted <= 1'b0;
    end else begin
      now <= now + 64'd1;

      // ---- pipeline
      if (flush) begin
        fetch_pc <= redirect;
        ir_valid <= 1'b0;
        x_valid  <= 1'b0;
      end else if (advance) begin
        fetch_pc  <= fetch_pc + 32'd1;
        ir_valid  <= 1'b1;
        ir_pc     <= fetch_pc;
        x_valid   <= ir_valid;
        x_pc      <= ir_pc;
        x_word    <= ir;
        x_dst     <= d_dst;
        x_src     <= d_src;
        x_imm     <= d_imm;
        x_immval  <= d_immval;
        x_sel     <= d_sel;
        x_illegal <= d_illegal;
      end

      // ---- architectural PC and trap state
      if (exec)       arch_pc <= taken ? value : x_pc + 32'd1;
      if (trap) begin
        epc     <= arch_pc;
        cause   <= trap_cause;
        trapped <= 1'b1;
        if (!trap_halts) arch_pc <= handler;
      end
      if (halt_now) halted <= 1'b1;

      // ---- dst writes (exec_base: see the note at its definition)
      if (exec_base) begin
        if (x_dst[7:4] == 4'h1) regs[x_dst[3:0]] <= value;
        unique case (x_dst)
          P_ALU_A:        alu_a <= value;
          P_ALU_OP:       if (!bad_alu_op) alu_op <= value[3:0];
          P_ALU_T_B:      alu_out <= alu_y;
          P_MUL_A:        mul_a <= value;
          P_CMP_A:        cmp_a <= value;
          P_CMP_T_B: begin
            cmp_eq  <= cmp_a == value;
            cmp_lt  <= $signed(cmp_a) < $signed(value);
            cmp_ltu <= cmp_a < value;
          end
          P_PC_COND:      cond <= value;
          P_PC_T_CALL:    if (!bad_jump) link <= x_pc + 32'd1;
          P_PC_LINK:      link <= value;
          P_MEM_DATA_IN:  mem_data_in <= value;
          P_TRAP_HANDLER: handler <= value;
          P_IO_PWM_CMD:   io_pwm_cmd <= value;
          default: ;
        endcase
      end
    end
  end

  // ---------------------------------------------------------------- report
  assign dbg_now   = now;
  assign rv_valid  = exec;
  assign rv_pc     = x_pc;
  assign rv_word   = x_word;
  assign rv_imm    = x_imm;
  assign rv_src    = x_src;
  assign rv_dst    = x_dst;
  assign rv_value  = value;
  assign tr_valid  = trap;
  assign tr_epc    = arch_pc;
  assign tr_cause  = trap_cause;
  assign ht_valid  = halt_now;
  assign ht_reason = do_halt ? 2'd0 : (trapped ? 2'd2 : 2'd1);
endmodule
