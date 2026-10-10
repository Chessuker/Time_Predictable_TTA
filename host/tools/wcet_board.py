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


ORDER = [(b, d) for b in range(len(bench.BENCHES)) for d in range(bench.DATASETS)]
ORDER_SET = set(ORDER)


class BoardError(Exception):
    pass


def parse_pass(chunks):
    """Bytes from the UART -> {(bench, dataset): cycles} for exactly one pass.

    Strict, so a bad stream can never pass: after the first magic word every 16
    bytes must be one record (magic, bench, dataset, elapsed), ids must be in
    range, and from (0, 0) on the records must follow the harness order exactly
    up to the last pair. Records before the first (0, 0) belong to a partial
    pass and are only checked for framing and range. A repeat, a gap, a stray
    byte, an elapsed below the call overhead or a stream that ends early raises
    BoardError."""
    magic = struct.pack("<I", bench.MAGIC)
    buf, synced, i, got = bytearray(), False, None, {}
    for chunk in chunks:
        buf += chunk
        if not synced:
            k = buf.find(magic)
            if k < 0:
                del buf[:-3]
                continue
            del buf[:k]
            synced = True
        while len(buf) >= 16:
            m, b, d, el = struct.unpack("<4I", bytes(buf[:16]))
            del buf[:16]
            if m != bench.MAGIC:
                raise BoardError(f"lost framing: expected the magic word, got 0x{m:08x}")
            if (b, d) not in ORDER_SET:
                raise BoardError(f"record for bench {b}, dataset {d}: out of range")
            if not OVERHEAD <= el < bench.PACE:
                raise BoardError(f"bench {b}, dataset {d}: elapsed {el} is not a possible value")
            if i is None:
                if (b, d) != ORDER[0]:
                    continue                      # tail of a pass that started before we listened
                i = 0
            if (b, d) != ORDER[i]:
                raise BoardError(f"expected bench {ORDER[i][0]}, dataset {ORDER[i][1]}; "
                                 f"got bench {b}, dataset {d} (repeated, missing or out of order)")
            got[(b, d)] = el - OVERHEAD
            i += 1
            if i == len(ORDER):
                return got
    raise BoardError(f"the stream ended after {0 if i is None else i} of {len(ORDER)} records")


def compare(board, static, iss):
    """(ok, lines). ok needs every pair present on both sides, board == ISS on
    each, dataset 0 == static and nothing above static."""
    lines, ok = [], True
    if set(board) != ORDER_SET or set(iss) != ORDER_SET:
        return False, ["record set incomplete: board has %d, ISS has %d of %d pairs"
                       % (len(set(board) & ORDER_SET), len(set(iss) & ORDER_SET), len(ORDER))]
    lines.append(f"{'bench':8} {'static':>6}   board cycles per dataset (dataset 0 = worst case)")
    for i, b in enumerate(bench.BENCHES):
        row = [board[(i, d)] for d in range(bench.DATASETS)]
        exact = all(board[(i, d)] == iss[(i, d)] for d in range(bench.DATASETS))
        tight = row[0] == static[i]
        safe = max(row) <= static[i]
        ok &= exact and tight and safe
        marks = (("" if exact else "  BOARD != ISS") + ("" if tight else "  WORST != STATIC")
                 + ("" if safe else "  OVER STATIC"))
        lines.append(f"{b.name:8} {static[i]:6}   {' '.join(f'{c:5}' for c in row)}{marks}")
    return ok, lines


def serial_chunks(port, baud, idle_limit=5):
    import serial
    with serial.Serial(port, baud, timeout=2) as ser:
        idle = 0
        while True:
            chunk = ser.read(max(1, ser.in_waiting))
            if chunk:
                idle = 0
                yield chunk
                continue
            idle += 1
            print("no data for 2 s (wrong port, or the board is not running wcet_bench?)")
            if idle >= idle_limit:
                return


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
    try:
        board = parse_pass(serial_chunks(args.port, args.baud))
    except BoardError as e:
        print(f"\nFAIL: {e}")
        return 1
    ok, lines = compare(board, static, iss)
    print()
    print("\n".join(lines))
    print("\nboard == ISS on every run, worst case == static, nothing over static:",
          "YES" if ok else "NO")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
