"""Static WCET analysis by implicit path enumeration (timing_model.md sec. 3).

Every function's move-level CFG (host/common/cfg.py, structure only) is cut at
the sync points t_advance and t_wait. A SEGMENT runs from a start (the function
entry, or the move after a sync point) to an end (a sync point, a return, a
halt or a word that always traps, also one reached inside a callee). Its WCET W
is the longest path, found as an integer linear program over how often each
edge is taken (IPET, Li & Malik 1995):

  maximize    sum of x(move) * 1  +  sum of x(edge) * extra(edge)
  subject to  x(source) = 1, x(sink) = 1
              flow in = flow out at every move
              x(header) <= N * x(edges entering the loop)      for @loop_bound N

Costs follow the timing rules (the cfg.py edge cycles are lower bounds and are
not used here):
  every move that executes costs 1 cycle                            R1
  a taken jump or call, and a return, adds P bubbles                R4
  a call adds the callee's WCET from its entry to its return move   (callees first; S5)
  a call to a callee that may halt or trap also ends the caller's segment at
    the call: the call move, P, and the callee's WCET from its entry to that end
  the sync point that closes a segment, and its stall, are not counted   sec. 3

Budget: a segment that ends at a sync point with an immediate v has
  delta = v - offset - D,  offset = 0 after t_advance or t_sync, v_prev after t_wait
and passes when W <= delta. t_sync is not a sync point (no stall) but it moves
the anchor, so it also cuts: the segment that ends at it has no budget, and
the one after it starts at anchor + 1 like a segment after t_advance. A segment
that starts at the function entry has no budget (its anchor is not known).

Checks done here: S6 (every cycle has @loop_bound or passes a sync point, i.e.
t_advance or t_wait; a cycle through t_sync alone still needs a bound, although
t_sync cuts it into segments), irreducible loops, calls into functions that
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
    end: int                      # address of the closing move (the call, if `via`)
    end_kind: str                 # t_advance | t_wait | t_sync | return | halt | trap
    wcet: int                     # W: cycles from the first move to the closing one
    budget: int | None            # delta, None when it cannot be known
    steps: list[tuple[int, int, int]] = field(default_factory=list)
    # the witness path as edges (from, to, extra cycles), in execution order
    loop_bounds: dict[int, int] = field(default_factory=dict)
    # header address -> N for every loop the result relies on, callees included
    via: str | None = None        # the halt or trap is inside this callee, called at `end`

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
    to_end: dict[str, tuple[int, dict[int, int]]] = field(default_factory=dict)
    # "halt" / "trap" -> (WCET from entry to and including that move, loop bounds)
    has_sync: bool = False        # contains t_advance, t_wait or t_sync


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
    low = [(a, f"@loop_bound {n} at {a}: N must be >= 1") for a, n in bounds.items() if n < 1]
    if low:                               # the ILP is feasible only for N >= 1 (see _solve)
        raise WcetError(low)
    by_start = {f.start: f for f in funcs.values()}
    errors, warnings = [], []
    results = {}

    for name in _callee_first(funcs):
        f = funcs[name]
        res = _function(f, bounds, by_start, results, errors, warnings)
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


def _function(f, bounds, by_start, callees, errors, warnings):
    _check_t_sync_loops(f, bounds, by_start, callees, errors)
    starts = [(f.start, "entry", None)]
    for n in f.nodes.values():
        if n.dst in CUT and n.addr + 1 in f.nodes:
            starts.append((n.addr + 1, n.dst.split(".")[1], n))

    segments = []
    for start, kind, sync in starts:
        g = _segment_graph(f, start, by_start, callees, errors)
        offset = _offset(kind, sync, warnings)
        ends = [(end, g.end_kind[end], 0, {}, None) for end in g.ends] + g.call_ends
        for end, end_kind, sink_extra, end_bounds, via in ends:
            seg = _solve(f, g, start, kind, end, bounds, errors,
                         end_kind=end_kind, sink_extra=sink_extra, end_bounds=end_bounds, via=via)
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

    if any(n.dst in CUT for n in f.nodes.values()):
        return FunctionResult(f.name, f.start, segments, None, has_sync=True)

    def worst(kind):
        segs = [s for s in segments if s.end_kind == kind and s.start_kind == "entry"]
        if not segs:
            return None
        b = {}
        for s in segs:
            b.update(s.loop_bounds)
        return max(s.wcet for s in segs), b

    ret = worst("return")
    to_end = {k: w for k in ("halt", "trap") if (w := worst(k)) is not None}
    return FunctionResult(f.name, f.start, segments, ret[0] if ret else None,
                          ret[1] if ret else {}, to_end)


def _check_t_sync_loops(f, bounds, by_start, callees, errors):
    """S6 for cycles that pass t_sync. Only t_advance and t_wait are sync points
    (timing_model.md sec. 3), but t_sync also cuts segments, so such a cycle never
    shows up inside one segment and the per-segment check cannot see it. A cycle
    is accepted if it passes a sync point or a move with @loop_bound."""
    def succ(a):
        n = f.nodes[a]
        if n.dst in SYNC:
            return []
        out = []
        for e in n.succ:
            if e.to not in f.nodes or e.to in bounds:
                continue
            if e.kind == "after_call":
                callee = by_start.get(n.call)
                r = callees.get(callee.name) if callee else None
                if r is not None and not r.has_sync and r.to_return is None:
                    continue                    # the callee never returns
            out.append(e.to)
        return out

    for a, n in sorted(f.nodes.items()):
        if n.dst != "tmr.t_sync" or a in bounds:
            continue
        seen, stack = set(), succ(a)
        while stack:
            b = stack.pop()
            if b == a:
                errors.append((a, f"loop through t_sync at {a} in {f.name} has no @loop_bound "
                                  "and passes no sync point (t_advance or t_wait) (S6)"))
                break
            if b not in seen:
                seen.add(b)
                stack.extend(succ(b))


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
    call_ends: list = field(default_factory=list)
    # (call address, "halt" | "trap", cycles after the call move, callee loop bounds, callee)


def _segment_graph(f, start, by_start, callees, errors):
    nodes, edges, ends, end_kind, callee_bounds, call_ends = [], [], [], {}, {}, []
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
                r = callees[callee.name]
                if r.has_sync:
                    errors.append((a, f"call at {a} to {callee.name}, which contains a sync point; "
                                      "not supported"))
                    continue
                # a callee that may stop the program ends this segment at the call
                for kind, (w_end, cb_end) in r.to_end.items():
                    call_ends.append((a, kind, spec.P + w_end, cb_end, callee.name))
                if r.to_return is None:
                    continue                     # never returns: no edge to the move after the call
                extra = spec.P + r.to_return + spec.P
                callee_bounds[len(edges)] = r.return_bounds
            else:
                extra = 0
            edges.append((a, e.to, extra))
            if e.to not in seen:
                seen.add(e.to)
                work.append(e.to)
    nodes.sort(key=lambda x: (x != start, x))
    return _Graph(nodes, edges, sorted(ends), end_kind, callee_bounds, call_ends)


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
def _solve(f, g, start, start_kind, end, bounds, errors,
           end_kind=None, sink_extra=0, end_bounds=None, via=None):
    """W of one (start, end) pair. An end inside a callee (via) leaves the graph
    at its call move, and the sink carries sink_extra = P + the callee's W to it."""
    end_kind = end_kind or g.end_kind[end]
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
        obj[i] += extra + (0 if (v == end and end_kind in SYNC_KINDS) else 1)
    obj[src] += 0 if (start == end and end_kind in SYNC_KINDS) else 1
    obj[snk] += sink_extra

    cons = [LinearConstraint(np.array(a_eq), b_eq, b_eq)]
    if a_ub:
        cons.append(LinearConstraint(np.array(a_ub), -np.inf, 0))
    res = milp(-obj, constraints=cons, integrality=np.ones(nv), bounds=(0, np.inf))
    # The ILP always has an optimum, so anything else is a solver failure, never
    # "unreachable", and is reported instead of dropping the segment.
    # Feasible: g holds only moves reachable from start, so a simple path p from
    # start to end exists. Take x(e) = 1 on p, x(src) = x(snk) = 1. Flow holds on
    # a simple path. For a loop header h, the left side x(edges into h) + [h =
    # start] is at most 1 because p visits h once. If p enters h by u -> h with
    # h != start, u is outside h's body (h dominates its body, so reaching u
    # would pass h first), so u -> h is an entering edge and the right side is
    # at least N >= 1 (analyse() checks every N >= 1); if h = start it is N.
    # Bounded: irreducible loops are refused, so every cycle has a back edge to
    # a header whose runs are at most N times its entering edges; from the
    # outermost loop in, whose entering edges lie on no cycle and carry at most
    # the one unit of flow, every edge count has a finite bound.
    # tests/test_wcet_ilp_feasible.py checks the witness x against the real
    # constraint rows of every ILP the test programs produce.
    where = f"{f.name} {start}->{end}"
    if res.status != 0 or res.x is None or res.fun is None:
        raise WcetError([(start, f"internal: ILP solver gave no proven optimum for {where} "
                                 f"(status {res.status}: {res.message})")])
    x = np.rint(res.x).astype(int)
    if np.abs(res.x - x).max() > 1e-6 or x.min() < 0 or x[src] != 1 or x[snk] != 1:
        raise WcetError([(start, f"internal: ILP solution for {where} is not one path "
                                 "from start to end")])
    w = int(round(-res.fun))
    if abs(w + res.fun) > 1e-6 or w != int(obj @ x):
        raise WcetError([(start, f"internal: ILP objective for {where} is not an integer "
                                 "matching its solution")])
    seq = _witness(g, x, start, end, loops, bounds)
    if seq is None:
        raise WcetError([(start, f"internal: no witness path for {f.name} {start}->{end} "
                                 "within the search budget")])
    _check_witness(g, x, seq, start, end, obj, w, f.name, loops, bounds)
    steps = [g.edges[i] for i in seq]
    # W is valid only while every loop the segment can run stays within its
    # bound, on the witness path or not: a loop on another path that runs past
    # its bound could make that path the longest.
    used = {h: bounds[h] for h in loops}
    for cb in g.callee_bounds.values():
        used.update(cb)
    used.update(end_bounds or {})
    return Segment(f.name, start, start_kind, end, end_kind, w, None, steps,
                   dict(sorted(used.items())), via)


