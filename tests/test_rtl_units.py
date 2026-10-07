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
