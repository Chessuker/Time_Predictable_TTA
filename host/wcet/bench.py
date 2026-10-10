"""Generate programs/wcet_bench.tta: every Phase 4 benchmark in one image.

    py -m host.wcet.bench            rewrite programs/wcet_bench.tta

The board runs one bitstream (design decision, Phase 4). Each benchmark is a
function that reads its inputs from a fixed block in .data. For every dataset
the harness copies the inputs in, then

    #0 -> tmr.t_sync            ; anchor = s
    #bench -> pc.t_call         ; s + 1
    tmr.elapsed -> r13          ; s + 1 + (1 + P) + W + P

so the measured cycles of the function are  elapsed - (2 + 2P)  and must equal
the static entry-to-return WCET W on the worst-case dataset and never exceed it
on any other. Each run sends one record over the UART:

    MAGIC, bench id, dataset id, elapsed

and waits until anchor + PACE, so the telemetry FIFO never overflows.
Dataset 0 of every benchmark is its worst case, chosen by hand from the witness
path; the rest are random (fixed seed, so the file is reproducible).

Registers: benchmarks use r1-r9, the harness r10-r15.
"""
import random
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "programs" / "wcet_bench.tta"

MAGIC = 0x5743_5431          # "WCT1"
PACE = 20_000                # cycles from t_sync to the next run
DATASETS = 8                 # per benchmark, dataset 0 = worst case
SEED = 2026
AMAX = 2047 << 16            # PID integrator clamp


@dataclass
class Bench:
    name: str                # function label
    inputs: str              # label of the input block
    length: int              # words per dataset
    source: str              # the function, .func ... .endfunc
    data: str                # extra .data lines (input block, constants)
    worst: list[int]         # dataset 0
    rand: object             # rng -> list[int]


