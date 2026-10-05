"""Two-pass assembler for docs/asm_syntax.md.

Pass 1 parses lines, assigns addresses and collects symbols. Pass 2 evaluates
expressions, validates every move against the ISA and the assembler rules, and
encodes. All errors are collected; nothing is produced if there is any error.

Not done here: the CFG-based static checks S1, S3, S4 and recursion (S5), which
run after a successful assembly (Phase 1 step 5).
"""
import re
from dataclasses import dataclass, field

from host.common import spec
from host.common.encoding import Move, decode, encode, illegal_reason

from . import expr as ex

PORTS = spec.PORT_BY_NAME
LABEL_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:")
ANNOT_RE = re.compile(r"@([A-Za-z_]+)\s*(.*)$")
NO_LABEL_DIRECTIVES = (".text", ".data", ".equ", ".func", ".endfunc")
WORD_MIN = -(1 << 31)


@dataclass
class AsmError:
    line: int
    col: int
    msg: str


class AssemblyFailed(Exception):
    def __init__(self, filename, errors):
        self.filename = filename
        self.errors = sorted(errors, key=lambda e: (e.line, e.col))
        super().__init__(self.render())

    def render(self):
        return "\n".join(f"{self.filename}:{e.line}:{e.col}: error: {e.msg}" for e in self.errors)


@dataclass
class Symbol:
    kind: str                 # label | func | equ
    section: str | None
    value: int | None
    line: int
    node: object = None       # expression for equ


@dataclass
class CodeItem:
    addr: int
    line: int
    col: int
    kind: str                 # move | illegal
    owner: str                # function name, "main" outside any .func
    dst: spec.Port | None = None
    src: spec.Port | None = None
    node: object = None       # immediate or .illegal expression
    word: int = 0
    note: str | None = None   # resolved value shown in the listing


@dataclass
class DataItem:
    addr: int
    line: int
    col: int
    node: object = None       # None for .space
    word: int = 0


@dataclass
class Program:
    filename: str
    source_lines: list[str]
    imem_words: int
    dmem_words: int
    code: list[int]           # padded to imem_words
    data: list[int]           # padded to dmem_words
    code_len: int
    data_len: int
    symbols: dict[str, Symbol]
    functions: list[tuple[str, int, int]]
    annotations: list[dict]
    code_items: list[CodeItem]
    data_items: list[DataItem]


@dataclass
class _State:
    section: str = "text"
    tc: int = 0
    dc: int = 0
    func: tuple | None = None          # (name, start, line)
    seen_func: bool = False
    pending: tuple | None = None       # (kind, arg_text, line, col)
    symbols: dict = field(default_factory=dict)
    functions: list = field(default_factory=list)
    annotations: list = field(default_factory=list)   # (kind, arg, addr, line, col)
    code: list = field(default_factory=list)
    data: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def assemble(source, filename="<input>", imem_words=spec.IMEM_WORDS, dmem_words=spec.DMEM_WORDS):
    lines = source.splitlines()
    st = _State()
    for lineno, raw in enumerate(lines, 1):
        _pass1_line(st, lineno, raw)
    if st.pending:
        _err(st, st.pending[2], st.pending[3], "annotation is not followed by a label in .text")
    if st.func:
        _err(st, st.func[2], 1, f".func {st.func[0]} has no .endfunc")
    prog = _pass2(st, filename, lines, imem_words, dmem_words)
    if st.errors:
        raise AssemblyFailed(filename, st.errors)
    return prog


def _err(st, line, col, msg):
    st.errors.append(AsmError(line, col, msg))


# ------------------------------------------------------------------ pass 1

