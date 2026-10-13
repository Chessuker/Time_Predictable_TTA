-- NEORV32 configuration of the Phase 6 baseline (docs/design_decisions.md).
--
-- Used as it is by Vivado (generics from arty_neorv32_top) and converted to one
-- plain-Verilog module by GHDL for Verilator (baseline/sim/convert.sh, used by
-- tests/test_baseline_rtl.py; the generics are fixed at conversion with -g).
--
--   CPU      rv32imc + Zicntr, fast multiplier and barrel shifter, 100 MHz
--   memory   no internal IMEM/DMEM: code and data are on the XBUS, behind the
--            caches (xbus_mem.sv adds the wait states of a slower memory)
--   caches   I$ and D$ 64 x 64 B = 4 KB each when CACHE_EN = 1, line fills in bursts
--   timer    no CLINT: MTIME/MTIMECMP live in xbus_hil.sv, which drives irq_mti_i
--            so the release instant can be timestamped in hardware
--   I/O      UART0 (bootloader, 19200 baud), one GPTMR slice (background load)
library ieee;
use ieee.std_logic_1164.all;

library neorv32;
use neorv32.neorv32_package.all;

entity neorv32_baseline is
  generic (
    CACHE_EN  : natural range 0 to 1 := 1;  -- 1: I$ and D$ (the cache factor of the experiment)
    BOOT_MODE : natural := 0;             -- 0 = bootloader (board), 1 = start at 0x0 (simulation)
    TRACE_EN  : natural range 0 to 1 := 0 -- 1: NEORV32 instruction trace port on (simulation
                                          -- measurements only, docs/baseline_measurement.md)
  );
  port (
    clk_i       : in  std_ulogic;
    rstn_i      : in  std_ulogic;         -- low-active
    xbus_adr_o  : out std_ulogic_vector(31 downto 0);
    xbus_dat_o  : out std_ulogic_vector(31 downto 0);
    xbus_cti_o  : out std_ulogic_vector(2 downto 0);
    xbus_tag_o  : out std_ulogic_vector(2 downto 0);  -- bit 2: instruction fetch (simulation statistics)
    xbus_we_o   : out std_ulogic;
    xbus_sel_o  : out std_ulogic_vector(3 downto 0);
    xbus_stb_o  : out std_ulogic;
    xbus_cyc_o  : out std_ulogic;
    xbus_dat_i  : in  std_ulogic_vector(31 downto 0);
    xbus_ack_i  : in  std_ulogic;
    xbus_err_i  : in  std_ulogic;
    uart0_txd_o : out std_ulogic;
    uart0_rxd_i : in  std_ulogic;
    irq_mti_i   : in  std_ulogic          -- machine timer interrupt from xbus_hil
  );
end entity;

architecture rtl of neorv32_baseline is
begin

  neorv32_top_inst: neorv32_top
  generic map (
    CLOCK_FREQUENCY   => 100_000_000,
    BOOT_MODE_SELECT  => BOOT_MODE,
    TRACE_PORT_EN     => TRACE_EN = 1,
    BOOT_ADDR_CUSTOM  => x"00000000",
    RISCV_ISA_C       => true,
    RISCV_ISA_M       => true,
    RISCV_ISA_Zicntr  => true,
    CPU_FAST_MUL_EN   => true,
    CPU_FAST_SHIFT_EN => true,
    IMEM_EN           => false,
    DMEM_EN           => false,
    ICACHE_EN         => CACHE_EN = 1,
    ICACHE_NUM_BLOCKS => 64,
    DCACHE_EN         => CACHE_EN = 1,
    DCACHE_NUM_BLOCKS => 64,
    CACHE_BLOCK_SIZE  => 64,
    CACHE_BURSTS_EN   => true,
    XBUS_EN           => true,
    XBUS_TIMEOUT      => 2048,
    IO_CLINT_EN       => false,
    IO_UART0_EN       => true,
    IO_GPTMR_NUM      => 1
  )
  port map (
    clk_i       => clk_i,
    rstn_i      => rstn_i,
    xbus_adr_o  => xbus_adr_o,
    xbus_dat_o  => xbus_dat_o,
    xbus_cti_o  => xbus_cti_o,
    xbus_tag_o  => xbus_tag_o,
    xbus_we_o   => xbus_we_o,
    xbus_sel_o  => xbus_sel_o,
    xbus_stb_o  => xbus_stb_o,
    xbus_cyc_o  => xbus_cyc_o,
    xbus_dat_i  => xbus_dat_i,
    xbus_ack_i  => xbus_ack_i,
    xbus_err_i  => xbus_err_i,
    uart0_txd_o => uart0_txd_o,
    uart0_rxd_i => uart0_rxd_i,
    irq_mti_i   => irq_mti_i
  );

end architecture;
