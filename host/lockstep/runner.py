"""Run a program on the ISS and on the RTL (Verilator in WSL) and compare the
two traces line by line (toolchain_formats.md sec. 6.3).

The RTL is built once into build/verilator and rebuilt when any RTL or
testbench file is newer than the binary. Verilator runs in the Ubuntu-24.04
WSL distro with oss-cad-suite (see project memory); nothing else is used.
"""
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from host.asm import assemble, output_files
from host.iss import Simulator, Stimulus

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
OBJ = BUILD / "verilator"
BINARY = OBJ / "Vtb_tta"
DISTRO = "Ubuntu-24.04"
ENV = "source ~/oss-cad-suite/environment >/dev/null 2>&1"

# HDL lives in the Vivado project's own layout (Time_Predictable_TTA.srcs).
SRCS = "Time_Predictable_TTA.srcs"
DESIGN_DIR = f"{SRCS}/sources_1/new"
SIM_DIR = f"{SRCS}/sim_1/new"
CORE_SOURCES = [f"{DESIGN_DIR}/{f}" for f in (
    "tta_pkg.sv", "tta_sram_1r1w.sv", "fu_alu.sv", "fu_mul.sv", "fu_tmr.sv", "fu_telem.sv",
    "tta_core.sv")]
HIL_SOURCES = [f"{DESIGN_DIR}/{f}" for f in ("plant_pkg.sv", "dc_motor_plant_mc.sv", "hil_env.sv")]
BOARD_SOURCES = CORE_SOURCES + HIL_SOURCES + [f"{DESIGN_DIR}/uart_tx_word.sv", f"{DESIGN_DIR}/arty_tta_top.sv"]
RTL_SOURCES = CORE_SOURCES + [f"{SIM_DIR}/tb_tta.sv"]


class LockstepError(RuntimeError):
    pass


def wsl_path(p):
    """D:\\a\\b -> /mnt/d/a/b"""
    p = PureWindowsPath(Path(p).resolve())
    return "/mnt/" + p.drive[0].lower() + "/" + "/".join(p.parts[1:])


def wsl(cmd, timeout=900):
    full = ["wsl", "-d", DISTRO, "-e", "bash", "-c", f"{ENV}; {cmd}"]
    r = subprocess.run(full, capture_output=True, timeout=timeout)
    out = (r.stdout + r.stderr).decode("utf-8", errors="replace").replace("\x00", "")
    return r.returncode, out


def build(force=False):
    srcs = [ROOT / s for s in RTL_SOURCES]
    if not force and BINARY.exists() and BINARY.stat().st_mtime >= max(s.stat().st_mtime for s in srcs):
        return
    OBJ.mkdir(parents=True, exist_ok=True)
    cmd = " ".join([
        "cd", shlex.quote(wsl_path(ROOT)), "&&",
        "verilator --binary --timing --timescale 1ns/1ps -j 0 -Wall -Wno-fatal +define+TTA_SIM",
        f"--top-module tb_tta -I{DESIGN_DIR}",
        "--Mdir", shlex.quote(wsl_path(OBJ)), "-o Vtb_tta",
        *RTL_SOURCES,
    ])
    code, out = wsl(cmd)
    if code != 0 or not BINARY.exists():
        raise LockstepError("verilator build failed:\n" + out[-6000:])
    warnings = [l for l in out.splitlines() if l.startswith("%Warning")]
    if warnings:
        raise LockstepError("verilator warnings (treated as errors):\n" + out[-6000:])


@dataclass
class Outcome:
    name: str
    iss: list[str]
    rtl: list[str]
    mismatch: str | None

    @property
    def ok(self):
        return self.mismatch is None


def compare(iss, rtl, source_lines=None, pc_line=None, context=10):
    """First differing line, with context, or None (sec. 6.3)."""
    for i in range(max(len(iss), len(rtl))):
        a = iss[i] if i < len(iss) else "<end>"
        b = rtl[i] if i < len(rtl) else "<end>"
        if a != b:
            ctx = "\n".join(f"    {l}" for l in iss[max(0, i - context):i])
            where = ""
            f = a.split()
            if pc_line and len(f) > 2 and f[0] == "M":
                line = pc_line.get(int(f[2], 16))
                if line and source_lines:
                    where = f"\n  source line {line}: {source_lines[line - 1].strip()}"
            return (f"trace differs at record {i + 1}:\n  iss: {a}\n  rtl: {b}{where}\n"
                    f"  previous records:\n{ctx}")
    return None


