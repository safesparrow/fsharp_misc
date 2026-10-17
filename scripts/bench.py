#!/usr/bin/env python3
"""Benchmark fsc against captured per-project command lines.

Each (project, config) cell is run `--warmup` + `--runs` times. Configs are interleaved
round-robin in a shuffled (seeded) order per round, so slow drift (thermals, background
load) hits every config equally. Results are appended as JSON lines and the run can be resumed.

Usage:
  bench.py --cmds work/cmds/baseline --configs configs/phase1.json --out results/phase1 \
           [--runs 10] [--warmup 1] [--only REGEX] [--variants par,seq]

A "cell" is (config, variant). variant par = command line as captured (--parallelcompilation+),
seq = --parallelcompilation- (graph checking, parallel optimization and IL gen off).
"""
import argparse, json, os, random, re, shutil, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostmon  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Response-file options that name compiler OUTPUTS. They are redirected to a scratch dir
# so benchmark runs never touch the obj/ files other projects reference.
OUTPUT_OPTS = ("-o:", "--out:", "--refout:", "--doc:", "--pdb:", "--sig:")


def rewrite_rsp(lines, outdir: Path, variant: str):
    out = []
    for l in lines:
        s = l.strip()
        hit = next((p for p in OUTPUT_OPTS if s.startswith(p)), None)
        if hit:
            orig = s[len(hit):].strip('"')
            out.append(f"{hit}{outdir / Path(orig).name}")
            continue
        if variant == "seq":
            if s.startswith("--parallelcompilation"):
                continue  # replaced below
            # legacy per-feature switches would re-enable parts of parallel compilation
            if s in ("--test:GraphBasedChecking", "--test:ParallelOptimization", "--test:ParallelIlxGen",
                     "--parallelreferenceresolution"):
                continue
        out.append(l)
    if variant == "seq":
        # options must precede sources; insert before the first non-option line
        idx = next((i for i, l in enumerate(out) if not l.startswith("-")), len(out))
        out.insert(idx, "--parallelcompilation-")
    elif variant == "par":
        if not any(l.strip().startswith("--parallelcompilation") for l in out):
            idx = next((i for i, l in enumerate(out) if not l.startswith("-")), len(out))
            out.insert(idx, "--parallelcompilation+")
    return out


def clean_env(extra):
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("DOTNET_") or k.startswith("COMPlus_") or k.startswith("FSC_CAPTURE"))}
    env.update(extra)
    return env


def parse_times_csv(path: Path):
    """Aggregate fsc --times:<csv> rows: total seconds per activity name, and per top-level phase.

    Top-level phases are the activities whose parent is the root "FSC compilation" activity
    (the ReportTime phases in fsc.fs: Parse inputs, Typecheck, Optimizations, TAST -> IL, ...)."""
    import csv
    if not path.exists():
        return None, None
    rows = list(csv.reader(path.open(newline="")))
    if len(rows) < 2:
        return None, None
    hdr = rows[0]
    iN, iD, iId, iP = hdr.index("Name"), hdr.index("Duration(s)"), hdr.index("Id"), hdr.index("ParentId")
    # The root "FSC compilation" row is written last and is sometimes truncated (fsc exits before
    # its async CSV writer flushes), so don't rely on it: a top-level phase is any activity whose
    # parent is not itself a row in the file.
    ids = {r[iId] for r in rows[1:] if len(r) > iP and r[iN] != "FSC compilation"}
    root_ids = {r[iP] for r in rows[1:] if len(r) > iP and r[iN] != "FSC compilation" and r[iP] not in ids}
    phases, acts = {}, {}
    for r in rows[1:]:
        if len(r) <= iP or r[iN] == "FSC compilation":
            continue
        try:
            d = float(r[iD])
        except ValueError:
            continue
        name = re.sub(r"^file-\d+_", "", r[iN])  # optimizer emits one activity per file and phase
        acts[name] = acts.get(name, 0.0) + d
        if r[iP] in root_ids:
            phases[r[iN]] = phases.get(r[iN], 0.0) + d
    return phases, acts


