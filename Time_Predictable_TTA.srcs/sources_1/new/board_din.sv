`timescale 1ns / 1ps
// Board input interface for io.din (isa.md sec. 3, docs/io_interface.md).
//
// The core takes io.din as it is; this block makes the raw board inputs safe
// to use. Each input group has a 2-FF synchroniser and a debounce over the
// whole group, so a group changes all its bits in one cycle and only after
// they have been stable together for N_DB + 1 cycles:
//
//   group      pins     goes to
//   setpoint   sw[1:0]  din[1:0], copied when BTN0 is pressed (commit)
//   load       sw[2]    din[2] and the HIL load (live)
//   commit     btn0     its rising edge after debounce copies the setpoint group
//   (reserved) sw[3]    not used; din[31:3] = 0
//
// host/hil/board_din.py models this cycle for cycle and tests/test_board_din.py
// compares the two. Times there are counted from the first cycle after reset.
module board_din #(
  parameter int N_DB = 500_000
) (
  input  logic        clk,
  input  logic        rst,
  /* verilator lint_off UNUSEDSIGNAL */
  input  logic [3:0]  sw,                // sw[3] reserved
  /* verilator lint_on UNUSEDSIGNAL */
  input  logic        btn0,
  output logic [31:0] din,               // to io.din
  output logic        load_sw            // to the HIL load (same signal as din[2])
);
  logic [1:0] sp_db, sp;
  logic       load_db, btn_db, btn_prev;

  din_debounce #(.W(2), .N_DB(N_DB)) u_sp   (.clk, .rst, .pin(sw[1:0]), .out(sp_db));
  din_debounce #(.W(1), .N_DB(N_DB)) u_load (.clk, .rst, .pin(sw[2]),   .out(load_db));
  din_debounce #(.W(1), .N_DB(N_DB)) u_btn  (.clk, .rst, .pin(btn0),    .out(btn_db));

  always_ff @(posedge clk) begin
    if (rst) begin
      btn_prev <= 1'b0; sp <= '0;
    end else begin
      btn_prev <= btn_db;
      if (btn_db && !btn_prev) sp <= sp_db;       // commit on the press, once per press
    end
  end

  assign din     = {29'b0, load_db, sp};
  assign load_sw = load_db;
endmodule
