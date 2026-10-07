// Synchronous SRAM, one read port and one write port, written so any tool
// infers its own memory (FPGA block RAM or an ASIC macro). No vendor primitive.
//
// Read: when re is high, rdata takes mem[raddr] at the clock edge and holds
// its value while re is low. That hold is what lets the pipeline stall
// (code memory) and keeps mem.data_out stable between loads (data memory).
module tta_sram_1r1w #(
  parameter int    WORDS       = 4096,
  parameter string INIT_FILE   = "",   // $readmemh image for synthesis/simulation
  parameter string SIM_PLUSARG = ""    // simulation only: +<name>=<file> overrides INIT_FILE
) (
  input  logic                     clk,
  input  logic                     rst,      // clears rdata only (spec: readable state is 0 after reset)
  input  logic                     re,
  input  logic [$clog2(WORDS)-1:0] raddr,
  output logic [31:0]              rdata,
  input  logic                     we,
  input  logic [$clog2(WORDS)-1:0] waddr,
  input  logic [31:0]              wdata
);
  logic [31:0] mem [WORDS];

  initial begin
    for (int i = 0; i < WORDS; i++) mem[i] = '0;
    if (INIT_FILE != "") $readmemh(INIT_FILE, mem);
`ifdef TTA_SIM
    begin
      string file;
      if (SIM_PLUSARG != "" && $value$plusargs({SIM_PLUSARG, "=%s"}, file))
        $readmemh(file, mem);
    end
`endif
  end

  always_ff @(posedge clk) begin
    if (we) mem[waddr] <= wdata;
    if (rst)     rdata <= '0;
    else if (re) rdata <= mem[raddr];
  end
endmodule