BENCHES = [
    Bench("fib", "fib_in", 1, """
; fib(n), 1 <= n <= 20: the loop count depends on the input
.func fib
        #fib_in     -> mem.t_load
        #0          -> r1           ; a
        mem.data_out -> r4          ; n
        #1          -> r2           ; b
        #ADD        -> alu.op
@loop_bound 20
fib_lp: r1          -> alu.a
        r2          -> alu.t_b
        r2          -> r1
        alu.out     -> r2
        r4          -> alu.a
        #-1         -> alu.t_b
        alu.out     -> r4
        r4          -> pc.cond
        #fib_lp     -> pc.t_jnz
        pc.link     -> pc.t_jump
.endfunc
""", "fib_in:     .space 1\n", [20], lambda r: [r.randint(1, 20)]),

    Bench("search", "s_key", 17, """
; linear search for s_key in s_arr[16] with an early exit; r1 = words left
.func search
        #s_key      -> mem.t_load
        mem.data_out -> cmp.a
        #s_arr      -> r2
        #16         -> r3
        #ADD        -> alu.op
@loop_bound 16
s_lp:   r2          -> mem.t_load
        mem.data_out -> cmp.t_b
        cmp.eq      -> pc.cond
        #s_hit      -> pc.t_jnz
        r2          -> alu.a
        #1          -> alu.t_b
        alu.out     -> r2
        r3          -> alu.a
        #-1         -> alu.t_b
        alu.out     -> r3
        r3          -> pc.cond
        #s_lp       -> pc.t_jnz
        #-1         -> r1
        pc.link     -> pc.t_jump
s_hit:  r3          -> r1
        pc.link     -> pc.t_jump
.endfunc
""", "s_key:      .space 1\ns_arr:      .space 16\n",
          [99] + list(range(16)),                                    # key not present
          lambda r: [r.randint(0, 20)] + [r.randint(0, 20) for _ in range(16)]),

    Bench("cpos", "c_arr", 16, """
; count the positive words of c_arr[16]; a positive word takes the longer path
.func cpos
        #0          -> r1
        #c_arr      -> r2
        #16         -> r3
        #0          -> cmp.a
        #ADD        -> alu.op
@loop_bound 16
c_lp:   r2          -> mem.t_load
        mem.data_out -> cmp.t_b     ; 0 < x ?
        cmp.lt      -> pc.cond
        #c_skip     -> pc.t_jz
        r1          -> alu.a
        #1          -> alu.t_b
        alu.out     -> r1
c_skip: r2          -> alu.a
        #1          -> alu.t_b
        alu.out     -> r2
        r3          -> alu.a
        #-1         -> alu.t_b
        alu.out     -> r3
        r3          -> pc.cond
        #c_lp       -> pc.t_jnz
        pc.link     -> pc.t_jump
.endfunc
""", "c_arr:      .space 16\n", [i + 1 for i in range(16)],
          lambda r: [r.randint(-100, 100) for _ in range(16)]),

    Bench("sort8", "b_arr", 8, """
; branch-free bubble sort of b_arr[8] (MIN/MAX): the same path for every input
.func sort8
        #7          -> r3
        #ADD        -> alu.op
@loop_bound 7
b_out:  #b_arr      -> r1
        #7          -> r2
@loop_bound 7
b_in:   r1          -> mem.t_load
        mem.data_out -> r4
        r1          -> alu.a
        #1          -> alu.t_b
        alu.out     -> r6
        r6          -> mem.t_load
        mem.data_out -> r5
        r4          -> alu.a
        #MIN        -> alu.op
        r5          -> alu.t_b
        alu.out     -> mem.data_in
        r1          -> mem.t_store
        #MAX        -> alu.op
        r5          -> alu.t_b
        alu.out     -> mem.data_in
        r6          -> mem.t_store
        r6          -> r1
        #ADD        -> alu.op
        r2          -> alu.a
        #-1         -> alu.t_b
        alu.out     -> r2
        r2          -> pc.cond
        #b_in       -> pc.t_jnz
        r3          -> alu.a
        #-1         -> alu.t_b
        alu.out     -> r3
        r3          -> pc.cond
        #b_out      -> pc.t_jnz
        pc.link     -> pc.t_jump
.endfunc
""", "b_arr:      .space 8\n", [8, 7, 6, 5, 4, 3, 2, 1],
          lambda r: [r.randint(-1000, 1000) for _ in range(8)]),

    Bench("pid", "pid_ref", 4, """
; one PID step as in host/plant_model: e clamped to +-4096, D on the measurement,
; output saturated to +-2047, conditional integration (anti-windup) and an
; integrator clamp. The clamps are MIN/MAX; only the anti-windup branches.
.func pid
        #pid_ref    -> mem.t_load
        mem.data_out -> alu.a
        #SUB        -> alu.op
        #pid_enc    -> mem.t_load
        mem.data_out -> r2          ; enc
        r2          -> alu.t_b      ; ref - enc
        #MIN        -> alu.op
        alu.out     -> alu.a
        #4096       -> alu.t_b
        #MAX        -> alu.op
        alu.out     -> alu.a
        #-4096      -> alu.t_b
        alu.out     -> r1           ; e
        #KPQ        -> mul.a
        r1          -> mul.t_b
        #pid_prev   -> mem.t_load   ; fills the MUL latency slot
        mul.out     -> r3           ; p = KPQ * e
        #SUB        -> alu.op
        r2          -> alu.a
        mem.data_out -> alu.t_b     ; enc - prev
        #KDQ        -> mul.a
        alu.out     -> mul.t_b
        #pid_acc    -> mem.t_load
        mul.out     -> r4           ; d = KDQ * (enc - prev)
        r3          -> alu.a
        r4          -> alu.t_b      ; p - d
        #ADD        -> alu.op
        alu.out     -> alu.a
        mem.data_out -> r5          ; acc
        r5          -> alu.t_b      ; p - d + acc
        alu.out     -> alu.a
        #HALF       -> alu.t_b
        #SRA        -> alu.op
        alu.out     -> alu.a
        #16         -> alu.t_b
        alu.out     -> r6           ; out before saturation
        #MIN        -> alu.op
        r6          -> alu.a
        #2047       -> alu.t_b
        #MAX        -> alu.op
        alu.out     -> alu.a
        #-2047      -> alu.t_b
        alu.out     -> r7           ; out
        r7          -> mem.data_in
        #pid_out    -> mem.t_store
        r6          -> cmp.a
        r7          -> cmp.t_b
        cmp.eq      -> pc.cond
        #p_int      -> pc.t_jnz     ; not saturated: integrate
        #0          -> cmp.a
        r1          -> cmp.t_b
        cmp.lt      -> r8           ; 0 < e
        r6          -> cmp.t_b
        #XOR        -> alu.op
        r8          -> alu.a
        cmp.lt      -> alu.t_b      ; (0 < e) xor (0 < out)
        alu.out     -> pc.cond
        #p_skip     -> pc.t_jz      ; saturated and e pushes the same way: hold
p_int:  #KIQ        -> mul.a
        r1          -> mul.t_b
        #ADD        -> alu.op
        mul.out     -> alu.a
        r5          -> alu.t_b      ; acc + KIQ * e
        #MIN        -> alu.op
        alu.out     -> alu.a
        #pid_amax   -> mem.t_load
        mem.data_out -> alu.t_b
        #MAX        -> alu.op
        alu.out     -> alu.a
        #pid_amin   -> mem.t_load
        mem.data_out -> alu.t_b
        alu.out     -> mem.data_in
        #pid_acc    -> mem.t_store
p_skip: r2          -> mem.data_in
        #pid_prev   -> mem.t_store
        pc.link     -> pc.t_jump
.endfunc
""", f"""pid_ref:    .space 1
pid_enc:    .space 1
pid_prev:   .space 1
pid_acc:    .space 1
pid_out:    .space 1
pid_amax:   .word {AMAX}
pid_amin:   .word {-AMAX}
""",
          # saturated high while e < 0: both anti-windup tests run, then integrate
          [0, 1, 101, AMAX],
          lambda r: (lambda enc: [r.randint(-5000, 5000), enc, enc + r.randint(-300, 300),
                                  r.randint(-AMAX, AMAX)])(r.randint(-5000, 5000))),

    Bench("spo", "spo_in", 1, """
; nested call: spo(x) = square(x) + 1, with the link register saved in r9
.func spo
        #spo_in     -> mem.t_load
        mem.data_out -> r1
        pc.link     -> r9
        #square     -> pc.t_call
        #ADD        -> alu.op
        r2          -> alu.a
        #1          -> alu.t_b
        alu.out     -> r2
        r9          -> pc.link
        pc.link     -> pc.t_jump
.endfunc

.func square
        r1          -> mul.a
        r1          -> mul.t_b
        nop
        mul.out     -> r2
        pc.link     -> pc.t_jump
.endfunc
""", "spo_in:     .space 1\n", [3], lambda r: [r.randint(-1000, 1000)]),
]

