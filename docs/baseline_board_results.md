# Phase 6 board runs: NEORV32 baseline, interrupt mode

Arty A7-100T, 100 MHz, NEORV32 v1.13.6 (rv32imc + Zicntr), XBUS memory with
FIRST_LAT 10 for the first word of every transaction and BURST_LAT 1 for the
further words of a burst (corrected in audit Round B, A5-1). 14 runs of
10,000 periods (100,000 cycles each), recorded on 2026-10-13 with

    py -m host.baseline.board run <port> --cache 1
    py -m host.baseline.board run <port> --cache 0

and tabulated by `py -m host.baseline.board report` from the records in
`build/baseline_board/` (not in git), copied here unchanged. Definitions:
`docs/baseline_measurement.md` sections 1 and 2. Every period is timestamped
by the xbus_hil monitor in hardware. Integrity OK means the record sequence
check and the model replay passed; it says nothing about timing. Deadline
misses are observed counts over these runs, not guaranteed rates.

**What was measured.** Bitstreams built on 2026-10-13 by `nrv_build` from
commit d3ab82a, the corrected `xbus_mem` included: c1 sha256
dcc6872f9cdfe4c6... (2026-10-13 22:00:56), c0 sha256 ba19e9af72297284...
(2026-10-13 22:07:14); the FPGA was programmed by hand with these files. Every
run's `summary.json` records the sha256 of the uploaded program, the commit
(d3ab82a, with only `Time_Predictable_TTA.xpr` uncommitted) and the bitstream
file. These runs came from the first version of the provenance code, which
read the git state at run time; rebuilding the programs afterwards with the
build-manifest version gives the same sha256 for all 14 (no file under
`baseline/` changed since d3ab82a), so each program maps to that commit.

Loads: L0 none; L1d D$ thrashing (16 KB, one word per 64 B line); L1di L1d
plus an 8 KB straight code block (cache on only; this board binary differs
from the code-chain reference used in simulation, see
`docs/baseline_measurement.md` section 3.1); L2 L1d plus a GPTMR interrupt at
7300.3 Hz with a fixed 60-iteration handler. asap: actuate when computed;
wait: busy-wait to release + 1001, then actuate.

All periods (primary, period 0 with cold caches included). Cycles, min/p50/p99/max. sense interval = consecutive SENSE minus 100,000 (releases are exact: release interval column).
| cache | config | integrity | misses | release->start | release->sense | sense->act | release->act | sense interval | release interval |
|---|---|---|---|---|---|---|---|---|---|
| c0 | irq_L0_asap | OK | 0/10000 | 81/91/103/103 | 626/636/648/757 | 501/583/641/641 | 1127/1224/1289/1396 | -126/0/12/122 | 0/0/0/0 |
| c0 | irq_L0_wait | OK | 0/10000 | 81/92/103/103 | 626/637/648/758 | 536/618/676/676 | 1162/1257/1324/1433 | -126/1/13/122 | 0/0/0/0 |
| c0 | irq_L1d_asap | OK | 0/10000 | 79/97/115/115 | 624/642/660/766 | 501/583/641/641 | 1127/1227/1300/1407 | -138/-1/23/133 | 0/0/0/0 |
| c0 | irq_L1d_wait | OK | 0/10000 | 79/99/114/115 | 624/644/659/770 | 536/618/676/676 | 1162/1266/1334/1446 | -136/-1/23/122 | 0/0/0/0 |
| c0 | irq_L2_asap | OK | **1109/10000** | 79/97/2233/2371 | 622/640/2776/2914 | 511/593/651/651 | 1134/1240/3373/3565 | -2290/-3/2130/2287 | 0/0/0/0 |
| c0 | irq_L2_wait | OK | **1138/10000** | 79/98/2233/2370 | 622/641/2776/2913 | 548/630/688/688 | 1170/1278/3412/3592 | -2288/-1/2141/2280 | 0/0/0/0 |
| c1 | irq_L0_asap | OK | 0/10000 | 29/32/35/91 | 173/176/179/339 | 139/162/174/229 | 312/339/353/568 | -165/-1/6/32 | 0/0/0/0 |
| c1 | irq_L0_wait | OK | 0/10000 | 29/32/35/93 | 173/176/179/341 | 678/851/860/860 | 1019/1026/1033/1033 | -164/2/6/30 | 0/0/0/0 |
| c1 | irq_L1d_asap | OK | 0/10000 | 27/46/91/96 | 380/429/475/526 | 170/222/234/289 | 585/651/708/815 | -104/3/66/103 | 0/0/0/0 |
| c1 | irq_L1d_wait | OK | 0/10000 | 41/76/85/88 | 421/459/469/518 | 513/566/605/611 | 1019/1026/1033/1033 | -72/-1/32/57 | 0/0/0/0 |
| c1 | irq_L1di_asap | OK | 0/10000 | 85/100/149/149 | 463/524/558/586 | 285/338/380/389 | 787/870/935/966 | -76/-2/62/88 | 0/0/0/0 |
| c1 | irq_L1di_wait | OK | 0/10000 | 56/85/120/127 | 434/516/552/559 | 467/514/562/596 | 1019/1028/1033/1033 | -94/-1/70/91 | 0/0/0/0 |
| c1 | irq_L2_asap | OK | 0/10000 | 27/53/562/710 | 325/428/888/1038 | 172/224/236/262 | 534/655/1112/1274 | -667/-4/457/615 | 0/0/0/0 |
| c1 | irq_L2_wait | OK | 0/10000 | 27/52/583/707 | 324/436/911/1035 | 207/595/673/703 | 1019/1027/1151/1281 | -670/0/474/612 | 0/0/0/0 |

