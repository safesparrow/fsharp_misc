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
  python3 scripts/bench.py --cmds $CMDS --configs configs/phase1.json --configs-only baseline,pgo0_ccd0 \
    --out results/phase2 --variants seq,par --runs 20 --warmup 1 --only "$SMALL" --seed 201
  python3 scripts/bench.py --cmds $CMDS --configs configs/phase1.json --configs-only baseline,pgo0_ccd0 \
    --out results/phase2 --variants seq,par --runs 10 --warmup 1 --only "$LARGE" --seed 202
fi
if [[ $what == all || $what == traces ]]; then
  python3 scripts/trace.py --cmds $CMDS --configs configs/phase1.json --out results/traces --runs 3 --variants par
fi
echo "run_all: $what done"
