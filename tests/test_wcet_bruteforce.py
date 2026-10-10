"""IPET against exhaustive path enumeration on random structured programs (PR #5 review).

A generator writes small structured programs: straight-line moves, if/else,
and loops with @loop_bound N that may also leave early (a second exit) or jump
back to the header from the middle of the body (a second back edge), nested
up to two deep. Every conditional jump reads a register nothing writes, so
for the analysis it can go either way.

The brute force walks every path of the CFG from the first move to the halt,
counting header runs per entry into each loop, and keeps the longest. It takes
the loops from the generator (header and body as address ranges), not from the
analyzer's loop detection, so the two sides are independent. Costs are the
timing rules: 1 cycle per move, + P for a taken jump.
"""
import random
from functools import lru_cache

import pytest

from host.asm import assemble
from host.common import cfg, spec
from host.wcet import analyse

P = spec.P


class Gen:
    def __init__(self, rng):
        self.rng = rng
        self.lines = []          # source lines
        self.n = 0               # label counter
        self.loops = []          # (header label, end label, bound)

    def label(self, stem):
        self.n += 1
        return f"{stem}{self.n}"

    def emit(self, text, label=None):
        self.lines.append((label, text))

    def block(self, depth, budget):
        for _ in range(self.rng.randint(1, 3)):
            if budget[0] <= 0:
                break
            kind = self.rng.choice(["nop", "nop", "if", "loop"] if depth < 2 else ["nop", "if"])
            budget[0] -= 1
            if kind == "nop":
                for _ in range(self.rng.randint(1, 3)):
                    self.emit("nop")
            elif kind == "if":
                els, end = self.label("else"), self.label("end")
                self.emit(f"r{self.rng.randint(1, 9)} -> pc.cond")
                self.emit(f"#{els} -> pc.t_jz")
                self.block(depth + 1, budget)
                self.emit(f"#{end} -> pc.t_jump")
                self.emit("nop", label=els)
                self.block(depth + 1, budget)
                self.emit("nop", label=end)
            else:
                head, out = self.label("head"), self.label("out")
                bound = self.rng.randint(1, 3)
                self.loops.append((head, out, bound))
                self.lines.append(("@", f"@loop_bound {bound}"))
                self.emit("nop", label=head)
                self.block(depth + 1, budget)
                if self.rng.random() < 0.5:                       # second exit
                    self.emit(f"r{self.rng.randint(1, 9)} -> pc.cond")
                    self.emit(f"#{out} -> pc.t_jnz")
                if self.rng.random() < 0.5:                       # second back edge
                    self.emit(f"r{self.rng.randint(1, 9)} -> pc.cond")
                    self.emit(f"#{head} -> pc.t_jnz")
                self.block(depth + 1, budget)
                self.emit(f"r{self.rng.randint(1, 9)} -> pc.cond")
                self.emit(f"#{head} -> pc.t_jnz")              # latch
                self.emit("nop", label=out)

    def source(self):
        self.block(0, [6])
        self.emit("#0 -> trap.t_halt")
        out = []
        for label, text in self.lines:
            if label == "@":
                out.append(text)
            else:
                out.append(f"{label + ':':10} {text}" if label else f"           {text}")
        return "\n".join(out) + "\n"


def brute_force(prog, loops):
    """Longest path from address 0 to the halt, header runs counted per entry."""
    f = cfg.build(prog.code, prog.functions)["main"]
    sym = prog.symbols
    ranges = [(sym[h].value, sym[o].value - 1, n) for h, o, n in loops]   # header .. last body move

    def inside(a, r):
        return r[0] <= a <= r[1]

    @lru_cache(maxsize=None)
    def longest(a, runs):
        """Max cycles from the start of move a to the halt (inclusive); runs is
        a tuple of header runs in the current entry of each loop (0 = not in it)."""
        node = f.nodes[a]
        if node.dst == "trap.t_halt":
            return 1
        best = None
        for e in node.succ:
            new = list(runs)
            ok = True
            for k, r in enumerate(ranges):
                if inside(a, r) and not inside(e.to, r):
                    new[k] = 0                                   # left the loop
                if e.to == r[0]:
                    new[k] = new[k] + 1 if inside(a, r) else 1   # back edge or a new entry
                    if new[k] > r[2]:
                        ok = False
            if not ok:
                continue
            rest = longest(e.to, tuple(new))
            if rest is None:
                continue
            c = 1 + (P if e.kind == "taken" else 0) + rest
            best = c if best is None else max(best, c)
        return best

    first = tuple(1 if r[0] == 0 else 0 for r in ranges)
    return longest(0, first)


@pytest.mark.parametrize("seed", range(300))
def test_ipet_equals_exhaustive_enumeration(seed):
    g = Gen(random.Random(seed))
    src = g.source()
    prog = assemble(src, f"rand{seed}.tta")
    expect = brute_force(prog, g.loops)
    assert analyse(prog).program_wcet() == expect, src


def test_the_generator_covers_the_shapes_asked_for():
    shapes = {"nested": 0, "two_exits": 0, "two_back_edges": 0, "bound_1": 0}
    for seed in range(300):
        g = Gen(random.Random(seed))
        src = g.source()
        heads = [h for h, _, _ in g.loops]
        shapes["nested"] += any(src.index(f"{a}:") < src.index(f"{b}:") < src.index(f"#{a} -> pc.t_jnz")
                                for a in heads for b in heads if a != b)
        shapes["two_exits"] += any(src.count(f"#{o} -> pc.t_jnz") for _, o, _ in g.loops)
        shapes["two_back_edges"] += any(src.count(f"#{h} -> pc.t_jnz") >= 2 for h in heads)
        shapes["bound_1"] += any(n == 1 for _, _, n in g.loops)
    assert all(v >= 20 for v in shapes.values()), shapes
