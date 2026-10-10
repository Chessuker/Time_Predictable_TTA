`timescale 1ns / 1ps
// board_din against host/hil/board_din.py (tests/test_board_din.py).
//
// Plusargs: +pins=<file>  lines "<cycle> sw <0..15>" or "<cycle> btn <0|1>",
//                         cycles from the first cycle after reset, increasing per pin
//           +max=<cycles>
// Prints "I <cycle> <din>" whenever din changes, "L <cycle> <load_sw>" whenever
// load_sw changes, then "END". Pins change in the middle of a cycle, so the
// value given for cycle t is the one the synchroniser samples at the end of t.
module tb_board_din;
  parameter int N_DB = 4;

  logic clk, rst;
  initial begin
    clk = 1'b0;
    forever #5 clk = ~clk;
  end

  logic [3:0]  sw;
  logic        btn0, load_sw;
  logic [31:0] din;

  board_din #(.N_DB(N_DB)) dut (.clk, .rst, .sw, .btn0, .din, .load_sw);

  longint      sw_c [$], btn_c [$];
  logic [3:0]  sw_v [$];
  logic        btn_v [$];

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

  longint t;                            // cycles since reset was released
  initial begin
    string  path;
    longint max_cycles;
    logic [31:0] din_prev;
    logic        load_prev;
    if ($value$plusargs("pins=%s", path)) load_pins(path);
    if (!$value$plusargs("max=%d", max_cycles)) max_cycles = 10_000;
    sw = '0; btn0 = 1'b0; rst = 1'b1;
    repeat (3) @(posedge clk);
    // Cycle 0 is the one that began with the last rising edge in reset: its
    // registers hold their reset values, and the next rising edge is the first
    // update. Release reset in its middle, then walk one cycle per iteration.
    @(negedge clk);
    rst = 1'b0;
    t = 0;
    din_prev = '0; load_prev = 1'b0;
    while (t < max_cycles) begin
      foreach (sw_c[i]) if (sw_c[i] <= t) sw = sw_v[i];
      foreach (btn_c[i]) if (btn_c[i] <= t) btn0 = btn_v[i];
      if (din !== din_prev) $display("I %0d %0d", t, din);
      if (load_sw !== load_prev) $display("L %0d %0d", t, load_sw);
      din_prev = din; load_prev = load_sw;
      @(negedge clk);
      t++;
    end
    $display("END");
    $finish;
  end
endmodule
