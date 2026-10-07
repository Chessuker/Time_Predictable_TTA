`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: 
// 
// Create Date: 10/06/2026 08:36:25 PM
// Design Name: 
// Module Name: tb_tta.sv
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

// Lockstep testbench: runs one program on tta_core and writes the trace of
// toolchain_formats.md sec. 6, record for record the same format as the ISS.
//
//   +code=<file>   code image (.code.hex)      +data=<file>  data image (.data.hex)
//   +stim=<file>   IO stimulus (.stim)         +trace=<file> trace output
//   +max=<n>       stop with an E record at cycle n (default 1,000,000)
//
// Stimulus is checked like the ISS does it: a port's cycles must strictly
// increase in file order, otherwise the run stops with an error.
// The timescale comes from the simulator command line (--timescale 1ns/1ps).
/* verilator lint_off UNUSEDSIGNAL */  // the testbench ignores some core outputs
module tb_tta;
  import tta_pkg::*;

  logic clk, rst;
  initial begin
    clk = 1'b0;
    forever #5 clk = ~clk;
  end

  logic [31:0] io_encoder, io_pwm_cmd, telem_tx_data;
  logic        telem_tx_start, halted;
  logic [63:0] now;
  logic        rv_valid, rv_imm, tr_valid, ht_valid;
  logic [31:0] rv_pc, rv_word, rv_value, tr_epc;
  logic [7:0]  rv_src, rv_dst;
  logic [2:0]  tr_cause;
  logic [1:0]  ht_reason;

  tta_core dut (
    .clk, .rst, .io_encoder, .io_pwm_cmd, .telem_tx_start, .telem_tx_data, .halted,
    .dbg_now(now), .rv_valid, .rv_pc, .rv_word, .rv_imm, .rv_src, .rv_dst, .rv_value,
    .tr_valid, .tr_epc, .tr_cause, .ht_valid, .ht_reason
  );

  // ---------------------------------------------------------------- stimulus
  longint      stim_cycle [$];
  logic [31:0] stim_value [$];

  task automatic load_stim(string path);
    int fd, n, line;
    longint c;
    longint v;
    string port;
    fd = $fopen(path, "r");
    if (fd == 0) begin $display("error: cannot open stim %s", path); $fatal(1); end
    line = 0;
    while (!$feof(fd)) begin
      n = $fscanf(fd, "%d %s %d\n", c, port, v);
      line++;
      if (n <= 0) continue;
      if (n != 3 || port != "io.encoder") begin
        $display("error: stim line %0d: expected '<cycle> io.encoder <value>'", line); $fatal(1);
      end
      if (c < 0 || (stim_cycle.size() > 0 && c <= stim_cycle[$])) begin
        $display("error: stim line %0d: io.encoder cycle %0d is not after the previous one", line, c);
        $fatal(1);
      end
      stim_cycle.push_back(c);
      stim_value.push_back(v[31:0]);
    end
    $fclose(fd);
  endtask

  always_comb begin
    io_encoder = '0;
    foreach (stim_cycle[i]) if (stim_cycle[i] <= longint'(now)) io_encoder = stim_value[i];
  end

  // ---------------------------------------------------------------- trace
  int     tf;
  longint max_cycles;

  initial begin
    string path;
    rst = 1'b1;
    if (!$value$plusargs("trace=%s", path)) path = "rtl.trace";
    tf = $fopen(path, "w");
    if (tf == 0) begin $display("error: cannot open trace %s", path); $fatal(1); end
    if (!$value$plusargs("max=%d", max_cycles)) max_cycles = 1_000_000;
    if ($value$plusargs("stim=%s", path)) load_stim(path);
    repeat (3) @(posedge clk);
    rst = 1'b0;                       // the next cycle is cycle 0
  end

  // Sample in the middle of each cycle, after the X-stage logic has settled.
  always @(negedge clk) if (!rst) begin
    if (longint'(now) >= max_cycles) begin
      $fwrite(tf, "E %0d maxcycles\n", max_cycles);
      $fclose(tf);
      $finish;
    end
    if (rv_valid) begin
      if (rv_imm) $fwrite(tf, "M %0d %08x %08x imm %02x %08x\n", now, rv_pc, rv_word, rv_dst, rv_value);
      else        $fwrite(tf, "M %0d %08x %08x %02x %02x %08x\n", now, rv_pc, rv_word, rv_src, rv_dst, rv_value);
    end
    if (tr_valid) $fwrite(tf, "T %0d %08x %0d\n", now, tr_epc, tr_cause);
    if (ht_valid) begin
      // A ternary of string literals would become a padded bit vector; a
      // string variable prints without padding.
      string reason;
      case (ht_reason)
        2'd0:    reason = "halt";
        2'd1:    reason = "nohandler";
        default: reason = "double";
      endcase
      $fwrite(tf, "H %0d %s\n", now, reason);
      $fclose(tf);
      $finish;
    end
  end
endmodule
/* verilator lint_on UNUSEDSIGNAL */
