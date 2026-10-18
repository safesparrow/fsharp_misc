#!/usr/bin/env python3
"""Whole-build benchmark and output check for the F# compiler server.

Builds FSharp.Compiler.Service.slnx (repos/baseline) with each config, with the compiler, FSharp.Build and targets taken
from a layout (FSharpTargetsPath / FSharpPropsShim / FSharpTargetsShim / FSharpOverridesTargetsShim):
  baseline   layouts/baseline (stock main), one fsc process per project
  flags      the same, DOTNET_TieredPGO=0 DOTNET_TC_CallCountingDelayMs=0 on fsc processes only (FscToolExe wrapper)
  server     layouts/server, FSharpUseSharedCompilation=true: compilations run on fsc.dll workers

Per config: stop compiler server workers and MSBuild nodes, then one warm-up build and N measured builds, all
`dotnet build -c Release --no-restore --no-incremental -nodeReuse:false`. Workers survive between the builds of the
server config (that is the point of the server); they are stopped before and after it.

CPU = rusage of the build process tree + CPU used during the build by worker processes that are still alive at the end
(sampled with ps before and after). System-wide busy CPU from hostmon is recorded as a cross-check.

Every build's compilation outputs (-o, --refout, --doc and the .pdb next to each assembly, for the 17 compilations
captured in work/cmds/baseline) are hashed. The report compares every build against the first baseline build.

Usage: serverbench.py [--configs baseline,flags,server] [--runs 2] [--warmup 1] [--out results/server]
"""
import argparse, hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostmon  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "repos" / "baseline"
DOTNET = REPO / ".dotnet" / "dotnet"
WRAPPER = ROOT / "scripts" / "fsc-jitflags-wrapper.sh"
CONFIGS = {
    "baseline": dict(layout="baseline", server=False, flags=False),
    "flags": dict(layout="baseline", server=False, flags=True),
    "server": dict(layout="server", server=True, flags=False),
    # Supplementary: the same as server, with more measured builds, to see where warm workers settle.
    "server_long": dict(layout="server", server=True, flags=False),
}


