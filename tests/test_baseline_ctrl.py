"""Phase 6: the baseline control program (baseline/sw/ctrl) on the whole NEORV32
system in simulation, and what its background loads really do to the caches.

The XBUS trace (tb_baseline +bus, host/baseline/bustrace.py) shows every line
fill and write-back, and with xbus_tag_o(2) whether it is an instruction fetch.
With the caches off every access is a single transfer, so that run gives the
lines the control ISR touches (its footprint); the cache-on runs must then show:

  L0   (no load)        warm periods: no fills or write-backs in the ISR at all
  L1d  (D$ thrashing)   warm periods: every ISR data line refilled, each fill
                        right after the write-back of a dirty line of the same
                        index; no I$ fill
  L1di (L1d + 8 KB code) warm periods: also I$ fills, all of ISR code lines
  L2   (L1d + GPTMR)    7-8 background bodies per period, each of the same
                        length; the monitor's counts equal the trace; the
                        control task is never preempted and computes the same

The window of a release is release .. its MTIMECMP write (+ 40 cycles of
epilogue with the cache on; with it off, until the first fetch back in the
background code). Warm = every period after the first.

The control law itself (release spacing, setpoint schedule, FixedPID on the
encoder read) is checked over 620 short periods, across two schedule steps.
"""
import shlex
import struct

import pytest

from host.baseline import analysis as A, board as BD, bustrace as bt
from host.hil import env
from host.lockstep.runner import BUILD, ROOT, wsl, wsl_path
from host.plant_model import model as m

from .test_baseline_pid import XPACK_GCC
from .test_baseline_rtl import B, SOC_SOURCES, converted, verilate
from .test_lockstep import pytestmark  # noqa: F401  (same skip rule)

BIN = "~/xpack/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-"
CTRL = ROOT / "baseline" / "sw" / "ctrl"
OUT = BUILD / "baseline_ctrl"
PERIOD = 100_000                 # real period: the cache checks depend on it (sweeps per period)
START_DELAY = 20_000
MAX_CYCLES = 650_000             # first release near 98k, then 5 more
LOADS = {"L0": 0, "L1d": 1, "L1di": 2}
ISR_SYMBOLS = ("ctrl_vectors", "ctrl_mti_entry", "ctrl_isr", "fpid_step")


def build(name, **defs):
    """make the control program with -D settings into build/baseline_ctrl/<name>; -> (hex, symbols)"""
    code, _ = wsl(f"test -x {XPACK_GCC}")
    if code != 0:
        pytest.skip("xPack riscv-none-elf-gcc not installed")
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    q = lambda p: shlex.quote(wsl_path(p))                                  # noqa: E731
    flags = " ".join(f"{k}={v}" for k, v in defs.items())
    code, text = wsl(f"cd {q(CTRL)} && make -s {flags} BUILD_DIR={q(out / 'obj')} APP_ELF={q(out / 'main.elf')} "
                     f"APP_FLT={q(out / 'elf.bin')} APP_HEX={q(out / 'prog.hex')} clean hex")
    assert code == 0 and (out / "prog.hex").exists(), text[-4000:]
    code, text = wsl(f"{BIN}nm -n -S {q(out / 'main.elf')}")
    assert code == 0, text
    syms = {}
    for row in text.splitlines():
        f = row.split()
        if len(f) == 4:
            syms[f[3]] = (int(f[0], 16), int(f[0], 16) + int(f[1], 16))
    return out / "prog.hex", syms


_VTB = {}


def simulate(cache, hexfile, max_cycles, bus=True, plusargs="", trace=0):
    """Run tb_baseline; trace=1 uses the netlist with the instruction trace port."""
    key = (cache, trace)
    tag = f"c{cache}{'t' if trace else ''}"
    if key not in _VTB:
        v = converted(cache, trace)
        _VTB[key] = verilate("tb_baseline", [f"{B}/sim/neorv32.vlt", wsl_path(v), *SOC_SOURCES,
                                             f"{B}/sim/tb_baseline.sv"], BUILD / f"baseline_sim_{tag}",
                             "-DITRACE" if trace else "")
    log = hexfile.parent / f"sim_{tag}.log"
    code, out = wsl(f"{shlex.quote(wsl_path(_VTB[key]))} +code={shlex.quote(wsl_path(hexfile))} "
                    f"+max={max_cycles} {'+bus=0' if bus else ''} {plusargs} > {shlex.quote(wsl_path(log))}; "
                    f"tail -3 {shlex.quote(wsl_path(log))}", timeout=1800)
    text = log.read_text()
    assert code == 0 and "END" in text.splitlines() and "FAIL" not in text, out[-2000:]
    return bt.parse(text)


