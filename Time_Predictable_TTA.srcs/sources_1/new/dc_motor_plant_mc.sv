`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company:
// Engineer:
//
// Create Date: 10/10/2026 10:00:00 PM
// Design Name:
// Module Name: dc_motor_plant_mc.sv
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

// DC motor plant, multi-cycle variant: same arithmetic and ports as
// dc_motor_plant.sv, but one shared multiplier with registers around it.
//
// At a step cycle t (t mod STEP_CYCLES == PHASE) it latches omega, u and tau.
// It then feeds the six products C01*omega, CU0*u, CT0*tau, C11*omega,
// CU1*u, CT1*tau through the multiplier, one per cycle, and accumulates them.
// The new state is written at the end of cycle t + LAST, so it is visible from
// cycle t + LATENCY with LATENCY = LAST + 1 = 10 (the single-cycle plant: 1). The inputs
// count as they were during cycle t, exactly as in the single-cycle plant.
// Only the time the result appears differs, and the closed-loop models take
// LATENCY as a parameter.
module dc_motor_plant_mc import plant_pkg::*; #(
  parameter int STEP_CYCLES = PLANT_STEP,
  parameter int PHASE       = 0
) (
  input  logic               clk,
  input  logic               rst,
  input  logic signed [31:0] u,
  input  logic signed [31:0] tau,
  output logic signed [31:0] encoder,
  output logic               stepped,    // the new state was written this cycle (for tests)
  output logic signed [47:0] theta_q,
  output logic signed [39:0] omega_q
);
  localparam logic signed [63:0] HALF = 64'sd1 <<< (FC - 1);
  localparam int CW = $clog2(STEP_CYCLES + 1);
  // issue 6 products, 3 register stages (operands, product, accumulate)
  localparam int LAST = 6 + 3;

  logic [CW-1:0] cnt;
  logic          step;
  assign step = cnt == CW'(PHASE);

  // latched inputs of the step in flight
  logic signed [39:0] om_l, u_l, tau_l;
  logic [3:0]         phase_i;           // 0 = idle, 1..LAST = cycle of the computation

  // operand mux: product k uses coefficient COEF[k] and variable VAR[k]
  logic signed [27:0] a_r;               // coefficient (every C fits 28 signed bits)
  logic signed [39:0] b_r;               // variable
  logic signed [63:0] m_r;               // product, low 64 bits as in the single-cycle plant
  logic               a_v, m_v;          // valid flags through the pipeline
  logic               a_d, m_d;          // which accumulator (0: theta, 1: omega)
  logic signed [63:0] acc0, acc1;
  /* verilator lint_off UNUSEDSIGNAL */   // coefficients are 64-bit constants that fit in 28 bits
  logic signed [63:0] coef;
  /* verilator lint_on UNUSEDSIGNAL */
  logic signed [39:0] var_s;
  logic               dst;

  always_comb begin
    coef = '0; var_s = '0; dst = 1'b0;
    unique case (phase_i)
      4'd1: begin coef = C01; var_s = om_l;  dst = 1'b0; end
      4'd2: begin coef = CU0; var_s = u_l;   dst = 1'b0; end
      4'd3: begin coef = CT0; var_s = tau_l; dst = 1'b0; end
      4'd4: begin coef = C11; var_s = om_l;  dst = 1'b1; end
      4'd5: begin coef = CU1; var_s = u_l;   dst = 1'b1; end
      4'd6: begin coef = CT1; var_s = tau_l; dst = 1'b1; end
      default: ;
    endcase
  end

  /* verilator lint_off UNUSEDSIGNAL */   // the top bits are sign copies within the stated range
  logic signed [63:0] d_theta, omega_n;
  /* verilator lint_on UNUSEDSIGNAL */
  assign d_theta = (acc0 + HALF) >>> FC;
  assign omega_n = (acc1 + HALF) >>> FC;

  always_ff @(posedge clk) begin
    if (rst) begin
      cnt <= '0;
      theta_q <= '0; omega_q <= '0;
      om_l <= '0; u_l <= '0; tau_l <= '0;
      phase_i <= '0;
      a_r <= '0; b_r <= '0; m_r <= '0;
      a_v <= 1'b0; m_v <= 1'b0; a_d <= 1'b0; m_d <= 1'b0;
      acc0 <= '0; acc1 <= '0;
      stepped <= 1'b0;
    end else begin
      cnt <= (cnt == CW'(STEP_CYCLES - 1)) ? '0 : cnt + 1'b1;
      stepped <= 1'b0;

      // stage 0: sequencer
      if (step) begin
        om_l <= omega_q; u_l <= 40'(u); tau_l <= 40'(tau);
        acc0 <= '0; acc1 <= '0;
        phase_i <= 4'd1;
      end else if (phase_i != 0) begin
        phase_i <= (phase_i == 4'(LAST)) ? 4'd0 : phase_i + 4'd1;
      end

      // stage 1: operands
      a_v <= phase_i >= 4'd1 && phase_i <= 4'd6;
      a_d <= dst;
      a_r <= 28'(coef);
      b_r <= var_s;
      // stage 2: product
      m_v <= a_v;
      m_d <= a_d;
      m_r <= 64'(a_r) * 64'(b_r);
      // stage 3: accumulate
      if (m_v) begin
        if (m_d) acc1 <= acc1 + m_r;
        else     acc0 <= acc0 + m_r;
      end

      // commit once the last product is in
      if (phase_i == 4'(LAST)) begin
        theta_q <= theta_q + 48'(d_theta);
        omega_q <= 40'(omega_n);
        stepped <= 1'b1;
      end
    end
  end

  assign encoder = 32'(theta_q >>> FP);
endmodule
