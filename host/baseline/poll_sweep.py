"""The polling chunk-size sweep of docs/baseline_measurement.md section 3.5, in
simulation with the instruction trace.

    py -m host.baseline.poll_sweep                                  every selection config, N = 1..64
    py -m host.baseline.poll_sweep --runs c1:L1d:9-15 c1:L1di:5-7 c0:L1d:9-31
    py -m host.baseline.poll_sweep --runs c1:L1d:8 --periods 1000    the near-threshold rerun

Selection configurations (ASAP): c1 L1d, c1 L1di, c0 L1d. Per configuration
and period count the interrupt-mode reference is run once:
- L1d: the committed interrupt-mode program (as on the board)
- L1di: the interrupt-mode simulation reference BG_CODE=1, the same code chain
  as polling (the board's L1di program runs a straight 2048-nop block that is
  not 4-byte aligned; it stays as it is for the board results)
L0 polling gives the MTIP latency bracket per cache. Every run is also
audited against the definitions (polling.audit, chunk_accounting,
piece_accounting).

Results of one methodology only: they accumulate in
build/baseline_poll_sweep/<METHODOLOGY>/results_<periods>.json, which also
holds a fingerprint (manifest()) of everything that shapes a result: the
program sources and makefiles (compiler flags), the bench, the NEORV32
wrapper, its conversion and the traced netlists themselves (cache on and
off), the plant RTL, the build/simulation helpers, the parsers and analysis,
the NEORV32 submodule commit and the GCC, GHDL and Verilator versions. A results file whose methodology or fingerprint
differs from the current one is refused (no reuse of references or runs);
--fresh starts that file over. Selection only ever looks at runs of the
current fingerprint. table_<periods>.md shows every run, failed candidates
included.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from host.baseline import analysis as A, bustrace as bt, polling as PL       # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
METHODOLOGY = "m2-chain-aligned"   # code chain, BG_CODE=1 L1di reference, aligned loops, any N
OUT = ROOT / "build" / "baseline_poll_sweep" / METHODOLOGY
_D = "Time_Predictable_TTA.srcs/sources_1/new"
FINGERPRINT_SOURCES = [
    # the program and its build: sources, makefile (CHUNK, BG_CODE, -falign-loops),
    # baseline.mk (march, -O2, memory sizes), the PID and its generated constants
    "baseline/sw/ctrl/main.c", "baseline/sw/ctrl/entry.S", "baseline/sw/ctrl/makefile",
    "baseline/sw/common/baseline.mk", "baseline/sw/common/hil.h", "baseline/sw/pid/pid.c",
    "baseline/sw/pid/pid.h", "baseline/sw/pid/pid_const.h",
    # the simulated system: NEORV32 wrapper (CACHE_EN, TRACE_EN), its conversion,
    # the XBUS slaves, the plant, the bench with the trace printing
    "baseline/rtl/neorv32_baseline.vhd", "baseline/sim/convert.sh", "baseline/sim/neorv32.vlt",
    "baseline/rtl/xbus_hil.sv", "baseline/rtl/xbus_mem.sv", "baseline/rtl/baseline_soc.sv",
    f"{_D}/tta_pkg.sv", f"{_D}/plant_pkg.sv", f"{_D}/dc_motor_plant_mc.sv", f"{_D}/hil_env.sv",
    f"{_D}/fu_telem.sv", f"{_D}/uart_tx_word.sv", "baseline/sim/tb_baseline.sv",
    # how programs are built and simulated (make variables, plusargs, Verilator flags)
    "tests/test_baseline_ctrl.py", "tests/test_baseline_rtl.py", "host/lockstep/runner.py",
    # the analysis: parsers, statistics, replay model, sweep and selection
    "host/baseline/bustrace.py", "host/baseline/analysis.py", "host/baseline/polling.py",
    "host/baseline/poll_sweep.py", "host/hil/env.py", "host/hil/programs.py", "host/plant_model/model.py"]


def manifest():
    """What a result depends on, as {name: sha256 or version string}:
    - every file of FINGERPRINT_SOURCES (text normalised to LF)
    - the NEORV32 submodule commit (rtl, crt0, linker script, common.mk) and
      whether its tree is modified
    - the two traced netlists actually simulated (c1t, c0t: CACHE_EN frozen in
      them), converted first if their sources changed
    - the tool versions: xPack GCC, GHDL, Verilator"""
    import subprocess
    from host.lockstep.runner import wsl
    from tests.test_baseline_rtl import converted
    m = {}
    for f in FINGERPRINT_SOURCES:
        m[f] = hashlib.sha256((ROOT / f).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    sub = ROOT / "baseline" / "neorv32"
    head = subprocess.run(["git", "-C", str(sub), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(sub), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True).stdout.strip()
    m["neorv32 submodule"] = head + (" modified" if dirty else "")
    for cache in (1, 0):
        v = converted(cache, 1)
        m[f"netlist c{cache}t"] = hashlib.sha256(v.read_bytes()).hexdigest()
    code, out = wsl("~/xpack/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-gcc --version | head -1; "
                    "ghdl --version | head -1; verilator --version")
    m["tools"] = " | ".join(l.strip() for l in out.splitlines() if l.strip())
    return m


def fingerprint(m=None):
    """sha256 over the manifest (first 16 hex digits)"""
    m = manifest() if m is None else m
    h = hashlib.sha256()
    for k in sorted(m):
        h.update(k.encode() + b"\0" + m[k].encode() + b"\0")
    return h.hexdigest()[:16]


class StaleResults(Exception):
    pass


def load_results(path, periods, fp, fresh=False):
    """The results file of this methodology and fingerprint, or a new one."""
    if path.exists() and not fresh:
        res = json.loads(path.read_text())
        if res.get("methodology") != METHODOLOGY or res.get("fingerprint") != fp:
            raise StaleResults(f"{path} was made by methodology {res.get('methodology')} with sources "
                               f"{res.get('fingerprint')}, now {METHODOLOGY} / {fp}: rerun with --fresh")
        return res
    return {"methodology": METHODOLOGY, "fingerprint": fp, "periods": periods, "l_mip": {}, "reference": {},
            "runs": []}


def candidates(runs, fp):
    """N with a passing run in every selection configuration, among the runs
    of fingerprint fp only (runs of other sources are never used)."""
    by_n = {}
    for e in runs:
        if e.get("methodology") != METHODOLOGY or e.get("fingerprint") != fp:
            continue
        by_n.setdefault(e["chunk"], {})[f"c{e['cache']}:{'L1di' if e['load'] == 2 else 'L1d'}"] = e
    return sorted(n for n, d in by_n.items() if set(d) == set(SELECTION) and all(passes(x) for x in d.values()))


def passes(e):
    """A candidate run: integrity clean, no missing events, the audit clean,
    and both criteria (timing and throughput)."""
    t = e["timing"]
    return (not any(e["integrity"].values()) and t["missing_events"] == 0 and audit_ok(e)
            and e["pass_timing"] and e["pass_R"])
CHUNKS = (1, 2, 4, 8, 16, 32, 64)
SELECTION = {"c1:L1d": (1, 1), "c1:L1di": (1, 2), "c0:L1d": (0, 1)}
BUDGET = 1500                    # D - S = 2000 - 500
R_MIN = 0.98
NEAR_R = (0.975, 0.985)
NEAR_T = (1450, 1550)
START_DELAY = 20_000
BUS_END = 1_400_000              # XBUS trace over the first periods (k = 1..10 at least)


def ranges(syms, load):
    """(thrash array, code units region): the 256 pieces of the code chain
    (without its trailing wrap jump), or the board's straight code block"""
    thrash = syms.get("thrash", (0, 0))
    code = (0, 0)
    if load == 2 and "ctrl_code_pieces" in syms:
        lo = syms["ctrl_code_pieces"][0]
        code = (lo, lo + 256 * 32)
    elif load == 2:
        code = syms.get("code_block", (0, 0))
    return thrash, code


