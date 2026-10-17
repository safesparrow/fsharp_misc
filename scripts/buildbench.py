#!/usr/bin/env python3
"""Build-level benchmark: time a whole `dotnet build` under different SDKs / env settings.

Mirrors how people try these flags in practice (`X=Y dotnet build`), and separates where any gain comes from:
  none     no extra env
  env_all  DOTNET_TieredPGO=0 DOTNET_TC_CallCountingDelayMs=0 set on `dotnet build`: affects the CLI/MSBuild
           process, MSBuild worker nodes, and every fsc process
  env_fsc  the same two vars applied to fsc processes ONLY (FscToolExe = a wrapper that sets them),
           with MSBuild running with default settings

Each run is `dotnet build <sln> -c Release --no-restore --no-incremental -nodeReuse:false`, with
`dotnet build-server shutdown` beforehand, so no MSBuild node or compiler server survives from a run with a
different config. Configs are interleaved in shuffled order every round. The host monitor and quiet-wait are the
same as bench.py.

CPU time = rusage of the build process tree (children are reaped by .NET's Process class, so fsc and MSBuild nodes
are included). System-wide busy CPU from the host monitor is recorded as a cross-check.

Usage: buildbench.py --repo repos/fantomas --sln fantomas.slnx --sdks sdk10=work/sdks/dotnet10,sdk11=repos/baseline/.dotnet \
                     --out results/buildbench --runs 5 --warmup 1
"""
import argparse, json, os, random, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostmon  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FLAGS = {"DOTNET_TieredPGO": "0", "DOTNET_TC_CallCountingDelayMs": "0"}
WRAPPER = ROOT / "scripts" / "fsc-jitflags-wrapper.sh"


def base_env(dotnet_root: Path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DOTNET_", "COMPlus_", "MSBuild", "MSBUILD"))}
    env.update(DOTNET_ROOT=str(dotnet_root), PATH=f"{dotnet_root}:{env['PATH']}", DOTNET_CLI_TELEMETRY_OPTOUT="1",
               DOTNET_NOLOGO="1", DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1", MSBUILDDISABLENODEREUSE="1")
    return env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sln", required=True)
    ap.add_argument("--sdks", required=True, help="name=dotnet_root,...")
    ap.add_argument("--modes", default="none,env_all,env_fsc")
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--seed", type=int, default=401)
    ap.add_argument("--extra", default="", help="extra msbuild args")
    a = ap.parse_args()

    repo = (ROOT / a.repo).resolve()
    sdks = {k: (ROOT / v).resolve() for k, v in (x.split("=", 1) for x in a.sdks.split(","))}
    cells = [(s, m) for s in sdks for m in a.modes.split(",")]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    resf = out / "runs.jsonl"
    done = set()
    if resf.exists():
        for l in resf.read_text().splitlines():
            try:
                r = json.loads(l); done.add((r["sdk"], r["mode"], r["iter"]))
            except json.JSONDecodeError:
                pass
    meta = dict(argv=sys.argv, start=time.strftime("%Y-%m-%dT%H:%M:%S%z"), repo=str(repo),
                commit=subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip(),
                sdks={k: str(v) for k, v in sdks.items()}, flags=FLAGS)
    (out / f"meta-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(meta, indent=1))
    rng = random.Random(a.seed)
    logs = ROOT / "work" / "buildbench_logs"; logs.mkdir(parents=True, exist_ok=True)
    with resf.open("a") as rf:
        for it in range(a.warmup + a.runs):
            order = cells[:]; rng.shuffle(order)
            for sdk, mode in order:
                warm = it < a.warmup
                if not warm and (sdk, mode, it - a.warmup) in done:
                    continue
                root = sdks[sdk]
                env = base_env(root)
                cmd = [str(root / "dotnet"), "build", a.sln, "-c", "Release", "--no-restore", "--no-incremental",
                       "-nodeReuse:false"] + (a.extra.split() if a.extra else [])
                if mode == "env_all":
                    env.update(FLAGS)
                elif mode == "env_fsc":
                    env["FSC_JITFLAGS_DOTNET"] = str(root / "dotnet")
                    cmd += [f"-p:FscToolPath={WRAPPER.parent}", f"-p:FscToolExe={WRAPPER.name}"]
                subprocess.run([str(root / "dotnet"), "build-server", "shutdown"], cwd=repo, env=base_env(root),
                               capture_output=True)
                pre, waited = hostmon.wait_quiet(1.0, 1.0, 60.0)
                logp = logs / f"{sdk}_{mode}_{it}.log"
                with logp.open("w") as lf, hostmon.RunMonitor() as mon:
                    t0 = time.perf_counter()
                    p = subprocess.Popen(cmd, cwd=repo, env=env, stdout=lf, stderr=subprocess.STDOUT)
                    _, status, ru = os.wait4(p.pid, 0)
                    wall = time.perf_counter() - t0
                rc = os.waitstatus_to_exitcode(status)
                cpu = ru.ru_utime + ru.ru_stime
                host = mon.finish(cpu)
                host.update(pre_busy_cores=pre, quiet_wait_s=waited)
                tag = "warm" if warm else f"{it - a.warmup + 1}/{a.runs}"
                print(f"{sdk:6s} {mode:8s} {tag:5s} rc={rc} wall={wall:7.2f}s cpu={cpu:7.2f}s "
                      f"sysbusy={host['sys_busy_cpu_s']:7.1f}s pre={pre:.2f}c", flush=True)
                if rc != 0:
                    print(logp.read_text()[-3000:]); sys.exit(f"build failed: {logp}")
                if warm:
                    continue
                rf.write(json.dumps(dict(sdk=sdk, mode=mode, iter=it - a.warmup, ts=time.time(), wall_s=wall, cpu_s=cpu,
                                         maxrss_mb=ru.ru_maxrss / 2**20, host=host)) + "\n"); rf.flush()


if __name__ == "__main__":
    main()
