## Arty A7-100T (xc7a100tcsg324-1) pins used by rtl/top/arty_tta_top.sv.
## Pin locations follow Digilent's Arty-A7-100-Master.xdc.

## 100 MHz system clock
set_property -dict { PACKAGE_PIN E3  IOSTANDARD LVCMOS33 } [get_ports { CLK100MHZ }]
create_clock -name sys_clk -period 10.000 -waveform {0.000 5.000} [get_ports { CLK100MHZ }]

## Red RESET button (active low)
set_property -dict { PACKAGE_PIN C2  IOSTANDARD LVCMOS33 } [get_ports { ck_rst }]

## Slide switches
set_property -dict { PACKAGE_PIN A8  IOSTANDARD LVCMOS33 } [get_ports { sw[0] }]
set_property -dict { PACKAGE_PIN C11 IOSTANDARD LVCMOS33 } [get_ports { sw[1] }]
set_property -dict { PACKAGE_PIN C10 IOSTANDARD LVCMOS33 } [get_ports { sw[2] }]
set_property -dict { PACKAGE_PIN A10 IOSTANDARD LVCMOS33 } [get_ports { sw[3] }]

## Green LEDs LD4..LD7
set_property -dict { PACKAGE_PIN H5  IOSTANDARD LVCMOS33 } [get_ports { led[0] }]
set_property -dict { PACKAGE_PIN J5  IOSTANDARD LVCMOS33 } [get_ports { led[1] }]
set_property -dict { PACKAGE_PIN T9  IOSTANDARD LVCMOS33 } [get_ports { led[2] }]
set_property -dict { PACKAGE_PIN T10 IOSTANDARD LVCMOS33 } [get_ports { led[3] }]

## USB-UART: FPGA transmit (the PC receives on this line)
set_property -dict { PACKAGE_PIN D10 IOSTANDARD LVCMOS33 } [get_ports { uart_rxd_out }]

## Asynchronous inputs go through 2-FF synchronisers in the top level.
set_false_path -from [get_ports { ck_rst sw[*] }]
## LEDs and the UART line are slow outputs with no timing relationship to care about.
set_false_path -to [get_ports { led[*] uart_rxd_out }]

## Bitstream
set_property CFGBVS VCCO        [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
