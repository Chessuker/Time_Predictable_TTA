# Report helpers shared by nrv_report (nrv.tcl) and tta_report (tta.tcl).
# nrv.tcl and tta.tcl source this file; source it alone only for tests
# (tests/test_vivado_tcl.py runs these procs in tclsh with stand-ins for the
# Vivado commands they call).
#
# LUTs (audit A6-1). Two counts differ: Vivado's "Slice LUTs" in
# report_utilization (one per physical 6-input LUT, after two small functions
# are combined into one), and the number of LUT primitive cells (each such
# function counted). The Phase 6 tables printed the cell count and called it
# LUT; it runs 13 to 17 % above Slice LUTs. Slice LUTs is what the reports and
# the docs use now; the cell count is printed next to it for continuity.

# The Used column of the "Slice LUTs" row of a report_utilization text, or "?"
# when there is no such row (never a guess).
proc rpt_slice_luts {text} {
    if {[regexp -line {^\|\s*Slice LUTs\*?\s*\|\s*([0-9]+)\s*\|} $text -> n]} { return $n }
    return "?"
}

# One block of the design: cell "" is the whole design.
proc rpt_block {prefix name cell} {
    if {$cell eq ""} {
        set in ""
        set util [report_utilization -return_string]
    } else {
        set in " && NAME =~ $cell/*"
        set util [report_utilization -cells [get_cells $cell] -return_string]
    }
    return [format "%s: %-6s Slice LUTs %6s (LUT cells %6d)  FF %6d  BRAM36 %5.1f  DSP %3d" $prefix $name \
        [rpt_slice_luts $util] \
        [llength [get_cells -hier -quiet -filter "PRIMITIVE_GROUP == LUT$in"]] \
        [llength [get_cells -hier -quiet -filter "PRIMITIVE_GROUP == FLOP_LATCH$in"]] \
        [expr {[llength [get_cells -hier -quiet -filter "REF_NAME == RAMB36E1$in"]] \
               + 0.5 * [llength [get_cells -hier -quiet -filter "REF_NAME == RAMB18E1$in"]]}] \
        [llength [get_cells -hier -quiet -filter "REF_NAME =~ DSP48*$in"]]]
}

# Messages of one severity issued so far in this session, or "?" if Vivado
# cannot say.
proc rpt_msg_count {severity} {
    if {[catch {get_msg_config -count -severity $severity} n]} { return "?" }
    return $n
}

# report_power into file, and how many WARNING and CRITICAL WARNING messages
# it issued (audit A6-5: Vivado prints them to the console and the log, not to
# the report, and has no Tcl call that returns their text).
proc rpt_power {file} {
    set w0 [rpt_msg_count WARNING]
    set c0 [rpt_msg_count {CRITICAL WARNING}]
    report_power -file $file
    set w1 [rpt_msg_count WARNING]
    set c1 [rpt_msg_count {CRITICAL WARNING}]
    if {"?" in [list $w0 $w1]} { set w "?" } else { set w [expr {$w1 - $w0}] }
    if {"?" in [list $c0 $c1]} { set c "?" } else { set c [expr {$c1 - $c0}] }
    return [list $w $c]
}

# The printed lines, also into a file next to the reports, so every number in
# the docs has a saved source.
proc rpt_save {path lines} {
    set f [open $path w]
    puts $f [join $lines \n]
    close $f
}
