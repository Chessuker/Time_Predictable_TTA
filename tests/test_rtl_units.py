"""Unit tests of RTL blocks outside the lockstep core, plus a lint of the board top."""
import shlex

import pytest

from host.lockstep.runner import BOARD_SOURCES, BUILD, DESIGN_DIR, ROOT, SIM_DIR, wsl, wsl_path

from .test_lockstep import pytestmark  # noqa: F401  (same skip rule)


def test_uart_tx_word():
    obj = BUILD / "verilator_uart"
    obj.mkdir(parents=True, exist_ok=True)
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && "
           f"verilator --binary --timing --timescale 1ns/1ps -Wall -Wno-fatal --top-module tb_uart "
           f"--Mdir {shlex.quote(wsl_path(obj))} -o Vtb_uart {DESIGN_DIR}/uart_tx_word.sv {SIM_DIR}/tb_uart.sv "
           f"&& {shlex.quote(wsl_path(obj / 'Vtb_uart'))}")
    code, out = wsl(cmd)
    assert code == 0, out[-4000:]
    assert "%Warning" not in out, out[-4000:]
    assert "PASS uart_tx_word" in out and "FAIL" not in out, out[-4000:]


def test_board_top_lints_clean():
    srcs = BOARD_SOURCES
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && verilator --lint-only -Wall "
           f"--top-module arty_tta_top -I{DESIGN_DIR} {' '.join(srcs)}")
    code, out = wsl(cmd)
    assert code == 0 and "%Warning" not in out, out[-4000:]


def test_telemetry_word_time_matches_uart():
    from host.common import spec
    assert spec.TELEM_CYCLES_PER_WORD % 40 == 0          # 4 bytes x 10 bits, integer clocks per bit


# tta_core -> FIFO -> UART through arty_tta_top (PR #3 review): a lone word,
# then a 70-word burst that fills the FIFO, sends back to back and drops 5.
BOARD_TELEM = ("#0 -> tmr.t_sync\n#0x5A -> telem.t_push\n#3000 -> tmr.t_wait\n"
               + "".join(f"#{0x100 + i} -> telem.t_push\n" for i in range(70))
               + "#0 -> trap.t_halt\n")


def test_board_telemetry_matches_iss():
    from host.asm import assemble, output_files
    from host.common import spec
    from host.iss import Simulator

    prog = assemble(BOARD_TELEM, "board_telem.tta")
    sim = Simulator(prog.code, prog.data, None, prog.imem_words, prog.dmem_words)
    sim.run(10_000)
    assert sim.telem.drops == 70 - spec.TELEM_FIFO_WORDS - 1

    # sec. 6.5 of toolchain_formats.md: a word pushed at p is popped at
    # max(UART free, p + 1); the line drops to the start bit one cycle later.
    expected, free = [], 0
    for c, word in sim.telem.accepted:
        pop = max(free, c + 1)
        free = pop + spec.TELEM_CYCLES_PER_WORD
        expected.append((pop + 1, word & 0xFFFF_FFFF))

    work = BUILD / "board_tb"
    work.mkdir(parents=True, exist_ok=True)
    for suffix, text in output_files(prog).items():
        (work / f"board_telem{suffix}").write_text(text, encoding="utf-8", newline="\n")
    imem, dmem = (shlex.quote(f'"{wsl_path(work / f"board_telem.{k}.hex")}"') for k in ("code", "data"))
    obj = work / "obj"
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && "
           f"verilator --binary --timing --timescale 1ns/1ps -j 0 -Wall -Wno-fatal --top-module tb_board "
           f"-I{DESIGN_DIR} -GIMEM_INIT={imem} -GDMEM_INIT={dmem} "
           f"--Mdir {shlex.quote(wsl_path(obj))} -o Vtb_board {' '.join(BOARD_SOURCES)} {SIM_DIR}/tb_board.sv "
           f"&& {shlex.quote(wsl_path(obj / 'Vtb_board'))} +max={free + 100}")
    code, out = wsl(cmd)
    assert code == 0 and "%Warning" not in out, out[-4000:]
    assert "END" in out and "FAIL" not in out, out[-4000:]

    got = [(int(f[1]), int(f[2], 16)) for f in (l.split() for l in out.splitlines()) if f[:1] == ["W"]]
    assert got == expected                         # every word, value and start cycle
    gaps = {b[0] - a[0] for a, b in zip(got[1:], got[2:])}
    assert gaps == {spec.TELEM_CYCLES_PER_WORD}    # the burst goes out back to back
    drops = [int(l.split()[1]) for l in out.splitlines() if l.startswith("D ")]
    assert drops == [sim.telem.drops]


