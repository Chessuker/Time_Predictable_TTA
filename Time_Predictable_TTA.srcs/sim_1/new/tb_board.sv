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
// Board inputs (switch mode): +pins=<file> with lines "<now> sw <0..15>" or
// "<now> btn <0|1>"; a pin given for cycle c is set in the middle of cycle c.
// Then it also prints
//   I <cycle> <din>       io.din at the core input changes (board_din output)
//   P <cycle> <pwm>       io.pwm_cmd at the core output changes
//
// Plusargs: +max=<cycles of now to run>. The code and data images come in
// through the IMEM_INIT/DMEM_INIT parameters (verilator -G), the debounce
// length through N_DB.
module tb_board;
  import tta_pkg::*;
  parameter string IMEM_INIT = "";
  parameter string DMEM_INIT = "";
  parameter int    N_DB      = 500_000;
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
  logic [3:0] sw = '0;
  logic       btn0 = 1'b0;

  arty_tta_top #(.IMEM_INIT(IMEM_INIT), .DMEM_INIT(DMEM_INIT), .N_DB(N_DB)) dut (
    .CLK100MHZ(clk), .ck_rst(1'b1), .sw, .btn0, .led, .uart_rxd_out(txd)
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

  // ---- board inputs from +pins, and the IO seen by the core
  longint     sw_c [$], btn_c [$];
  logic [3:0] sw_v [$];
  logic       btn_v [$];

  task automatic load_pins(string path);
    int fd, n, line;
    longint c;
    /* verilator lint_off UNUSEDSIGNAL */
    longint v;                          // only the low bits are pin values
    /* verilator lint_on UNUSEDSIGNAL */
    string pin;
    fd = $fopen(path, "r");
    if (fd == 0) begin $display("error: cannot open pins %s", path); $fatal(1); end
    line = 0;
    while (!$feof(fd)) begin
      n = $fscanf(fd, "%d %s %d\n", c, pin, v);
      line++;
      if (n <= 0) continue;
      if (n != 3 || (pin != "sw" && pin != "btn")) begin
        $display("error: pins line %0d", line); $fatal(1);
      end
      if (pin == "sw") begin sw_c.push_back(c); sw_v.push_back(v[3:0]); end
      else begin btn_c.push_back(c); btn_v.push_back(v[0]); end
    end
    $fclose(fd);
  endtask

  initial begin : io
    string       path;
    logic [31:0] din_prev, pwm_prev;
    din_prev = '0;
    pwm_prev = '0;
    if ($value$plusargs("pins=%s", path)) load_pins(path);
    wait (!dut.rst);
    forever begin
      @(negedge clk);
      foreach (sw_c[i]) if (sw_c[i] <= now()) sw = sw_v[i];
      foreach (btn_c[i]) if (btn_c[i] <= now()) btn0 = btn_v[i];
      if (dut.din !== din_prev) $display("I %0d %0d", now(), dut.din);
      if (dut.pwm !== pwm_prev) $display("P %0d %0d", now(), dut.pwm);
      din_prev = dut.din; pwm_prev = dut.pwm;
    end
  end

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