def windows_on(trs, recs):
    return [(r, r.rel, bt.mtimecmp_write(trs, r.rel) + 40) for r in recs]


def check_records(recs, period, n_min):
    """one record per release, `period` apart, all three events, nothing extra"""
    assert len(recs) >= n_min
    assert [r.k for r in recs] == list(range(len(recs)))
    assert {b.rel - a.rel for a, b in zip(recs, recs[1:])} == {period}
    for r in recs:
        assert r.seen == 0b111 and not r.extra and not r.dropped, r
        assert 0 < r.start < r.sense < r.act < env_deadline(), r


def env_deadline():
    from host.hil import programs as P
    return P.DEADLINE


@pytest.fixture(scope="module")
def runs():
    """name -> {'syms', 0: (trs, recs), 1: (trs, recs)}"""
    out = {}
    for name, load in LOADS.items():
        hexfile, syms = build(name, TRIG=0, LOAD=load, ACT=0, START_DELAY=START_DELAY)
        out[name] = {"syms": syms}
        for cache in (0, 1):
            reqs, recs = simulate(cache, hexfile, MAX_CYCLES)
            check_records(recs, PERIOD, 4)
            out[name][cache] = (bt.transfers(reqs), recs)
    return out


def footprint(run):
    """ISR data and code lines from the cache-off run, and the background code lines."""
    syms = run["syms"]
    trs, recs = run[0]
    isr = [syms[s] for s in ISR_SYMBOLS]
    thrash = syms.get("thrash", (0, 0))

    def isr_pc(a):
        return any(b <= a < e for b, e in isr)

    data, code, bg, wins = set(), set(), set(), []
    for r in recs:
        w = bt.mtimecmp_write(trs, r.rel)
        end = next(t.cycle for t in trs if t.cycle > w and t.kind == "Ird" and not isr_pc(t.adr))
        wins.append((r.rel, end))
        for t in bt.in_window(trs, r.rel, end - 1):
            if t.kind in ("Drd", "Dwr") and not thrash[0] <= t.adr < thrash[1]:
                data.add(bt.line(t.adr))
            elif t.kind == "Ird":
                code.add(bt.line(t.adr))
    for (_, e0), (a1, _) in zip(wins, wins[1:]):
        bg |= {bt.line(t.adr) for t in bt.in_window(trs, e0, a1 - 1) if t.kind == "Ird"}
    return data, code, bg


@pytest.mark.parametrize("name", list(LOADS))
def test_caches_off_every_access_is_single(runs, name):
    trs, _ = runs[name][0]
    c = bt.count(trs)
    assert c["Ifill"] == c["Dfill"] == c["Dwb"] == 0, c
    assert c["Ird"] > 0 and c["Drd"] > 0 and c["Dwr"] > 0, c


def test_isr_footprint_is_what_the_program_says(runs):
    # the stack (two lines at the top of the 64 KB data memory), the schedule
    # and release variables, the PID state and the setpoint table (.rodata, in
    # the code memory): the lines the tests below expect to see refilled
    data, code, bg = footprint(runs["L1d"])
    syms = runs["L1d"]["syms"]
    assert {0x8000_FF80, 0x8000_FFC0} <= data
    for s in ("release", "sched_left", "sched_i", "pid"):
        assert bt.line(syms[s][0]) in data, s
    assert any(a < 0x8000_0000 for a in data)                         # .rodata from the code memory
    assert bt.line(syms["ctrl_isr"][0]) in code and bt.line(syms["fpid_step"][0]) in code


def test_l0_warm_isr_hits_in_both_caches(runs):
    trs, recs = runs["L0"][1]
    for r, a, b in windows_on(trs, recs)[1:]:
        c = bt.count(bt.in_window(trs, a, b))
        assert c["Ifill"] == c["Dfill"] == c["Dwb"] == 0, (r, c)


@pytest.mark.parametrize("name", ["L1d", "L1di"])
def test_l1_every_isr_data_line_is_a_dirty_miss(runs, name):
    data, _, _ = footprint(runs[name])
    trs, recs = runs[name][1]
    for r, a, b in windows_on(trs, recs)[1:]:
        w = bt.in_window(trs, a, b)
        fills = [t for t in w if t.kind == "Dfill"]
        wbs = [t for t in w if t.kind == "Dwb"]
        assert data <= {bt.line(t.adr) for t in fills}, (r, sorted(map(hex, data)))
        assert bt.dirty_fills(trs, fills) == fills, r                 # each fill after a write-back of its index
        assert len(wbs) >= len(data) - 1, r                           # (one write-back can fall just before release)
        assert all(t.kind not in ("Ird", "Drd", "Dwr") for t in w), r


