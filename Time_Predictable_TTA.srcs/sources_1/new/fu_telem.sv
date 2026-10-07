`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/06/2026 07:14:01 PM
// Design Name: 
// Module Name: fu_telem.sv
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

// Telemetry FIFO + UART pacing (isa.md sec. 3, toolchain_formats.md sec. 6.5).
//
// Matches the ISS model exactly, because telem.drops is software-visible:
// each cycle the UART takes first, then a push lands; a word pushed at cycle p
// can start sending at p + 1, leaves the FIFO when it starts, and the UART is
// then busy for CYCLES_PER_WORD cycles. A push into a full FIFO is dropped.
module fu_telem #(
  parameter int DEPTH           = 64,
  parameter int CYCLES_PER_WORD = 1320
) (
  input  logic        clk,
  input  logic        rst,
  input  logic        push,
  input  logic [31:0] data,
  output logic [31:0] drops,
  output logic        tx_start,   // a word starts sending this cycle
  output logic [31:0] tx_data
);
  localparam int PW = $clog2(DEPTH);
  localparam int CW = $clog2(CYCLES_PER_WORD + 1);

  logic [31:0] fifo [DEPTH];
  logic [PW-1:0] rd, wr;
  logic [PW:0]   count;
  logic [CW-1:0] busy;
  logic          pop, accept;

  assign pop      = (count != 0) && (busy == 0);
  assign accept   = push && ((count - {{PW{1'b0}}, pop}) < (PW+1)'(DEPTH));
  assign tx_start = pop;
  assign tx_data  = fifo[rd];

  always_ff @(posedge clk) begin
    if (rst) begin
      rd <= '0; wr <= '0; count <= '0; busy <= '0; drops <= '0;
    end else begin
      count <= count + {{PW{1'b0}}, accept} - {{PW{1'b0}}, pop};
      if (pop) begin
        rd   <= rd + 1'b1;
        busy <= CW'(CYCLES_PER_WORD - 1);
      end else if (busy != 0) begin
        busy <= busy - 1'b1;
      end
      if (accept) begin
        fifo[wr] <= data;
        wr <= wr + 1'b1;
      end else if (push && drops != '1) begin
        drops <= drops + 1'b1;
      end
    end
  end
endmodule
