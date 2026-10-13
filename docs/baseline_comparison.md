# Phase 6: the TTA against a NEORV32 baseline

Both systems run the same control law (FixedPID, bit-exact with the Python
model) against the same plant RTL (`dc_motor_plant_mc.sv` in `hil_env`) on the
same board, an Arty A7-100T at 100 MHz. The period is 100,000 cycles (1 kHz),
the deadline is release + 2,000 cycles and the fixed actuation slot is
release + 1,001. Release means the anchor on the TTA and the MTIMECMP match
on NEORV32.

> **Measured again after the audit fixes (2026-10-13).** The NEORV32 board
> runs, the polling sweep and all Vivado figures on this page come from the
> sources after audit Rounds A to D: the corrected XBUS memory (A5-1), Vivado's
> Slice LUTs as the LUT figure (A6-1), saved report summaries (A6-5) and
> recorded provenance (A5-3). The TTA board rows are still the Phase 5 runs:
> the only TTA RTL change since then (A1-1) concerns fetches outside code
> memory, which these programs never make (their RTL and ISS traces are equal
> in lockstep before and after). The first results, made before the fixes,
> are in the git history of this file and of `baseline_board_results.md` up
> to commit ecaee4e.

| | TTA | NEORV32 baseline |
|---|---|---|
| core | this project, 1 move per cycle, constant-time ISA | NEORV32 v1.13.6, rv32imc + Zicntr |
| program | hand-written TTA assembly (`control.tta`, `control_sw.tta`; `control_sw` is 112 code words) | C, xPack GCC 15.2.0, -O2 (`baseline/sw/ctrl`) |
| memory | on-chip, 4,096 words of code and 4,096 of data, fixed latency | 64 KB code + 64 KB data on XBUS: FIRST_LAT 10 for the first word of every transaction (single access or line burst), BURST_LAT 1 for the further words of a burst; behind I$/D$ (64 x 64 B, direct-mapped) or with the caches off |
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

**How the TTA rows follow from those values (audit A6-4).** After
`t_advance` the first move runs at anchor + 1 (timing_model R5, D = 1): that
move reads `io.encoder`, so release->sense is 1. The move after the sense
reads `tmr.elapsed`, which gives 2 in `control`; in `control_sw` the read of
`io.din` comes first, so it gives 3. `t_wait` with v = 1,000 wakes the
actuating move at anchor + 1,001, and the `tmr.elapsed` read right after it
gives 1,002 in both. So release->sense = 1, release->act = 1,001 and
sense->act = 1,000 hold in every period in which the recorded elapsed values
are 2 (or 3) and 1,002, which the checker required of all of them. The TTA
rows are written in the min / p50 / p99 / max layout of the NEORV32 rows, but
each is one constant derived this way, not a distribution measured by an
instrument.

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
| NEORV32 c1 | L1di wait | 10,000 | 434/516/552/559 | 467/514/562/596 | 1019/1028/1033/1033 | -94/-1/70/91 | 0 |
| NEORV32 c1 | L2 wait | 10,000 | 324/436/911/1035 | 207/595/673/703 | 1019/1027/1151/1281 | -670/0/474/612 | 0 |
| NEORV32 c0 | L0 wait | 10,000 | 626/637/648/758 | 536/618/676/676 | 1162/1257/1324/1433 | -126/1/13/122 | 0 |
| NEORV32 c0 | L1d wait | 10,000 | 624/644/659/770 | 536/618/676/676 | 1162/1266/1334/1446 | -136/-1/23/122 | 0 |
| NEORV32 c0 | L2 wait | 10,000 | 622/641/2776/2913 | 548/630/688/688 | 1170/1278/3412/3592 | -2288/-1/2141/2280 | 1,138 |

The TTA reads the encoder at release + 1 and actuates at release + 1,001 in
every recorded period, with or without switch input. NEORV32 cannot hit the
slot exactly. With the caches on the busy-wait ends between 1,019 and 1,033,
except when a GPTMR handler runs at the slot (c1 L2: p99 1,151, max 1,281).
With the caches off the control step alone takes longer than 1,001 cycles
(c0 L0 asap release->act min 1,127), so the busy-wait never waits and every
c0 row misses the slot: actuation at 1,162 to 1,446 without the GPTMR load,
and with it up to 3,592, past the deadline in 1,138 of 10,000 periods (1,109
in L2 asap). The deadline misses are observed counts over these runs, not a
guaranteed rate.

