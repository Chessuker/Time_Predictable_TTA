"""Phase 6 on the board: the baseline control program in every configuration,
uploaded through the NEORV32 bootloader, measured by the xbus_hil monitor.

    py -m pip install pyserial
    py -m host.baseline.board build                       every configuration -> build/baseline_exe/<name>/
    py -m host.baseline.board run COM5 --cache 1          all runs for the bitstream with caches
    py -m host.baseline.board run COM5 --cache 0 --only irq_L2_asap
    py -m host.baseline.board report                      table of every saved run

One run (nothing to press on the board):
  1. at 19200 baud, a UART break (uart_break_reset: RX low >= 10 ms) resets
     the whole system, plant included, into the bootloader; wait for "CMD:> "
  2. 'i': the SYSINFO SOC word must show I$ and D$ as --cache says, so a run
     is never filed under the wrong bitstream
  3. 'u' and neorv32_exe.bin, wait for "OK"; 'e' starts the program
  4. the program switches the UART TX to the monitor (3 Mbaud) and releases
     the first period START_DELAY = 1 s later; the port switches baud meanwhile
  5. read NRV1 records until --periods are in: from k = 0, strictly in order
build writes, next to each executable, manifest.json: what it was built from
(git commit, tracked and untracked changes, sha256 of every file under
baseline/sw, the NEORV32 submodule commit, the GCC version), bound to the
executable's sha256. run refuses an executable its manifest does not describe.
Saved in build/baseline_board/c<cache>/<name>/: records.csv, summary.json (with the
provenance of the run: that manifest, the sources changed since the build, the
git state at run time, sha256 of the bitstream file on disk)
(host/baseline/analysis.py: statistics, record checks, model replay).

A run's verdict is about the integrity of the measurement only: "integrity OK"
means the record sequence check and the model replay found nothing. It says
nothing about timing; deadline misses are a result and are printed with every
run ("deadline misses N of M"). Statistics are given for all periods (primary,
period 0 included with its cold caches) and for the steady state (k >= 1).

Configurations: trigger (irq: timer interrupt) x background load (L0 none,
L1d D$ thrashing, L1di L1d + I$ pressure, cache 1 only, L2 L1d + GPTMR
handler) x actuation (asap, wait = release + 1001).
"""
import argparse
import csv
import hashlib
import json
import re
import shlex
import struct
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from host.baseline import analysis as A, bustrace as bt          # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXE_DIR = ROOT / "build" / "baseline_exe"
SW_SOURCES = ROOT / "baseline" / "sw"
MANIFEST = "manifest.json"
GCC = "$HOME/xpack/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-gcc"   # baseline.mk RISCV_PREFIX
OUT_DIR = ROOT / "build" / "baseline_board"
CTRL = ROOT / "baseline" / "sw" / "ctrl"

BOOT_BAUD = 19_200
MON_BAUD = 3_000_000
BREAK_S = 0.05                   # > 10 ms (N_BRK of arty_neorv32_top)
START_DELAY_S = 1.0              # main.c default
UPLOAD_GAP_S = 0.05              # after the upload prompt, before the first byte
SOC_ICACHE, SOC_DCACHE = 5, 6    # SYSINFO SOC bits

TRIGS = {"irq": 0}                # polling (TRIG=1) is not implemented yet
LOADS = {"L0": 0, "L1d": 1, "L1di": 2, "L2": 3}
ACTS = {"asap": 0, "wait": 1}


class BoardError(Exception):
    pass


def configs(cache):
    """name -> make variables, for the bitstream with (1) or without (0) caches"""
    out = {}
    for t, tv in TRIGS.items():
        for l, lv in LOADS.items():
            if l == "L1di" and not cache:         # I$ pressure needs an I$
                continue
            for a, av in ACTS.items():
                out[f"{t}_{l}_{a}"] = {"TRIG": tv, "LOAD": lv, "ACT": av}
    return out