def test_l1d_no_instruction_cache_misses(runs):
    data, code, bg = footprint(runs["L1d"])
    # no two different lines of the ISR and the background code share an I$ index
    assert not [(a, b) for a in code for b in bg if a != b and bt.index(a) == bt.index(b)]
    trs, recs = runs["L1d"][1]
    for r, a, b in windows_on(trs, recs)[1:]:
        assert bt.count(bt.in_window(trs, a, b))["Ifill"] == 0, r


def test_l1di_refills_the_isr_code(runs):
    _, code, bg = footprint(runs["L1di"])
    assert len(bg) * bt.LINE > 2 * bt.N_INDEX * bt.LINE                # the code block is twice the I$
    trs, recs = runs["L1di"][1]
    for r, a, b in windows_on(trs, recs)[1:]:
        ifills = {bt.line(t.adr) for t in bt.in_window(trs, a, b) if t.kind == "Ifill"}
        assert ifills <= code, r
        assert len(ifills) >= len(code) // 2, (r, len(ifills), len(code))


@pytest.mark.parametrize("name", ["L1d", "L1di"])
def test_l1_sweeps_fit_between_releases(runs, name):
    # the eviction above holds because a whole sweep of the 16 KB array runs
    # between the end of one ISR and the next release
    trs, recs = runs[name][1]
    first = runs[name]["syms"]["thrash"][0]
    t0 = [t.cycle for t in trs if t.kind == "Dfill" and bt.line(t.adr) == bt.line(first)]
    sweeps = [b - a for a, b in zip(t0, t0[1:]) if a > recs[0].rel]
    isr_end = max(b for _, _, b in windows_on(trs, recs)) - max(r.rel for r in recs)
    assert sweeps and max(sweeps) < PERIOD - isr_end
    print(f"{name}: sweep {min(sweeps)}..{max(sweeps)} cycles, period {PERIOD}")


# ---------------------------------------------------------------- L2: L1d + GPTMR
# 7300.3 Hz (2 x (6848 + 1) = 13,698 cycles) and BG_ITER = 60 loop iterations
# per body. Functional checks and first timings only: 120 periods cover 120 of
# the 6,849 relative phases of the GPTMR and the release.
GPTMR_PERIOD = 13_698
BODY = {1: 502, 0: 1063}         # BG_IN..BG_OUT cycles, caches on / off (no memory access in the loop)
N_L2 = 120


@pytest.fixture(scope="module")
def l2_runs():
    hexfile, _ = build("L2", TRIG=0, LOAD=3, ACT=0, START_DELAY=START_DELAY)
    out = {}
    for cache in (0, 1):
        # with the caches off crt0 clears the 16 KB array slowly: the first release is near 222k
        reqs, recs = simulate(cache, hexfile, 300_000 + (N_L2 + 1) * PERIOD, plusargs="+bus_h")
        recs = recs[:N_L2]
        assert len(recs) == N_L2
        assert [r.k for r in recs] == list(range(N_L2))
        assert {b.rel - a.rel for a, b in zip(recs, recs[1:])} == {PERIOD}
        out[cache] = (bt.bg_bodies(reqs), recs)
    return out


@pytest.mark.parametrize("cache", [1, 0])
def test_l2_background_interrupt_rate_and_body(l2_runs, cache):
    bodies, recs = l2_runs[cache]
    assert {o - i for i, o in bodies} == {BODY[cache]}               # fixed work: every body the same
    a, b = recs[0].rel, recs[-1].rel + PERIOD
    ins = [i for i, _ in bodies if a <= i < b]
    # the timer itself is exact; each BG_IN is late by its entry latency (< 2,000 cycles)
    assert abs((ins[-1] - ins[0]) - (len(ins) - 1) * GPTMR_PERIOD) < 2_000
    assert abs(len(ins) - N_L2 * PERIOD / GPTMR_PERIOD) <= 1
    assert {r.bg_n for r in recs} <= {7, 8}


@pytest.mark.parametrize("cache", [1, 0])
def test_l2_monitor_counts_equal_the_trace(l2_runs, cache):
    bodies, recs = l2_runs[cache]
    for r in recs:
        lo, hi = r.rel, r.rel + PERIOD
        n = sum(1 for i, _ in bodies if lo <= i < hi)
        cyc = sum(max(0, min(o, hi) - max(i, lo)) for i, o in bodies)
        assert (r.bg_n, r.bg_cycles) == (n, cyc), r


