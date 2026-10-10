"""ISS against the hand-counted examples in timing_model.md §5 and the trap rules."""
import json

import pytest

from host.asm import assemble, output_files
from host.common import spec
from host.iss import (ImageError, SimError, Simulator, Stimulus, StimulusError,
                      check_invariants, load)

from .test_asm import EXAMPLE_1

R = spec.R


def run(src, max_cycles=1_000_000, stim=None, checks=True):
    prog = assemble(src, "t.tta", checks=checks)
    sim = Simulator(prog.code, prog.data, stim, prog.imem_words, prog.dmem_words)
    return sim.run(max_cycles)


def moves(result, dst=None):
    """(cycle, pc, dst_name, value) of every M record, optionally for one dst."""
    out = []
    for line in result.trace:
        f = line.split()
        if f[0] == "M":
            name = spec.PORT_BY_ID[int(f[5], 16)].name
            if dst is None or name == dst:
                out.append((int(f[1]), int(f[2], 16), name, int(f[6], 16)))
    return out


def records(result, kind):
    return [line for line in result.trace if line[0] == kind]


# ---------------------------------------------------------------- §5 example 1
def test_example_1_straight_line():
    r = run(EXAMPLE_1)
    assert [m[0] for m in moves(r)] == list(range(R, R + 10))   # one move per cycle
    assert r.sim.regs[3] == 36 and r.sim.regs[4] == 10
    assert r.end == "halt" and r.cycle == R + 9 and r.trace[-1] == f"H {R + 9} halt"
    assert r.trace[0] == "M 2 00000000 11800005 imm 11 00000005"


def test_reading_mul_out_one_cycle_early_is_a_program_error():
    with pytest.raises(SimError, match="R3"):
        run("#2 -> mul.a\n#3 -> mul.t_b\nmul.out -> r1\n#0 -> trap.t_halt", checks=False)


# ---------------------------------------------------------------- §5 example 2
LOOP = """
        #{n}        -> r1
        #SUB        -> alu.op
@loop_bound {n}
loop:   r1          -> alu.a
        #1          -> alu.t_b
        alu.out     -> r1
        r1          -> pc.cond
        #loop       -> pc.t_jnz
after:  #0          -> trap.t_halt
"""


@pytest.mark.parametrize("n", [1, 2, 5, 16])
def test_example_2a_loop_takes_7n(n):
    r = run(LOOP.format(n=n))
    assert r.cycle == R + 7 * n                       # 'after' executes at 7N
    assert r.sim.regs[1] == 0


IF_ELSE = """
        #{x}        -> r1
        r1          -> pc.cond
        #else       -> pc.t_jz
        #10         -> r2
        #join       -> pc.t_jump
else:   #20         -> r2
join:   #0          -> trap.t_halt
"""


@pytest.mark.parametrize("x, join, r2", [(7, 6, 10), (0, 5, 20)])
def test_example_2b_if_else(x, join, r2):
    r = run(IF_ELSE.format(x=x))
    assert r.cycle == R + 1 + join                    # +1 for the move that sets r1
    assert r.sim.regs[2] == r2


# ---------------------------------------------------------------- §5 example 3
def control(k):
    """timing_model.md §5 example 3 with a k-move control law (nops)."""
    return (".equ PERIOD, 100_000\n"
            "        #on_trap    -> trap.handler\n"       # address 0
            "        #0          -> tmr.t_sync\n"         # 1
            "loop:   #PERIOD     -> tmr.t_advance\n"      # 2
            "        io.encoder  -> r1\n"                 # 3: sense
            "        #2000       -> tmr.t_arm\n"          # 4
            + "        nop\n" * k +                       # 5 ..
            "        #1000       -> tmr.t_wait\n"
            "        r5          -> io.pwm_cmd\n"         # actuate
            "        #0          -> tmr.t_clear\n"
            + "        r1          -> telem.t_push\n" * 6 +
            "        #loop       -> pc.t_jump\n"
            ".func on_trap\n"
            "        #0          -> io.pwm_cmd\n"
            "        trap.cause  -> telem.t_push\n"
            "        #0          -> trap.t_halt\n"
            ".endfunc\n")


A0 = R + 1                                            # t_sync executes at cycle 3


