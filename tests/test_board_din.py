"""Board input interface model (host/hil/board_din.py): cycle boundaries of the
debounce and the commit, and when a value is guaranteed to be sampled by a
task that reads io.din once per PERIOD."""
import math
import random

import pytest

from host.hil import board_din as B


def expand(changes, n):
    out, v, i = [], 0, 0
    for t in range(n):
        while i < len(changes) and changes[i][0] <= t:
            v = changes[i][1]
            i += 1
        out.append(v)
    return out


def random_script(rng, n_cycles, max_len, values=(0, 1)):
    t, script = 0, []
    while t < n_cycles:
        script.append((t, rng.choice(values)))
        t += rng.randint(1, max_len)
    return script


def holds(changes):
    """Lengths of every value of a change list except the last (still running)."""
    return [b[0] - a[0] for a, b in zip(changes, changes[1:])]


def seen(changes, value_start, phase, period, end):
    """Values present at the sample cycles phase, phase + period, ... < end."""
    out, i, v = [], 0, 0
    for s in range(phase, end, period):
        while i < len(changes) and changes[i][0] <= s:
            v = changes[i][1]
            i += 1
        out.append(v)
    return out


# ---------------------------------------------------------------- debounce
@pytest.mark.parametrize("n_db", [1, 2, 3, 5, 8])
def test_fast_model_equals_register_model(n_db):
    rng = random.Random(n_db)
    for _ in range(200):
        script = random_script(rng, 300, 3 * n_db + 4)
        n = 330
        assert expand(B.debounce(script, n_db), n) == B.debounce_cycles(script, n_db, n)


@pytest.mark.parametrize("n_db", [1, 3, 8, 50])
def test_accept_boundary_and_latency(n_db):
    p = 10
    # held for exactly N_DB + 1 cycles: accepted, out changes at p + N_DB + 3
    ch = B.debounce([(p, 1), (p + n_db + 1, 0)], n_db)
    assert ch[:2] == [(0, 0), (p + n_db + 3, 1)]
    assert B.debounce_cycles([(p, 1), (p + n_db + 1, 0)], n_db, p + n_db + 5)[p + n_db + 2:] == [0, 1, 1]
    # held for N_DB cycles: rejected
    assert B.debounce([(p, 1), (p + n_db, 0)], n_db) == [(0, 0)]
    assert set(B.debounce_cycles([(p, 1), (p + n_db, 0)], n_db, p + 3 * n_db + 10)) == {0}


@pytest.mark.parametrize("n_db", [1, 4, 9])
def test_every_value_is_held_at_least_n_db_plus_1(n_db):
    rng = random.Random(100 + n_db)
    for _ in range(300):
        ch = B.debounce(random_script(rng, 2000, 2 * n_db + 3), n_db)
        assert all(h >= n_db + 1 for h in holds(ch)), ch
    # the bound is reached: a pin that changes exactly every N_DB + 1 cycles
    ch = B.debounce([(10 + i * (n_db + 1), i % 2 == 0) for i in range(6)], n_db)
    assert min(holds(ch)) == n_db + 1


@pytest.mark.parametrize("n_db", [1, 4, 9])
def test_commit_copies_the_debounced_switches_and_is_rate_limited(n_db):
    rng = random.Random(200 + n_db)
    for _ in range(200):
        sw = random_script(rng, 3000, 3 * n_db, values=range(8))
        btn = random_script(rng, 3000, 3 * n_db)
        din = B.interface(sw, btn, n_db)
        edges = B.rising_edges(B.debounce(btn, n_db))
        assert all(b - a >= 2 * (n_db + 1) for a, b in zip(edges, edges[1:]))
        sw10 = B.debounce([(c, v & 3) for c, v in sw], n_db)
        committed = [(c, v & 3) for c, v in din]
        for e in edges:                                      # din[1:0](e+1) = sw_db[1:0](e)
            assert expand(committed, e + 2)[e + 1] == expand(sw10, e + 1)[e]
        changes10 = [committed[0]] + [b for a, b in zip(committed, committed[1:]) if a[1] != b[1]]
        # the reset value lasts until the first commit, however soon that comes;
        # every committed value is held for at least two debounced button levels
        assert all(h >= 2 * (n_db + 1) for h in holds(changes10[1:]))


