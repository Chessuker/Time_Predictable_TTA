#!/usr/bin/env bash
# Convert baseline/rtl/neorv32_baseline.vhd (NEORV32 + our configuration) into one
# plain-Verilog module with GHDL, for Verilator. Run from the repository root
# inside the Ubuntu-24.04 WSL distro with oss-cad-suite loaded:
#
#   baseline/sim/convert.sh <out_dir> <CACHE_EN 0|1> <BOOT_MODE 0|1> [TRACE_EN 0|1]
#
# Writes <out_dir>/neorv32_baseline.v (module neorv32_baseline, generics frozen).
# GHDL works inside <out_dir> (ghdl -m leaves an executable in its working directory).
set -euo pipefail
root=$(pwd)
out=$(mkdir -p "$1" && cd "$1" && pwd)
cache=$2
boot=$3
trace=${4:-0}
src=$(sed "s#\$NEORV32_HOME#$root/baseline/neorv32#" baseline/neorv32/rtl/file_list_core.f | tr -d '\r')
cd "$out"
mkdir -p work
ghdl -i --std=08 --work=neorv32 --workdir=work $src "$root/baseline/rtl/neorv32_baseline.vhd"
ghdl -m --std=08 --work=neorv32 --workdir=work neorv32_baseline
ghdl synth --std=08 --work=neorv32 --workdir=work -gCACHE_EN="$cache" -gBOOT_MODE="$boot" -gTRACE_EN="$trace" \
  --out=verilog neorv32_baseline > neorv32_baseline.v
echo "wrote $out/neorv32_baseline.v"
