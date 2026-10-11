"""Timing statistics and model replay of the baseline's monitor records
(NRV1, host/baseline/bustrace.py Record), for simulation and board runs alike.

replay()  the plant model (host/hil/env.py PlantCosim, from cycle 0 with
          pwm_cmd 0 and the load window) driven by the pwm_cmd writes at the
          cycles the monitor stamped: every encoder value the program read
          must be the model's at its sense cycle, bit for bit, and every
          pwm_cmd must be FixedPID of that value and the period's setpoint.
          Both the plant and mtime start at the system reset, so the bootloader
          time before the program only adds plant steps with pwm_cmd 0.
summary() the measured quantities over a list of records, in cycles:
            release->start, release->sense, sense->act, release->act
                offsets from the period's own release (the monitor's stamps)
            release interval
                release[k+1] - release[k] - PERIOD: the hardware timer's
                period error; 0 in every record that check_sequence passes
            sense interval
                (release[k+1] + sense[k+1]) - (release[k] + sense[k]) - PERIOD,
                the time between two consecutive SENSE events minus the period
                (the plan's "period jitter": consecutive t_sense minus the
                period). Consecutive k only. With exact releases it equals
                sense[k+1] - sense[k]; it is not a jitter of the releases.
            deadline misses
                periods whose ACT is missing or at release + DEADLINE or later
            background handlers (L2): BG_IN count and cycles per period
summaries() summary() of all periods (primary: includes period 0 with its
          cold caches) and of the steady state (periods k >= 1).
"""
import math

from host.hil import env, programs as P
from host.plant_model import model as m

PERIOD = P.PERIOD
DEADLINE = P.DEADLINE


def check_sequence(recs, period=PERIOD):
    """Problems with the record stream itself: k counting from 0, releases
    exactly one period apart, events present once, no dropped words."""
    out = []
    for i, r in enumerate(recs):
        if r.k != i:
            out.append(f"record {i}: k = {r.k}")
        if i and r.rel - recs[i - 1].rel != period:
            out.append(f"k={r.k}: release {r.rel - recs[i - 1].rel} cycles after the previous one")
        if r.seen != 0b111:
            out.append(f"k={r.k}: events seen 0b{r.seen:03b}")
        if r.extra:
            out.append(f"k={r.k}: a repeated event")
        if r.dropped:
            out.append(f"k={r.k}: monitor words dropped before it")
    return out


def replay(recs):
    """[(k, field, model, board)] where the record differs from the model."""
    plant = env.PlantCosim()
    pid = m.FixedPID()
    pwm_log, bad = [], []
    for r in recs:
        if r.sense is None or r.act is None:
            bad.append((r.k, "events", None, r.seen))
            continue
        enc = plant.encoder(r.rel + r.sense, pwm_log)
        if enc != r.enc:
            bad.append((r.k, "encoder", enc, r.enc))
        u = pid.step(env.ref_at(r.k), r.enc)
        if u != r.u:
            bad.append((r.k, "pwm_cmd", u, r.u))
        pwm_log.append((r.rel + r.act, r.u))
    return bad


def dist(xs):
    """min, p50, p99, max, spread of a list of ints (p = nearest rank)."""
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)

    def p(q):                                   # nearest rank: the ceil(q n)-th smallest
        return xs[max(0, math.ceil(q * n) - 1)]
    return {"n": n, "min": xs[0], "p50": p(0.50), "p99": p(0.99), "max": xs[-1], "spread": xs[-1] - xs[0]}


def summary(recs, period=PERIOD, deadline=DEADLINE):
    ok = [r for r in recs if r.seen == 0b111]
    s = {
        "periods": len(recs),
        "release_start": dist([r.start for r in ok]),
        "release_sense": dist([r.sense for r in ok]),
        "sense_act": dist([r.act - r.sense for r in ok]),
        "release_act": dist([r.act for r in ok]),
        "release_interval": dist([b.rel - a.rel - period for a, b in zip(recs, recs[1:]) if b.k == a.k + 1]),
        "sense_interval": dist([(b.rel + b.sense) - (a.rel + a.sense) - period for a, b in zip(ok, ok[1:])
                                if b.k == a.k + 1]),
        "deadline_misses": sum(1 for r in recs if r.act is None or r.act >= deadline),
        "bg_per_period": dist([r.bg_n for r in recs]),
        "bg_cycles_per_period": dist([r.bg_cycles for r in recs]),
    }
    return s


STEADY_FROM = 1                  # steady state: period 0 (cold caches, first release) left out


def summaries(recs, period=PERIOD, deadline=DEADLINE):
    return {"all": summary(recs, period, deadline),
            "steady": summary([r for r in recs if r.k >= STEADY_FROM], period, deadline)}


ROWS = [("release_start", "release->start"), ("release_sense", "release->sense"),
        ("sense_act", "sense->act"), ("release_act", "release->act"),
        ("release_interval", "release interval"), ("sense_interval", "sense interval"),
        ("bg_per_period", "bg handlers/period"), ("bg_cycles_per_period", "bg cycles/period")]


def format_summary(s, title=""):
    rows = [f"{title}{s['periods']} periods: deadline misses {s['deadline_misses']} of {s['periods']}"]
    for key, name in ROWS:
        d = s[key]
        if d:
            rows.append(f"  {name:<18} min {d['min']:>6} p50 {d['p50']:>6} p99 {d['p99']:>6} "
                        f"max {d['max']:>6} spread {d['spread']:>6}")
    return "\n".join(rows)