# ---------------------------------------------------------------- switch mode, pin to pwm_cmd
# The whole board in simulation: pins -> board_din -> io.din -> core -> io.pwm_cmd,
# telemetry -> UART. Each layer is compared on its own, so an off-by-one shows
# where it happens: board_din against its model, the core against the ISS fed
# with that model's io.din, the UART against the ISS telemetry, and finally the
# pin-to-pwm_cmd time against the formula of docs/io_interface.md.
E2E_PERIOD, E2E_ACT, E2E_NDB = 5_000, 100, 5_000      # 3 words/period need 3 x 1320 < PERIOD
E2E_SRC = f""".equ PERIOD, {E2E_PERIOD}
        #0          -> tmr.t_sync
loop:   #PERIOD     -> tmr.t_advance
        io.encoder  -> r1
        io.din      -> r2
        tmr.elapsed -> r14
        #{E2E_ACT}  -> tmr.t_wait
        r2          -> io.pwm_cmd
        tmr.elapsed -> r15
        r2          -> telem.t_push
        r14         -> telem.t_push
        r15         -> telem.t_push
        #loop       -> pc.t_jump
"""
S = 2                                                  # io.din is read at anchor + 2


def e2e_anchor(k):
    return 2 + (k + 1) * E2E_PERIOD                    # t_sync is the move at cycle R = 2