WITNESS_STEPS = 2_000_000          # search budget; exceeding it is an internal error


def _witness(g, x, start, end, loops, bounds):
    """One executable path from start to end that uses every edge exactly x(e)
    times and runs no loop header more than N times in one entry, returned as
    edge indices.

    The ILP bounds a loop in aggregate (x(header) <= N * x(entering)), so a walk
    that only matches the counts (e.g. plain Hierholzer) can run one entry of a
    loop past N and another below it. Such a walk always exists for reducible
    loops: the counts split into iterations, and the H header runs of E entries
    can be dealt out ceil(H/E) at a time, which is <= N because H <= N * E.

    The search is depth-first with backtracking. Each entry of a loop gets the
    quota ceil(H_left / E_left) as a hard limit; among the edges that are
    still allowed it first tries the one that moves toward what the open loops
    need (another iteration while under quota, the way out once it is met),
    so it rarely backtracks. Returns None if no path is found within the budget."""
    out = {}
    for i, (u, _, _) in enumerate(g.edges):
        out.setdefault(u, []).append(i)
    rem = [int(c) for c in x[:len(g.edges)]]
    h_rem, e_rem = {}, {}
    for h, (_, entering) in loops.items():
        h_rem[h] = sum(rem[i] for i, (_, v, _) in enumerate(g.edges) if v == h) + (h == start)
        e_rem[h] = sum(rem[i] for i in entering) + (h == start)
    active = {}                                   # header -> [quota, runs in this entry]
    if start in loops:
        active[start] = [-(-h_rem[start] // e_rem[start]), 1]
        e_rem[start] -= 1
        h_rem[start] -= 1

    def reaches(v, h, want_exit):
        """From v, staying in h's body and off its header, can the remaining
        edges reach an exit of h (want_exit) or a back edge to h?"""
        body = loops[h][0]
        seen, stack = {v}, [v]
        while stack:
            a = stack.pop()
            for i in out.get(a, []):
                if rem[i] <= 0:
                    continue
                b = g.edges[i][1]
                if b == h:
                    if not want_exit:
                        return True
                elif b not in body:
                    if want_exit:
                        return True
                elif b not in seen:
                    seen.add(b)
                    stack.append(b)
        return False

    def choices(u):
        cands = []
        for i in out.get(u, []):
            if rem[i] <= 0:
                continue
            v = g.edges[i][1]
            bad = 0
            for h, (q, done) in active.items():
                body = loops[h][0]
                if u not in body:
                    continue
                if v == h:
                    if done >= q:
                        bad = None                # would break the quota: not allowed
                        break
                elif v not in body:
                    bad += done < q               # leaving early
                else:
                    bad += not reaches(v, h, want_exit=done >= q)
            if bad is not None and v in loops and v not in active and e_rem[v] <= 0:
                bad = None                        # no entry of that loop left
            if bad is not None:
                cands.append((bad, v, i))
        cands.sort()
        return [i for _, _, i in cands]

    def take(i):
        u, v, _ = g.edges[i]
        undo = ({h: list(a) for h, a in active.items()}, dict(h_rem), dict(e_rem))
        rem[i] -= 1
        for h in [h for h in active if u in loops[h][0] and v not in loops[h][0]]:
            del active[h]
        if v in loops:
            if v in active and u in loops[v][0]:
                active[v][1] += 1
                h_rem[v] -= 1
            else:
                active[v] = [-(-h_rem[v] // e_rem[v]), 1]
                e_rem[v] -= 1
                h_rem[v] -= 1
        return undo

    def restore(i, undo):
        nonlocal active, h_rem, e_rem
        rem[i] += 1
        active, h_rem, e_rem = undo

    total = sum(rem)
    path, undos = [], []
    frames = [[start, choices(start), 0]]         # node, ordered choices, next to try
    steps = 0
    while frames:
        steps += 1
        if steps > WITNESS_STEPS:
            return None
        frame = frames[-1]
        u, cands, k = frame
        if u == end and len(path) == total:
            return path
        if k < len(cands):
            frame[2] += 1
            i = cands[k]
            undos.append(take(i))
            path.append(i)
            v = g.edges[i][1]
            frames.append([v, choices(v), 0])
        else:
            frames.pop()
            if path:
                restore(path.pop(), undos.pop())
    return None


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
    cost = obj[len(g.edges)] + sum(obj[i] for i in seq) + obj[len(g.edges) + 1]
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
