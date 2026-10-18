#!/usr/bin/env python3
"""Effect of parallel (graph-based) type checking, on warm compiler server workers.

  par        the captured compilation as is: fsc defaults = graph-based type checking, parallel optimization, parallel IL
             generation, parallel reference resolution (the captured response files carry no --parallelcompilation switch)
  seq_tc     the same, but type checking sequential: --parallelcompilation- then --test:ParallelOptimization,
             --test:ParallelIlxGen and --parallelreferenceresolution turn every other parallel phase back on
             (options are applied in order)

Two workers (one per side) with the branch's default worker settings. 1 warm-up round, then N rounds over every captured
compilation; within a round each compilation runs on both sides back to back (order alternating per round), one
compilation at a time. Writes results/server_partc/runs.jsonl.

Usage: partc_bench.py [--rounds 5] [--projects all|id,id]
"""
import argparse, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostmon  # noqa: E402
from fscbench import CMDS, ROOT, Worker, hashes, parse_times, prepare  # noqa: E402

SEQ_TC_OPTIONS = ["--parallelcompilation-", "--test:ParallelOptimization", "--test:ParallelIlxGen", "--parallelreferenceresolution"]
WORKER_ENV = {"DOTNET_TC_CallCountingDelayMs": "0", "DOTNET_gcConcurrent": "0"}  # same as the client's defaults


def seq_tc_rsp(rsp: Path):
    lines = rsp.read_text(encoding="utf-8-sig").splitlines()
    idx = next(i for i, l in enumerate(lines) if not l.startswith("-"))  # options before the first source file
    out = rsp.with_name("args_seqtc.rsp")
    out.write_text("\n".join(lines[:idx] + SEQ_TC_OPTIONS + lines[idx:]) + "\n", encoding="utf-8-sig")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--projects", default="all")
    ap.add_argument("--out", default="results/server_partc")
    a = ap.parse_args()
    projs = sorted(p.stem for p in CMDS.glob("*.json")) if a.projects == "all" else a.projects.split(",")
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    prepared = {}
    for pid in projs:
        cwd, rsp, outs = prepare(pid, "partc", ROOT / "work" / "partc_out")
        prepared[pid] = dict(cwd=cwd, outs=outs, par=rsp, seq_tc=seq_tc_rsp(rsp))
    workers = {"par": Worker("server", WORKER_ENV, "pcpar"), "seq_tc": Worker("server", WORKER_ENV, "pcseq")}
    resf = (out / "runs.jsonl").open("a")
    try:
        for rnd in range(a.warmup + a.rounds):
            warm = rnd < a.warmup
            sides = ["par", "seq_tc"] if rnd % 2 == 0 else ["seq_tc", "par"]
            for pid in projs:
                for side in sides:
                    p = prepared[pid]
                    pre, _ = hostmon.wait_quiet(1.0, 0.5, 30.0)
                    wall, resp = workers[side].compile(p["cwd"], str(p[side]))
                    lines = [t for _, t in resp["lines"]]
                    phases, last = parse_times(lines)
                    stats = resp.get("stats", {})
                    row = dict(side=side, project=pid, round=rnd, warm=warm, ts=time.time(), pre_busy=pre, rc=resp["exitCode"],
                               wall_s=wall, cpu_s=stats.get("cpuMs", 0) / 1000, stats=stats, phases=phases, times_last=last,
                               outputs=hashes(p["outs"]), worker_rss_mb=workers[side].rss_mb())
                    resf.write(json.dumps(row) + "\n")
                    resf.flush()
                    print(f"r{rnd}{'w' if warm else ' '} {side:6s} {pid:52s} rc={row['rc']} wall={wall:6.2f}s cpu={row['cpu_s']:6.2f}s "
                          f"tc={phases.get('Typecheck', 0):6.2f}s", flush=True)
                    if row["rc"] != 0:
                        print("\n".join(lines[-20:]))
                        sys.exit(f"compilation failed: {side} {pid}")
    finally:
        for w in workers.values():
            w.stop()


if __name__ == "__main__":
    main()
