using System.Text.Json;
using Microsoft.Diagnostics.Tracing;
using Microsoft.Diagnostics.Tracing.Analysis;
using Microsoft.Diagnostics.Tracing.Parsers.Clr;

// Usage: TraceSummary <trace.nettrace>  -> JSON on stdout
var path = args[0];
using var source = new EventPipeEventSource(path);
source.NeedLoadedDotNetRuntimes();

// Raw tier counting straight from MethodLoadVerbose, independent of the analysis layer.
var tierCount = new Dictionary<string, int>();
var tierIl = new Dictionary<string, long>();
source.Clr.MethodLoadVerbose += (MethodLoadUnloadVerboseTraceData d) =>
{
    var t = d.OptimizationTier.ToString();
    tierCount[t] = tierCount.GetValueOrDefault(t) + 1;
};
source.Process();

var proc = source.Processes().FirstOrDefault(p => p.LoadedDotNetRuntime() != null);
var rt = proc?.LoadedDotNetRuntime();
var result = new Dictionary<string, object>();
if (rt != null)
{
    var jitByTier = rt.JIT.Methods
        .GroupBy(m => m.OptimizationTier.ToString())
        .ToDictionary(g => g.Key, g => new
        {
            methods = g.Count(),
            compile_ms = Math.Round(g.Sum(m => m.CompileCpuTimeMSec), 1),
            il_kb = Math.Round(g.Sum(m => (double)m.ILSize) / 1024, 1),
            native_kb = Math.Round(g.Sum(m => (double)m.NativeSize) / 1024, 1),
        });
    result["jit_by_tier"] = jitByTier;
    result["jit_methods_total"] = rt.JIT.Methods.Count;
    result["jit_compile_ms_total"] = Math.Round(rt.JIT.Methods.Sum(m => m.CompileCpuTimeMSec), 1);
    var gcs = rt.GC.GCs.Where(g => g.PauseDurationMSec > 0).ToList();
    result["gc_count"] = gcs.Count;
    result["gc_pause_ms_total"] = Math.Round(gcs.Sum(g => g.PauseDurationMSec), 1);
    result["gc_pause_ms_max"] = gcs.Count == 0 ? 0 : Math.Round(gcs.Max(g => g.PauseDurationMSec), 1);
    result["gc_by_gen"] = gcs.GroupBy(g => $"gen{g.Generation}").ToDictionary(g => g.Key, g => new
    {
        count = g.Count(),
        pause_ms = Math.Round(g.Sum(x => x.PauseDurationMSec), 1),
    });
}
result["method_load_verbose_by_tier"] = tierCount;
Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
