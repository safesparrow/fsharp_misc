# fsc: turn off tiered PGO and the call-counting delay

_Short report. Interactive version: [https://safesparrow.github.io/fsharp_misc/reports/pr/report.html](https://safesparrow.github.io/fsharp_misc/reports/pr/report.html). Full reports: [JIT flags, parallel compilation, SDK 10 vs 11](https://safesparrow.github.io/fsharp_misc/reports/report.html) · [other runtime settings](https://safesparrow.github.io/fsharp_misc/reports/phase3/report.html). Generated 2026-10-17 19:21 BST._

**Contents:** [Summary](#summary) · [The change](#the-change) · [Results per compilation](#results-per-compilation) · [Whole build](#whole-build) · [Where the time goes](#where-the-time-goes) · [Setup and method](#setup-and-method) · [Measurement quality](#measurement-quality) · [Full reports and reproduction](#full-reports-and-reproduction)

## Summary

- Setting `System.Runtime.TieredPGO=false` and `System.Runtime.TieredCompilation.CallCountingDelayMs=0` for the fsc process makes compilation much faster on .NET 11.
- All 14 compilations of `FSharp.Compiler.Service.slnx` together (newest target framework of each project, parallel compilation on): **63.1 s → 36.3 s wall (−42.5%, 95% CI −43.4% to −41.9%)**, **CPU −31.0%** (−32.7% to −29.4%).
- Medium and large compilations (6 of 14, 1.5 s to 25 s): wall **−56% to −36%**, CPU −43% to +2%. FSharp.Compiler.Service itself: 24.4 s → 13.5 s.
- Small compilations (8, under 1.5 s): wall −19% to −3%, but CPU **+20% to +49%**: the process ends before recompiling startup code pays off.
- Whole `dotnet build` of Fantomas (SDK 11, 6 runs per mode): **−18.7% wall** with the settings on fsc only, −23.4% when also applied to MSBuild.
- GC pause time is unchanged (−1.1%). JIT CPU time goes up (+202%), mostly on the background tier-1 thread, which is what buys the speed-up.

<details><summary>Why it helps</summary>

- fsc is short-lived and keeps JIT-compiling new methods for most of its run.
- By default, methods are promoted from tier 0 to tier 1 only after a 100 ms call-counting delay, and the runtime extends that delay while new tier-0 methods keep appearing. In a compiler that can keep hot code at tier 0 for seconds.
- Tiered PGO adds an instrumented tier before tier 1, which delays optimised code further.
- Each setting alone helps (phase 1, all 17 compilations: `TieredPGO=0` −17.1%, `CallCountingDelayMs=0` −27.6%); together they do more than either.
- A 10 ms delay instead of 0 was also tried (phase 3, preliminary): slower on every target measured (+8% to +49% wall), so 0 is the right value.

</details>

## The change

- Branch [`jit_flags_runtimeconfig`](https://github.com/safesparrow/fsharp/tree/jit_flags_runtimeconfig) on the fork, commit [`2133b6e91`](https://github.com/safesparrow/fsharp/commit/2133b6e918e966961b92280b26962eafe910220d), based on dotnet/fsharp `99406265c`: two settings in `src/fsc/fsc.targets`, written to `fsc.runtimeconfig.json` at build time.
- `DOTNET_TieredPGO` / `DOTNET_TC_CallCountingDelayMs` env vars still override them.
- **The .NET SDK does not ship this file.** dotnet/sdk generates the runtimeconfig of the fsc it ships (`src/Layout/redist/tools/tool_fsc.csproj`), so the same two settings are needed there for SDK users to get the effect.
- Equivalent alternatives that were also measured: the env vars set on the fsc process (the numbers below), and the Fsc MSBuild task passing them (branch `jit_flags`): identical within noise (A/A difference +0.0%, CI −0.3% to +0.7%).

```diff
@@ -18,6 +18,18 @@
     <NoWarn>$(NoWarn);MSB3277</NoWarn>
   </PropertyGroup>
 
+  <!-- JIT settings for the compiler process, written to fsc.runtimeconfig.json.
+       fsc is short-lived and keeps JIT-compiling new methods for most of its run. With the default settings, the
+       call-counting delay is extended by every new tier-0 method, so hot code waits a long time for tier 1, and tiered
+       PGO adds an instrumented tier before it. Turning both off makes compilation substantially faster.
+       DOTNET_TieredPGO / DOTNET_TC_CallCountingDelayMs env vars still override these. -->
+  <PropertyGroup>
+    <TieredPGO>false</TieredPGO>
+  </PropertyGroup>
+  <ItemGroup>
+    <RuntimeHostConfigurationOption Include="System.Runtime.TieredCompilation.CallCountingDelayMs" Value="0" />
+  </ItemGroup>
+
   <!--  The fsc application does not provide a referencable public interface  -->
   <PropertyGroup>
     <NoOptimizationData>true</NoOptimizationData>
```

## Results per compilation

![Change per compilation](https://safesparrow.github.io/fsharp_misc/reports/pr/img/pr_change.png)

<details open><summary>Table</summary>

| Compilation | Defaults | Flags | Δ wall (95% CI) | Δ CPU (95% CI) |
|---|---:|---:|---|---|
| FSharp.Compiler.Service · net11.0 | 24.36 s | 13.49 s | **-44.6% (-46.6 to -43.4)** | **-43.1% (-45.5 to -41.0)** |
| FSharp.Core · net10.0 | 10.74 s | 4.71 s | **-56.1% (-57.1 to -55.5)** | **-26.9% (-28.7 to -25.6)** |
| FSharp.Compiler.ComponentTests · net11.0 | 6.48 s | 3.93 s | **-39.3% (-41.4 to -38.5)** | **-27.1% (-27.9 to -25.4)** |
| TestTP · netstandard2.0 | 6.02 s | 2.81 s | **-53.4% (-54.3 to -51.7)** | **-8.2% (-10.9 to -6.1)** |
| FSharp.Compiler.Service.Tests · net11.0 | 5.34 s | 3.18 s | **-40.5% (-42.4 to -37.3)** | **-21.0% (-22.8 to -19.7)** |
| FSharp.Test.Utilities · net11.0 | 3.10 s | 1.97 s | **-36.4% (-37.9 to -34.5)** | +1.6% (-2.9 to +3.7) |
| fsyacc · net11.0-osx-arm64 | 1.36 s | 1.10 s | **-18.6% (-19.3 to -17.7)** | **+30.2% (+29.0 to +34.1)** |
| fslex · net11.0-osx-arm64 | 1.10 s | 0.95 s | **-14.0% (-14.6 to -13.8)** | **+27.9% (+27.5 to +28.7)** |
| fsi · net11.0 | 1.06 s | 0.90 s | **-15.1% (-15.8 to -12.7)** | **+19.6% (+18.0 to +26.1)** |
| FSharp.Build · netstandard2.0 | 1.03 s | 0.89 s | **-13.3% (-13.8 to -12.1)** | **+28.9% (+27.4 to +35.3)** |
| fsc · net11.0 | 0.79 s | 0.67 s | **-14.1% (-15.5 to -12.5)** | **+36.4% (+31.2 to +41.1)** |
| FSharp.DependencyManager.Nuget · netstandard2.0 | 0.70 s | 0.68 s | **-2.5% (-4.5 to -2.0)** | **+42.7% (+40.7 to +43.2)** |
| AssemblyCheck · net11.0-osx-arm64 | 0.55 s | 0.51 s | **-7.9% (-9.1 to -6.1)** | **+49.4% (+47.1 to +50.8)** |
| FSharp.Compiler.Interactive.Settings · netstandard2.0 | 0.47 s | 0.46 s | **-2.5% (-4.0 to -1.3)** | **+42.4% (+39.8 to +47.3)** |
| **All 14** | 63.1 s | 36.3 s | **-42.5% (-43.4 to -41.9)** | **-31.0% (-32.7 to -29.4)** |

Bold: the 95% CI excludes 0.

</details>

## Whole build

`dotnet build fantomas.slnx -c Release --no-incremental` (Fantomas `f734124bc`, 12 projects) on SDK 11 RC1, 6 interleaved runs per mode, build servers shut down before each run.

| Settings applied to | Wall | Δ wall (95% CI) | Δ CPU (95% CI) |
|---|---:|---|---|
| nothing (defaults) | 30.6 s | | |
| fsc processes only (what this change does) | | **-18.7% (-22.7 to -17.2)** | -6.2% (-14.4 to +7.7) |
| `dotnet build` (MSBuild too) | | **-23.4% (-27.0 to -21.9)** | -8.5% (-15.2 to +2.4) |

Build CPU is measured system-wide and is noisy; neither CPU change is significant.

## Where the time goes

<details><summary>Compiler phases, GC and JIT</summary>

![Compiler phases](https://safesparrow.github.io/fsharp_misc/reports/pr/img/pr_phases.png)

| All compilations, sum of medians | Defaults | Flags | Δ (95% CI) |
|---|---:|---:|---|
| Wall time | 63.1 s | 36.3 s | **-42.5% (-43.4 to -41.9)** |
| CPU time | 210.0 s | 144.8 s | **-31.0% (-32.7 to -29.4)** |
| JIT time (all threads) | 9.1 s | 27.6 s | **+201.9% (+190.1 to +206.2)** |
| GC pause time | 6.1 s | 6.1 s | -1.1% (-2.0 to +0.6) |
| Phase: Import references | 2.9 s | 2.3 s | **-19.3% (-20.2 to -18.4)** |
| Phase: Parse | 2.2 s | 2.0 s | **-7.2% (-8.6 to -5.2)** |
| Phase: Typecheck | 23.6 s | 12.2 s | **-48.2% (-51.6 to -47.5)** |
| Phase: Optimizations | 18.1 s | 9.5 s | **-47.3% (-48.2 to -45.4)** |
| Phase: TAST -> IL | 5.8 s | 3.6 s | **-39.3% (-41.2 to -34.9)** |
| Phase: Write .NET binary | 6.1 s | 3.6 s | **-41.9% (-42.7 to -39.5)** |
| Phase: Other phases | 2.4 s | 1.4 s | **-44.3% (-47.0 to -42.2)** |
| Phase: Startup & exit | 1.5 s | 1.5 s | +0.1% (-0.8 to +1.0) |

</details>

## Setup and method

<details><summary>Hardware, software, method</summary>

- **Machine**: MacBook Pro (Mac15,6, model number MRX33B/A)
- **Chip**: Apple M3 Pro
- **CPU cores**: 11 = 5 performance + 6 efficiency (no SMT; 11 logical)
- **Caches**: P-cluster L2 16 MB, E-cluster L2 4 MB, L1d 128 KB (P) / 64 KB (E), cache line 128 B
- **Memory**: 18 GB unified (LPDDR5)
- **Storage**: internal Apple SSD (APFS)
- **Power**: AC Power; Low Power Mode off
- **Thermal**: no thermal or CPU-power throttling warnings recorded (pmset -g therm)

- **OS**: macOS 27.0.1 (26A434), kernel Darwin 27.0.0
- **Firmware**: 20457.1.29
- **dotnet/fsharp commit**: 99406265c88090c50e575afe8474f0979aa6f815
- **Runtime**: Microsoft.NETCore.App 11.0.0-rc.1.26420.103
- **Fork branch**: https://github.com/safesparrow/fsharp/tree/jit_flags_runtimeconfig (2133b6e91)

1. Build dotnet/fsharp `99406265c` in Release and lay fsc out the way the .NET SDK ships it: ReadyToRun via crossgen2 per assembly, and the SDK's `fsc.runtimeconfig.json` (Server GC).
2. Capture every fsc command line from one full `dotnet build FSharp.Compiler.Service.slnx -c Release` (parallel compilation is on by default).
3. Replay each compilation in isolation with identical inputs: 14 compilations, the newest target framework of each project.
4. Two configs: **baseline** (defaults) and **flags** (`DOTNET_TieredPGO=0`, `DOTNET_TC_CallCountingDelayMs=0` on the fsc process). Every round runs both, in shuffled order.
5. Runs: 20 per config for the small and mid-size projects, 12 for the five largest, after 1 warm-up. Changes are medians with a 95% bootstrap CI; a change counts only when its CI excludes 0.
6. A host monitor records system CPU and memory around every run; disturbed runs are flagged, not dropped.

</details>

## Measurement quality

<details><summary>Disturbed runs and caveats</summary>

- 24 of 480 runs were flagged as disturbed by other activity on the machine (rule: foreign CPU > 1.0 core avg during the run, or host busy >= 1.0 core right before it, or any swapout, or > 5000 page compressions). Excluding them does not change the totals.
- The stats hook and `--times` cost about 2% to 3% wall in every config alike (not significant).
- One machine: an 11-core Apple M3 Pro on macOS. Machines with more cores have more idle cores for the background tier-1 JIT, so gains on Linux x64 build machines may differ and are worth confirming.
- Compilations are replayed one at a time. In a real build several run in parallel, which is why the whole-build gain is smaller.

</details>

## Full reports and reproduction

- Full report (all 17 compilations and TFMs, every metric, parallel compilation on/off, SDK 10 vs 11): [https://safesparrow.github.io/fsharp_misc/reports/report.html](https://safesparrow.github.io/fsharp_misc/reports/report.html) ([MD](https://github.com/safesparrow/fsharp_misc/blob/main/reports/report.md))
- Other runtime settings tried on top of these (GC modes, DATAS, MultiCoreJit, composite R2R): [https://safesparrow.github.io/fsharp_misc/reports/phase3/report.html](https://safesparrow.github.io/fsharp_misc/reports/phase3/report.html) ([MD](https://github.com/safesparrow/fsharp_misc/blob/main/reports/phase3/report.md))
- Scripts and raw data: [https://github.com/safesparrow/fsharp_misc](https://github.com/safesparrow/fsharp_misc)
- Compiler change: [https://github.com/safesparrow/fsharp/tree/jit_flags_runtimeconfig](https://github.com/safesparrow/fsharp/tree/jit_flags_runtimeconfig)

```
scripts/run_all.sh phase1            # benchmark (resumes)
.venv/bin/python scripts/report.py   # full report
.venv/bin/python scripts/report_pr.py  # this report
```
