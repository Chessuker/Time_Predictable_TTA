# Helper commands for the Vivado Tcl Console. With Time_Predictable_TTA.xpr
# open, load them once per session:
#
#   source [get_property DIRECTORY [current_project]]/fpga/vivado/tta.tcl
#
# Then:
#   tta_setup         register the HDL in Time_Predictable_TTA.srcs with the project
#                     (run again after adding a file); sets the top and the generics
#   tta_asm ?name?    assemble programs/<name>.tta (default board_hello) into build/board
#                     and make it the image arty_tta_top loads at synthesis
#   tta_use name      switch the image to an already assembled program
#   tta_timing        after implementation: print WNS/WHS of the routed design
#   tta_paths ?n?     tta_timing plus one line per worst setup path
#
# Build and program with the usual buttons: Generate Bitstream, then
# Open Hardware Manager -> Program Device.

proc tta_root {} {
    return [get_property DIRECTORY [current_project]]
}

proc tta_setup {} {
    set root [tta_root]
    set srcs [file join $root Time_Predictable_TTA.srcs]

    # drop entries whose file no longer exists (e.g. the old rtl/ layout)
    foreach f [get_files -quiet] {
        if {![file exists $f]} {
            puts "tta_setup: removing missing file $f"
            remove_files $f
        }
    }

    proc add_once {fileset path} {
        if {[llength [get_files -quiet -of_objects [get_filesets $fileset] $path]] == 0} {
            add_files -norecurse -fileset $fileset $path
        }
    }
    foreach f [lsort [glob -directory [file join $srcs sources_1 new] *.sv]] {
        add_once sources_1 $f
        set_property file_type SystemVerilog [get_files $f]
    }
    foreach f [glob -directory [file join $srcs constrs_1 new] *.xdc] { add_once constrs_1 $f }
    foreach f [lsort [glob -directory [file join $srcs sim_1 new] *.sv]] {
        add_once sim_1 $f
        set_property file_type SystemVerilog [get_files $f]
    }

    set_property top arty_tta_top [get_filesets sources_1]
    set_property top tb_tta [get_filesets sim_1]
    set_property verilog_define TTA_SIM [get_filesets sim_1]

    # keep the program chosen with tta_asm/tta_use; board_hello the first time
    if {[get_property generic [get_filesets sources_1]] eq ""} { tta_use board_hello }

    puts "tta_setup: top = [get_property top [get_filesets sources_1]], [llength [get_files -of_objects [get_filesets sources_1]]] design files"
}

# The images are generated into build/ (not in git). Absolute paths, because
# synthesis runs inside Time_Predictable_TTA.runs/synth_1. Changing them marks
# synthesis out of date, so the next Generate Bitstream picks the new program.
proc tta_use {name} {
    set img [file join [tta_root] build board]
    set_property generic [list \
        "IMEM_INIT=\"[file join $img $name.code.hex]\"" \
        "DMEM_INIT=\"[file join $img $name.data.hex]\"" \
    ] [get_filesets sources_1]
    puts "tta_use: arty_tta_top now loads $name (build/board/$name.*.hex)"
}

proc tta_asm {{name board_hello}} {
    set root [tta_root]
    set src [file join $root programs $name.tta]
    if {![file exists $src]} { error "no such program: $src" }
    # Python finds the host package only from the repository root, and Vivado's
    # working directory is usually elsewhere: run from the root, then go back.
    # Vivado also points PYTHONHOME/PYTHONPATH at its own bundled Python, which
    # breaks the system Python; hide them for this call only.
    set saved {}
    foreach v {PYTHONHOME PYTHONPATH} {
        if {[info exists ::env($v)]} { dict set saved $v $::env($v); unset ::env($v) }
    }
    set here [pwd]
    cd $root
    set rc [catch {exec py -m host.asm $src -o [file join $root build board] 2>@1} out]
    cd $here
    dict for {k v} $saved { set ::env($k) $v }
    puts $out
    if {$rc} { error "tta_asm: assembler failed" }
    tta_use $name
}

proc tta_timing {} {
    set run [get_runs impl_1]
    if {[get_property PROGRESS $run] ne "100%"} {
        error "tta_timing: impl_1 has not finished ([get_property STATUS $run]); run Generate Bitstream first"
    }
    # current_design only warns when nothing is open, so compare its name
    if {[current_design -quiet] ne "impl_1"} { open_run impl_1 }
    set wns [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -setup]]
    set whs [get_property SLACK [get_timing_paths -max_paths 1 -nworst 1 -hold]]
    if {$wns >= 0 && $whs >= 0} { set verdict "met" } else { set verdict "NOT met" }
    puts "tta_timing: WNS = $wns ns, WHS = $whs ns ($verdict at 100 MHz)"
}

# One line per worst setup path: slack, logic levels, start -> end.
proc tta_paths {{n 15}} {
    tta_timing
    foreach p [get_timing_paths -max_paths $n -nworst 1 -setup] {
        puts [format "%8s ns  %2s lvl  %s -> %s" [get_property SLACK $p] [get_property LOGIC_LEVELS $p] \
            [get_property STARTPOINT_PIN $p] [get_property ENDPOINT_PIN $p]]
    }
}
