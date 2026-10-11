`timescale 1ns / 1ps
// xbus_mem against a reference array, driven like the NEORV32 XBUS drives it
// (soc_xbus.adoc, and what the converted NEORV32 does in tb_baseline): single
// accesses each in their own bus cycle, cache-line bursts (cti = 010) of 16
// words read or written in one cycle, the next strobe either in the cycle of
// the previous acknowledge (as the CPU does) or some cycles later. Checks every read value and the
// exact strobe-to-ack latency (FIRST_LAT, then BURST_LAT), and that a request
// abandoned by dropping cyc gets no acknowledge. Prints PASS or FAIL lines.
module tb_xbus_mem;
  parameter int FIRST_LAT = 10;
  parameter int BURST_LAT = 1;
  parameter int N_TRANS   = 3000;
  localparam int WORDS = 1024;
  localparam int AW = $clog2(WORDS);

  logic clk, rst = 1'b1;
  initial clk = 1'b0;
  always #5 clk = ~clk;

  logic [AW-1:0] adr;
  logic [31:0]   dat_i, dat_o;
  logic          we, stb, cyc, ack;
  logic [3:0]    sel;
  logic [31:0]   model [WORDS];
  int            errors = 0;

  xbus_mem #(.WORDS(WORDS), .FIRST_LAT(FIRST_LAT), .BURST_LAT(BURST_LAT)) dut (
    .clk, .rst, .adr, .dat_i, .we, .sel, .stb, .cyc, .dat_o, .ack
  );

  // one access inside an open bus cycle: strobe, wait for ack, check latency/data
  // same_cycle: strobe in the cycle in which the previous request was acknowledged
  // (the task returns at the falling edge of that cycle)
  task automatic access(input logic [AW-1:0] a, input logic w, input logic [31:0] d,
                        input logic [3:0] s, input int lat, input bit same_cycle = 0);
    int n;
    if (!same_cycle) @(negedge clk);
    adr = a; we = w; dat_i = d; sel = s; stb = 1'b1;
    @(negedge clk);
    stb = 1'b0;
    n = 1;
    while (!ack) begin
      @(negedge clk);
      n++;
      if (n > 64) begin $display("FAIL no ack"); errors++; return; end
    end
    if (n != lat) begin
      $display("FAIL latency %0d, expected %0d (adr %0d, we %0b)", n, lat, a, w);
      errors++;
    end
    if (!w && dat_o !== model[a]) begin
      $display("FAIL read %0d: %08x, expected %08x", a, dat_o, model[a]);
      errors++;
    end
    if (w) for (int b = 0; b < 4; b++) if (s[b]) model[a][8*b +: 8] = d[8*b +: 8];
  endtask

  initial begin
    automatic int unsigned seed = 1;
    stb = 1'b0; cyc = 1'b0; we = 1'b0; sel = '0; adr = '0; dat_i = '0;
    for (int i = 0; i < WORDS; i++) model[i] = 32'hx;
    repeat (3) @(posedge clk);
    rst = 1'b0;
    // fill the memory so every later read has a defined value
    for (int i = 0; i < WORDS; i += 16) begin
      @(negedge clk); cyc = 1'b1;
      for (int j = 0; j < 16; j++) access(AW'(i + j), 1'b1, $urandom(seed), 4'hF, j == 0 ? FIRST_LAT : BURST_LAT);
      @(negedge clk); cyc = 1'b0;
    end
    for (int t = 0; t < N_TRANS; t++) begin
      automatic int kind = $urandom_range(0, 9);
      @(negedge clk); cyc = 1'b1;
      if (kind < 6) begin                              // single access, random byte enables
        automatic logic w = kind < 3;
        access(AW'($urandom_range(0, WORDS - 1)), w, $urandom, w ? 4'($urandom_range(1, 15)) : 4'hF,
               FIRST_LAT);
      end else begin                                   // 16-word line, read or write burst
        automatic int base = $urandom_range(0, WORDS / 16 - 1) * 16;
        automatic logic w = kind == 9;
        automatic bit piped = $urandom_range(0, 3) != 0;  // mostly as the CPU does
        for (int j = 0; j < 16; j++)
          access(AW'(base + j), w, $urandom, 4'hF, j == 0 ? FIRST_LAT : BURST_LAT, piped && j > 0);
      end
      @(negedge clk); cyc = 1'b0;
      repeat ($urandom_range(0, 2)) @(negedge clk);
    end
    // a request abandoned before its ack (bus timeout) is never acknowledged
    if (FIRST_LAT > 2) begin
      @(negedge clk); cyc = 1'b1; adr = '0; we = 1'b0; stb = 1'b1;
      @(negedge clk); stb = 1'b0;
      @(negedge clk); cyc = 1'b0;
      repeat (FIRST_LAT + 2) begin
        @(negedge clk);
        if (ack) begin $display("FAIL ack after the cycle was dropped"); errors++; end
      end
      @(negedge clk); cyc = 1'b1;
      access('0, 1'b0, '0, 4'hF, FIRST_LAT);
      @(negedge clk); cyc = 1'b0;
    end
    if (errors == 0) $display("PASS xbus_mem FIRST_LAT=%0d BURST_LAT=%0d", FIRST_LAT, BURST_LAT);
    $finish;
  end
endmodule
