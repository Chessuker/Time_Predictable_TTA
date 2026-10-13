# Settings shared by the baseline programs; include from an application makefile
# after setting BASELINE (path to baseline/), then include NEORV32's common.mk.

NEORV32_HOME ?= $(BASELINE)/neorv32
RISCV_PREFIX ?= $(HOME)/xpack/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-

# neorv32_baseline.vhd: rv32imc + Zicntr
MARCH = rv32imc_zicntr_zicsr_zifencei
MABI  = ilp32
EFFORT = -O2

# code and data memories on the XBUS (baseline_soc.sv)
USER_FLAGS += -Wl,--defsym,__neorv32_rom_size=64k
USER_FLAGS += -Wl,--defsym,__neorv32_ram_size=64k

# set here, so common.mk keeps them (it only fills APP_SRC/APP_INC when unset)
APP_SRC = $(wildcard ./*.c) $(wildcard ./*.S) $(BASELINE)/sw/pid/pid.c
APP_INC = -I . -I $(BASELINE)/sw/pid -I $(BASELINE)/sw/common
