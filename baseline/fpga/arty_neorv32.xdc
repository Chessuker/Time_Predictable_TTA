## Arty A7-100T (xc7a100tcsg324-1) pins used by baseline/rtl/arty_neorv32_top.sv.
## Pin locations follow Digilent's Arty-A7-100-Master.xdc (same as constrs_1/new/arty.xdc).

## 100 MHz system clock
set_property -dict { PACKAGE_PIN E3  IOSTANDARD LVCMOS33 } [get_ports { CLK100MHZ }]
create_clock -name sys_clk -period 10.000 -waveform {0.000 5.000} [get_ports { CLK100MHZ }]

## Red RESET button (active low)
set_property -dict { PACKAGE_PIN C2  IOSTANDARD LVCMOS33 } [get_ports { ck_rst }]

## USB-UART
set_property -dict { PACKAGE_PIN D10 IOSTANDARD LVCMOS33 } [get_ports { uart_rxd_out }]
set_property -dict { PACKAGE_PIN A9  IOSTANDARD LVCMOS33 } [get_ports { uart_txd_in }]

## Green LEDs LD4..LD7
set_property -dict { PACKAGE_PIN H5  IOSTANDARD LVCMOS33 } [get_ports { led[0] }]
set_property -dict { PACKAGE_PIN J5  IOSTANDARD LVCMOS33 } [get_ports { led[1] }]
set_property -dict { PACKAGE_PIN T9  IOSTANDARD LVCMOS33 } [get_ports { led[2] }]
set_property -dict { PACKAGE_PIN T10 IOSTANDARD LVCMOS33 } [get_ports { led[3] }]

## Asynchronous inputs go through 2-FF synchronisers in the top level.
set_false_path -from [get_ports { ck_rst uart_txd_in }]
## LEDs and the UART line are slow outputs with no timing relationship to care about.
set_false_path -to [get_ports { led[*] uart_rxd_out }]

## Bitstream
set_property CFGBVS VCCO        [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