def run_once(dotnet, fsc, rsp, cwd, env, log):
    t0 = time.perf_counter_ns()
    p = subprocess.Popen([dotnet, fsc, f"@{rsp}"], cwd=cwd, env=env,
                         stdout=log, stderr=subprocess.STDOUT)
    _, status, ru = os.wait4(p.pid, 0)
    wall = (time.perf_counter_ns() - t0) / 1e9
    p.returncode = os.waitstatus_to_exitcode(status)
    return dict(rc=p.returncode, wall_s=wall, user_s=ru.ru_utime, sys_s=ru.ru_stime,
                cpu_s=ru.ru_utime + ru.ru_stime,
                maxrss_mb=ru.ru_maxrss / (1024 * 1024) if sys.platform == "darwin" else ru.ru_maxrss / 1024)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmds", required=True, help="dir with <project>.json from capture.py")
    ap.add_argument("--configs", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--only", default=None, help="regex on project id")
    ap.add_argument("--variants", default="par", help="comma list of par,seq")
    ap.add_argument("--configs-only", default=None, help="comma list of config ids to run")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-hook", action="store_true", help="don't load the GC/JIT stats startup hook")
    ap.add_argument("--hook", default="layouts/hook/FscPerfHook.dll", help="stats hook dll (relative to the repo root)")
    ap.add_argument("--no-times", action="store_true", help="don't pass --times:<csv> to fsc")
    ap.add_argument("--tag", default="", help="free-form label stored with each run")
    ap.add_argument("--quiet-threshold", type=float, default=1.0,
                    help="before each run, wait until host busy CPU < this many cores (1 s window)")
    ap.add_argument("--quiet-max-wait", type=float, default=60.0)
    ap.add_argument("--flag-foreign-cores", type=float, default=1.0,
                    help="flag a run if foreign (non-fsc) CPU averaged more than this many cores")
    a = ap.parse_args()

    cfgs = json.loads(Path(a.configs).read_text())
    if a.configs_only:
        keep = a.configs_only.split(",")
        cfgs = [c for c in cfgs if c["id"] in keep]
    variants = a.variants.split(",")
    cells = [(c, v) for c in cfgs for v in variants]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    results = out / "runs.jsonl"
    done = set()
    if results.exists():
        for l in results.read_text().splitlines():
            r = json.loads(l)
            done.add((r["project"], r["config"], r["variant"], r["iter"]))

    def sh(*c):
        try: return subprocess.run(c, capture_output=True, text=True).stdout.strip()
        except Exception: return None
    meta = dict(argv=sys.argv, start=time.strftime("%Y-%m-%dT%H:%M:%S%z"), configs=cfgs, variants=variants,
                runs=a.runs, warmup=a.warmup, seed=a.seed,
                host=dict(cpu=sh("sysctl", "-n", "machdep.cpu.brand_string"), ncpu=os.cpu_count(),
                          mem_bytes=sh("sysctl", "-n", "hw.memsize"), os=sh("sw_vers", "-productVersion"),
                          kernel=sh("uname", "-r"), power=sh("pmset", "-g", "batt")))
    for c in cfgs:
        lj = ROOT / c["layout"] / "layout.json"
        if lj.exists():
            meta.setdefault("layouts", {})[c["layout"]] = json.loads(lj.read_text())
    metaf = out / f"meta-{time.strftime('%Y%m%d-%H%M%S')}.json"
    metaf.write_text(json.dumps(meta, indent=1) + "\n")

    scratch = ROOT / "work" / "bench_out"
    cmds = sorted(Path(a.cmds).glob("*.json"))
    rng = random.Random(a.seed)
    with results.open("a") as rf:
        for cf in cmds:
            cmd = json.loads(cf.read_text())
            pid = cmd["id"]
            if a.only and not re.search(a.only, pid):
                continue
            rsps = {}
            for c, v in cells:
                od = scratch / v / c["id"] / pid
                od.mkdir(parents=True, exist_ok=True)
                rsp = od / "args.rsp"
                lines = rewrite_rsp(cmd["rsp"], od, v)
                if not a.no_times:
                    idx = next((i for i, l in enumerate(lines) if not l.startswith("-")), len(lines))
                    lines.insert(idx, f"--times:{od / 'times.csv'}")
                rsp.write_text("\n".join(lines) + "\n")
                rsps[(c["id"], v)] = rsp
            total = a.warmup + a.runs
            for it in range(total):
                order = cells[:]
                rng.shuffle(order)
                for pos, (c, v) in enumerate(order):
                    is_warm = it < a.warmup
                    key = (pid, c["id"], v, it - a.warmup)
                    if not is_warm and key in done:
                        continue
                    layout = ROOT / c["layout"]
                    dotnet = ROOT / c["dotnet"]
                    # "${ROOT}" in a config's env values expands to the repo root (e.g. for a hook dll or profile dir)
                    env = clean_env({k: v.replace("${ROOT}", str(ROOT)) for k, v in c.get("env", {}).items()})
                    env["DOTNET_ROOT"] = str(dotnet.parent)
                    rsp = rsps[(c["id"], v)]
                    statsf, timesf = rsp.parent / "stats.json", rsp.parent / "times.csv"
                    for f in (statsf, timesf):
                        f.unlink(missing_ok=True)
                    if not a.no_hook:
                        # a config may bring its own startup hook (phase 3: MultiCoreJit); the stats hook goes last
                        env["DOTNET_STARTUP_HOOKS"] = ":".join(h for h in (env.get("DOTNET_STARTUP_HOOKS"), str(ROOT / a.hook)) if h)
                        env["FSCPERF_STATS_FILE"] = str(statsf)
                    dotnet = str(dotnet)
                    logp = rsp.parent / "last.log"
                    # CPU/RSS of the busiest other processes; names are left out because results are published
                    top = [dict(cpu=x["cpu"], rss_mb=x["rss_mb"]) for x in hostmon.top_procs(3, exclude_pids={os.getpid()})] if not is_warm else []
                    pre_busy, waited = hostmon.wait_quiet(a.quiet_threshold, 1.0, a.quiet_max_wait)
                    with logp.open("w") as log, hostmon.RunMonitor() as mon:
                        r = run_once(dotnet, str(layout / "fsc.dll"), rsp, cmd["cwd"], env, log)
                    host = mon.finish(r["cpu_s"])
                    host.update(pre_busy_cores=pre_busy, quiet_wait_s=waited, top_before=top,
                                loadavg=os.getloadavg())
                    r["host"] = host
                    r["host_flag"] = bool(host["foreign_cores_avg"] > a.flag_foreign_cores or host["swapouts"] > 0
                                          or pre_busy >= a.quiet_threshold or host["compressions"] > 5000)
                    if r["rc"] != 0:
                        print(f"FAIL {pid} {c['id']} {v} rc={r['rc']} log={logp}", flush=True)
                        print(logp.read_text()[-3000:], flush=True)
                        sys.exit(2)
                    tag = "warm" if is_warm else f"{it - a.warmup + 1}/{a.runs}"
                    print(f"{pid:55s} {c['id']:12s} {v} {tag:6s} wall={r['wall_s']:7.2f}s "
                          f"cpu={r['cpu_s']:7.2f}s rss={r['maxrss_mb']:6.0f}MB "
                          f"foreign={host['foreign_cores_avg']:.2f}c pre={pre_busy:.2f}c"
                          f"{' FLAG' if r['host_flag'] else ''}", flush=True)
                    if is_warm:
                        continue
                    if statsf.exists():
                        r["rt"] = json.loads(statsf.read_text())
                    if not a.no_times:
                        r["phases"], r["activities"] = parse_times_csv(timesf)
                    rec = dict(project=pid, config=c["id"], variant=v, iter=it - a.warmup, tag=a.tag,
                               hook=not a.no_hook, times=not a.no_times,
                               pos=pos, ts=time.time(), **r)
                    rf.write(json.dumps(rec) + "\n"); rf.flush()
    meta["end"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    metaf.write_text(json.dumps(meta, indent=1) + "\n")


if __name__ == "__main__":
    main()
