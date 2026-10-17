## Summary

Tested: `DOTNET_TieredPGO=0` and `DOTNET_TC_CallCountingDelayMs=0` for the fsc process, on all 17 compilations of FSharp.Compiler.Service.slnx, replayed in isolation on an Apple M3 Pro (11 cores, macOS), with fsc laid out like the .NET SDK ships it (ReadyToRun, Server GC). Numbers are changes in the median with a 95% bootstrap CI.

### 1. Both flags together: about 45% less wall time and 33% less CPU

- All 17 compilations together (sum of medians): **105.8 s → 58.3 s wall (−44.9%, CI −45.8% to −44.5%)**, and **CPU −33.0%** (CI −35.2% to −31.6%).
- Each flag alone: `TieredPGO=0` −17.1% wall, `CallCountingDelayMs=0` −27.6% wall. Together they do more than the two alone would multiply to (about −40%).
- Large and medium compilations (5 s to 25 s): **−39% to −56% wall**, with lower CPU (−8% to −43%).
  - FSharp.Compiler.Service net11.0: 24.4 s → 13.5 s (−44.6%), CPU −43.1%.
  - FSharp.Core netstandard2.0: −56.3% wall, CPU −27.9%.
- Small compilations (under about 1.5 s): only −3% to −19% wall, and **CPU goes up 20% to 49%**. In a process this short, recompiling startup code at tier 1 costs more CPU than it saves.
- GC pause time does not change with the flags (within ±1.3%).
- The `jit_flags` branch (the flags set by `Microsoft.FSharp.Targets` instead of by hand) measures the same as setting the env vars (A/A difference +0.0%, CI −0.3% to +0.7%).

### 2. Parallel compilation, with and without the flags

- All compilations together, parallel vs sequential: **−27.7% wall without the flags, −34.4% with them**. CPU goes up 71% without the flags and 36% with them.
- Largest compilation (FSharp.Compiler.Service): the flags make parallel compilation help more: −31% becomes **−45%**. Flags plus parallel compilation take it from 35.1 s to 13.7 s.
- Mid-size and small compilations: parallel compilation helps less with the flags. For example FCS.Tests goes from −39% to −24%. The flags shrink the parallel phases, so the serial parts (startup, writing the binary) are a larger share of what is left.
- For every compilation, flags plus parallel compilation is still the fastest of the four combinations.

### 3. SDK 10 vs SDK 11, and whole builds

- On the compilers as shipped in SDK 10.0.302 and SDK 11 RC1 (no SDK files changed, flags as env vars), all 8 measured compilations together: **SDK 10 −48.6% wall / −41.9% CPU**, **SDK 11 −44.2% wall / −32.4% CPU**.
- Without the flags, SDK 10 and SDK 11 are within about ±10% of each other. With the flags, SDK 10 is 0% to 18% faster than SDK 11 on most compilations.
- With the same `fsc.runtimeconfig.json`, .NET 11 runs about 5 times as many gen0 GCs as .NET 10 on FSharp.Compiler.Service, with a 35% smaller peak heap. Total GC pause is about the same.
- Whole `dotnet build` of Fantomas: **−23% wall** with the flags set on `dotnet build`, on both SDKs. With the flags on fsc processes only: −19%. Build-level CPU changes were not significant.

### 4. Measurement quality and caveats

- Disturbed runs (host monitor): 50 of 1380 in phase 1, 13 of 640 in phase 2. Excluding them does not change the totals (−44.8% instead of −44.9%).
- Instrumentation cost (stats hook and `--times`) is +2% to +3% wall and not significant, in every config alike.
- One machine: 11-core Apple M3 Pro, macOS, .NET 11 RC1. Linux x64 machines with more cores may differ, especially in how much the background tier-1 JIT costs.
- Compilations are replayed one at a time. In a real build several run at once, which is why the whole-build gain is smaller.

## Untested candidates for further gains

None of these were benchmarked in phases 1-2; phase 3 tests several of them (separate report: `reports/phase3/report.html`). Each item gives the evidence from the data above, and how to test it. Scope: SDK 11 / `main`, parallel compilation on, both flags set.

### 1. GC: pauses are 11-24% of wall time, and the flags don't change them

- Evidence (both flags, total pause from `GC.GetTotalPauseDuration`):
  - FSharp.Compiler.Service ns2.0: **2.96 s of 12.4 s (24%)**, 33 GB allocated, 112 gen0 / 47 gen1 / 12 gen2 GCs.
  - ComponentTests: 0.68 s of 3.9 s (17%). FCS.Tests: 0.51 s of 3.2 s (16%). FSharp.Core ns2.0: 0.50 s of 4.6 s (11%).
  - Pause time is the same with and without the flags. As JIT time shrinks, GC becomes a larger share of what remains.
