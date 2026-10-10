`timescale 1ns / 1ps
// One debounced input group of the board input interface (board_din.sv):
// a 2-FF synchroniser per bit, then the group changes all its bits in one
// cycle once they have been stable together for N_DB + 1 cycles:
//
//   s2(t) != cand(t):          cand <= s2, cnt <= 0
//   else if cnt == N_DB - 1:   out <= cand      (cnt stays)
//   else:                      cnt <= cnt + 1
//
// A pin value held from cycle p for at least N_DB + 1 cycles reaches out at
// p + N_DB + 3 (one cycle later if the asynchronous edge resolves late); out
// holds each value for at least N_DB + 1 cycles. host/hil/board_din.py is the
// cycle-exact model.
module din_debounce #(
  parameter int W    = 1,
  parameter int N_DB = 500_000          // see above
) (
  input  logic         clk,
  input  logic         rst,
  input  logic [W-1:0] pin,              // asynchronous
  output logic [W-1:0] out
);
  localparam int CW = (N_DB > 1) ? $clog2(N_DB) : 1;

  (* ASYNC_REG = "TRUE" *) logic [W-1:0] s1, s2;
  logic [W-1:0]  cand;
  logic [CW-1:0] cnt;

  always_ff @(posedge clk) begin
    if (rst) begin
      s1 <= '0; s2 <= '0; cand <= '0; cnt <= '0; out <= '0;
    end else begin
      s1 <= pin;
      s2 <= s1;
      if (s2 != cand) begin
        cand <= s2;
        cnt  <= '0;
      end else if (cnt == CW'(N_DB - 1)) begin
        out <= cand;
      end else begin
        cnt <= cnt + 1'b1;
      end
    end
  end
endmodule
