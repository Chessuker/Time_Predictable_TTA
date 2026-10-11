"""Phase 6, polling mode (TRIG = 1) of baseline/sw/ctrl and its measurement
(docs/baseline_measurement.md section 3).

Without simulation: the trace analysis of host/baseline/polling.py on made-up
traces (chunk intervals, detection, MTIP latency bracket, critical sections,
the interval audit, chunk and unit accounting, background requests around
critical sections).

In simulation with the NEORV32 instruction trace:
- the trace port only observes: the interrupt-mode programs give the same
  monitor records and XBUS trace with and without it
- the MTIP latency is 4 cycles (L0 polling)
- chunk sizes that do not divide the 256-unit sweep (5, 9, 12): every unit
  in order across the sweep boundary, every chunk exactly N units, the code
  pieces following the units, every release detected within C_max + L_mip,
  the interval audit clean, units per period counted from the trace
- the interrupt-mode simulation reference (BG_CODE = 1) runs the same code
  chain, line aligned, one code unit per data unit
- the critical section: MIE read and restored as it was, START and ACT inside,
  no background body inside, a request inside it taken right after it, none
  lost or doubled (L2); nothing restored when MIE was 0 (L1d)
- the control law over 620 short periods (L0 ASAP and wait, L2) with the
  model replay against the RTL plant
"""
import pytest

from host.baseline import analysis as A, bustrace as bt, polling as PL

from .test_baseline_ctrl import GPTMR_PERIOD, MAX_CYCLES, PERIOD, SHORT, START_DELAY, build, simulate
from .test_lockstep import pytestmark  # noqa: F401  (same skip rule)

L_MIP = 4                        # cycles from a release until a poll of mip reads MTIP = 1 (upper bound)
FUNC_CHUNK = 4                   # chunk size of the functional tests (not the selected one)
CSRRCI, CSRSI = 0x3004_7073, 0x3004_6073     # csrrci x0?, mstatus, 8 / csrsi mstatus, 8 (funct3 is what counts)


# ---------------------------------------------------------------- analysis on made-up traces
def rec(k, rel, start, act, sense=None):
    return bt.Record(k, rel, start, sense if sense is not None else start + 5, act, 0, 0, 0b111)


def trace(polls=(), stores=(), csr=(), pieces=()):
    return bt.ITrace(list(polls), list(csr), list(stores), list(pieces), [])


def test_chunk_intervals_skip_the_control_step():
    t = trace(polls=[(100, 0), (200, 0), (300, 0x80), (1000, 0), (1090, 0)])
    assert PL.chunk_intervals(t) == [100, 100, 90]            # 300 -> 1000 holds the control step


def test_detection_and_mtip_bracket():
    t = trace(polls=[(98, 0), (103, 0), (105, 0x80), (196, 0), (204, 0x80)])
    r = [rec(0, 100, 20, 400), rec(1, 200, 20, 400)]
    d = PL.detections(r, t)
    assert [(a, b) for _, a, b in d] == [(103, 105), (196, 204)]
    assert PL.mip_latency(d) == (4, 4)                         # 103 read 0 (release + 3), 204 read 1 (+ 4)


def test_critical_sections_keep_the_old_mie():
    t = trace(csr=[(10, 0, 0x300, CSRRCI, 0x1888), (50, 0, 0x300, CSRSI, 0x1880),
                   (90, 0, 0x300, CSRRCI, 0x1880)])
    assert PL.critical_sections(t) == [(10, 50, 1), (90, None, 0)]


def make_run(chunk=3, rel=1000, unit=10, release_at=2):
    """polls every chunk of `chunk` units of `unit` cycles; the release comes
    `release_at` units into a chunk; detection, START 12 later, ACT 300 later"""
    polls, stores, csr = [], [], []
    c = 0
    while True:
        for _ in range(chunk):
            c += unit
            stores.append((c, 0x80000040 + 64 * (len(stores) % 256)))
        c += 4
        if c >= rel + L_MIP:
            polls.append((c, 0x80))
            csr.append((c + 6, 0, 0x300, CSRRCI, 0))
            return trace(polls=polls, stores=stores, csr=csr), rec(0, rel, c + 12 - rel, c + 312 - rel)
        polls.append((c, 0))


