# F# compiler perf: plan & context

This file holds the durable context for this work. Keep it up to date.

## Goal
Measure how JIT/runtime knobs affect the F# compiler (`fsc`) when it compiles each project
in `FSharp.Compiler.Service.slnx` (dotnet/fsharp). Use production-like compiler binaries,
and write reproducible scripts plus honest HTML and MD reports.

Configurations under test:
| id | what |
|---|---|
| `baseline` | baseline compiler, default runtime settings |
| `pgo0` | `DOTNET_TieredPGO=0` |
| `ccd0` | `DOTNET_TC_CallCountingDelayMs=0` |
| `pgo0_ccd0` | both env vars |
| `jit_flags` | compiler layout built from the `jit_flags` clone/branch (the shippable change, see below), env vars exactly as that branch's targets inject them; should match `pgo0_ccd0` |

### Why jit_flags is a .targets change, not a runtimeconfig change
- The SDK does **not** ship the fsharp repo's `fsc.runtimeconfig.json`. dotnet/sdk
  `src/Layout/redist/tools/tool_fsc.csproj` is published, and its generated `tool_fsc.runtimeconfig.json`
  is copied over `fsc.runtimeconfig.json` (`GenerateLayout.targets`). A runtimeconfig knob added in
  dotnet/fsharp would never reach users. (Official knob names exist: `System.Runtime.TieredPGO`,
  `System.Runtime.TieredCompilation.CallCountingDelayMs`, see runtime eeconfig.cpp on release/10.0.)
- What dotnet/fsharp does ship into `sdk/<v>/FSharp/` is FSharp.Build.dll plus `Microsoft.FSharp.Targets`.
  The `<Fsc>` invocation there passes no `EnvironmentVariables` (a ToolTask parameter). Branch `jit_flags`
  adds `EnvironmentVariables="$(FscEnvironmentVariables)"` and defaults that property to
  `DOTNET_TieredPGO=0;DOTNET_TC_CallCountingDelayMs=0`.
- The perf effect is therefore exactly "env vars on the fsc process". The `jit_flags` config is an
  end-to-end check: a separately built compiler, plus the env captured from that branch's real build.

