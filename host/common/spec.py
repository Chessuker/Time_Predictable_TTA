"""Single source of truth for the ISA and timing constants used by every tool.

Mirrors docs/isa.md and docs/timing_model.md. Production code imports this
module and never reads the Markdown; tests/test_isa_doc_sync.py checks that
the two stay in agreement.
"""
from dataclasses import dataclass
from enum import IntEnum

# Bump whenever encoding, semantics or timing change (toolchain_formats.md §3.1).
SPEC_ID = "phase5-din-2026-10-10"

# ---- instruction format (isa.md §1) ----
WORD_BITS = 32
DST_SHIFT = 24
IMM_BIT = 23
IMM_BITS = 23
IMM_MIN = -(1 << (IMM_BITS - 1))          # -4,194,304
IMM_MAX = (1 << (IMM_BITS - 1)) - 1       #  4,194,303
SRC_PORT_MASK = 0xFF                      # src port ID lives in bits 7:0
SRC_RESERVED_MASK = 0x7FFF00              # bits 22:8 must be zero when imm = 0
WORD_MASK = (1 << WORD_BITS) - 1

# ---- timing constants (timing_model.md §1) ----
D = 1   # wake delay after a timing move
P = 2   # bubbles per taken control transfer
H = 2   # bubbles between a trap and the first handler move
R = 2   # cycle of the first move after reset

# ---- implementation defaults (isa.md §8, toolchain_formats.md §6.5) ----
IMEM_WORDS = 4096
DMEM_WORDS = 4096
TELEM_FIFO_WORDS = 64           # provisional until the Phase 5 frame format
TELEM_CYCLES_PER_WORD = 1320    # 4 bytes x 10 bits (8N1) x baud divider 33; provisional


# ---- ports (isa.md §2) ----
@dataclass(frozen=True)
class Port:
    id: int
    name: str
    readable: bool = False
    writable: bool = False
    trigger: bool = False
    # For FU outputs: the trigger port that produces this value and its latency L
    # (timing_model.md R2). None for plain state ports.
    produced_by: str | None = None
    latency: int | None = None

    @property
    def fu(self) -> str | None:
        return self.name.split(".")[0] if "." in self.name else None


def _ports() -> tuple[Port, ...]:
    regs = [Port(0x10 + i, f"r{i}", readable=True, writable=True) for i in range(16)]
    return tuple(regs) + (
        Port(0x20, "alu.a", writable=True),
        Port(0x21, "alu.op", writable=True),
        Port(0x22, "alu.t_b", writable=True, trigger=True),
        Port(0x23, "alu.out", readable=True, produced_by="alu.t_b", latency=1),
        Port(0x30, "mul.a", writable=True),
        Port(0x31, "mul.t_b", writable=True, trigger=True),
        Port(0x32, "mul.out", readable=True, produced_by="mul.t_b", latency=2),
        Port(0x40, "cmp.a", writable=True),
        Port(0x41, "cmp.t_b", writable=True, trigger=True),
        Port(0x42, "cmp.eq", readable=True, produced_by="cmp.t_b", latency=1),
        Port(0x43, "cmp.lt", readable=True, produced_by="cmp.t_b", latency=1),
        Port(0x44, "cmp.ltu", readable=True, produced_by="cmp.t_b", latency=1),
        Port(0x50, "pc.cond", writable=True),
        Port(0x51, "pc.t_jump", writable=True, trigger=True),
        Port(0x52, "pc.t_jz", writable=True, trigger=True),
        Port(0x53, "pc.t_jnz", writable=True, trigger=True),
        Port(0x54, "pc.t_call", writable=True, trigger=True),
        Port(0x55, "pc.link", readable=True, writable=True),
        Port(0x60, "mem.data_in", writable=True),
        Port(0x61, "mem.t_load", writable=True, trigger=True),
        Port(0x62, "mem.t_store", writable=True, trigger=True),
        Port(0x63, "mem.data_out", readable=True, produced_by="mem.t_load", latency=1),
        Port(0x70, "tmr.t_sync", writable=True, trigger=True),
        Port(0x71, "tmr.t_advance", writable=True, trigger=True),
        Port(0x72, "tmr.t_wait", writable=True, trigger=True),
        Port(0x73, "tmr.t_arm", writable=True, trigger=True),
        Port(0x74, "tmr.t_clear", writable=True, trigger=True),
        Port(0x75, "tmr.elapsed", readable=True),
        Port(0x76, "tmr.flags", readable=True),
        Port(0x80, "trap.handler", readable=True, writable=True),
        Port(0x81, "trap.cause", readable=True),
        Port(0x82, "trap.epc", readable=True),
        Port(0x83, "trap.t_halt", writable=True, trigger=True),
        Port(0x90, "io.encoder", readable=True),
        Port(0x91, "io.pwm_cmd", writable=True),
        Port(0x92, "io.din", readable=True),
        Port(0xA0, "telem.t_push", writable=True, trigger=True),
        Port(0xA1, "telem.drops", readable=True),
        Port(0xFF, "null", writable=True),
    )


PORTS: tuple[Port, ...] = _ports()
PORT_BY_ID: dict[int, Port] = {p.id: p for p in PORTS}
PORT_BY_NAME: dict[str, Port] = {p.name: p for p in PORTS}

FU_NAMES = ("alu", "mul", "cmp", "pc", "mem", "tmr", "trap", "io", "telem")

# Port groups the tools need by name
JUMP_PORTS = ("pc.t_jump", "pc.t_jz", "pc.t_jnz")
CONTROL_PORTS = JUMP_PORTS + ("pc.t_call",)
TMR_VALUE_PORTS = ("tmr.t_advance", "tmr.t_wait", "tmr.t_arm")   # unsigned v (S2)
TMR_IMM_MAX = IMM_MAX                                              # 0..4,194,303
SYNC_POINT_PORTS = ("tmr.t_advance", "tmr.t_wait")


# ---- ALU opcodes (isa.md §4) ----
class AluOp(IntEnum):
    ADD = 0
    SUB = 1
    AND = 2
    OR = 3
    XOR = 4
    SHL = 5
    SHR = 6
    SRA = 7
    MIN = 8
    MAX = 9


# ---- trap causes (isa.md §9) ----
class TrapCause(IntEnum):
    NONE = 0
    DEADLINE = 1
    ILLEGAL_PORT = 2
    ILLEGAL_ALU_OP = 3
    DATA_ADDR = 4
    JUMP_TARGET = 5


# ---- tmr.flags bits (isa.md §7) ----
class TmrFlag(IntEnum):
    LATE = 0
    ARMED = 1
    TRAPPED = 2


# ---- names the assembler reserves (asm_syntax.md §7) ----
RESERVED_NAMES = frozenset(
    [f"r{i}" for i in range(16)] + ["null", "nop", "main"] + list(FU_NAMES)
    + [op.name for op in AluOp]
)
