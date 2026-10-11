`timescale 1ns / 1ps
// Main memory of the Phase 6 baseline on the NEORV32 XBUS (pipelined Wishbone,
// docs/datasheet/soc_xbus.adoc of NEORV32 v1.13.6).
//
// A block RAM made to answer like a slower memory: the first access of a bus
// cycle (cyc rising) is acknowledged FIRST_LAT cycles after its strobe, every
// further access in the same cycle (the rest of a cache-line burst, read or
// write) BURST_LAT cycles after its strobe. So with the caches off every
// fetch and load pays FIRST_LAT; with them on, a line fill of 16 words costs
// FIRST_LAT + 15 * BURST_LAT.
//
// The XBUS has one request in flight at a time, and pipelines: the next strobe
// may come in the same cycle as the acknowledge of the previous request (the
// CPU does this inside a burst), or later. If cyc drops while a request is
// pending (bus timeout), the request is dropped. A write happens in the
// cycle of its strobe, byte by byte as sel says; a read returns the word as it
// was in the cycle of its strobe.
module xbus_mem #(
  parameter int WORDS     = 16384,      // 64 KB
  parameter int FIRST_LAT = 10,         // strobe -> ack, first access of a cycle (>= 1)
  parameter int BURST_LAT = 1,          // strobe -> ack, further accesses (>= 1)
  parameter string SIM_PLUSARG = ""     // simulation only: +<name>=<file> preloads it ($readmemh)
) (
  input  logic                     clk,
  input  logic                     rst,
  input  logic [$clog2(WORDS)-1:0] adr,  // word address
  input  logic [31:0]              dat_i,
  input  logic                     we,
  input  logic [3:0]               sel,
  input  logic                     stb,  // already qualified by the address decoder
  input  logic                     cyc,
  output logic [31:0]              dat_o,
  output logic                     ack
);
  localparam int LW = $clog2((FIRST_LAT > BURST_LAT ? FIRST_LAT : BURST_LAT) + 1);

  logic [31:0] mem [WORDS];
  logic [31:0] rdata;
  logic        busy, in_cyc, accept;
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
      busy <= 1'b0; in_cyc <= 1'b0; wait_n <= '0;
    end else if (accept) begin
      busy   <= 1'b1;
      in_cyc <= 1'b1;
      wait_n <= LW'((in_cyc ? BURST_LAT : FIRST_LAT) - 1);
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
