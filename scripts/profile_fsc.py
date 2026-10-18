#!/usr/bin/env python3
"""EventPipe profiles of one compilation: a stock fsc process (optionally with env) vs a warm compiler server worker.

  process: dotnet-trace collect ... -- dotnet layouts/<layout>/fsc.dll @rsp
  worker:  start a worker, compile the project `--warm` times, then attach dotnet-trace to the worker (-p) for exactly
           one more compilation of it

Writes <out>/<name>.nettrace, the TraceSummary JSON (JIT per tier, GC), and a speedscope JSON for CPU samples.

Usage: profile_fsc.py --project FCS --name flags --mode process --env DOTNET_TieredPGO=0,DOTNET_TC_CallCountingDelayMs=0
       profile_fsc.py --project FCS --name srv --mode worker --warm 2 [--env K=V,...]
"""
import argparse, json, os, signal, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fscbench import PROJECTS, Worker, prepare, clean_env, DOTNET, ROOT  # noqa: E402

TRACE = Path.home() / ".dotnet" / "tools" / "dotnet-trace"
SUMMARY = ROOT / "layouts" / "tracesummary" / "TraceSummary.dll"
COMMON = ["--providers", "Microsoft-DotNETCore-SampleProfiler:0:5,Microsoft-Windows-DotNETRuntime:0x1000000019:5"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--mode", choices=["process", "worker"], required=True)
    ap.add_argument("--layout", default=None)
    ap.add_argument("--env", default="")
    ap.add_argument("--warm", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "work" / "profiles"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pid = PROJECTS[a.project]
    env_over = dict(kv.split("=", 1) for kv in a.env.split(",") if kv)
    cwd, rsp, _ = prepare(pid, "profile_" + a.name, ROOT / "work" / "fscbench_out")
    trace = out / f"{a.name}_{a.project}.nettrace"
    tenv = clean_env()
    if a.mode == "process":
        layout = a.layout or "baseline"
        env = clean_env() | env_over
        t0 = time.time()
        r = subprocess.run([str(TRACE), "collect", *COMMON, "-o", str(trace), "--show-child-io", "false", "--",
                            str(DOTNET), str(ROOT / "layouts" / layout / "fsc.dll"), f"@{rsp}"],
                           cwd=cwd, env=env, capture_output=True, text=True)
        print(f"traced process compilation in {time.time() - t0:.1f}s rc={r.returncode}")
    else:
        layout = a.layout or "server"
        w = Worker(layout, env_over, f"p{os.getpid()}")
        try:
            for i in range(a.warm):
                wall, resp = w.compile(cwd, str(rsp))
                print(f"warm {i}: {wall:.2f}s")
            t = subprocess.Popen([str(TRACE), "collect", *COMMON, "-p", str(w.proc.pid), "-o", str(trace)],
                                 env=tenv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            time.sleep(2.0)  # let the session start
            wall, resp = w.compile(cwd, str(rsp))
            print(f"traced worker compilation: {wall:.2f}s stats={resp.get('stats')}")
            time.sleep(0.3)
            t.send_signal(signal.SIGINT)
            t.wait(timeout=300)
        finally:
            w.stop()
    summ = subprocess.run([str(DOTNET), str(SUMMARY), str(trace)], capture_output=True, text=True, env=tenv)
    (out / f"{a.name}_{a.project}.summary.json").write_text(summ.stdout)
    print(summ.stdout[:1500])
    subprocess.run([str(TRACE), "convert", str(trace), "--format", "speedscope", "-o", str(out / f"{a.name}_{a.project}")],
                   env=tenv, capture_output=True)


if __name__ == "__main__":
    main()
