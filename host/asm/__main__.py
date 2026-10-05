"""py -m host.asm prog.tta [-o build] [--imem N] [--dmem N]"""
import argparse
import sys
from pathlib import Path

from host.common import spec

from .assembler import AssemblyFailed, assemble
from .output import output_files


def main(argv=None):
    ap = argparse.ArgumentParser(prog="py -m host.asm", description="TTA assembler")
    ap.add_argument("source", type=Path)
    ap.add_argument("-o", "--outdir", type=Path, default=Path("build"))
    ap.add_argument("--imem", type=int, default=spec.IMEM_WORDS, help="code memory words")
    ap.add_argument("--dmem", type=int, default=spec.DMEM_WORDS, help="data memory words")
    args = ap.parse_args(argv)

    try:
        text = args.source.read_text(encoding="utf-8")
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        prog = assemble(text, args.source.name, args.imem, args.dmem)
    except AssemblyFailed as e:
        print(e.render(), file=sys.stderr)
        return 1

    stem = args.source.name.removesuffix(".tta")
    args.outdir.mkdir(parents=True, exist_ok=True)
    for suffix, content in output_files(prog).items():
        (args.outdir / f"{stem}{suffix}").write_text(content, encoding="utf-8", newline="\n")
    print(f"{args.source.name}: {prog.code_len} code words, {prog.data_len} data words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
