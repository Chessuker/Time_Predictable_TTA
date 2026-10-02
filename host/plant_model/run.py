"""Run the closed-loop scenarios, print metrics and bit widths, save plots to out/."""
import os

import numpy as np

import model as m

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

TAU_LOAD = 0.01   # N m, about 12% of stall torque at 12 V

SCENARIOS = {
    # name: (periods, ref(k), tau(k))
    "step_100": (300, lambda k: 100, lambda k: 0.0),
    "step_1rev": (400, lambda k: 4096, lambda k: 0.0),
    "step_10rev": (800, lambda k: 40960, lambda k: 0.0),
    "step_neg_10rev": (800, lambda k: -40960, lambda k: 0.0),
    "load_1rev": (1000, lambda k: 4096, lambda k: TAU_LOAD if k >= 400 else 0.0),
}

PHASES = (0, 2_500, 5_000, 9_999)


def metrics(t, enc, u, ref):
    err = ref - enc
    last = enc[-50:]
    over = (np.max(enc) - ref) / ref * 100 if ref > 0 else (np.min(enc) - ref) / ref * 100
    band = max(2.0, 0.02 * abs(ref))
    outside = np.nonzero(np.abs(err) > band)[0]
    settle = t[outside[-1] + 1] if len(outside) and outside[-1] + 1 < len(t) else (
        float("nan") if len(outside) else t[0])
    return {
        "overshoot_%": max(over, 0.0),
        "settle_ms": settle * 1e3,
        "ss_err_max": float(np.max(np.abs(ref - last))),
        "sat_%": float(np.mean(np.abs(u) >= m.U_MAX - 0.5) * 100),
    }


def run():
    os.makedirs(OUT, exist_ok=True)
    print("== gains ==")
    print(f"KP={m.KP:.6f} KD={m.KD:.6f} KI={m.KI:.6f}  (LSB per count, per sample)")
    print(f"Q.{m.SH}: KPQ={m.KPQ} KDQ={m.KDQ} KIQ={m.KIQ}")
    for name, g, q in (("KP", m.KP, m.KPQ), ("KD", m.KD, m.KDQ), ("KI", m.KI, m.KIQ)):
        print(f"  {name} quantization error {abs(q / (1 << m.SH) - g) / g * 100:.3f}%")

    print("\n== stability (linear, lifted to 1 ms) ==")
    for ph in PHASES:
        rho = max(abs(np.linalg.eigvals(m.lifted_matrix(ph))))
        print(f"  phase {ph:5d}: spectral radius {rho:.5f}")

    widths = m.WidthLog()
    rows = []
    clamp_changes = []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        plt = None

    for name, (n, ref_fn, tau_fn) in SCENARIOS.items():
        ref = ref_fn(1)
        worst_dev = 0.0
        for ph in PHASES:
            tf, ef, uf, thf = m.simulate(m.FloatPlant(), m.FloatPID(), n, ref_fn, tau_fn, ph)
            lg = m.WidthLog()
            tq, eq, uq, thq = m.simulate(m.FixedPlant(lg), m.FixedPID(lg), n, ref_fn, tau_fn, ph)
            widths.merge(lg)
            worst_dev = max(worst_dev, float(np.max(np.abs(eq - ef))))
            # the error clamp must not change behavior
            _, eu, uu, _ = m.simulate(m.FixedPlant(), m.FixedPID(e_clamp=1 << 40),
                                      n, ref_fn, tau_fn, ph)
            if not (np.array_equal(eu, eq) and np.array_equal(uu, uq)):
                clamp_changes.append(f"{name}@{ph}")
            if ph == 0:
                mf, mq = metrics(tf, ef, uf, ref), metrics(tq, eq, uq, ref)
                if plt:
                    fig, ax = plt.subplots(2, 1, sharex=True, figsize=(8, 6))
                    ax[0].plot(tf * 1e3, ef, label="float")
                    ax[0].plot(tq * 1e3, eq, "--", label="fixed")
                    ax[0].axhline(ref, color="gray", lw=0.5)
                    ax[0].set_ylabel("encoder (counts)")
                    ax[0].legend()
                    ax[1].plot(tf * 1e3, uf, label="float")
                    ax[1].plot(tq * 1e3, uq, "--", label="fixed")
                    ax[1].set_ylabel("pwm_cmd (LSB)")
                    ax[1].set_xlabel("time (ms)")
                    fig.suptitle(name)
                    fig.tight_layout()
                    fig.savefig(os.path.join(OUT, f"{name}.png"), dpi=110)
                    plt.close(fig)
        rows.append((name, mf, mq, worst_dev))

    print("\n== closed loop (phase 0), float | fixed ==")
    print(f"{'scenario':16s} {'overshoot%':>17s} {'settle ms':>15s} {'ss err':>11s} {'sat%':>11s} {'max|fix-flt|':>12s}")
    for name, mf, mq, dev in rows:
        print(f"{name:16s} {mf['overshoot_%']:7.2f} | {mq['overshoot_%']:6.2f}"
              f" {mf['settle_ms']:6.0f} | {mq['settle_ms']:6.0f}"
              f" {mf['ss_err_max']:4.0f} | {mq['ss_err_max']:4.0f}"
              f" {mf['sat_%']:4.1f} | {mq['sat_%']:4.1f}"
              f" {dev:12.0f}")
    print("  (max|fix-flt| = worst encoder difference over all phases, counts)")
    print(f"\nE_CLAMP={m.E_CLAMP} changes behavior in: {clamp_changes or 'none'}")

    print("\n== signed bit widths seen (all scenarios, all phases) ==")
    for k in sorted(widths.max):
        print(f"  {k:28s} {widths.max[k]:3d}")


if __name__ == "__main__":
    run()
