"""Disassembler (toolchain_formats.md §5).

With prog.json the output assembles back to the same .hex files (round-trip).
Without it the output is for people: trailing zero words are trimmed, control
targets stay numeric and function boundaries are unknown.

    py -m host.asm.disasm build/prog.code.hex [--data FILE] [--meta FILE] [--no-meta]
"""
import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from host.common import spec
from host.common.encoding import decode, illegal_reason

from .assembler import port_src_error, value_error

INDENT = " " * 8
LABEL_TARGET_PORTS = spec.CONTROL_PORTS + ("trap.handler",)


class ImageError(ValueError):
    pass


@dataclass
class Disassembly:
    text: str
    warnings: list[str] = field(default_factory=list)


def read_hex(path):
    words = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        try:
            words.append(int(line, 16))
        except ValueError:
            raise ImageError(f"{path}:{n}: not a hex word: {line!r}") from None
    return words


def _trimmed_len(words):
    n = len(words)
    while n and words[n - 1] == 0:
        n -= 1
    return n


def disassemble(code, data=None, meta=None):
    data = data or []
    warnings = []
    if meta is not None:
        code_len, data_len = meta["code_len"], meta["data_len"]
        dmem = meta["dmem_words"]
        if len(code) != meta["imem_words"] or (data and len(data) != dmem):
            raise ImageError("hex length does not match imem_words/dmem_words in prog.json")
        if any(code[code_len:]) or any(data[data_len:]):
            warnings.append("non-zero words past code_len/data_len: the image is damaged")
        if not all(code[:code_len]):
            warnings.append("zero word inside code_len: the image is damaged")
        funcs = [f for f in meta["functions"] if f["name"] != "main"]
        func_starts = {f["start"] for f in funcs}
    else:
        code_len, data_len = _trimmed_len(code), _trimmed_len(data)
        dmem = len(data) or spec.DMEM_WORDS
        funcs, func_starts = [], None

    text_labels, data_labels = _labels(meta, code_len)
    ctx = dict(code_len=code_len, dmem_words=dmem, func_starts=func_starts)

    starts = {f["start"]: f["name"] for f in funcs}
    # every jump target needs a name when we have prog.json (call and handler
    # targets are always .func starts, which already have a name)
    if meta is not None:
        for addr in range(code_len):
            target = _control_target(code[addr], ctx, spec.JUMP_PORTS)
            if target is not None and target not in text_labels and target not in starts:
                name = f"L_{target:04x}"
                while name in meta["symbols"]:
                    name += "_"
                text_labels[target] = [name]
    names = None
    if meta is not None:
        names = {a: ls[0] for a, ls in text_labels.items()}
        for a, fname in starts.items():
            names.setdefault(a, fname)

    owner_of = {}
    for f in funcs:
        for a in range(f["start"], f["end"] + 1):
            owner_of[a] = f["name"]
    ends = {f["end"] for f in funcs}
    notes = {}
    for a in (meta or {}).get("annotations", []):
        line = f"@task {a['name']}" if a["kind"] == "task" else f"@loop_bound {a['n']}"
        notes.setdefault(a["addr"], []).append(line)

    out = [f"; disassembly{'' if meta is not None else ' (no prog.json: not round-trip safe)'}",
           "        .text"]
    for addr in range(code_len + 1):
        out += _labels_at(addr, text_labels.get(addr, []), notes.get(addr, []),
                          starts.get(addr))
        if addr == code_len:
            break
        owner = owner_of.get(addr, "main") if meta is not None else None
        out.append(INDENT + _render(code[addr], ctx, owner, names, starts))
        if addr in ends:
            out.append(".endfunc")

    if data_len or data_labels:
        out.append("")
        out.append("        .data")
        for addr in range(data_len + 1):
            for name in data_labels.get(addr, []):
                out.append(f"{name}:")
            if addr < data_len:
                out.append(f"{INDENT}.word 0x{data[addr]:08x}")
    return Disassembly("\n".join(out) + "\n", warnings)


def _labels(meta, code_len):
    text, data = {}, {}
    for name, sym in sorted((meta or {}).get("symbols", {}).items()):
        if sym["kind"] == "label":
            (text if sym["section"] == "text" else data).setdefault(sym["value"], []).append(name)
    return text, data


def _labels_at(addr, labels, notes, func_name):
    """.func, then one label per annotation (an annotation must precede a label)."""
    out = []
    if func_name:
        out.append(f".func {func_name}")
    labels = list(labels)
    for i, note in enumerate(notes):
        if i == 0 and func_name and not labels:
            out.insert(0, note)            # annotation directly on the .func line
            continue
        out.append(note)
        out.append(f"{labels.pop(0) if labels else f'L_{addr:04x}_{i}'}:")
    out += [f"{name}:" for name in labels]
    return out


def _control_target(word, ctx, ports):
    if illegal_reason(word) is not None:
        return None
    m = decode(word)
    dst = spec.PORT_BY_ID[m.dst]
    if m.imm and dst.name in ports and value_error(dst, m.value, ctx) is None:
        return m.value
    return None


def _render(word, ctx, owner, names, func_starts):
    """names: address -> symbol for jump targets (None without prog.json)."""
    if illegal_reason(word) is not None:
        return f".illegal 0x{word:08x}"
    m = decode(word)
    dst = spec.PORT_BY_ID[m.dst]
    if not m.imm:
        src = spec.PORT_BY_ID[m.src]
        if port_src_error(src, dst, owner):
            return f".illegal 0x{word:08x}"
        return f"{src.name:<12} -> {dst.name}"
    if value_error(dst, m.value, ctx):
        return f".illegal 0x{word:08x}"
    if dst.name == "null" and m.value == 0:
        return "nop"
    if names is not None and dst.name in LABEL_TARGET_PORTS:
        # call and handler targets must be written as the .func name
        name = func_starts[m.value] if dst.name not in spec.JUMP_PORTS else names[m.value]
        src = "#" + name
    elif dst.name == "alu.op":
        src = "#" + spec.AluOp(m.value).name
    else:
        src = f"#{m.value}"
    return f"{src:<12} -> {dst.name}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="py -m host.asm.disasm", description="TTA disassembler")
    ap.add_argument("code", type=Path, help="prog.code.hex")
    ap.add_argument("--data", type=Path, help="prog.data.hex (default: sibling file)")
    ap.add_argument("--meta", type=Path, help="prog.json (default: sibling file)")
    ap.add_argument("--no-meta", action="store_true", help="ignore prog.json")
    args = ap.parse_args(argv)

    stem = str(args.code).removesuffix(".code.hex")
    data_path = args.data or Path(stem + ".data.hex")
    meta_path = None if args.no_meta else (args.meta or Path(stem + ".json"))
    try:
        code = read_hex(args.code)
        data = read_hex(data_path) if data_path.exists() else []
        meta = None
        if meta_path is not None and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("format") != 1 or meta.get("spec") != spec.SPEC_ID:
                raise ImageError(f"{meta_path}: format/spec does not match this tool")
        result = disassemble(code, data, meta)
    except (OSError, ImageError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for w in result.warnings:
        print(f"warning: {w}", file=sys.stderr)
    sys.stdout.write(result.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
