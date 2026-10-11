`timescale 1ns / 1ps
// uart_break_reset with N_BRK = 20: brk exactly N_BRK cycles after the line went
// low, never for a shorter low stretch, and released the cycle after it is high.
module tb_uart_break_reset;
  localparam int N = 20;
  // rxd changes right after a negedge, so it is the value for the next posedge
  logic clk, rxd, brk;
  initial clk = 1'b0;
  always #5 clk = ~clk;
  uart_break_reset #(.N_BRK(N)) dut (.clk, .rxd, .brk);

  int cyc;
  initial cyc = 0;
  always @(posedge clk) cyc <= cyc + 1;
  int errors = 0;

  task automatic expect_brk(logic v, string what);
    if (brk !== v) begin
      $display("FAIL %s: brk=%b at cycle %0d", what, brk, cyc);
      errors++;
    end
  endtask

  initial begin
    rxd = 1'b1;
    repeat (3) @(negedge clk);
    // a low stretch of N - 1 cycles: never a break
    rxd = 1'b0;
    for (int i = 0; i < N - 1; i++) begin
      expect_brk(1'b0, "short low");
      @(negedge clk);
    end
    rxd = 1'b1;
    for (int i = 0; i < 5; i++) begin
      expect_brk(1'b0, "after short low");
      @(negedge clk);
    end
    // low for N cycles and more: brk from the (N+1)-th cycle on, while low
    rxd = 1'b0;
    for (int i = 0; i < N; i++) begin
      expect_brk(1'b0, "before N");
      @(negedge clk);
    end
    for (int i = 0; i < 50; i++) begin
      expect_brk(1'b1, "after N");
      @(negedge clk);
    end
    // high again: the cycle itself still shows brk, the next does not
    rxd = 1'b1;
    expect_brk(1'b1, "release cycle");
    @(negedge clk);
    for (int i = 0; i < 5; i++) begin
      expect_brk(1'b0, "released");
      @(negedge clk);
    end
    // a one-cycle high glitch restarts the count
    rxd = 1'b0;
    repeat (N - 5) @(negedge clk);
    rxd = 1'b1;
    @(negedge clk);
    rxd = 1'b0;
    for (int i = 0; i < N; i++) begin
      expect_brk(1'b0, "after glitch");
      @(negedge clk);
    end
    expect_brk(1'b1, "N after glitch");
    if (errors == 0) $display("PASS uart_break_reset");
    $finish;
  end
endmodule
