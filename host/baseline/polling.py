"""Polling-mode quantities of docs/baseline_measurement.md section 3.4, from a
simulation run with the instruction trace (bustrace.ITrace), the monitor
records and, for a stretch of periods, the XBUS trace.

All cycles are the bench's cycle count (= mtime). A poll is the retirement of
the CSR read of mip; its value's bit 7 is MTIP.
"""
import bisect

from host.baseline import analysis as A, bustrace as bt

MTIP = 1 << 7
CSR_MSTATUS = 0x300
F3_CSRRCI, F3_CSRSI = 0b111, 0b110


def chunk_intervals(itr):
    """Q_j = a_(j+1) - a_j for consecutive polls with no critical section
    between them (the earlier poll read MTIP = 0)."""
    p = itr.polls
    return [b[0] - a[0] for a, b in zip(p, p[1:]) if not a[1] & MTIP]


def detections(recs, itr):
    """Per record: (record, a_prev, a_d). a_d is the first poll at or after the
    release that reads MTIP = 1 (the detecting poll); a_prev is the poll
    immediately before a_d. a_prev read 0, but it is not necessarily before
    the release: a poll in [release, release + L_mip) still reads 0."""
    cyc = [c for c, _ in itr.polls]
    out = []
    for r in recs:
        i = bisect.bisect_left(cyc, r.rel)
        while i < len(cyc) and not itr.polls[i][1] & MTIP:
            i += 1
        if i == len(cyc):
            out.append((r, None, None))
            continue
        out.append((r, cyc[i - 1] if i else None, cyc[i]))
    return out


def mip_latency(dets):
    """(L_lo, L_hi): a poll retiring at release + L_lo - 1 or earlier read 0,
    one at release + L_hi read 1, so L_lo <= L_mip <= L_hi. None where no
    poll constrains the bound."""
    lo = [a_prev - r.rel + 1 for r, a_prev, a_d in dets if a_prev is not None and a_prev >= r.rel]
    hi = [a_d - r.rel for r, a_prev, a_d in dets if a_d is not None]
    return (max(lo) if lo else None, min(hi) if hi else None)


def critical_sections(itr):
    """[(begin, end, old_mie)]: each csrrci on mstatus (begin, with the MIE bit
    it read) and the csrsi on mstatus that follows it before the next csrrci
    (end; None if there is none, as when MIE was 0 and nothing is restored)."""
    out = []
    for c, _pc, csr, cmd, old in itr.csr:
        if csr != CSR_MSTATUS:
            continue
        f3 = cmd >> 12 & 7
        if f3 == F3_CSRRCI:
            out.append([c, None, old >> 3 & 1])
        elif f3 == F3_CSRSI and out and out[-1][1] is None:
            out[-1][1] = c
    return [tuple(x) for x in out]


def per_period_counts(recs, cycles, period=A.PERIOD, first=1):
    """How many of the sorted cycles fall in [rel_k, rel_k + period) for k >= first."""
    out = []
    for r in recs:
        if r.k < first:
            continue
        out.append(bisect.bisect_left(cycles, r.rel + period) - bisect.bisect_left(cycles, r.rel))
    return out


def audit(recs, itr, chunk, l_mip, load):
    """Checks of the intervals behind C_max, L_mip, P and W on every record, from
    the retirement trace (docs/baseline_measurement.md 3.4). Returns a dict of
    counts of violations (all 0 when the definitions hold) and coverage data.

    a_d is the detecting poll, a_prev the poll immediately before it (detections()).

    - chunk completion: exactly CHUNK units retired between a_prev and a_d (for
      LOAD >= 1): the chunk polled at a_d completed, final unit included. When
      a_prev <= release this is the chunk that was running at the release; when
      a_prev > release (a poll inside [release, release + L_mip) read 0), the
      chunk running at the release ended at a_prev and the one checked here
      started after the release. Either way a_d - release = (a_d - a_prev) +
      (a_prev - release) < Q + L_mip <= C_max + L_mip.
    - L_mip: a_prev < release + L_mip_hi (it read 0), and every poll at or
      after release + L_mip_hi up to START read 1
    - P: no unit and no poll between a_d and START
    - W: START and ACT inside the critical section (after its csrrci, before its
      csrsi when MIE was on)
    - a_prev_after_release: how many records had a_prev > release (coverage
      of that case); release_position: (release - a_prev) / (a_d - a_prev) in
      per mille, negative when a_prev > release
    - missing_events: records without START or ACT (not audited further)"""
    lo, hi = l_mip
    stores = [c for c, _ in itr.stores]
    polls = itr.polls
    pc = [c for c, _ in polls]
    secs = critical_sections(itr)
    sc = [b for b, _, _ in secs]
    v = {"chunk_incomplete": 0, "l_mip": 0, "p_interval": 0, "w_outside_section": 0, "undetected": 0,
         "missing_events": 0, "a_prev_after_release": 0}
    pos = []
    for r, a_prev, a_d in detections(recs, itr):
        if r.start is None or r.act is None:
            v["missing_events"] += 1
            continue
        if a_d is None or a_prev is None:
            v["undetected"] += 1
            continue
        if a_prev > r.rel:
            v["a_prev_after_release"] += 1
        start, act = r.rel + r.start, r.rel + r.act
        n_units = bisect.bisect_left(stores, a_d) - bisect.bisect_right(stores, a_prev)
        if load >= 1 and n_units != chunk:
            v["chunk_incomplete"] += 1
        if not a_prev < r.rel + hi:
            v["l_mip"] += 1
        i0, i1 = bisect.bisect_left(pc, r.rel + hi), bisect.bisect_left(pc, start)
        if any(not polls[i][1] & MTIP for i in range(i0, i1)):
            v["l_mip"] += 1
        if bisect.bisect_right(stores, a_d) != bisect.bisect_left(stores, start) or \
                bisect.bisect_right(pc, a_d) != bisect.bisect_left(pc, start):
            v["p_interval"] += 1
        k = bisect.bisect_right(sc, a_d)
        if k >= len(secs):
            v["w_outside_section"] += 1
        else:
            b, e, mie = secs[k]
            if not (a_d < b < start and (e is None or act < e)):
                v["w_outside_section"] += 1
        pos.append((r.rel - a_prev) / (a_d - a_prev))
    v["release_position"] = A.dist([round(1000 * x) for x in pos])      # per mille of the chunk
    return v