def _pass1_line(st, lineno, raw):
    text = raw.split(";", 1)[0]
    stripped = text.strip()
    if not stripped:
        return
    col = len(text) - len(text.lstrip()) + 1

    if stripped.startswith("@"):
        if st.pending:
            _err(st, st.pending[2], st.pending[3], "annotation is not followed by a label in .text")
        m = ANNOT_RE.match(stripped)
        kind = m.group(1) if m else ""
        if kind not in ("loop_bound", "task"):
            _err(st, lineno, col, f"unknown annotation '@{kind}'")
            st.pending = None
            return
        st.pending = (kind, m.group(2).strip(), lineno, col)
        return

    label, rest, rest_col = None, stripped, col
    m = LABEL_RE.match(stripped)
    if m:
        label = m.group(1)
        rest = stripped[m.end():].strip()
        rest_col = col + (len(stripped) - len(stripped[m.end():].lstrip()))

    directive = rest.split(None, 1)[0] if rest.startswith(".") else None
    if st.pending:
        kind, arg, aline, acol = st.pending
        st.pending = None
        # .func NAME defines the label NAME, so it can carry an annotation too
        if (label is None and directive != ".func") or st.section != "text":
            _err(st, aline, acol, "annotation is not followed by a label in .text")
        else:
            st.annotations.append((kind, arg, st.tc, aline, acol))
    if label is not None:
        if directive in NO_LABEL_DIRECTIVES:
            _err(st, lineno, col, f"a label is not allowed on a {directive} line")
        else:
            addr = st.tc if st.section == "text" else st.dc
            _define(st, label, Symbol("label", st.section, addr, lineno), lineno, col)

    if not rest:
        return
    if directive:
        _directive(st, lineno, rest_col, directive, rest[len(directive):].strip())
    elif rest == "nop":
        _code_item(st, lineno, rest_col, "move", dst=PORTS["null"], node=ex.Num(0))
    else:
        _move(st, lineno, rest_col, rest)


def _define(st, name, sym, line, col):
    if name in spec.RESERVED_NAMES:
        _err(st, line, col, f"'{name}' is a reserved name")
    elif name in st.symbols:
        _err(st, line, col, f"'{name}' is already defined on line {st.symbols[name].line}")
    else:
        st.symbols[name] = sym


def _owner(st):
    return st.func[0] if st.func else "main"


def _code_item(st, line, col, kind, **kw):
    if st.section != "text":
        _err(st, line, col, "code is only allowed in .text")
        return
    if st.func is None and st.seen_func:
        _err(st, line, col, "code outside .func is only allowed before the first .func")
    st.code.append(CodeItem(addr=st.tc, line=line, col=col, kind=kind, owner=_owner(st), **kw))
    st.tc += 1


def _parse_expr(st, text, line, col):
    try:
        return ex.parse(text, col)
    except ex.ExprError as e:
        _err(st, line, e.col, str(e))
        return None


def _port(st, name, line, col, role):
    if name.startswith("#"):
        _err(st, line, col, f"{role} cannot be an immediate")
        return None
    port = PORTS.get(name)
    if port is None:
        hint = (f"; immediates need '#' (e.g. #{name})"
                if role == "src" and re.fullmatch(r"[A-Za-z_]\w*", name) else "")
        _err(st, line, col, f"unknown port '{name}'{hint}")
        return None
    if role == "src" and not port.readable:
        _err(st, line, col, f"{name} cannot be read")
        return None
    if role == "dst" and not port.writable:
        _err(st, line, col, f"{name} cannot be written")
        return None
    return port


def _move(st, line, col, text):
    parts = text.split("->")
    if len(parts) != 2:
        _err(st, line, col, "expected 'src -> dst'")
        return
    src_text, dst_text = parts[0].strip(), parts[1].strip()
    dst_col = col + text.index("->") + 2 + (len(parts[1]) - len(parts[1].lstrip()))
    dst = _port(st, dst_text, line, dst_col, "dst")
    if src_text.startswith("#"):
        node = _parse_expr(st, src_text[1:], line, col + 1)
        if dst and node is not None:
            _code_item(st, line, col, "move", dst=dst, node=node)
        elif node is None or dst is None:
            st.tc += 1 if st.section == "text" else 0     # keep later addresses stable
        return
    src = _port(st, src_text, line, col, "src")
    if dst and src:
        _code_item(st, line, col, "move", dst=dst, src=src)
    else:
        st.tc += 1 if st.section == "text" else 0


