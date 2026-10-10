"""RTL plants against host/plant_model FixedPlant, bit for bit.

Both implementations run the same stimuli: the single-cycle baseline
(dc_motor_plant.sv) and the multi-cycle one with a shared multiplier
(dc_motor_plant_mc.sv). They must match the model on every step and differ
only in when the result appears (LATENCY 1 and 10).
"""
import random
import shlex

import pytest

from host.lockstep.runner import BUILD, DESIGN_DIR, ROOT, SIM_DIR, wsl, wsl_path
from host.plant_model import gen_sv, model as m

from .test_lockstep import pytestmark  # noqa: F401  (same skip rule)

PLANT_SOURCES = [f"{DESIGN_DIR}/plant_pkg.sv", f"{DESIGN_DIR}/dc_motor_plant.sv",
                 f"{DESIGN_DIR}/dc_motor_plant_mc.sv"]
IMPLS = {"1c": "", "mc": "+define+PLANT_MC"}
LATENCY = {"1c": 1, "mc": 10}
STEP_CYCLES = 32                       # longer than the multi-cycle latency


def test_committed_plant_pkg_is_up_to_date():
    assert gen_sv.OUT.read_text(encoding="utf-8") == gen_sv.generate(), \
        "plant_pkg.sv is stale; run: py -m host.plant_model.gen_sv"


def stimuli():
    rng = random.Random(5)
    step = [(2047, 0)] * 300 + [(-2047, 0)] * 300 + [(0, 0)] * 200          # open-loop step response
    rand = [(rng.randint(-2047, 2047), rng.randint(-20_000, 20_000)) for _ in range(800)]
    load = [(500, 10_000)] * 200 + [(500, -10_000)] * 200                   # load torque both ways
    big = [(2**31 - 1, 0)] * 10 + [(-2**31, 2**31 - 1)] * 10                # datapath width
    return {"step": step, "random": rand, "load": load, "extremes": big}


def python_states(seq):
    p = m.FixedPlant()
    out = []
    for u, tau_q in seq:
        tau = tau_q * m.TAU_LSB
        assert round(tau / m.TAU_LSB) == tau_q                # the model sees exactly tau_q
        p.step(u, tau)
        out.append((p.x[0], p.x[1], p.encoder()))
    return out


@pytest.fixture(scope="module")
def plant_bins():
    bins = {}
    for impl, define in IMPLS.items():
        obj = BUILD / f"verilator_plant_{impl}"
        obj.mkdir(parents=True, exist_ok=True)
        cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && "
               f"verilator --binary --timing --timescale 1ns/1ps -Wall {define} --top-module tb_plant "
               f"-GSTEP_CYCLES={STEP_CYCLES} -I{DESIGN_DIR} --Mdir {shlex.quote(wsl_path(obj))} "
               f"-o Vtb_plant {' '.join(PLANT_SOURCES)} {SIM_DIR}/tb_plant.sv")
        code, out = wsl(cmd)
        assert code == 0 and "%Warning" not in out, out[-4000:]
        bins[impl] = obj / "Vtb_plant"
    return bins


def run_rtl(binary, impl, name, seq):
    work = binary.parent
    stim, log = work / f"{name}.stim", work / f"{name}.log"
    stim.write_text("".join(f"{u} {t}\n" for u, t in seq), encoding="utf-8", newline="\n")
    code, out = wsl(f"{shlex.quote(wsl_path(binary))} +stim={shlex.quote(wsl_path(stim))} "
                    f"+log={shlex.quote(wsl_path(log))}")
    assert code == 0 and "END" in out, out[-2000:]
    return [tuple(int(v) for v in line.split()[1:7]) for line in log.read_text().splitlines()]


@pytest.mark.parametrize("impl", list(IMPLS))
@pytest.mark.parametrize("name", ["step", "random", "load", "extremes"])
def test_rtl_plant_equals_fixedplant(plant_bins, impl, name):
    seq = stimuli()[name]
    rtl = [r[3:6] for r in run_rtl(plant_bins[impl], impl, name, seq)]
    py = python_states(seq)
    assert len(rtl) == len(seq)
    for k, (a, b) in enumerate(zip(rtl, py)):
        assert a == b, f"{impl} step {k}: rtl (theta, omega, enc) = {a}, python = {b}, input {seq[k]}"


def test_the_two_plants_differ_only_in_latency(plant_bins):
    seq = stimuli()["random"][:50]
    one = run_rtl(plant_bins["1c"], "1c", "lat", seq)
    mc = run_rtl(plant_bins["mc"], "mc", "lat", seq)
    assert [r[3:6] for r in one] == [r[3:6] for r in mc]                 # same states
    assert {b[0] - a[0] for a, b in zip(one, mc)} == {LATENCY["mc"] - LATENCY["1c"]}
    assert {b[0] - a[0] for a, b in zip(one, one[1:])} == {STEP_CYCLES}   # one step per period


def test_step_response_moves_the_motor():
    th, om, enc = python_states(stimuli()["step"])[299]
    assert enc > 1000 and om > 0                           # sanity: the stimulus does something
