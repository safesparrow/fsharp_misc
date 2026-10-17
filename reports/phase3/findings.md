## Summary (preliminary: quick pass)

These numbers come from the quick pass: 2 runs per config for FCS and Fantomas, 3 for FSharp.Core, 10 for FSharp.Build. The full pass (5 / 8 / 20 / 5 runs) replaces them when it finishes. Every change is relative to **base**: both JIT flags set and parallel compilation on.

### What helped

- **DATAS off** (`DOTNET_GCDynamicAdaptationMode=0`) is the only setting that is faster on every target, with lower CPU everywhere:
  - wall: FCS −4.5%, FSharp.Core −2.5%, FSharp.Build −11.3%, Fantomas build −2.6%
  - CPU: −7% to −18%
  - GC pause: −13% (FCS) to −83% (FSharp.Build)
  - Cost: peak memory goes up 37% (FCS) to 135% (FSharp.Core), because the GC no longer shrinks the heap.
- **Non-concurrent GC** (`DOTNET_gcConcurrent=0`): small wins. Wall changes by 0% to −3%, and CPU drops 4% to 9%. GC pause rises about 10%, but it costs less than the background GC it replaces.
- **4 GC heaps** (`DOTNET_GCHeapCount=4`, which also turns DATAS off) helps small compilations (FSharp.Build −11.8%) and saves CPU everywhere (−12% to −18%). It slows FCS by 7.8%, so it isn't a general win.
- **MultiCoreJit** makes no difference to wall time. It does cut Fantomas build CPU by 13%.

### What did not help

- **10 ms call-counting delay** instead of 0: slower on every target (+8% to +49% wall). It lowers CPU only for the small compilation (−25%). A delay of 0 is the right choice.
- **256 MB gen0 budget**: slower (+5% to +40% wall); GC pause goes up 30% to 233%.
- **Workstation GC**: much slower on large compilations (FCS +113% wall).
- **Composite ReadyToRun** of the compiler's own assemblies: slower (+3% to +14% wall). The image does load: without tiering, 39% fewer methods need JIT compilation at all. But more of the precompiled code becomes hot and is recompiled at tier 1, so total JIT work goes up.
- **Thread-pool minimum of 16 threads**: no effect.

### Next

- The full pass adds `datas_nonconc` (DATAS off plus non-concurrent GC). On FSharp.Build it is measured after the other configs instead of interleaved with them.
- These results are from one 11-core Apple M3 Pro. GC settings depend on core count and memory, so they need checking on the Linux x64 machines before recommending any.
