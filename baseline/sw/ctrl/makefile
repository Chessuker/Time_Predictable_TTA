# make TRIG=0 LOAD=0|1|2|3 ACT=0|1 [BG_ITER=.. PERIOD_CYC=.. START_DELAY=..] exe|hex
# (host/baseline/build.py builds every configuration into its own directory)
BASELINE ?= ../..
TRIG ?= 0
LOAD ?= 0
ACT ?= 0
USER_FLAGS += -DTRIG=$(TRIG) -DLOAD=$(LOAD) -DACT=$(ACT)
ifdef PERIOD_CYC
USER_FLAGS += -DPERIOD_CYC=$(PERIOD_CYC)
endif
ifdef BG_ITER
USER_FLAGS += -DBG_ITER=$(BG_ITER)
endif
ifdef START_DELAY
USER_FLAGS += -DSTART_DELAY=$(START_DELAY)
endif
include $(BASELINE)/sw/common/baseline.mk
include $(NEORV32_HOME)/sw/common/common.mk
