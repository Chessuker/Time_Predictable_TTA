"""The XBUS memory latency rule on the whole baseline system (audit A5-1).

xbus_mem charges FIRST_LAT to the first access of every transaction and
BURST_LAT only to the further words of a burst; an access continues a burst
when the previous access of the same bus cycle to the same memory had
cti = 010 (tb_xbus_mem checks the rule on the module). Here the real NEORV32
drives it: in the bench's bus trace the next strobe can come at the earliest
in the acknowledge cycle of the previous access, so the gap between two
strobes of one bus cycle must be at least the latency of the first.

Before the fix the memory went by cyc alone, and with the caches off about
half of the strobes followed the previous one by 4 cycles instead of >= 10.
"""
import collections

import pytest

from .test_baseline_ctrl import build, simulate, START_DELAY
from .test_lockstep import pytestmark  # noqa: F401  (same skip rule)

FIRST_LAT, BURST_LAT = 10, 1          # baseline_soc defaults, as on the board
CYCLES = 150_000                       # start-up and the first periods of the caches-on runs


def strobe_gaps(log_text):
    """[(gap to the next strobe of the same bus cycle, latency of this access, cti)]
    for every code/data access that has a next strobe in its bus cycle."""
    prev, out = None, []
    for line in log_text.splitlines():
        if not line.startswith("B "):
            continue
        f = line.split()
        cyc, bc, tgt, cti = int(f[1]), int(f[2]), f[3], int(f[5])
        cont = prev is not None and prev[1] == bc and prev[2] == tgt and prev[3] == 0b010
        cur = (cyc, bc, tgt, cti, BURST_LAT if cont else FIRST_LAT)
        if prev is not None and prev[1] == bc and prev[2] in "CD":
            out.append((cyc - prev[0], prev[4], prev[3]))
        prev = cur
    return out


@pytest.mark.parametrize("cache", [0, 1])
def test_every_transaction_pays_first_lat(cache):
    hexfile, _ = build("xbus_latency", TRIG=0, LOAD=1, ACT=0, START_DELAY=START_DELAY)
    simulate(cache, hexfile, CYCLES)
    gaps = strobe_gaps((hexfile.parent / f"sim_c{cache}.log").read_text())
    kinds = collections.Counter("first" if lat == FIRST_LAT else "burst" for _, lat, _ in gaps)
    assert kinds["first"] > 1000                          # many accesses share a bus cycle
    assert cache == 0 or kinds["burst"] > 1000            # and line fills with the caches on
    short = [(g, lat) for g, lat, _ in gaps if g < lat]
    assert short == [], short[:10]


def test_the_rule_catches_the_old_behaviour():
    # a bus cycle with two single accesses 4 cycles apart, as before the fix
    log = "B 100 7 C 0 0 00000000 I\nB 104 7 C 0 0 00000004 I\n"
    assert strobe_gaps(log) == [(4, FIRST_LAT, 0)]
    # a 2-word burst, then a single in its ack cycle: the last word of the burst
    # (cti 000) is still a burst word, so 1 cycle after it is fine
    log = "B 100 7 C 0 2 00000000 I\nB 110 7 C 0 0 00000004 I\nB 111 7 C 0 0 00000040 I\n"
    assert strobe_gaps(log) == [(10, FIRST_LAT, 2), (1, BURST_LAT, 0)]