def test_switch_pins_to_pwm_cmd_end_to_end():
    from host.asm import assemble, output_files
    from host.common import spec as sp
    from host.hil import board_din as B
    from host.iss import Simulator, Stimulus

    a, L = e2e_anchor, E2E_NDB + 3                    # L: pin stable -> core input (sw[2])
    # load switch: bounces, then settles so io.din[2] changes exactly at a sample
    # cycle (anchor + 2); later it is released so the change lands one cycle after one
    p_on, p_off = a(4) + S - L, a(8) + S + 1 - L
    sw = [(p_on - 300, 4), (p_on - 250, 0), (p_on - 100, 4), (p_on - 90, 0), (p_on, 4), (p_off, 0)]
    # setpoint: sw[1:0] = 2 (bouncing), committed by a bouncy press so io.din[1:0]
    # changes at a(12) + 2; then sw[1:0] = 1 with no press (nothing happens)
    b1 = a(12) + S - (L + 1)
    sw += [(b1 - 4000, 2), (b1 - 3990, 1), (b1 - 3900, 2)]
    btn = [(b1 - 60, 1), (b1 - 50, 0), (b1, 1), (b1 + 9000, 0)]
    sw += [(a(14), 1)]
    # press first, move the switches one cycle later: the commit takes the
    # debounced value of that moment, which is still 1
    q2 = a(17)
    btn += [(q2, 1), (q2 + 9000, 0)]
    sw += [(q2 + 1, 3)]
    # a clean press once the switches have settled: io.din[1:0] = 3 at a(21) + 3
    b2 = a(21) + S + 1 - (L + 1)
    btn += [(b2, 1), (b2 + 9000, 0)]
    sw.sort()
    btn.sort()
    end = a(25)

    din = B.interface(sw, btn, E2E_NDB, end=end)
    # what the scenario was built to hit
    assert din == [(0, 0), (a(4) + S, 4), (a(8) + S + 1, 0), (a(12) + S, 2),
                   (q2 + L + 1, 1), (a(21) + S + 1, 3)]

    # ISS with io.din from the model
    prog = assemble(E2E_SRC, "e2e.tta")
    stim = Stimulus.parse("".join(f"{c} io.din {v}\n" for c, v in din[1:]))
    sim = Simulator(prog.code, prog.data, stim, prog.imem_words, prog.dmem_words)
    sim.run(end)
    assert sim.telem.drops == 0
    expected_w, free = [], 0
    for c, word in sim.telem.accepted:
        pop = max(free, c + 1)
        free = pop + sp.TELEM_CYCLES_PER_WORD
        expected_w.append((pop + 1, word & 0xFFFF_FFFF))
    expected_w = [w for w in expected_w if w[0] < end - 50 * 40]

    # RTL: the whole board
    work = BUILD / "board_e2e"
    work.mkdir(parents=True, exist_ok=True)
    for suffix, text in output_files(prog).items():
        (work / f"e2e{suffix}").write_text(text, encoding="utf-8", newline="\n")
    pins = sorted([(c, "sw", v) for c, v in sw] + [(c, "btn", v) for c, v in btn])
    (work / "pins.txt").write_text("".join(f"{c} {p} {v}\n" for c, p, v in pins), encoding="utf-8",
                                   newline="\n")
    imem, dmem = (shlex.quote(f'"{wsl_path(work / f"e2e.{k}.hex")}"') for k in ("code", "data"))
    obj = work / "obj"
    cmd = (f"cd {shlex.quote(wsl_path(ROOT))} && "
           f"verilator --binary --timing --timescale 1ns/1ps -j 0 -Wall -Wno-fatal --top-module tb_board "
           f"-I{DESIGN_DIR} -GIMEM_INIT={imem} -GDMEM_INIT={dmem} -GN_DB={E2E_NDB} "
           f"--Mdir {shlex.quote(wsl_path(obj))} -o Vtb_board {' '.join(BOARD_SOURCES)} {SIM_DIR}/tb_board.sv "
           f"&& {shlex.quote(wsl_path(obj / 'Vtb_board'))} +max={end} "
           f"+pins={shlex.quote(wsl_path(work / 'pins.txt'))}")
    code, out = wsl(cmd)
    assert code == 0 and "%Warning" not in out, out[-4000:]
    assert "END" in out and "FAIL" not in out, out[-4000:]
    log = {k: [(int(f[1]), int(f[2], 16) if k == "W" else int(f[2]))
               for f in (l.split() for l in out.splitlines()) if f[:1] == [k]] for k in "IPW"}

    # 1. board_din: io.din at the core input, cycle for cycle
    assert log["I"] == din[1:]
    # 2+3. core and UART: every telemetry word and its start cycle
    assert [w for w in log["W"] if w[0] < end - 50 * 40] == expected_w
    # pwm_cmd output: a write at cycle w is seen from w + 1
    pwm, prev = [], 0
    for c, v in sim.pwm_log:
        if v != prev:
            pwm.append((c + 1, v))
        prev = v
    assert log["P"] == [p for p in pwm if p[0] < end]
    # telemetry fields: din at anchor + 2 of each period, el_in = 3, el_act = ACT + 2
    words = [w for _, w in log["W"]]
    recs = [words[i:i + 3] for i in range(0, len(words) - 2, 3)]
    for k, (d, el_in, el_act) in enumerate(recs):
        assert (el_in, el_act) == (3, E2E_ACT + 2)
        assert d == next(v for c, v in reversed(din) if c <= a(k) + S)
    # 4. the formula: change at the core input at c is actuated at the first
    #    period with anchor + 2 >= c, written at anchor + ACT + 1, seen 1 later;
    #    from the pin: + N_DB + 3 (sw[2]) or + N_DB + 4 (BTN0 commit) before that
    for c, v in din[1:]:
        k = next(k for k in range(100) if a(k) + S >= c)
        write = a(k) + E2E_ACT + 1
        assert E2E_ACT + 1 - S <= write - c <= E2E_ACT + 1 - S + E2E_PERIOD - 1
        assert (write + 1, v) in log["P"]
    assert (p_on + L + (E2E_ACT + 1 - S) + 1, 4) in log["P"]                 # pin -> pwm, same period
    assert (p_off + L + (E2E_ACT + 1 - S) + E2E_PERIOD - 1 + 1, 0) in log["P"]   # one cycle late: next one
    assert (b1 + L + 1 + (E2E_ACT + 1 - S) + 1, 2) in log["P"]                 # commit path
