"""The Vivado report procedures, in tclsh with stand-ins for Vivado (audit A6-1, A6-5).

Vivado itself is not run. The stand-ins answer the commands the procs call
(report_utilization, get_cells, get_msg_config, report_power, ...) with fixed
data, so these tests check what the procs compute and print, and that
nrv.tcl and tta.tcl still parse. Whether Vivado accepts each command line is
only shown by running nrv_report / tta_report in Vivado.

Needs a tclsh (Git for Windows ships one); skipped otherwise.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VIVADO = ROOT / "fpga" / "vivado"
TCLSH = shutil.which("tclsh") or next(
    (p for p in (Path("C:/Program Files/Git/mingw64/bin/tclsh.exe"),) if p.exists()), None)
pytestmark = pytest.mark.skipif(TCLSH is None, reason="no tclsh")


def tcl(script, tmp_path):
    path = tmp_path / "t.tcl"
    path.write_text(script, encoding="utf-8")
    r = subprocess.run([str(TCLSH), str(path)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def q(p):
    return "{" + Path(p).as_posix() + "}"


UTIL = """| Site Type                  | Used | Fixed | Prohibited | Available | Util% |
+----------------------------+------+-------+------------+-----------+-------+
| Slice LUTs                 | {n} |     0 |          0 |     63400 |  2.97 |
|   LUT as Logic             | {n} |     0 |          0 |     63400 |  2.97 |
| Slice Registers            |  999 |     0 |          0 |    126800 |  1.48 |
"""

# Vivado stand-ins: utilisation per cell, cell counts per filter, message counters
STUBS = r"""
set ::util [dict create "" 1882 u_core 1445 u_env 370]
proc report_utilization {args} {
    if {"-return_string" ni $args} { return }
    set i [lsearch $args -cells]
    set cell [expr {$i < 0 ? "" : [lindex $args [expr {$i + 1}]]}]
    if {![dict exists $::util $cell]} { return "no table" }
    return [string map [list {{n}} [dict get $::util $cell]] $::util_text]
}
proc get_cells {args} {
    if {[lindex $args 0] ne "-hier"} { return [lindex $args 0] }
    set f [lindex $args end]
    set n [expr {[string match *u_core/* $f] ? 10 : 20}]
    if {[string match *LUT* $f]} { incr n 100 }
    if {[string match *RAMB18* $f]} { set n 1 }
    return [lrepeat $n c]
}
set ::warnings 7
proc get_msg_config {args} {
    if {[lindex $args end] eq "WARNING"} { return $::warnings }
    return 0
}
proc report_power {args} {
    incr ::warnings 2
    set f [open [lindex $args end] w]
    puts $f "| Total On-Chip Power (W)  | 0.117        |"
    close $f
}
"""


def test_slice_luts_is_read_from_the_used_column(tmp_path):
    out = tcl(f"""source {q(VIVADO / "report_common.tcl")}
puts [rpt_slice_luts {{{UTIL.replace("{n}", "1882")}}}]
puts [rpt_slice_luts {{| Slice LUTs*                | 3609 |     0 |}}]
puts [rpt_slice_luts {{| LUT as Logic | 12 |}}]
""", tmp_path)
    assert out.split() == ["1882", "3609", "?"]                # never a guess


@pytest.mark.parametrize("rpt, used", [("vivado_tta", 1882), ("vivado_nrv_c1", 3609), ("vivado_nrv_c0", 2965)])
def test_slice_luts_of_the_saved_phase6_reports(tmp_path, rpt, used):
    path = ROOT / "build" / rpt / "utilization.rpt"
    if not path.exists():
        pytest.skip("Phase 6 Vivado reports not in build/")
    out = tcl(f"""source {q(VIVADO / "report_common.tcl")}
set f [open {q(path)}]; puts [rpt_slice_luts [read $f]]; close $f
""", tmp_path)
    assert out.strip() == str(used)


def test_block_line_reports_slice_luts_and_the_cell_count(tmp_path):
    out = tcl(f"""source {q(VIVADO / "report_common.tcl")}
set ::util_text {{{UTIL}}}
{STUBS}
puts [rpt_block tta_report total ""]
puts [rpt_block tta_report core u_core]
puts [rpt_block tta_report uart u_uart]
""", tmp_path)
    lines = out.splitlines()
    assert lines[0] == "tta_report: total  Slice LUTs   1882 (LUT cells    120)  FF     20  BRAM36  20.5  DSP  20"
    assert lines[1] == "tta_report: core   Slice LUTs   1445 (LUT cells    110)  FF     10  BRAM36  10.5  DSP  10"
    assert "Slice LUTs      ?" in lines[2]                     # no table for that cell: "?"


def test_power_counts_the_warnings_it_issues(tmp_path):
    out = tcl(f"""source {q(VIVADO / "report_common.tcl")}
set ::util_text {{}}
{STUBS}
puts [rpt_power {q(tmp_path / "p.rpt")}]
proc get_msg_config {{args}} {{ error "not in this Vivado" }}
puts [rpt_power {q(tmp_path / "p.rpt")}]
""", tmp_path)
    assert out.splitlines() == ["2 0", "? ?"]


def test_nrv_report_end_to_end_writes_the_summary(tmp_path):
    out = tcl(f"""source {q(VIVADO / "nrv.tcl")}
set ::util_text {{{UTIL}}}
{STUBS}
dict set ::util u_soc 3598
proc nrv_open {{cache}} {{}}
proc current_design {{args}} {{ return impl_1 }}
proc nrv_timing {{}} {{ return {{0.339 0.027}} }}
proc report_timing_summary {{args}} {{}}
proc nrv_dir {{cache}} {{ return {q(tmp_path)} }}
nrv_report 1
""", tmp_path)
    summary = (tmp_path / "report_summary.txt").read_text().splitlines()
    assert summary[0].startswith("nrv_report: c1, ") and summary[0].endswith("WNS 0.339 ns, WHS 0.027 ns")
    assert summary[1].startswith("nrv_report: total  Slice LUTs   1882 (LUT cells")
    assert summary[2].startswith("nrv_report: system Slice LUTs   3598")
    assert summary[-1] == ("nrv_report: total on-chip power 0.117 W (vectorless estimate); report_power issued "
                           "2 WARNING and 0 CRITICAL WARNING messages (their text is in the Vivado log)")
    assert all(line in out for line in summary)                 # printed and saved alike


def test_tta_report_end_to_end_writes_the_summary(tmp_path):
    (tmp_path / "build" / "vivado_tta").mkdir(parents=True)
    out = tcl(f"""proc get_property {{args}} {{ return {q(tmp_path)} }}
proc current_project {{args}} {{}}
source {q(VIVADO / "tta.tcl")}
set ::util_text {{{UTIL}}}
{STUBS}
proc tta_timing {{}} {{ return {{0.429 0.056}} }}
proc report_timing_summary {{args}} {{}}
proc get_filesets {{args}} {{}}
tta_report
""", tmp_path)
    summary = (tmp_path / "build" / "vivado_tta" / "report_summary.txt").read_text().splitlines()
    assert summary[0].endswith("WNS 0.429 ns, WHS 0.056 ns")
    assert summary[2].startswith("tta_report: total  Slice LUTs   1882")
    assert summary[3].startswith("tta_report: core   Slice LUTs   1445")
    assert "issued 2 WARNING" in summary[-1] and summary[-1] in out
