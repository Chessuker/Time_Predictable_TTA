"""Read the board_hello telemetry from the Arty's USB-UART and print each record.

    py -m pip install pyserial
    py host/tools/uart_dump.py COM5 [--count 20]

Words arrive as 4 bytes, least significant byte first, at about 3 Mbaud.
A record is 5 words: 0x54544131 ("TTA1"), counter, fib(20), switches, tmr.flags.
The script re-synchronises on the magic word, so it can start mid-stream.
"""
import argparse
import struct
import sys
import time

MAGIC = 0x5454_4131
RECORD_WORDS = 5


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("port", help="serial port, e.g. COM5 (the Arty shows two; try the higher number)")
    ap.add_argument("--baud", type=int, default=3_000_000)
    ap.add_argument("--count", type=int, default=20, help="records to print, 0 = forever")
    args = ap.parse_args(argv)
    try:
        import serial
    except ImportError:
        print("needs pyserial: py -m pip install pyserial", file=sys.stderr)
        return 1

    with serial.Serial(args.port, args.baud, timeout=2) as ser:
        buf = bytearray()
        seen, last = 0, None
        print(f"listening on {args.port} at {args.baud} baud ...")
        while args.count == 0 or seen < args.count:
            chunk = ser.read(64)
            if not chunk:
                print("no data for 2 s (wrong port, or the board is not programmed?)")
                continue
            buf += chunk
            while True:
                i = buf.find(struct.pack("<I", MAGIC))
                if i < 0:
                    del buf[:-3]                       # keep a possible partial magic word
                    break
                if len(buf) < i + 4 * RECORD_WORDS:
                    del buf[:i]
                    break
                words = struct.unpack("<5I", bytes(buf[i:i + 4 * RECORD_WORDS]))
                del buf[:i + 4 * RECORD_WORDS]
                now = time.perf_counter()
                gap = f"{(now - last) * 1000:7.1f} ms" if last else "        -"
                last = now
                _, counter, fib, switches, flags = words
                status = "OK" if fib == 6765 and flags == 0 else "CHECK"
                print(f"#{counter:6d}  fib={fib:5d}  sw={switches:04b}  flags={flags:03b}  "
                      f"host gap {gap}  {status}")
                seen += 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
