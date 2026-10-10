"""Phase 5 on the board: read the control loop's telemetry and compare it with
the model, period by period.

    py -m pip install pyserial matplotlib
    py -m host.hil.board COM3                 control.tta, 3000 periods (3 s)
    py -m host.hil.board COM3 --trap          control_trap.tta

Start the script, then press RESET on the board: it waits for period 0, so
both sides start from the same state. Every period the board sends
    MAGIC, k, setpoint, encoder, pwm_cmd, elapsed after sense, elapsed after actuate, flags
(host/hil/programs.py). The script runs the same program on the ISS with the
co-simulated plant (which tests/test_hil.py ties to FixedPID + FixedPlant) and
requires every field of every period to match exactly. With --trap it requires
exactly TRAP_K periods (0..TRAP_K-1), all equal to the model, followed directly
by the trap record for period TRAP_K (cause, epc, elapsed at the handler).

Output: build/hil/<program>.csv and, if matplotlib is installed, <program>.png
(setpoint and encoder, pwm_cmd, the load window).
"""
import argparse
import csv
import struct
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from host.hil import env, model as M, programs as P       # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[2] / "build" / "hil"


class BoardError(Exception):
    pass


def parse_stream(chunks, n_periods, trap):
    """Bytes -> (periods 0..n-1, trap record or None).

    Before period 0 the stream may hold anything (an earlier run, a word cut
    by the reset), so the reader resynchronises on the magic words until it
    sees period 0. From then on it is strict: records back to back, k counting
    up by one, nothing else, until n periods are in. With trap the board must
    send exactly n periods (0..n-1) and then the trap record for period n; a
    trap record anywhere else, or a period past n-1, is an error."""
    magic = struct.pack("<I", P.MAGIC)
    tmagic = struct.pack("<I", P.TRAP_MAGIC)
    buf, started, periods = bytearray(), False, []
    for chunk in chunks:
        buf += chunk
        while True:
            if not started:
                i = buf.find(magic)
                if i < 0:
                    del buf[:-3]
                    break
                del buf[:i]
                if len(buf) < 4 * P.RECORD_WORDS:
                    break
                words = struct.unpack(f"<{P.RECORD_WORDS}I", bytes(buf[:4 * P.RECORD_WORDS]))
                if words[1] != 0:
                    del buf[:4]                   # not period 0 yet: keep looking
                    continue
                started = True
            if len(buf) < 4:
                break
            head = bytes(buf[:4])
            if head == magic:
                if len(buf) < 4 * P.RECORD_WORDS:
                    break
                words = struct.unpack(f"<{P.RECORD_WORDS}I", bytes(buf[:4 * P.RECORD_WORDS]))
                del buf[:4 * P.RECORD_WORDS]
                p, _ = M.parse(list(words))
                p = p[0]
                if p.k != len(periods):
                    raise BoardError(f"expected period {len(periods)}, got {p.k}")
                if len(periods) == n_periods:
                    raise BoardError(f"period {p.k} arrived where the trap record for "
                                     f"period {n_periods} should be")
                periods.append(p)
                if len(periods) == n_periods and not trap:
                    return periods, None
            elif head == tmagic:
                if len(buf) < 20:
                    break
                words = struct.unpack("<5I", bytes(buf[:20]))
                _, traps = M.parse(list(words))
                t = traps[0]
                if not trap:
                    raise BoardError(f"trap record after period {len(periods) - 1} "
                                     f"in a run without a trap: {t}")
                if len(periods) != n_periods:
                    raise BoardError(f"trap record after {len(periods)} periods, "
                                     f"expected {n_periods}: {t}")
                if t.k != n_periods:
                    raise BoardError(f"trap record names period {t.k}, expected {n_periods}")
                return periods, t
            else:
                raise BoardError(f"lost framing after period {len(periods) - 1}: 0x{head.hex()}")
    raise BoardError(f"the stream ended after {len(periods)} periods")


def serial_chunks(port, baud, idle_limit=10):
    import serial
    with serial.Serial(port, baud, timeout=1) as ser:
        idle = 0
        while True:
            chunk = ser.read(max(1, ser.in_waiting))
            if chunk:
                idle = 0
                yield chunk
                continue
            idle += 1
            if idle >= idle_limit:
                return


def compare(board, expected):
    """Lines describing every difference; empty when the board matches."""
    out = []
    for a, b in zip(board, expected):
        if a != b:
            out.append(f"period {a.k}: board {a}\n           model {b}")
    if len(board) != len(expected):
        out.append(f"board has {len(board)} periods, model {len(expected)}")
    return out


