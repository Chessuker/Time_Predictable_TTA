import json

import pytest

from host.asm import assemble, output_files
from host.asm.disasm import ImageError, disassemble

from .test_asm import CONTROL, EXAMPLE_1

CALLS = """
.equ N, 3
        #on_trap    -> trap.handler
        #N          -> r1
@task main_loop
top:
again:  #work       -> pc.t_call
        #MIN        -> alu.op
        r1          -> pc.cond
        #top        -> pc.t_jnz
        #on_trap    -> pc.t_jump

@loop_bound 2
.func work
        pc.link     -> r15
        #leaf       -> pc.t_call
        r15         -> pc.link
inner:  pc.link     -> pc.t_jump
.endfunc

.func leaf
        nop
        pc.link     -> pc.t_jump
.endfunc

.func on_trap
        #0          -> trap.t_halt
.endfunc

        .data
table:  .word -1, 0x7fff_ffff, 0
pad:    .space 2
end:
"""

ILLEGAL = """
        #on_trap    -> trap.handler
        .illegal 0x2180_000C
.func on_trap
        .illegal 0x0F80_0000
.endfunc
"""

PROGRAMS = {"example_1": EXAMPLE_1, "control": CONTROL, "calls": CALLS, "illegal": ILLEGAL}


def roundtrip(src):
    prog = assemble(src, "p.tta")
    meta = json.loads(output_files(prog)[".json"])
    dis = disassemble(prog.code, prog.data, meta)
    again = assemble(dis.text, "p.tta")
    return prog, again, dis


@pytest.mark.parametrize("name", PROGRAMS)
def test_roundtrip_is_binary_identical(name):
    prog, again, dis = roundtrip(PROGRAMS[name])
    assert dis.warnings == []
    assert again.code == prog.code
    assert again.data == prog.data


def test_roundtrip_keeps_functions_and_annotations():
    prog, again, _ = roundtrip(CALLS)
    assert again.functions == prog.functions
    assert again.annotations == prog.annotations


def test_rendering_uses_names():
    _, _, dis = roundtrip(CALLS)
    text = dis.text
    assert "#work        -> pc.t_call" in text
    assert "#on_trap     -> trap.handler" in text
    assert "#MIN         -> alu.op" in text
    assert "        nop" in text
    assert ".func leaf" in text and ".endfunc" in text
    assert ".word 0xffffffff" in text


def test_illegal_words_stay_illegal():
    _, _, dis = roundtrip(ILLEGAL)
    assert ".illegal 0x2180000c" in dis.text
    assert ".illegal 0x0f800000" in dis.text


def test_without_meta_trims_trailing_zeros():
    prog = assemble(CALLS, "p.tta")
    dis = disassemble(prog.code, prog.data)
    code_lines = [l for l in dis.text.splitlines() if l.startswith("        ") and "." != l.strip()[0]]
    assert len(code_lines) == prog.code_len
    assert dis.text.count(".word") == 2            # -1, 0x7fffffff; the zeros are trimmed
    assert "#7           -> pc.t_call" in dis.text       # numeric targets, no labels
    assert ".func" not in dis.text and "@" not in dis.text


def test_damaged_image_is_reported():
    prog = assemble(EXAMPLE_1, "p.tta")
    meta = json.loads(output_files(prog)[".json"])
    code = list(prog.code)
    code[-1] = 0x11800001
    assert disassemble(code, prog.data, meta).warnings
    with pytest.raises(ImageError):
        disassemble(code[:10], prog.data, meta)