def test_example_3_timeline():
    r = run(control(623), max_cycles=A0 + 3 * 100_000 + 2_000)
    for k in (1, 2, 3):
        a = A0 + k * 100_000
        assert (a + 1) in [m[0] for m in moves(r, "r1") if m[1] == 3]      # sense
        assert (a + 1001, 0) in r.sim.pwm_log                                # actuate
        adv = [m[0] for m in moves(r, "tmr.t_advance")]
        assert a + 1012 in adv
    assert r.end == "maxcycles" and r.trace[-1].startswith("E ")


def test_example_3_late_but_no_trap():
    r = run(control(1200), max_cycles=A0 + 2 * 100_000 + 3_000)
    a = A0 + 100_000
    waits = [m[0] for m in moves(r, "tmr.t_wait")]
    assert a + 1203 in waits
    assert (a + 1204, 0) in r.sim.pwm_log
    assert not records(r, "T")
    a2 = a + 100_000                                  # the next period is back on the grid
    assert (a2 + 1204, 0) in r.sim.pwm_log
    assert a2 + 1 in [m[0] for m in moves(r, "r1") if m[1] == 3]


def test_example_3_deadline_trap():
    r = run(control(2100), max_cycles=A0 + 2 * 100_000)
    a = A0 + 100_000
    d = a + 2000
    nop0 = 5                                          # address of the first nop
    assert records(r, "T") == [f"T {d} {nop0 + (2000 - 3):08x} 1"]
    assert r.sim.pwm_log[-1] == (d + 1 + spec.H, 0)   # safe state at d + 3
    assert r.end == "halt"


# ---------------------------------------------------------------- timer details
def test_late_flag_and_stall_wake():
    r = run("""
        #0          -> tmr.t_sync
        #10         -> tmr.t_wait
        tmr.flags   -> r1
        tmr.elapsed -> r2
        #1          -> tmr.t_wait
        tmr.flags   -> r3
        #0          -> trap.t_halt
""")
    sync = R
    assert moves(r, "r1")[0][0] == sync + 10 + spec.D       # woke at T + D
    assert r.sim.regs[1] == 0 and r.sim.regs[2] == 10 + 1 + 1
    assert r.sim.regs[3] == 1                                  # late bit


def test_trap_during_t_wait_stall():
    r = run("""
        #h          -> trap.handler
        #0          -> tmr.t_sync
        #20         -> tmr.t_arm
        #100        -> tmr.t_wait
after:  #0          -> trap.t_halt
.func h
        trap.epc    -> r1
        #0          -> trap.t_halt
.endfunc
""")
    sync = R + 1
    assert records(r, "T") == [f"T {sync + 20} 00000004 1"]   # epc = move after t_wait
    assert r.sim.regs[1] == 4


def test_past_deadline_traps_next_cycle():
    r = run("""
        #0          -> tmr.t_sync
        nop
        #0          -> tmr.t_arm
        nop
        #0          -> trap.t_halt
""")
    arm = R + 2
    assert records(r, "T") == [f"T {arm + 1} 00000003 1"] and r.end == "nohandler"


@pytest.mark.parametrize("nops, trapped", [(3, False), (4, True)])
def test_t_clear_must_run_before_the_deadline_cycle(nops, trapped):
    # sync at R, deadline = R + 6, t_clear at R + 2 + nops
    src = "#0 -> tmr.t_sync\n#6 -> tmr.t_arm\n" + "nop\n" * nops + "#0 -> tmr.t_clear\n#0 -> trap.t_halt"
    r = run(src)
    assert bool(records(r, "T")) == trapped           # t_clear at deadline - 1 is fine, at deadline it traps


def test_t_advance_while_armed_is_a_program_error():
    with pytest.raises(SimError, match="armed"):
        run("#0 -> tmr.t_sync\n#100 -> tmr.t_arm\n#10 -> tmr.t_advance\n#0 -> trap.t_halt",
            checks=False)


def test_not_taken_jump_with_bad_target_does_not_trap():
    r = run("#0 -> pc.cond\n.illegal 0x5380_1388\n.func h\n#0 -> trap.t_halt\n.endfunc",
            checks=False)                             # #5000 -> pc.t_jnz, not taken
    assert not records(r, "T")


# ---------------------------------------------------------------- traps
HANDLER = """
.func h
        trap.cause  -> r1
        trap.epc    -> r2
        #0          -> trap.t_halt
.endfunc
"""


