"""Assemble-time integer expressions (asm_syntax.md §4).

Grammar (C precedence, no division):
    or_e  = xor_e { "|" xor_e }      xor_e = and_e { "^" and_e }
    and_e = shift_e { "&" shift_e }  shift_e = add_e { ("<<"|">>") add_e }
    add_e = mul_e { ("+"|"-") mul_e } mul_e = unary { "*" unary }
    unary = ("-"|"~") unary | primary  primary = NUMBER | IDENT | "(" expr ")"
"""
import re
from dataclasses import dataclass

NUMBER_RE = re.compile(
    r"0[xX][0-9A-Fa-f](?:_?[0-9A-Fa-f])*|0[bB][01](?:_?[01])*|[0-9](?:_?[0-9])*")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
OPS = ("<<", ">>", "+", "-", "*", "&", "|", "^", "~", "(", ")")

BINARY_LEVELS = (("|",), ("^",), ("&",), ("<<", ">>"), ("+", "-"), ("*",))


class ExprError(ValueError):
    def __init__(self, msg, col):
        super().__init__(msg)
        self.col = col


@dataclass(frozen=True)
class Num:
    value: int


@dataclass(frozen=True)
class Sym:
    name: str
    col: int


@dataclass(frozen=True)
class Unary:
    op: str
    arg: object


@dataclass(frozen=True)
class Binary:
    op: str
    lhs: object
    rhs: object


def tokenize(text, col0):
    """Yield (kind, value, col) with kind in num/ident/op."""
    i = 0
    toks = []
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        col = col0 + i
        m = NUMBER_RE.match(text, i)
        if m and not (m.end() < len(text) and (text[m.end()].isalnum() or text[m.end()] == "_")):
            lit = m.group().replace("_", "")
            base = 16 if lit[:2].lower() == "0x" else 2 if lit[:2].lower() == "0b" else 10
            toks.append(("num", int(lit, base), col))
            i = m.end()
            continue
        m = IDENT_RE.match(text, i)
        if m:
            toks.append(("ident", m.group(), col))
            i = m.end()
            continue
        op = next((o for o in OPS if text.startswith(o, i)), None)
        if op is None:
            raise ExprError(f"unexpected character '{ch}' in expression", col)
        toks.append(("op", op, col))
        i += len(op)
    return toks


def parse(text, col0=1):
    toks = tokenize(text, col0)
    if not toks:
        raise ExprError("empty expression", col0)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else ("end", None, col0 + len(text))

    def take():
        nonlocal pos
        tok = peek()
        pos += 1
        return tok

    def level(n):
        if n == len(BINARY_LEVELS):
            return unary()
        node = level(n + 1)
        while peek()[0] == "op" and peek()[1] in BINARY_LEVELS[n]:
            op = take()[1]
            node = Binary(op, node, level(n + 1))
        return node

    def unary():
        kind, val, col = peek()
        if kind == "op" and val in ("-", "~"):
            take()
            return Unary(val, unary())
        return primary()

    def primary():
        kind, val, col = take()
        if kind == "num":
            return Num(val)
        if kind == "ident":
            return Sym(val, col)
        if kind == "op" and val == "(":
            node = level(0)
            k, v, c = take()
            if (k, v) != ("op", ")"):
                raise ExprError("expected ')'", c)
            return node
        raise ExprError("expected a number, symbol or '('", col)

    node = level(0)
    kind, val, col = peek()
    if kind != "end":
        raise ExprError(f"unexpected '{val}' in expression", col)
    return node


def evaluate(node, lookup):
    """lookup(Sym) -> int, may raise ExprError."""
    if isinstance(node, Num):
        return node.value
    if isinstance(node, Sym):
        return lookup(node)
    if isinstance(node, Unary):
        v = evaluate(node.arg, lookup)
        return -v if node.op == "-" else ~v
    a, b = evaluate(node.lhs, lookup), evaluate(node.rhs, lookup)
    if node.op in ("<<", ">>") and b < 0:
        raise ExprError("negative shift amount", 1)
    return {
        "|": lambda: a | b, "^": lambda: a ^ b, "&": lambda: a & b,
        "<<": lambda: a << b, ">>": lambda: a >> b,
        "+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
    }[node.op]()


def symbols_in(node):
    if isinstance(node, Sym):
        yield node
    elif isinstance(node, Unary):
        yield from symbols_in(node.arg)
    elif isinstance(node, Binary):
        yield from symbols_in(node.lhs)
        yield from symbols_in(node.rhs)
