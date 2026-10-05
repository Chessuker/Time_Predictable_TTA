"""Phase 1 test programs (programs/*.tta): result and hand-counted cycle count."""
import random
from pathlib import Path

import pytest

from host.asm import assemble
from host.iss import Simulator

PROGRAMS = Path(__file__).resolve().parents[1] / "programs"
M32 = 0xFFFFFFFF


def load(name):
    return assemble((PROGRAMS / name).read_text(encoding="utf-8"), name)


def run(prog, data=None):
    sim = Simulator(prog.code, data or prog.data, None, prog.imem_words, prog.dmem_words)
    result = sim.run(1_000_000)
    assert result.end == "halt"
    return result


def word(x):
    return x & M32


def signed(x):
    return x - (1 << 32) if x >> 31 else x


def test_fib():
    prog = load("fib.tta")
    r = run(prog)
    assert r.sim.regs[1] == 6765
    assert r.sim.dmem[prog.symbols["result"].value] == 6765
    assert r.cycle == 11 * 20 + 6


def test_bubble_sort_same_time_for_every_input():
    prog = load("bubble_sort.tta")
    n, base = 8, prog.symbols["arr"].value
    expected_cycle = 2 + 2 + (n - 1) * (2 + 25 * (n - 2) + 23 + 5) + 2 * (n - 2)
    rng = random.Random(1)
    inputs = [[signed(v) for v in prog.data[base:base + n]],
              list(range(n)),                         # already sorted
              list(range(n, 0, -1)),                  # reversed
              [rng.randint(-1000, 1000) for _ in range(n)]]
    for values in inputs:
        data = list(prog.data)
        data[base:base + n] = [word(v) for v in values]
        r = run(prog, data)
        out = [signed(v) for v in r.sim.dmem[base:base + n]]
        assert out == sorted(values)
        assert r.cycle == expected_cycle            # no data-dependent timing


def test_fixmul_matches_plant_model_rounding():
    prog = load("fixmul.tta")
    r = run(prog)
    ins = [100, -100, 4096, -4096]
    k = 186_659
    expected = [word((k * e + (1 << 15)) >> 16) for e in ins]
    outs = prog.symbols["outs"].value
    assert r.sim.dmem[outs:outs + 4] == expected
    assert r.cycle == 24 * 4 + 4


def test_call_return():
    r = run(load("call_return.tta"))
    assert r.sim.regs[10] == 10 and r.sim.regs[11] == 26
    assert r.cycle == 50


@pytest.mark.parametrize("name", sorted(p.name for p in PROGRAMS.glob("*.tta")))
def test_every_program_assembles(name):
    load(name)
