"""py -m host.lockstep programs/fib.tta [--stim FILE] [--max-cycles N] [--rebuild]"""
import argparse
import sys
from pathlib import Path

from .runner import LockstepError, build, run


def main(argv=None):
    ap = argparse.ArgumentParser(prog="py -m host.lockstep", description="ISS vs RTL lockstep compare")
    ap.add_argument("sources", nargs="+", type=Path)
    ap.add_argument("--stim", type=Path)
    ap.add_argument("--max-cycles", type=int, default=1_000_000)
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args(argv)
    try:
        build(force=args.rebuild)
        stim = args.stim.read_text(encoding="utf-8") if args.stim else None
        failed = 0
        for src in args.sources:
            o = run(src.read_text(encoding="utf-8"), src.stem, stim, args.max_cycles)
            print(f"{src.name}: {'MATCH' if o.ok else 'MISMATCH'} ({len(o.iss)} records)")
            if not o.ok:
                print(o.mismatch)
                failed += 1
    except LockstepError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
