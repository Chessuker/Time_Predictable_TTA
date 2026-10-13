# Phase 6 board runs: NEORV32 baseline, interrupt mode

> **Historical results (pre-A5-1 memory).** These 14 runs were made on
> 2026-10-11 with the bitstreams built that day, whose `xbus_mem` (as
> committed in 9837fde) charged FIRST_LAT only to the first access after
> `cyc` rose. The XBUS keeps `cyc` high while a request is pending and
> strobes the next request in the acknowledge cycle, so a new transaction
> that started inside an unbroken `cyc` was served at BURST_LAT 1 instead of
> FIRST_LAT 10 (audit A5-1, fixed in audit Round B). In the simulation bus
> traces of the same interrupt-mode programs this hit 30 to 50 % of the
> accesses with the caches off, and 1 to 19 line fills or single accesses
> per trace with them on. The caches-off rows below
> therefore ran on a much faster memory than the documented model, and every
> row is a result of the old memory, not of the corrected one. They are kept
> unchanged as the historical record. Replacing them needs a Vivado rebuild
> of both bitstreams and the 14 board runs again; neither has been done.

Arty A7-100T, 100 MHz, NEORV32 v1.13.6 (rv32imc + Zicntr), XBUS memory with
FIRST_LAT 10 and BURST_LAT 1 as built then (see the note above). 14 runs of
10,000 periods (100,000 cycles each),
recorded on 2026-10-11 with

    py -m host.baseline.board run <port> --cache 1
    py -m host.baseline.board run <port> --cache 0

and tabulated by `py -m host.baseline.board report` from the records in
`build/baseline_board/` (not in git), copied here unchanged. These runs carry
no record of the uploaded program or the bitstream (their `summary.json` has
only `git describe`, `9837fde-dirty`); runs made from now on record both,
with a build manifest written when each program is built (audit A5-3). Definitions:
`docs/baseline_measurement.md` sections 1 and 2. Every period is timestamped
by the xbus_hil monitor in hardware. Integrity OK means the record sequence
check and the model replay passed; it says nothing about timing. Deadline
misses are observed counts over these runs, not guaranteed rates.

Loads: L0 none; L1d D$ thrashing (16 KB, one word per 64 B line); L1di L1d
plus an 8 KB straight code block (cache on only; this board binary differs
from the code-chain reference used in simulation, see
`docs/baseline_measurement.md` section 3.1); L2 L1d plus a GPTMR interrupt at
7300.3 Hz with a fixed 60-iteration handler. asap: actuate when computed;
wait: busy-wait to release + 1001, then actuate.

All periods (primary, period 0 with cold caches included). Cycles, min/p50/p99/max. sense interval = consecutive SENSE minus 100,000 (releases are exact: release interval column).
| cache | config | integrity | misses | release->start | release->sense | sense->act | release->act | sense interval | release interval |
|---|---|---|---|---|---|---|---|---|---|
| c0 | irq_L0_asap | OK | 0/10000 | 54/62/69/69 | 530/538/545/612 | 357/424/472/472 | 887/963/1017/1084 | -82/-2/14/81 | 0/0/0/0 |
| c0 | irq_L0_wait | OK | 0/10000 | 54/61/69/69 | 530/537/545/609 | 450/508/527/528 | 1032/1044/1059/1103 | -72/2/9/75 | 0/0/0/0 |
| c0 | irq_L1d_asap | OK | 0/10000 | 47/58/72/72 | 523/534/548/611 | 357/424/472/472 | 881/959/1018/1083 | -79/-4/22/87 | 0/0/0/0 |
| c0 | irq_L1d_wait | OK | 0/10000 | 47/63/72/72 | 523/539/548/615 | 450/508/527/537 | 1032/1043/1059/1109 | -76/-1/23/84 | 0/0/0/0 |
| c0 | irq_L2_asap | OK | **169/10000** | 43/62/1151/1285 | 549/568/1657/1791 | 359/426/474/474 | 913/997/2091/2260 | -1232/0/1094/1226 | 0/0/0/0 |
| c0 | irq_L2_wait | OK | **191/10000** | 47/61/1156/1292 | 553/567/1662/1798 | 387/486/502/509 | 1033/1057/2123/2293 | -1240/0/1094/1238 | 0/0/0/0 |
| c1 | irq_L0_asap | OK | 0/10000 | 29/32/35/91 | 173/176/179/339 | 139/162/174/229 | 312/339/353/568 | -165/-1/6/32 | 0/0/0/0 |
| c1 | irq_L0_wait | OK | 0/10000 | 29/32/35/93 | 173/176/179/341 | 678/851/860/860 | 1019/1026/1033/1033 | -164/2/6/30 | 0/0/0/0 |
| c1 | irq_L1d_asap | OK | 0/10000 | 27/46/91/96 | 380/429/475/526 | 170/222/234/289 | 585/651/708/815 | -104/3/66/103 | 0/0/0/0 |
| c1 | irq_L1d_wait | OK | 0/10000 | 41/76/85/88 | 421/459/469/518 | 513/566/605/611 | 1019/1026/1033/1033 | -72/-1/32/57 | 0/0/0/0 |
| c1 | irq_L1di_asap | OK | 0/10000 | 85/100/149/149 | 463/524/558/586 | 285/338/380/389 | 787/870/935/966 | -76/-2/62/88 | 0/0/0/0 |
| c1 | irq_L1di_wait | OK | 0/10000 | 56/85/120/127 | 434/517/552/578 | 442/514/562/592 | 1019/1028/1033/1033 | -94/-1/73/92 | 0/0/0/0 |
| c1 | irq_L2_asap | OK | 0/10000 | 27/52/558/710 | 325/428/883/1038 | 172/224/236/262 | 549/655/1107/1274 | -683/-4/451/616 | 0/0/0/0 |
| c1 | irq_L2_wait | OK | 0/10000 | 27/52/584/705 | 324/436/912/1033 | 217/595/673/703 | 1019/1027/1151/1283 | -669/1/472/600 | 0/0/0/0 |

