`timescale 1ns / 1ps
// Main memory of the Phase 6 baseline on the NEORV32 XBUS (pipelined Wishbone,
// docs/datasheet/soc_xbus.adoc of NEORV32 v1.13.6).
//
// A block RAM made to answer like a slower memory: every transaction pays
// FIRST_LAT from the strobe of its first access to the acknowledge, and only
// the further words of a burst pay BURST_LAT. A transaction is one single
// access, or one cache-line burst: NEORV32 marks every word of a burst except
// the last with cti = 010 (incrementing burst) and the last with 000. So an
// access continues a burst exactly when the previous access this memory took
// in the same bus cycle had cti = 010. With the caches off every fetch and load
// pays FIRST_LAT; with them on, a line fill of 16 words costs
// FIRST_LAT + 15 * BURST_LAT, including a fill that starts in the acknowledge
// cycle of the previous one.
//
// cyc alone cannot tell this: the XBUS keeps cyc high while it is pending and
// raises the next strobe in the acknowledge cycle, so separate single accesses
// often share one unbroken cyc (until 2026-10, when this memory went by cyc,
// such an access was served at BURST_LAT; audit A5-1).
//
// The XBUS has one request in flight at a time, and pipelines: the next strobe
// may come in the same cycle as the acknowledge of the previous request (the
// CPU does this inside a burst), or later. If cyc drops while a request is
// pending (bus timeout), the request is dropped. A write happens in the
// cycle of its strobe, byte by byte as sel says; a read returns the word as it
// was in the cycle of its strobe.
module xbus_mem #(
  parameter int WORDS     = 16384,      // 64 KB
  parameter int FIRST_LAT = 10,         // strobe -> ack, first access of a transaction (>= 1)
  parameter int BURST_LAT = 1,          // strobe -> ack, further words of a burst (>= 1)
  parameter string SIM_PLUSARG = ""     // simulation only: +<name>=<file> preloads it ($readmemh)
) (
  input  logic                     clk,
  input  logic                     rst,
  input  logic [$clog2(WORDS)-1:0] adr,  // word address
  input  logic [31:0]              dat_i,
  input  logic                     we,
  input  logic [3:0]               sel,
  input  logic [2:0]               cti,  // 010: more words of this burst follow
  input  logic                     stb,  // already qualified by the address decoder
  input  logic                     cyc,
  output logic [31:0]              dat_o,
  output logic                     ack
);
  localparam int LW = $clog2((FIRST_LAT > BURST_LAT ? FIRST_LAT : BURST_LAT) + 1);

  logic [31:0] mem [WORDS];
  logic [31:0] rdata;
  logic        busy, cont, accept;    // cont: the next access continues a burst
  logic [LW-1:0] wait_n;               // cycles left until ack

  assign ack    = busy && (wait_n == '0);
  assign accept = stb && (!busy || ack);

  // block RAM: one read port, one byte-enable write port, both at the strobe
  always_ff @(posedge clk) begin
    if (accept) begin
      rdata <= mem[adr];
      if (we) for (int b = 0; b < 4; b++) if (sel[b]) mem[adr][8*b +: 8] <= dat_i[8*b +: 8];
    end
  end

  always_ff @(posedge clk) begin
    if (rst || !cyc) begin
      busy <= 1'b0; cont <= 1'b0; wait_n <= '0;
    end else if (accept) begin
      busy   <= 1'b1;
      cont   <= cti == 3'b010;
      wait_n <= LW'((cont ? BURST_LAT : FIRST_LAT) - 1);
    end else if (busy) begin
      if (wait_n == '0) busy <= 1'b0;
      else              wait_n <= wait_n - 1'b1;
    end
  end

  assign dat_o = rdata;

`ifndef SYNTHESIS
  initial begin
    string path;
    if (SIM_PLUSARG != "" && $value$plusargs({SIM_PLUSARG, "=%s"}, path)) $readmemh(path, mem);
  end
  // the bus never strobes again before the acknowledge cycle
  always_ff @(posedge clk) if (!rst && stb && !accept) $fatal(1, "xbus_mem: strobe while busy");
`endif
endmodule
