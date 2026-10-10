"""Static WCET (host/wcet) against the hand-counted examples and the ISS."""
from pathlib import Path

import pytest

from host.asm import assemble
from host.common import spec
from host.iss import Simulator
from host.wcet import WcetError, analyse, bench, compress

from .test_iss import IF_ELSE, LOOP, control, run

ROOT = Path(__file__).resolve().parents[1]
R = spec.R


def wcet(src):
    return analyse(assemble(src, "t.tta"))


def seg(rep, fn, start_kind, end_kind):
    s = [s for s in rep.functions[fn].segments if s.start_kind == start_kind and s.end_kind == end_kind]
    assert len(s) == 1, s
    return s[0]


# ---------------------------------------------------------------- equal to the ISS
@pytest.mark.parametrize("name", ["fib", "bubble_sort", "fixmul", "call_return"])
def test_program_wcet_equals_iss(name):
    src = (ROOT / "programs" / f"{name}.tta").read_text(encoding="utf-8")
    r = run(src)
    assert r.end == "halt"
    assert wcet(src).halt_cycle() == r.cycle          # the same cycle, not just an upper bound


@pytest.mark.parametrize("n", [1, 2, 5, 16])
def test_example_2a_loop(n):
    rep = wcet(LOOP.format(n=n))
    assert rep.halt_cycle() == R + 7 * n == run(LOOP.format(n=n)).cycle


def test_example_2b_if_else_takes_the_longer_branch():
    src = IF_ELSE.format(x=7)
    rep = wcet(src)
    assert rep.halt_cycle() == run(src).cycle == R + 1 + 6     # the then path
    assert rep.halt_cycle() > run(IF_ELSE.format(x=0)).cycle   # else is shorter
    w = seg(rep, "main", "entry", "halt").witness
    assert 3 in w and 5 not in w                               # through 'then', not 'else'


def test_witness_repeats_the_loop_body_n_times():
    rep = wcet(LOOP.format(n=5))
    w = seg(rep, "main", "entry", "halt").witness
    assert w.count(2) == 5                                     # loop header
    assert compress(w)[0][:2] == (0, 6)


# ---------------------------------------------------------------- segments and budgets (sec. 3)
def test_example_3_segments_and_budgets():
    rep = wcet(control(623))
    sense = seg(rep, "main", "t_advance", "t_wait")
    assert (sense.wcet, sense.budget, sense.ok) == (1 + 1 + 623, 1000 - spec.D, True)
    rest = seg(rep, "main", "t_wait", "t_advance")
    assert (rest.wcet, rest.budget) == (1 + 1 + 6 + 1 + spec.P, 100_000 - 1000 - spec.D)
    assert rep.program_wcet() is None                          # a periodic task never halts


def test_example_3_too_slow_is_reported():
    s = seg(wcet(control(1200)), "main", "t_advance", "t_wait")
    assert s.wcet == 1202 and not s.ok


def test_budget_is_the_largest_k_that_is_on_time():
    k_max = 1000 - spec.D - 2                                  # 997, timing_model.md sec. 5
    assert seg(wcet(control(k_max)), "main", "t_advance", "t_wait").ok
    assert not seg(wcet(control(k_max + 1)), "main", "t_advance", "t_wait").ok


def test_trap_handler_is_its_own_function():
    rep = wcet(control(10))
    assert seg(rep, "on_trap", "entry", "halt").wcet == 3


# ---------------------------------------------------------------- what the analysis refuses
def test_loop_without_bound_is_s6():
    src = "#3 -> r1\nloop: r1 -> pc.cond\n#loop -> pc.t_jnz\n#0 -> trap.t_halt"
    with pytest.raises(WcetError, match="S6"):
        wcet(src)


def test_loop_through_a_sync_point_needs_no_bound():
    rep = wcet("#0 -> tmr.t_sync\nloop: #100 -> tmr.t_advance\nnop\n#loop -> pc.t_jump")
    s = seg(rep, "main", "t_advance", "t_advance")
    assert (s.wcet, s.budget) == (1 + 1 + spec.P, 100 - spec.D)


def test_irreducible_loop_is_refused():
    src = """
        r1      -> pc.cond
        #b      -> pc.t_jz
a:      nop
@loop_bound 3
b:      nop
        #a      -> pc.t_jnz
        #0      -> trap.t_halt
"""
    with pytest.raises(WcetError, match="irreducible"):
        wcet(src)


def test_register_v_has_no_budget_but_still_a_wcet():
    rep = wcet((ROOT / "programs" / "board_hello.tta").read_text(encoding="utf-8"))
    s = seg(rep, "main", "t_advance", "t_advance")
    assert s.budget is None and s.wcet == 10 + spec.P
    assert any("register" in m for _, m in rep.warnings)


# ---------------------------------------------------------------- the board benchmark image
OVERHEAD = 2 + 2 * spec.P          # t_sync -> call -> callee ... return -> elapsed read


def bench_runs(seed):
    """{bench name: [measured cycles per dataset]} from one pass on the ISS."""
    prog = assemble(bench.generate(seed), "wcet_bench.tta")
    sim = Simulator(prog.code, prog.data, None, prog.imem_words, prog.dmem_words)
    sim.run(len(bench.BENCHES) * bench.DATASETS * bench.PACE + 10_000)
    assert sim.telem.drops == 0
    words = [w & 0xFFFF_FFFF for _, w in sim.telem.accepted]
    recs = [words[i:i + 4] for i in range(0, len(words) - 3, 4)]
    assert all(r[0] == bench.MAGIC for r in recs)
    out = {}
    for r in recs[:len(bench.BENCHES) * bench.DATASETS]:
        out.setdefault(bench.BENCHES[r[1]].name, []).append(r[3] - OVERHEAD)
    return prog, out


def test_committed_bench_is_up_to_date():
    assert bench.OUT.read_text(encoding="utf-8") == bench.generate(), \
        "programs/wcet_bench.tta is stale; run: py -m host.wcet.bench"


def test_worst_case_dataset_measures_exactly_the_static_wcet():
    prog, runs = bench_runs(bench.SEED)
    rep = analyse(prog)
    for b in bench.BENCHES:
        static = rep.functions[b.name].to_return
        assert runs[b.name][0] == static, b.name               # tight: equal, not just below


@pytest.mark.parametrize("seed", range(1, 41))
def test_random_inputs_never_exceed_the_static_wcet(seed):
    prog, runs = bench_runs(seed)
    rep = analyse(prog)
    for b in bench.BENCHES:
        static = rep.functions[b.name].to_return
        assert len(runs[b.name]) == bench.DATASETS
        assert max(runs[b.name]) <= static, (b.name, runs[b.name], static)


def test_data_dependent_benchmarks_really_vary():
    _, runs = bench_runs(bench.SEED)
    for name in ("fib", "search", "cpos", "pid"):
        assert len(set(runs[name])) > 1, name                 # the input changes the path
    for name in ("sort8", "spo"):
        assert len(set(runs[name])) == 1, name                # branch-free: one time for all
