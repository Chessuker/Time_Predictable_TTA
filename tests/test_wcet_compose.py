"""WCET of nested calls whose callees may halt, against every input on the ISS
(audit A3-1).

A generator builds a call tree (each function is called once, from one
place), where a function may branch, run a counted loop, call its children,
halt on one arm of a branch, or return. Every branch reads its own input
register that nothing writes, so every CFG path is feasible and each input
picks one. Then:
- no run may halt later than the static halt cycle (safe),
- the latest halt over all inputs must equal it, and
- for every call in main to a callee that may halt, the latest halt among the
  inputs that stop inside that callee's subtree must equal the segment that
  ends there: the composition of call, P and the callee's W to its halt, at
  every depth, is exact.
"""
import itertools
import random

import pytest

from host.asm import assemble
from host.common import cfg, spec
from host.iss import Simulator
from host.wcet import analyse

R = spec.R


class Gen:
    def __init__(self, rng):
        self.rng = rng
        self.funcs = []           # (name, lines)
        self.inputs = []          # condition registers, one per branch
        self.n = 0

    def cond(self):
        r = f"r{len(self.inputs) + 1}"
        self.inputs.append(r)
        return r

    def label(self):
        self.n += 1
        return f"L{self.n}"

    def function(self, depth):
        """Name of a new function with its own subtree of callees."""
        name = f"f{len(self.funcs)}"
        idx = len(self.funcs)
        self.funcs.append((name, None))
        save, counter = f"r{14 - idx}", "r8"     # own copy of pc.link; loops make no calls
        lines = [f"pc.link -> {save}"]
        for _ in range(self.rng.randint(1, 3)):
            kind = self.rng.choice(["nop", "branch", "loop", "call", "call"])
            if kind == "call" and depth < 3 and len(self.funcs) < 5:
                callee = self.function(depth + 1)
                lines += [f"#{callee} -> pc.t_call"]
                if self.rng.random() < 0.5:
                    lines += [f"{save} -> pc.link"]      # harmless re-restore
            elif kind == "branch" and len(self.inputs) < 7:
                skip = self.label()
                arm = ["nop"] * self.rng.randint(0, 4)
                if self.rng.random() < 0.4:              # this arm stops the program
                    arm += ["nop"] * self.rng.randint(0, 12) + ["#0 -> trap.t_halt"]
                lines += [f"{self.cond()} -> pc.cond", f"#{skip} -> pc.t_jz", *arm, f"{skip}: nop"]
            elif kind == "loop":
                k, head = self.rng.randint(1, 4), self.label()
                lines += [f"#{k} -> {counter}", "#ADD -> alu.op", f"@loop_bound {k}",
                          f"{head}: {counter} -> alu.a", "#-1 -> alu.t_b", f"alu.out -> {counter}",
                          f"{counter} -> pc.cond", f"#{head} -> pc.t_jnz"]
            else:
                lines += ["nop"] * self.rng.randint(1, 3)
        lines += [f"{save} -> pc.link", "pc.link -> pc.t_jump"]
        self.funcs[idx] = (name, lines)
        return name

    def source(self):
        main = ["nop"]
        for _ in range(self.rng.randint(1, 2)):
            main += [f"#{self.function(1)} -> pc.t_call"]
            if len(self.inputs) < 7 and self.rng.random() < 0.5:
                skip = self.label()
                main += [f"{self.cond()} -> pc.cond", f"#{skip} -> pc.t_jz", "nop", "nop", f"{skip}: nop"]
        main += ["#0 -> trap.t_halt"]
        text = main[:]
        for name, lines in self.funcs:
            text += [f".func {name}", *lines, ".endfunc"]
        return "\n".join(text) + "\n"


def with_inputs(g, src, values):
    return "".join(f"#{v} -> {r}\n" for r, v in zip(g.inputs, values)) + src


def check(seed):
    """(halt cycle of every input, {main call site with a callee halt: halt
    cycles of the inputs that stop inside that callee's subtree}, report, source).
    The input moves are straight-line and the same length for every input, so
    one analysis covers all of them."""
    g = Gen(random.Random(seed))
    src = g.source()
    prog = assemble(with_inputs(g, src, [0] * len(g.inputs)), "t.tta")
    rep = analyse(prog)
    calls = {f.name: f.calls for f in cfg.build(prog.code, prog.functions).values()}

    def owner(pc):
        return next(n for n, a, b in prog.functions if a <= pc <= b)

    def subtree(name):
        return {name}.union(*(subtree(c) for c in calls[name]))

    sites = {s.end: subtree(s.via) for s in rep.functions["main"].segments if s.via}
    halts, by_site = [], {a: [] for a in sites}
    for values in itertools.product((0, 1), repeat=len(g.inputs)):
        p = assemble(with_inputs(g, src, values), "t.tta")
        res = Simulator(p.code, p.data, None, p.imem_words, p.dmem_words).run(100_000)
        assert res.end == "halt", (values, src)
        halts.append(res.cycle)
        last_pc = [int(x.split()[2], 16) for x in res.trace if x.startswith("M ")][-1]
        for a, funcs in sites.items():
            if owner(last_pc) in funcs:
                by_site[a].append(res.cycle)
    return halts, by_site, rep, src


@pytest.mark.parametrize("seed", range(80))
def test_static_halt_is_the_latest_halt_over_all_inputs(seed):
    halts, by_site, rep, src = check(seed)
    assert max(halts) <= rep.halt_cycle(), src          # safe
    assert max(halts) == rep.halt_cycle(), src          # exact: every path is feasible
    for s in rep.functions["main"].segments:
        if s.via:                                       # each halt inside a callee, on its own
            assert by_site[s.end] and max(by_site[s.end]) == R + s.wcet - 1, (s, src)


def test_the_generator_reaches_halts_inside_callees():
    via = deep = worst = 0
    for seed in range(80):
        rep = analyse(assemble(Gen(random.Random(seed)).source(), "t.tta"))
        segs = [s for s in rep.functions["main"].segments if s.via]
        via += bool(segs)
        deep += any(any(t.via for t in rep.functions[s.via].segments) for s in segs)
        worst += max(rep.functions["main"].segments, key=lambda s: s.wcet).via is not None
    # halts one and two or more calls deep, and programs whose worst path ends in a callee
    assert via >= 30 and deep >= 10 and worst >= 3, (via, deep, worst)
