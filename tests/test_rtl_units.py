"""Unit tests of RTL blocks outside the lockstep core, plus a lint of the board top."""
import shlex

import pytest

from host.lockstep.runner import BOARD_SOURCES, BUILD, DESIGN_DIR, ROOT, SIM_DIR, wsl, wsl_path

from .test_lockstep import pytestmark  # noqa: F401  (same skip rule)


def test_uart_tx_word():
    obj = BUILD / "verilator_uart"
    obj.mkdir(parents=True, exist_ok=True)
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && "
           f"verilator --binary --timing --timescale 1ns/1ps -Wall -Wno-fatal --top-module tb_uart "
           f"--Mdir {shlex.quote(wsl_path(obj))} -o Vtb_uart {DESIGN_DIR}/uart_tx_word.sv {SIM_DIR}/tb_uart.sv "
           f"&& {shlex.quote(wsl_path(obj / 'Vtb_uart'))}")
    code, out = wsl(cmd)
    assert code == 0, out[-4000:]
    assert "%Warning" not in out, out[-4000:]
    assert "PASS uart_tx_word" in out and "FAIL" not in out, out[-4000:]


def test_board_top_lints_clean():
    srcs = BOARD_SOURCES
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && verilator --lint-only -Wall "
           f"--top-module arty_tta_top -I{DESIGN_DIR} {' '.join(srcs)}")
    code, out = wsl(cmd)
    assert code == 0 and "%Warning" not in out, out[-4000:]


def test_telemetry_word_time_matches_uart():
    from host.common import spec
    assert spec.TELEM_CYCLES_PER_WORD % 40 == 0          # 4 bytes x 10 bits, integer clocks per bit


# tta_core -> FIFO -> UART through arty_tta_top (PR #3 review): a lone word,
# then a 70-word burst that fills the FIFO, sends back to back and drops 5.
BOARD_TELEM = ("#0 -> tmr.t_sync\n#0x5A -> telem.t_push\n#3000 -> tmr.t_wait\n"
               + "".join(f"#{0x100 + i} -> telem.t_push\n" for i in range(70))
               + "#0 -> trap.t_halt\n")


def test_board_telemetry_matches_iss():
    from host.asm import assemble, output_files
    from host.common import spec
    from host.iss import Simulator

    prog = assemble(BOARD_TELEM, "board_telem.tta")
    sim = Simulator(prog.code, prog.data, None, prog.imem_words, prog.dmem_words)
    sim.run(10_000)
    assert sim.telem.drops == 70 - spec.TELEM_FIFO_WORDS - 1

    # sec. 6.5 of toolchain_formats.md: a word pushed at p is popped at
    # max(UART free, p + 1); the line drops to the start bit one cycle later.
    expected, free = [], 0
    for c, word in sim.telem.accepted:
        pop = max(free, c + 1)
        free = pop + spec.TELEM_CYCLES_PER_WORD
        expected.append((pop + 1, word & 0xFFFF_FFFF))

    work = BUILD / "board_tb"
    work.mkdir(parents=True, exist_ok=True)
    for suffix, text in output_files(prog).items():
        (work / f"board_telem{suffix}").write_text(text, encoding="utf-8", newline="\n")
    imem, dmem = (shlex.quote(f'"{wsl_path(work / f"board_telem.{k}.hex")}"') for k in ("code", "data"))
    obj = work / "obj"
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && "
           f"verilator --binary --timing --timescale 1ns/1ps -j 0 -Wall -Wno-fatal --top-module tb_board "
           f"-I{DESIGN_DIR} -GIMEM_INIT={imem} -GDMEM_INIT={dmem} "
           f"--Mdir {shlex.quote(wsl_path(obj))} -o Vtb_board {' '.join(BOARD_SOURCES)} {SIM_DIR}/tb_board.sv "
           f"&& {shlex.quote(wsl_path(obj / 'Vtb_board'))} +max={free + 100}")
    code, out = wsl(cmd)
    assert code == 0 and "%Warning" not in out, out[-4000:]
    assert "END" in out and "FAIL" not in out, out[-4000:]

    got = [(int(f[1]), int(f[2], 16)) for f in (l.split() for l in out.splitlines()) if f[:1] == ["W"]]
    assert got == expected                         # every word, value and start cycle
    gaps = {b[0] - a[0] for a, b in zip(got[1:], got[2:])}
    assert gaps == {spec.TELEM_CYCLES_PER_WORD}    # the burst goes out back to back
    drops = [int(l.split()[1]) for l in out.splitlines() if l.startswith("D ")]
    assert drops == [sim.telem.drops]
