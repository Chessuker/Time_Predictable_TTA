# Phase 6: the TTA against a NEORV32 baseline

Both systems run the same control law (FixedPID, bit-exact with the Python
model) against the same plant RTL (`dc_motor_plant_mc.sv` in `hil_env`) on the
same board, an Arty A7-100T at 100 MHz. The period is 100,000 cycles (1 kHz),
the deadline is release + 2,000 cycles and the fixed actuation slot is
release + 1,001. Release means the anchor on the TTA and the MTIMECMP match
on NEORV32.

| | TTA | NEORV32 baseline |
|---|---|---|
| core | this project, 1 move per cycle, constant-time ISA | NEORV32 v1.13.6, rv32imc + Zicntr |
| program | hand-written TTA assembly (`control.tta`, `control_sw.tta`; `control_sw` is 112 code words) | C, xPack GCC 15.2.0, -O2 (`baseline/sw/ctrl`) |
| memory | on-chip, 4,096 words of code and 4,096 of data, fixed latency | 64 KB code + 64 KB data on XBUS, FIRST_LAT 10, BURST_LAT 1, behind I$/D$ (64 x 64 B, direct-mapped) or with the caches off |
| release | `t_wait` on the anchor, `t_advance` adds the period in hardware | machine timer interrupt from MTIMECMP in `xbus_hil` |
| background work | none | L0 none, L1d D$ thrashing, L1di L1d + code (cache on only), L2 L1d + a GPTMR interrupt at 7300.3 Hz |

The NEORV32 measurement method is in `docs/baseline_measurement.md`. The raw
NEORV32 board tables are in `docs/baseline_board_results.md`, the polling
sweep in `docs/baseline_poll_sweep_120.md`.

## How each side is measured

- **TTA, timing reported from inside the processor.** The program reads
  `tmr.elapsed` (cycles since the anchor) after sensing and after actuating
  and sends both with every period. These elapsed values are constant in
  every recorded period. The anchor itself is not timestamped: its spacing
  is set by the timer unit, whose cycle-exact behaviour is proved against
  the absolute-time spec by k-induction (Phase 3, `formal/tta_core.sby`).
  The TTA period jitter in the table is therefore derived from the reported
  elapsed values and the proved timer, not measured by an instrument
  outside the core.
- **NEORV32, timing from a monitor outside the processor.** `xbus_hil`
  timestamps START, SENSE and ACT in hardware every period, and the host
  replays the plant model against the recorded writes (bit-exact in all 14
  runs).

The two kinds of evidence differ in scope: both give exact cycle numbers,
but only the NEORV32 numbers come from an instrument outside the processor
under test.

The TTA rows come from three board runs of Phase 5:
- `control`, 3,000 periods: every field of every period equal to the ISS
  with the co-simulated plant (the table row);
- `control_sw` with the switches down, 10,000 periods: every field equal to
  the ISS. Its CSV was overwritten by the next run, so it has no row; the
  checker required the same elapsed values in every period (3 after the
  inputs, 1,002 after actuating), and they held;
- `control_sw` with the switches used, 20,000 periods (the table row): the
  inputs come from the switches, so the values cannot be compared with the
  model; the checker required the same elapsed values in every period and
  the setpoint to match din, and both held.

## Timing on the board

All periods (period 0 included), cycles from the release, min / p50 / p99 /
max. Sense interval = consecutive SENSE minus 100,000 (the plan's period
jitter). NEORV32 releases themselves are exact (release interval 0 in every
run).

Fixed actuation slot (TTA, NEORV32 wait mode):

