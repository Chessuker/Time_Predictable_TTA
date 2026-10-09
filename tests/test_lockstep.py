"""RTL vs ISS lockstep (Phase 2 Done criterion): every trace must match exactly.

Needs Verilator from oss-cad-suite in the Ubuntu-24.04 WSL distro; the whole
module is skipped when that is not available.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from .test_asm import EXAMPLE_1
from .test_iss import (A0, DEADLINE_EDGE, EPC_ILLEGAL, EPC_IN_JUMP, FIFO_EDGE, HANDLER, IF_ELSE, LOOP,
                       POP_2, TIMING_EDGE, control)

ROOT = Path(__file__).resolve().parents[1]


def _verilator_available():
    if shutil.which("wsl") is None:
        return False
    try:
        r = subprocess.run(["wsl", "-d", "Ubuntu-24.04", "-e", "bash", "-c",
                            "source ~/oss-cad-suite/environment >/dev/null 2>&1; command -v verilator"],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


pytestmark = pytest.mark.skipif(not _verilator_available(),
                                reason="Verilator (oss-cad-suite in WSL Ubuntu-24.04) not available")

ALU = "#{a} -> alu.a\n#{op} -> alu.op\n#{b} -> alu.t_b\nalu.out -> r1\nr1 -> telem.t_push\n#0 -> trap.t_halt"

CASES = {
    # timing_model.md sec. 5
    "ex1": (EXAMPLE_1, None, 1_000),
    "ex2a_n1": (LOOP.format(n=1), None, 1_000),
    "ex2a_n16": (LOOP.format(n=16), None, 1_000),
    "ex2b_then": (IF_ELSE.format(x=7), None, 1_000),
    "ex2b_else": (IF_ELSE.format(x=0), None, 1_000),
    "ex3_on_time": (control(623), "0 io.encoder 0\n150000 io.encoder 42\n", A0 + 3 * 100_000 + 2_000),
    "ex3_late": (control(1200), None, A0 + 2 * 100_000 + 3_000),
    "ex3_trap": (control(2100), None, A0 + 2 * 100_000),
    # timer details
    "late_flag": ("""
        #0          -> tmr.t_sync
        #10         -> tmr.t_wait
        tmr.flags   -> r1
        tmr.elapsed -> r2
        #1          -> tmr.t_wait
        tmr.flags   -> r3
        r1          -> telem.t_push
        r2          -> telem.t_push
        r3          -> telem.t_push
        #0          -> trap.t_halt
""", None, 1_000),
    "trap_in_wait": ("""
        #h          -> trap.handler
        #0          -> tmr.t_sync
        #20         -> tmr.t_arm
        #100        -> tmr.t_wait
after:  #0          -> trap.t_halt
""" + HANDLER, None, 1_000),
    "past_deadline": ("#0 -> tmr.t_sync\nnop\n#0 -> tmr.t_arm\nnop\n#0 -> trap.t_halt", None, 1_000),
    "clear_in_time": ("#0 -> tmr.t_sync\n#6 -> tmr.t_arm\n" + "nop\n" * 3 + "#0 -> tmr.t_clear\n#0 -> trap.t_halt", None, 1_000),
    "clear_too_late": ("#0 -> tmr.t_sync\n#6 -> tmr.t_arm\n" + "nop\n" * 4 + "#0 -> tmr.t_clear\n#0 -> trap.t_halt", None, 1_000),
    "deadline_beats_illegal": ("""
        #h          -> trap.handler
        #0          -> tmr.t_sync
        #3          -> tmr.t_arm
        nop
        .illegal 0x0F80_0000
