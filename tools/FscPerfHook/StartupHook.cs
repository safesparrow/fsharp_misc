using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Runtime;
using System.Text;
using System.Threading;

// Must be a top-level, non-namespaced type named StartupHook with a static Initialize().
internal class StartupHook
{
    public static void Initialize()
    {
        var path = Environment.GetEnvironmentVariable("FSCPERF_STATS_FILE");
        if (string.IsNullOrEmpty(path)) return;
        AppDomain.CurrentDomain.ProcessExit += (_, _) => { try { Write(path); } catch { } };
    }

    static void Write(string path)
    {
        var inv = CultureInfo.InvariantCulture;
        var sb = new StringBuilder();
        void Kv(string k, object v) => sb.Append('"').Append(k).Append("\":").Append(Convert.ToString(v, inv)).Append(',');
        sb.Append('{');
        Kv("gc_pause_ms", GC.GetTotalPauseDuration().TotalMilliseconds);
        Kv("gc_count_gen0", GC.CollectionCount(0));
        Kv("gc_count_gen1", GC.CollectionCount(1));
        Kv("gc_count_gen2", GC.CollectionCount(2));
        Kv("gc_alloc_mb", GC.GetTotalAllocatedBytes(false) / 1048576.0);
        var mi = GC.GetGCMemoryInfo(GCKind.Any);
        Kv("gc_heap_peak_committed_mb", mi.TotalCommittedBytes / 1048576.0);
        Kv("gc_server", GCSettings.IsServerGC ? 1 : 0);
        Kv("jit_methods", System.Runtime.JitInfo.GetCompiledMethodCount(false));
        Kv("jit_il_kb", System.Runtime.JitInfo.GetCompiledILBytes(false) / 1024.0);
        Kv("jit_time_ms", System.Runtime.JitInfo.GetCompilationTime(false).TotalMilliseconds);
        Kv("lock_contentions", Monitor.LockContentionCount);
        Kv("threadpool_threads", ThreadPool.ThreadCount);
        Kv("threadpool_items", ThreadPool.CompletedWorkItemCount);
        sb.Length--; sb.Append('}');
        File.WriteAllText(path, sb.ToString());
    }
}
