import pytest

from host.common import spec
from host.common.encoding import EncodingError, Move, decode, encode, illegal_reason

P = spec.PORT_BY_NAME


def imm(dst, value):
    return Move(dst=P[dst].id, imm=True, value=value)


def mov(src, dst):
    return Move(dst=P[dst].id, imm=False, src=P[src].id)


# isa.md §1 encoding examples
@pytest.mark.parametrize("move, word", [
    (imm("r1", 1000), 0x118003E8),
    (imm("r2", -1), 0x12FFFFFF),
    (mov("r1", "alu.a"), 0x20000011),
    (mov("alu.out", "r3"), 0x13000023),
    (imm("null", 0), 0xFF800000),
])
def test_isa_examples(move, word):
    assert encode(move) == word
    assert decode(word) == move


@pytest.mark.parametrize("value", [spec.IMM_MIN, -1, 0, 1, spec.IMM_MAX])
def test_immediate_roundtrip(value):
    word = encode(imm("r0", value))
    assert decode(word).value == value


def test_bus_value_is_sign_extended_32_bit():
    assert decode(0x12FFFFFF).bus_value() == 0xFFFFFFFF
    assert decode(encode(imm("r0", spec.IMM_MIN))).bus_value() == 0xFFC00000


@pytest.mark.parametrize("value", [spec.IMM_MIN - 1, spec.IMM_MAX + 1])
def test_immediate_out_of_range(value):
    with pytest.raises(EncodingError):
        encode(imm("r0", value))


def test_every_legal_port_pair_roundtrips():
    for src in (p for p in spec.PORTS if p.readable):
        for dst in (p for p in spec.PORTS if p.writable):
            word = encode(Move(dst=dst.id, imm=False, src=src.id))
            assert illegal_reason(word) is None
            assert decode(word) == Move(dst=dst.id, imm=False, src=src.id)


@pytest.mark.parametrize("word, fragment", [
    (0x00000000, "dst 0x00"),                 # zero word traps (isa.md §2)
    (0x0F800000, "dst 0x0f"),                 # reserved block
    (0x33800000, "dst 0x33"),                 # reserved mul.out_hi
    (0x23800000, "alu.out is not writable"),  # write to an R-only port
    (0x10000020, "alu.a is not readable"),    # read a W-only port
    (0x10000110, "bits 22:8"),                # reserved src bits set
    (0x100000B0, "src 0xb0"),                 # src ID with no port
    (0x10000093, "src 0x93"),                 # first unused ID after io.din
    (0x92800000, "io.din is not writable"),   # io.din is an input
])
def test_illegal_words(word, fragment):
    reason = illegal_reason(word)
    assert reason is not None and fragment in reason


def test_nop_is_legal():
    assert illegal_reason(0xFF800000) is None
