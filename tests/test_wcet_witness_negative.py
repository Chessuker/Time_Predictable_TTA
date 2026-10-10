"""A witness that does not match the ILP solution must be rejected, never
reported as a WCET (PR #5 review, second round).

Graph used below, an outer loop at 1 (bound 2) around an inner loop at 2
(bound 2), plus a cycle 5 <-> 6 that nothing reaches:

    0 -> 1 -> 2 -(back)-> 2 -> 3 -(back)-> 1 ... 3 -> 4 (halt)      5 <-> 6
"""
import numpy as np
import pytest

from host.asm import assemble
from host.common import spec
from host.wcet import WcetError, analyse
from host.wcet import analysis as A

P = spec.P
EDGES = [(0, 1, 0), (1, 2, 0), (2, 2, P), (2, 3, 0), (3, 1, P), (3, 4, 0), (5, 6, 0), (6, 5, 0)]
GOOD = [0, 1, 2, 3, 4, 1, 2, 3, 5]                 # inner runs 2 then 2


def setup(cycle=0):
    g = A._Graph([0, 1, 2, 3, 4, 5, 6], EDGES, [4], {4: "halt"}, {})
    loops = {1: ({1, 2, 3}, [0]), 2: ({2}, [1])}
    bounds = {1: 2, 2: 2}
    x = np.array([1, 2, 2, 2, 1, 1, cycle, cycle, 1, 1])
    obj = np.array([e[2] + 1 for e in EDGES] + [1, 0])
    w = int(obj @ x)
    return g, loops, bounds, x, obj, w


def check(seq, **kw):
    g, loops, bounds, x, obj, w = setup(**{k: v for k, v in kw.items() if k == "cycle"})
    x = kw.get("x", x)
    w = kw.get("w", w)
    A._check_witness(g, x, seq, 0, 4, obj, w, "t", loops, bounds)


def test_the_consistent_witness_is_accepted():
    check(GOOD)


def test_inconsistent_edge_counts_are_rejected():
    _, _, _, x, _, _ = setup()
    x = x.copy()
    x[2] = 3                                         # the ILP says 3 inner back edges, the path has 2
    with pytest.raises(WcetError, match="edge counts differ"):
        check(GOOD, x=x)


def test_a_disconnected_cycle_in_the_counts_is_rejected():
    # flow conservation holds on 5 <-> 6, but no path from 0 can use it
    g, loops, bounds, x, obj, w = setup(cycle=2)
    assert A._witness(g, x, 0, 4, loops, bounds) is None
    with pytest.raises(WcetError, match="edge counts differ"):
        A._check_witness(g, x, GOOD, 0, 4, obj, w, "t", loops, bounds)
    with pytest.raises(WcetError, match="does not continue"):
        A._check_witness(g, x, GOOD + [6, 7, 6, 7], 0, 4, obj, w, "t", loops, bounds)


@pytest.mark.parametrize("seq, why", [
    ([0, 2, 1, 3, 4, 1, 2, 3, 5], "does not continue"),          # 1 -> 2 taken before reaching 2
    ([0, 1, 3, 2, 4, 1, 2, 3, 5], "does not continue"),          # exit then back edge
    ([0, 1, 2, 3, 4, 1, 2, 3], "ends at"),                       # stops before the sink
    ([1, 2, 3, 4, 1, 2, 3, 5], "does not continue"),             # does not start at the source
])
def test_invalid_transitions_are_rejected(seq, why):
    with pytest.raises(WcetError, match=why):
        check(seq)


def test_a_cost_that_differs_from_the_objective_is_rejected():
    _, _, _, _, _, w = setup()
    with pytest.raises(WcetError, match="differs from the ILP objective"):
        check(GOOD, w=w + 1)


def test_a_per_entry_bound_break_with_the_right_counts_is_rejected():
    with pytest.raises(WcetError, match="runs 3 times in one entry"):
        check([0, 1, 2, 2, 3, 4, 1, 3, 5])                       # 3 then 1


SRC = "#2 -> r1\n#ADD -> alu.op\n@loop_bound 2\nlp: r1 -> alu.a\n#-1 -> alu.t_b\nalu.out -> r1\n" \
      "r1 -> pc.cond\n#lp -> pc.t_jnz\n#0 -> trap.t_halt"


def test_analyse_raises_instead_of_reporting_when_the_witness_is_wrong(monkeypatch):
    prog = assemble(SRC, "t.tta")
    assert analyse(prog).program_wcet() == 2 + 5 + P + 5 + 1    # sanity: fine untouched
    monkeypatch.setattr(A, "_witness", lambda g, x, s, e, loops, bounds: [0])
    with pytest.raises(WcetError, match="internal"):
        analyse(prog)


def test_analyse_raises_when_no_witness_can_be_built(monkeypatch):
    prog = assemble(SRC, "t.tta")
    monkeypatch.setattr(A, "_witness", lambda g, x, s, e, loops, bounds: None)
    with pytest.raises(WcetError, match="no witness path"):
        analyse(prog)