COPY = """
; copy r13 words from r11 to r12 (harness)
.func copy
        #ADD        -> alu.op
@loop_bound MAXCOPY
cp_lp:  r11         -> mem.t_load
        mem.data_out -> mem.data_in
        r12         -> mem.t_store
        r11         -> alu.a
        #1          -> alu.t_b
        alu.out     -> r11
        r12         -> alu.a
        #1          -> alu.t_b
        alu.out     -> r12
        r13         -> alu.a
        #-1         -> alu.t_b
        alu.out     -> r13
        r13         -> pc.cond
        #cp_lp      -> pc.t_jnz
        pc.link     -> pc.t_jump
.endfunc
"""


def datasets(seed=SEED):
    """{bench name: [dataset 0 (worst), random ...]}"""
    rng = random.Random(seed)
    return {b.name: [b.worst] + [b.rand(rng) for _ in range(DATASETS - 1)] for b in BENCHES}


def generate(seed=SEED):
    ds = datasets(seed)
    w = []
    add = w.append
    add("; GENERATED by `py -m host.wcet.bench` (host/wcet/bench.py). Do not edit.")
    add(";")
    add("; Phase 4 WCET benchmarks on the board: every function runs on each of its")
    add(f"; {DATASETS} datasets (dataset 0 = worst case) and one record is sent per run:")
    add(";   MAGIC, bench id, dataset id, elapsed      elapsed - (2 + 2P) = cycles of the function")
    add("; Read it with: py host/tools/wcet_board.py COMx")
    add("")
    add(f".equ PACE,    {PACE}")
    add(f".equ MAXCOPY, {max(b.length for b in BENCHES)}")
    add(".equ KPQ,     186_659")
    add(".equ KDQ,     1_501_851")
    add(".equ KIQ,     6_766")
    add(".equ HALF,    1 << 15")
    add("")
    add("top:")
    for i, b in enumerate(BENCHES):
        add(f"        #ds_{b.name}    -> r14           ; bench {i}: {b.name}")
        add(f"        #{DATASETS}           -> r15")
        add("        #0           -> r10")
        add(f"h_{b.name}:")
        add("        r14          -> r11")
        add(f"        #{b.inputs}      -> r12")
        add(f"        #{b.length}           -> r13")
        add("        #copy        -> pc.t_call")
        add("        #0           -> tmr.t_sync")
        add(f"        #{b.name}         -> pc.t_call")
        add("        tmr.elapsed  -> r13")
        add("        #magic       -> mem.t_load")
        add("        mem.data_out -> telem.t_push")
        add(f"        #{i}           -> telem.t_push")
        add("        r10          -> telem.t_push")
        add("        r13          -> telem.t_push")
        add("        #PACE        -> tmr.t_wait")
        add("        #ADD         -> alu.op")
        add("        r14          -> alu.a")
        add(f"        #{b.length}           -> alu.t_b")
        add("        alu.out      -> r14")
        add("        r10          -> alu.a")
        add("        #1           -> alu.t_b")
        add("        alu.out      -> r10")
        add("        r15          -> alu.a")
        add("        #-1          -> alu.t_b")
        add("        alu.out      -> r15")
        add("        r15          -> pc.cond")
        add(f"        #h_{b.name}      -> pc.t_jnz")
    add("        #top         -> pc.t_jump")
    for b in BENCHES:
        w.extend(b.source.rstrip("\n").split("\n"))
    w.extend(COPY.rstrip("\n").split("\n"))
    add("")
    add("        .data")
    add(f"magic:      .word 0x{MAGIC:08X}           ; too big for an immediate")
    for b in BENCHES:
        w.extend(b.data.rstrip("\n").split("\n"))
        add(f"ds_{b.name}:")
        for d in ds[b.name]:
            assert len(d) == b.length
            add("        .word " + ", ".join(str(x) for x in d))
    return "\n".join(w) + "\n"


def main(argv=None):
    OUT.write_text(generate(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