def run(cache, load, defs, periods, tag):
    """Build and simulate one program with the instruction trace."""
    from tests.test_baseline_ctrl import build, simulate        # the same build and bench as the tests
    hexfile, syms = build(tag, LOAD=load, ACT=0, START_DELAY=START_DELAY, **defs)
    thrash, code = ranges(syms, load)
    plus = (f"+itrace +tr_data={thrash[0]:x}:{thrash[1]:x} +tr_code={code[0]:x}:{code[1]:x} "
            f"+bus_end={BUS_END}")
    reqs, recs = simulate(cache, hexfile, 300_000 + (periods + 1) * A.PERIOD, bus=True, plusargs=plus, trace=1)
    itr = bt.parse_itrace((hexfile.parent / f"sim_c{cache}t.log").read_text())
    recs = recs[:periods]
    assert len(recs) == periods, (tag, len(recs))
    return recs, itr, bt.transfers(reqs), thrash, code


def l_mip(cache):
    """MTIP latency bracket from L0 polling (a poll every few cycles)."""
    recs, itr, *_ = run(cache, 0, dict(TRIG=1, CHUNK=1), 20, f"sweep_c{cache}_L0_poll")
    return PL.mip_latency(PL.detections(recs, itr))


def reference(cache, load, periods):
    defs = dict(TRIG=0, BG_CODE=1) if load == 2 else dict(TRIG=0)
    tag = f"sweep_c{cache}_L{load}_irq" + ("ref" if load == 2 else "") + f"_{periods}"
    recs, itr, trs, thrash, code = run(cache, load, defs, periods, tag)
    return {"program": "interrupt mode, BG_CODE=1 (simulation reference)" if load == 2
            else "interrupt mode (committed, as on the board)",
            "units": PL.units(recs, itr), "cache_behaviour": PL.cache_behaviour(recs, trs, thrash, code),
            "release_start": A.dist([r.start for r in recs]), "release_act": A.dist([r.act for r in recs]),
            "piece_breaks": PL.piece_accounting(itr, code[0]) if load == 2 else 0,
            "integrity": {"sequence": len(A.check_sequence(recs)), "replay": len(A.replay(recs))}}


