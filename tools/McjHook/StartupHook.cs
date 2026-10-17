using System;
using System.IO;
using System.Runtime;
using System.Security.Cryptography;
using System.Text;

// What fsc itself would do: ProfileOptimization.SetProfileRoot + StartProfile at the start of main.
// The first run records the methods JIT-compiled at startup; later runs JIT them on background threads ahead of use.
internal class StartupHook
{
    public static void Initialize()
    {
        var dir = Environment.GetEnvironmentVariable("FSCPERF_MCJ_DIR");
        if (string.IsNullOrEmpty(dir)) return;
        Directory.CreateDirectory(dir);
        // Key on the output assembly: MSBuild's Fsc task passes a temp-named @rsp file, so the raw args change every build.
        var key = Environment.CurrentDirectory + "\n" + (OutputOf(Environment.GetCommandLineArgs()) ?? string.Join("\n", Environment.GetCommandLineArgs()));
        var name = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(key)))[..16] + ".mcj";
        ProfileOptimization.SetProfileRoot(dir);
        ProfileOptimization.StartProfile(name);
    }

    static string? OutputOf(string[] args)
    {
        foreach (var a in args)
        {
            if (!a.StartsWith('@') || !File.Exists(a[1..])) continue;
            foreach (var l in File.ReadLines(a[1..]))
            {
                var t = l.Trim().TrimStart('\uFEFF');
                if (t.StartsWith("-o:") || t.StartsWith("--out:")) return t;
            }
        }
        return null;
    }
}
