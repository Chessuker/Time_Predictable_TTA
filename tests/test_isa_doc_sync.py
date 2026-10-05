"""Check that docs/isa.md and host/common/spec.py still agree.

Only this test reads the Markdown. Production code imports spec.py.
"""
import re
from pathlib import Path

import pytest

from host.common import spec

ISA_MD = Path(__file__).resolve().parents[1] / "docs" / "isa.md"


@pytest.fixture(scope="module")
def isa_lines():
    return ISA_MD.read_text(encoding="utf-8").splitlines()


def section(lines, heading):
    """Lines under a '## <heading>' up to the next '## '."""
    start = next(i for i, l in enumerate(lines) if l.startswith("## ") and heading in l)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start + 1:end]


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def test_port_table(isa_lines):
    doc = {}
    row = re.compile(r"^\| `0x([0-9A-F]{2})`(?:–`0x([0-9A-F]{2})`)? \|")
    for line in section(isa_lines, "Port Map"):
        m = row.match(line)
        if not m:
            continue
        _, name, direction, _ = cells(line)
        if direction == "—":
            continue                              # reserved IDs, checked below
        first, last = int(m.group(1), 16), int(m.group(2) or m.group(1), 16)
        names = [f"r{i}" for i in range(16)] if first != last else [name.strip("`")]
        for pid, pname in zip(range(first, last + 1), names):
            doc[pid] = (pname, direction)

    code = {}
    for p in spec.PORTS:
        flags = "/".join(f for f, on in (("R", p.readable), ("W", p.writable), ("T", p.trigger)) if on)
        code[p.id] = (p.name, flags)
    assert doc == code


def test_alu_opcodes(isa_lines):
    doc = {}
    for line in section(isa_lines, "ALU Operations"):
        m = re.match(r"^\| (\d+) \| `(\w+)` \|", line)
        if m:
            doc[m.group(2)] = int(m.group(1))
    assert doc == {op.name: op.value for op in spec.AluOp}


def test_trap_causes(isa_lines):
    doc = set()
    for line in section(isa_lines, "Trap"):
        m = re.match(r"^\| (\d) \| ", line)
        if m:
            doc.add(int(m.group(1)))
    assert doc == {c.value for c in spec.TrapCause}


def test_timing_constants(isa_lines):
    doc = {}
    for line in section(isa_lines, "Timing Parameters"):
        m = re.match(r"^\| ([DPHR]) \(.*?\) \| (?:cycle )?(\d+) \|", line)
        if m:
            doc[m.group(1)] = int(m.group(2))
    assert doc == {"D": spec.D, "P": spec.P, "H": spec.H, "R": spec.R}


def test_fu_latencies(isa_lines):
    doc = {}
    for line in section(isa_lines, "Timing Parameters"):
        m = re.match(r"^\| (ALU|MUL|CMP|MEM load) \| (\d+) \|", line)
        if m:
            doc[m.group(1)] = int(m.group(2))
    output_of = {"ALU": "alu.out", "MUL": "mul.out", "CMP": "cmp.eq", "MEM load": "mem.data_out"}
    assert doc == {k: spec.PORT_BY_NAME[v].latency for k, v in output_of.items()}
    assert {p.latency for p in spec.PORTS if p.fu == "cmp" and p.latency} == {doc["CMP"]}


def test_flags_bits(isa_lines):
    doc = {}
    for line in section(isa_lines, "Timer"):
        m = re.match(r"^\| (\d) \| `(\w+)` \|", line)
        if m:
            doc[m.group(2)] = int(m.group(1))
    assert doc == {f.name.lower(): f.value for f in spec.TmrFlag}
