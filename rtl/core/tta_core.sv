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

  logic        x_valid, x_imm, x_illegal;
  logic [31:0] x_pc, x_word, x_immval;
  logic [7:0]  x_dst, x_src;

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

  tta_sram_1r1w #(.WORDS(IMEM_W), .INIT_FILE(IMEM_INIT), .SIM_PLUSARG("code")) u_imem (
    .clk, .rst,
    .re(advance && !flush), .raddr(fetch_pc[IAW-1:0]), .rdata(ir),
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

  // ---------------------------------------------------------------- X: source read
  logic        hold, dl_hit, tmr_late, tmr_armed;
  logic [31:0] tmr_elapsed, mul_out, mem_data_out, drops;
  logic [31:0] src_val, value;

  always_comb begin
    src_val = '0;
    if (x_src[7:4] == 4'h1) src_val = regs[x_src[3:0]];
    else unique case (x_src)
      P_ALU_OUT:      src_val = alu_out;
      P_MUL_OUT:      src_val = mul_out;
      P_CMP_EQ:       src_val = {31'b0, cmp_eq};
      P_CMP_LT:       src_val = {31'b0, cmp_lt};
      P_CMP_LTU:      src_val = {31'b0, cmp_ltu};
      P_PC_LINK:      src_val = link;
      P_MEM_DATA_OUT: src_val = mem_data_out;
      P_TMR_ELAPSED:  src_val = tmr_elapsed;
      P_TMR_FLAGS:    src_val = {29'b0, trapped, tmr_armed, tmr_late};
      P_TRAP_HANDLER: src_val = handler;
      P_TRAP_CAUSE:   src_val = {29'b0, cause};
      P_TRAP_EPC:     src_val = epc;
      P_IO_ENCODER:   src_val = io_encoder;
      P_TELEM_DROPS:  src_val = drops;
      default:        src_val = '0;
    endcase
  end
  assign value = x_imm ? x_immval : src_val;

  // ---------------------------------------------------------------- X: decision (R6 order)
  logic exec_ok, taken, trap_dl, trap_enc, trap_val, trap, exec, halt_now;
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

  always_comb begin
    if      (trap_dl)    trap_cause = CAUSE_DEADLINE;
    else if (trap_enc)   trap_cause = CAUSE_ILLEGAL_PORT;
    else if (bad_alu_op) trap_cause = CAUSE_ILLEGAL_ALU_OP;
    else if (bad_mem)    trap_cause = CAUSE_DATA_ADDR;
    else                 trap_cause = CAUSE_JUMP_TARGET;
  end

  logic do_halt, trap_halts;
  assign do_halt    = exec && x_dst == P_TRAP_T_HALT;
  assign trap_halts = trap && (trapped || handler == '0);
  assign halt_now   = do_halt || trap_halts;

  assign flush    = (exec && taken) || (trap && !trap_halts);
  assign redirect = trap ? handler : value;
  assign advance  = !hold && !halted;

  // ---------------------------------------------------------------- function units
  logic [31:0] alu_y;
  fu_alu u_alu (.op(alu_op), .a(alu_a), .b(value), .y(alu_y));

  fu_mul u_mul (.clk, .rst, .trig(exec && x_dst == P_MUL_T_B), .a(mul_a), .b(value), .out(mul_out));

  tta_sram_1r1w #(.WORDS(DMEM_W), .INIT_FILE(DMEM_INIT), .SIM_PLUSARG("data")) u_dmem (
    .clk, .rst,
    .re(exec && x_dst == P_MEM_T_LOAD),  .raddr(value[DAW-1:0]), .rdata(mem_data_out),
    .we(exec && x_dst == P_MEM_T_STORE), .waddr(value[DAW-1:0]), .wdata(mem_data_in)
  );

  fu_tmr u_tmr (
    .clk, .rst, .now,
    .do_sync   (exec && x_dst == P_TMR_T_SYNC),
    .do_advance(exec && x_dst == P_TMR_T_ADVANCE),
    .do_wait   (exec && x_dst == P_TMR_T_WAIT),
    .do_arm    (exec && x_dst == P_TMR_T_ARM),
    .do_clear  (exec && x_dst == P_TMR_T_CLEAR),
    .v(value), .trap_take(trap),
    .hold, .dl_hit, .elapsed(tmr_elapsed), .late(tmr_late), .armed(tmr_armed)
  );

  fu_telem #(.DEPTH(TELEM_FIFO_WORDS), .CYCLES_PER_WORD(TELEM_CYCLES_PER_WORD)) u_telem (
    .clk, .rst, .push(exec && x_dst == P_TELEM_T_PUSH), .data(value),
    .drops, .tx_start(telem_tx_start), .tx_data(telem_tx_data)
  );

  // ---------------------------------------------------------------- sequential
  always_ff @(posedge clk) begin
    if (rst) begin
      now <= '0;
      fetch_pc <= '0; ir_pc <= '0; ir_valid <= 1'b0; arch_pc <= '0;
      x_valid <= 1'b0; x_imm <= 1'b0; x_illegal <= 1'b0;
      x_pc <= '0; x_word <= '0; x_immval <= '0; x_dst <= '0; x_src <= '0;
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

      // ---- dst writes
      if (exec) begin
        if (x_dst[7:4] == 4'h1) regs[x_dst[3:0]] <= value;
        unique case (x_dst)
          P_ALU_A:        alu_a <= value;
          P_ALU_OP:       alu_op <= value[3:0];
          P_ALU_T_B:      alu_out <= alu_y;
          P_MUL_A:        mul_a <= value;
          P_CMP_A:        cmp_a <= value;
          P_CMP_T_B: begin
            cmp_eq  <= cmp_a == value;
            cmp_lt  <= $signed(cmp_a) < $signed(value);
            cmp_ltu <= cmp_a < value;
          end
          P_PC_COND:      cond <= value;
          P_PC_T_CALL:    link <= x_pc + 32'd1;
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
