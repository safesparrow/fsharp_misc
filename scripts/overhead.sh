#!/bin/bash
# A/B: cost of the measurement instrumentation (startup hook + --times:csv) vs a bare fsc run.
# Interleaves the two modes, one run at a time, baseline config only.
set -euo pipefail
cd "$(dirname "$0")/.."
N=${N:-10}; ONLY=${ONLY:-'^(fsc__|FSharp.Core__netstandard2.0)'}
for i in $(seq 1 "$N"); do
  python3 scripts/bench.py --cmds work/cmds/baseline --configs configs/phase1.json --configs-only baseline \
    --out results/overhead/instrumented --runs "$i" --warmup 0 --only "$ONLY" --tag instrumented
  python3 scripts/bench.py --cmds work/cmds/baseline --configs configs/phase1.json --configs-only baseline \
    --out results/overhead/bare --runs "$i" --warmup 0 --only "$ONLY" --no-hook --no-times --tag bare
done
