"""Load an assembled program and refuse images built for another spec
(toolchain_formats.md §3.1)."""
import json
from pathlib import Path

from host.common import spec

FORMAT = 1


class ImageError(ValueError):
    pass


def _hex(path):
    try:
        return [int(line, 16) for line in Path(path).read_text(encoding="utf-8").splitlines()]
    except ValueError as e:
        raise ImageError(f"{path}: {e}") from None


def load(stem):
    """stem = path without suffix, e.g. build/ctrl -> build/ctrl.code.hex etc."""
    stem = str(stem)
    meta = json.loads(Path(stem + ".json").read_text(encoding="utf-8"))
    if meta.get("format") != FORMAT:
        raise ImageError(f"{stem}.json: format {meta.get('format')} is not {FORMAT}")
    if meta.get("spec") != spec.SPEC_ID:
        raise ImageError(f"{stem}.json: built for spec {meta.get('spec')!r}, "
                         f"this ISS implements {spec.SPEC_ID!r}")
    code, data = _hex(stem + ".code.hex"), _hex(stem + ".data.hex")
    check_invariants(code, data, meta, stem)
    return code, data, meta


def check_invariants(code, data, meta, where="image"):
    """Contract between tools (toolchain_formats.md §3). The assembler already
    guarantees these; checking again here stops a hand-edited or damaged image
    from entering the ISS."""
    def fail(msg):
        raise ImageError(f"{where}: {msg}")

    imem, dmem = meta["imem_words"], meta["dmem_words"]
    code_len, data_len = meta["code_len"], meta["data_len"]
    if len(code) != imem or len(data) != dmem:
        fail("hex length does not match imem_words/dmem_words")
    if not 0 <= code_len <= imem:
        fail(f"code_len {code_len} is outside 0..{imem}")
    if not 0 <= data_len <= dmem:
        fail(f"data_len {data_len} is outside 0..{dmem}")
    if any(code[code_len:]):
        fail(f"non-zero code word after code_len {code_len}")
    if any(data[data_len:]):
        fail(f"non-zero data word after data_len {data_len}")
    if not all(code[:code_len]):
        fail(f"zero code word before code_len {code_len}")
    prev_end = -1
    for f in meta["functions"]:
        if not prev_end < f["start"] <= f["end"] < code_len:
            fail(f"function {f['name']} {f['start']}..{f['end']} is out of order, "
                 f"overlaps, or lies outside code_len {code_len}")
        prev_end = f["end"]