# ---------------------------------------------------------------- build
def build(names=None):
    from host.lockstep.runner import wsl, wsl_path
    q = lambda p: shlex.quote(wsl_path(p))                       # noqa: E731
    allc = configs(1)
    code, text = wsl(f"{GCC} --version | head -1")
    toolchain = text.strip() if code == 0 else "?"
    for name in names or allc:
        out = EXE_DIR / name
        out.mkdir(parents=True, exist_ok=True)
        flags = " ".join(f"{k}={v}" for k, v in allc[name].items())
        code, text = wsl(f"cd {q(CTRL)} && make -s {flags} BUILD_DIR={q(out / 'obj')} APP_ELF={q(out / 'main.elf')} "
                         f"APP_FLT={q(out / 'elf.bin')} APP_EXE={q(out / 'neorv32_exe.bin')} clean exe")
        if code != 0 or not (out / "neorv32_exe.bin").exists():
            raise BoardError(f"build {name} failed:\n{text[-3000:]}")
        m = write_manifest(name, out, allc[name], toolchain)
        print(f"built {name}: {m['exe']['bytes']} bytes, sha256 {m['exe']['sha256'][:16]}"
              f"{', from uncommitted changes' if m['git']['dirty'] else ''}")


# ---------------------------------------------------------------- bootloader
class Link:
    """Bytes from a serial port with a look-ahead buffer (pyserial-like object:
    read, write, flush, send_break, reset_input_buffer, in_waiting, baudrate)."""

    def __init__(self, ser):
        self.ser = ser
        self.buf = bytearray()

    def wait_for(self, token, timeout, errors=True):
        """Everything up to and including token; raises on timeout or, with
        errors, on a bootloader error message (BEL + ERROR_...)."""
        t0 = time.monotonic()
        while True:
            i = self.buf.find(token)
            if i >= 0:
                out = bytes(self.buf[:i + len(token)])
                del self.buf[:i + len(token)]
                return out
            m = re.search(rb"\a(ERROR_\w+)\n", self.buf) if errors else None   # the whole line is in
            if m:
                raise BoardError(f"bootloader: {m.group(1).decode()}")
            if time.monotonic() - t0 > timeout:
                raise BoardError(f"no {token!r} within {timeout:.0f} s; last bytes {bytes(self.buf[-120:])!r}")
            self.buf += self.ser.read(max(1, self.ser.in_waiting))


def reset_to_bootloader(link):
    link.ser.reset_input_buffer()
    link.buf.clear()
    link.ser.send_break(BREAK_S)
    # after reset the bootloader tries the SPI flash, which this system does not
    # have, and prints ERROR_DEVICE before its prompt: not an error here
    link.wait_for(b"CMD:> ", 10, errors=False)


def check_bitstream(link, cache):
    link.ser.write(b"i")
    info = link.wait_for(b"CMD:> ", 5)
    m = re.search(rb"SOC:\s+0x([0-9A-Fa-f]{8})", info)
    if not m:
        raise BoardError(f"no SOC word in the system info: {info!r}")
    soc = int(m.group(1), 16)
    ic, dc = soc >> SOC_ICACHE & 1, soc >> SOC_DCACHE & 1
    if (ic, dc) != (cache, cache):
        raise BoardError(f"the board has I$ {ic} D$ {dc}, the run is for --cache {cache}: wrong bitstream")


def upload_and_start(link, exe):
    # UART0 has a 1-byte RX FIFO (IO_UART0_RX_FIFO default): the bootloader only
    # reads the stream after it has printed its whole prompt, and a byte sent
    # while it is still printing overruns the FIFO. So wait for the last byte of
    # the prompt (then the bootloader is in its read loop), plus a margin.
    link.ser.write(b"u")
    link.wait_for(b"Awaiting neorv32_exe.bin... ", 5)
    time.sleep(UPLOAD_GAP_S)
    link.ser.write(exe)
    link.wait_for(b"OK", len(exe) / (BOOT_BAUD / 10) * 1.5 + 5)
    link.wait_for(b"CMD:> ", 5)
    link.ser.write(b"e")
    # the bootloader prints this at 19200 and waits for it to go out before it
    # jumps to the program, which then switches the TX line to the monitor
    link.wait_for(b"Booting", 3)


# ---------------------------------------------------------------- monitor stream
def parse_stream(chunks, n):
    """Bytes -> the first n records. Before record k = 0 anything may arrive
    (bootloader text at the wrong baud, a cut word); from k = 0 on, records must
    follow back to back with k counting up."""
    magic = struct.pack("<I", bt.MAGIC)
    buf, started, out = bytearray(), False, []
    for chunk in chunks:
        buf += chunk
        while True:
            if not started:
                i = buf.find(magic)
                if i < 0:
                    del buf[:-3]
                    break
                del buf[:i]
                if len(buf) < 8:
                    break
                if struct.unpack_from("<I", buf, 4)[0] != 0:
                    del buf[:4]
                    continue
                started = True
            if len(buf) < 40:
                break
            words = list(struct.unpack_from("<10I", buf))
            if words[0] != bt.MAGIC:
                raise BoardError(f"lost framing after record {len(out) - 1}: {bytes(buf[:4]).hex()}")
            del buf[:40]
            r = bt.records(words)[0]
            if r.k != len(out):
                raise BoardError(f"expected record {len(out)}, got {r.k}")
            out.append(r)
            if len(out) == n:
                return out
    raise BoardError(f"the stream ended after {len(out)} records")


