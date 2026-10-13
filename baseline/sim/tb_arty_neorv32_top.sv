`timescale 1ns / 1ps
// The board top level with the converted NEORV32 (simulation path of
// baseline_soc) and a program that switches the monitor on (led[0]): the UART
// break reset at the pins. N_BRK is shortened to 2000 cycles.
//
//   1. after configuration the program starts and led[0] goes on
//   2. RX low for N_BRK - 1 cycles, twice: nothing happens
//   3. RX low for 3 x N_BRK: led[0] goes off exactly N_BRK + 5 cycles after RX
//      went low (2 synchroniser stages, the N_BRK count, rst, mon_en, the led
//      register) and stays off until RX is high again; then the
//      program starts again and switches led[0] on
//   4. the RESET button does the same
// +code=<hex> (a program that writes MON_CTRL = 1)
module tb_arty_neorv32_top;
  localparam int N_BRK = 2000;
  logic clk, ck_rst, rxd;
  /* verilator lint_off UNUSEDSIGNAL */
  logic txd;
  logic [3:0] led;                      // led[0]: monitor on, i.e. the program has started
  /* verilator lint_on UNUSEDSIGNAL */
  initial clk = 1'b0;
  always #5 clk = ~clk;

  arty_neorv32_top #(.N_BRK(N_BRK)) dut (
    .CLK100MHZ(clk), .ck_rst, .uart_txd_in(rxd), .uart_rxd_out(txd), .led
  );

  int cyc;
  initial cyc = 0;
  always @(posedge clk) cyc <= cyc + 1;
  int errors = 0;

  task automatic fail(string what);
    $display("FAIL %s at cycle %0d", what, cyc);
    errors++;
  endtask

  // wait until led[0] is on, at most `limit` cycles
  task automatic wait_led(int limit, string what);
    int t0 = cyc;
    while (!led[0] && cyc - t0 < limit) @(negedge clk);
    if (!led[0]) fail(what);
  endtask

  initial begin
    int t_low;
    ck_rst = 1'b1;
    rxd = 1'b1;
    // 1
    wait_led(200_000, "program start after configuration");
    $display("led on at %0d", cyc);
    // 2: two low stretches just short of a break
    repeat (2) begin
      rxd = 1'b0;
      repeat (N_BRK - 1) begin
        @(negedge clk);
        if (!led[0]) fail("reset by a short low stretch");
      end
      rxd = 1'b1;
      repeat (100) begin
        @(negedge clk);
        if (!led[0]) fail("reset after a short low stretch");
      end
    end
    // 3: a break
    rxd = 1'b0;
    t_low = cyc;
    while (led[0] && cyc - t_low < N_BRK + 10) @(negedge clk);
    if (led[0]) fail("no reset by the break");
    else if (cyc - t_low != N_BRK + 5) fail("reset not N_BRK + 5 cycles after RX went low");
    $display("break: led off %0d cycles after RX went low", cyc - t_low);
    repeat (2 * N_BRK) begin
      @(negedge clk);
      if (led[0]) fail("led on while the break lasts");
    end
    rxd = 1'b1;
    wait_led(200_000, "program start after the break");
    $display("led on again %0d cycles after the break", cyc - t_low);
    // 4: the RESET button
    ck_rst = 1'b0;
    repeat (10) @(negedge clk);
    if (led[0]) fail("no reset by the button");
    ck_rst = 1'b1;
    wait_led(200_000, "program start after the button");
    if (errors == 0) $display("PASS arty_neorv32_top");
    $finish;
  end
endmodule