| system | config | periods | release->sense | sense->act | release->act | sense interval | deadline misses |
|---|---|---|---|---|---|---|---|
| TTA | `control` | 3,000 | 1/1/1/1 | 1000/1000/1000/1000 | 1001/1001/1001/1001 | 0/0/0/0 | 0 |
| TTA | `control_sw` (switches used) | 20,000 | 1/1/1/1 | 1000/1000/1000/1000 | 1001/1001/1001/1001 | 0/0/0/0 | 0 |
| NEORV32 c1 | L0 wait | 10,000 | 173/176/179/341 | 678/851/860/860 | 1019/1026/1033/1033 | -164/2/6/30 | 0 |
| NEORV32 c1 | L1d wait | 10,000 | 421/459/469/518 | 513/566/605/611 | 1019/1026/1033/1033 | -72/-1/32/57 | 0 |
| NEORV32 c1 | L1di wait | 10,000 | 434/517/552/578 | 442/514/562/592 | 1019/1028/1033/1033 | -94/-1/73/92 | 0 |
| NEORV32 c1 | L2 wait | 10,000 | 324/436/912/1033 | 217/595/673/703 | 1019/1027/1151/1283 | -669/1/472/600 | 0 |
| NEORV32 c0 | L0 wait | 10,000 | 530/537/545/609 | 450/508/527/528 | 1032/1044/1059/1103 | -72/2/9/75 | 0 |
| NEORV32 c0 | L1d wait | 10,000 | 523/539/548/615 | 450/508/527/537 | 1032/1043/1059/1109 | -76/-1/23/84 | 0 |
| NEORV32 c0 | L2 wait | 10,000 | 553/567/1662/1798 | 387/486/502/509 | 1033/1057/2123/2293 | -1240/0/1094/1238 | 191 |

The TTA reads the encoder at release + 1 and actuates at release + 1,001 in
every recorded period, with or without switch input. NEORV32 cannot hit the
slot exactly: the busy-wait ends between 1,019 and 1,033 with the caches on
and after 1,032 with them off, and a GPTMR handler running at the slot moves
the actuation further (c1 L2 up to 1,283; c0 L2 past the deadline in 191 of
10,000 periods). The deadline misses are observed counts over these runs,
not a guaranteed rate.

## Where the baseline does better

- **Latency when acting as soon as possible.** NEORV32 c1 L0 asap actuates
  at release + 312/339/353/568 (steady state max 381), well before the TTA's
  slot. The TTA board programs actuate at + 1,001 by design. The WCET tool
  bounds `control.tta` at W = 90 cycles against its budget of 999, so an
  earlier slot could be possible on the TTA. This is an inference from the
  WCET bound, not a measured latency: no TTA variant acting as soon as
  possible was run on the board, and no latency comparison is claimed.
- **Work besides the control task.** NEORV32 runs background loads beside
  the control task (D$ thrashing, extra code, a second interrupt source) and
  has no deadline miss in 12 of its 14 runs. The TTA runs the control task alone
  and stalls in `t_wait` for the rest of the period.
- **Programmability.** NEORV32 runs C compiled by GCC on a standard ISA with
  128 KB of memory, caches and interrupts. The TTA program is written in its
  own assembly.
- **fmax with the caches off.** The c0 build has the most slack of the three
  (estimates below).

## Polling mode

Polling (the background in chunks of N units, `mip.MTIP` checked after each
chunk) was swept in simulation, 120 periods per run. The predeclared rule
needs one N that meets both criteria (throughput ratio R >= 0.98; empirical
release->act bound and observed maximum <= 1,500 cycles) in all three
selection configurations, c1 L1d, c1 L1di and c0 L1d. Among the N tested,
no common N exists:
- c1 L1d passes at N = 10 (B 1,432, observed 1,316, R 0.9810), in this
  configuration alone;
- c1 L1di: no tested N passes both. Timing passes at N <= 6 (R 0.9487 at
  N = 6, failing at 7 and 8), and R >= 0.98 first at N = 32 (B 4,512);
- c0 L1d: no tested N passes both. Timing passes at N <= 9 (R 0.9354 at
  N = 9, failing from 10), and R >= 0.98 first at N = 27 (B 2,840).

N tested: c1 L1d 1, 2, 4, 8-16, 32, 64; c1 L1di 1, 2, 4-8, 16, 32, 64;
c0 L1d 1, 2, 4, 8-32, 64. The other values (for example c1 L1di 9-15 and
17-31) were not run, so this is not a proof that no N passes all three.
Across the tested values a larger chunk buys background throughput with a
longer release->act bound. Details: `docs/baseline_measurement.md` section
3.6. Polling was not run on the board.

