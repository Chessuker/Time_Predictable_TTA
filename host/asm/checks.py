"""Static checks on an assembled program (timing_model.md §4).

Run after a successful assembly:
  S1  no FU output is read before its latency, on any path
  S3  no path reaches t_advance while a deadline may be armed (B1)
  S4  t_arm and t_clear stay in the same function
  S5  no recursion and no jump into another function
S2, S7 and the indirect-jump part of S5 are checked per move in assembler.py.
S6 belongs to the WCET tool.
"""
from host.common import cfg, spec

OUTPUTS_OF = {}                      # trigger port -> [(output port, latency)]
for _p in spec.PORTS:
    if _p.produced_by:
        OUTPUTS_OF.setdefault(_p.produced_by, []).append((_p.name, _p.latency))
LATENCY = {p.name: p.latency for p in spec.PORTS if p.latency}
CAP = max(LATENCY.values())          # distances at or above this never matter


def run_checks(prog):
    """Return a list of (line, message)."""
    funcs = cfg.build(prog.code, prog.functions,
                      illegal=[i.addr for i in prog.code_items if i.kind == "illegal"])
    line_of = {i.addr: i.line for i in prog.code_items}
    errors = []

    def err(addr, msg):
        errors.append((line_of.get(addr, 1), msg))

    owner = {a: f.name for f in funcs.values() for a in f.nodes}
    for f in funcs.values():
        for n in f.nodes.values():
            for e in n.succ:
                if e.kind == "taken" and owner.get(e.to) != f.name:
                    err(n.addr, f"jump from {f.name} into {owner.get(e.to, 'nowhere')} (S5)")

    _check_recursion(funcs, err)
    for f in funcs.values():
        _check_latency(f, err)
    advances = _may_advance(funcs)
    for f in funcs.values():
        _check_armed(f, funcs, advances, err)
    return errors


# ---------------------------------------------------------------- S5 recursion
def _check_recursion(funcs, err):
    state = {}

    def visit(name, stack):
        state[name] = "open"
        for callee in sorted(funcs[name].calls):
            if state.get(callee) == "open":
                cycle = stack[stack.index(callee):] + [callee]
                site = next(n.addr for n in funcs[name].nodes.values()
                            if n.call == funcs[callee].start)
                err(site, f"recursion {' -> '.join(cycle)} (S5)")
            elif callee not in state:
                visit(callee, stack + [callee])
        state[name] = "done"

    for name in sorted(funcs):
        if name not in state:
            visit(name, [name])


# ---------------------------------------------------------------- S1 latency
def _check_latency(f, err):
    """Forward dataflow: minimum cycles since the last trigger of each output."""
    start = {out: CAP for out in LATENCY}
    inn = {f.start: start}
    work = [f.start]
    reported = set()
    while work:
        a = work.pop()
        state = inn[a]
        n = f.nodes[a]
        if n.src in LATENCY and state[n.src] < LATENCY[n.src] and a not in reported:
            reported.add(a)
            err(a, f"{n.src} is read {state[n.src]} cycle(s) after its trigger, "
                   f"needs {LATENCY[n.src]} (S1)")
        after = dict(state)
        for out, _ in OUTPUTS_OF.get(n.dst, []):
            after[out] = 0
        for e in n.succ:
            if e.to not in f.nodes:
                continue
            nxt = {k: min(CAP, v + e.cycles) for k, v in after.items()}
            old = inn.get(e.to)
            merged = nxt if old is None else {k: min(old[k], nxt[k]) for k in old}
            if merged != old:
                inn[e.to] = merged
                work.append(e.to)


# ---------------------------------------------------------------- S3 / S4 armed
def _may_advance(funcs):
    """Functions that may execute t_advance, directly or through calls."""
    direct = {name for name, f in funcs.items()
              if any(n.dst == "tmr.t_advance" for n in f.nodes.values())}
    result = set(direct)
    changed = True
    while changed:
        changed = False
        for name, f in funcs.items():
            if name not in result and f.calls & result:
                result.add(name)
                changed = True
    return result


def _check_armed(f, funcs, advances, err):
    """Path-insensitive 'may be armed' bit, starting clear at function entry."""
    inn = {f.start: False}
    work = [f.start]
    while work:
        a = work.pop()
        armed = inn[a]
        n = f.nodes[a]
        if n.dst == "tmr.t_advance" and armed:
            err(a, "t_advance may run while a deadline is armed; "
                   "move t_clear to where the paths meet (S3)")
        if n.call is not None and armed:
            callee = next((c for c in funcs.values() if c.start == n.call), None)
            if callee and callee.name in advances:
                err(a, f"call to {callee.name}, which may run t_advance, while armed (S3)")
        if n.is_return and armed:
            err(a, f"{f.name} may return with a deadline still armed; "
                   "t_arm and t_clear must stay in one function (S4)")
        out = True if n.dst == "tmr.t_arm" else False if n.dst == "tmr.t_clear" else armed
        for e in n.succ:
            if e.to in f.nodes and (e.to not in inn or (out and not inn[e.to])):
                inn[e.to] = inn.get(e.to, False) or out
                work.append(e.to)

    has_arm = any(n.dst == "tmr.t_arm" for n in f.nodes.values())
    for n in f.nodes.values():
        if n.dst == "tmr.t_clear" and not has_arm:
            err(n.addr, f"t_clear in {f.name}, which has no t_arm; "
                        "t_arm and t_clear must stay in one function (S4)")
