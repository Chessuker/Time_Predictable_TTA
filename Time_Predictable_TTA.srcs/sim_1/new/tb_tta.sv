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
`ifdef TTA_HIL
  parameter int LOAD_PERIOD_C = 2_000_000;
  parameter int LOAD_ON_C     = 600_000;
  parameter int LOAD_OFF_C    = 1_100_000;
`endif

  logic clk, rst;
  initial begin
    clk = 1'b0;
    forever #5 clk = ~clk;
  end

  logic [31:0] io_encoder, io_din, io_pwm_cmd, telem_tx_data;
  logic        telem_tx_start, halted;
  logic [63:0] now;
  logic        rv_valid, rv_imm, tr_valid, ht_valid;
  logic [31:0] rv_pc, rv_word, rv_value, tr_epc;
  logic [7:0]  rv_src, rv_dst;
  logic [2:0]  tr_cause;
  logic [1:0]  ht_reason;

  tta_core dut (
    .clk, .rst, .io_encoder, .io_din, .io_pwm_cmd, .telem_tx_start, .telem_tx_data, .halted,
    .dbg_now(now), .rv_valid, .rv_pc, .rv_word, .rv_imm, .rv_src, .rv_dst, .rv_value,
    .tr_valid, .tr_epc, .tr_cause, .ht_valid, .ht_reason
  );

  // ---------------------------------------------------------------- stimulus
  // one list per input port; cycles of one port strictly increase (toolchain_formats.md sec. 6.4)
  longint      enc_cycle [$], din_cycle [$];
  logic [31:0] enc_value [$], din_value [$];

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
      if (n != 3 || (port != "io.encoder" && port != "io.din")) begin
        $display("error: stim line %0d: expected '<cycle> <input port> <value>'", line); $fatal(1);
      end
      if (c < 0 || (port == "io.encoder" && enc_cycle.size() > 0 && c <= enc_cycle[$])
                || (port == "io.din" && din_cycle.size() > 0 && c <= din_cycle[$])) begin
        $display("error: stim line %0d: %s cycle %0d is not after the previous one", line, port, c);
        $fatal(1);
      end
      if (port == "io.encoder") begin
        enc_cycle.push_back(c);
        enc_value.push_back(v[31:0]);
      end else begin
        din_cycle.push_back(c);
        din_value.push_back(v[31:0]);
      end
    end
    $fclose(fd);
  endtask

`ifdef TTA_HIL
  // closed loop: io.encoder comes from the plant (host/lockstep/runner.py run_hil);
  // the load window is shortened so it falls inside a short simulation, and
  // io.din[2] (from the stimulus) is the load switch as on the board
  /* verilator lint_off UNUSEDSIGNAL */
  logic load_on;
  /* verilator lint_on UNUSEDSIGNAL */
  hil_env #(.LOAD_PERIOD_C(LOAD_PERIOD_C), .LOAD_ON_C(LOAD_ON_C), .LOAD_OFF_C(LOAD_OFF_C)) u_env (
    .clk, .rst, .pwm_cmd(io_pwm_cmd), .load_sw(io_din[2]), .encoder(io_encoder), .load_on
  );
`else
  always_comb begin
    io_encoder = '0;
    foreach (enc_cycle[i]) if (enc_cycle[i] <= longint'(now)) io_encoder = enc_value[i];
  end
`endif

  // io.din comes from the stimulus in both builds (the board's synchroniser and
  // debounce are outside the core and have their own testbench)
  always_comb begin
    io_din = '0;
    foreach (din_cycle[i]) if (din_cycle[i] <= longint'(now)) io_din = din_value[i];
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
