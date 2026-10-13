"""A solver result that is not a proven optimum must never become a WCET (audit A3-3).

Before the fix, any status other than 0 was read as "this end is unreachable"
and the segment was silently left out of the report. scipy.optimize.milp
statuses: 0 optimal, 1 iteration or time limit, 2 infeasible, 3 unbounded,
4 other (numerical trouble and the like).

Status 0 is only reported success: HiGHS stops once the gap is within its
tolerance (mip_rel_gap, nonzero by default), so the returned W may be below
the maximum. The analysis asks for mip_rel_gap = 0 and accepts W only when the
dual bound proves that no integer solution exceeds it (review of PR Round A).
"""
from types import SimpleNamespace

import numpy as np
import pytest

from host.asm import assemble
from host.wcet import WcetError, analyse
from host.wcet import analysis as A
from host.wcet.__main__ import main

SRC = "#3 -> r1\n@loop_bound 3\nloop: r1 -> pc.cond\n#loop -> pc.t_jnz\n#0 -> trap.t_halt"
REAL_MILP = A.milp


def fake(**kw):
    def milp(c, **_):
        n = len(c)
        res = dict(status=0, x=None, fun=None, message="faked")
        res.update({k: (v(n) if callable(v) else v) for k, v in kw.items()})
        return SimpleNamespace(**res)
    return milp


@pytest.mark.parametrize("status, message", [
    (1, "Time limit reached"),
    (2, "The problem is infeasible"),
    (3, "The problem is unbounded"),
    (4, "Numerical difficulties"),
])
def test_non_optimal_status_is_an_error_not_unreachable(monkeypatch, status, message):
    monkeypatch.setattr(A, "milp", fake(status=status, message=message))
    with pytest.raises(WcetError, match=rf"did not report success .*status {status}: {message}"):
        analyse(assemble(SRC, "t.tta"))


def test_status_1_with_a_feasible_but_unproven_point_is_an_error(monkeypatch):
    # a time limit can still hand back the best point found so far; it is not a bound
    def milp(c, **kw):
        res = REAL_MILP(c, **kw)
        return SimpleNamespace(status=1, x=res.x, fun=res.fun, message="Time limit reached")
    monkeypatch.setattr(A, "milp", milp)
    with pytest.raises(WcetError, match="status 1"):
        analyse(assemble(SRC, "t.tta"))


@pytest.mark.parametrize("x, fun, what", [
    (lambda n: None, -5.0, "did not report success"),
    (lambda n: np.zeros(n), 0.0, "not one path"),             # optimal but leaves no flow to the sink
    (lambda n: np.full(n, 0.5), -3.0, "not one path"),        # fractional counts
])
def test_optimal_status_with_an_unusable_solution_is_an_error(monkeypatch, x, fun, what):
    monkeypatch.setattr(A, "milp", fake(status=0, x=x, fun=fun))
    with pytest.raises(WcetError, match=what):
        analyse(assemble(SRC, "t.tta"))


def test_objective_that_disagrees_with_the_counts_is_an_error(monkeypatch):
    def milp(c, **kw):
        res = REAL_MILP(c, **kw)
        return SimpleNamespace(status=0, x=res.x, fun=res.fun - 1, message="faked")
    monkeypatch.setattr(A, "milp", milp)
    with pytest.raises(WcetError, match="objective"):
        analyse(assemble(SRC, "t.tta"))


def solved(**override):
    """The real solution with some fields replaced: what a solver that stopped
    early, or reported its bound badly, would hand back."""
    def milp(c, **kw):
        res = REAL_MILP(c, **kw)
        fields = {k: res[k] for k in res}
        fields.update({k: (v(res) if callable(v) else v) for k, v in override.items()})
        return SimpleNamespace(**fields)
    return milp


@pytest.mark.parametrize("dual", [
    lambda r: r.fun - 1,          # status 0, but the bound allows a W one higher
    lambda r: r.fun - 40,         # a wide gap, as with a nonzero mip_rel_gap
    lambda r: -np.inf,            # no bound at all
    lambda r: np.nan,
    None,                         # the field is missing
])
def test_success_without_a_proof_of_optimality_is_an_error(monkeypatch, dual):
    if dual is None:
        def milp(c, **kw):
            res = REAL_MILP(c, **kw)
            return SimpleNamespace(**{k: res[k] for k in res if k != "mip_dual_bound"})
    else:
        milp = solved(mip_dual_bound=dual, mip_gap=0.01)
    monkeypatch.setattr(A, "milp", milp)
    with pytest.raises(WcetError, match="reported success .* but did not prove W = .* optimal"):
        analyse(assemble(SRC, "t.tta"))


@pytest.mark.parametrize("override", [
    {"fun": np.nan},                                   # review: NaN fails every comparison
    {"fun": np.inf},
    {"fun": -np.inf},
    {"x": lambda r: np.where(np.arange(len(r.x)) == 0, np.nan, r.x)},
    {"x": lambda r: np.where(np.arange(len(r.x)) == 0, np.inf, r.x)},
])
def test_success_with_a_non_finite_objective_or_solution_is_an_error(monkeypatch, override):
    # status 0, and the rest of the result (counts, dual bound) is the real, valid one
    monkeypatch.setattr(A, "milp", solved(**override))
    with pytest.raises(WcetError, match="non-finite objective or solution"):
        analyse(assemble(SRC, "t.tta"))


def test_a_gap_below_one_cycle_is_still_a_proof(monkeypatch):
    # every W is an integer, so a bound below W + 1 leaves no better solution
    monkeypatch.setattr(A, "milp", solved(mip_dual_bound=lambda r: r.fun - 0.5))
    assert analyse(assemble(SRC, "t.tta")).program_wcet() == 12


def test_the_solver_is_asked_to_close_the_gap(monkeypatch):
    seen = []

    def milp(c, **kw):
        seen.append(kw.get("options"))
        return REAL_MILP(c, **kw)
    monkeypatch.setattr(A, "milp", milp)
    analyse(assemble(SRC, "t.tta"))
    assert seen and all(o == {"mip_rel_gap": 0.0} for o in seen)


def test_the_real_solver_still_gives_the_bound():
    assert analyse(assemble(SRC, "t.tta")).program_wcet() == 1 + 3 * 2 + 2 * 2 + 1


def test_cli_reports_the_failure_and_prints_no_wcet(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(A, "milp", fake(status=1, message="Time limit reached"))
    path = tmp_path / "t.tta"
    path.write_text(SRC, encoding="utf-8")
    assert main([str(path)]) == 1
    out, err = capsys.readouterr()
    assert "status 1" in err and "program:" not in out and "W =" not in out
