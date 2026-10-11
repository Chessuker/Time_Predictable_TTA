`timescale 1ns / 1ps
// Arty A7-100T top level of the Phase 6 baseline: NEORV32 with the same plant
// as the TTA core (baseline_soc), the board's reset button and USB-UART.
//
//   UART TX (uart_rxd_out): NEORV32 UART0 (bootloader, 19200 baud) until the
//                           program switches the timing monitor on, then the
//                           monitor's records (3 Mbaud, as the TTA telemetry)
//   UART RX (uart_txd_in):  NEORV32 UART0 (program upload to the bootloader);
//                           a break (low for 10 ms or more) resets the whole
//                           system, plant included (uart_break_reset)
//   led[0] monitor on, led[1] load torque on, led[3:2] = encoder[11:10]
module arty_neorv32_top #(
  parameter int CACHE_EN  = 1,
  parameter int N_BRK     = 1_000_000,    // UART break length that resets, cycles
  parameter int FIRST_LAT = 10,
  parameter int BURST_LAT = 1
) (
  input  logic       CLK100MHZ,
  input  logic       ck_rst,             // red RESET button, active low
  input  logic       uart_txd_in,        // PC -> FPGA (the name is from the PC's side)
  output logic       uart_rxd_out,       // FPGA -> PC
  output logic [3:0] led
);
  logic clk;
  assign clk = CLK100MHZ;

  // ---- reset: synchronise the button, hold reset for 16 cycles after configuration
  // (power-up values, as arty_tta_top), and reset while the host sends a break
  /* verilator lint_off PROCASSINIT */
  (* ASYNC_REG = "TRUE" *) logic [1:0] rst_sync = 2'b11;
  (* ASYNC_REG = "TRUE" *) logic [1:0] rx_sync = 2'b11;
  logic [3:0] por = '0;
  logic       rst = 1'b1;
  /* verilator lint_on PROCASSINIT */
  logic       brk;
  uart_break_reset #(.N_BRK(N_BRK)) u_brk (.clk, .rxd(rx_sync[1]), .brk);
  always_ff @(posedge clk) begin
    rst_sync <= {rst_sync[0], ~ck_rst};
    rx_sync  <= {rx_sync[0], uart_txd_in};
    if (por != 4'hF) por <= por + 1'b1;
    rst <= rst_sync[1] || (por != 4'hF) || brk;
  end

  logic        uart0_txd, mon_en, mon_txd, load_on;
  /* verilator lint_off UNUSEDSIGNAL */
  logic [31:0] pwm_cmd, encoder;
  /* verilator lint_on UNUSEDSIGNAL */

  baseline_soc #(.CACHE_EN(CACHE_EN), .FIRST_LAT(FIRST_LAT), .BURST_LAT(BURST_LAT)) u_soc (
    .clk, .rst, .uart0_rxd(rx_sync[1]), .uart0_txd, .mon_en, .mon_txd, .pwm_cmd, .encoder, .load_on
  );

  always_ff @(posedge clk) begin
    uart_rxd_out <= mon_en ? mon_txd : uart0_txd;
    led          <= {encoder[11:10], load_on, mon_en};
  end
endmodule