Steady state (periods k >= 1). Cycles, min/p50/p99/max. sense interval = consecutive SENSE minus 100,000 (releases are exact: release interval column).
| cache | config | integrity | misses | release->start | release->sense | sense->act | release->act | sense interval | release interval |
|---|---|---|---|---|---|---|---|---|---|
| c0 | irq_L0_asap | OK | 0/9999 | 54/62/69/69 | 530/538/545/612 | 357/424/472/472 | 887/963/1017/1084 | -82/-2/14/81 | 0/0/0/0 |
| c0 | irq_L0_wait | OK | 0/9999 | 54/61/69/69 | 530/537/545/609 | 450/508/527/528 | 1032/1044/1059/1103 | -72/2/9/75 | 0/0/0/0 |
| c0 | irq_L1d_asap | OK | 0/9999 | 47/58/72/72 | 523/534/548/611 | 357/424/472/472 | 881/959/1018/1083 | -79/-4/22/87 | 0/0/0/0 |
| c0 | irq_L1d_wait | OK | 0/9999 | 47/63/72/72 | 523/539/548/615 | 450/508/527/537 | 1032/1043/1059/1109 | -76/-1/23/84 | 0/0/0/0 |
| c0 | irq_L2_asap | OK | **169/9999** | 43/62/1152/1285 | 549/568/1658/1791 | 359/426/474/474 | 913/997/2095/2260 | -1232/0/1094/1226 | 0/0/0/0 |
| c0 | irq_L2_wait | OK | **191/9999** | 47/61/1156/1292 | 553/567/1662/1798 | 387/486/502/509 | 1033/1057/2127/2293 | -1240/0/1094/1238 | 0/0/0/0 |
| c1 | irq_L0_asap | OK | 0/9999 | 29/32/35/35 | 173/176/179/207 | 139/162/174/203 | 312/339/353/381 | -31/-1/6/32 | 0/0/0/0 |
| c1 | irq_L0_wait | OK | 0/9999 | 29/32/35/35 | 173/176/179/206 | 818/851/860/860 | 1019/1026/1033/1033 | -31/2/6/30 | 0/0/0/0 |
| c1 | irq_L1d_asap | OK | 0/9999 | 27/46/91/91 | 380/429/475/499 | 170/222/234/263 | 585/651/708/732 | -84/3/66/103 | 0/0/0/0 |
| c1 | irq_L1d_wait | OK | 0/9999 | 41/76/85/85 | 421/459/469/495 | 533/566/605/611 | 1019/1026/1033/1033 | -65/-1/32/57 | 0/0/0/0 |
| c1 | irq_L1di_asap | OK | 0/9999 | 85/100/149/149 | 463/524/558/586 | 285/338/380/389 | 787/870/935/966 | -76/-2/62/88 | 0/0/0/0 |
| c1 | irq_L1di_wait | OK | 0/9999 | 56/85/120/127 | 434/517/552/578 | 442/514/562/592 | 1019/1028/1033/1033 | -94/-1/73/92 | 0/0/0/0 |
| c1 | irq_L2_asap | OK | 0/9999 | 27/52/559/710 | 325/428/886/1038 | 172/224/236/236 | 549/655/1110/1274 | -683/-4/451/616 | 0/0/0/0 |
| c1 | irq_L2_wait | OK | 0/9999 | 27/52/585/705 | 324/436/912/1033 | 217/595/673/703 | 1019/1027/1152/1283 | -669/1/472/600 | 0/0/0/0 |
