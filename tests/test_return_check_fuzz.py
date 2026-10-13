"""The S5 return-address check against the ISS on random programs (audit A2-1).

Soundness: every program the assembler accepts must, on every input, return
from each call to the move after that call. A generator mixes the moves that
can disturb pc.link (copies through registers and memory, immediates,
trap.epc, calls into functions that clobber registers or memory, branches)
and the ISS runs every input; a shadow call stack checks each return target.

Precision: programs that follow the documented discipline (isa.md §6: copy
pc.link to a register or to memory, restore it before returning) must be
accepted, however their calls and branches are arranged.
"""
import itertools
import random

import pytest

from host.asm import AssemblyFailed, assemble
from host.common import cfg
from host.iss import Simulator

COND = [f"r{i}" for i in range(1, 5)]          # inputs; nothing else writes them
SAVE = [f"r{i}" for i in range(10, 13)]        # where pc.link copies may live
ADDRS = (7, 8)


def body(rng, name, callees, disciplined, save, addr):
    """Moves of one function, ending in its return (or a halt)."""
    out = []
    if disciplined:
        if save is not None and rng.random() < 0.5:
            out += [f"pc.link -> {save}"]
            restore = [f"{save} -> pc.link"]
        else:
            out += ["pc.link -> mem.data_in", f"#{addr} -> mem.t_store"]
            restore = [f"#{addr} -> mem.t_load", "mem.data_out -> pc.link"]
    n_label = itertools.count()
    for _ in range(rng.randint(1, 5)):
        kind = rng.choice(["call", "branch", "noise", "noise"])
        if kind == "call" and callees:
            out.append(f"#{rng.choice(callees)} -> pc.t_call")
        elif kind == "branch":
            lab = f"{name}_{next(n_label)}"
            arm = [f"#{rng.randint(0, 9)} -> {rng.choice(SAVE)}"] if not disciplined else ["nop"]
            if not disciplined and rng.random() < 0.5:
                arm = [f"{rng.choice(SAVE)} -> pc.link"]
            out += [f"{rng.choice(COND)} -> pc.cond", f"#{lab} -> pc.t_jz", *arm, f"{lab}: nop"]
        elif not disciplined:
            out.append(rng.choice([
                f"pc.link -> {rng.choice(SAVE)}",
                f"{rng.choice(SAVE)} -> pc.link",
                f"{rng.choice(SAVE)} -> {rng.choice(SAVE)}",
                f"#{rng.randint(0, 9)} -> {rng.choice(SAVE)}",
                f"trap.epc -> {rng.choice(SAVE)}",
                "pc.link -> mem.data_in",
                f"{rng.choice(SAVE)} -> mem.data_in",
                f"#{rng.choice(ADDRS)} -> mem.t_store",
                f"#{rng.choice(ADDRS)} -> mem.t_load\nmem.data_out -> {rng.choice(SAVE)}",
                f"#{rng.choice(ADDRS)} -> mem.t_load\nmem.data_out -> pc.link",
            ]))
        else:
            out.append("nop")
    if disciplined:
        out += restore
    out.append("pc.link -> pc.t_jump")
    return out


def program(rng, disciplined):
    names = [f"f{i}" for i in range(rng.randint(1, 4))]
    lines = [f"#{n} -> pc.t_call" for n in names] + ["#0 -> trap.t_halt"]   # every function runs
    for i, name in enumerate(names):
        save = SAVE[i] if i < len(SAVE) else None      # one register per function, then memory
        lines.append(f".func {name}")
        lines += body(rng, name, names[i + 1:], disciplined, save, 20 + i)
        lines.append(".endfunc")
    return "\n".join(lines) + "\n"


def runs(src):
    """Run every input on the ISS; return (returns checked, bad return or None)."""
    prog = assemble(src, "t.tta")
    nodes = {a: n for f in cfg.build(prog.code, prog.functions).values() for a, n in f.nodes.items()}
    checked = 0
    for values in itertools.product((0, 1), repeat=len(COND)):
        init = "".join(f"#{v} -> {r}\n" for r, v in zip(COND, values))
        p = assemble(init + src, "t.tta")
        sim = Simulator(p.code, p.data, None, p.imem_words, p.dmem_words)
        res = sim.run(20_000)
        pcs = [int(line.split()[2], 16) - len(COND) for line in res.trace if line.startswith("M ")]
        stack = []
        for i, pc in enumerate(pcs):
            if pc < 0:
                continue
            n = nodes[pc]
            if n.call is not None:
                stack.append(pc + 1)
            elif n.is_return:
                want = stack.pop() if stack else None
                got = pcs[i + 1] if i + 1 < len(pcs) else None
                if got != want:
                    return checked, (values, pc, got, want)
                checked += 1
        if res.end != "halt":
            return checked, (values, "end", res.end, "halt")
    return checked, None


@pytest.mark.parametrize("seed", range(150))
def test_accepted_programs_return_correctly(seed):
    rng = random.Random(seed)
    src = program(rng, disciplined=False)
    try:
        assemble(src, "t.tta")
    except AssemblyFailed as e:
        assert all("(S5)" in x.msg for x in e.errors), e.render()
        return
    _, bad = runs(src)
    assert bad is None, (bad, src)


@pytest.mark.parametrize("seed", range(60))
def test_disciplined_programs_are_accepted_and_return_correctly(seed):
    src = program(random.Random(seed), disciplined=True)
    assemble(src, "t.tta")                        # raises if the check rejects it
    checked, bad = runs(src)
    assert bad is None and checked > 0, (bad, src)
