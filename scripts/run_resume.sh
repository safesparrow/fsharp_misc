#!/bin/bash
# Resume everything after the 2026-10-17 pause, in priority order (each step resumes where it stopped):
#   1. fsc-level SDK 10 vs SDK 11 vs main comparison (the user's question)
#   2. build-level `dotnet build` of Fantomas: SDK 10 / SDK 11 x none / env_all / env_fsc
#   3. main pipeline (phase 1, phase 2, traces)
set -euo pipefail
cd "$(dirname "$0")/.."
./scripts/run_sdk_compare.sh
python3 scripts/buildbench.py --repo repos/fantomas --sln fantomas.slnx \
  --sdks sdk10=work/sdks/dotnet10,sdk11=repos/baseline/.dotnet --out results/buildbench --runs 6 --warmup 1
echo "buildbench done"
./scripts/run_all.sh all
echo "run_resume: all done"