def evaluate(cache, load, chunk, periods, ref, lm, fp):
    recs, itr, trs, thrash, code = run(cache, load, dict(TRIG=1, CHUNK=chunk), periods,
                                       f"sweep_c{cache}_L{load}_N{chunk}_{periods}")
    t = PL.timing(recs, itr, lm[1])
    u = PL.units(recs, itr)
    r = u["data_units"] / ref["units"]["data_units"]
    bad_order, bad_chunk = PL.chunk_accounting(itr, chunk, thrash_base=thrash[0])
    obs = t["release_act"]["max"] if t["release_act"] else None
    ok_t = t["missing_events"] == 0 and obs is not None and t["b"] is not None
    return {"methodology": METHODOLOGY, "fingerprint": fp,
            "cache": cache, "load": load, "chunk": chunk, "periods": periods,
            "integrity": {"sequence": len(A.check_sequence(recs)), "replay": len(A.replay(recs))},
            "timing": t, "units": u, "R": r, "cache_behaviour": PL.cache_behaviour(recs, trs, thrash, code),
            "audit": {**PL.audit(recs, itr, chunk, lm, load), "unit_order_breaks": bad_order,
                      "chunk_count_errors": bad_chunk,
                      "piece_breaks": PL.piece_accounting(itr, code[0]) if load == 2 else 0},
            "pass_timing": ok_t and obs <= BUDGET and t["b"] <= BUDGET and t["detection_bound_holds"],
            "pass_R": r >= R_MIN,
            "near": NEAR_R[0] <= r <= NEAR_R[1] or (ok_t and (NEAR_T[0] <= t["b"] <= NEAR_T[1]
                                                               or NEAR_T[0] <= obs <= NEAR_T[1]))}


def parse_runs(specs):
    """['c1:L1d:9-15', ...] -> [(config, [N...])]; empty -> every config x CHUNKS"""
    if not specs:
        return [(c, list(CHUNKS)) for c in SELECTION]
    out = []
    for s in specs:
        cfg, _, rng = s.rpartition(":")
        lo, _, hi = rng.partition("-")
        out.append((cfg, list(range(int(lo), int(hi or lo) + 1))))
    return out


