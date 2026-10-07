`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/06/2026 07:14:56 PM
// Design Name: 
// Module Name: fu_tmr.sv
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

// Timing Unit, slot 0 (timing_model.md R5, R6; isa.md sec. 7).
//
// The spec states everything in absolute 64-bit times (now, anchor, deadline,
// target). This implementation keeps only the distances it needs, which is
// equivalent and much shorter in logic:
//
//   el   = now - anchor           (64 bits, exact under the R7 no-wrap assumption)
//   wrem = target - now           while a t_wait/t_advance stall is running
//   rem  = deadline - now         while armed
//
// For a timing move at cycle n with bus value v (target T = anchor + v):
//   n + 1 <= T   <=>  el < v          -> stall next cycle (hold)
//   n > T        <=>  el > v          -> late
//   deadline <= n + 1  <=>  v <= el + 1 -> deadline trap next cycle (dl_hit)
// so each decision on the bus-value path is one 32-bit compare against el.
// (el >= 0 whenever a move executes: it is negative only inside a t_advance
// stall, and B1 keeps traps out of those.)
//
// Outputs are registered and valid for the current cycle:
//   hold    no move may execute (stall)
//   dl_hit  the deadline trap fires
//   elapsed tmr.elapsed, saturated to 32 bits (A6)
module fu_tmr (
  input  logic        clk,
  input  logic        rst,
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
  logic [63:0] el;
  logic [31:0] wrem, rem;

  logic        el_big;            // el >= 2^32 (or negative): compares against v are decided
  logic [32:0] el_lo, el_lo_p1;
  logic        lt_v, gt_v, ge_v_m1;
  logic [63:0] el_p1, el_adv;

  assign el_big   = el[63:32] != '0;
  assign el_lo    = {1'b0, el[31:0]};
  assign el_lo_p1 = el_lo + 33'd1;
  assign lt_v     = !el_big && (el_lo < {1'b0, v});            // el < v
  assign gt_v     =  el_big || (el_lo > {1'b0, v});            // el > v
  assign ge_v_m1  =  el_big || (el_lo_p1 >= {1'b0, v});        // el + 1 >= v
  assign el_p1    = el + 64'd1;
  assign el_adv   = el_p1 - {32'b0, v};                        // now + 1 - (anchor + v)

  always_ff @(posedge clk) begin
    if (rst) begin
      el <= '0;        // reset values are the values of cycle 0: now = anchor = 0
      wrem <= '0; rem <= '0;
      hold <= 1'b0; dl_hit <= 1'b0; armed <= 1'b0; late <= 1'b0;
    end else begin
      // ---- el
      if (do_sync)         el <= 64'd1;
      else if (do_advance) el <= el_adv;
      else                 el <= el_p1;

      // ---- stall (hold) and late
      if (trap_take) begin
        hold <= 1'b0;
      end else if (do_advance || do_wait) begin
        late <= gt_v;
        hold <= lt_v;
        wrem <= v - el[31:0] - 32'd1;                  // target - (n + 1), only used when hold
      end else if (hold) begin
        hold <= wrem != '0;
        wrem <= wrem - 32'd1;
      end

      // ---- deadline
      if (trap_take || do_clear) begin
        armed  <= 1'b0;
        dl_hit <= 1'b0;
      end else if (do_arm) begin
        armed  <= 1'b1;
        dl_hit <= ge_v_m1;
        rem    <= v - el[31:0] - 32'd1;                // deadline - (n + 1), only used when !dl_hit
      end else if (armed && !dl_hit) begin
        dl_hit <= rem == 32'd1;
        rem    <= rem - 32'd1;
      end
    end
  end

  assign elapsed = el_big ? 32'hFFFF_FFFF : el[31:0];
endmodule
