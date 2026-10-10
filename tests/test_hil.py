"""Phase 5 closed loop: control program, plant, model, trap, WCET, lockstep."""
import pytest

from host.asm import assemble
from host.common import spec
from host.hil import env, model as M, programs as P
from host.wcet import analyse

N = 1000                                   # one full setpoint schedule (1 s)


@pytest.fixture(scope="module")
def iss_run():
    return M.run_iss(N)


def test_committed_programs_are_up_to_date():
    for trap, path in P.OUT.items():
        assert path.read_text(encoding="utf-8") == P.generate(trap), \
            f"{path.name} is stale; run: py -m host.hil.programs"


def test_program_equals_the_python_model_every_period(iss_run):
    # the TTA program running on the ISS against FixedPID + FixedPlant on the
    # same cycle timeline: same encoder, same command, every period
    ref = M.reference(N, iss_run.c_sync)
    assert len(iss_run.periods) == N
    for a, b in zip(iss_run.periods, ref):
        assert a == b, f"period {a.k}: program {a}, model {b}"


def test_sense_and_actuate_never_move(iss_run):
    # zero jitter: every period reads elapsed at the same offsets from its anchor
    assert {p.el_sense for p in iss_run.periods} == {2}
    assert {p.el_act for p in iss_run.periods} == {P.ACT + 2}
    assert {p.flags for p in iss_run.periods} == {0}              # never late, never trapped
    ks = [p.k for p in iss_run.periods]
    assert ks == list(range(N))


def test_the_loop_tracks_the_setpoint_and_rejects_the_load(iss_run):
    enc = {p.k: p.enc for p in iss_run.periods}
    end_of = {}
    k = 0
    for n, ref in env.SCHEDULE:
        k += n
        end_of[k - 1] = ref
    for k, ref in end_of.items():
        assert abs(enc[k] - ref) <= 2, (k, enc[k], ref)              # settled by segment end
    load_k = env.LOAD_ON // P.PERIOD                                   # the load starts here
    dip = min(enc[k] for k in range(load_k, load_k + 100))
    assert 4096 - 100 < dip < 4096                                     # pushed back, not far
    assert abs(enc[env.LOAD_OFF // P.PERIOD - 1] - 4096) <= 2          # integrator removed it


def test_the_control_task_is_under_its_deadline():
    rep = analyse(assemble(P.generate(False), "control.tta"))
    segs = {(s.start_kind, s.end_kind): s for s in rep.functions["main"].segments}
    sense = segs[("t_advance", "t_wait")]
    assert sense.ok and sense.budget == P.ACT - spec.D and sense.wcet < 200
    assert segs[("t_wait", "t_advance")].ok
    assert sense.wcet + 1 < P.DEADLINE                 # t_wait itself comes before the deadline


def test_the_trap_variant_is_reported_over_budget():
    rep = analyse(assemble(P.generate(True), "control_trap.tta"))
    segs = {(s.start_kind, s.end_kind): s for s in rep.functions["main"].segments}
    assert not segs[("t_advance", "t_wait")].ok


def test_deadline_trap_fires_at_the_exact_cycle():
    run = M.run_iss(P.TRAP_K + 5, trap=True)
    anchor = run.c_sync + (P.TRAP_K + 1) * P.PERIOD
    d = anchor + P.DEADLINE
    t = [l for l in run.sim.trace if l.startswith("T ")]
    assert len(t) == 1
    cyc, epc, cause = t[0].split()[1:]
    assert int(cyc) == d and int(cause) == spec.TrapCause.DEADLINE
    assert run.traps == [M.Trap(spec.TrapCause.DEADLINE, int(epc, 16), P.DEADLINE + 1 + spec.H, P.TRAP_K)]
    assert run.sim.pwm_log[-1] == (d + 1 + spec.H + 1, 0)            # safe state right after
    assert run.sim.halted == "halt"
    # every period before the overrun is the normal loop
    ref = M.reference(P.TRAP_K, run.c_sync)
    assert run.periods[:P.TRAP_K] == ref


# ---------------------------------------------------------------- RTL closed loop
from .test_lockstep import _verilator_available                      # noqa: E402

rtl = pytest.mark.skipif(not _verilator_available(), reason="Verilator not available")


@rtl
def test_closed_loop_lockstep():
    from host.lockstep import runner
    o = runner.run_hil(P.generate(False), "control", max_cycles=3_000_000)
    assert o.ok, o.mismatch


@rtl
def test_closed_loop_lockstep_through_the_trap():
    from host.lockstep import runner
    o = runner.run_hil(P.generate(True), "control_trap", max_cycles=6_000_000)
    assert o.ok, o.mismatch
    assert o.iss[-1].endswith("halt")
