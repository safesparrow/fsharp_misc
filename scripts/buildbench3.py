#!/usr/bin/env python3
"""Phase 3 whole-build benchmark: `dotnet build` of a repo, with each benchmark config applied to fsc ONLY.

Per config (configs/phase3.json): the build uses that config's fsc layout (-p:DotnetFscCompilerPath=<layout>/fsc.dll)
and runs fsc through scripts/fsc-env-wrapper.sh, which exports the config's env to the fsc process. MSBuild itself
runs with default runtime settings in every config. Otherwise the same as buildbench.py: build servers shut down before
each run, configs interleaved in shuffled order every round, host monitor + quiet-wait. CPU = system-wide busy CPU
during the build minus the background measured right before it (rusage of the build tree misses fsc processes).

Usage: buildbench3.py --repo repos/fantomas --sln fantomas.slnx --configs configs/phase3.json --out results/phase3_build
"""
import argparse, json, os, random, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostmon  # noqa: E402
from buildbench import base_env  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "scripts" / "fsc-env-wrapper.sh"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--sln", required=True)
    ap.add_argument("--configs", required=True)
    ap.add_argument("--configs-only", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--seed", type=int, default=501)
    ap.add_argument("--wrap-log", default=None, help="append every fsc invocation to this file (verification)")
    a = ap.parse_args()

    repo = (ROOT / a.repo).resolve()
    cfgs = json.loads((ROOT / a.configs).read_text())
    if a.configs_only:
        keep = a.configs_only.split(","); cfgs = [c for c in cfgs if c["id"] in keep]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    resf = out / "runs.jsonl"
    done = set()
    if resf.exists():
        for l in resf.read_text().splitlines():
            try:
                r = json.loads(l); done.add((r["config"], r["iter"]))
            except json.JSONDecodeError:
                pass
    meta = dict(argv=sys.argv, start=time.strftime("%Y-%m-%dT%H:%M:%S%z"), repo=str(repo), runs=a.runs, warmup=a.warmup,
                commit=subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip(),
                configs=cfgs)
    (out / f"meta-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(meta, indent=1))
    rng = random.Random(a.seed)
    logs = ROOT / "work" / "buildbench3_logs"; logs.mkdir(parents=True, exist_ok=True)
    with resf.open("a") as rf:
        for it in range(a.warmup + a.runs):
            order = cfgs[:]; rng.shuffle(order)
            for c in order:
                warm = it < a.warmup
                if not warm and (c["id"], it - a.warmup) in done:
                    continue
                dotnet = (ROOT / c["dotnet"]).resolve()
                env = base_env(dotnet.parent)
                for k, v in c.get("env", {}).items():
                    v = v.replace("${ROOT}", str(ROOT))
                    if k == "FSCPERF_MCJ_DIR":
                        v = v.rstrip("/") + "_build"
                    env[f"FSCWRAP_{k}"] = v
                env["FSC_WRAP_DOTNET"] = str(dotnet)
                if a.wrap_log:
                    env["FSC_WRAP_LOG"] = str(Path(a.wrap_log).resolve())
                fsc = (ROOT / c["layout"] / "fsc.dll").resolve()
                cmd = [str(dotnet), "build", a.sln, "-c", "Release", "--no-restore", "--no-incremental", "-nodeReuse:false",
                       f"-p:DotnetFscCompilerPath={fsc}", f"-p:FscToolPath={WRAPPER.parent}", f"-p:FscToolExe={WRAPPER.name}"]
                subprocess.run([str(dotnet), "build-server", "shutdown"], cwd=repo, env=base_env(dotnet.parent), capture_output=True)
                pre, waited = hostmon.wait_quiet(1.0, 1.0, 60.0)
                logp = logs / f"{c['id']}_{it}.log"
                with logp.open("w") as lf, hostmon.RunMonitor() as mon:
                    t0 = time.perf_counter()
                    p = subprocess.Popen(cmd, cwd=repo, env=env, stdout=lf, stderr=subprocess.STDOUT)
                    _, status, ru = os.wait4(p.pid, 0)
                    wall = time.perf_counter() - t0
                rc = os.waitstatus_to_exitcode(status)
                host = mon.finish(ru.ru_utime + ru.ru_stime)
                host.update(pre_busy_cores=pre, quiet_wait_s=waited)
                cpu = host["sys_busy_cpu_s"] - pre * host["mon_wall_s"]
                tag = "warm" if warm else f"{it - a.warmup + 1}/{a.runs}"
                print(f"build {c['id']:14s} {tag:5s} rc={rc} wall={wall:7.2f}s cpu~{cpu:7.1f}s pre={pre:.2f}c", flush=True)
                if rc != 0:
                    print(logp.read_text()[-3000:]); sys.exit(f"build failed: {logp}")
                if warm:
                    continue
                rf.write(json.dumps(dict(config=c["id"], iter=it - a.warmup, ts=time.time(), wall_s=wall, cpu_s=cpu,
                                         maxrss_mb=ru.ru_maxrss / 2**20, host=host)) + "\n"); rf.flush()


if __name__ == "__main__":
    main()
