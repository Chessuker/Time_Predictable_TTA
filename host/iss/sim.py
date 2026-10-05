"""Cycle-accurate instruction set simulator: the golden model of the spec.

Timing follows docs/timing_model.md exactly (R1-R7). The simulator is event
driven: it jumps straight to the next cycle where something can happen (a move
executes or the deadline fires), so a 100,000-cycle stall costs one step.

Program errors that the assembler is meant to reject statically (R3 early
reads, t_advance while armed, elapsed with now < anchor) stop the simulation
with SimError instead of producing undefined behaviour.
"""
import bisect
from collections import deque
from dataclasses import dataclass, field

from host.common import spec
from host.common.encoding import decode, illegal_reason

M32 = spec.WORD_MASK
P = spec.PORT_BY_NAME


class SimError(RuntimeError):
    def __init__(self, cycle, pc, msg):
        super().__init__(f"cycle {cycle}, pc 0x{pc:08x}: {msg}")
        self.cycle, self.pc = cycle, pc


def s32(x):
    x &= M32
    return x - (1 << 32) if x >> 31 else x


@dataclass
class _Out:
    """An FU output: value plus the first cycle it may be read (R2/R3)."""
    value: int = 0
    ready: int = 0


@dataclass
class Telemetry:
    """FIFO + UART model (toolchain_formats.md §6.5).

    Each cycle the UART takes first, then a push lands: a word pushed at cycle
    p can start sending at p + 1, leaves the FIFO when it starts, and the UART
    is busy for cycles_per_word cycles per word.
    """
    depth: int = spec.TELEM_FIFO_WORDS
    cycles_per_word: int = spec.TELEM_CYCLES_PER_WORD
    fifo: deque = field(default_factory=deque)       # push cycles of queued words
    busy_until: int = 0
    drops: int = 0
    accepted: list = field(default_factory=list)     # (cycle, word)

    def advance(self, c):
        while self.fifo:
            start = max(self.busy_until, self.fifo[0] + 1)
            if start > c:
                break
            self.fifo.popleft()
            self.busy_until = start + self.cycles_per_word

    def push(self, c, word):
        self.advance(c)
        if len(self.fifo) < self.depth:
            self.fifo.append(c)
            self.accepted.append((c, word))
        else:
            self.drops = min(self.drops + 1, M32)


class StimulusError(ValueError):
    pass


class Stimulus:
    """Input ports driven from prog.stim (toolchain_formats.md §6.4).

    The file is part of the ISS <-> RTL contract, so malformed input is
    rejected, never repaired: cycles of one port must strictly increase in file
    order, and cycles cannot be negative.
    """

    def __init__(self, entries=(), where=None):
        """entries: (cycle, port, value) in file order; where[i] names entry i in errors."""
        self._cycles, self._values = {}, {}
        for i, (cycle, port, value) in enumerate(entries):
            at = where[i] if where else f"stim entry {i + 1}"
            if cycle < 0:
                raise StimulusError(f"{at}: negative cycle {cycle}")
            cycles = self._cycles.setdefault(port, [])
            if cycles and cycle <= cycles[-1]:
                raise StimulusError(f"{at}: {port} cycle {cycle} is not after "
                                    f"the previous {port} cycle {cycles[-1]}")
            cycles.append(cycle)
            self._values.setdefault(port, []).append(value)

    @classmethod
    def parse(cls, text):
        entries, where = [], []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) != 3 or parts[1] not in P or not P[parts[1]].readable:
                raise StimulusError(f"stim line {n}: expected '<cycle> <input port> <value>'")
            try:
                entries.append((int(parts[0]), parts[1], int(parts[2])))
            except ValueError:
                raise StimulusError(f"stim line {n}: cycle and value must be decimal integers") from None
            where.append(f"stim line {n}")
        return cls(entries, where)

    def value(self, port, c):
        cycles = self._cycles.get(port)
        if not cycles:
            return 0
        i = bisect.bisect_right(cycles, c) - 1
        return self._values[port][i] if i >= 0 else 0


@dataclass
class Result:
    trace: list[str]
    end: str                    # halt | nohandler | double | maxcycles
    cycle: int                  # cycle of the final H/E record
    sim: "Simulator"


