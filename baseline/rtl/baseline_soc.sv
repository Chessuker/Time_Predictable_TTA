`timescale 1ns / 1ps
// The Phase 6 baseline system without the board pins: NEORV32 (neorv32_baseline),
// its XBUS slaves and the same HIL environment as the TTA core (hil_env: DC
// motor plant and load window, cycle-counted from reset).
//
//   XBUS address   slave                                  cached
//   0x0000_0000    code memory, 64 KB (xbus_mem)          yes
//   0x8000_0000    data memory, 64 KB (xbus_mem)          yes
//   0xF000_0000    xbus_hil: timer, plant I/O, monitor    no (>= CACHE_UC_BASE)
//   anything else  bus error
// The XBUS has one request in flight at a time, so the response mux only needs
// to remember which slave took the last strobe.
module baseline_soc #(
  /* verilator lint_off UNUSEDPARAM */
  parameter int CACHE_EN  = 1,          // synthesis only; the simulation netlist has it fixed
  /* verilator lint_on UNUSEDPARAM */
  parameter int FIRST_LAT = 10,         // main memory: strobe -> ack, first word of a transaction
  parameter int BURST_LAT = 1           //              further words of a burst (cti, see xbus_mem)
) (
  input  logic        clk,
  input  logic        rst,
  input  logic        uart0_rxd,
  output logic        uart0_txd,
  output logic        mon_en,           // the monitor owns the UART TX line
  output logic        mon_txd,
  output logic [31:0] pwm_cmd,
  output logic [31:0] encoder,
  output logic        load_on
);
  /* verilator lint_off UNUSEDSIGNAL */
  logic [31:0] adr;                     // word accesses only: adr[1:0] is not decoded
  /* verilator lint_on UNUSEDSIGNAL */
  logic [31:0] wdat, rdat;
  logic [2:0]  cti;
  logic [3:0]  sel;
  logic        we, stb, cyc, ack, err, irq_mti;

  /* verilator lint_off UNUSEDSIGNAL */
  logic [2:0] tag;                      // tag[2]: instruction fetch; only the bench looks at it
  /* verilator lint_on UNUSEDSIGNAL */

`ifdef SYNTHESIS
  neorv32_baseline #(.CACHE_EN(CACHE_EN), .BOOT_MODE(0)) u_cpu (
`else
  // GHDL netlist from baseline/sim/convert.sh: the generics are frozen in it
  neorv32_baseline u_cpu (
`endif
    .clk_i(clk), .rstn_i(!rst),
    .xbus_adr_o(adr), .xbus_dat_o(wdat), .xbus_cti_o(cti), .xbus_tag_o(tag), .xbus_we_o(we),
    .xbus_sel_o(sel), .xbus_stb_o(stb), .xbus_cyc_o(cyc),
    .xbus_dat_i(rdat), .xbus_ack_i(ack), .xbus_err_i(err),
    .uart0_txd_o(uart0_txd), .uart0_rxd_i(uart0_rxd), .irq_mti_i(irq_mti)
  );

  // ---------------------------------------------------------------- decode
  typedef enum logic [1:0] {T_CODE, T_DATA, T_HIL, T_ERR} target_e;
  target_e hit, last;
  always_comb begin
    if (adr[31:28] == 4'h0 && adr[27:16] == '0)       hit = T_CODE;
    else if (adr[31:28] == 4'h8 && adr[27:16] == '0)  hit = T_DATA;
    else if (adr[31:6] == 26'h3C0_0000)               hit = T_HIL;   // 0xF000_0000..0xF000_003F
    else                                               hit = T_ERR;
  end

  logic        stb_code, stb_data, stb_hil, ack_code, ack_data, ack_hil;
  logic [31:0] dat_code, dat_data, dat_hil;
  assign stb_code = stb && hit == T_CODE;
  assign stb_data = stb && hit == T_DATA;
  assign stb_hil  = stb && hit == T_HIL;

  always_ff @(posedge clk) begin
    if (rst) begin last <= T_CODE; err <= 1'b0; end
    else begin
      if (stb) last <= hit;
      err <= stb && hit == T_ERR;
    end
  end

  xbus_mem #(.WORDS(16384), .FIRST_LAT(FIRST_LAT), .BURST_LAT(BURST_LAT), .SIM_PLUSARG("code")) u_code (
    .clk, .rst, .adr(adr[15:2]), .dat_i(wdat), .we, .sel, .cti, .stb(stb_code), .cyc,
    .dat_o(dat_code), .ack(ack_code)
  );
  xbus_mem #(.WORDS(16384), .FIRST_LAT(FIRST_LAT), .BURST_LAT(BURST_LAT), .SIM_PLUSARG("")) u_data (
    .clk, .rst, .adr(adr[15:2]), .dat_i(wdat), .we, .sel, .cti, .stb(stb_data), .cyc,
    .dat_o(dat_data), .ack(ack_data)
  );
  xbus_hil u_hil (
    .clk, .rst, .adr(adr[5:2]), .dat_i(wdat), .we, .stb(stb_hil), .cyc,
    .dat_o(dat_hil), .ack(ack_hil), .irq_mti,
    .encoder, .din(32'b0), .pwm_cmd, .mon_en, .mon_txd
  );

  assign ack  = ack_code || ack_data || ack_hil;
  assign rdat = last == T_DATA ? dat_data : last == T_HIL ? dat_hil : dat_code;

  // ---------------------------------------------------------------- plant
  hil_env u_env (.clk, .rst, .pwm_cmd, .load_sw(1'b0), .encoder, .load_on);
endmodule
