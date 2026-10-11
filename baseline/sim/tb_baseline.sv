`timescale 1ns / 1ps
// Whole-system simulation of the Phase 6 baseline: baseline_soc with NEORV32
// converted to Verilog by GHDL (baseline/sim/convert.sh, BOOT_MODE = 1 so it
// starts at 0x0 without the bootloader), the program preloaded into the code
// memory. Decodes the timing monitor's UART line and prints, per 32-bit word,
//   W <cycle> <word>        cycle = cycles since reset when the start bit begins
// and at the end "END". Plusargs: +code=<hex> +max=<cycles>
module tb_baseline;
  parameter int FIRST_LAT = 10;
  parameter int BURST_LAT = 1;
  localparam longint CPB = 33;           // clocks per bit, as xbus_hil

  logic clk, rst;
  initial clk = 1'b0;
  always #5 clk = ~clk;

  /* verilator lint_off UNUSEDSIGNAL */
  logic        uart0_txd, mon_en, load_on;
  logic [31:0] pwm_cmd, encoder;
  /* verilator lint_on UNUSEDSIGNAL */
  logic        mon_txd;

  baseline_soc #(.FIRST_LAT(FIRST_LAT), .BURST_LAT(BURST_LAT)) dut (
    .clk, .rst, .uart0_rxd(1'b1), .uart0_txd, .mon_en, .mon_txd, .pwm_cmd, .encoder, .load_on
  );

  longint cyc_n;                          // cycles since reset (= xbus_hil mtime)
  always_ff @(posedge clk) cyc_n <= rst ? 0 : cyc_n + 1;

  task automatic wait_cyc(longint c);
    while (cyc_n < c) @(negedge clk);
  endtask

  // +trace=<first>:<last> prints the XBUS (stb/ack/err/cyc) between those cycles
  longint tr_a = -1, tr_b = -1;
  initial begin
    string arg;
    if ($value$plusargs("trace=%s", arg)) void'($sscanf(arg, "%d:%d", tr_a, tr_b));
  end
  always @(negedge clk) begin
    if (cyc_n >= tr_a && cyc_n <= tr_b && (dut.stb || dut.ack || dut.err || dut.cyc))
      $display("X %0d cyc=%0b stb=%0b adr=%08x we=%0b cti=%0d ack=%0b err=%0b last=%0d busy_c=%0b",
               cyc_n, dut.cyc, dut.stb, dut.adr, dut.we, dut.cti, dut.ack, dut.err, dut.last,
               dut.u_code.busy);
  end

  // +bus=<first> prints every XBUS request accepted by a slave from that cycle on:
  //   B <cycle> <bus cycle no.> <C|D|H|E> <we> <cti> <adr> <I|D>
  // target slave, cti 2 = burst (the last word of a burst has 0), last field:
  // instruction fetch or data access (xbus_tag_o(2)). A bus cycle is one
  // stretch of cyc high; with the caches off it can hold several single accesses.
  // +bus_h: only the xbus_hil requests (small traces over many periods)
  longint bus_from = -1;
  longint bus_no;
  logic   cyc_q;
  bit     bus_h;
  initial begin
    void'($value$plusargs("bus=%d", bus_from));
    bus_h = $test$plusargs("bus_h");
  end
  always @(negedge clk) begin
    if (rst) begin
      bus_no <= 0;
      cyc_q  <= 1'b0;
    end else begin
      automatic longint n = bus_no + longint'(dut.cyc && !cyc_q);
      bus_no <= n;
      cyc_q  <= dut.cyc;
      if (bus_from >= 0 && cyc_n >= bus_from && dut.stb && (!bus_h || dut.hit == 2'd2))
        $display("B %0d %0d %s %0b %0d %08x %s", cyc_n, n,
                 dut.hit == 2'd0 ? "C" : dut.hit == 2'd1 ? "D" : dut.hit == 2'd2 ? "H" : "E",
                 dut.we, dut.cti, dut.adr, dut.tag[2] ? "I" : "D");
    end
  end

  int errors = 0;
  initial begin : decode
    logic prev;
    prev = 1'b1;
    forever begin
      @(negedge clk);
      if (!rst && prev && !mon_txd) begin
        longint      s;
        logic [39:0] f;
        logic [31:0] w;
        s = cyc_n;
        for (int b = 0; b < 40; b++) begin
          wait_cyc(s + longint'(b) * CPB + CPB / 2);
          f[b] = mon_txd;
        end
        for (int k = 0; k < 4; k++) begin
          if (f[k * 10] !== 1'b0 || f[k * 10 + 9] !== 1'b1) begin
            $display("FAIL framing at %0d", s);
            errors++;
          end
          w[k * 8 +: 8] = f[k * 10 + 1 +: 8];
        end
        $display("W %0d %08x", s, w);
      end
      prev = mon_txd;
    end
  end

  initial begin
    longint max_cycles;
    if (!$value$plusargs("max=%d", max_cycles)) max_cycles = 200_000;
    rst = 1'b1;
    repeat (5) @(posedge clk);
    @(negedge clk);
    rst = 1'b0;
    wait_cyc(max_cycles);
    if (errors == 0) $display("END");
    $finish;
  end
endmodule
