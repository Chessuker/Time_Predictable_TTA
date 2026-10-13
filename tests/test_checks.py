"""CFG static checks S1, S3, S4, S5 (timing_model.md §4)."""
from pathlib import Path

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


# ---------------------------------------------------------------- S5 returns
# A return is 'pc.link -> pc.t_jump'. pc.link is writable, so the check must
# make sure it still holds the function's own return address (audit A2-1).
def return_errors(src):
    return [(line, m) for line, m in errors_of(src) if m.startswith("return in ")]


SAVE_IN_MEMORY = """
        #f          -> pc.t_call
        #0          -> trap.t_halt
.func f
        pc.link     -> mem.data_in
        #7          -> mem.t_store
        #g          -> pc.t_call
        #7          -> mem.t_load
        mem.data_out -> r3
        r3          -> pc.link
        pc.link     -> pc.t_jump
.endfunc
.func g
        #1          -> r3
        pc.link     -> pc.t_jump
.endfunc
"""

BRANCHES_KEEP_THE_COPY = """
        #f          -> pc.t_call
        #0          -> trap.t_halt
.func f
        pc.link     -> r15
        r1          -> pc.cond
        #other      -> pc.t_jz
        r15         -> r14
        r14         -> pc.link
        pc.link     -> pc.t_jump
other:  #g          -> pc.t_call
        r15         -> pc.link
        pc.link     -> pc.t_jump
.endfunc
.func g
        #5          -> r14
        pc.link     -> pc.t_jump
.endfunc
"""


@pytest.mark.parametrize("src", [SAVE_IN_MEMORY, BRANCHES_KEEP_THE_COPY])
def test_s5_return_after_restoring_a_copy_passes(src):
    assemble(src, "t.tta")


def test_s5_programs_that_save_pc_link_pass():
    for name in ("call_return", "wcet_bench", "control", "control_sw", "control_trap"):
        path = Path(__file__).parent.parent / "programs" / f"{name}.tta"
        assemble(path.read_text(encoding="utf-8"), path.name)


def callee(body, extra=""):
    return f"""
        #f          -> pc.t_call
        #0          -> trap.t_halt
.func f
{body}
        pc.link     -> pc.t_jump
.endfunc
{extra}"""


G_WRITES_R15 = ".func g\n#1 -> r15\npc.link -> pc.t_jump\n.endfunc"
G_CALLS_H = (".func g\npc.link -> r1\n#h -> pc.t_call\nr1 -> pc.link\npc.link -> pc.t_jump\n.endfunc\n"
             ".func h\n#1 -> r15\npc.link -> pc.t_jump\n.endfunc")


@pytest.mark.parametrize("body, extra", [
    ("        #far -> pc.link", ""),                                    # audit A2-1: jump anywhere
    ("        trap.epc -> pc.link", ""),                                # S7 through pc.link
    ("        trap.epc -> r2\n        r2 -> pc.link", ""),
    ("        r1 -> pc.link", ""),                                      # r1 never held it
    ("        #g -> pc.t_call", G_WRITES_R15),                          # call without saving
    ("        pc.link -> r15\n        #g -> pc.t_call\n        r15 -> pc.link", G_WRITES_R15),
    ("        pc.link -> r15\n        #g -> pc.t_call\n        r15 -> pc.link", G_CALLS_H),
    ("        pc.link -> alu.a\n        #0 -> alu.op\n        #0 -> alu.t_b\n"
     "        alu.out -> pc.link", ""),                                 # computed, not a copy
    ("        pc.link -> r15\n        r1 -> pc.cond\n        #skip -> pc.t_jz\n"
     "        #9 -> r15\nskip:   r15 -> pc.link", ""),                   # lost on one path
    ("        pc.link -> mem.data_in\n        #7 -> mem.t_store\n        #g -> pc.t_call\n"
     "        #7 -> mem.t_load\n        mem.data_out -> pc.link",
     ".func g\n#0 -> mem.data_in\n#7 -> mem.t_store\npc.link -> pc.t_jump\n.endfunc"),
    ("        pc.link -> mem.data_in\n        #7 -> mem.t_store\n        #3 -> r4\n"
     "        #0 -> mem.data_in\n        r4 -> mem.t_store\n        #7 -> mem.t_load\n"
     "        mem.data_out -> pc.link", ""),                           # store to an unknown address
    ("        pc.link -> mem.data_in\n        #7 -> mem.t_store\n"
     "        #8 -> mem.t_load\n        mem.data_out -> pc.link", ""),     # other address
])
def test_s5_return_without_its_return_address_is_rejected(body, extra):
    src = callee(body + "\nfar:    nop", extra) if "#far" in body else callee(body, extra)
    errs = return_errors(src)
    assert len(errs) == 1, errors_of(src)
    assert "return in f" in errs[0][1] and errs[0][1].endswith("(S5)")


def test_s5_audit_a2_1_probe_never_reaches_the_wcet_tool():
    src = """
        #1          -> r1
        #f          -> pc.t_call
        #0          -> trap.t_halt
.func f
        #far        -> pc.link
        pc.link     -> pc.t_jump
far:    #0          -> trap.t_halt
.endfunc
"""
    assert return_errors(src) == [(7, "return in f, but pc.link may hold something other than "
                                      "its return address here; pc.link may only be restored "
                                      "from a copy of itself (S5)")]


def test_s5_no_return_from_a_trap_handler():
    errs = return_errors("""
        #h          -> trap.handler
        .illegal 0x0F80_0000
.func h
        pc.link     -> pc.t_jump
.endfunc
""")
    assert errs == [(5, "return in h, but it is entered by a trap, which sets no return "
                        "address; pc.link may only be restored from a copy of itself (S5)")]


def test_s5_handler_set_from_a_register_makes_every_return_unprovable():
    errs = return_errors("""
        #f          -> r1
        r1          -> trap.handler
        #f          -> pc.t_call
        #0          -> trap.t_halt
.func f
        pc.link     -> pc.t_jump
.endfunc
""")
    assert [line for line, _ in errs] == [7] and "entered by a trap" in errs[0][1]