- .NET 10 and .NET 11 behave differently with an identical `fsc.runtimeconfig.json`. For FCS, .NET 11 runs about 5x more gen0 GCs (112 vs 23) with a 35% smaller peak heap (2.7 vs 4.3 GB), but total pause is about the same (~2.9 s). This fits a change in DATAS tuning; not verified.
- Since 5x more collections give the same total pause, pause time is probably dominated by marking and promoting the compiler's long-lived data (typed trees, symbol tables), not by collection count.
- To try:
  - A larger gen0 budget (`DOTNET_GCgen0size`, or `GCGen0MaxBudget`), so fewer GCs promote live data.
  - `DOTNET_GCDynamicAdaptationMode=0` (DATAS off).
  - `DOTNET_GCConcurrent=0`. A batch process gains little from background gen2 GC, which costs CPU.
  - In whole builds: `DOTNET_GCHeapCount` (e.g. 4), or turning off heap affinitization. Several fsc processes with 11 Server GC heaps each run on 11 cores at once. This may be part of why the build-level gain (-23%) is smaller than the per-compilation gain.
  - Longer term: fewer allocations (FCS allocates about 2.6 GB/s). This needs compiler code changes.

### 2. JIT: ship the flags properly, and lower startup JIT cost

- Ship the flags as runtimeconfig properties of fsc and fsi. That means `System.Runtime.TieredPGO: false` and `System.Runtime.TieredCompilation.CallCountingDelayMs: 0` in dotnet/sdk `tool_fsc.csproj`. This scopes them to the compiler. An env var on `dotnet build` also changes MSBuild, which accounted for about 4 of the 23 points at build level.
- Why the default is slow for a compiler (reading of the runtime design, not traced): the 100 ms call-counting delay is extended whenever new tier-0 code is jitted. A compiler keeps jitting new methods for seconds, so hot methods stay at tier 0. For FCS net11.0, `CallCountingDelayMs=0` alone gives about -28% wall and `TieredPGO=0` alone about -20%. Together they give about -44%.
- Tiny compilations (under ~1 s) pay +21-42% CPU with the flags, because startup code is recompiled at tier 1 almost at once. A small non-zero delay (e.g. 10-20 ms) may keep most of the win without that. Untested.
- A lot of code is still jitted despite R2R. Even `fsc.dll` (0.8 s) jits about 2,800 methods in about 180 ms of JIT time. These are probably generic instantiations over FSharp.Core types, closures, and methods R2R rejected. Options:
  - MultiCoreJit in fsc's entry point (`System.Runtime.ProfileOptimization.SetProfileRoot` + `StartProfile`). It records startup methods and pre-jits them on background threads in later runs. Roughly a two-line change.
  - Crossgen2 `--inputbubble` (or a composite image) for FSharp.Compiler.Service + FSharp.Core. This allows cross-assembly inlining and pre-compiled generic instantiations.
  - MIBC profiles for crossgen2 (`--mibc`). The SDK's crossgen step passes neither `-O` nor any profile today.
- NativeAOT would remove JIT entirely, but the compiler's reflection use and FSharp.Core `printf` make it a long-term option at best.

### 3. Parallelism and serial phases

- FCS with both flags averages 5.1 busy cores out of 11 (69 s CPU in 13.5 s wall).
- Phases (`--times`, both flags): Optimizations 4.9 s, Typecheck 3.4 s, **Write .NET Binary 1.8 s**, TAST -> IL 1.7 s, Parse 0.8 s.
- `Write .NET Binary` (13% of wall) is likely single-threaded, and so a clear target. Per-phase CPU wasn't recorded, so which phases are serial is not confirmed.
- FCS ends with about 31 thread-pool threads on 11 cores. That suggests blocking waits on pool threads, which make the pool inject threads. Lower confidence; worth checking in a trace.

### 4. Suggested order

1. Move the flags into fsc's runtimeconfig. The gain is measured and the change is small.
2. Sweep GC settings on FCS and FSharp.Core, both in isolation and in a whole build.
3. Prototype MultiCoreJit in fsc. It mainly helps the many small projects in real codebases.
4. Try crossgen2 `--inputbubble` / composite and MIBC.
5. A compiler server removes most of the JIT cost and part of the GC cost (warm code, reused heap). It is the larger, separate step.
