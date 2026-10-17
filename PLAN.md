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
| `jit_flags_rc` | compiler built from the `jit_flags` clone, with both knobs baked into `fsc.runtimeconfig.json` (the way the change would actually ship); checks that it matches `pgo0_ccd0` |

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

## Status log
- (see git history and the sections below as they're filled in)
