"""Cycle-exact model of the board input interface (board_din.sv, Phase 5
switch mode): a 2-FF synchroniser and a vector debounce per input group, and
the BTN0 commit that copies the debounced sw[1:0] into din[1:0].

Registers update at the end of a cycle, so "x(t)" is the value during cycle t.
Per group, with s2 the synchroniser output:

    s1(t+1) = pin(t)             s2(t+1) = s1(t)
    s2(t) != cand(t):  cand(t+1) = s2(t), cnt(t+1) = 0
    else if cnt(t) == N_DB - 1:  out(t+1) = cand(t)           (cnt stays)
    else:  cnt(t+1) = cnt(t) + 1
    all registers are 0 after reset (cycle 0)

From this (tests/test_board_din.py checks each one):
  - a pin value held for d cycles starting at cycle p reaches s2 for cycles
    p+2 .. p+d+1, and is accepted iff d >= N_DB + 1; out then changes at
    p + N_DB + 3 (one cycle later if the asynchronous edge resolves late,
    which this synchronous model does not show)
  - out holds every value for at least N_DB + 1 cycles
  - commit: din[1:0](t+1) = sw_db(t) when btn_db rises (btn_db(t) = 1 and
    btn_db(t-1) = 0), so two commits are at least 2(N_DB + 1) cycles apart

pins are scripts of (cycle, value) changes, in increasing cycle order, value
from that cycle on; before the first entry the pin is 0.
"""


def _at(script, t):
    v = 0
    for c, x in script:
        if c > t:
            break
        v = x
    return v


def debounce_cycles(script, n_db, n_cycles):
    """Register-by-register model, out(t) for t in 0..n_cycles-1. Slow; it is
    the reference the fast model and the RTL are compared with."""
    s1 = s2 = cand = cnt = out = 0
    res = []
    for t in range(n_cycles):
        res.append(out)
        pin = _at(script, t)
        if s2 != cand:
            n_cand, n_cnt, n_out = s2, 0, out
        elif cnt == n_db - 1:
            n_cand, n_cnt, n_out = cand, cnt, cand
        else:
            n_cand, n_cnt, n_out = cand, cnt + 1, out
        s1, s2, cand, cnt, out = pin, s1, n_cand, n_cnt, n_out
    return res


def debounce(script, n_db, end=None):
    """Same as debounce_cycles, as changes: [(cycle, value)] of out, starting
    with (0, 0). Works on long scripts (N_DB = 500 000)."""
    # s2 is the pin delayed by 2 cycles; split it into constant segments
    segs = []
    for c, v in script:
        c += 2
        if segs and segs[-1][0] == c:
            segs.pop()
        if not segs or segs[-1][1] != v:
            segs.append((c, v))
    if not segs or segs[0][0] > 0:
        segs.insert(0, (0, 0))
    out, changes = 0, [(0, 0)]
    for i, (c, v) in enumerate(segs):
        nxt = segs[i + 1][0] if i + 1 < len(segs) else None
        length = None if nxt is None else nxt - c
        if v != out and (length is None or length >= n_db + 1):
            at = c + n_db + 1
            if end is None or at < end:
                changes.append((at, v))
                out = v
    return changes


def rising_edges(changes):
    """Cycles at which a 1-bit signal given as changes goes 0 -> 1."""
    prev, edges = 0, []
    for c, v in changes:
        if v and not prev:
            edges.append(c)
        prev = v
    return edges


def interface(sw, btn, n_db, end=None):
    """Core input io.din as changes [(cycle, value)]: din[1:0] = sw[1:0] committed
    by BTN0, din[2] = debounced sw[2]. sw is a script of 3-bit values, btn of
    0/1. Groups: sw[1:0], sw[2] and btn[0] are debounced independently."""
    sw10 = debounce([(c, v & 3) for c, v in sw], n_db, end)
    sw2 = debounce([(c, v >> 2 & 1) for c, v in sw], n_db, end)
    btn_db = debounce(btn, n_db, end)
    din10 = [(0, 0)]
    for e in rising_edges(btn_db):
        v = _at(sw10, e)
        if v != din10[-1][1] and (end is None or e + 1 < end):
            din10.append((e + 1, v))
    cycles = sorted({c for c, _ in din10} | {c for c, _ in sw2})
    out = []
    for c in cycles:
        v = _at(din10, c) | _at(sw2, c) << 2
        if not out or out[-1][1] != v:
            out.append((c, v))
    return out
