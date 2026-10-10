`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company:
// Engineer:
//
// Create Date: 10/11/2026 09:00:00 AM
// Design Name:
// Module Name: hil_env.sv
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

// Hardware-in-the-loop environment around the core: the DC motor plant
// (multi-cycle, see docs/design_decisions.md) and a load torque that is on
// during [LOAD_ON, LOAD_OFF) of every LOAD_PERIOD cycles. Counted from reset
// like the core's now, so host/hil/env.py reproduces it cycle for cycle.
// The load window can be shortened for simulation through the parameters.
// load_sw (the debounced load switch, board_din.sv) also turns the load on;
// with it at 0 the environment is exactly the Phase 5 one.
module hil_env import plant_pkg::*; #(
  parameter int LOAD_PERIOD_C = LOAD_PERIOD,
  parameter int LOAD_ON_C     = LOAD_ON,
  parameter int LOAD_OFF_C    = LOAD_OFF
) (
  input  logic        clk,
  input  logic        rst,
  input  logic [31:0] pwm_cmd,         // io.pwm_cmd of the core
  input  logic        load_sw,         // switch-mode load, already debounced
  output logic [31:0] encoder,         // to io.encoder of the core
  output logic        load_on
);
  localparam int LW = $clog2(LOAD_PERIOD_C + 1);

  logic [LW-1:0] lc;
  always_ff @(posedge clk) begin
    if (rst) lc <= '0;
    else     lc <= (lc == LW'(LOAD_PERIOD_C - 1)) ? '0 : lc + 1'b1;
  end
  assign load_on = (lc >= LW'(LOAD_ON_C) && lc < LW'(LOAD_OFF_C)) || load_sw;

  logic signed [31:0] tau, enc;
  assign tau = load_on ? 32'(LOAD_TAU) : '0;

  /* verilator lint_off PINCONNECTEMPTY */
  dc_motor_plant_mc #(.PHASE(PLANT_PHASE)) u_plant (
    .clk, .rst, .u(pwm_cmd), .tau, .encoder(enc),
    .stepped(), .theta_q(), .omega_q()
  );
  /* verilator lint_on PINCONNECTEMPTY */
  assign encoder = enc;
endmodule