@pytest.mark.parametrize("body, cause, epc", [
    (".illegal 0x0F80_0000", 2, 1),                       # dst 0x0f does not exist
    (".illegal 0x2180_000C", 3, 1),                       # #12 -> alu.op
    ("#5000 -> r3\nr3 -> mem.t_load\n#0 -> trap.t_halt", 4, 2),
    (".illegal 0x5180_1388", 5, 1),                       # #5000 -> pc.t_jump
])
def test_trap_causes(body, cause, epc):
    r = run("#h -> trap.handler\n" + body + "\n" + HANDLER)
    t = records(r, "T")
    assert len(t) == 1 and t[0].endswith(f" {epc:08x} {cause}")
    d = int(t[0].split()[1])
    assert moves(r)[-3][0] == d + 1 + spec.H              # first handler move
    assert r.sim.regs[1] == cause and r.sim.regs[2] == epc


def test_no_handler_halts():
    r = run(".illegal 0x0F80_0000")
    assert r.trace == [f"T {R} 00000000 2", f"H {R} nohandler"]


def test_double_fault_halts():
    r = run("#h -> trap.handler\n.illegal 0x0F80_0000\n.func h\n.illegal 0x0F80_0001\n.endfunc")
    assert [l.split()[0] for l in r.trace] == ["M", "T", "T", "H"]
    assert r.trace[-1].endswith("double")


def test_deadline_beats_illegal_in_the_same_cycle():
    r = run("""
        #h          -> trap.handler
        #0          -> tmr.t_sync
        #3          -> tmr.t_arm
        nop
        .illegal 0x0F80_0000
""" + HANDLER)
    sync = R + 1
    assert records(r, "T") == [f"T {sync + 3} 00000004 1"]


# ---------------------------------------------------------------- PR #3 review: exact boundaries
# Each program below is also run in lockstep (tests/test_lockstep.py), so the
# RTL is held to the same numbers.

# t_wait / t_advance at c = R + 1 with anchor = R, so T = R + v and
# v = 2, 1, 0 put the move at now = T - 1, T, T + 1.
TIMING_EDGE = """
        #0          -> tmr.t_sync
        #{v}        -> tmr.{op}
        tmr.flags   -> r1
        tmr.elapsed -> r2
        #0          -> trap.t_halt
"""


@pytest.mark.parametrize("op", ["t_wait", "t_advance"])
@pytest.mark.parametrize("v, stall, late", [(2, 1, 0), (1, 0, 0), (0, 0, 1)])
def test_timing_move_at_target_boundaries(op, v, stall, late):
    r = run(TIMING_EDGE.format(op=op, v=v))
    c = R + 1
    assert moves(r, "r1")[0][0] == c + stall + spec.D     # max(c, T) + D
    assert r.sim.regs[1] == late                         # flags = {trapped, armed, late}
    anchor = R + v if op == "t_advance" else R
    assert r.sim.regs[2] == c + stall + spec.D + 1 - anchor


# t_arm at c = R + 2 with anchor = R + 1, so deadline = R + 1 + v and
# v = 0..3 put the deadline at c - 1, c, c + 1, c + 2.
DEADLINE_EDGE = """
        #h          -> trap.handler
        #0          -> tmr.t_sync
        #{v}        -> tmr.t_arm
        nop
        nop
        nop
        #0          -> trap.t_halt
""" + HANDLER


@pytest.mark.parametrize("v, d, epc", [
    (0, R + 3, 3),            # deadline already past: trap at c + 1
    (1, R + 3, 3),            # deadline = c: trap at c + 1
    (2, R + 3, 3),            # deadline = c + 1: trap exactly there, not at c
    (3, R + 4, 4),            # deadline = c + 2: no trap at c + 1
])
def test_deadline_boundaries(v, d, epc):
    r = run(DEADLINE_EDGE.format(v=v))
    assert records(r, "T") == [f"T {d} {epc:08x} 1"]
    assert r.sim.regs[2] == epc


# trap.epc is the next move in program order that has not executed (R6), in
# every place a deadline can land: on a move, in a jump bubble, on the target.
EPC_IN_JUMP = """
        #h          -> trap.handler
        #0          -> tmr.t_sync
        #{v}        -> tmr.t_arm
        #tgt        -> pc.t_jump
        nop
tgt:    #0          -> trap.t_halt
""" + HANDLER