def _const_now(st, node, line, col):
    """Evaluate in pass 1: numbers, opcodes and .equ only (for .space)."""
    def lookup(s):
        if s.name in spec.AluOp.__members__:
            return spec.AluOp[s.name].value
        sym = st.symbols.get(s.name)
        if sym is None or sym.kind != "equ":
            raise ex.ExprError(f"'{s.name}' must be a constant defined earlier", s.col)
        return ex.evaluate(sym.node, lookup)
    try:
        return ex.evaluate(node, lookup)
    except ex.ExprError as e:
        _err(st, line, e.col, str(e))
        return None


def _directive(st, line, col, name, args):
    def need_section(sec):
        if st.section != sec:
            _err(st, line, col, f"{name} is only allowed in .{sec}")
            return False
        return True

    if name in (".text", ".data"):
        if args:
            _err(st, line, col, f"{name} takes no arguments")
        st.section = name[1:]
    elif name == ".word":
        if not need_section("data"):
            return
        for part in args.split(","):
            node = _parse_expr(st, part.strip(), line, col)
            st.data.append(DataItem(st.dc, line, col, node if node is not None else ex.Num(0)))
            st.dc += 1
    elif name == ".space":
        if not need_section("data"):
            return
        node = _parse_expr(st, args, line, col)
        n = _const_now(st, node, line, col) if node is not None else None
        if n is not None and n < 1:
            _err(st, line, col, ".space needs a size of at least 1")
        for _ in range(n or 0):
            st.data.append(DataItem(st.dc, line, col, None))
            st.dc += 1
    elif name == ".equ":
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*,\s*(.+)", args)
        if not m:
            _err(st, line, col, "expected '.equ NAME, expr'")
            return
        node = _parse_expr(st, m.group(2), line, col)
        if node is not None:
            _define(st, m.group(1), Symbol("equ", None, None, line, node), line, col)
    elif name == ".func":
        if not need_section("text"):
            return
        if not re.fullmatch(r"[A-Za-z_]\w*", args):
            _err(st, line, col, "expected '.func NAME'")
            return
        if st.func:
            _err(st, line, col, f".func {args} inside .func {st.func[0]}")
            return
        _define(st, args, Symbol("func", "text", st.tc, line), line, col)
        st.func = (args, st.tc, line)
        st.seen_func = True
    elif name == ".endfunc":
        if args:
            _err(st, line, col, ".endfunc takes no arguments")
        if not st.func:
            _err(st, line, col, ".endfunc without .func")
            return
        fname, start, fline = st.func
        if st.tc == start:
            _err(st, fline, 1, f"function {fname} is empty")
        else:
            st.functions.append((fname, start, st.tc - 1))
        st.func = None
    elif name == ".illegal":
        node = _parse_expr(st, args, line, col)
        if node is not None:
            _code_item(st, line, col, "illegal", node=node)
    else:
        _err(st, line, col, f"unknown directive '{name}'")


# ------------------------------------------------------------------ pass 2