def chunk_accounting(itr, chunk, n_lines=256, thrash_base=None):
    """The unit sequence: every store goes to the next line (64 B on, wrapping
    after n_lines), and between two consecutive polls with no critical section
    exactly `chunk` units retire. Returns (bad order count, bad chunk count).
    Stores before the first poll are left out (crt0 clears the array in .bss)."""
    first = itr.polls[0][0] if itr.polls else 0
    st = [s for s in itr.stores if s[0] > first]
    bad_order = 0
    for (_, a), (_, b) in zip(st, st[1:]):
        if thrash_base is not None:
            ia, ib = (a - thrash_base) // 64, (b - thrash_base) // 64
            if ib != (ia + 1) % n_lines:
                bad_order += 1
    cyc = [c for c, _ in st]
    p = itr.polls
    bad_chunk = 0
    for (a, va), (b, _) in zip(p, p[1:]):
        if va & MTIP:
            continue
        n = bisect.bisect_left(cyc, b) - bisect.bisect_right(cyc, a)
        if n != chunk:
            bad_chunk += 1
    return bad_order, bad_chunk


def piece_accounting(itr, code_base, n_pieces=256):
    """Code pieces follow the units: consecutive piece starts are consecutive
    pieces (mod 256). Returns the number of breaks in the sequence."""
    ks = [(pc - code_base) // 32 for _, pc in itr.pieces]
    return sum(1 for a, b in zip(ks, ks[1:]) if b != (a + 1) % n_pieces)


def timing(recs, itr, l_mip_hi, period=A.PERIOD):
    """The terms of B(N) and the observed release->act of one polling run.
    Records without START or ACT are counted (missing_events) and left out of
    the distributions; they are deadline misses."""
    """The terms of B(N) and the checks of section 3.4 for one polling run."""
    q = chunk_intervals(itr)
    dets = detections(recs, itr)
    c_max = max(q) if q else 0
    full = [r for r in recs if r.start is not None and r.act is not None]
    p = [r.rel + r.start - a_d for r, _, a_d in dets if a_d is not None and r.start is not None]
    w = [r.act - r.start for r in full]
    det_delay = [a_d - r.rel for r, _, a_d in dets if a_d is not None]
    b = c_max + l_mip_hi + max(p) + max(w) if p and w else None
    return {
        "missing_events": len(recs) - len(full),
        "chunk_interval": A.dist(q),
        "c_max": c_max,
        "l_mip_hi": l_mip_hi,
        "detection_delay": A.dist(det_delay),
        "detection_bound_holds": all(d <= c_max + l_mip_hi for d in det_delay) and len(det_delay) == len(recs),
        "p": A.dist(p),
        "w": A.dist(w),
        "b": b,
        "release_start": A.dist([r.start for r in recs if r.start is not None]),
        "release_act": A.dist([r.act for r in full]),
        "deadline_misses": sum(1 for r in recs if r.act is None or r.act >= A.DEADLINE),
    }


def units(recs, itr, period=A.PERIOD):
    """Mean data units and code units per warm period (k >= 1)."""
    d = per_period_counts(recs, [c for c, _ in itr.stores], period)
    k = per_period_counts(recs, [c for c, _ in itr.pieces], period)
    n = len(d)
    return {"periods": n, "data_units": sum(d) / n, "code_units": sum(k) / n,
            "data_units_min": min(d), "data_units_max": max(d)}


def cache_behaviour(recs, trs, thrash, code, period=A.PERIOD):
    """From the XBUS transfers of the traced periods (k >= 1 whose whole
    period is in the trace): per-period counts, control-window counts, and the
    sets of thrash lines filled and written back and code lines filled."""
    if not trs:
        return None
    last = trs[-1].cycle
    ks = [r for r in recs if r.k >= 1 and r.rel + period <= last]
    if not ks:
        return None
    lo, hi = ks[0].rel, ks[-1].rel + period
    win = [t for t in trs if lo <= t.cycle < hi]

    def in_rng(a, rng):
        return rng[0] <= a < rng[1]
    dfill_t = [t for t in win if t.kind == "Dfill" and in_rng(t.adr, thrash)]
    dwb_t = [t for t in win if t.kind == "Dwb" and in_rng(t.adr, thrash)]
    ifill_c = [t for t in win if t.kind == "Ifill" and in_rng(t.adr, code)]
    drd_t = [t for t in win if t.kind == "Drd" and in_rng(t.adr, thrash)]
    dwr_t = [t for t in win if t.kind == "Dwr" and in_rng(t.adr, thrash)]
    ird_c = [t for t in win if t.kind == "Ird" and in_rng(t.adr, code)]
    # control window from the release (as in interrupt mode) and, separately,
    # from START (polling: the release window also holds the rest of the chunk)
    ctrl = {"Ifill": 0, "Dfill": 0, "Dwb": 0}
    ctrl_start = {"Ifill": 0, "Dfill": 0, "Dwb": 0}
    for r in ks:
        end = bt.mtimecmp_write(trs, r.rel) + 40
        for t in bt.in_window(trs, r.rel, end):
            if t.kind in ctrl:
                ctrl[t.kind] += 1
                if r.start is not None and t.cycle >= r.rel + r.start:
                    ctrl_start[t.kind] += 1
    n = len(ks)
    return {
        "periods": n,
        "per_period": {k: v / n for k, v in bt.count(win).items()},
        "control_window_per_period": {k: v / n for k, v in ctrl.items()},
        "control_window_from_start_per_period": {k: v / n for k, v in ctrl_start.items()},
        "thrash_lines_filled": len({bt.line(t.adr) for t in dfill_t}),
        "thrash_lines_written_back": len({bt.line(t.adr) for t in dwb_t}),
        "code_lines_filled": len({bt.line(t.adr) for t in ifill_c}),
        "thrash_dfill": len(dfill_t), "thrash_dwb": len(dwb_t), "code_ifill": len(ifill_c),
        "thrash_single_rd": len(drd_t), "thrash_single_wr": len(dwr_t), "code_single_fetch": len(ird_c),
    }


def gptmr_audit(bodies, secs, gptmr_period, first_cycle=0, max_entry=200):
    """Background requests around critical sections (L2 polling), from the
    observed bodies only. bodies = [(BG_IN, BG_OUT)], secs =
    critical_sections(). The GPTMR runs freely, so request k comes at
    T0 + k * gptmr_period; T0 is estimated from the observed bodies as
    min(BG_IN_k - k * period), which assumes no request before the first
    observed body is missing.

    What it can show, inside the window [first observed BG_IN, last observed
    BG_IN] only:
    - inside: bodies starting inside a section (must be 0: interrupts are off)
    - late_after_section: requests estimated inside a section whose body did
      not start within max_entry cycles after the section's end
    - gaps: consecutive BG_IN further apart than 1.5 periods (a lost request
      between them) or closer than 0.5 periods (a duplicate)
    - count_error: bodies in the window minus round(window / period) + 1 (a
      loss or duplicate between the first and the last body)
    What it cannot show: a request lost before the first observed body or
    after the last one (the window starts and ends at observed bodies), or a
    lost request together with a duplicate in the same interval. Sections
    outside the window are reported as sections_outside_window."""
    ins = [i for i, _ in bodies if i >= first_cycle]
    if not ins:
        return {"inside": 0, "late_after_section": 0, "gaps": 0, "deferred": 0, "count_error": 0,
                "window": None, "sections_outside_window": len(secs)}
    t0 = min(c - k * gptmr_period for k, c in enumerate(ins))
    closed = [(b, e) for b, e, _ in secs if e is not None]
    inside = sum(1 for i in ins for b, e in closed if b <= i <= e)
    late = deferred = 0
    for k, c in enumerate(ins):
        tick = t0 + k * gptmr_period
        for b, e in closed:
            if b <= tick <= e:
                deferred += 1
                if not e < c <= e + max_entry:
                    late += 1
    gaps = sum(1 for a, b in zip(ins, ins[1:]) if not 0.5 * gptmr_period < b - a < 1.5 * gptmr_period)
    count_error = len(ins) - (round((ins[-1] - ins[0]) / gptmr_period) + 1)
    outside = sum(1 for b, e, _ in secs if b < ins[0] or (e if e is not None else b) > ins[-1])
    return {"inside": inside, "late_after_section": late, "gaps": gaps, "deferred": deferred,
            "count_error": count_error, "window": (ins[0], ins[-1]), "sections_outside_window": outside}