## Where the baseline does better

- **Latency when acting as soon as possible.** NEORV32 c1 L0 asap actuates
  at release + 312/339/353/568 (steady state max 381), well before the TTA's
  slot. The TTA board programs actuate at + 1,001 by design. The WCET tool
  bounds `control.tta` at W = 90 cycles against its budget of 999, so an
  earlier slot could be possible on the TTA. That is an inference from the
  WCET bound, not a measured latency; no TTA variant acting as soon as
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
- c1 L1d passes at N = 10 in the 120-period runs (B 1,432, observed 1,316,
  R 0.9810), in this configuration alone. This pass is provisional: R falls
  in the band [0.975, 0.985] where the rule reruns an N with 1,000 periods
  before choosing it, and B is 68 cycles under the limit. The rerun was not
  made because no N passes in the other two configurations, so it could not
  change the outcome (audit A6-2);
- c1 L1di: no tested N passes both. Timing passes at N <= 6 (R 0.9487 at
  N = 6, failing at 7 and 8), and R >= 0.98 first at N = 32 (B 4,512);
- c0 L1d: no tested N passes both. Timing passes at N <= 5 (R 0.8716 at
  N = 5, failing from 6), and R >= 0.98 first at N = 29 (B 3,582).

N tested: c1 L1d 1, 2, 4, 8-16, 32, 64; c1 L1di 1, 2, 4-8, 16, 32, 64;
c0 L1d 1, 2, 4-32, 64. The other values (for example c1 L1di 9-15 and
17-31) were not run, so this is not a proof that no N passes all three.
Across the tested values a larger chunk buys background throughput with a
longer release->act bound. Details: `docs/baseline_measurement.md` section
3.6. Polling was not run on the board.

## Resources, fmax and power

Routed designs, Vivado 2025.2, `xc7a100tcsg324-1`, constraint 10 ns, built
and reported on 2026-10-13: the TTA with the `control_sw` image
(`tta_report` in `fpga/vivado/tta.tcl`), NEORV32 the two bitstreams of the
board runs (`nrv_report 1`, `nrv_report 0` in `fpga/vivado/nrv.tcl`). Every
figure below is in the `report_summary.txt` those procedures saved next to
the reports (`build/vivado_tta`, `build/vivado_nrv_c1`, `build/vivado_nrv_c0`,
not in git).

**LUT count (audit A6-1).** LUT means Vivado's "Slice LUTs"
(`utilization.rpt`): physical 6-input LUTs, after two small functions share
one. The number of LUT primitive cells, which the first version of these
tables reported, runs 12 to 18 % higher here and is given once for reference.

**Whole design minus the plant** (Slice LUTs / FF / BRAM36 / DSP). The plant
is the same RTL on both sides (TTA 370 / 372 / 0 / 4, NEORV32 c1 375 / 372 /
0 / 4, c0 370 / 372 / 0 / 4) and is taken out.

| | TTA | NEORV32 c1 | NEORV32 c0 |
|---|---|---|---|
| whole design minus plant | **1535 / 1369 / 8 / 3** | **3227 / 2483 / 39 / 4** | **2594 / 2242 / 34 / 4** |
| whole design | 1905 / 1741 / 8 / 7 | 3602 / 2855 / 39 / 8 | 2964 / 2614 / 34 / 8 |
| LUT cells, whole design minus plant | 1800 | 3641 | 2989 |
| contains | core with its memories, timer and telemetry FIFO; telemetry UART (38 / 53); switch debounce (27 / 76) | NEORV32; code and data memory; `xbus_hil` (timer, monitor, record UART: 419 / 947); break reset | same as c1, `xbus_hil` 354 / 947 |

The minus-plant Slice LUTs subtract the plant's figure from the whole design.
Vivado notes that hierarchical figures do not add up exactly when LUTs are
shared across a boundary, so they are close, not exact. FF, BRAM and DSP are
exact counts.

The headline still holds parts of the measurement set-up (audit A6-6).
`xbus_hil` holds the release timer, the hardware monitor that timestamps
START, SENSE and ACT, and its record UART; it is 947 FF in both NEORV32
builds, more than the whole FF difference to the TTA with the caches off
(2242 - 1369 = 873) and most of it with the caches on (2483 - 1369 = 1114).
The TTA's timer is inside its core, and its telemetry UART is 53 FF. The
processors alone (below) are closer: 1233 FF for the TTA core against 1248
for NEORV32 with the caches off.

