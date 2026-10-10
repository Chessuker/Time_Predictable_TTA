`timescale 1ns / 1ps
// Unit testbench for dc_motor_plant: drives u and tau from a stimulus file,
// one line per plant step ("u tau", decimal), and logs the state after every
// step as "S <cycle> <u> <tau> <theta_q> <omega_q> <encoder>". tests/test_plant.py
// replays the same inputs through host/plant_model FixedPlant and compares.
//
// Plusargs: +stim=<file> +log=<file>. The step period is shortened with
// -GSTEP_CYCLES (the arithmetic does not depend on it). +define+PLANT_MC tests
// the multi-cycle plant instead of the single-cycle one.
module tb_plant;
  parameter int STEP_CYCLES = 8;
  parameter int PHASE       = 3;

  logic clk, rst;
  logic signed [31:0] u, tau, encoder;
  logic stepped;
  logic signed [47:0] theta_q;
  logic signed [39:0] omega_q;
  longint cyc;

`ifdef PLANT_MC
  dc_motor_plant_mc #(.STEP_CYCLES(STEP_CYCLES), .PHASE(PHASE)) dut (
`else
  dc_motor_plant #(.STEP_CYCLES(STEP_CYCLES), .PHASE(PHASE)) dut (
`endif
    .clk, .rst, .u, .tau, .encoder, .stepped, .theta_q, .omega_q
  );

  initial begin
    clk = 1'b0;
    forever #5 clk = ~clk;
  end

  initial begin
    string stim_name, log_name;
    int fs, fl, n;
    int uu, tt;
    if (!$value$plusargs("stim=%s", stim_name) || !$value$plusargs("log=%s", log_name)) begin
      $display("FAIL need +stim= and +log=");
      $finish;
    end
    fs = $fopen(stim_name, "r");
    fl = $fopen(log_name, "w");
    rst = 1'b1; u = '0; tau = '0; cyc = 0;
    @(negedge clk); @(negedge clk);
    rst = 1'b0;
    forever begin
      // inputs for the next step are set on the falling edge before it
      n = $fscanf(fs, "%d %d\n", uu, tt);
      if (n != 2) break;
      u = uu;
      tau = tt;
      do begin
        @(negedge clk);
        cyc++;
      end while (!stepped);
      $fdisplay(fl, "S %0d %0d %0d %0d %0d %0d", cyc, u, tau, theta_q, omega_q, encoder);
    end
    $fclose(fs);
    $fclose(fl);
    $display("END");
    $finish;
  end
endmodule
