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
    if len(code) != meta["imem_words"] or len(data) != meta["dmem_words"]:
        raise ImageError(f"{stem}: hex length does not match imem_words/dmem_words")
    return code, data, meta