**Processor alone.** The boundaries differ, so this row is not a like-for-like
comparison:

| | TTA core | NEORV32 c1 | NEORV32 c0 |
|---|---|---|---|
| Slice LUTs / FF / BRAM36 / DSP | 1467 / 1233 / 8 / 3 | 2750 / 1487 / 7 / 4 | 2195 / 1248 / 2 / 4 |
| LUT cells | 1702 | 3085 | 2502 |
| includes | instruction and data memory (4 + 4 BRAM36), timer, telemetry FIFO | I$ and D$, UART0, GPTMR, bootloader ROM | UART0, GPTMR, bootloader ROM |
| excludes | | the 128 KB memory (32 BRAM36), timer and monitor (`xbus_hil`) | same as c1 |

BRAM is reported but not claimed as a result: the memory sizes were chosen
for each program (16 KB per side on the TTA, 64 KB per side on NEORV32), not
reduced to what each design needs.

**Timing and fmax.** fmax is estimated as 1 / (10 ns - WNS); no frequency
sweep was made.

| | WNS | WHS | fmax estimate |
|---|---|---|---|
| TTA | +0.233 ns | +0.034 ns | about 102.4 MHz |
| NEORV32 c1 | +0.273 ns | +0.021 ns | about 102.8 MHz |
| NEORV32 c0 | +0.600 ns | +0.044 ns | about 106.4 MHz |

WNS moves between implementation runs: the first Phase 6 builds gave +0.429,
+0.339 and +0.877 ns, and the Phase 5 `board_din` build of the TTA +0.406 ns.
Since then the TTA decode gained the A1-1 check and `xbus_mem` the A5-1
change, so the two sets are not one design implemented twice; all three
builds meet timing at 100 MHz either way.

**Power.** Vectorless estimates from `report_power` with default activity:

| | total | dynamic | device static | confidence |
|---|---|---|---|---|
| TTA | 0.120 W | 0.022 W (core 0.018) | 0.098 W | Low |
| NEORV32 c1 | 0.162 W | 0.063 W (NEORV32 0.055) | 0.099 W | Medium |
| NEORV32 c0 | 0.133 W | 0.034 W (NEORV32 0.026) | 0.099 W | Medium |

These are not measurements. For each of the three builds `report_power`
issued one warning, saved in the Vivado log of the session:
`[Power 33-332] Found switching activity that implies high-fanout reset nets
being asserted for excessive periods of time which may result in inaccurate
power analysis`; `report_summary.txt` records that count (audit A6-5). The TTA
estimate has Low confidence because more than 75 % of its inputs have no
activity specified (`power.rpt`), and device static power, about 0.1 W, is
most of each total. Only the order of the dynamic estimates is reported, not
a ratio.

## Reproducing

    py -m host.hil.board <port>                      TTA, control.tta (Phase 5)
    py -m host.hil.board <port> --switch             TTA, control_sw.tta
    py -m host.baseline.board build
    py -m host.baseline.board run <port> --cache 1   NEORV32, after programming the c1 bitstream
    py -m host.baseline.board run <port> --cache 0
    py -m host.baseline.board report

Vivado Tcl Console: `tta_report` with `Time_Predictable_TTA.xpr` open and the
`control_sw` image built; `nrv_build` and `nrv_report` for 1 and 0 after
sourcing `fpga/vivado/nrv.tcl`. Each report writes its printed summary (WNS,
WHS, Slice LUTs and LUT cells per block, power, number of `report_power`
warnings) to `report_summary.txt` next to the reports; keep the Vivado log of
the session for the text of the warnings.

Provenance (audit A5-3): `py -m host.baseline.board build` writes, next to
each executable, `manifest.json` with what it was built from (git commit,
tracked and untracked changes, sha256 of every file under `baseline/sw`, the
NEORV32 submodule commit, sha256 of every changed or untracked file in the
repository and in the submodule, the GCC version) bound to the executable's
sha256. `run` refuses an executable its manifest does not describe and
records the manifest, the sources changed since the build, the git state at
run time and the sha256 of the bitstream file on disk in `summary.json`;
`py -m host.baseline.board report` lists them under the tables. The 14 runs
of 2026-10-13 came from the version before the build manifest; how they map
to their source is in `baseline_board_results.md`.
