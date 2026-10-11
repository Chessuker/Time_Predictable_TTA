`timescale 1ns / 1ps
// xbus_hil on its own: timer, register semantics and the timing monitor, with
// every bus access strobed at a chosen cycle so the expected timestamps are
// known exactly. The monitor's UART line is decoded and every record is
// compared word by word with the one this bench predicts. Prints PASS/FAIL.
//
// Cycle t = cycles since reset = the DUT's mtime.
module tb_xbus_hil;
  localparam int CPW = 1320;                          // UART: 40 bits x 33 clocks
  localparam longint CPB = longint'(CPW) / 40;
  localparam logic [31:0] MAGIC = 32'h4E52_5631, NONE = 32'hFFFF_FFFF;

  logic clk, rst;
  initial clk = 1'b0;
  always #5 clk = ~clk;

  logic [3:0]  adr;
  logic [31:0] dat_i, dat_o, pwm_cmd, encoder;
  logic        we, stb, cyc, ack, irq_mti, mon_en, mon_txd;

  xbus_hil dut (
    .clk, .rst, .adr, .dat_i, .we, .stb, .cyc, .dat_o, .ack, .irq_mti,
    .encoder, .din(32'h0000_00A5), .pwm_cmd, .mon_en, .mon_txd
  );

  longint t;
  always_ff @(posedge clk) t <= rst ? 0 : t + 1;
  assign encoder = 32'(t * 3 + 7);                    // a different value every cycle

  int errors = 0;
  task automatic fail(string msg);
    $display("FAIL %s", msg);
    errors++;
  endtask

  // strobe in cycle c (all signals change at the falling edge before it)
  task automatic access(longint c, logic [3:0] a, logic w, logic [31:0] d, output logic [31:0] q);
    while (t < c - 1) @(negedge clk);
    if (t != c - 1) fail($sformatf("bench late for cycle %0d", c));
    @(negedge clk);                                   // now in cycle c
    adr = a; we = w; dat_i = d; stb = 1'b1; cyc = 1'b1;
    @(negedge clk);                                   // cycle c + 1: ack
    stb = 1'b0;
    if (!ack) fail($sformatf("no ack in cycle %0d", c + 1));
    q = dat_o;
    cyc = 1'b0;
  endtask
  task automatic wr(longint c, logic [3:0] a, logic [31:0] d);
    /* verilator lint_off UNUSEDSIGNAL */
    logic [31:0] q;
    /* verilator lint_on UNUSEDSIGNAL */
    access(c, a, 1'b1, d, q);
  endtask
  task automatic rd(longint c, logic [3:0] a, output logic [31:0] q);
    access(c, a, 1'b0, '0, q);
  endtask

  // ---------------------------------------------------------------- UART decode
  logic [31:0] got [$];
  initial begin : decode
    logic prev;
    prev = 1'b1;
    forever begin
      @(negedge clk);
      if (!rst && prev && !mon_txd) begin
        longint      s;
        logic [39:0] f;
        logic [31:0] w;
        s = t;
        for (int b = 0; b < 40; b++) begin
          while (t < s + longint'(b) * CPB + CPB / 2) @(negedge clk);
          f[b] = mon_txd;
        end
        for (int k = 0; k < 4; k++) w[k * 8 +: 8] = f[k * 10 + 1 +: 8];
        got.push_back(w);
      end
      prev = mon_txd;
    end
  end

  logic [31:0] exp_w [$];
  task automatic expect_record(int k, longint rel, logic [31:0] ds, logic [31:0] dse, logic [31:0] da,
                               logic [31:0] enc, logic [31:0] u, logic [31:0] flags);
    exp_w.push_back(MAGIC); exp_w.push_back(32'(k));
    exp_w.push_back(rel[31:0]); exp_w.push_back(rel[63:32]);
    exp_w.push_back(ds); exp_w.push_back(dse); exp_w.push_back(da);
    exp_w.push_back(enc); exp_w.push_back(u); exp_w.push_back(flags);
  endtask

  initial begin
    logic [31:0] q;
    stb = 1'b0; cyc = 1'b0; we = 1'b0; adr = '0; dat_i = '0;
    rst = 1'b1;
    repeat (3) @(posedge clk);
    @(negedge clk);
    rst = 1'b0;

    // registers
    rd(10, 4'd0, q);   if (q != 32'd10) fail($sformatf("MTIME_LO %0d at 10", q));
    rd(12, 4'd1, q);   if (q != 32'd0)  fail("MTIME_HI");
    rd(14, 4'd6, q);   if (q != 32'hA5) fail("DIN");
    rd(16, 4'd4, q);   if (q != 32'(16 * 3 + 7)) fail($sformatf("ENCODER %0d: not the value of the strobe cycle", q));
    rd(18, 4'd2, q);   if (q != NONE) fail("MTIMECMP resets to all ones");
    if (irq_mti) fail("irq before mtimecmp is set");
    // PWM_CMD: written in the strobe cycle, seen by the plant from the next one
    wr(20, 4'd5, 32'd123);
    if (pwm_cmd != 32'd123) fail("pwm_cmd after the write");

    // timer: release in the first cycle with mtime >= mtimecmp, irq one cycle later
    wr(30, 4'd3, 32'd0);
    wr(32, 4'd2, 32'd100);
    while (t < 100) begin
      @(negedge clk);
      if (irq_mti && t <= 100) fail($sformatf("irq at %0d", t));
    end
    @(negedge clk);                                   // t = 101
    if (!irq_mti) fail("no irq at 101");

    // monitor on: records start at the next release
    wr(110, 4'd8, 32'd1);
    rd(112, 4'd8, q); if (q != 32'd1 || !mon_en) fail("MON_CTRL");
    // period 0: release 200, start 203, sense 205, act 1201
    wr(150, 4'd2, 32'd200);
    wr(203, 4'd7, 32'd0);
    rd(205, 4'd4, q);
    wr(1201, 4'd5, 32'hFFFF_FF00);
    // period 1: release 3000; sense twice (extra), no act, an ACT in its release cycle belongs to it
    wr(1300, 4'd2, 32'd3000);
    expect_record(0, 200, 3, 5, 1001, 32'(205 * 3 + 7), 32'hFFFF_FF00, 32'b0_0111);
    wr(3000, 4'd5, 32'd77);                           // the release cycle itself: offset 0
    rd(3004, 4'd4, q);
    rd(3010, 4'd4, q);
    // period 2: release 6000; only START; then period 3 at 9000 closes it
    wr(3100, 4'd2, 32'd6000);
    expect_record(1, 3000, NONE, 4, 0, 32'(3004 * 3 + 7), 32'd77, 32'b0_1110);
    wr(6001, 4'd7, 32'd0);
    wr(6100, 4'd2, 32'd9000);
    expect_record(2, 6000, 1, NONE, NONE, 32'd0, 32'd0, 32'b0_0001);
    // a late act (deadline is the host's call): act at release + 2500
    wr(9100, 4'd7, 32'd0);
    rd(9102, 4'd4, q);                                // each access takes two cycles
    wr(11500, 4'd5, 32'd5);
    wr(11600, 4'd2, 32'd20000);
    expect_record(3, 9000, 100, 102, 2500, 32'(9102 * 3 + 7), 32'd5, 32'b0_0111);
    // all records share one UART line (1320 cycles a word): wait for every word
    while (got.size() < exp_w.size() && t < 3000 + (longint'(exp_w.size()) + 5) * longint'(CPW)) @(negedge clk);

    if (got.size() != exp_w.size()) fail($sformatf("%0d words, expected %0d", got.size(), exp_w.size()));
    if (got.size() != exp_w.size()) foreach (got[i]) $display("  got %0d: %08x", i, got[i]);
    foreach (exp_w[i]) if (i < got.size() && got[i] != exp_w[i])
      fail($sformatf("word %0d (record %0d field %0d): %08x, expected %08x", i, i / 10, i % 10, got[i], exp_w[i]));
    rd(longint'(t) + 2, 4'd9, q); if (q != 32'd0) fail("drops");
    if (errors == 0) $display("PASS xbus_hil");
    $finish;
  end
endmodule