@pytest.mark.parametrize("cache", [1, 0])
def test_l2_control_task_is_not_preempted_and_computes_the_same(l2_runs, cache):
    bodies, recs = l2_runs[cache]
    pid = m.FixedPID()
    for r in recs:
        assert r.seen == 0b111 and not r.extra and not r.dropped, r
        # interrupts do not nest: no background body starts between START and ACT
        assert not [i for i, _ in bodies if r.rel + r.start <= i <= r.rel + r.act], r
        assert r.u == pid.step(env.ref_at(r.k), r.enc), r


def test_l2_a_release_inside_a_body_waits_for_it(l2_runs):
    hits = 0
    for cache, (bodies, recs) in l2_runs.items():
        for r in recs:
            for i, o in bodies:
                if i <= r.rel < o:
                    hits += 1
                    assert r.start > o - r.rel, (cache, r, i, o)
    assert hits > 0
    print(f"releases inside a background body: {hits}")


# ---------------------------------------------------------------- board top
def test_board_top_uart_break_reset():
    # the pins of arty_neorv32_top: short low stretches do nothing, a break
    # resets the whole system and the program starts again, as with the button
    hexfile, _ = build("top_l0", TRIG=0, LOAD=0, ACT=0, START_DELAY=START_DELAY)
    v = converted(1)
    vtb = verilate("tb_arty_neorv32_top", [f"{B}/sim/neorv32.vlt", wsl_path(v), *SOC_SOURCES,
                                           f"{B}/rtl/uart_break_reset.sv", f"{B}/rtl/arty_neorv32_top.sv",
                                           f"{B}/sim/tb_arty_neorv32_top.sv"], BUILD / "arty_neorv32_top")
    code, out = wsl(f"{shlex.quote(wsl_path(vtb))} +code={shlex.quote(wsl_path(hexfile))}", timeout=1800)
    assert code == 0 and "PASS arty_neorv32_top" in out and "FAIL" not in out, out[-3000:]
    print(out[-400:])


# ---------------------------------------------------------------- the control law
SHORT = 15_000                   # > one 10-word monitor record (13,200 cycles); 620 periods cross k = 100 and 600


@pytest.mark.parametrize("load, act", [(0, 0), (0, 1), (3, 0)])
def test_release_schedule_and_pid_over_620_periods(load, act):
    hexfile, _ = build(f"short_L{load}_act{act}", TRIG=0, LOAD=load, ACT=act, PERIOD_CYC=SHORT,
                       START_DELAY=START_DELAY)
    reqs, recs = simulate(1, hexfile, 120_000 + 621 * SHORT)     # L1d+ clears 16 KB first: main near 78k
    recs = recs[:620]
    # main reads mtime (MTIME_LO returns the cycle of its strobe) and sets the
    # first release START_DELAY later; the ISR adds the period to `release`,
    # which main stored before enabling the interrupt. Without the memory
    # barrier GCC dropped that store: the ISR read 0 and released period 1 at
    # SHORT instead of release 0 + SHORT, and the schedule counters read 0.
    mtime_lo = next(r.cycle for r in reqs if r.target == "H" and not r.we and r.adr & 0x3F == 0)
    assert recs[0].rel == mtime_lo + START_DELAY
    check_records(recs, SHORT, 620)
    pid = m.FixedPID()
    assert [r.u for r in recs] == [pid.step(env.ref_at(r.k), r.enc) for r in recs]
    assert {env.ref_at(r.k) for r in recs} == {0, 4096, -4096}
    # the board's check: the plant model (PlantCosim from cycle 0) driven by the
    # stamped pwm_cmd writes gives every encoder value the program read, here
    # against the RTL plant of the simulation
    assert A.check_sequence(recs, period=SHORT) == []
    assert A.replay(recs) == []
    assert max(abs(r.enc) for r in recs) > 4000                  # the motor really moved
    if act:
        from host.hil import programs as P
        # the busy-wait reads MTIME_LO in a loop: the store lands some cycles
        # after release + CTRL_ACT, spread over one loop iteration (measured
        # 1020..1033 with the caches on)
        assert all(P.ACT + 1 <= r.act < P.ACT + 1 + 40 for r in recs), sorted({r.act for r in recs})


def test_board_executables_have_the_bootloader_format():
    # what board.py uploads: signature, base 0, size, checksum, then the image;
    # the fake bootloader of test_baseline_board.py checks the same rules
    BD.build(["irq_L2_wait"])
    b = (BD.EXE_DIR / "irq_L2_wait" / "neorv32_exe.bin").read_bytes()
    sig, base, size, chk = struct.unpack_from("<4I", b)
    words = struct.unpack_from(f"<{size // 4}I", b, 16)
    assert (sig, base, len(b)) == (0x214F454E, 0, 16 + size)
    assert (sum(words) + chk + 1) & 0xFFFF_FFFF == 0