def test_audit_accepts_a_correct_run_and_flags_broken_ones():
    t, r = make_run()
    v = PL.audit([r], t, 3, (L_MIP, L_MIP), load=1)
    assert {k: v[k] for k in v if k != "release_position"} == \
        {"chunk_incomplete": 0, "l_mip": 0, "p_interval": 0, "w_outside_section": 0, "undetected": 0,
         "missing_events": 0, "a_prev_after_release": 0}
    # a unit missing from the chunk that was running at the release
    t2 = bt.ITrace(t.polls, t.csr, t.stores[:-1], [], [])
    assert PL.audit([r], t2, 3, (L_MIP, L_MIP), load=1)["chunk_incomplete"] == 1
    # a unit between detection and START
    a_d = t.polls[-1][0]
    t3 = bt.ITrace(t.polls, t.csr, t.stores + [(a_d + 3, 0)], [], [])
    assert PL.audit([r], t3, 3, (L_MIP, L_MIP), load=1)["p_interval"] == 1
    # START before the critical section began
    t4 = bt.ITrace(t.polls, [(a_d + 20, 0, 0x300, CSRRCI, 0)], t.stores, [], [])
    assert PL.audit([r], t4, 3, (L_MIP, L_MIP), load=1)["w_outside_section"] == 1


def test_chunk_and_unit_accounting_across_the_sweep_end():
    # chunk 5 over 256 lines: chunks cross the end of the sweep
    stores, polls, c = [], [], 0
    for j in range(60):
        for _ in range(5):
            c += 10
            stores.append((c, 0x80000040 + 64 * (len(stores) % 256)))
        c += 4
        polls.append((c, 0))
    t = trace(polls=polls, stores=stores)
    assert PL.chunk_accounting(t, 5, thrash_base=0x80000040) == (0, 0)
    skipped = trace(polls=polls, stores=stores[:100] + stores[101:])          # a unit lost
    assert PL.chunk_accounting(skipped, 5, thrash_base=0x80000040) == (1, 1)
    p = [(s, 0x180 + 32 * ((i + 7) % 256)) for i, (s, _) in enumerate(stores)]
    assert PL.piece_accounting(trace(pieces=p), 0x180) == 0
    assert PL.piece_accounting(trace(pieces=p[:10] + p[11:]), 0x180) == 1


def test_units_are_counted_over_release_intervals():
    recs = [rec(0, 0, 10, 20), rec(1, 100, 10, 20), rec(2, 200, 10, 20)]
    cyc = [5, 99, 100, 150, 199, 200, 250, 299, 300]
    assert PL.per_period_counts(recs, cyc, period=100) == [3, 3]            # k = 1, 2: [100, 200), [200, 300)


def test_background_requests_around_critical_sections():
    P = 1000
    secs = [(2990, 3100, 1), (5500, 5600, 1)]
    ok = [(20, 120), (1020, 1120), (2020, 2120), (3110, 3210), (4020, 4120), (5020, 5120)]
    a = PL.gptmr_audit(ok, secs, P)
    assert {k: a[k] for k in ("inside", "late_after_section", "gaps", "deferred", "count_error")} == \
        {"inside": 0, "late_after_section": 0, "gaps": 0, "deferred": 1, "count_error": 0}  # tick 3000 waited
    assert a["sections_outside_window"] == 1                   # (5500, 5600) is after the last body
    assert PL.gptmr_audit(ok[:3] + [(3050, 3150)] + ok[4:], secs, P)["inside"] == 1
    assert PL.gptmr_audit(ok[:3] + ok[4:], secs, P)["gaps"] == 1                    # the deferred one lost
    assert PL.gptmr_audit(ok[:3] + [(3400, 3500)] + ok[4:], secs, P)["late_after_section"] == 1


# ---------------------------------------------------------------- simulation
def traced(name, cache, max_cycles, bus_end=0, bus_h=False, **defs):
    hexfile, syms = build(name, START_DELAY=START_DELAY, **defs)
    from host.baseline.poll_sweep import ranges
    thrash, code = ranges(syms, defs.get("LOAD", 0))
    plus = f"+itrace +tr_data={thrash[0]:x}:{thrash[1]:x} +tr_code={code[0]:x}:{code[1]:x} +bus_end={bus_end}"
    if bus_h:
        plus += " +bus_h"
    reqs, recs = simulate(cache, hexfile, max_cycles, plusargs=plus, trace=1)
    itr = bt.parse_itrace((hexfile.parent / f"sim_c{cache}t.log").read_text())
    return reqs, recs, itr, thrash, code, syms


