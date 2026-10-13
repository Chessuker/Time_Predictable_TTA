# make TRIG=0|1 LOAD=0|1|2|3 ACT=0|1 [CHUNK=.. BG_CODE=.. BG_ITER=.. PERIOD_CYC=.. START_DELAY=..] exe|hex
# (host/baseline/build.py builds every configuration into its own directory)
BASELINE ?= ../..
TRIG ?= 0
LOAD ?= 0
ACT ?= 0
USER_FLAGS += -DTRIG=$(TRIG) -DLOAD=$(LOAD) -DACT=$(ACT)
ifdef PERIOD_CYC
USER_FLAGS += -DPERIOD_CYC=$(PERIOD_CYC)
endif
ifneq ($(filter 1,$(TRIG) $(BG_CODE)),)
# polling and the interrupt-mode simulation reference (BG_CODE=1): loop heads on
# 4-byte boundaries, as the board's interrupt-mode sweep loop happens to be
# (NEORV32 fetches 32-bit words; docs/baseline_measurement.md 3.1)
USER_FLAGS += -falign-loops=4
endif
ifdef BG_CODE
USER_FLAGS += -DBG_CODE=$(BG_CODE)
endif
ifdef CHUNK
USER_FLAGS += -DCHUNK=$(CHUNK)
endif
ifdef BG_ITER
USER_FLAGS += -DBG_ITER=$(BG_ITER)
endif
ifdef START_DELAY
USER_FLAGS += -DSTART_DELAY=$(START_DELAY)
endif
include $(BASELINE)/sw/common/baseline.mk
include $(NEORV32_HOME)/sw/common/common.mk
