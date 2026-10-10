"""Closed-loop references and the telemetry format of programs/control*.tta.

reference()  the Python model: host/plant_model FixedPID + FixedPlant on the
             exact cycle timeline of the control program, no ISS involved
run_iss()    the control program itself on the ISS, with the plant co-simulated
parse()      telemetry words -> period records and trap records

tests/test_hil.py requires run_iss() == reference() for every period, so the
program is the model; the lockstep and the board then compare against the ISS.
"""
from dataclasses import dataclass

from host.asm import assemble
from host.common import spec
from host.iss import Simulator
from host.plant_model import model as m

from . import env, programs as P


@dataclass(frozen=True)
class Period:
    k: int
    ref: int
    enc: int
    u: int
    el_sense: int = 1 + 1        # elapsed read one move after sense
    el_act: int = P.ACT + 2      # elapsed read one move after actuate
    flags: int = 0


@dataclass(frozen=True)
class Trap:
    cause: int
    epc: int
    elapsed: int
    k: int


def s32(x):
    x &= 0xFFFF_FFFF
    return x - (1 << 32) if x & 0x8000_0000 else x


def parse(words):
    """Words in order -> ([Period], [Trap]); raises on anything unexpected."""
    periods, traps, i = [], [], 0
    while i < len(words):
        w = words[i] & 0xFFFF_FFFF
        if w == P.MAGIC and i + P.RECORD_WORDS <= len(words):
            _, k, ref, enc, u, es, ea, fl = (s32(x) for x in words[i:i + P.RECORD_WORDS])
            periods.append(Period(k, ref, enc, u, es, ea, fl))
            i += P.RECORD_WORDS
        elif w == P.TRAP_MAGIC and i + 5 <= len(words):
            _, cause, epc, el, k = (s32(x) for x in words[i:i + 5])
            traps.append(Trap(cause, epc, el, k))
            i += 5
        else:
            raise ValueError(f"word {i}: 0x{w:08x} does not start a record")
    return periods, traps


def reference(n_periods, c_sync, latency=env.LATENCY):
    """The model on the program's timeline. c_sync is the cycle of t_sync, so
    period k has anchor c_sync + (k + 1) * PERIOD, senses at anchor + 1 and
    actuates at anchor + ACT + 1."""
    pid = m.FixedPID()
    plant = env.PlantCosim(latency=latency)
    pwm_log, out = [], []
    for k in range(n_periods):
        anchor = c_sync + (k + 1) * P.PERIOD
        enc = plant.encoder(anchor + 1, pwm_log)
        ref = env.ref_at(k)
        u = pid.step(ref, enc)
        pwm_log.append((anchor + P.ACT + 1, u))
        out.append(Period(k, ref, enc, u))
    return out


@dataclass
class IssRun:
    periods: list
    traps: list
    sim: Simulator
    c_sync: int


def run_iss(n_periods, trap=False, latency=env.LATENCY):
    prog = assemble(P.generate(trap), "control_trap.tta" if trap else "control.tta")
    sim = Simulator(prog.code, prog.data, None, prog.imem_words, prog.dmem_words,
                    plant=env.PlantCosim(latency=latency))
    c_sync = None
    # the move before the first t_advance is t_sync; run just past n periods
    sim.run(spec.R + 16 + (n_periods + 1) * P.PERIOD + P.ACT + 200)
    for line in sim.trace:
        f = line.split()
        if f[0] == "M" and spec.PORT_BY_ID[int(f[5], 16)].name == "tmr.t_sync":
            c_sync = int(f[1])
            break
    words = [w for _, w in sim.telem.accepted]
    periods, traps = parse(words)
    return IssRun(periods[:n_periods], traps, sim, c_sync)
