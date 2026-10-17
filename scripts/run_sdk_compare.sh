#!/bin/bash
# fsc-level comparison of shipped compilers (SDK 10.0.302 on .NET 10, SDK 11 RC1 on .NET 11, and main@layouts/baseline),
# each with and without DOTNET_TieredPGO=0 + DOTNET_TC_CallCountingDelayMs=0, on identical captured compilations.
set -euo pipefail
cd "$(dirname "$0")/.."
LARGE='^(FSharp\.Compiler\.Service__netstandard2\.0$|FSharp\.Compiler\.Service\.Tests|FSharp\.Compiler\.ComponentTests|FSharp\.Core__netstandard2\.0$|TestTP)__'
SMALL='^(fsc|FSharp\.Build|FSharp\.Test\.Utilities)__'
python3 scripts/bench.py --cmds work/cmds/baseline --configs configs/sdk_compare.json --out results/sdk_compare \
  --runs 6 --warmup 1 --only "$LARGE" --seed 301
python3 scripts/bench.py --cmds work/cmds/baseline --configs configs/sdk_compare.json --out results/sdk_compare \
  --runs 10 --warmup 1 --only "$SMALL" --seed 302
echo "sdk_compare done"
