"""Static WCET analysis by implicit path enumeration (timing_model.md sec. 3).

Every function's move-level CFG (host/common/cfg.py, structure only) is cut at
the sync points t_advance and t_wait. A SEGMENT runs from a start (the function
entry, or the move after a sync point) to an end (a sync point, a return, a
halt or a word that always traps). Its WCET W is the longest path, found as an
integer linear program over how often each edge is taken (IPET, Li & Malik
1995):

  maximize    sum of x(move) * 1  +  sum of x(edge) * extra(edge)
  subject to  x(source) = 1, x(sink) = 1
              flow in = flow out at every move
              x(header) <= N * x(edges entering the loop)      for @loop_bound N

Costs follow the timing rules (the cfg.py edge cycles are lower bounds and are
not used here):
  every move that executes costs 1 cycle                            R1
  a taken jump or call, and a return, adds P bubbles                R4
  a call adds the callee's WCET from its entry to its return move   (callees first; S5)
  the sync point that closes a segment, and its stall, are not counted   sec. 3

Budget: a segment that ends at a sync point with an immediate v has
  delta = v - offset - D,  offset = 0 after t_advance or t_sync, v_prev after t_wait
and passes when W <= delta. t_sync is not a sync point (no stall) but it moves
the anchor, so it also cuts: the segment that ends at it has no budget, and
the one after it starts at anchor + 1 like a segment after t_advance. A segment
that starts at the function entry has no budget (its anchor is not known).

Checks done here: S6 (every loop in a segment has @loop_bound; loops through a
sync point are cut and need none), irreducible loops, calls into functions that
contain sync points (not supported), and v of a timing move being an immediate.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import LinearConstraint, milp

from host.common import cfg, spec

SYNC = ("tmr.t_advance", "tmr.t_wait")
CUT = SYNC + ("tmr.t_sync",)            # t_sync moves the anchor, so it cuts too


class WcetError(Exception):
    """The program is outside what the analysis supports; .errors is [(addr, msg)]."""

    def __init__(self, errors):
        super().__init__("; ".join(m for _, m in errors))
        self.errors = errors


@dataclass
class Segment:
    function: str
    start: int                    # address of the first move
    start_kind: str               # entry | t_advance | t_wait | t_sync
    end: int                      # address of the closing move
    end_kind: str                 # t_advance | t_wait | t_sync | return | halt | trap
    wcet: int                     # W: cycles from the first move to the closing one
    budget: int | None            # delta, None when it cannot be known
    steps: list[tuple[int, int, int]] = field(default_factory=list)
    # the witness path as edges (from, to, extra cycles), in execution order
    loop_bounds: dict[int, int] = field(default_factory=dict)
    # header address -> N for every loop the result relies on, callees included

    @property
    def witness(self):
        """Witness path as move addresses, in execution order."""
        return [self.start] + [v for _, v, _ in self.steps]

    @property
    def ok(self):
        return self.budget is None or self.wcet <= self.budget


@dataclass
class FunctionResult:
    name: str
    start: int
    segments: list[Segment]
    to_return: int | None         # WCET from entry to (and including) the return move
    return_bounds: dict[int, int] = field(default_factory=dict)   # loop bounds to_return relies on


@dataclass
class Report:
    functions: dict[str, FunctionResult]
    warnings: list[tuple[int, str]]

    def program_wcet(self):
        """Cycles from the first move after reset to the halt, inclusive, for a
        program whose main has no sync point; None otherwise."""
        main = self.functions.get("main")
        if main is None:
            return None
        if any(s.start_kind != "entry" or s.end_kind in SYNC_KINDS for s in main.segments):
            return None
        ends = [s.wcet for s in main.segments if s.end_kind == "halt"]
        return max(ends) if ends else None

    def halt_cycle(self):
        w = self.program_wcet()
        return None if w is None else spec.R + w - 1


SYNC_KINDS = ("t_advance", "t_wait", "t_sync")


# ---------------------------------------------------------------- driver
def analyse(prog):
    """WCET of every function and segment of an assembled program."""
    funcs = cfg.build(prog.code, prog.functions,
                      illegal=[i.addr for i in prog.code_items if i.kind == "illegal"])
    bounds = {a["addr"]: a["n"] for a in prog.annotations if a["kind"] == "loop_bound"}
    by_start = {f.start: f for f in funcs.values()}
    errors, warnings = [], []
    results = {}

    for name in _callee_first(funcs):
        f = funcs[name]
        to_return = {n: (results[n].to_return, results[n].return_bounds) for n in results}
        res = _function(f, bounds, by_start, to_return, errors, warnings)
        results[name] = res
    if errors:
        raise WcetError(errors)
    warnings = list(dict.fromkeys(warnings))
    order = sorted(results.values(), key=lambda r: r.start)
    return Report({r.name: r for r in order}, warnings)


def _callee_first(funcs):
    order, seen = [], set()

    def visit(name):
        if name in seen:
            return
        seen.add(name)
        for c in sorted(funcs[name].calls):
            visit(c)
        order.append(name)

    for name in sorted(funcs, key=lambda n: funcs[n].start):
        visit(name)
    return order


def _function(f, bounds, by_start, to_return, errors, warnings):
    starts = [(f.start, "entry", None)]
    for n in f.nodes.values():
        if n.dst in CUT and n.addr + 1 in f.nodes:
            starts.append((n.addr + 1, n.dst.split(".")[1], n))

    segments = []
    for start, kind, sync in starts:
        g = _segment_graph(f, start, by_start, to_return, errors)
        if g is None:
            continue
        offset = _offset(kind, sync, warnings)
        for end in g.ends:
            seg = _solve(f, g, start, kind, end, bounds, errors)
            if seg is None:
                continue
            if seg.end_kind in ("t_advance", "t_wait"):
                v = f.nodes[end].imm
                if v is None:
                    warnings.append((end, f"{f.nodes[end].dst} at {end} takes v from a register; "
                                          "its budget cannot be checked"))
                elif offset is not None:
                    seg.budget = v - offset - spec.D
            segments.append(seg)

    rets = [s for s in segments if s.end_kind == "return" and s.start_kind == "entry"]
    has_sync = any(n.dst in CUT for n in f.nodes.values())
    if not rets or has_sync:
        return FunctionResult(f.name, f.start, segments, None)
    rb = {}
    for s in rets:
        rb.update(s.loop_bounds)
    return FunctionResult(f.name, f.start, segments, max(s.wcet for s in rets), rb)


def _offset(kind, sync, warnings):
    if kind == "entry":
        return None                       # anchor unknown at the function entry
    if kind in ("t_advance", "t_sync"):
        return 0
    if sync.imm is None:
        warnings.append((sync.addr, f"t_wait at {sync.addr} takes v from a register; "
                                    "the budget of the segment after it cannot be checked"))
        return None
    return sync.imm


# ---------------------------------------------------------------- one segment's graph
@dataclass
class _Graph:
    nodes: list[int]                       # reachable moves, start first
    edges: list[tuple[int, int, int]]      # (from, to, extra cycles)
    ends: list[int]                        # addresses where a path may stop
    end_kind: dict[int, str]
    callee_bounds: dict[int, dict[int, int]]   # edge index -> loop bounds of the callee


def _segment_graph(f, start, by_start, to_return, errors):
    nodes, edges, ends, end_kind, callee_bounds = [], [], [], {}, {}
    seen = {start}
    work = [start]
    while work:
        a = work.pop()
        nodes.append(a)
        n = f.nodes[a]
        if n.dst in CUT:
            ends.append(a)
            end_kind[a] = n.dst.split(".")[1]
            continue
        if n.is_return or n.terminal:
            ends.append(a)
            end_kind[a] = ("return" if n.is_return else "halt" if n.dst == "trap.t_halt" else "trap")
            continue
        for e in n.succ:
            if e.to not in f.nodes:
                continue
            if e.kind == "taken":
                extra = spec.P
            elif e.kind == "after_call":
                callee = by_start.get(n.call)
                if callee is None:
                    errors.append((a, f"call at {a} to {n.call}, which is not a function start"))
                    continue
                w, cb = to_return.get(callee.name, (None, {}))
                if w is None:
                    errors.append((a, f"call at {a} to {callee.name}, which contains a sync point "
                                      "or never returns; not supported"))
                    continue
                extra = spec.P + w + spec.P
                callee_bounds[len(edges)] = cb
            else:
                extra = 0
            edges.append((a, e.to, extra))
            if e.to not in seen:
                seen.add(e.to)
                work.append(e.to)
    nodes.sort(key=lambda x: (x != start, x))
    return _Graph(nodes, edges, sorted(ends), end_kind, callee_bounds)


# ---------------------------------------------------------------- loops
def _loops(g, start):
    """Natural loops: {header: (body nodes, entering edge indices)}; raises on
    irreducible flow."""
    succ = {a: [] for a in g.nodes}
    pred = {a: [] for a in g.nodes}
    for i, (u, v, _) in enumerate(g.edges):
        succ[u].append(v)
        pred[v].append(u)

    order, seen = [], {start}               # reverse postorder from start (iterative DFS)
    stack = [(start, iter(succ[start]))]
    while stack:
        u, it = stack[-1]
        v = next(it, None)
        if v is None:
            order.append(u)
            stack.pop()
        elif v not in seen:
            seen.add(v)
            stack.append((v, iter(succ[v])))
    order.reverse()
    index = {a: i for i, a in enumerate(order)}

    dom = {a: set(g.nodes) for a in g.nodes}
    dom[start] = {start}
    changed = True
    while changed:
        changed = False
        for a in order[1:]:
            new = set.intersection(*(dom[p] for p in pred[a])) | {a} if pred[a] else {a}
            if new != dom[a]:
                dom[a] = new
                changed = True

    loops = {}
    for u, v, _ in g.edges:
        if index[v] <= index[u]:            # retreating edge
            if v not in dom[u]:
                raise WcetError([(v, f"irreducible loop at {v}: entered other than through its header")])
            body = loops.setdefault(v, set([v]))
            stack = [u]
            while stack:
                x = stack.pop()
                if x not in body:
                    body.add(x)
                    stack.extend(pred[x])
    result = {}
    for h, body in loops.items():
        entering = [i for i, (u, v, _) in enumerate(g.edges) if v == h and u not in body]
        result[h] = (body, entering)
    return result


# ---------------------------------------------------------------- ILP
def _solve(f, g, start, start_kind, end, bounds, errors):
    try:
        loops = _loops(g, start)
    except WcetError as e:
        errors.extend(e.errors)
        return None
    for h in loops:
        if h not in bounds:
            errors.append((h, f"loop at {h} in {f.name} has no @loop_bound and does not "
                              "pass a sync point (S6)"))
    if any(h not in bounds for h in loops):
        return None

    ne = len(g.edges)
    nv = ne + 2                             # edges, then source (into start), then sink (out of end)
    src, snk = ne, ne + 1

    a_eq, b_eq = [], []
    for a in g.nodes:                       # flow in == flow out
        row = np.zeros(nv)
        for i, (u, v, _) in enumerate(g.edges):
            if v == a:
                row[i] += 1
            if u == a:
                row[i] -= 1
        if a == start:
            row[src] += 1
        if a == end:
            row[snk] -= 1
        a_eq.append(row)
        b_eq.append(0)
    row = np.zeros(nv)
    row[src] = 1
    a_eq.append(row)
    b_eq.append(1)

    a_ub = []
    for h, (_, entering) in loops.items():  # x(h) <= N * x(entering)
        row = np.zeros(nv)
        for i, (_, v, _) in enumerate(g.edges):
            if v == h:
                row[i] += 1
        if h == start:
            row[src] += 1
        for i in entering:
            row[i] -= bounds[h]
        if h == start:
            row[src] -= bounds[h]
        a_ub.append(row)

    # objective: each move counted once per arrival, plus edge extras;
    # the sync point that closes the segment is not counted
    obj = np.zeros(nv)
    for i, (u, v, extra) in enumerate(g.edges):
        obj[i] += extra + (0 if (v == end and g.end_kind[end] in SYNC_KINDS) else 1)
    obj[src] += 0 if (start == end and g.end_kind[end] in SYNC_KINDS) else 1

    cons = [LinearConstraint(np.array(a_eq), b_eq, b_eq)]
    if a_ub:
        cons.append(LinearConstraint(np.array(a_ub), -np.inf, 0))
    res = milp(-obj, constraints=cons, integrality=np.ones(nv), bounds=(0, np.inf))
    if res.status != 0 or res.x is None:
        return None                         # the end is not reachable under the bounds
    x = np.rint(res.x).astype(int)
    if x[snk] != 1:
        return None
    w = int(round(-res.fun))
    seq = _witness(g, x, start, end, loops, bounds)
    _check_witness(g, x, seq, start, end, obj, w, f.name, loops, bounds)
    steps = [g.edges[i] for i in seq]
    # W is valid only while every loop the segment can run stays within its
    # bound, on the witness path or not: a loop on another path that runs past
    # its bound could make that path the longest.
    used = {h: bounds[h] for h in loops}
    for cb in g.callee_bounds.values():
        used.update(cb)
    return Segment(f.name, start, start_kind, end, g.end_kind[end], w, None, steps, dict(sorted(used.items())))


def _witness(g, x, start, end, loops, bounds):
    """One executable path from start to end that uses every edge exactly x(e)
    times, returned as edge indices.

    The ILP bounds a loop in aggregate (x(header) <= N * x(entering)), so an
    arbitrary walk with the right counts (e.g. plain Hierholzer) may run one
    entry of an inner loop more than N times and another fewer. The walk here
    gives each entry ceil(H / E) of the H header runs and E entries still left,
    which never exceeds N because H <= N * E: it keeps a loop going while the
    current entry is below its quota and leaves once the quota is met.
    _check_witness then replays the path and checks every bound per entry."""
    out = {}
    for i, (u, _, _) in enumerate(g.edges):
        out.setdefault(u, []).append(i)
    rem = [int(c) for c in x[:len(g.edges)]]
    h_rem, e_rem = {}, {}
    for h, (_, entering) in loops.items():
        h_rem[h] = sum(rem[i] for i, (_, v, _) in enumerate(g.edges) if v == h) + (h == start)
        e_rem[h] = sum(rem[i] for i in entering) + (h == start)
    active = {}                                   # header -> [quota, header runs so far]

    def enter(h):
        active[h] = [-(-h_rem[h] // e_rem[h]), 1]
        e_rem[h] -= 1
        h_rem[h] -= 1

    if start in loops:
        enter(start)
    path, u = [], start
    while u != end:
        cands = [i for i in out.get(u, []) if rem[i] > 0]
        if not cands:
            break

        def rank(i):
            _, v, _ = g.edges[i]
            score = 0
            for h, (q, done) in active.items():
                body = loops[h][0]
                if u not in body:
                    continue
                if v == h:                        # back edge of h
                    score += 0 if done < q else 2
                elif v not in body:               # leaves h
                    score += 1 if done < q else 0
            return (score, v, i)

        i = min(cands, key=rank)
        _, v, _ = g.edges[i]
        rem[i] -= 1
        path.append(i)
        for h in [h for h in active if u in loops[h][0] and v not in loops[h][0]]:
            del active[h]                         # left the loop
        if v in loops:
            if u in loops[v][0] and v in active:
                active[v][1] += 1                 # next iteration
                h_rem[v] -= 1
            else:
                enter(v)
        u = v
    return path


def _check_witness(g, x, seq, start, end, obj, w, fname, loops=None, bounds=None):
    """The witness must be one executable path from start to end that reproduces
    the ILP solution: right ends, connected, only graph edges, the same edge
    counts, the same cost, and no loop header run more than N times in any one
    entry. Flow conservation alone does not promise this, so a mismatch is an
    internal error, never a silent result."""
    problems = []
    for h, (body, _) in (loops or {}).items():
        runs, inside = ([1], True) if h == start else ([], False)   # header runs per entry
        for i in seq:
            u, v, _ = g.edges[i]
            if v == h:
                if inside and u in body:
                    runs[-1] += 1
                else:
                    runs.append(1)
                    inside = True
            elif inside and u in body and v not in body:
                inside = False
        if runs and max(runs) > bounds[h]:
            problems.append(f"loop at {h} runs {max(runs)} times in one entry, bound {bounds[h]}")
    at = start
    for i in seq:
        u, v, _ = g.edges[i]
        if u != at:
            problems.append(f"edge {u}->{v} does not continue from {at}")
            break
        at = v
    if at != end:
        problems.append(f"path ends at {at}, not at {end}")
    counts = np.zeros(len(g.edges), dtype=int)
    for i in seq:
        counts[i] += 1
    if not np.array_equal(counts, x[:len(g.edges)]):
        problems.append("edge counts differ from the ILP solution")
    cost = obj[len(g.edges)] + sum(obj[i] for i in seq)
    if int(round(cost)) != w:
        problems.append(f"path cost {cost} differs from the ILP objective {w}")
    if problems:
        raise WcetError([(start, f"internal: witness of {fname} {start}->{end}: " + "; ".join(problems))])


# ---------------------------------------------------------------- witness formatting
def compress(path):
    """[(first, last, repeat)]: straight runs of consecutive addresses, with
    identical consecutive runs folded into a repeat count."""
    runs = []
    for a in path:
        if runs and a == runs[-1][1] + 1:
            runs[-1][1] = a
        else:
            runs.append([a, a])
    out = []
    for first, last in runs:
        if out and out[-1][0] == first and out[-1][1] == last:
            out[-1][2] += 1
        else:
            out.append([first, last, 1])
    return [tuple(r) for r in out]
