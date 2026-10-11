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
