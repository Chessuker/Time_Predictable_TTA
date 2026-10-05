import json

import pytest

from host.asm import AssemblyFailed, assemble, output_files
from host.common import spec
from host.common.encoding import Move, encode

P = spec.PORT_BY_NAME


def imm(dst, v):
    return encode(Move(dst=P[dst].id, imm=True, value=v))


def mov(src, dst):
    return encode(Move(dst=P[dst].id, imm=False, src=P[src].id))


def errors_of(src):
    with pytest.raises(AssemblyFailed) as e:
        assemble(src, "t.tta")
    return [x.msg for x in e.value.errors]


def assert_error(src, fragment):
    msgs = errors_of(src)
    assert any(fragment in m for m in msgs), msgs


# timing_model.md §5 example 1, ending with a halt
EXAMPLE_1 = """
        #5          -> r1
        #7          -> r2
        #ADD        -> alu.op
        r1          -> alu.a
        r2          -> alu.t_b
        alu.out     -> mul.a
        #3          -> mul.t_b
        #10         -> r4
        mul.out     -> r3
        #0          -> trap.t_halt
"""

CONTROL = """
.equ PERIOD, 100_000
.equ V_ACT,  1_000
.equ DL,     2_000

        .data
gains:  .word 186_659, 1_501_851, 6_766

        .text
        #on_trap    -> trap.handler
        #0          -> tmr.t_sync
@task control
loop:   #PERIOD     -> tmr.t_advance
        io.encoder  -> r1
        #DL         -> tmr.t_arm
        #gains      -> mem.t_load
        mem.data_out -> r2
        #V_ACT      -> tmr.t_wait
        r5          -> io.pwm_cmd
        #0          -> tmr.t_clear
        #loop       -> pc.t_jump

.func on_trap
        #0          -> io.pwm_cmd
        trap.cause  -> telem.t_push
        trap.epc    -> telem.t_push
        #0          -> trap.t_halt
.endfunc
"""


def test_example_1_words():
    prog = assemble(EXAMPLE_1, "ex1.tta")
    assert prog.code[:prog.code_len] == [
        imm("r1", 5), imm("r2", 7), imm("alu.op", 0), mov("r1", "alu.a"), mov("r2", "alu.t_b"),
        mov("alu.out", "mul.a"), imm("mul.t_b", 3), imm("r4", 10), mov("mul.out", "r3"),
        imm("trap.t_halt", 0)]
    assert len(prog.code) == spec.IMEM_WORDS and set(prog.code[prog.code_len:]) == {0}
    assert all(prog.code[:prog.code_len])


def test_control_program_metadata():
    prog = assemble(CONTROL, "ctrl.tta")
    meta = json.loads(output_files(prog)[".json"])
    assert meta["spec"] == spec.SPEC_ID and meta["format"] == 1
    assert meta["code_len"] == 15 and meta["data_len"] == 3
    assert meta["symbols"]["loop"] == {"kind": "label", "section": "text", "value": 2}
    assert meta["symbols"]["gains"] == {"kind": "label", "section": "data", "value": 0}
    assert meta["symbols"]["PERIOD"] == {"kind": "equ", "value": 100000}
    assert meta["symbols"]["on_trap"]["kind"] == "func"
    assert meta["functions"] == [{"name": "main", "start": 0, "end": 10},
                                 {"name": "on_trap", "start": 11, "end": 14}]
    assert meta["annotations"] == [{"kind": "task", "name": "control", "addr": 2}]
    assert [e["line"] for e in meta["source_map"]][:3] == [10, 11, 13]
    assert prog.code[0] == imm("trap.handler", 11)
    assert prog.data[:3] == [186_659, 1_501_851, 6_766]


def test_loop_bound_annotation():
    prog = assemble("""
        #4          -> r1
        #SUB        -> alu.op
@loop_bound 4
loop:   r1          -> alu.a
        #1          -> alu.t_b
        alu.out     -> r1
        r1          -> pc.cond
        #loop       -> pc.t_jnz
        #0          -> trap.t_halt
""")
    assert prog.annotations == [{"kind": "loop_bound", "addr": 2, "n": 4}]


