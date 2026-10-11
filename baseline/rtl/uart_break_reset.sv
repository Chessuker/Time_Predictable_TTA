`timescale 1ns / 1ps
// Reset by UART break: when the (synchronised) RX line has been low for N_BRK
// cycles in a row, brk goes high and stays high until the line is high again.
// The host sends a break (pyserial send_break) to put the board back into the
// NEORV32 bootloader between runs, without the RESET button.
//
// A character can hold the line low for at most 9 bit times (start bit and
// eight 0 data bits): 4.7 ms at 1920 baud, 0.47 ms at the bootloader's 19200,
// so N_BRK = 10 ms never fires on data at 1920 baud or faster.
//
//   rxd low from cycle c (and every cycle up to c + N_BRK - 1)  ->  brk = 1 from cycle c + N_BRK
//   rxd high at cycle h                                          ->  brk = 0 from cycle h + 1
module uart_break_reset #(
  parameter int N_BRK = 1_000_000          // 10 ms at 100 MHz
) (
  input  logic clk,
  input  logic rxd,                        // already synchronised to clk
  output logic brk
);
  localparam int W = $clog2(N_BRK + 1);
  /* verilator lint_off PROCASSINIT */
  logic [W-1:0] low = '0;                  // low cycles so far, saturates at N_BRK
  /* verilator lint_on PROCASSINIT */

  always_ff @(posedge clk) begin
    if (rxd) low <= '0;
    else if (low != W'(N_BRK)) low <= low + 1'b1;
  end
  assign brk = low == W'(N_BRK);
endmodule