def audit_ok(e):
    a = e["audit"]
    return all(a[k] == 0 for k in ("chunk_incomplete", "l_mip", "p_interval", "w_outside_section", "undetected",
                                   "missing_events", "unit_order_breaks", "chunk_count_errors", "piece_breaks"))


def table(runs):
    rows = ["| config | N | integrity | audit | release->start | release->act max | C_max | L_mip | P max | W max | "
            "B(N) | data units/period | R | code units/period | D$ fill/WB per period | I$ fill per period | "
            "timing | R >= 0.98 | near |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in sorted(runs, key=lambda e: (-e["cache"], e["load"], e["chunk"])):
        t, u, cb = e["timing"], e["units"], e["cache_behaviour"] or {}
        rs, pp = t["release_start"], cb.get("per_period", {})
        rows.append(
            f"| c{e['cache']} {'L1di' if e['load'] == 2 else 'L1d'} | {e['chunk']} | "
            f"{'OK' if not any(e['integrity'].values()) else e['integrity']} | {'OK' if audit_ok(e) else 'FAIL'} | "
            f"{rs['min']}/{rs['p50']}/{rs['p99']}/{rs['max']} | "
            f"{t['release_act']['max'] if t['release_act'] else '-'} (missing {t['missing_events']}) | {t['c_max']} | "
            f"{t['l_mip_hi']} | {t['p']['max']} | {t['w']['max']} | {t['b']} | {u['data_units']:.1f} | "
            f"{e['R']:.4f} | {u['code_units']:.1f} | {pp.get('Dfill', 0):.0f}/{pp.get('Dwb', 0):.0f} | "
            f"{pp.get('Ifill', 0):.0f} | {'pass' if e['pass_timing'] else 'FAIL'} | "
            f"{'pass' if e['pass_R'] else 'FAIL'} | {'yes' if e['near'] else ''} |")
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="*", default=[])
    ap.add_argument("--periods", type=int, default=120)
    ap.add_argument("--fresh", action="store_true", help="start this methodology's results file over")
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"results_{a.periods}.json"
    man = manifest()
    fp = fingerprint(man)
    res = load_results(path, a.periods, fp, a.fresh)
    res["manifest"] = man
    print(f"methodology {METHODOLOGY}, sources {fp}, results {path}", flush=True)
    for cache in (1, 0):
        if str(cache) not in res["l_mip"]:
            res["l_mip"][str(cache)] = PL_bracket = l_mip(cache)
            print(f"c{cache} L_mip bracket {PL_bracket}", flush=True)
    for cfg, chunks in parse_runs(a.runs):
        cache, load = SELECTION[cfg]
        if cfg not in res["reference"]:
            res["reference"][cfg] = reference(cache, load, a.periods)
            print(f"reference {cfg}: {res['reference'][cfg]['units']['data_units']:.1f} data units/period", flush=True)
        ref = res["reference"][cfg]
        for n in chunks:
            e = evaluate(cache, load, n, a.periods, ref, tuple(res["l_mip"][str(cache)]), fp)
            res["runs"] = [x for x in res["runs"] if (x["cache"], x["load"], x["chunk"]) != (cache, load, n)] + [e]
            print(f"  {cfg} N={n:<3} R={e['R']:.4f} B={e['timing']['b']} obs={e['timing']['release_act']['max']} "
                  f"audit {'OK' if audit_ok(e) else 'FAIL'} timing {'pass' if e['pass_timing'] else 'FAIL'} "
                  f"R {'pass' if e['pass_R'] else 'FAIL'}{' NEAR' if e['near'] else ''}", flush=True)
            path.write_text(json.dumps(res, indent=1, default=str))
    if fingerprint() != fp:
        raise StaleResults("sources changed during the sweep")
    res["candidates"] = candidates(res["runs"], fp)
    res["near"] = sorted({e["chunk"] for e in res["runs"] if e["near"]})
    path.write_text(json.dumps(res, indent=1, default=str))
    (OUT / f"table_{a.periods}.md").write_text(table(res["runs"]) + "\n")
    print(table(res["runs"]))
    print(f"N meeting every criterion in every selection configuration: {res['candidates']}; "
          f"near a threshold: {res['near']}")


if __name__ == "__main__":
    main()
