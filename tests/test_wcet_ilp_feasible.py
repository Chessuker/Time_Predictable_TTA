"""The claim behind audit fix A3-3, checked on the real constraint rows.

analysis._solve treats every solver status other than a proven optimum as an
error, on the argument that the ILP always has a feasible point: a simple
path from start to end (see the comment in _solve). This test captures every
ILP the analyzer builds for many programs and checks that the 0/1 vector of a
shortest start-to-end path, with source and sink 1, satisfies its equality and
inequality rows exactly as passed to milp. The real solver must also report a
proven optimum for each of them.
"""
import random
from collections import deque
from pathlib import Path

import numpy as np
import pytest

from host.asm import assemble
from host.wcet import WcetError, analyse, bench
from host.wcet import analysis as A

from . import test_wcet_bruteforce as brute
from . import test_wcet_compose as compose
from .test_wcet_review import HALT_IN_CALLEE

ROOT = Path(__file__).resolve().parents[1]


def sources():
    for p in sorted((ROOT / "programs").glob("*.tta")):
        yield p.name, p.read_text(encoding="utf-8")
    yield "bench", bench.generate()
    yield "halt_in_callee", HALT_IN_CALLEE.format(c=1)
    for seed in range(60):
        yield f"brute{seed}", brute.Gen(random.Random(seed)).source()
    for seed in range(60):
        yield f"compose{seed}", compose.Gen(random.Random(seed)).source()


def capture(monkeypatch, src):
    """[(graph, start, end, constraints, result)] for every ILP of one analysis."""
    real_milp, real_solve = A.milp, A._solve
    found, box = [], {}

    def milp(c, constraints, **kw):
        box["cons"] = constraints
        box["res"] = real_milp(c, constraints=constraints, **kw)
        return box["res"]

    def solve(f, g, start, start_kind, end, *args, **kw):
        box.clear()
        seg = real_solve(f, g, start, start_kind, end, *args, **kw)
        if "cons" in box:
            found.append((g, start, end, box["cons"], box["res"]))
        return seg

    monkeypatch.setattr(A, "milp", milp)
    monkeypatch.setattr(A, "_solve", solve)
    analyse(assemble(src, "t.tta"))
    return found


def path_vector(g, start, end):
    """0/1 edge counts of a shortest path start -> end, then source and sink."""
    prev = {start: None}
    queue = deque([start])
    while queue:
        u = queue.popleft()
        for i, (a, b, _) in enumerate(g.edges):
            if a == u and b not in prev:
                prev[b] = i
                queue.append(b)
    assert end in prev, "end not reachable from start"
    x = np.zeros(len(g.edges) + 2)
    at = end
    while prev[at] is not None:
        i = prev[at]
        x[i] += 1
        at = g.edges[i][0]
    x[-2] = x[-1] = 1
    return x


def test_every_ilp_has_the_path_as_a_feasible_point(monkeypatch):
    count = 0
    for name, src in sources():
        for g, start, end, cons, res in capture(monkeypatch, src):
            x = path_vector(g, start, end)
            for c in cons:
                ax = np.asarray(c.A) @ x
                assert np.all(ax >= np.asarray(c.lb) - 1e-9) and np.all(ax <= np.asarray(c.ub) + 1e-9), \
                    (name, start, end)
            assert res.status == 0, (name, start, end, res.message)
            count += 1
    assert count >= 400, count                      # the claim was checked on many ILPs (480 now)


def test_a_bound_below_one_is_refused_by_the_analysis_itself():
    prog = assemble("#2 -> r1\n@loop_bound 2\nl: r1 -> pc.cond\n#l -> pc.t_jnz\n#0 -> trap.t_halt", "t.tta")
    for a in prog.annotations:
        a["n"] = 0                                  # the assembler refuses 0; force it
    with pytest.raises(WcetError, match="N must be >= 1"):
        analyse(prog)