def run(source, name="prog", stim_text=None, max_cycles=1_000_000, checks=True):
    """Assemble source, run ISS and RTL, return Outcome."""
    prog = assemble(source, f"{name}.tta", checks=checks)
    work = BUILD / "lockstep"
    work.mkdir(parents=True, exist_ok=True)
    for suffix, text in output_files(prog).items():
        (work / f"{name}{suffix}").write_text(text, encoding="utf-8", newline="\n")

    stim = Stimulus.parse(stim_text) if stim_text else None
    sim = Simulator(prog.code, prog.data, stim, prog.imem_words, prog.dmem_words)
    iss = sim.run(max_cycles).trace
    (work / f"{name}.iss.trace").write_text("\n".join(iss) + "\n", encoding="utf-8", newline="\n")

    build()
    args = [f"+code={wsl_path(work / (name + '.code.hex'))}",
            f"+data={wsl_path(work / (name + '.data.hex'))}",
            f"+trace={wsl_path(work / (name + '.rtl.trace'))}",
            f"+max={max_cycles}"]
    if stim_text:
        (work / f"{name}.stim").write_text(stim_text, encoding="utf-8", newline="\n")
        args.append(f"+stim={wsl_path(work / (name + '.stim'))}")
    code, out = wsl(shlex.quote(wsl_path(BINARY)) + " " + " ".join(shlex.quote(a) for a in args))
    trace_path = work / f"{name}.rtl.trace"
    if code != 0 or not trace_path.exists():
        raise LockstepError(f"RTL simulation failed:\n{out[-4000:]}")
    rtl = trace_path.read_text(encoding="utf-8").splitlines()

    pc_line = {i.addr: i.line for i in prog.code_items}
    return Outcome(name, iss, rtl, compare(iss, rtl, prog.source_lines, pc_line))


# ---------------------------------------------------------------- closed loop (Phase 5)
OBJ_HIL = BUILD / "verilator_hil"
BINARY_HIL = OBJ_HIL / "Vtb_tta"
HIL_TEST_LOAD = (2_000_000, 600_000, 1_100_000)     # tb_tta defaults under TTA_HIL


def build_hil(force=False):
    srcs = [ROOT / s for s in CORE_SOURCES + HIL_SOURCES + [f"{SIM_DIR}/tb_tta.sv"]]
    if not force and BINARY_HIL.exists() and BINARY_HIL.stat().st_mtime >= max(s.stat().st_mtime for s in srcs):
        return
    OBJ_HIL.mkdir(parents=True, exist_ok=True)
    cmd = " ".join([
        "cd", shlex.quote(wsl_path(ROOT)), "&&",
        "verilator --binary --timing --timescale 1ns/1ps -j 0 -Wall -Wno-fatal +define+TTA_SIM +define+TTA_HIL",
        f"--top-module tb_tta -I{DESIGN_DIR}",
        "--Mdir", shlex.quote(wsl_path(OBJ_HIL)), "-o Vtb_tta",
        *CORE_SOURCES, *HIL_SOURCES, f"{SIM_DIR}/tb_tta.sv",
    ])
    code, out = wsl(cmd)
    if code != 0 or not BINARY_HIL.exists():
        raise LockstepError("verilator build failed:\n" + out[-6000:])
    if any(l.startswith("%Warning") for l in out.splitlines()):
        raise LockstepError("verilator warnings (treated as errors):\n" + out[-6000:])


def run_hil(source, name="prog", max_cycles=1_000_000, checks=True):
    """Like run(), but io.encoder comes from the plant: hil_env in the RTL,
    host/hil/env.PlantCosim on the ISS, both with the test load window."""
    from host.hil import env
    period, on, off = HIL_TEST_LOAD
    tau = lambda t: env.LOAD_TAU if on <= t % period < off else 0      # noqa: E731
    prog = assemble(source, f"{name}.tta", checks=checks)
    work = BUILD / "lockstep_hil"
    work.mkdir(parents=True, exist_ok=True)
    for suffix, text in output_files(prog).items():
        (work / f"{name}{suffix}").write_text(text, encoding="utf-8", newline="\n")
    sim = Simulator(prog.code, prog.data, None, prog.imem_words, prog.dmem_words,
                    plant=env.PlantCosim(tau=tau))
    iss = sim.run(max_cycles).trace
    build_hil()
    args = [f"+code={wsl_path(work / (name + '.code.hex'))}",
            f"+data={wsl_path(work / (name + '.data.hex'))}",
            f"+trace={wsl_path(work / (name + '.rtl.trace'))}",
            f"+max={max_cycles}"]
    code, out = wsl(shlex.quote(wsl_path(BINARY_HIL)) + " " + " ".join(shlex.quote(a) for a in args))
    trace_path = work / f"{name}.rtl.trace"
    if code != 0 or not trace_path.exists():
        raise LockstepError(f"RTL simulation failed:\n{out[-4000:]}")
    rtl = trace_path.read_text(encoding="utf-8").splitlines()
    pc_line = {i.addr: i.line for i in prog.code_items}
    return Outcome(name, iss, rtl, compare(iss, rtl, prog.source_lines, pc_line))
