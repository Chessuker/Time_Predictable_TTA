`timescale 1ns / 1ps
// xbus_mem against a reference array, driven like the NEORV32 XBUS drives it
// (soc_xbus.adoc, neorv32_xbus.vhd and the cache of NEORV32 v1.13.6): a
// transaction is one single access (cti = 000) or one 16-word cache-line burst
// whose words carry cti = 010 except the last (000). The XBUS keeps cyc high
// while a request is pending and may raise the next strobe in the cycle of the
// previous acknowledge, so several transactions can share one unbroken cyc.
//
// The latency rule checked here: the first access of every transaction is
// acknowledged FIRST_LAT cycles after its strobe, the further words of a burst
// BURST_LAT cycles after theirs, whether or not cyc dropped in between.
//
// Directed cases first (each prints FAIL <case> on a wrong latency), then
// random runs of transactions inside held bus cycles; every read value is
// checked, and a request abandoned by dropping cyc gets no acknowledge.
// Prints PASS or FAIL lines.
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
  logic [2:0]    cti;
  logic [31:0]   model [WORDS];
  int            errors = 0;
  string         where = "fill";

  xbus_mem #(.WORDS(WORDS), .FIRST_LAT(FIRST_LAT), .BURST_LAT(BURST_LAT)) dut (
    .clk, .rst, .adr, .dat_i, .we, .sel, .cti, .stb, .cyc, .dat_o, .ack
  );

  // one access inside an open bus cycle: strobe, wait for ack, check latency/data
  // same_cycle: strobe in the cycle in which the previous request was acknowledged
  // (the task returns at the falling edge of that cycle)
  task automatic access(input logic [AW-1:0] a, input logic w, input logic [31:0] d,
                        input logic [3:0] s, input logic [2:0] c, input int lat,
                        input bit same_cycle = 0);
    int n;
    if (!same_cycle) @(negedge clk);
    adr = a; we = w; dat_i = d; sel = s; cti = c; stb = 1'b1;
    @(negedge clk);
    stb = 1'b0;
    n = 1;
    while (!ack) begin
      @(negedge clk);
      n++;
      if (n > 64) begin $display("FAIL %s: no ack", where); errors++; return; end
    end
    if (n != lat) begin
      $display("FAIL %s: latency %0d, expected %0d (adr %0d, we %0b, cti %0b)", where, n, lat, a, w, c);
      errors++;
    end
    if (!w && dat_o !== model[a]) begin
      $display("FAIL %s: read %0d: %08x, expected %08x", where, a, dat_o, model[a]);
      errors++;
    end
    if (w) for (int b = 0; b < 4; b++) if (s[b]) model[a][8*b +: 8] = d[8*b +: 8];
  endtask

  // one single access (a whole transaction): always FIRST_LAT
  task automatic single(input logic [AW-1:0] a, input logic w, input bit piped);
    access(a, w, $urandom, w ? 4'($urandom_range(1, 15)) : 4'hF, 3'b000, FIRST_LAT, piped);
  endtask

  // one 16-word line burst: FIRST_LAT, then BURST_LAT; cti 010 except the last word
  // piped: every word after the first is strobed in the ack cycle of the one before;
  // first_piped: the first word too (in the ack cycle of the previous transaction)
  task automatic burst(input int base, input logic w, input bit piped, input bit first_piped);
    for (int j = 0; j < 16; j++)
      access(AW'(base + j), w, $urandom, 4'hF, j == 15 ? 3'b000 : 3'b010,
             j == 0 ? FIRST_LAT : BURST_LAT, j == 0 ? first_piped : piped);
  endtask

  task automatic open_cycle();  @(negedge clk); cyc = 1'b1; endtask
  task automatic close_cycle(); @(negedge clk); cyc = 1'b0; endtask

  initial begin
    automatic int unsigned seed = 1;
    stb = 1'b0; cyc = 1'b0; we = 1'b0; sel = '0; adr = '0; dat_i = '0; cti = '0;
    for (int i = 0; i < WORDS; i++) model[i] = 32'hx;
    repeat (3) @(posedge clk);
    rst = 1'b0;

    // fill the memory so every later read has a defined value (also: first access after reset)
    for (int i = 0; i < WORDS; i += 16) begin
      open_cycle();
      for (int j = 0; j < 16; j++)
        access(AW'(i + j), 1'b1, $urandom(seed), 4'hF, j == 15 ? 3'b000 : 3'b010,
               j == 0 ? FIRST_LAT : BURST_LAT);
      close_cycle();
    end

    // ---- directed: transaction boundaries inside one unbroken cyc
    where = "singles piped in one cycle";        // what the CPU does with the caches off
    open_cycle(); single(10, 0, 0); single(11, 0, 1); single(12, 1, 1); single(13, 0, 1); close_cycle();

    where = "singles with gaps in one cycle";
    open_cycle(); single(20, 0, 0); repeat (2) @(negedge clk); single(21, 0, 0); single(22, 1, 0); close_cycle();

    where = "burst then single, piped";
    open_cycle(); burst(64, 0, 1, 0); single(5, 0, 1); close_cycle();

    where = "burst then burst, piped";            // a line fill in the ack cycle of the last
    open_cycle(); burst(128, 0, 1, 0); burst(256, 0, 1, 1); close_cycle();

    where = "single then burst, piped";
    open_cycle(); single(7, 0, 0); burst(320, 1, 1, 1); close_cycle();

    where = "write burst then read burst";
    open_cycle(); burst(384, 1, 1, 0); burst(384, 0, 1, 1); close_cycle();

    where = "burst cut by dropping cyc";          // cti 010 seen, then the cycle ends
    open_cycle();
    access(AW'(512), 1'b0, '0, 4'hF, 3'b010, FIRST_LAT);
    access(AW'(513), 1'b0, '0, 4'hF, 3'b010, BURST_LAT, 1);
    close_cycle();
    open_cycle(); single(514, 0, 0); close_cycle();   // a new transaction: FIRST_LAT again

    // ---- random: 1 to 4 transactions per bus cycle, piped or not
    where = "random";
    for (int t = 0; t < N_TRANS; ) begin
      automatic int k = $urandom_range(1, 4);
      open_cycle();
      for (int i = 0; i < k; i++, t++) begin
        automatic int kind = $urandom_range(0, 9);
        automatic bit piped = i > 0 && $urandom_range(0, 3) != 0;  // mostly as the CPU does
        if (kind < 6) single(AW'($urandom_range(0, WORDS - 1)), kind < 3, piped);
        else burst($urandom_range(0, WORDS / 16 - 1) * 16, kind == 9, $urandom_range(0, 3) != 0, piped);
        if (!piped && $urandom_range(0, 2) == 0) @(negedge clk);
      end
      close_cycle();
      repeat ($urandom_range(0, 2)) @(negedge clk);
    end

    // a request abandoned before its ack (bus timeout) is never acknowledged
    where = "abandoned request";
    if (FIRST_LAT > 2) begin
      open_cycle(); adr = '0; we = 1'b0; cti = 3'b000; stb = 1'b1;
      @(negedge clk); stb = 1'b0;
      close_cycle();
      repeat (FIRST_LAT + 2) begin
        @(negedge clk);
        if (ack) begin $display("FAIL %s: ack after the cycle was dropped", where); errors++; end
      end
      open_cycle(); access('0, 1'b0, '0, 4'hF, 3'b000, FIRST_LAT); close_cycle();
    end
    if (errors == 0) $display("PASS xbus_mem FIRST_LAT=%0d BURST_LAT=%0d", FIRST_LAT, BURST_LAT);
    $finish;
  end
endmodule