Steady state (periods k >= 1). Cycles, min/p50/p99/max. sense interval = consecutive SENSE minus 100,000 (releases are exact: release interval column).
| cache | config | integrity | misses | release->start | release->sense | sense->act | release->act | sense interval | release interval |
|---|---|---|---|---|---|---|---|---|---|
| c0 | irq_L0_asap | OK | 0/9999 | 81/91/103/103 | 626/636/648/757 | 501/583/641/641 | 1127/1224/1289/1396 | -126/0/12/122 | 0/0/0/0 |
| c0 | irq_L0_wait | OK | 0/9999 | 81/92/103/103 | 626/637/648/758 | 536/618/676/676 | 1162/1257/1324/1433 | -126/1/13/122 | 0/0/0/0 |
| c0 | irq_L1d_asap | OK | 0/9999 | 79/97/115/115 | 624/642/660/766 | 501/583/641/641 | 1127/1227/1300/1407 | -138/-1/23/133 | 0/0/0/0 |
| c0 | irq_L1d_wait | OK | 0/9999 | 79/99/114/115 | 624/644/659/770 | 536/618/676/676 | 1162/1266/1334/1446 | -136/-1/23/122 | 0/0/0/0 |
| c0 | irq_L2_asap | OK | **1109/9999** | 79/97/2234/2371 | 622/640/2777/2914 | 511/593/651/651 | 1134/1240/3374/3565 | -2290/-3/2130/2287 | 0/0/0/0 |
| c0 | irq_L2_wait | OK | **1138/9999** | 79/98/2234/2370 | 622/641/2777/2913 | 548/630/688/688 | 1170/1278/3414/3592 | -2288/-1/2141/2280 | 0/0/0/0 |
| c1 | irq_L0_asap | OK | 0/9999 | 29/32/35/35 | 173/176/179/207 | 139/162/174/203 | 312/339/353/381 | -31/-1/6/32 | 0/0/0/0 |
| c1 | irq_L0_wait | OK | 0/9999 | 29/32/35/35 | 173/176/179/206 | 818/851/860/860 | 1019/1026/1033/1033 | -31/2/6/30 | 0/0/0/0 |
| c1 | irq_L1d_asap | OK | 0/9999 | 27/46/91/91 | 380/429/475/499 | 170/222/234/263 | 585/651/708/732 | -84/3/66/103 | 0/0/0/0 |
| c1 | irq_L1d_wait | OK | 0/9999 | 41/76/85/85 | 421/459/469/497 | 533/566/605/611 | 1019/1026/1033/1033 | -65/-1/32/57 | 0/0/0/0 |
| c1 | irq_L1di_asap | OK | 0/9999 | 85/100/149/149 | 463/524/558/586 | 285/338/380/389 | 787/870/935/966 | -76/-2/62/88 | 0/0/0/0 |
| c1 | irq_L1di_wait | OK | 0/9999 | 56/85/120/127 | 434/516/552/559 | 467/514/562/596 | 1019/1028/1033/1033 | -94/-1/70/91 | 0/0/0/0 |
| c1 | irq_L2_asap | OK | 0/9999 | 27/53/562/710 | 325/428/890/1038 | 172/224/236/236 | 534/655/1114/1274 | -667/-4/457/615 | 0/0/0/0 |
| c1 | irq_L2_wait | OK | 0/9999 | 27/52/583/707 | 324/436/911/1035 | 207/595/673/703 | 1019/1027/1151/1281 | -670/0/474/612 | 0/0/0/0 |


