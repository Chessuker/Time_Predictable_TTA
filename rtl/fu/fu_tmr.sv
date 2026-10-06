// Timing Unit, slot 0 (timing_model.md R5, R6; isa.md sec. 7).
//
// State is 64 bits; values on the bus are 32 bits, zero-extended. The two
// signals that steer the pipeline are registered, computed one cycle ahead
// from next-state values, so no 64-bit compare sits on the stall/trap path:
//   hold   : no move may execute this cycle   (now <= target of the last sync point)
//   dl_hit : the deadline trap fires this cycle (armed && now >= deadline)
module fu_tmr (
  input  logic        clk,
  input  logic        rst,
  input  logic [63:0] now,
  input  logic        do_sync,
  input  logic        do_advance,
  input  logic        do_wait,
  input  logic        do_arm,
  input  logic        do_clear,
  input  logic [31:0] v,
  input  logic        trap_take,   // a trap is taken this cycle
  output logic        hold,
  output logic        dl_hit,
  output logic [31:0] elapsed,
  output logic        late,
  output logic        armed
);
  logic [63:0] anchor, deadline, wait_t;
  logic [63:0] anchor_n, deadline_n, wait_n, target, now1, diff_n;
  logic        armed_n, late_n;

  assign target = anchor + {32'b0, v};
  assign now1   = now + 64'd1;

  always_comb begin
    anchor_n   = anchor;
    deadline_n = deadline;
    wait_n     = wait_t;
    late_n     = late;
    armed_n    = armed;
    if (do_sync)    anchor_n = now;
    if (do_advance) anchor_n = target;
    if (do_advance || do_wait) begin
      wait_n = target;
      late_n = now > target;
    end
    if (do_arm)   begin deadline_n = target; armed_n = 1'b1; end
    if (do_clear) armed_n = 1'b0;
    if (trap_take) begin
      armed_n = 1'b0;     // R6: the trap disarms
      wait_n  = '0;       // a trap ends a t_wait stall
    end
  end

  assign diff_n = now1 - anchor_n;

  always_ff @(posedge clk) begin
    if (rst) begin
      anchor <= '0; deadline <= '0; wait_t <= '0;
      armed <= 1'b0; late <= 1'b0;
      hold <= 1'b0; dl_hit <= 1'b0; elapsed <= '0;
    end else begin
      anchor   <= anchor_n;
      deadline <= deadline_n;
      wait_t   <= wait_n;
      armed    <= armed_n;
      late     <= late_n;
      hold     <= now1 <= wait_n;                      // next cycle <= target: stall
      dl_hit   <= armed_n && (now1 >= deadline_n);
      // elapsed = now - anchor, saturating to 32 bits (A6). now < anchor
      // only occurs inside a t_advance stall, where nothing can read it.
      if (now1 < anchor_n)         elapsed <= '0;
      else if (diff_n[63:32] != 0) elapsed <= '1;
      else                         elapsed <= diff_n[31:0];
    end
  end
endmodule
