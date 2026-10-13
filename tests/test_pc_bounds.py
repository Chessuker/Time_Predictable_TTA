"""PC bounds at the end of code memory (audit A1-1; isa.md sec. 2, 6, 8, 9).

Code memory holds IMEM_WORDS words at addresses 0 .. IMEM_WORDS - 1; the last
word is an ordinary word. Two rules cover every way a PC can leave it:
- a taken jump, call or return whose target is >= IMEM_WORDS traps with
  cause 5 at that move (isa.md sec. 6); a jump that is not taken never traps;
- a move fetched from an address >= IMEM_WORDS reads as word 0, which is
  always illegal (isa.md sec. 2): cause 2, trap.epc = that address. This is
  how execution runs off the last word, or a trap enters a handler address
  outside code memory (the trap is then a double fault and the core halts).
The PC is never wrapped or clamped. Before the fix the RTL fetched
fetch_pc mod IMEM_WORDS and so ran code from the bottom of memory instead.

The ISS cases pin the behaviour; the lockstep cases check the RTL against it.
"""
import pytest

from host.asm import assemble
from host.common import spec
from host.iss import Simulator

from .test_iss import run
from .test_lockstep import _verilator_available

N = spec.IMEM_WORDS
NOP = "nop\n"

# at N - 1 a move the CPU executes but the assembler only takes raw: #0 -> trap.handler
FALL_OFF_END = f"#last -> pc.t_jump\n{NOP * (N - 2)}last: .illegal 0x8080_0000\n"
JUMP_TO_LAST = f"#last -> pc.t_jump\n{NOP * (N - 2)}last: #0 -> trap.t_halt\n"
HANDLER_LAST = (f"#h -> trap.handler\n{NOP * (N - 3)}.illegal 0x0F80_0000\n"
                ".func h\n#0 -> trap.t_halt\n.endfunc\n")                        # h at N - 1
HANDLER_OUT = "#{h} -> r1\nr1 -> trap.handler\n.illegal 0x0F80_0000\n"
RAW = {                                       # dst << 24 | imm bit | target, written raw (no S-checks)
    "jump": 0x5180_0000, "jz": 0x5280_0000, "jnz": 0x5380_0000, "call": 0x5480_0000,
}


def raw(op, target):
    return f".illegal 0x{RAW[op] | target:08X}"


CASES = {
    "pc_fall_off_end": FALL_OFF_END,
    "pc_jump_to_last": JUMP_TO_LAST,
    "pc_handler_last": HANDLER_LAST,
    "pc_handler_n": HANDLER_OUT.format(h=N),
    "pc_handler_max": HANDLER_OUT.format(h=-1),                  # 0xFFFF_FFFF
    "pc_jump_n": raw("jump", N),
    "pc_call_n": "#5 -> pc.link\n" + raw("call", N) + "\n",
    "pc_return_n": f"#{N} -> pc.link\n.illegal 0x5100_0055\n",   # pc.link -> pc.t_jump
    "pc_jz_taken_n": "#0 -> pc.cond\n" + raw("jz", N) + "\n",
    "pc_jnz_not_taken_n": "#0 -> pc.cond\n" + raw("jnz", N) + "\n#0 -> trap.t_halt\n",
}


def traps(result):
    return [(int(f[2], 16), int(f[3])) for f in (line.split() for line in result.trace) if f[0] == "T"]


# ---------------------------------------------------------------- the specified behaviour (ISS)
def test_the_last_word_is_ordinary_and_the_next_fetch_is_illegal():
    r = run(FALL_OFF_END)
    pcs = [int(line.split()[2], 16) for line in r.trace if line.startswith("M ")]
    assert pcs == [0, N - 1]                               # the jump, then the move at N - 1
    assert traps(r) == [(N, 2)] and r.end == "nohandler"   # fetch at N: word 0, not address 0


def test_a_jump_to_the_last_word_is_legal():
    r = run(JUMP_TO_LAST)
    assert traps(r) == [] and r.end == "halt"


def test_a_handler_at_the_last_word_runs():
    prog = assemble(HANDLER_LAST, "t.tta")
    assert prog.symbols["h"].value == N - 1
    r = run(HANDLER_LAST)
    assert traps(r) == [(N - 2, 2)] and r.end == "halt"


@pytest.mark.parametrize("h", [N, 2**32 - 1])
def test_a_handler_outside_code_memory_is_a_double_fault(h):
    r = run(HANDLER_OUT.format(h=h if h < 2**31 else -1))
    assert traps(r) == [(2, 2), (h, 2)] and r.end == "double"


@pytest.mark.parametrize("case, epc", [("pc_jump_n", 0), ("pc_call_n", 1), ("pc_return_n", 1),
                                       ("pc_jz_taken_n", 1)])
def test_a_taken_transfer_to_n_is_cause_5_at_the_move(case, epc):
    r = run(CASES[case])
    assert traps(r) == [(epc, 5)] and r.end == "nohandler"


def test_a_call_that_traps_leaves_pc_link_alone():
    r = run(CASES["pc_call_n"])
    assert r.sim.link == 5


def test_a_jump_not_taken_to_n_does_not_trap():
    r = run(CASES["pc_jnz_not_taken_n"])
    assert traps(r) == [] and r.end == "halt"


def test_the_iss_bounds_fetches_by_the_memory_size_not_the_image_length():
    # an image longer than the memory must not be executed past IMEM_WORDS
    word_halt = assemble("#0 -> trap.t_halt", "t.tta").code[0]
    code = [0xFF80_0000] * 3 + [word_halt]            # nop nop nop halt
    r = Simulator(code, imem_words=3).run(1_000)
    assert traps(r) == [(3, 2)] and r.end == "nohandler"


# ---------------------------------------------------------------- RTL against the ISS
@pytest.mark.skipif(not _verilator_available(), reason="Verilator (oss-cad-suite in WSL Ubuntu-24.04) not available")
@pytest.mark.parametrize("name", sorted(CASES))
def test_rtl_matches_iss_at_the_bounds(name):
    from host.lockstep import run as lockstep
    outcome = lockstep(CASES[name], name, None, 10_000, checks=False)
    assert outcome.ok, outcome.mismatch
    assert outcome.iss[-1][0] == "H"
