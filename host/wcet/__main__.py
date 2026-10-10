"""py -m host.wcet prog.tta  ->  WCET of every function and segment."""
import argparse
import sys
from pathlib import Path

from host.asm import AssemblyFailed, assemble

from .analysis import WcetError, analyse, compress

GUARANTEE = """\
What the numbers mean
  W is a SAFE UPPER BOUND on the cycles of the segment for every input under
  which each loop listed in 'valid if' runs its header at most N times per
  entry into the loop. The analysis also assumes no trap is taken inside the
  segment and that every move is legal (the static checks S1-S7 passed).
  W is TIGHT (some input takes exactly W cycles) when the witness path is
  feasible. The tool does not check path feasibility: correlated branches can
  make the witness infeasible and W pessimistic, never too small. Run the
  witness input on the ISS or the board to confirm tightness."""


def fmt_witness(path, limit=12):
    runs = compress(path)
    parts = [(f"{a}" if a == b else f"{a}-{b}") + (f" x{r}" if r > 1 else "") for a, b, r in runs[:limit]]
    return " ".join(parts) + (" ..." if len(runs) > limit else "")


def fmt_bounds(bounds, line_of):
    if not bounds:
        return "no loops (straight-line or branches only)"
    return ", ".join(f"loop at line {line_of.get(h, '?')} <= {n}" for h, n in bounds.items())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="py -m host.wcet", description="TTA static WCET (IPET)")
    ap.add_argument("source", type=Path)
    args = ap.parse_args(argv)
    try:
        prog = assemble(args.source.read_text(encoding="utf-8"), args.source.name)
        rep = analyse(prog)
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except AssemblyFailed as e:
        print(e.render(), file=sys.stderr)
        return 1
    except WcetError as e:
        line_of = {i.addr: i.line for i in prog.code_items}
        for addr, msg in e.errors:
            print(f"{args.source.name}:{line_of.get(addr, '?')}: error: {msg}", file=sys.stderr)
        return 1

    line_of = {i.addr: i.line for i in prog.code_items}
    failed = False
    for f in rep.functions.values():
        ret = "" if f.to_return is None else f"   entry -> return: {f.to_return} cycles"
        print(f"{f.name} @ {f.start}{ret}")
        for s in f.segments:
            budget = "" if s.budget is None else f"  budget {s.budget}  {'OK' if s.ok else 'OVER'}"
            failed |= not s.ok
            print(f"  {s.start_kind:9} {s.start:4} (line {line_of.get(s.start, '?')}) -> "
                  f"{s.end_kind:9} {s.end:4} (line {line_of.get(s.end, '?')}):  W = {s.wcet}{budget}")
            print(f"            valid if: {fmt_bounds(s.loop_bounds, line_of)}")
            print(f"            witness:  {fmt_witness(s.witness)}")
    if rep.program_wcet() is not None:
        print(f"program: {rep.program_wcet()} cycles, halt at cycle {rep.halt_cycle()}")
    print()
    print(GUARANTEE)
    for addr, msg in rep.warnings:
        print(f"{args.source.name}:{line_of.get(addr, '?')}: warning: {msg}", file=sys.stderr)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
