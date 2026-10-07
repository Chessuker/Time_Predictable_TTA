`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/06/2026 07:12:13 PM
// Design Name: 
// Module Name: fu_mul.sv
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

// MUL: signed 32 x 32, low 32 bits (isa.md, timing_model.md sec. 7), L = 2.
//
// The trigger cycle c captures both operands; the product registers at the
// end of c + 1, so the first legal read is c + 2. Capturing a here, not at
// c + 1, keeps the result right even if mul.a is rewritten right after the
// trigger. Back-to-back triggers pipeline cleanly.
module fu_mul (
  input  logic        clk,
  input  logic        rst,
  input  logic        trig,
  input  logic [31:0] a,
  input  logic [31:0] b,
  output logic [31:0] out
);
  logic [31:0] x, y;
  logic        pend;
  logic [31:0] prod;

  // Only the low 32 bits are kept; they are the same for signed and unsigned
  // operands, so a 32-bit unsigned multiply is exact.
  assign prod = x * y;

  always_ff @(posedge clk) begin
    if (rst) begin
      x <= '0; y <= '0; pend <= 1'b0; out <= '0;
    end else begin
      pend <= trig;
      if (trig) begin x <= a; y <= b; end
      if (pend) out <= prod;
    end
  end
endmodule