@pytest.mark.parametrize("v, d, epc", [
    (2, R + 3, 3),            # on the jump itself: squashed, epc = the jump
    (3, R + 4, 5),            # first bubble: epc = target
    (4, R + 5, 5),            # second bubble: epc = target
    (5, R + 6, 5),            # on the target: squashed, epc = target
])
def test_trap_epc_around_a_jump(v, d, epc):
    r = run(EPC_IN_JUMP.format(v=v))
    assert records(r, "T") == [f"T {d} {epc:08x} 1"]
    assert r.sim.regs[2] == epc


EPC_ILLEGAL = "#h -> trap.handler\n" + "nop\n" * 5 + ".illegal 0x0F80_0000\n" + HANDLER


def test_trap_epc_is_the_illegal_move():
    r = run(EPC_ILLEGAL)
    assert records(r, "T") == [f"T {R + 6} 00000006 2"] and r.sim.regs[2] == 6


# FIFO full: 65 pushes from R + 1 leave 64 words queued and one in the UART,
# whose next pop is at R + 2 + TELEM_CYCLES_PER_WORD. One more push lands at
# R + v + 1: one cycle early it is dropped, on the pop cycle it fits (the UART
# pops first, sec. 6.5 of toolchain_formats.md), one cycle late it fits.
FIFO_EDGE = ("#0 -> tmr.t_sync\n" + "#0 -> telem.t_push\n" * (spec.TELEM_FIFO_WORDS + 1)
             + "#{v} -> tmr.t_wait\n#1 -> telem.t_push\ntelem.drops -> r1\n#0 -> trap.t_halt")
POP_2 = 2 + spec.TELEM_CYCLES_PER_WORD               # second pop, relative to the sync at R


@pytest.mark.parametrize("v, drops", [(POP_2 - 2, 1), (POP_2 - 1, 0), (POP_2, 0)])
def test_fifo_full_push_on_the_pop_cycle(v, drops):
    r = run(FIFO_EDGE.format(v=v))
    assert moves(r, "telem.t_push")[-1][0] == R + v + 1
    assert r.sim.regs[1] == drops
    assert len(r.sim.telem.accepted) == spec.TELEM_FIFO_WORDS + 2 - drops


# ---------------------------------------------------------------- FUs
@pytest.mark.parametrize("op, a, b, out", [
    ("ADD", 7, -9, -2), ("SUB", 3, 5, -2), ("AND", 12, 10, 8), ("OR", 12, 10, 14),
    ("XOR", 12, 10, 6), ("SHL", 1, 33, 2), ("SHR", -1, 28, 15), ("SRA", -16, 2, -4),
    ("MIN", -5, 3, -5), ("MAX", -5, 3, 3),
])
def test_alu(op, a, b, out):
    r = run(f"#{a} -> alu.a\n#{op} -> alu.op\n#{b} -> alu.t_b\nalu.out -> r1\n#0 -> trap.t_halt")
    assert r.sim.regs[1] == out & 0xFFFFFFFF


def test_mul_and_cmp():
    r = run("""
        #-3         -> mul.a
        #5          -> mul.t_b
        #-1         -> cmp.a
        mul.out     -> r1
        #1          -> cmp.t_b
        cmp.eq      -> r2
        cmp.lt      -> r3
        cmp.ltu     -> r4
        #0          -> trap.t_halt
""")
    assert r.sim.regs[1:5] == [(-15) & 0xFFFFFFFF, 0, 1, 0]


def test_memory_store_then_load_next_cycle():
    r = run("""
        #77         -> mem.data_in
        #9          -> mem.t_store
        #9          -> mem.t_load
        mem.data_out -> r1
        #tbl        -> mem.t_load
        mem.data_out -> r2
        #0          -> trap.t_halt
        .data
tbl:    .word -2
""")
    assert r.sim.regs[1] == 77 and r.sim.regs[2] == 0xFFFFFFFE


def test_call_and_return():
    r = run("""
        #f          -> pc.t_call
        #5          -> r2
        #0          -> trap.t_halt
.func f
        pc.link     -> r1
        pc.link     -> pc.t_jump
.endfunc
""")
    calls = moves(r)
    assert r.sim.regs[1] == 1 and r.sim.regs[2] == 5
    # call at R, f runs at R+3 and R+4, the move after the call at R+7
    assert [m[0] for m in calls] == [R, R + 3, R + 4, R + 7, R + 8]