Provenance (`summary.json`, program sha256 abbreviated):

- c0 irq_L0_asap: program sha256 ebae7d8e5684d83c, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file ba19e9af72297284 (2026-10-13 22:07:14)
- c0 irq_L0_wait: program sha256 21da10cb2245e837, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file ba19e9af72297284 (2026-10-13 22:07:14)
- c0 irq_L1d_asap: program sha256 8da01efbc9e6f493, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file ba19e9af72297284 (2026-10-13 22:07:14)
- c0 irq_L1d_wait: program sha256 6cf760c93f4f532a, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file ba19e9af72297284 (2026-10-13 22:07:14)
- c0 irq_L2_asap: program sha256 8c90c692e7d04450, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file ba19e9af72297284 (2026-10-13 22:07:14)
- c0 irq_L2_wait: program sha256 947cc14a35a872fd, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file ba19e9af72297284 (2026-10-13 22:07:14)
- c1 irq_L0_asap: program sha256 ebae7d8e5684d83c, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file dcc6872f9cdfe4c6 (2026-10-13 22:00:56)
- c1 irq_L0_wait: program sha256 21da10cb2245e837, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file dcc6872f9cdfe4c6 (2026-10-13 22:00:56)
- c1 irq_L1d_asap: program sha256 8da01efbc9e6f493, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file dcc6872f9cdfe4c6 (2026-10-13 22:00:56)
- c1 irq_L1d_wait: program sha256 6cf760c93f4f532a, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file dcc6872f9cdfe4c6 (2026-10-13 22:00:56)
- c1 irq_L1di_asap: program sha256 1e167145caf02b1b, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file dcc6872f9cdfe4c6 (2026-10-13 22:00:56)
- c1 irq_L1di_wait: program sha256 78acf10c6bafb730, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file dcc6872f9cdfe4c6 (2026-10-13 22:00:56)
- c1 irq_L2_asap: program sha256 8c90c692e7d04450, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file dcc6872f9cdfe4c6 (2026-10-13 22:00:56)
- c1 irq_L2_wait: program sha256 947cc14a35a872fd, no build manifest; at run time commit d3ab82a8f8 + changes: Time_Predictable_TTA.xpr, bitstream file dcc6872f9cdfe4c6 (2026-10-13 22:00:56)

## Change from the historical runs

The first 14 runs (2026-10-11, bitstreams before the A5-1 fix, tables in the
git history of this file up to commit ecaee4e) ran on a memory that served a
new transaction at BURST_LAT whenever the bus cycle had not dropped since the
previous one. All periods, release->act min/p50/p99/max and deadline misses:

| config | historical | corrected |
|---|---|---|
| c1 L0 asap | 312/339/353/568, 0 | 312/339/353/568, 0 |
| c1 L1d wait | 1019/1026/1033/1033, 0 | 1019/1026/1033/1033, 0 |
| c1 L2 asap | 549/655/1107/1274, 0 | 534/655/1112/1274, 0 |
| c1 L2 wait | 1019/1027/1151/1283, 0 | 1019/1027/1151/1281, 0 |
| c0 L0 asap | 887/963/1017/1084, 0 | 1127/1224/1289/1396, 0 |
| c0 L0 wait | 1032/1044/1059/1103, 0 | 1162/1257/1324/1433, 0 |
| c0 L1d wait | 1032/1043/1059/1109, 0 | 1162/1266/1334/1446, 0 |
| c0 L2 asap | 913/997/2091/2260, 169 | 1134/1240/3373/3565, 1109 |
| c0 L2 wait | 1033/1057/2123/2293, 191 | 1170/1278/3412/3592, 1138 |

With the caches on, the L0, L1d and L1di rows are unchanged or differ by a
few cycles in single statistics (L1di wait release->sense max 578 -> 559),
and the L2 rows by a few cycles; with the caches off every row is slower and
the control step alone now takes longer than 1,001 cycles (c0 L0 asap
release->act min 1,127), so c0 wait mode never waits and never meets the
fixed slot.
