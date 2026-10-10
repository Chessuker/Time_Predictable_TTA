"""WCET correctness properties asked for in the PR #5 review.

1. @loop_bound N means at most N header executions per entry into the loop
2. the witness is one valid source-to-sink path with the ILP's cost
3. call/return and taken/not-taken accounting against hand counts
5. every result lists the loop bounds it relies on, and the CLI states the guarantee
(4, the board harness, is in test_wcet_board.py.)
Every hand count is also checked against the ISS.
"""
from pathlib import Path

import numpy as np
import pytest

from host.asm import assemble
from host.common import cfg, spec
from host.wcet import WcetError, analyse, bench
from host.wcet import analysis as A

from .test_iss import IF_ELSE, control, run

ROOT = Path(__file__).resolve().parents[1]
R, P = spec.R, spec.P


def wcet(src):
    return analyse(assemble(src, "t.tta"))


def seg(rep, fn, start_kind, end_kind):
    s = [s for s in rep.functions[fn].segments if s.start_kind == start_kind and s.end_kind == end_kind]
    assert len(s) == 1, s
    return s[0]


def halt_both(src, hand):
    """Static halt cycle == hand count == ISS."""
    assert wcet(src).halt_cycle() == hand
    r = run(src)
    assert r.end == "halt" and r.cycle == hand


def executed_pcs(src):
    """PCs of the moves the ISS executed, in order (M records)."""
    return [int(line.split()[2], 16) for line in run(src).trace if line.startswith("M ")]


# ---------------------------------------------------------------- 1. @loop_bound N
NESTED = """
        #{m}        -> r3
        #ADD        -> alu.op
@loop_bound {bm}
outer:  #{n}        -> r2
@loop_bound {bn}
inner:  r2          -> alu.a
        #-1         -> alu.t_b
        alu.out     -> r2
        r2          -> pc.cond
        #inner      -> pc.t_jnz
        r3          -> alu.a
        #-1         -> alu.t_b
        alu.out     -> r3
        r3          -> pc.cond
        #outer      -> pc.t_jnz
        #0          -> trap.t_halt
"""


def nested_hand(m, n, bn=None):
    """Halt cycle with the inner header run bn (default n) times per entry."""
    k = n if bn is None else bn
    inner = 5 * k + P * (k - 1)            # k headers per entry, k - 1 taken back jumps
    body = 1 + inner + 5
    return R + 2 + m * body + P * (m - 1)


@pytest.mark.parametrize("m, n", [(1, 1), (1, 2), (2, 1), (2, 2)])
def test_loop_bound_is_header_runs_per_entry_in_nested_loops(m, n):
    # the inner bound applies per entry: n inner headers on each of m entries
    halt_both(NESTED.format(m=m, n=n, bm=m, bn=n), nested_hand(m, n))


def test_loop_bound_of_1_means_no_back_jump():
    rep = wcet(NESTED.format(m=1, n=1, bm=1, bn=1))
    assert rep.program_wcet() == 2 + 1 + 5 + 5 + 1        # straight through, no P anywhere


def test_a_bound_above_the_real_count_is_safe_but_not_tight():
    src = NESTED.format(m=2, n=2, bm=2, bn=3)             # inner runs 2, bound says 3
    assert wcet(src).halt_cycle() == nested_hand(2, 2, bn=3)
    assert wcet(src).halt_cycle() > run(src).cycle == nested_hand(2, 2)


def test_a_bound_below_the_real_count_breaks_the_precondition():
    src = NESTED.format(m=2, n=2, bm=2, bn=1)             # inner runs 2, bound says 1
    assert wcet(src).halt_cycle() < run(src).cycle        # why the bound is an assumption


EARLY_EXIT = """
        #3          -> r1
        #ADD        -> alu.op
@loop_bound 3
lp:     r2          -> pc.cond          ; r2 = exit flag
        #out        -> pc.t_jnz         ; exit 1: early
        r1          -> alu.a
        #-1         -> alu.t_b
        alu.out     -> r1
        r1          -> pc.cond
        #lp         -> pc.t_jnz         ; exit 2: count done
out:    #0          -> trap.t_halt
"""


def test_loop_with_two_exits():
    # worst: never leave early; 3 headers of 7 moves, 2 taken back jumps
    halt_both(EARLY_EXIT, R + 2 + 7 * 3 + 2 * P)
    early = "#1 -> r2\n" + EARLY_EXIT                     # leave at the first header
    assert run(early).cycle == R + 1 + 2 + 2 + P
    assert run(early).cycle < wcet(early).halt_cycle()


TWO_ENTRIES = """
        #2          -> r1
        #ADD        -> alu.op
        r5          -> pc.cond
        #lp         -> pc.t_jz          ; taken: enter the loop by the jump
        nop                             ; not taken: enter by falling through
@loop_bound 2
lp:     r1          -> alu.a
        #-1         -> alu.t_b
        alu.out     -> r1
        r1          -> pc.cond
        #lp         -> pc.t_jnz
        #0          -> trap.t_halt
"""