def test_call_return_min_cycles_is_the_true_lower_bound():
    """The shortest callee is a single move (the return itself), so the move
    after a call can start 2(1 + P) cycles after it, not 2(1 + P) + 1.
    cfg.CALL_RETURN_MIN_CYCLES must equal this; a larger value would make S1
    accept reads that come too early."""
    from host.common.cfg import CALL_RETURN_MIN_CYCLES
    r = run("#f -> pc.t_call\n#1 -> r1\n#0 -> trap.t_halt\n.func f\npc.link -> pc.t_jump\n.endfunc")
    call, ret, after = [m[0] for m in moves(r)][:3]
    assert (ret - call, after - ret) == (1 + spec.P, 1 + spec.P)
    assert after - call == CALL_RETURN_MIN_CYCLES == 2 * (1 + spec.P)


def test_telemetry_drops_are_deterministic():
    src = "".join(f"#{i} -> telem.t_push\n" for i in range(70))
    r = run(src + "telem.drops -> r2\n#0 -> trap.t_halt")
    assert r.sim.regs[2] == 70 - spec.TELEM_FIFO_WORDS - 1    # one word already left for the UART
    assert len(r.sim.telem.accepted) == spec.TELEM_FIFO_WORDS + 1


def test_stimulus_drives_io_encoder():
    stim = Stimulus.parse("0 io.encoder 5\n4 io.encoder -3\n")
    r = run("io.encoder -> r1\nio.encoder -> r2\nio.encoder -> r3\n#0 -> trap.t_halt", stim=stim)
    assert r.sim.regs[1:4] == [5, 5, (-3) & 0xFFFFFFFF]


def test_stimulus_drives_io_din_from_the_start_of_its_cycle():
    # isa.md sec. 3: a read at cycle r sees the value of the latest line with cycle <= r
    stim = Stimulus.parse("3 io.din 7\n4 io.din 9\n")
    r = run("io.din -> r1\nio.din -> r2\nio.din -> r3\nio.din -> r4\n#0 -> trap.t_halt", stim=stim)
    assert r.sim.regs[1:5] == [0, 7, 9, 9]                 # reads at cycles 2, 3, 4, 5


def test_io_din_and_io_encoder_have_their_own_cycle_order():
    stim = Stimulus.parse("10 io.encoder 1\n0 io.din 5\n20 io.encoder 2\n")
    assert stim.value("io.din", 0) == 5 and stim.value("io.encoder", 15) == 1


# a periodic task that samples io.din at anchor + 2 and actuates it at anchor + 101
DIN_PERIOD, DIN_ACT = 1000, 100
DIN_EDGE = f""".equ PERIOD, {DIN_PERIOD}
        #0          -> tmr.t_sync
loop:   #PERIOD     -> tmr.t_advance
        io.encoder  -> r1
        io.din      -> r2
        tmr.elapsed -> r14
        #{DIN_ACT}  -> tmr.t_wait
        r2          -> io.pwm_cmd
        tmr.elapsed -> r15
        r2          -> telem.t_push
        r14         -> telem.t_push
        r15         -> telem.t_push
        #loop       -> pc.t_jump
"""
DIN_ANCHOR = [R + (k + 1) * DIN_PERIOD for k in range(8)]       # t_sync executes at R
DIN_CHANGES = [(DIN_ANCHOR[1] + 2, 11), (DIN_ANCHOR[2] + 3, 22),  # on the sample cycle / one after it
               (DIN_ANCHOR[4] + 2, 33), (DIN_ANCHOR[4] + 600, 44)]
DIN_STIM = "".join(f"{c} io.din {v}\n" for c, v in DIN_CHANGES)


def test_io_din_sampling_edge_and_input_to_actuation_latency():
    r = run(DIN_EDGE, stim=Stimulus.parse(DIN_STIM), max_cycles=DIN_ANCHOR[7])
    w = [x for _, x in r.sim.telem.accepted]
    recs = [w[i:i + 3] for i in range(0, len(w) - 2, 3)]
    assert [x[0] for x in recs] == [0, 11, 11, 22, 33, 44, 44]      # value used by each period
    assert {(x[1], x[2]) for x in recs} == {(3, DIN_ACT + 2)}       # el_in, el_act
    s = 2
    for c, v in DIN_CHANGES:
        k = next(k for k, a in enumerate(DIN_ANCHOR) if a + s >= c)
        write = next(cy for cy, val in r.sim.pwm_log if val == v)
        assert write == DIN_ANCHOR[k] + DIN_ACT + 1
        assert DIN_ACT + 1 - s <= write - c <= DIN_ACT + 1 - s + DIN_PERIOD - 1