## Resources, fmax and power

Routed designs, Vivado 2025.2, `xc7a100tcsg324-1`, constraint 10 ns. The TTA
build loads `control_sw` (`tta_report` in `fpga/vivado/tta.tcl`), NEORV32
the two bitstreams of the board runs (`nrv_report 1`, `nrv_report 0` in
`fpga/vivado/nrv.tcl`).

**Whole design minus the plant** (LUT / FF / BRAM36 / DSP). The plant is the
same RTL on both sides (TTA 453 / 372 / 0 / 4, NEORV32 457 / 372 / 0 / 4) and
is taken out.

| | TTA | NEORV32 c1 | NEORV32 c0 |
|---|---|---|---|
| whole design minus plant | **1748 / 1369 / 8 / 3** | **3635 / 2482 / 39 / 4** | **2972 / 2244 / 34 / 4** |
| contains | core with its memories, timer and telemetry FIFO; telemetry UART; switch debounce (27 / 76) | NEORV32; code and data memory; `xbus_hil` (timer, monitor, record UART: c1 490 / 947, c0 422 / 947); break reset | same as c1 |

**Processor alone.** The boundaries differ, so this row is not a like-for-like
comparison:

| | TTA core | NEORV32 c1 | NEORV32 c0 |
|---|---|---|---|
| LUT / FF / BRAM36 / DSP | 1650 / 1233 / 8 / 3 | 3077 / 1486 / 7 / 4 | 2486 / 1248 / 2 / 4 |
| includes | instruction and data memory (4 + 4 BRAM36), timer, telemetry FIFO | I$ and D$, UART0, GPTMR, bootloader ROM | UART0, GPTMR, bootloader ROM |
| excludes | | the 128 KB memory (32 BRAM36), timer and monitor (`xbus_hil`) | same as c1 |

BRAM is reported but not claimed as a result: the memory sizes were chosen
for each program (16 KB per side on the TTA, 64 KB per side on NEORV32), not
reduced to what each design needs.

**Timing and fmax.** fmax is estimated as 1 / (10 ns - WNS); no frequency
sweep was made.

| | WNS | WHS | fmax estimate |
|---|---|---|---|
| TTA | +0.429 ns | +0.056 ns | about 104.5 MHz |
| NEORV32 c1 | +0.339 ns | +0.027 ns | about 103.5 MHz |
| NEORV32 c0 | +0.877 ns | +0.024 ns | about 109.6 MHz |

The TTA's WNS varies between implementation runs with the loaded image
(+0.406 ns for the Phase 5 `board_din` build).

**Power.** Vectorless estimates from `report_power` with default activity:

| | total | dynamic | device static | confidence |
|---|---|---|---|---|
| TTA | 0.117 W | 0.020 W (core 0.016) | 0.097 W | Low |
| NEORV32 c1 | 0.160 W | 0.061 W (NEORV32 0.052) | 0.099 W | Medium |
| NEORV32 c0 | 0.134 W | 0.035 W (NEORV32 0.027) | 0.099 W | Medium |

These are not measurements. All three reports carry the same warning that
high-fanout reset nets are asserted for long periods, which makes the
estimate inaccurate; the TTA estimate has Low confidence because more than
75% of its I/O has no activity specified; and device static power, about
0.1 W, is most of each total. Only the order of the dynamic estimates is
reported, not a ratio.

## Reproducing

    py -m host.hil.board <port>                      TTA, control.tta (Phase 5)
    py -m host.hil.board <port> --switch             TTA, control_sw.tta
    py -m host.baseline.board build
    py -m host.baseline.board run <port> --cache 1   NEORV32, after programming the c1 bitstream
    py -m host.baseline.board run <port> --cache 0
    py -m host.baseline.board report

Vivado Tcl Console: `tta_report` with `Time_Predictable_TTA.xpr` open and the
`control_sw` image built; `nrv_report 1` and `nrv_report 0` after sourcing
`fpga/vivado/nrv.tcl`.
