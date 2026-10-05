"""py -m host.iss build/prog [--stim FILE] [--max-cycles N] [-o build/prog.trace]"""
import argparse
import json
import sys
from pathlib import Path

from .image import ImageError, load
from .sim import SimError, Simulator, Stimulus


def main(argv=None):
    ap = argparse.ArgumentParser(prog="py -m host.iss", description="TTA cycle-accurate ISS")
    ap.add_argument("stem", help="assembled program without suffix, e.g. build/ctrl")
    ap.add_argument("--stim", type=Path, help="prog.stim (default: <stem>.stim if present)")
    ap.add_argument("--max-cycles", type=int, default=10_000_000)
    ap.add_argument("-o", "--trace", type=Path, help="trace file (default: <stem>.trace)")
    args = ap.parse_args(argv)

    stim_path = args.stim or Path(args.stem + ".stim")
    try:
        code, data, meta = load(args.stem)
        stim = Stimulus.parse(stim_path.read_text(encoding="utf-8")) if stim_path.exists() else None
        sim = Simulator(code, data, stim, meta["imem_words"], meta["dmem_words"])
        result = sim.run(args.max_cycles)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except SimError as e:
        print(f"program error: {e}", file=sys.stderr)
        return 2
    trace_path = args.trace or Path(args.stem + ".trace")
    trace_path.write_text("\n".join(result.trace) + "\n", encoding="utf-8", newline="\n")
    print(f"{result.end} at cycle {result.cycle}, {len(result.trace)} trace records -> {trace_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
