"""Phase 6: the C PID of the soft-core baseline (baseline/sw/pid) against
host/plant_model FixedPID, bit for bit.

Built with gcc in the Ubuntu-24.04 WSL distro and fed (ref, enc) sequences
from the closed-loop model, the switch-mode model and random inputs that hit
every branch (error clamp, output saturation, anti-windup hold, integrator
clamp). Also cross-compiled with the xPack RISC-V GCC when it is installed.
"""
import random
import shlex

import pytest

from host.baseline import gen_c
from host.hil import env, model as M
from host.lockstep.runner import BUILD, ROOT, wsl, wsl_path
from host.plant_model import model as m

from .test_lockstep import pytestmark  # noqa: F401  (needs the WSL distro)

PID_DIR = "baseline/sw/pid"
XPACK_GCC = "~/xpack/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-gcc"
CFLAGS = "-std=c11 -O2 -Wall -Wextra -Werror"
# random encoder steps per period: well past what the motor can do (281 at full
# pwm_cmd) and still inside the 32-bit budget of p - d + acc (831)
D_ENC_MAX = 800


@pytest.fixture(scope="module")
def pid_bin():
    out = BUILD / "baseline_pid"
    out.mkdir(parents=True, exist_ok=True)
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && gcc {CFLAGS} -I{PID_DIR} "
           f"{PID_DIR}/pid.c {PID_DIR}/pid_host.c -o {shlex.quote(wsl_path(out / 'pid_host'))}")
    code, text = wsl(cmd)
    assert code == 0, text
    return out / "pid_host"


def run_c(pid_bin, sequences):
    """Each sequence is [(ref, enc)]; the C state is reset between sequences."""
    lines = []
    for seq in sequences:
        lines.append("reset")
        lines += [f"{r} {e}" for r, e in seq]
    work = pid_bin.parent / "in.txt"
    work.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    code, text = wsl(f"{shlex.quote(wsl_path(pid_bin))} < {shlex.quote(wsl_path(work))}")
    assert code == 0, text
    return [int(x) for x in text.split()]


def run_py(sequences, log=None):
    out = []
    for seq in sequences:
        pid = m.FixedPID(log=log)
        out += [pid.step(r, e) for r, e in seq]
    return out


def test_constants_are_up_to_date():
    assert gen_c.main(["--check"]) == 0


def test_closed_loop_trajectories(pid_bin):
    # the Phase 5 schedule (step up, load, step down) and the switch-mode table
    script = M.reference(1000, 3)
    a = lambda k: 3 + (k + 1) * 100_000                              # noqa: E731
    din = [(0, 0), (a(50) + 2, 1), (a(250) + 2, 2), (a(450) + 2, 3 | 4), (a(650) + 2, 0)]
    switch = M.reference_din(800, 3, din)
    seqs = [[(p.ref, p.enc) for p in script], [(p.ref, p.enc) for p in switch]]
    assert run_c(pid_bin, seqs) == [p.u for p in script] + [p.u for p in switch]


def random_sequences(seed, n_seq=40, length=400):
    rng = random.Random(seed)
    seqs = []
    for _ in range(n_seq):
        enc, ref, seq = rng.randint(-50_000, 50_000), 0, []
        for _ in range(length):
            if rng.random() < 0.05:
                ref = rng.choice([enc, enc + rng.randint(-20_000, 20_000), rng.randint(-60_000, 60_000)])
            enc += rng.randint(-D_ENC_MAX, D_ENC_MAX)
            seq.append((ref, enc))
        seqs.append(seq)
    return seqs


def branch_counts(seqs):
    """How often each branch of FixedPID.step is taken on these inputs."""
    n = {"e clamped": 0, "saturated": 0, "hold": 0, "acc clamped": 0, "linear": 0}
    for seq in seqs:
        pid = m.FixedPID()
        for r, e_ in seq:
            e = max(-m.E_CLAMP, min(m.E_CLAMP, r - e_))
            u = pid.step(r, e_)
            n["e clamped"] += abs(r - e_) > m.E_CLAMP
            sat = abs(u) == m.U_MAX
            n["saturated"] += sat
            n["hold"] += sat and e * u > 0
            n["acc clamped"] += abs(pid.acc) == m.ACC_MAX
            n["linear"] += not sat
    return n


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_random_inputs_hit_every_branch(pid_bin, seed):
    seqs = random_sequences(seed)
    assert run_c(pid_bin, seqs) == run_py(seqs)
    n = branch_counts(seqs)
    assert all(v > 0 for v in n.values()), n


def test_the_motor_cannot_leave_the_32_bit_budget():
    # |p - d + acc| < 2^31 needs |d_enc| <= (2^31 - 1 - KPQ * E_CLAMP - ACC_MAX) / KDQ
    budget = ((1 << 31) - 1 - m.KPQ * m.E_CLAMP - m.ACC_MAX) // m.KDQ
    plant, prev, top = env.PlantCosim(), 0, 0
    for k in range(1, 3000):                         # full pwm_cmd until the speed settles
        enc = plant.encoder(k * 100_000, [(0, m.U_MAX)])
        top, prev = max(top, abs(enc - prev)), enc
    assert top < D_ENC_MAX <= budget, (top, budget)


def test_every_intermediate_fits_in_32_bits():
    # the C (and TTA) arithmetic is 32-bit; FixedPID is not, so the equality
    # above rests on this
    log = m.WidthLog()
    run_py(random_sequences(9), log)
    wide = {k: v for k, v in log.max.items() if v > 32}
    assert not wide, wide


def test_cross_compiles_for_the_neorv32():
    code, _ = wsl(f"test -x {XPACK_GCC}")
    if code != 0:
        pytest.skip("xPack riscv-none-elf-gcc not installed")
    out = BUILD / "baseline_pid"
    out.mkdir(parents=True, exist_ok=True)
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && {XPACK_GCC} -march=rv32imc_zicsr -mabi=ilp32 "
           f"{CFLAGS} -I{PID_DIR} -c {PID_DIR}/pid.c -o {shlex.quote(wsl_path(out / 'pid.o'))}")
    code, text = wsl(cmd)
    assert code == 0, text
