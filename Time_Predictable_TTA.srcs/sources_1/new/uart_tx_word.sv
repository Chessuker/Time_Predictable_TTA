`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/06/2026 07:21:46 PM
// Design Name: 
// Module Name: uart_tx_word.sv
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

// UART transmitter for one 32-bit word: 4 bytes, least significant byte first,
// each 8N1 (start bit, 8 data bits LSB first, stop bit), CLKS_PER_BIT clocks
// per bit. A word takes exactly 40 * CLKS_PER_BIT clocks, which must equal
// TELEM_CYCLES_PER_WORD so fu_telem's pacing (and telem.drops) is exact.
//
// A word started at cycle s drives its first start bit from s + 1 and its
// last stop bit ends at s + 40 * CLKS_PER_BIT. fu_telem starts the next word at
// exactly that cycle, so `ready` includes the final clock of the last stop bit
// and words go out back to back with no gap.
module uart_tx_word #(
  parameter int CLKS_PER_BIT = 33
) (
  input  logic        clk,
  input  logic        rst,
  input  logic        start,
  input  logic [31:0] word,
  output logic        txd,
  output logic        busy,
  output logic        ready
);
  localparam int CW = $clog2(CLKS_PER_BIT);

  logic [39:0] frame;      // bits still to send, LSB goes out first
  logic [5:0]  bits_left;
  logic [CW-1:0] tick;

  function automatic logic [39:0] make_frame(logic [31:0] w);
    logic [39:0] f;
    for (int i = 0; i < 4; i++) f[i*10 +: 10] = {1'b1, w[i*8 +: 8], 1'b0};  // stop, data, start
    return f;
  endfunction

  assign busy  = bits_left != 0;
  assign ready = !busy || (bits_left == 6'd1 && tick == 0);

  always_ff @(posedge clk) begin
    if (rst) begin
      frame <= '1; bits_left <= '0; tick <= '0; txd <= 1'b1;
    end else if (start && ready) begin
      frame     <= make_frame(word) >> 1;
      txd       <= 1'b0;                     // first start bit
      bits_left <= 6'd40;
      tick      <= CW'(CLKS_PER_BIT - 1);
    end else if (busy) begin
      if (tick != 0) begin
        tick <= tick - 1'b1;
      end else begin
        bits_left <= bits_left - 1'b1;
        tick      <= CW'(CLKS_PER_BIT - 1);
        txd       <= (bits_left == 6'd1) ? 1'b1 : frame[0];
        frame     <= {1'b1, frame[39:1]};
      end
    end
  end
endmodule
