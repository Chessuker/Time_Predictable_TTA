`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company:
// Engineer:
//
// Create Date: 10/10/2026 09:00:00 PM
// Design Name:
// Module Name: dc_motor_plant.sv
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

// DC motor plant for the hardware-in-the-loop demo, single-cycle baseline.
//
// Bit-exact with host/plant_model FixedPlant (coefficients from plant_pkg.sv).
// A step happens at every cycle t with t mod STEP_CYCLES == PHASE, t counted
// from reset like the core's now. The whole update is one combinational
// datapath (three constant multiplies and a sum per state, round, shift) from
// the state registers and the inputs to the state registers:
//   - the step at cycle t uses u and tau as they are during cycle t
//     (pwm_cmd written by a move at cycle w is seen by steps at cycles > w)
//   - its result is visible from cycle t + 1
//     (io.encoder read at cycle c sees every step at a cycle < c)
//
// Widths: products and sums are 64 bits, exact for any 32-bit u and tau and
// |omega| < 2^38 (the closed loop stays near 2^22). theta is Q.16 in 48 bits,
// so the encoder (theta >> 16) is exact up to +-2^31 counts.
module dc_motor_plant import plant_pkg::*; #(
  parameter int STEP_CYCLES = PLANT_STEP,
  parameter int PHASE       = 0
) (
  input  logic               clk,
  input  logic               rst,
  input  logic signed [31:0] u,          // pwm_cmd, LSB
  input  logic signed [31:0] tau,        // load torque, 1e-6 N m
  output logic signed [31:0] encoder,    // counts, floor(theta)
  output logic               stepped,    // a step took effect this cycle (for tests)
  output logic signed [47:0] theta_q,    // Q.16
  output logic signed [39:0] omega_q     // Q.16
);
  localparam logic signed [63:0] HALF = 64'sd1 <<< (FC - 1);
  localparam int CW = $clog2(STEP_CYCLES + 1);

  logic [CW-1:0] cnt;
  logic          step;
  assign step = cnt == CW'(PHASE);

  logic signed [63:0] om64, u64, tau64, acc0, acc1;
  /* verilator lint_off UNUSEDSIGNAL */   // the top bits are sign copies within the stated range
  logic signed [63:0] d_theta, omega_n;
  /* verilator lint_on UNUSEDSIGNAL */
  assign om64  = 64'(omega_q);
  assign u64   = 64'(u);
  assign tau64 = 64'(tau);
  assign acc0  = C01 * om64 + CU0 * u64 + CT0 * tau64;
  assign acc1  = C11 * om64 + CU1 * u64 + CT1 * tau64;
  assign d_theta = (acc0 + HALF) >>> FC;
  assign omega_n = (acc1 + HALF) >>> FC;

  always_ff @(posedge clk) begin
    if (rst) begin
      cnt <= '0;
      theta_q <= '0;
      omega_q <= '0;
      stepped <= 1'b0;
    end else begin
      cnt <= (cnt == CW'(STEP_CYCLES - 1)) ? '0 : cnt + 1'b1;
      stepped <= step;
      if (step) begin
        theta_q <= theta_q + 48'(d_theta);
        omega_q <= 40'(omega_n);
      end
    end
  end

  assign encoder = 32'(theta_q >>> FP);
endmodule
