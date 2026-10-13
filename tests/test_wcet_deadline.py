"""Deadlines and the WCET tool (audit A3-4; timing_model.md R6, sec. 3).

Run time is fully specified and already checked elsewhere: the trap comes at
the first cycle d with armed and d >= deadline, so t_clear must execute by
deadline - 1 (lockstep clear_in_time / clear_too_late, edge_arm_v*,
past_deadline, deadline_beats_illegal; formal A3). The WCET tool, though, has
no deadline check: timing_model.md sec. 3 defines a task-level WCET "from
wake to t_clear" but not what the tool must prove with it, and that is left
for a reviewed spec change. Until then the tool must not look as if it checked
deadlines: every segment that may run while a deadline is armed is marked
deadline_unchecked, the CLI says NOT checked, and the guarantee says so.
"""
import re
from pathlib import Path

import pytest

from host.asm import assemble
from host.wcet import analyse

from .test_iss import run
from .test_lockstep import _verilator_available

ROOT = Path(__file__).resolve().parents[1]
CONTROL = (ROOT / "programs" / "control.tta").read_text(encoding="utf-8")


def wcet(src):
    return analyse(assemble(src, "t.tta"))


def flags(rep, fn="main"):
    return {(s.start_kind, s.end_kind, s.via): s.deadline_unchecked for s in rep.functions[fn].segments}


def test_the_audit_case_is_flagged_and_really_traps():
    # control.tta with its deadline at anchor + 50: every budget passes, the ISS traps
    src = re.sub(r"#DEADLINE\s*-> tmr\.t_arm", "#50 -> tmr.t_arm", CONTROL)
    rep = wcet(src)
    seg = [s for s in rep.functions["main"].segments if (s.start_kind, s.end_kind) == ("t_advance", "t_wait")][0]
    assert seg.ok and seg.deadline_unchecked                 # W 90 <= 999, deadline not shown
    r = run(src, max_cycles=300_000)
    assert any(line.startswith("T ") and line.split()[3] == "1" for line in r.trace)   # cause 1


def test_control_flags_only_the_armed_span():
    assert flags(wcet(CONTROL)) == {("entry", "t_sync", None): False, ("t_sync", "t_advance", None): False,
                                    ("t_advance", "t_wait", None): True, ("t_wait", "t_advance", None): True}
    assert flags(wcet(CONTROL), "on_trap") == {("entry", "halt", None): False}


def test_no_t_arm_no_flag():
    rep = wcet("#0 -> tmr.t_sync\nloop: #100 -> tmr.t_advance\nnop\n#loop -> pc.t_jump")
    assert rep.unchecked_deadlines() == []


CALL_WHILE_ARMED = """
        #0          -> tmr.t_sync
        #500        -> tmr.t_arm
        #f          -> pc.t_call
        #0          -> tmr.t_clear
        #0          -> trap.t_halt
.func f
        nop
        nop
        pc.link     -> pc.t_jump
.endfunc
"""

CALLEE_ARMS = """
        #0          -> tmr.t_sync
        #f          -> pc.t_call
        #0          -> trap.t_halt
.func f
        #500        -> tmr.t_arm
        nop
        #0          -> tmr.t_clear
        pc.link     -> pc.t_jump
.endfunc
"""

HALT_WHILE_ARMED = """
        #0          -> tmr.t_sync
        #500        -> tmr.t_arm
        r1          -> pc.cond
        #stop       -> pc.t_jz
        #0          -> tmr.t_clear
stop:   #0          -> trap.t_halt
"""


def test_a_call_made_while_armed_flags_the_caller_not_the_callee():
    rep = wcet(CALL_WHILE_ARMED)
    assert flags(rep) == {("entry", "t_sync", None): False, ("t_sync", "halt", None): True}
    assert flags(rep, "f") == {("entry", "return", None): False}       # the deadline is the caller's


def test_a_callee_that_arms_flags_every_caller_segment_that_calls_it():
    rep = wcet(CALLEE_ARMS)
    assert flags(rep, "f") == {("entry", "return", None): True}
    assert flags(rep) == {("entry", "t_sync", None): False, ("t_sync", "halt", None): True}


def test_a_path_that_halts_while_armed_is_flagged():
    assert flags(wcet(HALT_WHILE_ARMED)) == {("entry", "t_sync", None): False, ("t_sync", "halt", None): True}


def test_cli_says_deadlines_are_not_checked(tmp_path, capsys):
    from host.wcet.__main__ import main
    path = tmp_path / "c.tta"
    path.write_text(CONTROL, encoding="utf-8")
    assert main([str(path)]) == 0                          # nothing is rejected, nothing is claimed
    out, err = capsys.readouterr()
    assert out.count("deadline: may run while a t_arm deadline is armed; NOT checked") == 2
    assert "DEADLINES ARE NOT CHECKED" in out
    assert "2 segment(s) may run while a deadline is armed" in err


# ---------------------------------------------------------------- run time: halt while armed (isa.md sec. 9)
HALT_ARMED_RUN = "#0 -> tmr.t_sync\n#20 -> tmr.t_arm\n#0 -> trap.t_halt\n"


def test_no_deadline_trap_after_a_halt_while_armed():
    r = run(HALT_ARMED_RUN, max_cycles=1_000)                # the deadline passes at cycle 22
    assert r.end == "halt" and not any(line.startswith("T ") for line in r.trace)


@pytest.mark.skipif(not _verilator_available(), reason="Verilator (oss-cad-suite in WSL Ubuntu-24.04) not available")
@pytest.mark.parametrize("name, src", [("dl_halt_armed", HALT_ARMED_RUN), ("dl_call_armed", CALL_WHILE_ARMED),
                                       ("dl_callee_arms", CALLEE_ARMS)])
def test_rtl_matches_iss_with_a_deadline_armed(name, src):
    from host.lockstep import run as lockstep
    outcome = lockstep(src, name, None, 2_000, checks=True)
    assert outcome.ok, outcome.mismatch
