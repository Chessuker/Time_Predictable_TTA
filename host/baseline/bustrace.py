"""The XBUS trace of baseline/sim/tb_baseline.sv (+bus) and the timing monitor
words it decodes, turned into transfers and per-release windows.

    B <cycle> <bus cycle no.> <C|D|H|E> <we> <cti> <adr> <I|D>
    W <cycle> <word>

A transfer is one single access or one cache-line burst (cti 2 on every word
but the last). Kinds:
    Ifill  I$ line fill (burst, instruction fetch)
    Dfill  D$ line fill (burst read, data access; may target the code memory,
           e.g. .rodata)
    Dwb    D$ write-back (burst write)
    Ird    single instruction fetch (caches off)
    Drd    single data read, Dwr single data write (caches off)
    Hrd    xbus_hil read, Hwr xbus_hil write
"""
from dataclasses import dataclass

MAGIC = 0x4E52_5631            # "NRV1"
NONE = 0xFFFF_FFFF
HIL_MTIMECMP_LO = 0x08
HIL_MARK_IN = 0x28             # BG_IN: a background handler body starts
HIL_MARK_OUT = 0x2C            # BG_OUT: ... and ends
LINE = 64
N_INDEX = 64                   # 64 lines of 64 B in each cache (neorv32_baseline.vhd)


@dataclass(frozen=True)
class Req:
    cycle: int
    target: str                # C code, D data, H xbus_hil, E error
    we: int
    cti: int
    adr: int
    src: str                   # I instruction fetch, D data access


@dataclass(frozen=True)
class Transfer:
    cycle: int                 # first request
    kind: str
    adr: int
    words: int


@dataclass(frozen=True)
class Record:
    k: int
    rel: int
    start: int | None
    sense: int | None
    act: int | None
    enc: int
    u: int
    flags: int

    @property
    def seen(self):                # bit 0..2: start, sense, act present
        return self.flags & 0x7

    @property
    def extra(self):               # a second event of a kind
        return bool(self.flags >> 3 & 1)

    @property
    def dropped(self):             # monitor words were dropped before this record
        return bool(self.flags >> 4 & 1)

    @property
    def bg_n(self):                # BG_IN events in [release, next release), saturating at 255
        return self.flags >> 8 & 0xFF

    @property
    def bg_cycles(self):           # cycles inside background bodies in that span, saturating at 65535
        return self.flags >> 16


def s32(x):
    return x - (1 << 32) if x >> 31 else x


def records(words):
    """Monitor words -> [Record] (complete records only, in order)."""
    out = []
    for i in range(0, len(words) - 9, 10):
        w = words[i:i + 10]
        if w[0] != MAGIC:
            raise ValueError(f"word {i}: {w[0]:08x} is not a record start")
        out.append(Record(w[1], w[2] | w[3] << 32, *(None if x == NONE else x for x in w[4:7]),
                          s32(w[7]), s32(w[8]), w[9]))
    return out


def parse(text):
    """Bench output -> ([Req], [Record])."""
    reqs, words = [], []
    for line in text.splitlines():
        f = line.split()
        if not f:
            continue
        if f[0] == "B":
            reqs.append(Req(int(f[1]), f[3], int(f[4]), int(f[5]), int(f[6], 16), f[7]))
        elif f[0] == "W":
            words.append(int(f[2], 16))
    return reqs, records(words)


def transfers(reqs):
    out, cur = [], None
    for r in reqs:
        if cur is not None:
            cur[3] += 1
            if r.cti != 2:                    # the last word of the burst
                out.append(Transfer(*cur))
                cur = None
            continue
        if r.cti == 2:
            cur = [r.cycle, "Dwb" if r.we else ("Ifill" if r.src == "I" else "Dfill"), r.adr, 1]
        elif r.target == "H":
            out.append(Transfer(r.cycle, "Hwr" if r.we else "Hrd", r.adr, 1))
        else:
            out.append(Transfer(r.cycle, "Dwr" if r.we else ("Ird" if r.src == "I" else "Drd"), r.adr, 1))
    return out


def line(a):
    return a & ~(LINE - 1)


def index(a):
    return (a // LINE) % N_INDEX


def mtimecmp_write(trs, after):
    """Cycle of the first MTIMECMP_LO write at or after `after` (the control step's last bus access)."""
    return next(t.cycle for t in trs if t.cycle >= after and t.kind == "Hwr" and t.adr & 0x3F == HIL_MTIMECMP_LO)


def in_window(trs, a, b):
    return [t for t in trs if a <= t.cycle <= b]


def count(trs, kinds=("Ifill", "Dfill", "Dwb", "Ird", "Drd", "Dwr")):
    return {k: sum(1 for t in trs if t.kind == k) for k in kinds}


def bg_bodies(reqs):
    """[(BG_IN cycle, BG_OUT cycle)] from the xbus_hil writes of the trace"""
    ins = [r.cycle for r in reqs if r.target == "H" and r.we and r.adr & 0x3F == HIL_MARK_IN]
    outs = [r.cycle for r in reqs if r.target == "H" and r.we and r.adr & 0x3F == HIL_MARK_OUT]
    return list(zip(ins, outs))


def dirty_fills(trs, fills, gap=60):
    """Fills that directly follow a write-back of the same index (a dirty eviction)."""
    wbs = [t for t in trs if t.kind == "Dwb"]
    return [f for f in fills if any(index(w.adr) == index(f.adr) and f.cycle - gap <= w.cycle < f.cycle for w in wbs)]
