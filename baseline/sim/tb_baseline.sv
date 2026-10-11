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
  // +bus_end=<last>: stop the bus trace after that cycle
  longint bus_from = -1;
  longint bus_end = 64'h7FFF_FFFF_FFFF_FFFF;
  longint bus_no;
  logic   cyc_q;
  bit     bus_h;
  initial begin
    void'($value$plusargs("bus=%d", bus_from));
    void'($value$plusargs("bus_end=%d", bus_end));
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
      if (bus_from >= 0 && cyc_n >= bus_from && cyc_n <= bus_end && dut.stb && (!bus_h || dut.hit == 2'd2))
        $display("B %0d %0d %s %0b %0d %08x %s", cyc_n, n,
                 dut.hit == 2'd0 ? "C" : dut.hit == 2'd1 ? "D" : dut.hit == 2'd2 ? "H" : "E",
                 dut.we, dut.cti, dut.adr, dut.tag[2] ? "I" : "D");
    end
  end

  // Instruction trace, compiled with -DITRACE against the netlist converted with
  // TRACE_EN = 1 (docs/baseline_measurement.md 3.3); +itrace prints, from the
  // CPU's trace port (the outputs of neorv32_top_inst), per retired instruction
  //   I M <cycle> <mip read value>          a CSR read of mip (the polling loop)
  //   I C <cycle> <pc> <csr> <cmd> <old>    a CSR instruction on mstatus (old = value read)
  //   I S <cycle> <adr>                     a store into [+tr_data=lo:hi)
  //   I K <cycle> <pc>                      an instruction at lo + 32 i in [+tr_code=lo:hi) (piece starts)
  //   I T <cycle> <pc>                      the first instruction of a trap handler
  // <cycle> is the cycle in which the trace port reports the retirement.
`ifdef ITRACE
  bit          itrace;
  logic [31:0] tr_data_lo, tr_data_hi, tr_code_lo, tr_code_hi;
  initial begin
    string arg;
    tr_data_lo = '0; tr_data_hi = '0; tr_code_lo = '0; tr_code_hi = '0;
    itrace = $test$plusargs("itrace");
    if ($value$plusargs("tr_data=%s", arg)) void'($sscanf(arg, "%h:%h", tr_data_lo, tr_data_hi));
    if ($value$plusargs("tr_code=%s", arg)) void'($sscanf(arg, "%h:%h", tr_code_lo, tr_code_hi));
  end
  /* verilator lint_off UNUSEDSIGNAL */
  logic        t_valid, t_intr;
  logic [31:0] t_pc, t_cmd, t_maddr, t_csr_r;
  logic [11:0] t_csr;
  logic [3:0]  t_wmask;
  /* verilator lint_on UNUSEDSIGNAL */
  assign t_valid = dut.u_cpu.neorv32_top_inst.\trace_cpu0_o[valid] ;
  assign t_intr  = dut.u_cpu.neorv32_top_inst.\trace_cpu0_o[intr] ;
  assign t_pc    = dut.u_cpu.neorv32_top_inst.\trace_cpu0_o[pc_rdata] ;
  assign t_cmd   = dut.u_cpu.neorv32_top_inst.\trace_cpu0_o[cmd32] ;
  assign t_maddr = dut.u_cpu.neorv32_top_inst.\trace_cpu0_o[mem_addr] ;
  assign t_wmask = dut.u_cpu.neorv32_top_inst.\trace_cpu0_o[mem_wmask] ;
  assign t_csr   = dut.u_cpu.neorv32_top_inst.\trace_cpu0_o[csr_addr] ;
  assign t_csr_r = dut.u_cpu.neorv32_top_inst.\trace_cpu0_o[csr_rdata] ;
  logic t_is_csr;
  assign t_is_csr = t_cmd[6:0] == 7'h73 && t_cmd[14:12] != 3'b000;
  always @(negedge clk) begin
    if (itrace && !rst && t_valid) begin
      if (t_intr) $display("I T %0d %08x", cyc_n, t_pc);
      if (t_is_csr && t_csr == 12'h344) $display("I M %0d %08x", cyc_n, t_csr_r);
      else if (t_is_csr && t_csr == 12'h300) $display("I C %0d %08x %03x %08x %08x", cyc_n, t_pc, t_csr, t_cmd, t_csr_r);
      if (t_wmask != '0 && t_maddr >= tr_data_lo && t_maddr < tr_data_hi) $display("I S %0d %08x", cyc_n, t_maddr);
      if (t_pc >= tr_code_lo && t_pc < tr_code_hi && t_pc[4:0] == tr_code_lo[4:0]) $display("I K %0d %08x", cyc_n, t_pc);
    end
  end
`endif

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
