# fsharp_misc: F# compiler performance experiments

Scripts and reports that measure how runtime/JIT settings affect the F# compiler (`fsc`) when it compiles
the projects of dotnet/fsharp's `FSharp.Compiler.Service.slnx`. The compiler binaries are production-like:
ReadyToRun, laid out the way the .NET SDK ships them.

- Report (short, shareable): [`reports/report.md`](reports/report.md)
- Report (interactive, all metrics): https://safesparrow.github.io/fsharp_misc/reports/report.html
  (`reports/report.html`; it also works opened locally, since it loads `reports/data/report-data.js`)
- Phase 3, runtime knobs on top of the JIT flags: https://safesparrow.github.io/fsharp_misc/reports/phase3/report.html
  ([`reports/phase3/report.md`](reports/phase3/report.md))
- GitHub Pages serves the root of `main`, so a push updates the site.
- All report numbers: [`reports/data/summary.json`](reports/data/summary.json). Raw runs: `results/*/runs.jsonl`.
- Plan and context: [`PLAN.md`](PLAN.md)
- Compiler change under test: branch [`jit_flags`](https://github.com/safesparrow/fsharp/tree/jit_flags) on the fork.

## Layout

| path | what |
|---|---|
| `repos/` (gitignored) | `baseline` = dotnet/fsharp main, `jit_flags` = same commit + the branch change |
| `layouts/` (gitignored) | SDK-like R2R compiler layouts, plus the built helper tools |
| `work/` (gitignored) | captured command lines, scratch outputs, logs |
| `configs/phase1.json` | benchmark configurations (layout + env vars) |
| `scripts/` | the pipeline, see below |
| `tools/FscPerfHook` | startup hook: writes GC and JIT counters at process exit |
| `tools/TraceSummary` | summarizes an EventPipe trace: JIT methods per tier, GC pauses |
| `results/` | raw benchmark data (committed) |
| `reports/` | generated reports |

## Reproduce

Prerequisites: macOS on Apple Silicon (the host monitor uses Mach APIs), git, python3, network access.
The pinned .NET SDK is downloaded by the fsharp build.

```bash
# 1. Clone and build both compilers (each clone fetches its pinned SDK into .dotnet/)
git clone https://github.com/dotnet/fsharp repos/baseline
git clone repos/baseline repos/jit_flags
git -C repos/jit_flags remote add fork https://github.com/safesparrow/fsharp
git -C repos/jit_flags fetch fork jit_flags && git -C repos/jit_flags checkout -b jit_flags FETCH_HEAD
(cd repos/baseline && ./build.sh -c Release)
(cd repos/jit_flags && ./build.sh -c Release)

# 2. SDK-like ReadyToRun layouts. Run this BEFORE step 3: the slnx build overwrites artifacts/.
python3 scripts/make_layout.py repos/baseline layouts/baseline
python3 scripts/make_layout.py repos/jit_flags layouts/jit_flags

# 3. Capture every fsc command line from a full build of the solution
python3 scripts/capture.py repos/baseline work/cmds/baseline
python3 scripts/capture.py repos/jit_flags work/cmds/jit_flags   # also proves the branch injects the env vars

# 4. Helper tools (stats hook, trace summarizer), built with the repo's SDK
D=repos/baseline/.dotnet
DOTNET_ROOT=$D $D/dotnet build -c Release tools/FscPerfHook -o layouts/hook
DOTNET_ROOT=$D $D/dotnet build -c Release tools/TraceSummary -o layouts/tracesummary

# 5. Benchmarks (hours; resumable). Close busy apps first, since the host monitor flags background load.
N=10 scripts/overhead.sh          # instrumentation overhead A/B
scripts/run_all.sh all            # phase 1, phase 2, traces

# 6. Reports
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/report.py
```

## Scripts

| script | does |
|---|---|
| `make_layout.py` | copies the built fsc, crossgen2s each assembly like dotnet/sdk's `Crossgen.targets`, writes the SDK's runtimeconfig |
| `capture.py` + `fsc-capture-wrapper.sh` | full `dotnet build` of the .slnx with the Fsc task's host replaced by a wrapper that records each response file |
| `bench.py` | replays the captured compilations, with configs interleaved in shuffled order every round; records rusage, the hook's counters, `--times` phases and host activity |
| `hostmon.py` | host CPU and memory monitor (Mach `host_statistics`). Waits for a quiet box before each run and measures foreign CPU during it |
| `trace.py` | separate EventPipe-traced runs, summarized by `tools/TraceSummary` |
| `overhead.sh` | A/B of the instrumentation (hook + `--times`) against bare runs |
| `report.py`, `report_md.py`, `report_template.html` | statistics (bootstrap CIs) and the MD, HTML and data files |
| `hardware.py` | writes `results/hardware.json` (no serial numbers or UUIDs) |
