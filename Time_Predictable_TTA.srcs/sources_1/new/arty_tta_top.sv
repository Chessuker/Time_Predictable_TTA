`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/06/2026 07:49:25 PM
// Design Name: 
// Module Name: arty_tta_top.sv
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

// Arty A7-100T top level: the core in its hardware-in-the-loop environment.
//
// Board-specific (pins, the 100 MHz oscillator, the active-low reset button);
// everything under it is vendor-neutral. The code and data images are loaded
// at synthesis from IMEM_INIT/DMEM_INIT ($readmemh, paths relative to the
// directory Vivado runs in).
//
//   io.pwm_cmd -> hil_env (DC motor plant + load torque) -> io.encoder   (Phase 5)
//   led[3] = load torque on, led[2:0] = encoder[11:9]; all four on when the core halts
//   sw[3:0] is not used yet (planned: io.sw for interactive setpoints)
//   telemetry -> UART TX (uart_rxd_out), 4 bytes per word, LSB first, 8N1
//                at 100 MHz / CLKS_PER_BIT (about 3.03 Mbaud; open the port at 3,000,000)
module arty_tta_top import tta_pkg::*; #(
  parameter string IMEM_INIT = "board_hello.code.hex",
  parameter string DMEM_INIT = "board_hello.data.hex"
) (
  input  logic       CLK100MHZ,
  input  logic       ck_rst,          // red RESET button, active low
  input  logic [3:0] sw,
  output logic [3:0] led,
  output logic       uart_rxd_out     // FPGA -> PC (the name is from the PC's side)
);
  localparam int CLKS_PER_BIT = TELEM_CYCLES_PER_WORD / 40;

  logic clk;
  assign clk = CLK100MHZ;

  // ---- reset: synchronise the button, and hold reset for 16 cycles after configuration.
  // These registers rely on FPGA power-up values (there is no reset before them).
  /* verilator lint_off PROCASSINIT */
  (* ASYNC_REG = "TRUE" *) logic [1:0] rst_sync = 2'b11;
  logic [3:0] por = '0;
  logic       rst = 1'b1;
  always_ff @(posedge clk) begin
    rst_sync <= {rst_sync[0], ~ck_rst};
    if (por != 4'hF) por <= por + 1'b1;
    rst <= rst_sync[1] || (por != 4'hF);
  end

  /* verilator lint_on PROCASSINIT */

  /* verilator lint_off UNUSEDSIGNAL */  // sw: reserved; encoder: only [11:9] reaches the LEDs
  logic [3:0]  sw_unused;
  logic [31:0] encoder;
  /* verilator lint_on UNUSEDSIGNAL */
  assign sw_unused = sw;
  logic [31:0] pwm, tx_data;
  logic        tx_start, halted, load_on;

  hil_env u_env (.clk, .rst, .pwm_cmd(pwm), .encoder, .load_on);

  /* verilator lint_off PINCONNECTEMPTY */
  tta_core #(.IMEM_INIT(IMEM_INIT), .DMEM_INIT(DMEM_INIT)) u_core (
    .clk, .rst,
    .io_encoder(encoder),
    .io_pwm_cmd(pwm),
    .telem_tx_start(tx_start),
    .telem_tx_data(tx_data),
    .halted,
    .dbg_now(), .rv_valid(), .rv_pc(), .rv_word(), .rv_imm(), .rv_src(), .rv_dst(), .rv_value(),
    .tr_valid(), .tr_epc(), .tr_cause(), .ht_valid(), .ht_reason()
  );

  uart_tx_word #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_uart (
    .clk, .rst, .start(tx_start), .word(tx_data), .txd(uart_rxd_out), .busy(), .ready()
  );
  /* verilator lint_on PINCONNECTEMPTY */

  assign led = halted ? 4'hF : {load_on, encoder[11:9]};
endmodule