def din_pulse(start, hold, value=5):
    """io.din = value for exactly `hold` cycles from `start`, 0 otherwise."""
    return f"{start} io.din {value}\n{start + hold} io.din 0\n"


# start of the pulse relative to the sample cycle anchor + 2 of period 3
DIN_PHASES = [-1, 0, 1, DIN_PERIOD // 2, DIN_PERIOD - 1]


@pytest.mark.parametrize("phase", DIN_PHASES)
def test_io_din_held_exactly_one_period_is_used_by_exactly_one_period(phase):
    start = DIN_ANCHOR[3] + 2 + phase
    r = run(DIN_EDGE, stim=Stimulus.parse(din_pulse(start, DIN_PERIOD)), max_cycles=DIN_ANCHOR[7])
    w = [x for _, x in r.sim.telem.accepted]
    assert [w[i] for i in range(0, len(w), 3)].count(5) == 1


def test_io_din_held_one_cycle_less_than_a_period_can_be_missed():
    # from one cycle after a sample to the cycle before the next one
    start = DIN_ANCHOR[3] + 2 + 1
    r = run(DIN_EDGE, stim=Stimulus.parse(din_pulse(start, DIN_PERIOD - 1)), max_cycles=DIN_ANCHOR[7])
    w = [x for _, x in r.sim.telem.accepted]
    assert 5 not in [w[i] for i in range(0, len(w), 3)]


def test_writing_io_din_is_an_illegal_port():
    r = run("#h -> trap.handler\n.illegal 0x9280_0005\n" + HANDLER)   # #5 -> io.din
    assert r.sim.regs[1] == spec.TrapCause.ILLEGAL_PORT


@pytest.mark.parametrize("text, fragment", [
    ("100 io.encoder 1\n50 io.encoder 2\n", "stim line 2: io.encoder cycle 50 is not after"),
    ("100 io.encoder 1\n100 io.encoder 2\n", "stim line 2: io.encoder cycle 100 is not after"),
    ("-1 io.encoder 1\n", "stim line 1: negative cycle"),
    ("0 io.pwm_cmd 1\n", "stim line 1: expected"),          # not an input port
    ("0 io.encoder 0x10\n", "decimal integers"),
])
def test_malformed_stimulus_is_rejected(text, fragment):
    with pytest.raises(StimulusError, match=fragment):
        Stimulus.parse(text)


def _image():
    prog = assemble(EXAMPLE_1, "p.tta")
    return list(prog.code), list(prog.data), json.loads(output_files(prog)[".json"])


@pytest.mark.parametrize("damage, fragment", [
    (lambda c, d, m: m.update(code_len=5000), "code_len 5000"),
    (lambda c, d, m: m.update(data_len=-1), "data_len -1"),
    (lambda c, d, m: c.__setitem__(20, 1), "non-zero code word after"),
    (lambda c, d, m: d.__setitem__(0, 1), "non-zero data word after"),
    (lambda c, d, m: c.__setitem__(3, 0), "zero code word before"),
    (lambda c, d, m: m["functions"][0].update(end=99), "function main"),
    (lambda c, d, m: m["functions"].append({"name": "x", "start": 2, "end": 3}), "function x"),
])
def test_damaged_images_are_refused(damage, fragment):
    code, data, meta = _image()
    check_invariants(code, data, meta)                      # the clean image passes
    damage(code, data, meta)
    with pytest.raises(ImageError, match=fragment):
        check_invariants(code, data, meta)


def test_max_cycles_ends_with_E():
    r = run("loop: #loop -> pc.t_jump", max_cycles=50)
    assert r.trace[-1] == "E 50 maxcycles"


def test_load_checks_spec(tmp_path):
    prog = assemble(EXAMPLE_1, "p.tta")
    files = output_files(prog)
    for suffix, text in files.items():
        (tmp_path / f"p{suffix}").write_text(text)
    code, data, meta = load(tmp_path / "p")
    assert code == prog.code
    meta["spec"] = "something-else"
    (tmp_path / "p.json").write_text(json.dumps(meta))
    with pytest.raises(ImageError, match="spec"):
        load(tmp_path / "p")