class Simulator:
    def __init__(self, code, data=(), stim=None, imem_words=None, dmem_words=None):
        self.imem = list(code)
        self.imem_words = imem_words or len(self.imem)
        self.dmem = list(data) + [0] * ((dmem_words or spec.DMEM_WORDS) - len(data))
        self.stim = stim or Stimulus()
        self.trace = []

        self.pc = 0
        self.next_exec = spec.R
        self.halted = None
        self.regs = [0] * 16
        self.alu_a = self.alu_op = 0
        self.mul_a = self.cmp_a = 0
        self.alu_out, self.mul_out, self.mem_out = _Out(), _Out(), _Out()
        self.cmp_out = {"cmp.eq": _Out(), "cmp.lt": _Out(), "cmp.ltu": _Out()}
        self.cond = self.link = 0
        self.mem_data_in = 0
        self.anchor = self.deadline = 0
        self.armed = self.late = self.trapped = 0
        self.armed_from = 0                     # first cycle the armed bit is visible
        self.handler = self.cause = self.epc = 0
        self.pwm = 0
        self.pwm_log = []                       # (cycle, value) of io.pwm_cmd writes
        self.telem = Telemetry()

    # ---------------------------------------------------------------- run
    def run(self, max_cycles):
        while self.halted is None:
            c = self.next_exec
            dl = max(self.deadline, self.armed_from) if self.armed else None
            if dl is not None and dl < c:
                c = dl
            if c >= max_cycles:
                self.trace.append(f"E {max_cycles} maxcycles")
                return Result(self.trace, "maxcycles", max_cycles, self)
            if dl is not None and c >= dl:           # R6 step 2: deadline wins
                self._trap(c, spec.TrapCause.DEADLINE)
            else:
                self._execute(c)
        return Result(self.trace, self.halted, self._halt_cycle, self)

    # ---------------------------------------------------------------- trap/halt
    def _halt(self, c, reason):
        self.halted, self._halt_cycle = reason, c
        self.trace.append(f"H {c} {reason}")

    def _trap(self, c, cause):
        # epc = next move in program order that has not executed (R6); self.pc
        # already points at it whether we are executing, stalled or in a bubble
        epc = self.pc
        self.trace.append(f"T {c} {epc:08x} {int(cause)}")
        double = self.trapped
        self.cause, self.epc = int(cause), epc
        self.armed, self.trapped = 0, 1
        if double:
            self._halt(c, "double")
        elif self.handler == 0:
            self._halt(c, "nohandler")
        else:
            self.pc = self.handler
            self.next_exec = c + 1 + spec.H

    # ---------------------------------------------------------------- one move
    def _execute(self, c):
        pc = self.pc
        word = self.imem[pc] if pc < len(self.imem) else 0
        if illegal_reason(word) is not None:
            self._trap(c, spec.TrapCause.ILLEGAL_PORT)
            return
        m = decode(word)
        dst = spec.PORT_BY_ID[m.dst]
        if m.imm:
            value, src_txt = m.value & M32, "imm"
        else:
            value, src_txt = self._read(spec.PORT_BY_ID[m.src], c), f"{m.src:02x}"

        self.pc, self.next_exec = pc + 1, c + 1
        cause = self._write(dst, value, c, pc)
        if cause is not None:                       # squashed: no M record
            self.pc = pc
            self._trap(c, cause)
            return
        self.trace.append(f"M {c} {pc:08x} {word:08x} {src_txt} {m.dst:02x} {value:08x}")
        if dst.name == "trap.t_halt":
            self._halt(c, "halt")

    def _ready(self, out, name, c):
        if c < out.ready:
            raise SimError(c, self.pc, f"{name} read at cycle {c}, ready at {out.ready} (R3)")
        return out.value

    def _read(self, port, c):
        n = port.name
        if n[0] == "r" and n[1:].isdigit():
            return self.regs[int(n[1:])]
        if n == "alu.out":
            return self._ready(self.alu_out, n, c)
        if n == "mul.out":
            return self._ready(self.mul_out, n, c)
        if n in self.cmp_out:
            return self._ready(self.cmp_out[n], n, c)
        if n == "mem.data_out":
            return self._ready(self.mem_out, n, c)
        if n == "pc.link":
            return self.link
        if n == "tmr.elapsed":
            if c < self.anchor:
                raise SimError(c, self.pc, "tmr.elapsed read with now < anchor")
            return min(c - self.anchor, M32)
        if n == "tmr.flags":
            return self.late | self.armed << 1 | self.trapped << 2
        if n == "trap.handler":
            return self.handler
        if n == "trap.cause":
            return self.cause
        if n == "trap.epc":
            return self.epc
        if n == "io.encoder":
            return self.stim.value(n, c) & M32
        if n == "telem.drops":
            return self.telem.drops
        raise AssertionError(n)

    def _jump(self, target, c):
        if target >= self.imem_words:
            return spec.TrapCause.JUMP_TARGET
        self.pc, self.next_exec = target, c + 1 + spec.P
        return None

    def _write(self, dst, v, c, pc):
        """Apply a move to dst. Returns a trap cause to squash the move."""
        n = dst.name
        if n[0] == "r" and n[1:].isdigit():
            self.regs[int(n[1:])] = v
        elif n == "alu.a":
            self.alu_a = v
        elif n == "alu.op":
            if v not in spec.AluOp._value2member_map_:
                return spec.TrapCause.ILLEGAL_ALU_OP
            self.alu_op = v
        elif n == "alu.t_b":
            self.alu_out = _Out(_alu(self.alu_op, self.alu_a, v), c + P["alu.out"].latency)
        elif n == "mul.a":
            self.mul_a = v
        elif n == "mul.t_b":
            self.mul_out = _Out((s32(self.mul_a) * s32(v)) & M32, c + P["mul.out"].latency)
        elif n == "cmp.a":
            self.cmp_a = v
        elif n == "cmp.t_b":
            a, ready = self.cmp_a, c + P["cmp.eq"].latency
            self.cmp_out = {"cmp.eq": _Out(int(a == v), ready),
                            "cmp.lt": _Out(int(s32(a) < s32(v)), ready),
                            "cmp.ltu": _Out(int(a < v), ready)}
        elif n == "pc.cond":
            self.cond = v
        elif n == "pc.t_jump":
            return self._jump(v, c)
        elif n in ("pc.t_jz", "pc.t_jnz"):
            if (self.cond == 0) == (n == "pc.t_jz"):
                return self._jump(v, c)
        elif n == "pc.t_call":
            cause = self._jump(v, c)
            if cause is None:
                self.link = pc + 1
            return cause
        elif n == "pc.link":
            self.link = v
        elif n == "mem.data_in":
            self.mem_data_in = v
        elif n == "mem.t_load":
            if v >= len(self.dmem):
                return spec.TrapCause.DATA_ADDR
            self.mem_out = _Out(self.dmem[v], c + P["mem.data_out"].latency)
        elif n == "mem.t_store":
            if v >= len(self.dmem):
                return spec.TrapCause.DATA_ADDR
            self.dmem[v] = self.mem_data_in
        elif n == "tmr.t_sync":
            self.anchor = c
        elif n in ("tmr.t_advance", "tmr.t_wait"):
            target = self.anchor + v
            if n == "tmr.t_advance":
                if self.armed:
                    raise SimError(c, pc, "t_advance while armed (B1/S3)")
                self.anchor = target
            self.late = int(c > target)
            self.next_exec = max(c, target) + spec.D
        elif n == "tmr.t_arm":
            self.deadline, self.armed, self.armed_from = self.anchor + v, 1, c + 1
        elif n == "tmr.t_clear":
            self.armed = 0
        elif n == "trap.handler":
            self.handler = v
        elif n == "trap.t_halt":
            pass                                   # handled after the M record
        elif n == "io.pwm_cmd":
            self.pwm = v
            self.pwm_log.append((c, s32(v)))
        elif n == "telem.t_push":
            self.telem.push(c, v)
        elif n == "null":
            pass
        else:
            raise AssertionError(n)
        return None


def _alu(op, a, b):
    sh = b & 31
    if op == spec.AluOp.ADD:
        r = a + b
    elif op == spec.AluOp.SUB:
        r = a - b
    elif op == spec.AluOp.AND:
        r = a & b
    elif op == spec.AluOp.OR:
        r = a | b
    elif op == spec.AluOp.XOR:
        r = a ^ b
    elif op == spec.AluOp.SHL:
        r = a << sh
    elif op == spec.AluOp.SHR:
        r = a >> sh
    elif op == spec.AluOp.SRA:
        r = s32(a) >> sh
    elif op == spec.AluOp.MIN:
        r = min(s32(a), s32(b))
    else:
        r = max(s32(a), s32(b))
    return r & M32
