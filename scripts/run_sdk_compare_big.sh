#!/bin/bash
# The two largest compilations for the SDK comparison. They were skipped by a regex typo in the first
# run_sdk_compare.sh run; this adds them to the same results file (resumable).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/bench.py --cmds work/cmds/baseline --configs configs/sdk_compare.json --out results/sdk_compare \
  --runs 6 --warmup 1 --only '^(FSharp\.Compiler\.Service__netstandard2\.0|FSharp\.Core__netstandard2\.0)$' --seed 303
echo "sdk_compare_big done"