def base_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DOTNET_", "COMPlus_", "MSBuild", "MSBUILD"))}
    env.update(DOTNET_ROOT=str(DOTNET.parent), PATH=f"{DOTNET.parent}:{env['PATH']}", DOTNET_CLI_TELEMETRY_OPTOUT="1",
               DOTNET_NOLOGO="1", DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1", MSBUILDDISABLENODEREUSE="1")
    return env


def output_files():
    files = []
    for f in sorted((ROOT / "work" / "cmds" / "baseline").glob("*.json")):
        d = json.loads(f.read_text())
        for line in d["rsp"]:
            opt, _, val = line.partition(":")
            if opt in ("-o", "--out", "--refout", "--doc"):
                p = Path(d["cwd"], val.strip('"'))
                files.append(p)
                if opt in ("-o", "--out"):
                    files.append(p.with_suffix(".pdb"))
    return files


def hash_outputs(files):
    out = {}
    for p in files:
        rel = str(p.relative_to(REPO))
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    return out


def workers():
    """pid -> cpu seconds, for live compiler server workers."""
    r = subprocess.run(["ps", "-A", "-o", "pid=,time=,command="], capture_output=True, text=True).stdout
    res = {}
    for line in r.splitlines():
        if "--compiler-server-worker" not in line:
            continue
        pid, t, _ = line.strip().split(None, 2)
        parts = [float(x) for x in t.split(":")]
        secs = 0.0
        for x in parts:
            secs = secs * 60 + x
        res[int(pid)] = secs
    return res


def worker_rss_mb():
    r = subprocess.run(["ps", "-A", "-o", "rss=,command="], capture_output=True, text=True).stdout
    return sum(int(l.split()[0]) for l in r.splitlines() if "--compiler-server-worker" in l) / 1024


def stop_workers():
    fsc = ROOT / "layouts" / "server" / "fsc.dll"
    if fsc.exists():
        subprocess.run([str(DOTNET), str(fsc), "--compiler-server-shutdown"], env=base_env(), capture_output=True)
    subprocess.run(["pkill", "-f", "--compiler-server-worker"], capture_output=True)


def build_cmd(cfg):
    layout = ROOT / "layouts" / cfg["layout"]
    cmd = [str(DOTNET), "build", "FSharp.Compiler.Service.slnx", "-c", "Release", "--no-restore", "--no-incremental",
           "-nodeReuse:false",
           f"-p:FSharpTargetsPath={layout}/Microsoft.FSharp.Targets",
           f"-p:FSharpPropsShim={layout}/Microsoft.FSharp.NetSdk.props",
           f"-p:FSharpTargetsShim={layout}/Microsoft.FSharp.NetSdk.targets",
           f"-p:FSharpOverridesTargetsShim={layout}/Microsoft.FSharp.Overrides.NetSdk.targets",
           f"-p:FSharpUseSharedCompilation={'true' if cfg['server'] else 'false'}"]
    env = base_env()
    if cfg["flags"]:
        env["FSC_JITFLAGS_DOTNET"] = str(DOTNET)
        cmd += [f"-p:FscToolPath={WRAPPER.parent}", f"-p:FscToolExe={WRAPPER.name}"]
    return cmd, env


def diagnostics(log: Path):
    """Distinct compiler diagnostics lines (warnings/errors) of a build log, without the MSBuild node prefix."""
    lines = set()
    for l in log.read_text(errors="replace").splitlines():
        if re.search(r"\b(warning|error) FS\d+", l):
            lines.add(re.sub(r"^\s*(\d+>)?", "", l).strip())
    return sorted(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="baseline,flags,server")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--out", default="results/server")
    a = ap.parse_args()

    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    logs = ROOT / "work" / "serverbench_logs"
    logs.mkdir(parents=True, exist_ok=True)
    files = output_files()
    meta = dict(argv=sys.argv, start=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                commit=subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip(),
                server_commit=subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT / "repos" / "server", capture_output=True,
                                             text=True).stdout.strip(),
                ncpu=os.cpu_count(), outputs=len(files))
    (out / f"meta-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(meta, indent=1))
    resf = (out / "runs.jsonl").open("a")

    for name in a.configs.split(","):
        cfg = CONFIGS[name]
        stop_workers()
        cmd, env = build_cmd(cfg)
        for it in range(a.warmup + a.runs):
            warm = it < a.warmup
            subprocess.run([str(DOTNET), "build-server", "shutdown"], cwd=REPO, env=base_env(), capture_output=True)
            pre, waited = hostmon.wait_quiet(1.0, 1.0, 60.0)
            tag = "warmup" if warm else f"run{it - a.warmup + 1}"
            logp = logs / f"{name}_{tag}.log"
            w0 = workers()
            with logp.open("w") as lf, hostmon.RunMonitor() as mon:
                t0 = time.perf_counter()
                p = subprocess.Popen(cmd + [f"-bl:{logs / f'{name}_{tag}.binlog'}"], cwd=REPO, env=env, stdout=lf,
                                     stderr=subprocess.STDOUT)
                _, status, ru = os.wait4(p.pid, 0)
                wall = time.perf_counter() - t0
            w1 = workers()
            rc = os.waitstatus_to_exitcode(status)
            tree_cpu = ru.ru_utime + ru.ru_stime
            worker_cpu = sum(c - w0.get(pid, 0.0) for pid, c in w1.items())
            cpu = tree_cpu + worker_cpu
            host = mon.finish(cpu)
            host.update(pre_busy_cores=pre, quiet_wait_s=waited)
            row = dict(config=name, run=tag, warm=warm, ts=time.time(), rc=rc, wall_s=wall, cpu_s=cpu, tree_cpu_s=tree_cpu,
                       worker_cpu_s=worker_cpu, workers_alive=len(w1), worker_rss_mb=worker_rss_mb(),
                       new_workers=len(set(w1) - set(w0)), host=host, outputs=hash_outputs(files),
                       diagnostics=diagnostics(logp))
            print(f"{name:8s} {tag:6s} rc={rc} wall={wall:7.2f}s cpu={cpu:7.1f}s (workers {worker_cpu:6.1f}s, "
                  f"{len(w1)} alive, {row['worker_rss_mb']:.0f} MB) sysbusy={host['sys_busy_cpu_s']:7.1f}s pre={pre:.2f}c",
                  flush=True)
            resf.write(json.dumps(row) + "\n")
            resf.flush()
            if rc != 0:
                print(logp.read_text()[-3000:])
                sys.exit(f"build failed: {logp}")
        stop_workers()


if __name__ == "__main__":
    main()