def _pass2(st, filename, lines, imem_words, dmem_words):
    code_len, data_len = st.tc, st.dc
    if code_len == 0:
        _err(st, 1, 1, ".text is empty")
    if code_len > imem_words:
        _err(st, 1, 1, f"code is {code_len} words, larger than code memory ({imem_words})")
    if data_len > dmem_words:
        _err(st, 1, 1, f"data is {data_len} words, larger than data memory ({dmem_words})")

    func_starts = {s for _, s, _ in st.functions}
    ctx = dict(code_len=code_len, dmem_words=dmem_words, func_starts=func_starts)
    resolving = []

    def lookup(s):
        if s.name in spec.AluOp.__members__:
            return spec.AluOp[s.name].value
        sym = st.symbols.get(s.name)
        if sym is None:
            raise ex.ExprError(f"undefined symbol '{s.name}'", s.col)
        if sym.kind != "equ":
            return sym.value
        if sym.value is None:
            if s.name in resolving:
                raise ex.ExprError(f".equ '{s.name}' refers to itself", s.col)
            resolving.append(s.name)
            try:
                sym.value = ex.evaluate(sym.node, lookup)
            finally:
                resolving.pop()
        return sym.value

    def evaluate(node, line):
        try:
            return ex.evaluate(node, lookup)
        except ex.ExprError as e:
            _err(st, line, e.col, str(e))
            return None

    for name, sym in st.symbols.items():        # resolve every .equ, used or not
        if sym.kind == "equ" and sym.value is None:
            evaluate(ex.Sym(name, 1), sym.line)

    for item in st.code:
        if item.kind == "illegal":
            _encode_illegal(st, item, evaluate, ctx)
        else:
            _encode_move(st, item, evaluate, ctx)

    for d in st.data:
        if d.node is None:
            continue
        v = evaluate(d.node, d.line)
        if v is None:
            continue
        if not WORD_MIN <= v <= spec.WORD_MASK:
            _err(st, d.line, d.col, f".word value {v} does not fit in 32 bits")
        d.word = v & spec.WORD_MASK

    _check_fall_through(st, code_len)

    annotations = []
    for kind, arg, addr, aline, acol in st.annotations:
        if kind == "task":
            if not re.fullmatch(r"[A-Za-z_]\w*", arg):
                _err(st, aline, acol, "expected '@task NAME'")
            else:
                annotations.append({"kind": "task", "name": arg, "addr": addr})
        else:
            node = _parse_expr(st, arg, aline, acol)
            n = evaluate(node, aline) if node is not None else None
            if n is not None and n < 1:
                _err(st, aline, acol, "@loop_bound needs N >= 1")
            elif n is not None:
                annotations.append({"kind": "loop_bound", "addr": addr, "n": n})

    functions = list(st.functions)
    first = min(func_starts, default=code_len)
    if first > 0 and code_len > 0:
        functions.append(("main", 0, first - 1))
    functions.sort(key=lambda f: f[1])

    code = [i.word for i in st.code] + [0] * max(0, imem_words - code_len)
    data = [d.word for d in st.data] + [0] * max(0, dmem_words - data_len)
    return Program(filename, lines, imem_words, dmem_words, code, data, code_len, data_len,
                   st.symbols, functions, annotations, st.code, st.data)


def value_error(dst, value, ctx):
    """Why an immediate value cannot go to dst in normal syntax, or None.

    ctx: code_len, dmem_words, func_starts. The disassembler passes
    func_starts=None when it has no prog.json and cannot know the functions.
    """
    name = dst.name
    if name in spec.JUMP_PORTS:
        if not 0 <= value < ctx["code_len"]:
            return f"jump target {value} is outside the program"
    elif name in ("pc.t_call", "trap.handler"):
        if ctx["func_starts"] is not None and value not in ctx["func_starts"]:
            return f"{name} target {value} is not the start of a .func"
    elif name in spec.TMR_VALUE_PORTS:
        if not 0 <= value <= spec.TMR_IMM_MAX:
            return f"{name} takes 0..{spec.TMR_IMM_MAX} (S2), got {value}"
    elif name == "alu.op":
        if value not in spec.AluOp._value2member_map_:
            return f"alu.op takes 0..{max(spec.AluOp)}, got {value}"
    elif name in ("mem.t_load", "mem.t_store"):
        if not 0 <= value < ctx["dmem_words"]:
            return f"data address {value} is outside data memory (0..{ctx['dmem_words'] - 1})"
    if not spec.IMM_MIN <= value <= spec.IMM_MAX:
        return f"immediate {value} does not fit in 23 bits ({spec.IMM_MIN}..{spec.IMM_MAX})"
    return None


