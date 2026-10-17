#!/bin/bash
# Full benchmark pipeline. Every step is resumable (bench.py and trace.py skip runs that are already recorded).
#   phase 1: JIT configs, parallel compilation on (as captured)
#   phase 2: parallel compilation on/off x {baseline, pgo0_ccd0}
#   traces : EventPipe JIT-tier / GC detail (separate runs; tracing perturbs timings)
# Usage: scripts/run_all.sh [phase1|phase2|traces|all]   (default: all)
set -euo pipefail
cd "$(dirname "$0")/.."
SMALL='^(AssemblyCheck|FSharp\.Build|FSharp\.Compiler\.Interactive\.Settings|FSharp\.DependencyManager\.Nuget|FSharp\.Test\.Utilities|fsc|fsi|fslex|fsyacc)__'
LARGE='^(FSharp\.Compiler\.Service|FSharp\.Compiler\.Service\.Tests|FSharp\.Compiler\.ComponentTests|FSharp\.Core|TestTP)__'
TINY='^(AssemblyCheck|FSharp\.Build|FSharp\.Compiler\.Interactive\.Settings|FSharp\.DependencyManager\.Nuget|fsc|fsi|fslex|fsyacc)__'
LARGE_P2='^(FSharp\.Compiler\.Service__.*|FSharp\.Compiler\.Service\.Tests__.*|FSharp\.Compiler\.ComponentTests__.*|FSharp\.Core__netstandard2\.0|TestTP__.*)$'
TRACE_ONLY='^(?!FSharp\.Core__(net10\.0|netstandard2\.1)$)'
CMDS=work/cmds/baseline
what=${1:-all}

python3 scripts/hardware.py > /dev/null

if [[ $what == all || $what == phase1 ]]; then
  python3 scripts/bench.py --cmds $CMDS --configs configs/phase1.json --out results/phase1 --variants par \
    --runs 20 --warmup 1 --only "$SMALL" --seed 101
  python3 scripts/bench.py --cmds $CMDS --configs configs/phase1.json --out results/phase1 --variants par \
    --runs 12 --warmup 1 --only "$LARGE" --seed 102
fi
if [[ $what == all || $what == phase2 ]]; then
  # Trimmed to fit the time budget: the 8 tiny compilations get 10 runs (their effects are far outside the noise),
  # and FSharp.Core is measured for netstandard2.0 only (net10.0 / netstandard2.1 compile the same sources).
  P2_ARGS="--cmds $CMDS --configs configs/phase1.json --configs-only baseline,pgo0_ccd0 --out results/phase2 --variants seq,par --warmup 1"
  python3 scripts/bench.py $P2_ARGS --runs 10 --only "$TINY" --seed 201
  python3 scripts/bench.py $P2_ARGS --runs 20 --only '^FSharp\.Test\.Utilities__' --seed 203
  python3 scripts/bench.py $P2_ARGS --runs 10 --only "$LARGE_P2" --seed 202
fi
if [[ $what == all || $what == traces ]]; then
  # Traces explain JIT tiering; baseline vs both flags covers that.
  python3 scripts/trace.py --cmds $CMDS --configs configs/phase1.json --configs-only baseline,pgo0_ccd0 \
    --out results/traces --runs 3 --variants par --only "$TRACE_ONLY"
fi
echo "run_all: $what done"
