"""Phase 5 on the board: read the control loop's telemetry and compare it with
the model, period by period.

    py -m pip install pyserial matplotlib
    py -m host.hil.board COM3                 control.tta, 3000 periods (3 s)
    py -m host.hil.board COM3 --trap          control_trap.tta
    py -m host.hil.board COM3 --switch        control_sw.tta, 10000 periods (10 s)

Start the script, then press RESET on the board: it waits for period 0, so
both sides start from the same state. Every period the board sends
    MAGIC, k, setpoint, encoder, pwm_cmd, elapsed after sense, elapsed after actuate, flags
(host/hil/programs.py). The script runs the same program on the ISS with the
co-simulated plant (which tests/test_hil.py ties to FixedPID + FixedPlant) and
requires every field of every period to match exactly. With --trap it requires
exactly TRAP_K periods (0..TRAP_K-1), all equal to the model, followed directly
by the trap record for period TRAP_K (cause, epc, elapsed at the handler).

With --switch (control_sw.tta, docs/io_interface.md) the board sends
    SW_MAGIC, k, din, setpoint, encoder, pwm_cmd, elapsed after the inputs, elapsed after actuate, flags
and the inputs come from the switches, so the model cannot know them in
advance. The script then checks what holds for any input: every period reads
its inputs and actuates at the same cycles (elapsed 3 and ACT + 2, flags 0),
the setpoint is SP_TABLE[din & 3] of the din that period read, and din has
no bits above 2. Only if din stayed 0 in every record does it also compare
every field with the model, as in script mode.

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


def parse_stream(chunks, n_periods, trap, switch=False):
    """Bytes -> (periods 0..n-1, trap record or None). switch: HSW1 records.

    Before period 0 the stream may hold anything (an earlier run, a word cut
    by the reset), so the reader resynchronises on the magic words until it
    sees period 0. From then on it is strict: records back to back, k counting
    up by one, nothing else, until n periods are in. With trap the board must
    send exactly n periods (0..n-1) and then the trap record for period n; a
    trap record anywhere else, or a period past n-1, is an error."""
    magic = struct.pack("<I", P.SW_MAGIC if switch else P.MAGIC)
    nw = P.SW_RECORD_WORDS if switch else P.RECORD_WORDS
    parse = M.parse_sw if switch else M.parse
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
                if len(buf) < 4 * nw:
                    break
                words = struct.unpack(f"<{nw}I", bytes(buf[:4 * nw]))
                if words[1] != 0:
                    del buf[:4]                   # not period 0 yet: keep looking
                    continue
                started = True
            if len(buf) < 4:
                break
            head = bytes(buf[:4])
            if head == magic:
                if len(buf) < 4 * nw:
                    break
                words = struct.unpack(f"<{nw}I", bytes(buf[:4 * nw]))
                del buf[:4 * nw]
                p, _ = parse(list(words))
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


def check_switch(board, trap, c_sync):
    """Switch mode (control_sw.tta). Returns (differences, exact): what must hold
    for any input, and, if din was 0 in every record, the model field by field
    (exact = True). Empty differences means the board passed."""
    diffs = []
    if trap is not None:
        diffs.append(f"trap record in switch mode: {trap}")
    timing = (P.DIN_AT + 1, P.ACT + 2, 0)
    for p in board:
        if (p.el_in, p.el_act, p.flags) != timing:
            diffs.append(f"period {p.k}: elapsed after the inputs {p.el_in}, after actuate "
                         f"{p.el_act}, flags {p.flags}; expected {timing}")
        if p.din >> 3:
            diffs.append(f"period {p.k}: din 0x{p.din:x} has bits above 2")
        if p.ref != env.SP_TABLE[p.din & env.DIN_SP]:
            diffs.append(f"period {p.k}: setpoint {p.ref} for din {p.din}, "
                         f"expected {env.SP_TABLE[p.din & env.DIN_SP]}")
    exact = all(p.din == 0 for p in board)
    if exact:
        diffs += compare(board, M.reference_din(len(board), c_sync, [(0, 0)]))
    return diffs, exact


def din_changes(periods):
    """[(k, din)] where din differs from the previous period (and period 0)."""
    out = []
    for p in periods:
        if not out or out[-1][1] != p.din:
            out.append((p.k, p.din))
    return out


def save_switch(name, periods):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.csv"
    with path.open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["k", "time_ms", "din", "setpoint", "encoder", "pwm_cmd", "load_switch",
                     "elapsed_inputs", "elapsed_actuate", "flags"])
        for p in periods:
            wr.writerow([p.k, p.k, p.din, p.ref, p.enc, p.u, p.din >> 2 & 1,
                         p.el_in, p.el_act, p.flags])
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return path, None
    t = [p.k for p in periods]
    fig, ax = plt.subplots(2, 1, sharex=True, figsize=(9, 6))
    ax[0].plot(t, [p.ref for p in periods], color="gray", lw=1, label="setpoint (from din)")
    ax[0].plot(t, [p.enc for p in periods], lw=1.2, label="encoder (board)")
    ax[0].set_ylabel("counts")
    ax[0].legend(loc="upper right")
    ax[1].plot(t, [p.u for p in periods], lw=1, color="tab:orange", label="pwm_cmd (board)")
    ax[1].set_ylabel("pwm_cmd (LSB)")
    ax[1].set_xlabel("period (ms)")
    spans, start = [], None
    for k, o in zip(t + [None], [p.din >> 2 & 1 for p in periods] + [0]):
        if o and start is None:
            start = k
        elif not o and start is not None:
            spans.append((start, k))
            start = None
    for a in ax:
        for s0, e in spans:
            a.axvspan(s0, e, color="tab:red", alpha=0.1, lw=0)
    fig.suptitle(f"{name} on the board (shaded: load switch on, as sampled each period)")
    fig.tight_layout()
    png = OUT_DIR / f"{name}.png"
    fig.savefig(png, dpi=110)
    plt.close(fig)
    return path, png


def main_switch(args):
    n = args.periods or 10_000
    c_sync = M.run_iss_sw(1).c_sync
    print(f"listening on {args.port}: press RESET on the board now, then use the switches")
    print(f"  sw[1:0] + BTN0: setpoint {list(env.SP_TABLE)}, sw[2]: load (live), {n} periods")
    try:
        board, trap = parse_stream(serial_chunks(args.port, args.baud), n, False, switch=True)
    except BoardError as e:
        print(f"FAIL: {e}")
        return 1
    diffs, exact = check_switch(board, trap, c_sync)
    csv_path, png = save_switch("control_sw", board)
    print(f"{len(board)} periods checked")
    print(f"  encoder at anchor + {sorted({p.el_in - 2 for p in board})}, "
          f"din at anchor + {sorted({p.el_in - 1 for p in board})}, "
          f"actuate at anchor + {sorted({p.el_act - 1 for p in board})}, "
          f"flags {sorted({p.flags for p in board})}")
    ch = din_changes(board)
    print(f"  din by period: " + ", ".join(f"{k}: {d}" for k, d in ch[:12])
          + (f", ... ({len(ch)} values)" if len(ch) > 12 else ""))
    print(f"  wrote {csv_path}" + (f" and {png}" if png else ""))
    if diffs:
        print(f"FAIL: {len(diffs)} difference(s); first:\n" + "\n".join(diffs[:3]))
        return 1
    if exact:
        print("din stayed 0: board == model on every period: YES")
    else:
        print("timing and setpoint mapping hold in every period: YES "
              "(din changed, so the plant is not compared with the model)")
    return 0


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
    ap.add_argument("--periods", type=int, default=None,
                    help="3000 (script mode) or 10000 (--switch)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--trap", action="store_true", help="the board runs control_trap.tta")
    mode.add_argument("--switch", action="store_true", help="the board runs control_sw.tta")
    args = ap.parse_args(argv)
    try:
        import serial  # noqa: F401
    except ImportError:
        print("needs pyserial: py -m pip install pyserial", file=sys.stderr)
        return 1
    if args.switch:
        return main_switch(args)
    if args.periods is None:
        args.periods = 3000
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
