"""Phase 4 on the board: compare measured cycles with the static WCET.

    py -m pip install pyserial
    py host/tools/wcet_board.py COM5

The board must run programs/wcet_bench.tta (in Vivado: tta_asm wcet_bench,
then Generate Bitstream and Program Device). Each run sends
    0x57435431 ("WCT1"), bench id, dataset id, elapsed
and the measured cycles of the function are elapsed - (2 + 2P).

For every benchmark and dataset the script prints the board's number, the
ISS's number for the same dataset, and the static WCET, then checks
  1. board == ISS on every run (the hardware is cycle-exact)
  2. board == static on dataset 0, the worst case (the WCET is tight)
  3. board <= static on every dataset (the WCET is safe)
Exit code 0 when all three hold.
"""
import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from host.asm import assemble                     # noqa: E402
from host.common import spec                      # noqa: E402
from host.iss import Simulator                    # noqa: E402
from host.wcet import analyse, bench              # noqa: E402

OVERHEAD = 2 + 2 * spec.P


def expected():
    """(static WCET per bench, ISS cycles per (bench id, dataset id))."""
    prog = assemble(bench.OUT.read_text(encoding="utf-8"), bench.OUT.name)
    rep = analyse(prog)
    static = {i: rep.functions[b.name].to_return for i, b in enumerate(bench.BENCHES)}
    sim = Simulator(prog.code, prog.data, None, prog.imem_words, prog.dmem_words)
    sim.run(len(bench.BENCHES) * bench.DATASETS * bench.PACE + 10_000)
    words = [w & 0xFFFF_FFFF for _, w in sim.telem.accepted]
    iss = {}
    for i in range(0, len(words) - 3, 4):
        _, b, d, el = words[i:i + 4]
        iss.setdefault((b, d), el - OVERHEAD)
    return static, iss


def read_pass(port, baud):
    import serial
    want = {(b, d) for b in range(len(bench.BENCHES)) for d in range(bench.DATASETS)}
    got = {}
    magic = struct.pack("<I", bench.MAGIC)
    with serial.Serial(port, baud, timeout=2) as ser:
        buf = bytearray()
        idle = 0
        while not want <= got.keys():
            chunk = ser.read(max(1, ser.in_waiting))
            if not chunk:
                idle += 1
                print("no data for 2 s (wrong port, or the board is not running wcet_bench?)")
                if idle >= 5:
                    raise SystemExit(1)
                continue
            idle = 0
            buf += chunk
            while True:
                i = buf.find(magic)
                if i < 0:
                    del buf[:-3]
                    break
                if len(buf) < i + 16:
                    del buf[:i]
                    break
                _, b, d, el = struct.unpack("<4I", bytes(buf[i:i + 16]))
                del buf[:i + 16]
                if (b, d) in want:
                    got[(b, d)] = el - OVERHEAD
    return got


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("port", help="serial port, e.g. COM5")
    ap.add_argument("--baud", type=int, default=3_000_000)
    args = ap.parse_args(argv)
    try:
        import serial  # noqa: F401
    except ImportError:
        print("needs pyserial: py -m pip install pyserial", file=sys.stderr)
        return 1

    static, iss = expected()
    print(f"listening on {args.port} for one full pass "
          f"({len(bench.BENCHES)} benchmarks x {bench.DATASETS} datasets) ...")
    board = read_pass(args.port, args.baud)

    ok = True
    print(f"\n{'bench':8} {'static':>6}   board cycles per dataset (dataset 0 = worst case)")
    for i, b in enumerate(bench.BENCHES):
        row = [board[(i, d)] for d in range(bench.DATASETS)]
        exact = all(board[(i, d)] == iss[(i, d)] for d in range(bench.DATASETS))
        tight = row[0] == static[i]
        safe = max(row) <= static[i]
        ok &= exact and tight and safe
        marks = ("" if exact else "  BOARD != ISS") + ("" if tight else "  WORST != STATIC") \
            + ("" if safe else "  OVER STATIC")
        print(f"{b.name:8} {static[i]:6}   {' '.join(f'{c:5}' for c in row)}{marks}")
    print("\nboard == ISS on every run, worst case == static, nothing over static:",
          "YES" if ok else "NO")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
