`timescale 1ns / 1ps
// Integration testbench: arty_tta_top as on the board, tta_core -> telemetry
// FIFO -> uart_tx_word -> uart_rxd_out.
//
// Decodes every 8N1 frame on the UART line and prints, per 32-bit word,
//   W <cycle> <word>      cycle = tta_core's now when the start bit begins
// and at the end
//   D <drops>             telem.drops
// tests/test_rtl_units.py compares these with the ISS telemetry model.
//
// Plusargs: +max=<cycles of now to run>. The code and data images come in
// through the IMEM_INIT/DMEM_INIT parameters (verilator -G).
module tb_board;
  import tta_pkg::*;
  parameter string IMEM_INIT = "";
  parameter string DMEM_INIT = "";
  localparam int     CPB_I = TELEM_CYCLES_PER_WORD / 40;
  localparam longint CPB   = longint'(CPB_I);

  logic clk;
  initial begin
    clk = 1'b0;
    forever #5 clk = ~clk;
  end

  /* verilator lint_off UNUSEDSIGNAL */
  logic [3:0] led;
  /* verilator lint_on UNUSEDSIGNAL */
  logic       txd;

  arty_tta_top #(.IMEM_INIT(IMEM_INIT), .DMEM_INIT(DMEM_INIT)) dut (
    .CLK100MHZ(clk), .ck_rst(1'b1), .sw(4'b0), .led, .uart_rxd_out(txd)
  );

  // Everything is sampled on the falling edge, where now and txd both hold
  // the values of the current cycle.
  function automatic longint now();
    return longint'(dut.u_core.now);
  endfunction

  task automatic wait_now(longint c);
    while (now() < c) @(negedge clk);
  endtask

  int   errors = 0;
  logic txd_prev = 1'b1;

  initial begin : decode
    forever begin
      @(negedge clk);
      if (txd_prev && !txd) begin
        longint      s;
        logic [39:0] f;
        logic [31:0] w;
        s = now();
        for (int b = 0; b < 40; b++) begin
          wait_now(s + longint'(b) * CPB + CPB / 2);
          f[b] = txd;
        end
        for (int k = 0; k < 4; k++) begin
          if (f[k * 10] !== 1'b0 || f[k * 10 + 9] !== 1'b1) begin
            $display("FAIL framing in byte %0d of the word starting at %0d", k, s);
            errors++;
          end
          w[k * 8 +: 8] = f[k * 10 + 1 +: 8];          // LSB-first bytes, LSB-first bits
        end
        $display("W %0d %08x", s, w);
      end
      txd_prev = txd;
    end
  end

  initial begin
    longint max_cycles;
    if (!$value$plusargs("max=%d", max_cycles)) max_cycles = 100_000;
    wait (!dut.rst);
    wait_now(max_cycles);
    $display("D %0d", dut.u_core.drops);
    if (errors == 0) $display("END");
    $finish;
  end
endmodule