@pytest.mark.parametrize("cache", [1, 0])
def test_trace_port_does_not_change_timing(cache):
    hexfile, _ = build("irq_L1d_eq", TRIG=0, LOAD=1, ACT=0, START_DELAY=START_DELAY)
    reqs0, recs0 = simulate(cache, hexfile, MAX_CYCLES)
    reqs1, recs1 = simulate(cache, hexfile, MAX_CYCLES, trace=1)
    assert len(recs0) >= 4 and recs0 == recs1
    assert reqs0 == reqs1


@pytest.mark.parametrize("cache", [1, 0])
def test_mtip_latency(cache):
    _, recs, itr, *_ = traced("poll_L0_lmip", cache, 300_000 + 21 * PERIOD, TRIG=1, LOAD=0, ACT=0, CHUNK=1)
    lo, hi = PL.mip_latency(PL.detections(recs[:20], itr))
    assert hi == L_MIP and lo is not None and lo <= hi


@pytest.mark.parametrize("cache, load, chunk", [(1, 2, 5), (1, 1, 9), (0, 1, 12)])
def test_chunks_that_do_not_divide_the_sweep(cache, load, chunk):
    n = 30
    _, recs, itr, thrash, code, _ = traced(f"poll_L{load}_N{chunk}_acct", cache, 300_000 + (n + 1) * PERIOD,
                                        TRIG=1, LOAD=load, ACT=0, CHUNK=chunk)
    recs = recs[:n]
    assert A.check_sequence(recs) == [] and A.replay(recs) == []
    assert PL.chunk_accounting(itr, chunk, thrash_base=thrash[0]) == (0, 0)
    assert len(itr.stores) > 3 * 256                                       # several sweep boundaries crossed
    if load == 2:
        assert PL.piece_accounting(itr, code[0]) == 0
        # the code pieces follow the data units one for one (crt0 clears the
        # array word by word up to its last word, which no unit writes)
        last_clear = max(i for i, (_, a) in enumerate(itr.stores) if a == thrash[1] - 4)
        units = [(a - thrash[0]) // 64 for _, a in itr.stores[last_clear + 1:]]
        assert [(pc - code[0]) // 32 for _, pc in itr.pieces[:600]] == units[:600]
    v = PL.audit(recs, itr, chunk, (L_MIP, L_MIP), load)
    assert {k: v[k] for k in v if k not in ("release_position", "a_prev_after_release")} == \
        {"chunk_incomplete": 0, "l_mip": 0, "p_interval": 0, "w_outside_section": 0, "undetected": 0,
         "missing_events": 0}
    t = PL.timing(recs, itr, L_MIP)
    assert t["detection_bound_holds"]
    u = PL.units(recs, itr)
    assert u["periods"] == n - 1 and u["data_units"] > 0
    if load == 2:
        assert abs(u["code_units"] - u["data_units"]) <= chunk
    # a critical section per release, MIE was 0, nothing restored
    secs = PL.critical_sections(itr)
    assert len(secs) >= n and all(e is None and mie == 0 for _, e, mie in secs)


def test_interrupt_mode_reference_runs_the_same_code_chain():
    n = 30
    reqs, recs, itr, thrash, code, syms = traced("irq_L2_ref_chk", 1, 300_000 + (n + 1) * PERIOD,
                                                 bus_end=1_400_000, TRIG=0, LOAD=2, ACT=0, BG_CODE=1)
    recs = recs[:n]
    lo, hi = syms["ctrl_code_pieces"]
    assert lo % 64 == 0 and hi - lo == 256 * 32 + 4                    # line aligned, 256 pieces + the wrap jump
    assert A.check_sequence(recs) == [] and A.replay(recs) == []
    assert PL.piece_accounting(itr, code[0]) == 0
    u = PL.units(recs, itr)
    assert abs(u["code_units"] - u["data_units"]) <= 256                # one sweep of pieces per sweep of lines
    cb = PL.cache_behaviour(recs, bt.transfers(reqs), thrash, code)
    assert cb["thrash_lines_filled"] == cb["thrash_lines_written_back"] == 256
    assert cb["code_lines_filled"] >= 128


@pytest.mark.parametrize("cache", [1, 0])
def test_critical_section_with_background_interrupts(cache):
    n = 120
    reqs, recs, itr, *_ = traced(f"poll_L3_cs", cache, 300_000 + (n + 1) * PERIOD, bus_end=10**9, bus_h=True,
                                 TRIG=1, LOAD=3, ACT=1, CHUNK=FUNC_CHUNK)   # wait mode: the longest sections
    recs = recs[:n]
    assert A.check_sequence(recs) == [] and A.replay(recs) == []
    secs = [s for s in PL.critical_sections(itr) if s[0] < recs[-1].rel + PERIOD]
    assert len(secs) >= n
    assert all(mie == 1 and e is not None for _, e, mie in secs)          # MIE read as 1, restored
    for r in recs:                                                       # START and ACT inside a section
        assert any(b < r.rel + r.start and r.rel + r.act < e for b, e, _ in secs), r
    bodies = bt.bg_bodies(reqs)
    a = PL.gptmr_audit(bodies, secs, GPTMR_PERIOD, first_cycle=recs[0].rel)
    assert a["inside"] == a["late_after_section"] == a["gaps"] == a["count_error"] == 0, a
    assert a["deferred"] > 0                                             # the case did occur
    for r in recs:                                                       # monitor counts as the trace
        lo, hi = r.rel, r.rel + PERIOD
        assert r.bg_n == sum(1 for i, _ in bodies if lo <= i < hi)


@pytest.mark.parametrize("load, act", [(0, 0), (0, 1), (3, 0)])
def test_polling_control_law_over_620_periods(load, act):
    hexfile, _ = build(f"poll_short_L{load}_act{act}", TRIG=1, LOAD=load, ACT=act, CHUNK=FUNC_CHUNK,
                       PERIOD_CYC=SHORT, START_DELAY=START_DELAY)
    _, recs = simulate(1, hexfile, 120_000 + 621 * SHORT, bus=False)
    recs = recs[:620]
    assert A.check_sequence(recs, period=SHORT) == []
    assert A.replay(recs) == []
    assert max(abs(r.enc) for r in recs) > 4000


# ---------------------------------------------------------------- review fixes (no simulation)
def test_a_poll_after_the_release_that_still_reads_zero():
    # release at 100, L_mip 4: the poll at 102 comes after the release but reads
    # 0; the chunk 102..140 started after the release and is the one detected
    polls = [(78, 0), (90, 0), (102, 0), (140, 0x80)]
    stores = [(c, 0x80000040 + 64 * i) for i, c in enumerate([80, 84, 88, 92, 96, 100, 110, 120, 130])]
    csr = [(146, 0, 0x300, CSRRCI, 0)]
    t = trace(polls=polls, stores=stores, csr=csr)
    r = rec(0, 100, 52, 352)                                   # START at 152, ACT at 452
    v = PL.audit([r], t, 3, (L_MIP, L_MIP), load=1)
    assert v["a_prev_after_release"] == 1
    assert {k: v[k] for k in ("chunk_incomplete", "l_mip", "p_interval", "w_outside_section", "undetected",
                              "missing_events")} == dict.fromkeys(
        ("chunk_incomplete", "l_mip", "p_interval", "w_outside_section", "undetected", "missing_events"), 0)
    tm = PL.timing([r], t, L_MIP)
    assert tm["c_max"] == 38 and tm["detection_delay"]["max"] == 40 and tm["detection_bound_holds"]


def test_missing_act_is_counted_and_cannot_be_selected():
    from host.baseline import poll_sweep as S
    t, r = make_run()
    lost = bt.Record(1, r.rel + PERIOD, r.start, r.sense, None, 0, 0, 0b011)
    tm = PL.timing([r, lost], t, L_MIP)                        # no crash on the missing ACT
    assert tm["missing_events"] == 1 and tm["deadline_misses"] == 1 and tm["release_act"]["n"] == 1
    clean = {"integrity": {"sequence": 0, "replay": 0}, "timing": {"missing_events": 0},
             "audit": dict.fromkeys(("chunk_incomplete", "l_mip", "p_interval", "w_outside_section", "undetected",
                                     "missing_events", "unit_order_breaks", "chunk_count_errors", "piece_breaks"), 0),
             "pass_timing": True, "pass_R": True}
    assert S.passes(clean)
    assert not S.passes({**clean, "timing": {"missing_events": 1}})
    assert not S.passes({**clean, "audit": {**clean["audit"], "missing_events": 1}})
    assert not S.passes({**clean, "integrity": {"sequence": 1, "replay": 0}})


def test_results_of_other_sources_are_refused_and_never_selected(tmp_path):
    import json
    from host.baseline import poll_sweep as S
    path = tmp_path / "results_120.json"
    old = {"methodology": "m1", "fingerprint": "abc", "periods": 120, "l_mip": {}, "reference": {"c1:L1di": 1},
           "runs": []}
    path.write_text(json.dumps(old))
    with pytest.raises(S.StaleResults):
        S.load_results(path, 120, "def")
    fresh = S.load_results(path, 120, "def", fresh=True)
    assert fresh["reference"] == {} and fresh["runs"] == [] and fresh["fingerprint"] == "def"
    path.write_text(json.dumps(fresh))
    assert S.load_results(path, 120, "def")["fingerprint"] == "def"
    # a passing set of runs for every configuration, but made by other sources
    ok = {"integrity": {"sequence": 0, "replay": 0}, "timing": {"missing_events": 0},
          "audit": dict.fromkeys(("chunk_incomplete", "l_mip", "p_interval", "w_outside_section", "undetected",
                                  "missing_events", "unit_order_breaks", "chunk_count_errors", "piece_breaks"), 0),
          "pass_timing": True, "pass_R": True, "chunk": 7}
    runs = [{**ok, "cache": c, "load": l, "methodology": S.METHODOLOGY, "fingerprint": "def"}
            for c, l in S.SELECTION.values()]
    assert S.candidates(runs, "def") == [7]
    assert S.candidates(runs, "xyz") == []                     # current sources differ: nothing selected
    stale = [dict(x, methodology="m1") for x in runs]
    assert S.candidates(stale, "def") == []


def test_what_the_background_audit_can_and_cannot_see():
    P = 1000
    secs = [(2990, 3100, 1)]
    ok = [(20 + k * P, 120 + k * P) for k in range(6)]
    ok[3] = (3110, 3210)                                       # deferred by the section
    a = PL.gptmr_audit(ok, secs, P)
    assert (a["gaps"], a["count_error"], a["late_after_section"], a["inside"]) == (0, 0, 0, 0)
    # a request lost between the first and the last body: seen
    a = PL.gptmr_audit(ok[:2] + ok[3:], secs, P)
    assert a["gaps"] == 1 and a["count_error"] == -1
    # a duplicate: seen
    a = PL.gptmr_audit(sorted(ok + [(2500, 2600)]), secs, P)
    assert a["gaps"] >= 1 and a["count_error"] == 1
    # the first request lost: NOT seen (the window starts at the first observed body)
    a = PL.gptmr_audit(ok[1:], secs, P)
    assert (a["gaps"], a["count_error"]) == (0, 0) and a["window"] == (ok[1][0], ok[-1][0])
    # a section before the first observed body is reported as not covered
    assert PL.gptmr_audit(ok[1:], [(500, 600, 1)] + secs, P)["sections_outside_window"] == 1


def test_fingerprint_covers_what_shapes_a_result():
    from host.baseline import poll_sweep as S
    required = ["baseline/sw/ctrl/main.c", "baseline/sw/ctrl/makefile", "baseline/sw/common/baseline.mk",
                "baseline/sim/tb_baseline.sv", "baseline/rtl/neorv32_baseline.vhd", "baseline/sim/convert.sh",
                "host/baseline/polling.py", "host/baseline/poll_sweep.py", "host/baseline/bustrace.py",
                "host/baseline/analysis.py", "tests/test_baseline_ctrl.py", "tests/test_baseline_rtl.py"]
    assert set(required) <= set(S.FINGERPRINT_SOURCES)
    m = S.manifest()
    assert {"neorv32 submodule", "netlist c1t", "netlist c0t", "tools"} <= set(m)
    assert "riscv-none-elf-gcc" in m["tools"] and "GHDL" in m["tools"] and "Verilator" in m["tools"]
    assert m["netlist c1t"] != m["netlist c0t"]                  # cache on and off are different netlists
    fp = S.fingerprint(m)
    for k in m:                                                  # any change anywhere changes the fingerprint
        assert S.fingerprint({**m, k: m[k] + "x"}) != fp, k