def test_loop_with_two_entering_edges_is_bounded_per_entry():
    # worst entry is the jump (4 moves + P); then 2 headers: 5 + P + 5. The two
    # entering edges are alternatives, so the bound is not doubled.
    rep = wcet(TWO_ENTRIES)
    assert rep.program_wcet() == 4 + P + 5 + P + 5 + 1
    halt_both(TWO_ENTRIES, R + 4 + P + 5 + P + 5)           # r5 = 0: the jump is taken
    fall = "#1 -> r5\n" + TWO_ENTRIES
    assert run(fall).cycle == R + 1 + 4 + 1 + 5 + P + 5     # by falling through: 1 cycle less


# ---------------------------------------------------------------- 2. witness path
def all_segments():
    srcs = [(ROOT / "programs" / f"{n}.tta").read_text(encoding="utf-8")
            for n in ("fib", "bubble_sort", "fixmul", "call_return", "board_hello")]
    srcs += [bench.generate(), control(623), IF_ELSE.format(x=7), EARLY_EXIT, TWO_ENTRIES,
             NESTED.format(m=2, n=2, bm=2, bn=2)]
    for src in srcs:
        prog = assemble(src, "t.tta")
        rep = analyse(prog)
        funcs = {f.start: f for f in cfg.build(prog.code, prog.functions).values()}
        for f in rep.functions.values():
            for s in f.segments:
                yield rep, funcs[f.start], s


def test_every_witness_is_a_valid_source_to_sink_path_with_cost_w():
    n = 0
    for rep, f, s in all_segments():
        n += 1
        at = s.start
        cost = 0 if (s.start == s.end and f.nodes[s.end].dst in A.CUT) else 1
        for u, v, extra in s.steps:
            assert u == at                                # starts at the source, stays connected
            node = f.nodes[u]
            kinds = [e.kind for e in node.succ if e.to == v]
            assert kinds, f"{u}->{v} is not a CFG edge"   # only real edges
            if extra == 0:
                assert set(kinds) & {"seq", "not_taken"}
            elif extra == P and "taken" in kinds:
                pass
            else:
                callee = next(c for c in rep.functions.values() if c.start == node.call)
                assert "after_call" in kinds and extra == P + callee.to_return + P
            cost += extra + (0 if (v == s.end and f.nodes[v].dst in A.CUT) else 1)
            at = v
        assert at == s.end                                # reaches the sink
        assert cost == s.wcet                             # same cost as the ILP objective
    assert n > 40


@pytest.mark.parametrize("src", [
    (ROOT / "programs" / "fib.tta").read_text(encoding="utf-8"),
    (ROOT / "programs" / "bubble_sort.tta").read_text(encoding="utf-8"),
    (ROOT / "programs" / "fixmul.tta").read_text(encoding="utf-8"),
    IF_ELSE.format(x=7), EARLY_EXIT, TWO_ENTRIES, NESTED.format(m=2, n=2, bm=2, bn=2),
    NESTED.format(m=3, n=2, bm=3, bn=2),
], ids=["fib", "bubble_sort", "fixmul", "if_else", "early_exit", "two_entries",
        "nested_2x2", "nested_3x2"])
def test_witness_is_exactly_what_the_iss_executes_on_the_worst_input(src):
    assert seg(wcet(src), "main", "entry", "halt").witness == executed_pcs(src)


def test_parallel_edges_to_the_next_move_are_told_apart():
    # a jnz to the very next address: taken and not taken reach the same move
    # with different costs, so the witness is kept as edges, not addresses
    src = "#1 -> pc.cond\n#nxt -> pc.t_jnz\nnxt: #0 -> trap.t_halt"
    s = seg(wcet(src), "main", "entry", "halt")
    assert s.wcet == 1 + 1 + P + 1 and s.steps[1][2] == P
    halt_both(src, R + 1 + 1 + P)


def test_a_witness_that_does_not_reproduce_the_ilp_is_an_internal_error():
    # 0 -> 1 -> 2 (cost 3) or 0 -> 2 taken (cost 1 + P + 1); the ILP picked the first
    g = A._Graph([0, 1, 2], [(0, 1, 0), (1, 2, 0), (0, 2, P)], [2], {2: "halt"}, {})
    x = np.array([1, 1, 0, 1, 1])
    obj = np.array([1, 1, 1 + P, 1, 0])
    A._check_witness(g, x, [0, 1], 0, 2, obj, 3, "t")      # the solved path: accepted
    with pytest.raises(WcetError, match="internal"):
        A._check_witness(g, x, [2], 0, 2, obj, 3, "t")     # other edges and cost
    with pytest.raises(WcetError, match="internal"):
        A._check_witness(g, x, [0], 0, 2, obj, 3, "t")     # stops before the sink
    with pytest.raises(WcetError, match="internal"):
        A._check_witness(g, x, [1, 0], 0, 2, obj, 3, "t")  # right edges, not a path