def port_src_error(src, dst, owner):
    """Why a port-to-port move is not allowed in normal syntax, or None.

    owner is the function name, "main", or None when unknown (no prog.json).
    """
    if dst.name not in spec.CONTROL_PORTS:
        return None
    if src.name == "trap.epc":
        return "trap.epc cannot be a jump or call target (S7)"
    if src.name == "pc.link" and dst.name == "pc.t_jump":
        return None if owner != "main" else "return (pc.link -> pc.t_jump) outside a .func"
    return f"{src.name} -> {dst.name} is an indirect jump; only 'pc.link -> pc.t_jump' is allowed (S5)"


def _encode_move(st, item, evaluate, ctx):
    if item.src is not None:
        err = port_src_error(item.src, item.dst, item.owner)
        if err:
            _err(st, item.line, item.col, err)
            return
        item.word = encode(Move(dst=item.dst.id, imm=False, src=item.src.id))
        return

    node, name = item.node, item.dst.name
    if name in spec.CONTROL_PORTS or name == "trap.handler":
        want = "func" if name in ("pc.t_call", "trap.handler") else "label"
        sym = st.symbols.get(node.name) if isinstance(node, ex.Sym) else None
        ok = sym is not None and sym.section == "text" and (
            sym.kind == "func" if want == "func" else sym.kind in ("label", "func"))
        if not ok:
            what = "a .func name" if want == "func" else "a single .text label"
            _err(st, item.line, item.col, f"the target of {name} must be {what}")
            return
        if want == "label" and sym.value >= ctx["code_len"]:
            _err(st, item.line, item.col, f"label '{node.name}' points past the end of .text")
            return
    value = evaluate(node, item.line)
    if value is None:
        return
    err = value_error(item.dst, value, ctx)
    if err:
        _err(st, item.line, item.col, err)
        return
    item.word = encode(Move(dst=item.dst.id, imm=True, value=value))
    if not isinstance(node, ex.Num):
        item.note = f"= {value}"


def _encode_illegal(st, item, evaluate, ctx):
    value = evaluate(item.node, item.line)
    if value is None:
        return
    if value == 0:
        _err(st, item.line, item.col, ".illegal 0 is not allowed (zero is the memory fill value)")
        return
    if not 0 < value <= spec.WORD_MASK:
        _err(st, item.line, item.col, f".illegal word {value} does not fit in 32 bits")
        return
    item.word = value
    if illegal_reason(value) is not None:
        return
    move = decode(value)
    dst = spec.PORT_BY_ID[move.dst]
    if move.imm:
        normal_err = value_error(dst, move.value, ctx)
        text = f"#{move.value} -> {dst.name}"
    else:
        src = spec.PORT_BY_ID[move.src]
        normal_err = port_src_error(src, dst, item.owner)
        text = f"{src.name} -> {dst.name}"
    if normal_err is None:
        _err(st, item.line, item.col,
             f"0x{value:08x} is a normal move here; write it as '{text}'")


def _is_terminal(item):
    if item.kind == "illegal":
        return True
    return item.dst is not None and item.dst.name in ("pc.t_jump", "trap.t_halt")


def _check_fall_through(st, code_len):
    if code_len == 0 or len(st.code) != code_len:
        return                      # earlier errors already reported
    by_addr = st.code
    for fname, start, end in st.functions:
        if start > 0 and not _is_terminal(by_addr[start - 1]):
            i = by_addr[start - 1]
            _err(st, i.line, i.col, f"code falls through into .func {fname}; "
                                    "end with pc.t_jump, trap.t_halt or .illegal")
        if not _is_terminal(by_addr[end]):
            i = by_addr[end]
            _err(st, i.line, i.col, f"the last move of .func {fname} must be "
                                    "pc.t_jump, trap.t_halt or .illegal")
    last = by_addr[-1]
    if not _is_terminal(last):
        _err(st, last.line, last.col, "the last move of .text must be pc.t_jump, "
                                      "trap.t_halt or .illegal")