### Crossgen details (dotnet/sdk `src/Tasks/sdk-tasks/Crossgen.cs`)
`crossgen2 <in.dll> -o <out> --targetos osx --targetarch arm64 -r <FSharpDir>/*.dll -r <sharedfw>/*.dll --perfmap`.
No `-O` and no MIBC are passed explicitly (`tool_fsc.csproj` sets ReadyToRunOptimizationData, but the
redist crossgen step is the custom task above). We mirror that exactly. The SDK crossgens every PE in FSharp/ except
FSharp.Build.dll and *.resources.dll.
The SDK's runtimeconfig is `{GC.Server: true, MetadataUpdater.IsSupported: false,
EnableUnsafeBinaryFormatterSerialization: false}`, and the layout uses the same.

## Repos / layout
- `./` = https://github.com/safesparrow/fsharp_misc (scripts and reports; commit and push as we go)
- `repos/` is gitignored
  - `repos/baseline`: clone of dotnet/fsharp `main` (commit pinned in `results/*/meta.json`)
  - `repos/jit_flags`: second clone, same commit, plus the runtimeconfig change, on branch `jit_flags`
- Compiler changes are pushed to the fork https://github.com/safesparrow/fsharp. Before that, sync the
  fork to dotnet/fsharp. **Never open PRs against dotnet/fsharp.**

## What "production-like" means (verified against the installed SDK 10.0.103)
- `sdk/<v>/FSharp/{fsc,FSharp.Compiler.Service,FSharp.Core,FSharp.DependencyManager.Nuget,...}.dll`
  are ReadyToRun-compiled (per-assembly crossgen2, not composite). FSharp.Build.dll is excluded.
  Source: dotnet/sdk `src/Layout/redist/targets/Crossgen.targets`.
- `fsc.runtimeconfig.json` sets `System.GC.Server: true`.
- fsc runs as `dotnet fsc.dll @rsp` on the shared Microsoft.NETCore.App runtime.
- So: build dotnet/fsharp in Release, take the fsc output dir, crossgen2 each managed PE
  (reference: the same folder plus the shared framework), and keep the runtimeconfig that was built.

## Method
1. Full build of each clone (`./build.sh -c Release`), which gives the Release fsc.
2. Make an R2R layout: `layouts/<name>/` (crossgen2 via the Microsoft.NETCore.App.Crossgen2 pack
   matching the runtime version).
3. Capture the fsc command lines: one full `dotnet build` of `FSharp.Compiler.Service.slnx` with an
   fsc wrapper (FscToolPath/DotnetFscCompilerPath override) that records the cwd and the response file
   for each project. Rewrite outputs (`-o`, `--refout`, `--doc`, `--pdb`...) to a scratch directory so
   perf runs never mutate inputs that other projects reference.
4. Perf runs (`scripts/bench.py`): for each project, run every config round-robin (interleaved) to
   cancel out drift, with W warm-up and N measured runs. Use the same captured args for all configs;
   only the binaries and env differ. Record wall time, user and sys CPU, and peak RSS (os.wait4 rusage).
5. Stats: median, mean, stdev, min, bootstrap 95% CI of the median ratio vs baseline. A change is
   called significant only when the CI excludes 1.0.
6. Reports: `reports/report.md` and `reports/report.html`. The HTML is self-contained, with
   project/config selectors and charts.

## Metrics (user request)
- Wall time, CPU time (user + sys) and peak RSS: from os.wait4 rusage on the fsc process.
- GC pause time, GC counts, allocated MB, JIT methods / JIT time / JIT IL bytes, lock contentions, threadpool:
  from `tools/FscPerfHook`, a DOTNET_STARTUP_HOOKS assembly that writes `$FSCPERF_STATS_FILE` at ProcessExit
  (`GC.GetTotalPauseDuration`, `JitInfo.*`). It's loaded in every config, so the cost is the same for all of them.
- Compilation phases: `--times:<csv>` (Activity CSV export). Top-level phases are children of the
  "FSC compilation" activity. Measure the overhead of hook + times once (A/B) and report it.
- Deeper JIT detail (methods per tier: tier0 / instrumented / tier1 / OSR, rejit counts): separate, fewer
  EventPipe-traced runs. These are perturbed and reported separately from the timings.

## Report requirements (user requests)
- All report data lives in data files (`reports/data/*.json` plus a `data.js` wrapper so file:// works). The HTML
  loads them and has nothing hardcoded. HTML has project/config selectors and charts.
- MD = a short, "safe" version that can go into an official PR or issue as-is: basic tables plus PNG charts
  (matplotlib, `.venv`, `requirements.txt`) where MD can't express it.
- Structure: bullet or numbered lists, not long comma-joined sentences.
- Every chart has a title and a legend, so a screenshot stands alone.
- State the exact hardware used.
- Be honest: give CIs, the noise floor (jit_flags vs pgo0_ccd0 is roughly an A/A pair), and caveats.

## Phase 2 (user request): parallel compilation on/off
After the phase 1 results and reports are done, test every JIT config with and without F# parallel
compilation (`--parallelcompilation+/-`; check what the captured cmdlines already use, and what it
toggles: graph-based checking, parallel optimization, parallel IL gen). Hypothesis from the user: the
benefit of parallel compilation is more visible with the JIT flags set. Report: speedup of par vs seq
under each JIT config, on wall and CPU. Update both reports.

## Verified facts (2026-10-17)
- dotnet/fsharp main @ `99406265c88090c50e575afe8474f0979aa6f815`; pinned SDK 11.0.100-rc.1.26420.103,
  runtime 11.0.0-rc.1.26420.103 (in `repos/*/.dotnet`). crossgen2 is the same version, from the NuGet cache.
- The shipped SDK 11 RC1 `FSharp/` is R2R and uses the same runtimeconfig as SDK 10 (Server GC).
- crossgen2 output with and without `-O` is **byte-identical** (FCS, FSharp.Core). R2R is optimized by default.
- Layout vs shipped SDK 11 RC1 sizes: FCS 48.6 MB vs 49.2 MB, FSharp.Core 5.79 MB vs 5.67 MB (different commits,
  SDK is signed). So it's the same kind of binary.
- `FSharp.Compiler.Service.slnx` builds set `BUILDING_USING_DOTNET=true`. They compile with the repo's
  **Bootstrap** fsc (`artifacts/Bootstrap/fsc/fsc.dll`, per the captured argv), use the FSharp.Core *package*, and write
  to `artifacts/{bin,obj}/<proj>/Release/`, which is shared with build.sh output.
  So the layout is made right after build.sh and before the capture build.
- Fsc task: `ParallelCompilation` defaults to true and emits `--parallelcompilation+`.

- Captured: 17 fsc invocations (14 projects; FSharp.Core x3 TFMs, FCS x2). Response files start with a UTF-8 BOM.
  Baseline wall for all 17 is about 111 s (FCS net11.0 about 27 s wall / 137 s CPU).
- jit_flags branch: commit 43600ddcb on https://github.com/safesparrow/fsharp/tree/jit_flags. The capture build proved
  that all 17 fsc invocations received DOTNET_TieredPGO=0 and DOTNET_TC_CallCountingDelayMs=0 from the targets.
- `--times:<csv>`: the last row (root "FSC compilation") is sometimes truncated because fsc exits before the async CSV
  writer flushes (a small compiler bug). The parser doesn't depend on that row.
- Instrumentation overhead (hook + --times) vs bare: about +2-3% wall, the same in all configs (results/overhead).
- The box is a desktop session: background load is 0.3-1.5 cores (WindowServer, VS Code, Activity Monitor,
  mediaanalysisd, Spotlight), plus about 2.4 GB of swap in use. Hence hostmon.py, the per-run flags, and the report
  sensitivity check without flagged runs.

## Fork state
- safesparrow/fsharp `main` was fast-forwarded to dotnet/fsharp main (no fork-only commits were on it).
- NOT done: deleting the fork's other 96 branches and 88 tags to make it 1:1. Branch `pars_2` is the head of the
  open upstream draft PR dotnet/fsharp#15433, and deleting it would auto-close that PR. The fork also has 2 open
  internal PRs. Waiting for the user's decision.

## Status log
- 2026-10-17: the user interrupted and used the box. The earlier overhead A/B was discarded and redone.
- 2026-10-17: `scripts/run_all.sh all` started (nohup), log at work/logs/run_all.log. It's resumable: re-running skips
  recorded runs.
