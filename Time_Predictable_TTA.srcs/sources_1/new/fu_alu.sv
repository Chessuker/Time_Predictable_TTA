`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/06/2026 07:10:03 PM
// Design Name: 
// Module Name: fu_alu.sv
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

// ALU datapath (isa.md sec. 4). Combinational; the core registers the result,
// which gives latency L = 1. op is always a legal opcode: an illegal write to
// alu.op traps and never reaches the op register.
module fu_alu import tta_pkg::*; (
  input  logic [3:0]  op,
  input  logic [31:0] a,
  input  logic [31:0] b,
  output logic [31:0] y
);
  logic [4:0] sh;
  assign sh = b[4:0];

  always_comb begin
    unique case (alu_op_e'(op))
      ALU_ADD: y = a + b;
      ALU_SUB: y = a - b;
      ALU_AND: y = a & b;
      ALU_OR:  y = a | b;
      ALU_XOR: y = a ^ b;
      ALU_SHL: y = a << sh;
      ALU_SHR: y = a >> sh;
      ALU_SRA: y = $unsigned($signed(a) >>> sh);
      ALU_MIN: y = ($signed(a) < $signed(b)) ? a : b;
      ALU_MAX: y = ($signed(a) < $signed(b)) ? b : a;
      default: y = '0;
    endcase
  end
endmodule
