"""Output files of the assembler (toolchain_formats.md §2–§4)."""
import json
from pathlib import PurePath

from host.common import spec

FORMAT = 1


def hex_text(words):
    return "".join(f"{w:08x}\n" for w in words)


def metadata(prog):
    base = PurePath(prog.filename).name
    symbols = {}
    for name, sym in prog.symbols.items():
        entry = {"kind": sym.kind, "value": sym.value}
        if sym.kind != "equ":
            entry["section"] = sym.section
        symbols[name] = entry
    return {
        "format": FORMAT,
        "spec": spec.SPEC_ID,
        "source": base,
        "imem_words": prog.imem_words,
        "dmem_words": prog.dmem_words,
        "code_len": prog.code_len,
        "data_len": prog.data_len,
        "symbols": symbols,
        "functions": [{"name": n, "start": s, "end": e} for n, s, e in prog.functions],
        "annotations": sorted(prog.annotations, key=lambda a: (a["addr"], a["kind"])),
        "source_map": [{"addr": i.addr, "file": base, "line": i.line} for i in prog.code_items],
    }


def json_text(prog):
    return json.dumps(metadata(prog), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def listing_text(prog):
    rows = {}
    for i in prog.code_items:
        rows.setdefault(i.line, []).append((f"{i.addr:04x}", i.word, i.note))
    for d in prog.data_items:
        rows.setdefault(d.line, []).append((f"D{d.addr:04x}", d.word, None))
    out = ["addr   word      line  source"]
    for lineno, src in enumerate(prog.source_lines, 1):
        src = src.rstrip()
        items = rows.get(lineno)
        if not items:
            out.append(f"{'':6} {'':8}  {lineno:>5}  {src}".rstrip())
            continue
        addr, word, note = items[0]
        tail = f"  ; {note}" if note else ""
        out.append(f"{addr:<6} {word:08x}  {lineno:>5}  {src}{tail}".rstrip())
        for addr, word, _ in items[1:]:
            out.append(f"{addr:<6} {word:08x}")
    return "\n".join(out) + "\n"


def output_files(prog):
    """{suffix: text} for every output; written only after assembly succeeded."""
    return {
        ".code.hex": hex_text(prog.code),
        ".data.hex": hex_text(prog.data),
        ".json": json_text(prog),
        ".lst": listing_text(prog),
    }
