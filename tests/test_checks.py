"""CFG static checks S1, S3, S4, S5 (timing_model.md §4)."""
import pytest

from host.asm import AssemblyFailed, assemble

from .test_asm import CONTROL, EXAMPLE_1
from .test_disasm import CALLS


def errors_of(src):
    with pytest.raises(AssemblyFailed) as e:
        assemble(src, "t.tta")
    return [(x.line, x.msg) for x in e.value.errors]


@pytest.mark.parametrize("src", [EXAMPLE_1, CONTROL, CALLS])
def test_good_programs_pass(src):
    assemble(src, "t.tta")


# ---------------------------------------------------------------- S1
def test_s1_read_right_after_trigger():
    errs = errors_of("#2 -> mul.a\n#3 -> mul.t_b\nmul.out -> r1\n#0 -> trap.t_halt")
    assert errs == [(3, "mul.out is read 1 cycle(s) after its trigger, needs 2 (S1)")]


def test_s1_one_move_between_is_enough():
    assemble("#2 -> mul.a\n#3 -> mul.t_b\nnop\nmul.out -> r1\n#0 -> trap.t_halt")


def test_s1_taken_jump_counts_its_bubbles():
    assemble("#3 -> mul.t_b\n#x -> pc.t_jump\nx: mul.out -> r1\n#0 -> trap.t_halt")


ONE_PATH = """
        #3          -> mul.t_b
        r1          -> pc.cond
        #j          -> pc.t_jnz
        {fill}
j:      mul.out     -> r2
        #0          -> trap.t_halt
"""


def test_s1_violation_on_one_path_only():
    # taken path: 4 cycles after the trigger; fall-through: 1 cycle after the retrigger
    errs = errors_of(ONE_PATH.format(fill="#5          -> mul.t_b"))
    assert errs == [(6, "mul.out is read 1 cycle(s) after its trigger, needs 2 (S1)")]
    assemble(ONE_PATH.format(fill="nop"))


def test_s1_loop_back_edge_counts_the_jump():
    assemble("""
loop:   mul.out     -> r1           ; 3 cycles after the trigger via the back edge
        #3          -> mul.t_b
        #loop       -> pc.t_jump
""")


# ---------------------------------------------------------------- S3
def test_s3_advance_while_armed():
    errs = errors_of("#0 -> tmr.t_sync\n#100 -> tmr.t_arm\n#10 -> tmr.t_advance\n#0 -> trap.t_halt")
    assert any("(S3)" in m for _, m in errs)


def test_s3_clear_only_on_one_path():
    errs = errors_of("""
        #0          -> tmr.t_sync
loop:   #1000       -> tmr.t_advance
        #500        -> tmr.t_arm
        r1          -> pc.cond
        #skip       -> pc.t_jz
        #0          -> tmr.t_clear
skip:   #loop       -> pc.t_jump
""")
    assert [l for l, m in errs if "(S3)" in m] == [3]


def test_s3_call_to_a_function_that_advances():
    errs = errors_of("""
        #0          -> tmr.t_sync
        #500        -> tmr.t_arm
        #tick       -> pc.t_call
        #0          -> tmr.t_clear
        #0          -> trap.t_halt
.func tick
        #10         -> tmr.t_advance
        pc.link     -> pc.t_jump
.endfunc
""")
    assert any("call to tick" in m for _, m in errs)


# ---------------------------------------------------------------- S4
def test_s4_return_while_armed():
    errs = errors_of("""
        #f          -> pc.t_call
        #0          -> tmr.t_clear
        #0          -> trap.t_halt
.func f
        #500        -> tmr.t_arm
        pc.link     -> pc.t_jump
.endfunc
""")
    msgs = [m for _, m in errs]
    assert any("may return with a deadline still armed (S4)" in m or "(S4)" in m for m in msgs)
    assert any("t_clear in main, which has no t_arm" in m for m in msgs)


# ---------------------------------------------------------------- S5
def test_s5_recursion():
    errs = errors_of("""
        #a          -> pc.t_call
        #0          -> trap.t_halt
.func a
        #b          -> pc.t_call
        pc.link     -> pc.t_jump
.endfunc
.func b
        #a          -> pc.t_call
        pc.link     -> pc.t_jump
.endfunc
""")
    assert any("recursion" in m and "(S5)" in m for _, m in errs)


def test_s5_jump_into_another_function():
    errs = errors_of("""
        #inside     -> pc.t_jump
.func f
        nop
inside: #0          -> trap.t_halt
.endfunc
""")
    assert errs == [(2, "jump from main into f (S5)")]
