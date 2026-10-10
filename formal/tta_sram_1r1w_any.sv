// Formal stand-in for Time_Predictable_TTA.srcs/sources_1/new/tta_sram_1r1w.sv.
//
// Same module name, ports and read timing (rdata changes only on a read and
// holds otherwise), but every read returns an unconstrained word. The core
// uses it for both code and data memory, so the solver tries every program
// and every data value: a property that passes holds for all programs,
// including ones with illegal moves. Writes are ignored.
module tta_sram_1r1w #(
  parameter int    WORDS       = 4096,
  parameter string INIT_FILE   = "",
  parameter string SIM_PLUSARG = ""
) (
  input  logic                     clk,
  input  logic                     rst,
  input  logic                     re,
  input  logic [$clog2(WORDS)-1:0] raddr,
  output logic [31:0]              rdata,
  input  logic                     we,
  input  logic [$clog2(WORDS)-1:0] waddr,
  input  logic [31:0]              wdata
);
  // yosys-slang ignores this attribute: any_word is elaborated undriven and
  // sby's setundef -undriven -anyseq makes its one use below a fresh free
  // value every cycle (cover C8/C9 in tta_formal.sv check this). Keep it to
  // exactly one use: a second reader would get a different free value.
  (* anyseq *) logic [31:0] any_word;

  always_ff @(posedge clk) begin
    if (rst)     rdata <= '0;
    else if (re) rdata <= any_word;
  end
endmodule