def check(board, trap, exp, n_periods, trap_mode):
    """The whole run against the model: periods 0..n-1 field by field and, with
    trap_mode, the single trap record that must follow them. Empty when equal."""
    diffs = compare(board, exp.periods[:n_periods])
    if len(exp.periods) < n_periods:
        diffs.append(f"the model has only {len(exp.periods)} periods, expected {n_periods}")
    if trap_mode:
        if len(exp.traps) != 1 or exp.traps[0].k != n_periods:
            diffs.append(f"the model's trap records are {exp.traps}, expected one for "
                         f"period {n_periods}")
        elif trap != exp.traps[0]:
            diffs.append(f"trap record: board {trap}, model {exp.traps[0]}")
    elif trap is not None:
        diffs.append(f"trap record in a run without a trap: {trap}")
    return diffs


def save(name, periods, c_sync):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.csv"
    with path.open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["k", "time_ms", "setpoint", "encoder", "pwm_cmd", "load_on",
                     "elapsed_sense", "elapsed_actuate", "flags"])
        for p in periods:
            sense = c_sync + (p.k + 1) * P.PERIOD + 1
            wr.writerow([p.k, p.k, p.ref, p.enc, p.u, int(env.tau_at(sense) != 0),
                         p.el_sense, p.el_act, p.flags])
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return path, None
    t = [p.k for p in periods]
    fig, ax = plt.subplots(2, 1, sharex=True, figsize=(9, 6))
    ax[0].plot(t, [p.ref for p in periods], color="gray", lw=1, label="setpoint")
    ax[0].plot(t, [p.enc for p in periods], lw=1.2, label="encoder (board)")
    ax[0].set_ylabel("counts")
    ax[0].legend(loc="upper right")
    ax[1].plot(t, [p.u for p in periods], lw=1, color="tab:orange", label="pwm_cmd (board)")
    ax[1].set_ylabel("pwm_cmd (LSB)")
    ax[1].set_xlabel("period (ms)")
    on = [env.tau_at(c_sync + (k + 1) * P.PERIOD + 1) != 0 for k in t]
    spans, start = [], None
    for k, o in zip(t + [None], on + [False]):
        if o and start is None:
            start = k
        elif not o and start is not None:
            spans.append((start, k))
            start = None
    for a in ax:
        for s, e in spans:
            a.axvspan(s, e, color="tab:red", alpha=0.1, lw=0)
    fig.suptitle(f"{name} on the board (shaded: load torque 0.01 N m)")
    fig.tight_layout()
    png = OUT_DIR / f"{name}.png"
    fig.savefig(png, dpi=110)
    plt.close(fig)
    return path, png


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=3_000_000)
    ap.add_argument("--periods", type=int, default=3000)
    ap.add_argument("--trap", action="store_true", help="the board runs control_trap.tta")
    args = ap.parse_args(argv)
    try:
        import serial  # noqa: F401
    except ImportError:
        print("needs pyserial: py -m pip install pyserial", file=sys.stderr)
        return 1
    name = "control_trap" if args.trap else "control"
    # with --trap: periods 0..TRAP_K-1, then the trap record for period TRAP_K
    n = P.TRAP_K if args.trap else args.periods
    print(f"model: running {name}.tta on the ISS ...")
    exp = M.run_iss(n + 1 if args.trap else n, trap=args.trap)
    print(f"listening on {args.port}: press RESET on the board now")
    try:
        board, trap = parse_stream(serial_chunks(args.port, args.baud), n, args.trap)
    except BoardError as e:
        print(f"FAIL: {e}")
        return 1
    diffs = check(board, trap, exp, n, args.trap)
    csv_path, png = save(name, board, exp.c_sync)
    print(f"{len(board)} periods compared field by field")
    print(f"  sense at anchor + {sorted({p.el_sense - 1 for p in board})}, "
          f"actuate at anchor + {sorted({p.el_act - 1 for p in board})}, "
          f"flags {sorted({p.flags for p in board})}")
    if args.trap and trap:
        print(f"  trap: cause {trap.cause}, epc {trap.epc}, handler at anchor + {trap.elapsed}, "
              f"period {trap.k}")
    print(f"  wrote {csv_path}" + (f" and {png}" if png else ""))
    if diffs:
        print(f"FAIL: {len(diffs)} difference(s); first:\n" + "\n".join(diffs[:3]))
        return 1
    print("board == model on every period: YES")
    return 0


if __name__ == "__main__":
    sys.exit(main())
