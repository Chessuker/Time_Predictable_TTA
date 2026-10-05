"""Instruction-level control-flow graph per function.

This is a STRUCTURAL, MINIMUM-LATENCY CFG. Each node is one code word and each
edge carries a lower bound on the cycles from the start of one move to the
start of the next. That is what the Phase 1 static checks need (S1 asks "can
this read come too early?"), and it is NOT a timing model of the program:

- An `after_call` edge (call site -> the move after it) costs
  CALL_RETURN_MIN_CYCLES, which leaves out the callee's own execution time.
  An interprocedural WCET analysis must add the callee's WCET separately.
- The edge out of t_advance / t_wait costs 1 (the late case). The stall is not
  on the edge; the WCET method splits the program at these sync points instead
  (B3, timing_model.md §3).
- Traps are not edges. The trap handler is analysed as its own function.

The WCET tool (Phase 4) may reuse the graph structure, but must not sum these
edge costs as if they were execution times.
"""
from dataclasses import dataclass, field

from . import spec
from .encoding import decode, illegal_reason

# Lower bound on cycles from a call to the move after it: the call itself,
# P bubbles, at least one callee move (the return), P more bubbles. A lower
# bound only: the callee's real execution time is not included.
CALL_RETURN_MIN_CYCLES = (1 + spec.P) + (1 + spec.P)


@dataclass
class Edge:
    to: int
    cycles: int              # LOWER BOUND on cycles between the two moves, not a cost
    kind: str                # seq | taken | not_taken | after_call


@dataclass
class Node:
    addr: int
    word: int
    dst: str | None = None   # dst port name, None for an illegal word
    src: str | None = None   # src port name, None for an immediate
    imm: int | None = None   # immediate value
    succ: list[Edge] = field(default_factory=list)
    call: int | None = None  # callee address for pc.t_call
    is_return: bool = False
    terminal: bool = False   # halt or a word that always traps


@dataclass
class Function:
    name: str
    start: int
    end: int
    nodes: dict[int, Node]
    calls: set[str] = field(default_factory=set)


def build(code, functions, illegal=()):
    """functions: iterable of (name, start, end) with inclusive ends.
    illegal: addresses written with .illegal; they always trap, so they end a path."""
    by_start = {s: n for n, s, _ in functions}
    illegal = set(illegal)
    out = {}
    for name, start, end in functions:
        fn = Function(name, start, end, {})
        for a in range(start, end + 1):
            node = Node(a, code[a], terminal=True) if a in illegal else _node(a, code[a])
            fn.nodes[a] = node
            if node.call is not None and node.call in by_start:
                fn.calls.add(by_start[node.call])
        out[name] = fn
    return out


def _node(a, word):
    if illegal_reason(word) is not None:
        return Node(a, word, terminal=True)
    m = decode(word)
    dst = spec.PORT_BY_ID[m.dst].name
    src = None if m.imm else spec.PORT_BY_ID[m.src].name
    n = Node(a, word, dst=dst, src=src, imm=m.value if m.imm else None)
    seq = Edge(a + 1, 1, "seq")
    taken = 1 + spec.P
    if dst == "trap.t_halt":
        n.terminal = True
    elif dst == "pc.t_jump":
        if src == "pc.link":
            n.is_return = True
        elif m.imm:
            n.succ = [Edge(m.value, taken, "taken")]
        else:
            n.terminal = True        # indirect jump; rejected by S5 elsewhere
    elif dst in ("pc.t_jz", "pc.t_jnz"):
        n.succ = [Edge(a + 1, 1, "not_taken")]
        if m.imm:
            n.succ.append(Edge(m.value, taken, "taken"))
    elif dst == "pc.t_call":
        if m.imm:
            n.call = m.value
        n.succ = [Edge(a + 1, CALL_RETURN_MIN_CYCLES, "after_call")]
    else:
        n.succ = [seq]               # timing moves: at least 1 cycle (late case)
    return n