""" + HANDLER, None, 1_000),
    # traps
    "cause2": ("#h -> trap.handler\n.illegal 0x0F80_0000\n" + HANDLER, None, 1_000),
    "cause3": ("#h -> trap.handler\n.illegal 0x2180_000C\n" + HANDLER, None, 1_000),
    "cause4": ("#h -> trap.handler\n#5000 -> r3\nr3 -> mem.t_load\n#0 -> trap.t_halt\n" + HANDLER, None, 1_000),
    "cause5": ("#h -> trap.handler\n.illegal 0x5180_1388\n" + HANDLER, None, 1_000),
    "nohandler": (".illegal 0x0F80_0000", None, 1_000),
    "double": ("#h -> trap.handler\n.illegal 0x0F80_0000\n.func h\n.illegal 0x0F80_0001\n.endfunc", None, 1_000),
    "not_taken_bad_target": ("#0 -> pc.cond\n.illegal 0x5380_1388\n.func h\n#0 -> trap.t_halt\n.endfunc", None, 1_000),
    # function units
    "mul_cmp": ("""
        #-3         -> mul.a
        #5          -> mul.t_b
        #-1         -> cmp.a
        mul.out     -> r1
        #1          -> cmp.t_b
        cmp.eq      -> r2
        cmp.lt      -> r3
        cmp.ltu     -> r4
        #0          -> trap.t_halt
""", None, 1_000),
    "memory": ("""
        #77         -> mem.data_in
        #9          -> mem.t_store
        #9          -> mem.t_load
        mem.data_out -> r1
        #tbl        -> mem.t_load
        mem.data_out -> r2
        #0          -> trap.t_halt
        .data
tbl:    .word -2
""", None, 1_000),
    "call_min": ("#f -> pc.t_call\n#1 -> r1\n#0 -> trap.t_halt\n.func f\npc.link -> pc.t_jump\n.endfunc", None, 1_000),
    "telem_drops": ("".join(f"#{i} -> telem.t_push\n" for i in range(70))
                    + "telem.drops -> r2\nr2 -> telem.t_push\n#0 -> trap.t_halt", None, 1_000),
    "stim": ("io.encoder -> r1\nio.encoder -> r2\nio.encoder -> r3\n#0 -> trap.t_halt",
             "0 io.encoder 5\n4 io.encoder -3\n", 1_000),
    "maxcycles": ("loop: #loop -> pc.t_jump", None, 50),
}
for _op, _a, _b in [("ADD", 7, -9), ("SUB", 3, 5), ("AND", 12, 10), ("OR", 12, 10), ("XOR", 12, 10),
                    ("SHL", 1, 33), ("SHR", -1, 28), ("SRA", -16, 2), ("MIN", -5, 3), ("MAX", -5, 3)]:
    CASES[f"alu_{_op.lower()}"] = (ALU.format(op=_op, a=_a, b=_b), None, 1_000)
# exact boundaries from the PR #3 review (expected values are in test_iss.py)
for _op in ("t_wait", "t_advance"):
    for _v in (2, 1, 0):
        CASES[f"edge_{_op}_v{_v}"] = (TIMING_EDGE.format(op=_op, v=_v), None, 1_000)
for _v in range(4):
    CASES[f"edge_arm_v{_v}"] = (DEADLINE_EDGE.format(v=_v), None, 1_000)
for _v in range(2, 6):
    CASES[f"epc_jump_v{_v}"] = (EPC_IN_JUMP.format(v=_v), None, 1_000)
CASES["epc_illegal"] = (EPC_ILLEGAL, None, 1_000)
for _v in (POP_2 - 2, POP_2 - 1, POP_2):
    CASES[f"fifo_full_v{_v}"] = (FIFO_EDGE.format(v=_v), None, 5_000)
for _p in sorted((ROOT / "programs").glob("*.tta")):
    CASES[f"prog_{_p.stem}"] = (_p.read_text(encoding="utf-8"), None, 1_000_000)


@pytest.mark.parametrize("name", sorted(CASES))
def test_rtl_matches_iss(name):
    from host.lockstep import run
    source, stim, max_cycles = CASES[name]
    outcome = run(source, name, stim, max_cycles, checks=True)
    assert outcome.ok, outcome.mismatch
    assert outcome.iss[-1][0] in "HE"                 # the run reached a proper end
