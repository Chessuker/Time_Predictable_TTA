# Phase 6 baseline: measurement definitions

How the NEORV32 baseline is measured and what each reported number means.
Interrupt mode is committed (6343d5e); this file also fixes, before any
polling-mode run, the definitions, the critical section and the selection
rule of the polling chunk size.

All times are in cycles of the 100 MHz clock. `release` is the first cycle
with mtime >= mtimecmp in `baseline/rtl/xbus_hil.sv`.

## 1. Monitor records (board and simulation)

Per release the xbus_hil monitor sends one NRV1 record: release, the offsets
of START (first write of word 7), SENSE (first read of ENCODER) and ACT
(first write of PWM_CMD) from the release, the encoder value read, the
pwm_cmd written, and flags (events seen, repeated event, dropped words,
BG_IN count and background cycles in [release, next release)). Software only
causes the events; the timestamps are taken in hardware.

## 2. Statistics (`host/baseline/analysis.py`)

| name | definition |
|---|---|
| release->start, release->sense, release->act | offsets in the record |
| sense->act | act - sense |
| release interval | release[k+1] - release[k] - 100,000; 0 whenever the sequence check passes |
| sense interval | (release[k+1] + sense[k+1]) - (release[k] + sense[k]) - 100,000, consecutive k only: the time between two SENSE events minus the period (the plan's "period jitter"). It is not a jitter of the releases. |
| deadline miss | ACT missing, or act >= 2,000 |

Every quantity is reported for all periods (primary; period 0 runs with cold
caches) and for the steady state (k >= 1). A run's verdict "integrity OK"
means the sequence check (k in order, releases exactly one period apart, each
event once, no dropped words) and the model replay found nothing; it says
nothing about deadlines. Deadline misses are printed with every run as
"N of M periods". Miss counts are observed results of the runs that were
made (10,000 periods cover all 6,849 relative phases of the L2 timer and the
release), not guaranteed rates.

## 3. Polling mode (TRIG = 1)

### 3.1 Program

```
loop:
    N units (N = CHUNK, 1..256), continuing where the last chunk stopped
    poll: csrr mip; if MTIP == 0, goto loop
    critical section (3.2): START, control step
    goto loop
```

- L0: no units; the loop is the poll alone.
- L1d unit: one read-modify-write of the word at the start of thrash line i
  (16 KB array, 64-byte lines), lines in order 0..255, 0..255, ... A chunk
  is the same loop as a sweep of interrupt mode over N lines (7
  instructions per unit). When N does not divide 256, a chunk that reaches
  line 255 finishes the sweep and goes on from line 0 in a second loop of
  the same shape (one compare per chunk more); the line index is masked to
  0..255 between chunks.
- L1di unit: one L1d unit and one 32-byte piece (8 instructions) of the code
  chain: 256 pieces, line aligned, each 5 nops, a decrement of t0 and a
  branch that falls through into the next piece while t0 > 0, then a return
  that only the last piece of a call executes; after piece 255 a jump leads
  back to piece 0. A chunk makes one call that runs the pieces of its N
  units (wrapping with them). A call per unit would add about 3 instructions
  per unit.
- L2: L1d units; the GPTMR interrupt (FIRQ 12) stays enabled, the timer
  interrupt (MTIE) is never enabled: MTIP is only read from mip.
- Polling builds align loop heads to 4 bytes (`-falign-loops=4`); the
  board's interrupt-mode sweep loop happens to be aligned. NEORV32 fetches
  32-bit words, so a loop head at a 2-byte offset costs fetch cycles per
  iteration that have nothing to do with polling.

Whether this is the same background work as interrupt mode is measured on
the bus and in the trace, not inferred from the source (3.5).

#### Interrupt-mode references for the throughput ratio

- L1d (c1, c0): the committed interrupt-mode program, the one run on the
  board.
- L1di: the board's L1di program (6343d5e) runs a straight block of 2048
  nops that starts at a 2-byte offset (0x17e), so each 4-byte nop spans two
  fetch words; that is the workload of the board results and stays as it is.
  For the throughput ratio a separate simulation reference is used,
  `TRIG=0 LOAD=2 BG_CODE=1`: interrupt mode with the same line-aligned code
  chain as polling, called once per sweep for all 256 pieces, loop heads
  aligned. Its results are labelled "simulation reference" and are never
  mixed with the board's L1di results.

### 3.2 Critical section

```
    csrrci  s, mstatus, MIE     ; begins: s keeps the previous MIE, interrupts off
    sw      START               ; first store after detection
    control step                ; SENSE, PID, (wait), ACT, release += period,
                                ; MTIMECMP_HI, MTIMECMP_LO (clears MTIP)
    if (s & MIE) csrsi mstatus, MIE   ; ends: the previous state is restored
```

- It begins when the `csrrci` retires and ends when the restoring `csrsi`
  retires (the CPU trace gives both cycles). START, SENSE, ACT and the
  MTIMECMP update are inside it.
- A GPTMR request during the section is not lost: the GPTMR interrupt bit
  is sticky until the handler writes CSR1.IRQ = 0 (`neorv32_gptmr.vhd`), so
  mip.FIRQ12P stays pending and the trap is taken right after the `csrsi`
  retires. Only the handler acknowledges it. The section (at most about
  1,100 cycles in wait mode) is far shorter than the GPTMR period (13,698),
  so at most one request can be pending; the timer keeps counting, later
  requests do not move.
- With MIE already 0 (L0, L1d, L1di) the section changes nothing.
- An interrupt between the poll and the `csrrci` runs before START, as a
  background handler running at the release does in interrupt mode.

### 3.3 Instruction trace (simulation only)

`neorv32_baseline` has a generic `TRACE_EN` (default false: synthesis and the
bitstreams are unchanged). A second GHDL conversion with TRACE_EN = 1 turns
on NEORV32's trace port (TRACE_PORT_EN), which reports every retired
instruction with its pc, CSR access and memory access. The bench prints from
it the poll reads of mip, the stores into the thrash array, the instructions
of the code block and the critical-section CSR writes. The trace generator
only observes; a test requires the monitor records and the XBUS trace of the
interrupt-mode programs to be identical with and without it.

### 3.4 Quantities

For each poll j, a_j is the cycle in which the `csrr mip` of the poll loop
retires, and m_j its MTIP bit.

- **Chunk interval** Q_j = a_(j+1) - a_j, for consecutive polls with no
  critical section between them. A chunk runs from just after one poll to
  the next poll, so Q_j is the chunk start-to-start interval. Every unit of
  the chunk has retired by a_(j+1): the CPU is in order and a store retires
  only after its bus acknowledge (no store buffer), so a_(j+1) is the
  completion of the chunk including its final unit.
- **C_max(N)** = max Q_j over the run.
- **MTIP latency** L_mip: polls retiring before release + L_mip read 0, from
  release + L_mip they read 1. Measured from the polls that fall near a
  release (largest a - release that still read 0, plus 1; checked against
  the smallest a - release that read 1).
- **Detection delay** of a release r: a_d - r, where a_d is the first poll
  at or after r that reads 1 (the detecting poll) and a_prev = a_(d-1) the
  poll immediately before it. Two polls must be told apart: a_prev, the last
  poll before the detecting one, and the last poll before the release. They
  differ when a poll falls in [r, r + L_mip): it comes after the release but
  still reads 0, because MTIP is not visible yet. In both cases a_prev read
  0, so a_prev < r + L_mip, and a_d read 1, so a_d >= r + L_mip. Hence
  a_d - r = (a_d - a_prev) + (a_prev - r) < Q_(d-1) + L_mip <= C_max + L_mip.
  When a_prev <= r, the chunk between a_prev and a_d is the one running at
  the release; when a_prev > r, the chunk running at the release ended at
  a_prev and the chunk between a_prev and a_d started after the release, and
  the bound holds the same way. The sweep checks the inequality on every
  record and counts the records with a_prev > r.
- **P_j** = (release + start) - a_d: from the detecting poll's retirement to
  the START strobe (branch, `csrrci`, the store). P_max = max P_j.
- **W_j** = act - start (monitor). W_max = max W_j. A record without START
  or ACT is a deadline miss and a missing event; it is left out of the
  distributions, and a run with any missing event cannot be selected.
- **B(N)** = C_max + L_mip + P_max + W_max. This is an empirical
  conservative bound: a sum of separately measured maxima, which is at least
  any release->act the same measurements could produce. It is not a formal
  WCET: a path never executed in simulation is not covered.
- **Units**: retired stores into the thrash array (data units) and retired
  instructions at the start of each 32-byte piece of the code (code units:
  the 256 pieces of the chain, without its wrap jump; for the board's
  straight block, every 32 bytes from its start), counted from the trace in
  [release_k, release_(k+1)) for the warm periods k = 1..K, in both modes.
  The primary throughput ratio uses these intervals only.
- **Throughput ratio** R(N, c, load) = data units (polling, N) / data units
  (interrupt mode), same cache c, load, K and START_DELAY. The interrupt
  reference is the committed interrupt-mode program, unchanged.
- **Cache behaviour** (XBUS trace): D$ fills, D$ write-backs and I$ fills per
  period; inside the control window [release, MTIMECMP write + 40] (as in
  interrupt mode) and, reported separately, from START to the same end (in
  polling the release window also holds the rest of the chunk); the sets of
  thrash lines filled and written back and, for L1di, the code lines filled.

#### Audit of the intervals (every record, from the retirement trace)

- chunk completion: between a_prev (the poll immediately before the
  detecting poll) and a_d exactly N units retire: the chunk polled at a_d
  completed, final unit included. This is the chunk running at the release
  when a_prev <= release, and the next one when a_prev > release (see the
  detection delay above)
- MTIP latency: a_prev retired before release + L_mip; every poll from
  release + L_mip until START reads 1
- P: no unit store and no poll between the detecting poll and START
- W: START and ACT lie after the section's `csrrci` and, when MIE was on,
  before its `csrsi`
- the order of units (line i + 1 after line i, 0 after 255), N units per
  chunk, the code pieces in the same order as the data units
- the position of the release in its chunk, (release - a_prev) /
  (a_d - a_prev), is reported to show which parts of a chunk were covered

Critical sections: the MIE bit read by `csrrci` is 1 in L2 and a `csrsi`
follows every section; with MIE 0 (L0, L1d, L1di) there is no `csrsi`.

Background requests (L2), from the observed bodies only (`gptmr_audit`). The
request times are estimated as T0 + k x 13,698 with T0 = min(BG_IN_k - k x
13,698), which assumes that no request before the first observed body is
missing. Inside the window [first observed BG_IN, last observed BG_IN] the
audit shows: no body starts inside a section; a request estimated inside a
section starts its body within 200 cycles after the section ends;
consecutive bodies are between 0.5 and 1.5 periods apart, and the number of
bodies equals the window length in periods plus one, so no request between
the first and the last body was lost or doubled. It cannot show a request
lost before the first or after the last observed body, nor a loss and a
duplicate cancelling inside one interval; sections outside the window are
counted and reported as not covered. It is not an independent proof that no
request was lost; the monitor's per-period BG_IN counts come from the same
events.

### 3.5 Selection of N

Selection configurations, all in ASAP mode: c1 L1d, c1 L1di, c0 L1d.

- Timing safety, for every selection configuration, with D = 2,000 cycles
  and reserved slack S = 500 (budget 1,500):
  - observed release->act max <= 1,500, and
  - B(N) <= 1,500.
- Throughput: R >= 0.98 in every selection configuration.
- N is the smallest value of the sweep {1, 2, 4, 8, 16, 32, 64} that meets
  both, with a clean integrity check and audit and no missing event in
  every selection configuration. If none does, the sweep is extended (N
  need not divide 256); the criteria are not relaxed.
- The initial runs are 120 periods. If an R falls in [0.975, 0.985] or a
  B(N) or observed maximum in [1,450, 1,550], that N is rerun with 1,000
  periods before the decision.
- Workload equivalence: per full sweep, the same 256 thrash lines are filled
  and written back as in interrupt mode, and for L1di the same 128
  code-block lines are filled; the interrupt-mode cache checks (every data
  line of the control step refilled after a dirty write-back, no I$ fill in
  the L1d control window) must hold.
- L2 and wait mode are reported at the chosen N, not used for selection.

The full table of the sweep, failed candidates included, is kept with the
results.

Results belong to one methodology: `host/baseline/poll_sweep.py` writes them
to `build/baseline_poll_sweep/<methodology>/` together with a fingerprint of
every source that shapes a result (program, bench, netlist wrapper,
analysis). An existing results file with another methodology or fingerprint
is refused, and candidates are selected only from runs carrying the current
fingerprint. The first sweep (straight interrupt-mode L1di block as
reference, per-unit loop with a separate counter, unaligned loop heads) is
not reused.

### 3.6 Outcome: no tested N meets the criteria

The 120-period sweep (N = 1, 2, 4, 8, 16, 32, 64, extended to every N in
c1 L1d 9-15, c1 L1di 5-7, c0 L1d 9-31; table in
`docs/baseline_poll_sweep_120.md`) found no N that meets both criteria in
every selection configuration. Every run has a clean integrity check and
audit and no missing event.

| config | largest tested N passing timing | R there | smallest tested N with R >= 0.98 | B(N) there |
|---|---|---|---|---|
| c1 L1d | N = 10 (B 1,432, observed 1,316) | 0.9810 | N = 10 | 1,432 |
| c1 L1di | N = 6 (B 1,451, observed 1,296) | 0.9487 | N = 32 | 4,512 |
| c0 L1d | N = 9 (B 1,454, observed 1,390) | 0.9354 | N = 27 | 2,840 |

c1 L1d alone passes at N = 10. In c1 L1di and c0 L1d no tested N passes
both: at every tested N where timing passes, R is below 0.95, and at every
tested N where R passes, B(N) is above 2,800.

The result covers the N tested: c1 L1d 1, 2, 4, 8-16, 32, 64; c1 L1di 1,
2, 4-8, 16, 32, 64; c0 L1d 1, 2, 4, 8-32, 64. The other values were not
run. B(N) and R mostly grow with N over the tested values, but not strictly (c0 L1d:
R 0.9733 at N = 16, 0.9670 at N = 17), so the trend is no proof for the
untested ones.

The 1,000-period reruns were not made, because they cannot change the
outcome for the N tested. The simulation is deterministic from cycle 0
with the same binary, so the first 120 periods of a 1,000-period run are
the runs above, and B(N) and the observed maximum, both maxima, can only
stay or grow: no N that fails timing can pass. The tested N that pass
timing in c1 L1di (<= 6) and c0 L1d (<= 9) have R between 0.73 and 0.95, outside the [0.975, 0.985]
band where a rerun could change the R verdict.

The thresholds are unchanged. No N is chosen, so polling mode has no board
runs; the trade-off between throughput and the release->act bound is the
result reported for polling.