def commit_order_cases(n_db):
    """sw[1:0] moves to 3 at t_sw, BTN0 goes down at t_btn; both stay."""
    t = 20
    return [([(t, 3)], [(t + 1, 1)]),          # switch one cycle before the button: new value
            ([(t, 3)], [(t, 1)]),              # same cycle: new value
            ([(t + 1, 3)], [(t, 1)])]          # button first: the value debounced before (0)


@pytest.mark.parametrize("n_db", [1, 4, 9])
def test_commit_takes_the_new_value_only_if_the_switch_settles_no_later(n_db):
    got = [B.interface(sw, btn, n_db) for sw, btn in commit_order_cases(n_db)]
    assert got[0][-1][1] == 3 and got[1][-1][1] == 3
    assert got[2] == [(0, 0)]


# ---------------------------------------------------------------- sampling
@pytest.mark.parametrize("period", [1, 4, 7, 10])
def test_a_value_held_exactly_period_cycles_is_always_sampled(period):
    # samples at phase + k * period; a value held H cycles from cycle a
    for hold in (period - 1, period, period + 1):
        if hold < 1:
            continue
        missed = []
        for a in range(3 * period, 4 * period):
            for phase in range(period):
                n = sum(1 for s in range(phase, 10 * period, period) if a <= s < a + hold)
                if n == 0:
                    missed.append((a, phase))
                if hold == period:
                    assert n == 1
        assert bool(missed) == (hold < period)


@pytest.mark.parametrize("period", [5, 8, 13])
def test_live_input_is_sampled_iff_n_db_plus_1_reaches_the_period(period):
    rng = random.Random(period)
    n_db = period - 1                            # boundary: N_DB + 1 = PERIOD
    for _ in range(200):
        ch = B.debounce(random_script(rng, 40 * period, 2 * period), n_db)
        values = [v for _, v in ch]
        for phase in range(period):
            got = seen(ch, 0, phase, period, ch[-1][0] + period)
            # every accepted value appears, in order (consecutive duplicates collapse)
            assert [v for i, v in enumerate(got) if i == 0 or got[i - 1] != v] == values
    # one cycle less and a value can fall between two samples
    n_db = period - 2
    ch = B.debounce([(10, 1), (10 + n_db + 1, 0)], n_db)
    assert ch == [(0, 0), (n_db + 13, 1), (2 * n_db + 14, 0)]       # held n_db + 1 = period - 1
    assert any(1 not in seen(ch, 0, phase, period, 10 * period) for phase in range(period))


@pytest.mark.parametrize("period", [6, 9, 14])
def test_committed_input_is_sampled_when_two_holds_reach_the_period(period):
    rng = random.Random(1000 + period)
    n_db = math.ceil(period / 2) - 1                   # boundary: 2(N_DB + 1) >= PERIOD
    assert 2 * (n_db + 1) >= period and 2 * n_db < period
    for _ in range(200):
        sw = random_script(rng, 40 * period, period, values=range(4))
        btn = random_script(rng, 40 * period, period)
        din = B.interface(sw, btn, n_db)
        values = [v for _, v in din]
        for phase in range(period):
            got = seen(din, 0, phase, period, din[-1][0] + period)
            got = [v for i, v in enumerate(got) if i == 0 or got[i - 1] != v]
            # every committed value appears; the reset value may be gone before the first sample
            assert got in (values, values[1:])


# ---------------------------------------------------------------- RTL (board_din.sv)
from .test_lockstep import _verilator_available                      # noqa: E402

rtl = pytest.mark.skipif(not _verilator_available(), reason="Verilator not available")