def serial_chunks(ser, deadline, raw=None):
    """Bytes until the deadline; a copy goes into raw (bytearray) if given."""
    while time.monotonic() < deadline:
        b = ser.read(max(1, ser.in_waiting))
        if b:
            if raw is not None:
                raw += b
            yield b
    return


def describe_raw(raw):
    """One line about what arrived, for a run that produced no records."""
    magic = struct.pack("<I", bt.MAGIC)
    n_magic = raw.count(magic)
    return (f"{len(raw)} bytes at {MON_BAUD} baud, {n_magic} NRV1 magic words; "
            f"first bytes {bytes(raw[:48]).hex(' ')}")


# ---------------------------------------------------------------- one run
def save(path, name, cache, recs, exe=None, manifest=None):
    path.mkdir(parents=True, exist_ok=True)
    with (path / "records.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["k", "release", "start", "sense", "act", "encoder", "pwm_cmd", "flags", "bg_n", "bg_cycles"])
        for r in recs:
            w.writerow([r.k, r.rel, r.start, r.sense, r.act, r.enc, r.u, r.flags, r.bg_n, r.bg_cycles])
    summary = {"name": name, "cache": cache, "config": configs(cache)[name], "git": git_describe(),
               "provenance": provenance(cache, exe, manifest), **evaluate(recs)}
    (path / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def evaluate(recs):
    """Integrity checks and statistics of one run's records."""
    seq = A.check_sequence(recs)
    rep = A.replay(recs)
    return {"integrity_ok": not seq and not rep,
            "sequence_problems": seq[:50], "n_sequence_problems": len(seq),
            "replay_mismatches": [list(x) for x in rep[:50]], "n_replay_mismatches": len(rep),
            **A.summaries(recs)}


def load_records(path):
    """records.csv -> [Record]"""
    def opt(x):
        return None if x in ("", "None") else int(x)
    with Path(path).open(newline="") as f:
        return [bt.Record(int(r["k"]), int(r["release"]), opt(r["start"]), opt(r["sense"]), opt(r["act"]),
                          int(r["encoder"]), int(r["pwm_cmd"]), int(r["flags"]))
                for r in csv.DictReader(f)]


def verdict(s):
    """One line: the integrity checks, then the deadline misses (a result, not a check)."""
    integ = "integrity OK" if s["integrity_ok"] else "INTEGRITY PROBLEMS"
    a = s["all"]
    miss = f"deadline misses {a['deadline_misses']} of {a['periods']}"
    if a["deadline_misses"]:
        miss = "*** " + miss.upper() + " ***"
    return (f"{integ} (sequence problems {s['n_sequence_problems']}, replay mismatches "
            f"{s['n_replay_mismatches']}); {miss}")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def bitstream_path(cache):
    """The bitstream nrv_build writes (fpga/vivado/nrv.tcl, nrv_bit)."""
    return ROOT / "build" / f"vivado_nrv_c{cache}" / f"nrv_c{cache}.runs" / "impl_1" / "arty_neorv32_top.bit"


def source_hashes():
    """sha256 of every file under baseline/sw as it is on disk now, tracked or not."""
    return {f.relative_to(ROOT).as_posix(): sha256(f.read_bytes())
            for f in sorted(SW_SOURCES.rglob("*")) if f.is_file()}


def changed_hashes(repo, state):
    """sha256 of every changed or untracked file of a git_state(), by path
    relative to repo; None for a file that is deleted. A commit identifies the
    committed content only; this pins what was on disk besides it."""
    out = {}
    for line in state["changed"]:
        path = _path(line)
        f = Path(repo) / path
        out[path] = sha256(f.read_bytes()) if f.is_file() else None
    return out


def write_manifest(name, out, defines, toolchain):
    """What one executable was built from, written next to it at build time and
    bound to its sha256 (audit A5-3): a run made later records this, not the
    tree as it is when the board runs. Besides the commits, the content of
    every changed or untracked file is hashed, in the repository and in the
    NEORV32 submodule (whose crt0, linker script and libraries go into every
    executable), so a local edit there is identified too."""
    exe = (out / "neorv32_exe.bin").read_bytes()
    sub = ROOT / "baseline" / "neorv32"
    repo, nrv = git_state(), git_state(sub)
    m = {"name": name, "defines": defines, "exe": {"bytes": len(exe), "sha256": sha256(exe)},
         "built": time.strftime("%Y-%m-%d %H:%M:%S"),
         "git": {**repo, "changed_sha256": changed_hashes(ROOT, repo)}, "sources": source_hashes(),
         "neorv32": {**nrv, "changed_sha256": changed_hashes(sub, nrv)}, "toolchain": toolchain}
    (out / MANIFEST).write_text(json.dumps(m, indent=1))
    return m


def load_manifest(name, exe):
    path = EXE_DIR / name / MANIFEST
    if not path.exists():
        raise BoardError(f"{name}: no build manifest; run 'py -m host.baseline.board build' again")
    m = json.loads(path.read_text())
    if m["exe"]["sha256"] != sha256(exe):
        raise BoardError(f"{name}: neorv32_exe.bin is not the executable its manifest describes "
                         "(changed after 'build'); run build again")
    return m


def provenance(cache, exe, manifest=None):
    """What a run used, so its records can be tied to one program and one source
    tree (audit A5-3): the uploaded executable, the manifest written when it was
    built, the files under baseline/sw changed since that build, the git state
    at run time, and the bitstream file on disk. The FPGA is programmed by hand,
    so the bitstream entry names the file that was there, not what is on the
    board (check_bitstream() only checks its cache setting)."""
    bit = bitstream_path(cache)
    changed = None
    if manifest is not None:
        now, then = source_hashes(), manifest["sources"]
        changed = sorted(f for f in now.keys() | then.keys() if now.get(f) != then.get(f))
    return {
        "exe": None if exe is None else {"bytes": len(exe), "sha256": sha256(exe)},
        "build": manifest,
        "sources_changed_since_build": changed,
        "run": {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "git": git_state()},
        "bitstream_file": None if not bit.exists() else {
            "path": bit.relative_to(ROOT).as_posix(), "sha256": sha256(bit.read_bytes()),
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(bit.stat().st_mtime))},
    }


def git_state(repo=None):
    """Commit and every changed or untracked file (git status --porcelain lines,
    'XY path'), ignored files left out."""
    def git(*args):
        return subprocess.run(["git", "-C", str(repo or ROOT), *args], capture_output=True, text=True,
                              timeout=10).stdout
    try:
        commit = git("rev-parse", "HEAD").strip()
        changed = git("status", "--porcelain", "--untracked-files=all").splitlines()   # keep the leading space
    except OSError:
        return {"commit": "?", "dirty": None, "changed": []}
    return {"commit": commit, "dirty": bool(changed), "changed": changed}   # complete: the manifest hashes each


SHOW_FILES = 10                  # console and report lines only; the manifest keeps every file


def _path(line):
    """The path of one 'git status --porcelain' line ('XY path', a rename
    'XY old -> new'). Runs saved by the first A5-3 version stored the first
    line stripped ('M path'), so the two status letters are not assumed."""
    path = line[3:] if len(line) > 2 and line[2] == " " else line.split(None, 1)[-1]
    return path.split(" -> ")[-1].strip('"')


def _files(lines):
    names = [_path(c) for c in lines]
    more = len(names) - SHOW_FILES
    return ", ".join(names[:SHOW_FILES]) + (f" and {more} more" if more > 0 else "")


def provenance_line(prov):
    if not prov:
        return "provenance not recorded (run made before audit A5-3)"
    exe, b, bit = prov["exe"], prov.get("build"), prov["bitstream_file"]
    if b is not None:
        g = b["git"]
        n = b.get("neorv32") or {}
        src = (f"built {b['built']} from commit {g['commit'][:10]}"
               f"{' + changes: ' + _files(g['changed']) if g['dirty'] else ''}"
               f"{', NEORV32 changes: ' + _files(n['changed']) if n.get('dirty') else ''}")
        if prov.get("sources_changed_since_build"):
            src += f" (sources changed since the build: {', '.join(prov['sources_changed_since_build'])})"
    else:                                               # run-time state only (first A5-3 version)
        g = prov.get("git") or {"commit": "?", "dirty": False, "changed": []}
        src = (f"no build manifest; at run time commit {g['commit'][:10]}"
               f"{' + changes: ' + _files(g['changed']) if g['dirty'] else ''}")
    return (f"program sha256 {exe['sha256'][:16] if exe else '?'}, {src}, "
            f"bitstream file {bit['sha256'][:16] + ' (' + bit['modified'] + ')' if bit else 'not found'}")


def git_describe():
    try:
        return subprocess.run(["git", "-C", str(ROOT), "describe", "--always", "--dirty"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except OSError:
        return "?"


def run_one(ser, name, cache, n):
    exe = (EXE_DIR / name / "neorv32_exe.bin").read_bytes()
    manifest = load_manifest(name, exe)                  # before touching the board
    ser.baudrate = BOOT_BAUD
    link = Link(ser)
    reset_to_bootloader(link)
    check_bitstream(link, cache)
    upload_and_start(link, exe)
    ser.baudrate = MON_BAUD
    ser.reset_input_buffer()
    deadline = time.monotonic() + START_DELAY_S + n * 1e-3 + 5
    raw = bytearray()
    path = OUT_DIR / f"c{cache}" / name
    try:
        recs = parse_stream(serial_chunks(ser, deadline, raw), n)
    except BoardError as e:
        path.mkdir(parents=True, exist_ok=True)
        (path / "raw_failed.bin").write_bytes(raw)
        raise BoardError(f"{e}\n  monitor stream: {describe_raw(raw)}\n  saved in {path / 'raw_failed.bin'}") from None
    return save(path, name, cache, recs, exe, manifest)


def run(port, cache, names, n):
    import serial
    names = names or list(configs(cache))
    with serial.Serial(port, BOOT_BAUD, timeout=0.05) as ser:
        for name in names:
            s = run_one(ser, name, cache, n)
            print(f"--- c{cache} {name}: {verdict(s)}")
            print(f"    {provenance_line(s['provenance'])}")
            print(A.format_summary(s["all"], "all periods (primary), "))
            print(A.format_summary(s["steady"], "steady state k >= 1, "))


def report(out=sys.stdout):
    """Tables of every saved run, recomputed from its records.csv: all periods
    (primary) and steady state (k >= 1). misses = deadline misses / periods;
    integrity = sequence check and model replay (not a timing verdict)."""
    runs, provs = [], []
    for p in sorted(OUT_DIR.glob("c*/*/records.csv")):
        recs = load_records(p)
        runs.append((p.parent.parent.name, p.parent.name, evaluate(recs)))
        js = p.parent / "summary.json"
        provs.append(json.loads(js.read_text()).get("provenance") if js.exists() else None)

    def f(x):
        return f"{x['min']}/{x['p50']}/{x['p99']}/{x['max']}" if x else "-"
    for key, title in [("all", "All periods (primary, period 0 with cold caches included)"),
                       ("steady", "Steady state (periods k >= 1)")]:
        print(f"\n{title}. Cycles, min/p50/p99/max. sense interval = consecutive SENSE minus 100,000 "
              f"(releases are exact: release interval column).", file=out)
        print("| cache | config | integrity | misses | release->start | release->sense | sense->act | "
              "release->act | sense interval | release interval |", file=out)
        print("|---|---|---|---|---|---|---|---|---|---|", file=out)
        for cache, name, s in runs:
            d = s[key]
            integ = "OK" if s["integrity_ok"] else f"seq {s['n_sequence_problems']} / replay {s['n_replay_mismatches']}"
            miss = f"{d['deadline_misses']}/{d['periods']}"
            if d["deadline_misses"]:
                miss = f"**{miss}**"
            print(f"| {cache} | {name} | {integ} | {miss} | {f(d['release_start'])} | {f(d['release_sense'])} | "
                  f"{f(d['sense_act'])} | {f(d['release_act'])} | {f(d['sense_interval'])} | "
                  f"{f(d['release_interval'])} |", file=out)
    print("\nProvenance (summary.json):", file=out)
    for (cache, name, _), prov in zip(runs, provs):
        print(f"- {cache} {name}: {provenance_line(prov)}", file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--only", nargs="*")
    r = sub.add_parser("run")
    r.add_argument("port")
    r.add_argument("--cache", type=int, choices=(0, 1), required=True)
    r.add_argument("--only", nargs="*")
    r.add_argument("--periods", type=int, default=10_000)
    sub.add_parser("report")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.only)
    elif a.cmd == "run":
        bad = [x for x in a.only or [] if x not in configs(a.cache)]
        if bad:
            ap.error(f"unknown for --cache {a.cache}: {bad}")
        run(a.port, a.cache, a.only, a.periods)
    else:
        report()


if __name__ == "__main__":
    main()
