#!/bin/bash
# Phase 3: try the untested candidates on top of base (both JIT flags + parallel compilation).
# Usage: [PASS=quick|full] scripts/run_phase3.sh [prep|fsc|build|all]   (default: all, full). Every step resumes, so a
# quick pass (few runs, for early results) can be topped up by a full pass later.
set -euo pipefail
cd "$(dirname "$0")/.."
D=repos/baseline/.dotnet
what=${1:-all}
if [[ ${PASS:-full} == quick ]]; then NB=5; NC=3; NF=2; NBUILD=2; else NB=20; NC=8; NF=5; NBUILD=5; fi
CONFIGS=configs/phase3.json

if [[ $what == all || $what == prep ]]; then
  # Stats hook with the effective-settings dump (phases 1-2 keep layouts/hook), and the MultiCoreJit hook.
  DOTNET_ROOT=$D $D/dotnet build -c Release tools/FscPerfHook -o layouts/hook2 -nologo -v:q
  DOTNET_ROOT=$D $D/dotnet build -c Release tools/McjHook -o layouts/mcjhook -nologo -v:q
  python3 scripts/make_variant_layout.py layouts/baseline layouts/p3_ccd10 \
    System.Runtime.TieredPGO=false System.Runtime.TieredCompilation.CallCountingDelayMs=10
  python3 scripts/make_variant_layout.py layouts/baseline layouts/p3_tp_min16 System.Threading.ThreadPool.MinThreads=16
  python3 scripts/make_composite_layout.py repos/baseline layouts/baseline layouts/p3_composite \
    || { echo "composite layout failed: r2r_composite is skipped"; rm -rf layouts/p3_composite; }
  rm -rf work/mcj
fi

ONLY_CFGS=$(python3 -c "import json,os;print(','.join(c['id'] for c in json.load(open('$CONFIGS')) if os.path.isdir(c['layout'])))")
echo "configs: $ONLY_CFGS"
B="python3 scripts/bench.py --cmds work/cmds/baseline --configs $CONFIGS --configs-only $ONLY_CFGS --out results/phase3 \
   --variants par --warmup 1 --hook layouts/hook2/FscPerfHook.dll"
if [[ $what == all || $what == fsc ]]; then
  $B --runs $NB --only '^FSharp\.Build__netstandard2\.0$' --seed 303
  $B --runs $NC --only '^FSharp\.Core__netstandard2\.0$' --seed 302
  $B --runs $NF --only '^FSharp\.Compiler\.Service__net11\.0$' --seed 301
fi
if [[ $what == all || $what == build ]]; then
  python3 scripts/buildbench3.py --repo repos/fantomas --sln fantomas.slnx --configs $CONFIGS --configs-only $ONLY_CFGS \
    --out results/phase3_build --runs $NBUILD --warmup 1
fi
echo "run_phase3: $what done"