def _pins_text(sw, btn):
    lines = [(c, "sw", v) for c, v in sw] + [(c, "btn", v) for c, v in btn]
    return "".join(f"{c} {p} {v}\n" for c, p, v in sorted(lines, key=lambda x: x[0]))


def _run_rtl(n_db, cases, n_cycles):
    """Build tb_board_din for one N_DB, run every (sw, btn) case, return the logs."""
    import shlex
    from host.lockstep.runner import BUILD, DESIGN_DIR, ROOT, SIM_DIR, wsl, wsl_path
    work = BUILD / "board_din" / f"n{n_db}"
    work.mkdir(parents=True, exist_ok=True)
    obj = work / "obj"
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && verilator --binary --timing --timescale 1ns/1ps -Wall "
           f"-Wno-fatal --top-module tb_board_din -GN_DB={n_db} --Mdir {shlex.quote(wsl_path(obj))} "
           f"-o Vtb {DESIGN_DIR}/din_debounce.sv {DESIGN_DIR}/board_din.sv {SIM_DIR}/tb_board_din.sv")
    runs = []
    for i, (sw, btn) in enumerate(cases):
        (work / f"pins{i}.txt").write_text(_pins_text(sw, btn), encoding="utf-8", newline="\n")
        runs.append(f"echo CASE {i}; {shlex.quote(wsl_path(obj / 'Vtb'))} "
                    f"+pins={shlex.quote(wsl_path(work / f'pins{i}.txt'))} +max={n_cycles}")
    code, out = wsl(cmd + " && " + " && ".join(runs))
    assert code == 0 and "%Warning" not in out, out[-4000:]
    logs, cur = [], None
    for line in out.splitlines():
        f = line.split()
        if f[:1] == ["CASE"]:
            cur = {"I": [(0, 0)], "L": [(0, 0)], "end": False}
            logs.append(cur)
        elif cur is not None and f[:1] in (["I"], ["L"]):
            cur[f[0]].append((int(f[1]), int(f[2])))
        elif cur is not None and f[:1] == ["END"]:
            cur["end"] = True
    assert len(logs) == len(cases) and all(x["end"] for x in logs), out[-4000:]
    return logs


def _bouncy(rng, n_cycles, n_db, values):
    """Switch-like script: settle on a value, with bursts of short glitches."""
    t, script, v = 0, [], 0
    while t < n_cycles:
        v = rng.choice(values)
        for _ in range(rng.randint(0, 4)):                 # bounce before settling
            script.append((t, rng.choice(values)))
            t += rng.randint(1, n_db)
        script.append((t, v))
        t += rng.randint(1, 4 * n_db)
    return script


@rtl
@pytest.mark.parametrize("n_db", [1, 4, 9])
def test_rtl_equals_model_cycle_for_cycle(n_db):
    rng = random.Random(7000 + n_db)
    n = 60 * (n_db + 2)
    cases = []
    for k in range(12):
        sw = _bouncy(rng, n, n_db, range(16)) if k % 2 else random_script(rng, n, 2 * n_db + 3, range(16))
        btn = _bouncy(rng, n, n_db, (0, 1)) if k % 3 else random_script(rng, n, 2 * n_db + 3)
        cases.append((sw, btn))
    # exact boundaries: pulses of N_DB and N_DB + 1 cycles on every group
    p = 5
    cases.append(([(p, 4), (p + n_db, 0), (p + 3 * n_db + 10, 4), (p + 4 * n_db + 11, 0)], []))
    cases.append(([(p, 3)], [(p + 2 * n_db, 1), (p + 3 * n_db + 1, 0), (p + 6 * n_db + 9, 1)]))
    cases += commit_order_cases(n_db)
    for (sw, btn), log in zip(cases, _run_rtl(n_db, cases, n)):
        assert log["I"] == B.interface(sw, btn, n_db, end=n)
        assert log["L"] == B.debounce([(c, v >> 2 & 1) for c, v in sw], n_db, end=n)
