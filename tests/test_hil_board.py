"""host.hil.board: the board stream parser and the check against the model,
fed with the ISS's own telemetry as if it came from the UART."""
import dataclasses
import random
import struct

import pytest

from host.hil import board as B, model as M, programs as P

N = 20                                     # periods in the normal-run tests


def _bytes(words):
    return struct.pack(f"<{len(words)}I", *(w & 0xFFFF_FFFF for w in words))


def _records(run):
    """The ISS telemetry split into records: one list of words each."""
    words = [w for _, w in run.sim.telem.accepted]
    out, i = [], 0
    while i < len(words):
        n = P.RECORD_WORDS if words[i] & 0xFFFF_FFFF == P.MAGIC else 5
        out.append(words[i:i + n])
        i += n
    return out


def _chunks(data, seed=None):
    if seed is None:
        return [data]
    rng, out, i = random.Random(seed), [], 0
    while i < len(data):
        n = rng.randint(1, 37)
        out.append(data[i:i + n])
        i += n
    return out


@pytest.fixture(scope="module")
def normal():
    run = M.run_iss(N)
    return run, _records(run)


@pytest.fixture(scope="module")
def trapped():
    run = M.run_iss(P.TRAP_K + 1, trap=True)
    recs = _records(run)
    assert len(recs) == P.TRAP_K + 1 and recs[-1][0] == P.TRAP_MAGIC
    return run, recs


def _parse(recs, n, trap, seed=None, prefix=b""):
    data = prefix + b"".join(_bytes(r) for r in recs)
    return B.parse_stream(_chunks(data, seed), n, trap)


# ---------------------------------------------------------------- trap run
@pytest.mark.parametrize("seed", [None, 1, 2, 3])
def test_trap_run_accepts_the_complete_sequence(trapped, seed):
    run, recs = trapped
    board, trap = _parse(recs, P.TRAP_K, True, seed)
    assert [p.k for p in board] == list(range(P.TRAP_K))
    assert trap.k == P.TRAP_K
    assert B.check(board, trap, run, P.TRAP_K, True) == []


def test_trap_run_rejects_a_missing_last_period(trapped):
    # the reviewer's case: the trap record itself is right, a period before it is gone
    _, recs = trapped
    with pytest.raises(B.BoardError, match="trap record after 49 periods, expected 50"):
        _parse(recs[:P.TRAP_K - 1] + recs[P.TRAP_K:], P.TRAP_K, True)


def test_trap_run_rejects_a_missing_middle_period(trapped):
    _, recs = trapped
    with pytest.raises(B.BoardError, match="expected period 10, got 11"):
        _parse(recs[:10] + recs[11:], P.TRAP_K, True)


def test_trap_run_rejects_a_trap_with_no_periods_before_it(trapped):
    _, recs = trapped
    # period 0 is what starts the comparison, so a lone trap record never ends it
    with pytest.raises(B.BoardError, match="ended after 0 periods"):
        _parse(recs[P.TRAP_K:], P.TRAP_K, True)
    with pytest.raises(B.BoardError, match="trap record after 1 periods"):
        _parse(recs[:1] + recs[P.TRAP_K:], P.TRAP_K, True)


def test_trap_run_rejects_an_extra_period_before_the_trap(trapped):
    _, recs = trapped
    extra = list(recs[P.TRAP_K - 1])
    extra[1] = P.TRAP_K
    with pytest.raises(B.BoardError, match="period 50 arrived where the trap record"):
        _parse(recs[:P.TRAP_K] + [extra] + recs[P.TRAP_K:], P.TRAP_K, True)


def test_trap_run_rejects_a_trap_record_for_another_period(trapped):
    _, recs = trapped
    bad = list(recs[P.TRAP_K])
    bad[4] = P.TRAP_K + 1
    with pytest.raises(B.BoardError, match="names period 51, expected 50"):
        _parse(recs[:P.TRAP_K] + [bad], P.TRAP_K, True)


def test_trap_run_rejects_a_stream_that_never_traps(trapped):
    _, recs = trapped
    with pytest.raises(B.BoardError, match="ended after 50 periods"):
        _parse(recs[:P.TRAP_K], P.TRAP_K, True)


def test_trap_run_resynchronises_on_period_zero(trapped):
    run, recs = trapped
    # an earlier run's tail and a word cut by the reset come before period 0
    junk = b"\x01\x02\x03" + b"".join(_bytes(r) for r in recs[30:])
    board, trap = _parse(recs, P.TRAP_K, True, seed=5, prefix=junk)
    assert B.check(board, trap, run, P.TRAP_K, True) == []


def test_check_reports_a_wrong_field_and_a_wrong_trap(trapped):
    run, recs = trapped
    board, trap = _parse(recs, P.TRAP_K, True)
    board[7] = dataclasses.replace(board[7], u=board[7].u + 1)
    diffs = B.check(board, trap, run, P.TRAP_K, True)
    assert len(diffs) == 1 and diffs[0].startswith("period 7:")
    board, trap = _parse(recs, P.TRAP_K, True)
    diffs = B.check(board, dataclasses.replace(trap, epc=trap.epc + 1), run, P.TRAP_K, True)
    assert len(diffs) == 1 and diffs[0].startswith("trap record:")


def test_check_requires_the_full_period_count(trapped):
    run, recs = trapped
    board, trap = _parse(recs, P.TRAP_K, True)
    diffs = B.check(board[:-1], trap, run, P.TRAP_K, True)
    assert diffs == [f"board has {P.TRAP_K - 1} periods, model {P.TRAP_K}"]


# ---------------------------------------------------------------- normal run
@pytest.mark.parametrize("seed", [None, 1, 2])
def test_normal_run_stops_after_n_periods(normal, seed):
    run, recs = normal
    board, trap = _parse(recs, N, False, seed)
    assert trap is None and len(board) == N
    assert B.check(board, trap, run, N, False) == []


def test_normal_run_rejects_a_trap_record(normal, trapped):
    _, recs = normal
    _, trecs = trapped
    with pytest.raises(B.BoardError, match="in a run without a trap"):
        _parse(recs[:5] + trecs[P.TRAP_K:], N, False)


def test_normal_run_rejects_a_short_stream(normal):
    _, recs = normal
    with pytest.raises(B.BoardError, match=f"ended after {N - 1} periods"):
        _parse(recs[:N - 1], N, False)