def test_outputs_are_deterministic_and_path_independent():
    a = output_files(assemble(CONTROL, "ctrl.tta"))
    b = output_files(assemble(CONTROL, "./sub/ctrl.tta"))
    assert a == b
    assert a[".code.hex"].count("\n") == spec.IMEM_WORDS
    assert a[".data.hex"].startswith("0002d923\n0016ea9b\n00001a6e\n00000000\n")


def test_listing_shows_resolved_values():
    lst = output_files(assemble(CONTROL, "ctrl.tta"))[".lst"]
    assert "#PERIOD     -> tmr.t_advance  ; = 100000" in lst
    assert "D0000  0002d923" in lst and "\nD0002  00001a6e" in lst


def test_expressions_and_number_formats():
    prog = assemble("""
.equ A, 0x10 + 0b11 * 2
        #(A << 2) | 1  -> r1
        #-A            -> r2
        #~0            -> r3
        #0             -> trap.t_halt
""")
    assert prog.code[:3] == [imm("r1", (22 << 2) | 1), imm("r2", -22), imm("r3", -1)]


def test_illegal_directive():
    prog = assemble("""
        #on_trap    -> trap.handler
        .illegal 0x2180_000C
.func on_trap
        #0          -> trap.t_halt
.endfunc
""")
    assert prog.code[1] == 0x2180000C


@pytest.mark.parametrize("src, fragment", [
    ("loop: loop -> pc.t_jump", "immediates need '#'"),
    ("#1 -> alu.bogus\n#0 -> trap.t_halt", "unknown port 'alu.bogus'"),
    ("alu.a -> r1\n#0 -> trap.t_halt", "alu.a cannot be read"),
    ("r1 -> alu.out\n#0 -> trap.t_halt", "alu.out cannot be written"),
    ("#-1 -> tmr.t_wait\n#0 -> trap.t_halt", "(S2)"),
    ("#4_194_304 -> tmr.t_advance\n#0 -> trap.t_halt", "(S2)"),
    ("#12 -> alu.op\n#0 -> trap.t_halt", "alu.op takes 0..9"),
    ("#4096 -> mem.t_load\n#0 -> trap.t_halt", "outside data memory"),
    ("#4_194_304 -> r1\n#0 -> trap.t_halt", "does not fit in 23 bits"),
    ("a: #a + 1 -> pc.t_jump", "must be a single .text label"),
    ("#end -> pc.t_jump\nend:\n", "label 'end' points"),
    ("#f -> pc.t_call\n#0 -> trap.t_halt\nf: #0 -> trap.t_halt", "must be a .func name"),
    ("trap.epc -> pc.t_jump", "(S7)"),
    ("r1 -> pc.t_jump", "(S5)"),
    ("pc.link -> pc.t_jump", "outside a .func"),
    ("#1 -> r1\n.func f\n#0 -> trap.t_halt\n.endfunc", "falls through into .func f"),
    (".func f\n#1 -> r1\n.endfunc", "last move of .func f"),
    ("#1 -> r1", "last move of .text"),
    (".func f\n#0 -> trap.t_halt\n.endfunc\n#0 -> trap.t_halt", "only allowed before the first .func"),
    ("#0 -> trap.t_halt\n.illegal 0", "not allowed"),
    ("#0 -> trap.t_halt\n.illegal 0x1180_0005", "write it as '#5 -> r1'"),
    ("r1: #0 -> trap.t_halt", "reserved name"),
    ("a: #1 -> r1\na: #0 -> trap.t_halt", "already defined"),
    ("#X -> r1\n#0 -> trap.t_halt", "undefined symbol 'X'"),
    (".equ A, B\n.equ B, A\n#0 -> trap.t_halt", "refers to itself"),
    ("@loop_bound 3\n#0 -> trap.t_halt", "not followed by a label"),
    (".data\n#1 -> r1\n.text\n#0 -> trap.t_halt", "only allowed in .text"),
    (".word 1\n#0 -> trap.t_halt", "only allowed in .data"),
    ("", ".text is empty"),
])
def test_errors(src, fragment):
    assert_error(src, fragment)


def test_errors_report_file_line_and_collect_all():
    with pytest.raises(AssemblyFailed) as e:
        assemble("#12 -> alu.op\nloop -> pc.t_jump\n", "bad.tta")
    text = e.value.render()
    assert "bad.tta:1:" in text and "bad.tta:2:" in text
