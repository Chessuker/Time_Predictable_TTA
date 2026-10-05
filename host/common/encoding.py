"""Encode and decode one 32-bit move word (isa.md §1, §11).

Only encoding-level legality lives here. Value-level checks (ALU opcode range,
memory and jump-target bounds) depend on runtime values or memory sizes and
belong to the ISS and the assembler.
"""
from dataclasses import dataclass

from . import spec


class EncodingError(ValueError):
    pass


@dataclass(frozen=True)
class Move:
    dst: int                 # dst port ID
    imm: bool
    src: int | None = None   # src port ID when imm is False
    value: int | None = None # sign-extended immediate when imm is True

    def bus_value(self) -> int:
        """Immediate as the 32-bit value placed on the bus."""
        assert self.imm
        return self.value & spec.WORD_MASK


def sign_extend_imm(field: int) -> int:
    field &= (1 << spec.IMM_BITS) - 1
    return field - (1 << spec.IMM_BITS) if field >> (spec.IMM_BITS - 1) else field


def encode(move: Move) -> int:
    """Encode a move. Checks field ranges only, not port legality."""
    if not 0 <= move.dst <= 0xFF:
        raise EncodingError(f"dst {move.dst} out of 8-bit range")
    word = move.dst << spec.DST_SHIFT
    if move.imm:
        if move.value is None or not spec.IMM_MIN <= move.value <= spec.IMM_MAX:
            raise EncodingError(f"immediate {move.value} out of 23-bit signed range")
        word |= 1 << spec.IMM_BIT
        word |= move.value & ((1 << spec.IMM_BITS) - 1)
    else:
        if move.src is None or not 0 <= move.src <= spec.SRC_PORT_MASK:
            raise EncodingError(f"src {move.src} out of 8-bit range")
        word |= move.src
    return word


def decode(word: int) -> Move:
    """Split a word into fields. Never fails; use illegal_reason() for legality."""
    word &= spec.WORD_MASK
    dst = word >> spec.DST_SHIFT
    if (word >> spec.IMM_BIT) & 1:
        return Move(dst=dst, imm=True, value=sign_extend_imm(word))
    return Move(dst=dst, imm=False, src=word & spec.SRC_PORT_MASK)


def illegal_reason(word: int) -> str | None:
    """Encoding-level illegality per isa.md §11 (all are trap cause 2), else None."""
    word &= spec.WORD_MASK
    move = decode(word)
    dst = spec.PORT_BY_ID.get(move.dst)
    if dst is None:
        return f"dst 0x{move.dst:02x} is not a port"
    if not dst.writable:
        return f"dst {dst.name} is not writable"
    if not move.imm:
        if word & spec.SRC_RESERVED_MASK:
            return "src bits 22:8 are not zero"
        src = spec.PORT_BY_ID.get(move.src)
        if src is None:
            return f"src 0x{move.src:02x} is not a port"
        if not src.readable:
            return f"src {src.name} is not readable"
    return None
