"""Phase 6 baseline RTL: the XBUS memory, the plant I/O + timing monitor, and the
whole system with the real NEORV32 (converted to Verilog by GHDL) running a
small program in Verilator.

Needs the Ubuntu-24.04 WSL distro (oss-cad-suite: Verilator, GHDL) and the
xPack RISC-V GCC; skipped like the lockstep tests when they are missing. The
GHDL conversion takes a few minutes and is cached under build/neorv32_ghdl.
"""
import shlex
from pathlib import Path

import pytest

from host.lockstep.runner import BUILD, ROOT, wsl, wsl_path
from host.plant_model import model as m

from .test_baseline_pid import XPACK_GCC
from .test_lockstep import pytestmark  # noqa: F401  (same skip rule)

D = "Time_Predictable_TTA.srcs/sources_1/new"
B = "baseline"
NEORV32_SRC = ROOT / "baseline" / "neorv32" / "rtl" / "core"
SOC_SOURCES = [f"{D}/tta_pkg.sv", f"{D}/plant_pkg.sv", f"{D}/dc_motor_plant_mc.sv", f"{D}/hil_env.sv",
               f"{D}/fu_telem.sv", f"{D}/uart_tx_word.sv", f"{B}/rtl/xbus_mem.sv", f"{B}/rtl/xbus_hil.sv",
               f"{B}/rtl/baseline_soc.sv"]
MAGIC = 0x4E52_5631
SMOKE_PERIOD, SMOKE_REF = 20_000, 4096


def verilate(top, sources, mdir, extra=""):
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && verilator --binary --timing --timescale 1ns/1ps -Wall "
           f"-Wno-fatal -j 0 --top-module {top} {extra} --Mdir {shlex.quote(wsl_path(mdir))} -o Vtb "
           + " ".join(sources))
    code, out = wsl(cmd)
    assert code == 0, out[-4000:]
    warnings = [l for l in out.splitlines() if l.startswith("%Warning") and "neorv32_baseline.v" not in l]
    assert not warnings, "\n".join(warnings)
    return mdir / "Vtb"


@pytest.mark.parametrize("first, burst", [(10, 1), (1, 1), (3, 2)])
def test_xbus_mem(first, burst):
    vtb = verilate("tb_xbus_mem", [f"{B}/rtl/xbus_mem.sv", f"{B}/sim/tb_xbus_mem.sv"],
                   BUILD / f"xbus_mem_{first}_{burst}", f"-GFIRST_LAT={first} -GBURST_LAT={burst}")
    code, out = wsl(shlex.quote(wsl_path(vtb)))
    assert code == 0 and f"PASS xbus_mem FIRST_LAT={first} BURST_LAT={burst}" in out and "FAIL" not in out, out


def test_xbus_hil():
    vtb = verilate("tb_xbus_hil", [f"{D}/tta_pkg.sv", f"{D}/fu_telem.sv", f"{D}/uart_tx_word.sv",
                                   f"{B}/rtl/xbus_hil.sv", f"{B}/sim/tb_xbus_hil.sv"], BUILD / "xbus_hil")
    code, out = wsl(shlex.quote(wsl_path(vtb)))
    assert code == 0 and "PASS xbus_hil" in out and "FAIL" not in out, out


def test_uart_break_reset():
    vtb = verilate("tb_uart_break_reset", [f"{B}/rtl/uart_break_reset.sv", f"{B}/sim/tb_uart_break_reset.sv"],
                   BUILD / "uart_break_reset")
    code, out = wsl(shlex.quote(wsl_path(vtb)))
    assert code == 0 and "PASS uart_break_reset" in out and "FAIL" not in out, out


# ---------------------------------------------------------------- whole system
def converted(cache):
    """NEORV32 as one Verilog module (BOOT_MODE 1: starts at 0x0), rebuilt when
    the wrapper or the NEORV32 sources change."""
    out = BUILD / "neorv32_ghdl" / f"c{cache}"
    v = out / "neorv32_baseline.v"
    srcs = [ROOT / "baseline" / "rtl" / "neorv32_baseline.vhd", *NEORV32_SRC.glob("*.vhd")]
    if not v.exists() or v.stat().st_mtime < max(s.stat().st_mtime for s in srcs):
        code, text = wsl(f"cd {shlex.quote(wsl_path(ROOT))} && bash baseline/sim/convert.sh "
                         f"{shlex.quote(wsl_path(out))} {cache} 1", timeout=1800)
        assert code == 0 and v.exists(), text[-4000:]
    return v


