# Phase 6 baseline (NEORV32) in Vivado, from the Tcl Console. The baseline has
# its own projects under build/ (not in git), so Time_Predictable_TTA.xpr is never
# touched. With any project open (or none), load once per session:
#
#   source <repo>/fpga/vivado/nrv.tcl
#
# Then:
#   nrv_build cache   create build/vivado_nrv_c<cache>/nrv_c<cache>.xpr (cache 0 or 1),
#                     synthesise, implement and write the bitstream; prints WNS/WHS
#   nrv_report cache  timing, utilisation of the baseline system and of NEORV32
#                     alone, and the power estimate, into build/vivado_nrv_c<cache>/
#   nrv_bit cache     path of the bitstream, for Program Device
#
# Program with the Hardware Manager as usual, then use the host scripts in
# host/baseline (they upload the programs through the NEORV32 bootloader).
# To go back to the TTA core: open_project <repo>/Time_Predictable_TTA.xpr

# the repository root: this file is <root>/fpga/vivado/nrv.tcl
set ::nrv_root [file normalize [file join [file dirname [info script]] .. ..]]

proc nrv_dir {cache} { return [file join $::nrv_root build vivado_nrv_c$cache] }

proc nrv_create {cache} {
    if {$cache ne "0" && $cache ne "1"} { error "nrv_create: cache must be 0 or 1" }
    set root $::nrv_root
    create_project -force nrv_c$cache [nrv_dir $cache] -part xc7a100tcsg324-1

    # NEORV32 v1.13.6 (git submodule) in library neorv32, in the order of its file list
    set fl [open [file join $root baseline neorv32 rtl file_list_core.f] r]
    set vhd {}
    foreach line [split [read $fl] "\n"] {
        set line [string trim $line]
        if {$line eq ""} continue
        lappend vhd [string map [list {$NEORV32_HOME} [file join $root baseline neorv32]] $line]
    }
    close $fl
    lappend vhd [file join $root baseline rtl neorv32_baseline.vhd]
    add_files -norecurse $vhd
    set_property library neorv32 [get_files $vhd]
    set_property file_type {VHDL 2008} [get_files $vhd]

    # our SystemVerilog: the shared plant/UART/FIFO of the TTA design and the baseline system
    set srcs [file join $root Time_Predictable_TTA.srcs sources_1 new]
    set sv {}
    foreach f {tta_pkg.sv plant_pkg.sv dc_motor_plant_mc.sv hil_env.sv fu_telem.sv uart_tx_word.sv} {
        lappend sv [file join $srcs $f]
    }
    foreach f {xbus_mem.sv xbus_hil.sv baseline_soc.sv arty_neorv32_top.sv} {
        lappend sv [file join $root baseline rtl $f]
    }
    add_files -norecurse $sv
    set_property file_type SystemVerilog [get_files $sv]

    add_files -fileset constrs_1 -norecurse [file join $root baseline fpga arty_neorv32.xdc]
    set_property top arty_neorv32_top [get_filesets sources_1]
    set_property generic "CACHE_EN=$cache" [get_filesets sources_1]
    update_compile_order -fileset sources_1
}

proc nrv_open {cache} {
    if {[current_project -quiet] eq "nrv_c$cache"} return     ;# already the open project
    set xpr [file join [nrv_dir $cache] nrv_c$cache.xpr]
    if {![file exists $xpr]} { nrv_create $cache } else { open_project $xpr }
}

proc nrv_timing {} {
    if {[current_design -quiet] ne "impl_1"} { open_run impl_1 }
    set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
    set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
    if {$wns >= 0 && $whs >= 0} { set verdict "met" } else { set verdict "NOT met" }
    puts "nrv_timing: WNS = $wns ns, WHS = $whs ns ($verdict at 100 MHz)"
    return [list $wns $whs]
}

proc nrv_build {cache} {
    nrv_open $cache
    reset_run synth_1
    launch_runs impl_1 -to_step write_bitstream -jobs 4
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
        error "nrv_build: impl_1 did not finish: [get_property STATUS [get_runs impl_1]]"
    }
    nrv_timing
    puts "nrv_build: bitstream [nrv_bit $cache]"
}

proc nrv_bit {cache} {
    return [file join [nrv_dir $cache] nrv_c$cache.runs impl_1 arty_neorv32_top.bit]
}

proc nrv_report {cache} {
    nrv_open $cache
    if {[current_design -quiet] ne "impl_1"} { open_run impl_1 }
    set out [nrv_dir $cache]
    nrv_timing
    report_timing_summary -file [file join $out timing.rpt]
    report_utilization -hierarchical -hierarchical_depth 3 -file [file join $out utilization_hier.rpt]
    report_utilization -file [file join $out utilization.rpt]
    report_power -file [file join $out power.rpt]
    foreach {name cell} {system u_soc cpu u_soc/u_cpu} {
        set c [get_cells $cell]
        puts [format "nrv_report: %-6s LUT %6d  FF %6d  BRAM36 %5.1f  DSP %3d" $name \
            [llength [get_cells -hier -quiet -filter "PRIMITIVE_GROUP == LUT && NAME =~ $cell/*"]] \
            [llength [get_cells -hier -quiet -filter "PRIMITIVE_GROUP == FLOP_LATCH && NAME =~ $cell/*"]] \
            [expr {[llength [get_cells -hier -quiet -filter "REF_NAME == RAMB36E1 && NAME =~ $cell/*"]] \
                   + 0.5 * [llength [get_cells -hier -quiet -filter "REF_NAME == RAMB18E1 && NAME =~ $cell/*"]]}] \
            [llength [get_cells -hier -quiet -filter "REF_NAME =~ DSP48* && NAME =~ $cell/*"]]]
    }
    set w "?"
    regexp {Total On-Chip Power \(W\)\s*\|\s*([0-9.]+)} [report_power -return_string] -> w
    puts "nrv_report: total on-chip power $w W (vectorless estimate)"
    puts "nrv_report: reports in $out"
}
