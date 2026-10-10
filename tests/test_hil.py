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
    assert P.OUT_SW.read_text(encoding="utf-8") == P.generate(switch=True), \
        f"{P.OUT_SW.name} is stale; run: py -m host.hil.programs"


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


# ---------------------------------------------------------------- switch mode (control_sw.tta)
SW_N = 700


@pytest.fixture(scope="module")
def sw_c_sync():
    return M.run_iss_sw(1).c_sync


def sw_din(c):
    """io.din at the core input for the switch-mode tests: every setpoint code,
    changes on the sample cycle (anchor + 2) and one after it, the load switch on
    outside the load window and again across it."""
    a = lambda k: c + (k + 1) * P.PERIOD                            # noqa: E731
    return [(0, 0), (a(50) + 2, 1), (a(250) + 3, 2), (a(300) + 2, 2 | 4), (a(330) + 777, 2),
            (a(380) + 2, 3 | 4), (a(520) + 3, 3), (a(600) + 40_000, 0)]


def test_switch_mode_constants_keep_every_value_sampled():
    # docs/io_interface.md sec. 3: din[2] needs N_DB + 1 >= PERIOD, din[1:0] 2(N_DB + 1) >= PERIOD
    assert env.N_DB + 1 >= P.PERIOD and 2 * (env.N_DB + 1) >= P.PERIOD
    assert len(env.SP_TABLE) == env.DIN_SP + 1
    assert all(abs(v) <= m_eclamp() for v in env.SP_TABLE)


def m_eclamp():
    from host.plant_model import model as m
    return 4 * m.E_CLAMP


def test_control_sw_shares_the_pid_text_of_control():
    def pid(text):
        return text[text.index("        ; e = clamp"):text.index("alu.out     -> r5               ; acc")]
    assert pid(P.generate(switch=True)) == pid(P.generate(False))


def test_control_sw_equals_the_model_with_din_zero(sw_c_sync):
    # all switches down: setpoint 0 and the load window as the only disturbance
    run = M.run_iss_sw(N)
    assert run.c_sync == sw_c_sync
    assert run.periods == M.reference_din(N, run.c_sync, [(0, 0)])
    assert {p.din for p in run.periods} == {0}
    assert max(abs(p.enc) for p in run.periods) > 0                 # the load did move the motor


def test_control_sw_follows_din_and_the_load_switch(sw_c_sync):
    din = sw_din(sw_c_sync)
    run = M.run_iss_sw(SW_N, din)
    ref = M.reference_din(SW_N, sw_c_sync, din)
    for a, b in zip(run.periods, ref):
        assert a == b, f"period {a.k}: program {a}, model {b}"
    assert len(run.periods) == SW_N and run.traps == []
    p = run.periods
    # a change on the sample cycle is used in that period, one cycle later in the next
    assert (p[49].ref, p[50].ref) == (0, env.SP_TABLE[1])
    assert (p[250].ref, p[251].ref) == (env.SP_TABLE[1], env.SP_TABLE[2])
    assert (p[519].din, p[520].din, p[521].din) == (3 | 4, 3 | 4, 3)
    # the load switch reaches the plant: on from anchor(300) + 2, so the encoder
    # sensed at anchor(301) + 1 is the first one that differs from a run without it
    no_switch = M.reference_din(SW_N, sw_c_sync, [(c, v & env.DIN_SP) for c, v in din])
    first = next(k for k, (x, y) in enumerate(zip(p, no_switch)) if x.enc != y.enc)
    assert first == 301


def test_control_sw_timing_does_not_depend_on_din(sw_c_sync):
    run = M.run_iss_sw(SW_N, sw_din(sw_c_sync))
    assert {(x.el_in, x.el_act, x.flags) for x in run.periods} == {(P.DIN_AT + 1, P.ACT + 2, 0)}
    rep = analyse(assemble(P.generate(switch=True), "control_sw.tta"))
    segs = {(s.start_kind, s.end_kind): s for s in rep.functions["main"].segments}
    sense = segs[("t_advance", "t_wait")]
    assert sense.ok and sense.budget == P.ACT - spec.D and sense.wcet < 200


@rtl
def test_switch_closed_loop_lockstep():
    # io.din from the stimulus, din[2] also the load (tb_tta wires it to hil_env),
    # with the shortened test load window: setpoint changes on and after the
    # sample cycle, the load switch on and off inside and outside the window
    from host.lockstep import runner
    c = M.run_iss_sw(1).c_sync
    a = lambda k: c + (k + 1) * P.PERIOD                            # noqa: E731
    stim = "".join(f"{cy} io.din {v}\n" for cy, v in
                   [(a(2) + 2, 1), (a(6) + 3, 2 | 4), (a(9) + 500, 2), (a(14) + 2, 3 | 4), (a(24) + 3, 0)])
    o = runner.run_hil(P.generate(switch=True), "control_sw", max_cycles=3_000_000, stim_text=stim)
    assert o.ok, o.mismatch