@pytest.fixture(scope="module")
def smoke_hex():
    code, _ = wsl(f"test -x {XPACK_GCC}")
    if code != 0:
        pytest.skip("xPack riscv-none-elf-gcc not installed")
    out = BUILD / "baseline_sw" / "smoke"
    out.mkdir(parents=True, exist_ok=True)
    cmd = (f"cd {shlex.quote(wsl_path(ROOT / 'baseline' / 'sw' / 'smoke'))} && "
           f"make -s BUILD_DIR={shlex.quote(wsl_path(out / 'obj'))} "
           f"APP_ELF={shlex.quote(wsl_path(out / 'main.elf'))} APP_FLT={shlex.quote(wsl_path(out / 'elf.bin'))} "
           f"APP_HEX={shlex.quote(wsl_path(out / 'smoke.hex'))} hex")      # SMOKE_PERIOD: main.c default
    code, text = wsl(cmd)
    assert code == 0 and (out / "smoke.hex").exists(), text[-4000:]
    return out / "smoke.hex"


def records(words):
    """Monitor words -> list of dicts (complete records only)."""
    out = []
    for i in range(0, len(words) - 9, 10):
        w = words[i:i + 10]
        assert w[0] == MAGIC, f"word {i}: {w[0]:08x}"
        none = 0xFFFF_FFFF
        out.append({"k": w[1], "rel": w[2] | w[3] << 32,
                    "start": None if w[4] == none else w[4], "sense": None if w[5] == none else w[5],
                    "act": None if w[6] == none else w[6],
                    "enc": w[7] - (1 << 32) if w[7] >> 31 else w[7],
                    "u": w[8] - (1 << 32) if w[8] >> 31 else w[8], "flags": w[9]})
    return out


@pytest.mark.parametrize("cache", [1, 0])
def test_neorv32_runs_the_pid_from_the_timer_interrupt(cache, smoke_hex):
    v = converted(cache)
    vtb = verilate("tb_baseline", [f"{B}/sim/neorv32.vlt", wsl_path(v), *SOC_SOURCES, f"{B}/sim/tb_baseline.sv"],
                   BUILD / f"baseline_sim_c{cache}")
    code, out = wsl(f"{shlex.quote(wsl_path(vtb))} +code={shlex.quote(wsl_path(smoke_hex))} +max=260000",
                    timeout=1800)
    assert code == 0 and "END" in out and "FAIL" not in out, out[-4000:]
    words = [int(f[2], 16) for f in (l.split() for l in out.splitlines()) if f[:1] == ["W"]]
    recs = records(words)
    assert len(recs) >= 8
    # the monitor: one record per release, every release SMOKE_PERIOD apart,
    # each with its three events in order, nothing extra
    assert [r["k"] for r in recs] == list(range(len(recs)))
    assert {b["rel"] - a["rel"] for a, b in zip(recs, recs[1:])} == {SMOKE_PERIOD}
    for r in recs:
        assert r["flags"] == 0b111, r
        assert 0 < r["start"] < r["sense"] < r["act"] < SMOKE_PERIOD, r
    # the control law on the board-side data path: every pwm_cmd written is
    # FixedPID of the encoder value the monitor saw the program read
    pid = m.FixedPID()
    assert [r["u"] for r in recs] == [pid.step(SMOKE_REF, r["enc"]) for r in recs]
    assert any(r["enc"] != 0 for r in recs)                      # the motor moved
    print(f"cache {cache}: start/sense/act after release:",
          [(r["start"], r["sense"], r["act"]) for r in recs[:4]])


def test_board_top_lints_clean():
    # the simulation path of baseline_soc (the synthesis path needs the VHDL)
    v = converted(1)
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && verilator --lint-only -Wall --timescale 1ns/1ps "
           f"--top-module arty_neorv32_top {B}/sim/neorv32.vlt {shlex.quote(wsl_path(v))} "
           + " ".join(SOC_SOURCES) + f" {B}/rtl/uart_break_reset.sv {B}/rtl/arty_neorv32_top.sv")
    code, out = wsl(cmd)
    warnings = [l for l in out.splitlines() if l.startswith("%Warning") and "neorv32_baseline.v" not in l]
    assert code == 0 and not warnings, out[-4000:]


def test_sources_listed_for_vivado_exist():
    for f in ["baseline/rtl/neorv32_baseline.vhd", "baseline/rtl/arty_neorv32_top.sv",
              "baseline/rtl/uart_break_reset.sv",
              "baseline/fpga/arty_neorv32.xdc", *SOC_SOURCES]:
        assert (ROOT / f).exists(), f
    assert Path(ROOT / "baseline/neorv32/rtl/file_list_core.f").exists()