def test_a_witness_that_breaks_a_per_entry_bound_is_an_internal_error():
    # outer loop at 1 (bound 2) around inner loop at 2 (bound 2):
    #   0 -> 1 -> 2 ->(2)... -> 3 -> 1 ... -> 3 -> 4
    edges = [(0, 1, 0), (1, 2, 0), (2, 2, P), (2, 3, 0), (3, 1, P), (3, 4, 0)]
    g = A._Graph([0, 1, 2, 3, 4], edges, [4], {4: "halt"}, {})
    loops = A._loops(g, 0)
    bounds = {1: 2, 2: 2}
    x = np.array([1, 2, 2, 2, 1, 1, 1, 1])
    obj = np.array([e[2] + 1 for e in edges] + [1, 0])
    w = int(obj @ x)
    good = [0, 1, 2, 3, 4, 1, 2, 3, 5]                      # inner runs 2, then 2
    bad = [0, 1, 2, 2, 3, 4, 1, 3, 5]                       # inner runs 3, then 1: same counts
    A._check_witness(g, x, good, 0, 4, obj, w, "t", loops, bounds)
    with pytest.raises(WcetError, match="runs 3 times in one entry"):
        A._check_witness(g, x, bad, 0, 4, obj, w, "t", loops, bounds)
    assert A._witness(g, x, 0, 4, loops, bounds) == good    # the walk spreads the runs evenly


# ---------------------------------------------------------------- 3. calls, returns, taken / not taken
RET_ONLY = "#f -> pc.t_call\n#0 -> trap.t_halt\n.func f\npc.link -> pc.t_jump\n.endfunc"


def test_return_only_function():
    rep = wcet(RET_ONLY)
    assert rep.functions["f"].to_return == 1                # just the return move
    halt_both(RET_ONLY, R + (1 + P) + 1 + P)                # call, f, back: halt at 8


def test_caller_and_callee_with_a_body():
    src = "#f -> pc.t_call\n#0 -> trap.t_halt\n.func f\nnop\nnop\nnop\npc.link -> pc.t_jump\n.endfunc"
    assert wcet(src).functions["f"].to_return == 4
    halt_both(src, R + (1 + P) + 4 + P)


NESTED_CALLS = """
        #a          -> pc.t_call
        #0          -> trap.t_halt
.func a
        pc.link     -> r9
        #b          -> pc.t_call
        r9          -> pc.link
        pc.link     -> pc.t_jump
.endfunc
.func b
        nop
        pc.link     -> pc.t_jump
.endfunc
"""


def test_nested_calls():
    rep = wcet(NESTED_CALLS)
    assert rep.functions["b"].to_return == 2
    # save link, call b (1 + P + W(b) + P), restore link, return
    assert rep.functions["a"].to_return == 1 + (1 + P + 2 + P) + 1 + 1 == 10
    halt_both(NESTED_CALLS, R + (1 + P) + 10 + P)


@pytest.mark.parametrize("nops", [1, 3])
def test_taken_versus_not_taken_picks_the_longer(nops):
    # not taken: cond, jz, nops, halt      taken: cond, jz, +P, halt
    src = "#{c} -> pc.cond\n#x -> pc.t_jz\n" + "nop\n" * nops + "x: #0 -> trap.t_halt"
    not_taken, taken = 2 + nops + 1, 2 + P + 1
    assert wcet(src.format(c=0)).program_wcet() == max(taken, not_taken)
    assert run(src.format(c=0)).cycle == R + taken - 1      # cond = 0: jz taken
    assert run(src.format(c=1)).cycle == R + not_taken - 1  # cond = 1: falls through


# ---------------------------------------------------------------- 5. assumptions are reported
def test_segments_list_every_loop_bound_they_rely_on():
    rep = wcet(NESTED.format(m=2, n=2, bm=2, bn=2))
    assert sorted(seg(rep, "main", "entry", "halt").loop_bounds.values()) == [2, 2]
    b = analyse(assemble(bench.generate(), "b.tta"))
    assert 20 in b.functions["fib"].return_bounds.values()
    # a caller inherits the bounds of what it calls
    after_sync = [s for s in b.functions["main"].segments if s.start_kind == "t_sync"]
    assert any(20 in s.loop_bounds.values() for s in after_sync)


def test_a_loop_off_the_worst_path_is_still_an_assumption():
    # the loop branch is shorter at its bound, yet W still depends on that bound
    src = """
        r5          -> pc.cond
        #slow       -> pc.t_jnz
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        nop
        #end        -> pc.t_jump
@loop_bound 1
slow:   r6          -> pc.cond
        #slow       -> pc.t_jnz
end:    #0          -> trap.t_halt
"""
    s = seg(wcet(src), "main", "entry", "halt")
    assert 1 in s.loop_bounds.values()


def test_cli_states_the_guarantee(capsys):
    from host.wcet.__main__ import main
    assert main([str(ROOT / "programs" / "fib.tta")]) == 0
    out = capsys.readouterr().out
    assert "valid if: loop at line" in out and "<= 20" in out
    assert "SAFE UPPER BOUND" in out and "TIGHT" in out and "feasib" in out
