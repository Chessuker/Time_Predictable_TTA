`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/06/2026 08:35:41 PM
// Design Name: 
// Module Name: tb_uart.sv
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

// uart_tx_word check: bit timing, byte order, and back-to-back words exactly
// 40 * CLKS_PER_BIT cycles apart (the pacing fu_telem assumes). Prints PASS or
// FAIL lines; the Python test looks for them.
/* verilator lint_off UNUSEDSIGNAL */
module tb_uart;
  localparam int     CPB = 33;
  localparam longint CPB_L = longint'(CPB);
  localparam longint WORD_CYCLES = 40 * CPB_L;

  logic clk, rst, start, txd, busy, ready;
  logic [31:0] word;
  int   errors;
  longint cyc;

  initial begin
    errors = 0;
    cyc = 0;
    clk = 1'b0;
    forever #5 clk = ~clk;
  end
  always @(posedge clk) cyc <= cyc + 1;

  uart_tx_word #(.CLKS_PER_BIT(CPB)) dut (.clk, .rst, .start, .word, .txd, .busy, .ready);

  // Sample one 40-bit frame whose start bit begins on the cycle after `s`.
  task automatic expect_word(longint s, logic [31:0] w);
    for (int b = 0; b < 40; b++) begin
      logic exp;
      int byte_i, bit_i;
      byte_i = b / 10; bit_i = b % 10;
      exp = (bit_i == 0) ? 1'b0 : (bit_i == 9) ? 1'b1 : w[byte_i * 8 + bit_i - 1];
      wait (cyc == s + 1 + longint'(b) * CPB_L + CPB_L / 2);
      @(negedge clk);
      if (txd !== exp) begin
        $display("FAIL bit %0d of word %08x: txd=%b expected %b", b, w, txd, exp);
        errors++;
      end
    end
  endtask

  initial begin
    longint s0, s1;
    rst = 1'b1; start = 1'b0; word = '0;
    repeat (3) @(posedge clk);
    @(negedge clk) rst = 1'b0;
    repeat (5) @(negedge clk);
    if (txd !== 1'b1 || !ready) begin $display("FAIL idle"); errors++; end

    // word 1
    word = 32'hA1B2C3D4; start = 1'b1; s0 = cyc;
    @(negedge clk) start = 1'b0;
    fork expect_word(s0, 32'hA1B2C3D4); join_none

    // ready must rise exactly WORD_CYCLES after the start, not earlier
    wait (cyc == s0 + WORD_CYCLES - 1); @(negedge clk);
    if (ready) begin $display("FAIL ready one cycle early"); errors++; end
    wait (cyc == s0 + WORD_CYCLES); @(negedge clk);
    if (!ready) begin $display("FAIL not ready at start + %0d", WORD_CYCLES); errors++; end

    // word 2, back to back
    word = 32'h0F00FF5A; start = 1'b1; s1 = cyc;
    @(negedge clk) start = 1'b0;
    expect_word(s1, 32'h0F00FF5A);

    wait (cyc == s1 + WORD_CYCLES + 2); @(negedge clk);
    if (txd !== 1'b1 || busy) begin $display("FAIL line not idle after word 2"); errors++; end

    if (errors == 0) $display("PASS uart_tx_word");
    $finish;
  end
endmodule
/* verilator lint_on UNUSEDSIGNAL */
