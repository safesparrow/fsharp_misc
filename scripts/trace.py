#!/usr/bin/env python3
"""EventPipe-traced fsc runs for JIT-tier and GC-pause detail.

These runs are SEPARATE from the timing runs in bench.py: tracing perturbs timings.
Each run writes a .nettrace, which tools/TraceSummary turns into JSON (methods / compile ms per
optimization tier, GC count and pauses). The trace file is deleted afterwards and only the summary is kept.

Usage: trace.py --cmds work/cmds/baseline --configs configs/phase1.json --out results/phase1_traces
                [--runs 3] [--only REGEX] [--variants par]
"""
import argparse, json, os, random, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench import ROOT, rewrite_rsp, clean_env, run_once  # noqa: E402

# GC (0x1) | Loader (0x8) | JIT (0x10) | Compilation/tiering (0x1000000000), verbose.
EVENTPIPE_CONFIG = "Microsoft-Windows-DotNETRuntime:0x1000000019:5"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmds", required=True)
    ap.add_argument("--configs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", default=None)
    ap.add_argument("--variants", default="par")
    ap.add_argument("--configs-only", default=None)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    cfgs = json.loads(Path(a.configs).read_text())
    if a.configs_only:
        cfgs = [c for c in cfgs if c["id"] in a.configs_only.split(",")]
    cells = [(c, v) for c in cfgs for v in a.variants.split(",")]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    resf = out / "traces.jsonl"
    done = set()
    if resf.exists():
        for l in resf.read_text().splitlines():
            r = json.loads(l); done.add((r["project"], r["config"], r["variant"], r["iter"]))
    summarizer = ROOT / "layouts" / "tracesummary" / "TraceSummary.dll"
    rng = random.Random(a.seed)
    scratch = ROOT / "work" / "trace_out"
    with resf.open("a") as rf:
        for cf in sorted(Path(a.cmds).glob("*.json")):
            cmd = json.loads(cf.read_text())
            pid = cmd["id"]
            if a.only and not re.search(a.only, pid):
                continue
            for it in range(a.runs):
                order = cells[:]; rng.shuffle(order)
                for c, v in order:
                    if (pid, c["id"], v, it) in done:
                        continue
                    od = scratch / v / c["id"] / pid; od.mkdir(parents=True, exist_ok=True)
                    rsp = od / "args.rsp"
                    rsp.write_text("\n".join(rewrite_rsp(cmd["rsp"], od, v)) + "\n")
                    tracef = od / "trace.nettrace"; tracef.unlink(missing_ok=True)
                    dotnet = ROOT / c["dotnet"]
                    env = clean_env(c.get("env", {}))
                    env.update(DOTNET_ROOT=str(dotnet.parent), DOTNET_EnableEventPipe="1",
                               DOTNET_EventPipeOutputPath=str(tracef), DOTNET_EventPipeConfig=EVENTPIPE_CONFIG)
                    with (od / "last.log").open("w") as log:
                        r = run_once(str(dotnet), str(ROOT / c["layout"] / "fsc.dll"), rsp, cmd["cwd"], env, log)
                    if r["rc"] != 0:
                        sys.exit(f"FAIL {pid} {c['id']} {v}: see {od / 'last.log'}")
                    s = subprocess.run([str(dotnet), str(summarizer), str(tracef)], capture_output=True, text=True,
                                       env={**os.environ, "DOTNET_ROOT": str(dotnet.parent)})
                    if s.returncode != 0:
                        sys.exit(f"TraceSummary failed: {s.stderr[-2000:]}")
                    summ = json.loads(s.stdout)
                    size = tracef.stat().st_size
                    tracef.unlink()
                    rec = dict(project=pid, config=c["id"], variant=v, iter=it, ts=time.time(),
                               trace_mb=size / 2**20, wall_s_traced=r["wall_s"], **summ)
                    rf.write(json.dumps(rec) + "\n"); rf.flush()
                    tiers = {k: x["methods"] for k, x in summ.get("jit_by_tier", {}).items()}
                    print(f"{pid:50s} {c['id']:10s} {v} #{it} wall(traced)={r['wall_s']:.2f}s "
                          f"gc_pause={summ.get('gc_pause_ms_total')}ms tiers={tiers}", flush=True)


if __name__ == "__main__":
    main()
