#!/usr/bin/env python3
"""Build the reports from benchmark results.

Inputs (all optional except phase1):
  results/phase1/runs.jsonl       timing runs, JIT configs, variant=par       (bench.py)
  results/phase2/runs.jsonl       timing runs, JIT configs x {seq, par}       (bench.py --variants seq,par)
  results/traces/traces.jsonl     EventPipe-traced runs (JIT tiers, GC)       (trace.py)
  results/overhead/{bare,instrumented}/runs.jsonl  instrumentation A/B        (overhead.sh)
  reports/findings.md             hand-written interpretation (kept separate from generated numbers)

Outputs:
  reports/data/summary.json       every number the reports show (canonical data file)
  reports/data/report-data.js     the same, as `window.REPORT_DATA = ...` so report.html works from file://
  reports/report.html             static page, loads data/report-data.js, has no data of its own
  reports/report.md               short, shareable version with PNG charts (reports/img/*.png)

Statistics:
  * per (project, cell, metric): n, median, mean, sd, min, max, q1, q3, raw values
  * change = median(cell)/median(ref) - 1, 95% percentile bootstrap CI (groups resampled
    independently, B=2000, fixed seed). "significant" = CI excludes 0
  * whole solution = sum over projects of per-project medians, CI by bootstrapping every project
  * phase 2 interaction = (par/seq under config) / (par/seq under baseline) - 1, i.e. does the
    config change how much parallel compilation helps

Run with .venv/bin/python (matplotlib for the PNGs).
"""
import argparse, json, math, random, statistics, subprocess, time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
B = int(__import__("os").environ.get("REPORT_B", "2000"))  # bootstrap resamples; lower only for quick previews

# ------------------------------------------------------------------ metrics
PHASE_SEGMENTS = [  # (segment, [fsc ReportTime phases])
    ("Import references", ["Import mscorlib+FSharp.Core", "Import non-system references"]),
    ("Parse", ["Parse inputs"]),
    ("Typecheck", ["Typecheck", "Typechecked"]),
    ("Optimizations", ["Optimizations", "Ending Optimizations"]),
    ("TAST -> IL", ["TAST -> IL"]),
    ("Write .NET binary", ["Write .NET Binary"]),
    ("Other phases", None),  # every other top-level phase (interface/opt data, XML docs, tailcall checks...)
    ("Startup & exit", "outside"),  # wall - sum(phases): runtime start, option parsing, exit
]

METRICS = [  # key, label, unit, group, digits
    ("wall_s", "Wall time", "s", "Process", 2),
    ("cpu_s", "CPU time (user+sys)", "s", "Process", 2),
    ("maxrss_mb", "Peak RSS", "MB", "Process", 0),
    ("gc_pause_ms", "GC pause time (total)", "ms", "Runtime: GC", 0),
    ("gc_count", "GC count (all gens)", "", "Runtime: GC", 0),
    ("gc_count_gen2", "Gen2 GC count", "", "Runtime: GC", 0),
    ("gc_alloc_mb", "Allocated", "MB", "Runtime: GC", 0),
    ("jit_time_ms", "JIT time (all threads)", "ms", "Runtime: JIT", 0),
    ("jit_methods", "Methods JIT-compiled", "", "Runtime: JIT", 0),
    ("jit_il_kb", "IL bytes JIT-compiled", "KB", "Runtime: JIT", 0),
    ("lock_contentions", "Monitor lock contentions", "", "Runtime: other", 0),
] + [(f"phase:{s}", f"Phase: {s}", "s", "Compiler phases (--times)", 2) for s, _ in PHASE_SEGMENTS]
MKEYS = [m[0] for m in METRICS]
TOTAL_METRICS = ["wall_s", "cpu_s", "gc_pause_ms", "jit_time_ms", "gc_alloc_mb"] + [f"phase:{s}" for s, _ in PHASE_SEGMENTS]

CONFIG_DESC = {
    "baseline": "Default runtime settings (what the SDK does today)",
    "pgo0": "DOTNET_TieredPGO=0",
    "ccd0": "DOTNET_TC_CallCountingDelayMs=0",
    "pgo0_ccd0": "DOTNET_TieredPGO=0 + DOTNET_TC_CallCountingDelayMs=0",
    "jit_flags": "Compiler built from branch jit_flags; env exactly as its Microsoft.FSharp.Targets injects (same as pgo0_ccd0)",
}


def segments(r):
    ph = r.get("phases") or {}
    if not ph:
        return {}
    known = {p for _, ps in PHASE_SEGMENTS if isinstance(ps, list) for p in ps}
    out = {}
    for s, ps in PHASE_SEGMENTS:
        if isinstance(ps, list):
            out[s] = sum(ph.get(p, 0.0) for p in ps)
        elif ps is None:
            out[s] = sum(v for k, v in ph.items() if k not in known)
    out["Startup & exit"] = max(0.0, r["wall_s"] - sum(ph.values()))
    return out


def metrics_of(r):
    m = dict(wall_s=r["wall_s"], cpu_s=r["cpu_s"], maxrss_mb=r["maxrss_mb"])
    rt = r.get("rt") or {}
    if rt:
        m.update(gc_pause_ms=rt["gc_pause_ms"], gc_count=rt["gc_count_gen0"], gc_count_gen2=rt["gc_count_gen2"],
                 gc_alloc_mb=rt["gc_alloc_mb"], jit_time_ms=rt["jit_time_ms"], jit_methods=rt["jit_methods"],
                 jit_il_kb=rt["jit_il_kb"], lock_contentions=rt["lock_contentions"])
    for s, v in segments(r).items():
        m[f"phase:{s}"] = v
    return m


# ------------------------------------------------------------------ stats
def q(xs, p):
    xs = sorted(xs); k = (len(xs) - 1) * p; f = math.floor(k); c = math.ceil(k)
    return xs[f] if f == c else xs[f] + (xs[c] - xs[f]) * (k - f)


def summarize(xs):
    return dict(n=len(xs), median=statistics.median(xs), mean=statistics.fmean(xs),
                sd=statistics.stdev(xs) if len(xs) > 1 else 0.0, min=min(xs), max=max(xs),
                q1=q(xs, .25), q3=q(xs, .75), values=[round(x, 5) for x in xs])


def _bmed(rng, xs):
    return statistics.median(rng.choices(xs, k=len(xs)))


def _ci(rs):
    rs.sort(); n = len(rs)
    return rs[int(.025 * n)], rs[min(n - 1, int(.975 * n))]


def ratio(rng, a, b):
    ma, mb = statistics.median(a), statistics.median(b)
    if ma == 0:
        return None
    rs = []
    for _ in range(B):
        x = _bmed(rng, a)
        if x != 0:
            rs.append(_bmed(rng, b) / x - 1)
    lo, hi = _ci(rs)
    return dict(d=mb / ma - 1, lo=lo, hi=hi, sig=bool(lo > 0 or hi < 0))


def ratio_total(rng, ga, gb):
    ta = sum(statistics.median(g) for g in ga); tb = sum(statistics.median(g) for g in gb)
    if ta == 0:
        return None
    rs = []
    for _ in range(B // 2):
        x = sum(_bmed(rng, g) for g in ga)
        if x:
            rs.append(sum(_bmed(rng, g) for g in gb) / x - 1)
    lo, hi = _ci(rs)
    return dict(ref=ta, val=tb, d=tb / ta - 1, lo=lo, hi=hi, sig=bool(lo > 0 or hi < 0))


def interaction(rng, seq0, par0, seq1, par1):
    """(par1/seq1)/(par0/seq0) - 1 on medians; inputs are lists (per project) or lists of lists (total)."""
    tot = isinstance(seq0[0], list)
    f = (lambda gs, r: sum(_bmed(r, g) for g in gs)) if tot else (lambda g, r: _bmed(r, g))
    m = (lambda gs: sum(statistics.median(g) for g in gs)) if tot else statistics.median
    d = (m(par1) / m(seq1)) / (m(par0) / m(seq0)) - 1
    rs = []
    for _ in range(B // (2 if tot else 1)):
        rs.append((f(par1, rng) / f(seq1, rng)) / (f(par0, rng) / f(seq0, rng)) - 1)
    lo, hi = _ci(rs)
    return dict(d=d, lo=lo, hi=hi, sig=bool(lo > 0 or hi < 0))


def load_runs(d):
    p = Path(d) / "runs.jsonl"
    if not p.exists():
        return [], []
    runs = []
    for l in p.read_text().splitlines():
        try:
            runs.append(json.loads(l))
        except json.JSONDecodeError:
            pass  # a line still being written by a running benchmark
    metas = [json.loads(m.read_text()) for m in sorted(Path(d).glob("meta-*.json"))]
    return runs, metas


FLAG_RULE = "foreign CPU > 1.0 core avg during the run, or host busy >= 1.0 core right before it, or any swapout, or > 5000 page compressions"


def is_flagged(r):
    """Uniform host-noise rule, recomputed from stored monitor data (see hostmon.py)."""
    h = r.get("host")
    if not h:
        return False
    return bool(h["foreign_cores_avg"] > 1.0 or h["pre_busy_cores"] >= 1.0 or h["swapouts"] > 0
                or h["compressions"] > 5000)


def group(runs, cellf):
    g = defaultdict(lambda: defaultdict(list))
    flags = defaultdict(lambda: dict(n=0, flagged=0, foreign=[]))
    for r in runs:
        c = cellf(r)
        for k, v in metrics_of(r).items():
            g[(r["project"], c)][k].append(v)
        f = flags[(r["project"], c)]
        f["n"] += 1
        f["flagged"] += int(is_flagged(r))
        if r.get("host"):
            f["foreign"].append(r["host"]["foreign_cores_avg"])
    return g, flags


def flags_out(flags, projects, cells):
    out = defaultdict(dict)
    for p in projects:
        for c in cells:
            f = flags.get((p, c))
            if f:
                out[p][c] = dict(n=f["n"], flagged=f["flagged"],
                                 foreign_cores_median=statistics.median(f["foreign"]) if f["foreign"] else None,
                                 foreign_cores_max=max(f["foreign"]) if f["foreign"] else None)
    return out


def stats_block(g, projects, cells):
    out = defaultdict(lambda: defaultdict(dict))
    for p in projects:
        for c in cells:
            for k in MKEYS:
                xs = g.get((p, c), {}).get(k)
                if xs:
                    out[p][c][k] = summarize(xs)
    return out


def cmp_block(rng, g, projects, ref, cells, metrics=MKEYS):
    per = defaultdict(lambda: defaultdict(dict)); tot = defaultdict(dict)
    for c in cells:
        if c == ref:
            continue
        for k in metrics:
            ga, gb = [], []
            for p in projects:
                a = g.get((p, ref), {}).get(k); b = g.get((p, c), {}).get(k)
                if a and b:
                    r = ratio(rng, a, b)
                    if r:
                        per[p][c][k] = r
                    ga.append(a); gb.append(b)
            if ga and k in TOTAL_METRICS and len(ga) == len(projects):
                r = ratio_total(rng, ga, gb)
                if r:
                    tot[c][k] = r
    return per, tot


def project_order(g, ref):
    ps = sorted({p for p, _ in g})
    return sorted(ps, key=lambda p: -statistics.median(g[(p, ref)]["wall_s"]) if (p, ref) in g else 0)


def git(cwd, *a):
    return subprocess.run(["git", *a], capture_output=True, text=True, cwd=cwd).stdout.strip()


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(ROOT / "results"))
    ap.add_argument("--out", default=str(ROOT / "reports"))
    a = ap.parse_args()
    res = Path(a.results); out = Path(a.out)
    (out / "data").mkdir(parents=True, exist_ok=True); (out / "img").mkdir(parents=True, exist_ok=True)
    rng = random.Random(1234)

    runs1, metas1 = load_runs(res / "phase1")
    if not runs1:
        raise SystemExit("no phase1 results")
    runs1 = [r for r in runs1 if r["variant"] == "par"]
    meta1 = metas1[-1]
    g1, fl1 = group(runs1, lambda r: r["config"])
    cells1 = [c["id"] for c in meta1["configs"] if any(k[1] == c["id"] for k in g1)]
    projects = project_order(g1, "baseline")
    per1, tot1 = cmp_block(rng, g1, projects, "baseline", cells1)
    # sensitivity: whole-solution totals using only runs NOT flagged by the host monitor
    g1c, _ = group([r for r in runs1 if not is_flagged(r)], lambda r: r["config"])
    _, tot1c = cmp_block(rng, g1c, [p for p in projects if all((p, c) in g1c for c in cells1)], "baseline", cells1,
                         metrics=["wall_s", "cpu_s"])
    # noise floor: two builds of the same compiler code with the same env (jit_flags vs pgo0_ccd0)
    aa = None
    if "jit_flags" in cells1 and "pgo0_ccd0" in cells1:
        pa, ta = cmp_block(rng, g1, projects, "pgo0_ccd0", ["jit_flags"], metrics=["wall_s", "cpu_s"])
        aa = dict(per={p: pa[p]["jit_flags"] for p in projects}, total=ta["jit_flags"])

    cmds = {json.loads(f.read_text())["id"]: json.loads(f.read_text()) for f in (ROOT / "work/cmds/baseline").glob("*.json")}
    pinfo = {p: dict(project=cmds[p]["project"], tfm=cmds[p]["tfm"], n_sources=cmds[p]["n_sources"]) if p in cmds else {}
             for p in projects}

    data = dict(
        meta=build_meta(meta1, metas1, cells1),
        metrics=[dict(key=k, label=l, unit=u, group=gname, digits=dg) for k, l, u, gname, dg in METRICS],
        phase_segments=[s for s, _ in PHASE_SEGMENTS],
        projects=projects, project_info=pinfo,
        phase1=dict(ref="baseline", cells=cells1, stats=stats_block(g1, projects, cells1), cmp=per1, total=tot1,
                    total_clean=tot1c, flags=flags_out(fl1, projects, cells1), aa=aa,
                    runs=runs_label(metas1), warmup=meta1["warmup"]))

    runs2, metas2 = load_runs(res / "phase2")
    if runs2:
        meta2 = metas2[-1]
        jit = [c["id"] for c in meta2["configs"]]
        g2, fl2 = group(runs2, lambda r: f'{r["config"]}/{r["variant"]}')
        cells2 = [f"{c}/{v}" for c in jit for v in ("seq", "par")]
        p2 = [p for p in projects if all((p, c) in g2 for c in cells2)]
        par_eff, par_tot = defaultdict(dict), {}
        for c in jit:
            per, tot = cmp_block(rng, g2, p2, f"{c}/seq", [f"{c}/par"])
            for p in p2:
                par_eff[p][c] = per[p][f"{c}/par"]
            par_tot[c] = tot[f"{c}/par"]
        jit_eff, jit_tot = {}, {}
        for v in ("seq", "par"):
            per, tot = cmp_block(rng, g2, p2, f"baseline/{v}", [f"{c}/{v}" for c in jit])
            jit_eff[v] = {p: {c.split("/")[0]: x for c, x in per[p].items()} for p in p2}
            jit_tot[v] = {c.split("/")[0]: x for c, x in tot.items()}
        inter, inter_tot = defaultdict(dict), {}
        for c in jit:
            if c == "baseline":
                continue
            for k in ("wall_s", "cpu_s"):
                for p in p2:
                    inter[p].setdefault(c, {})[k] = interaction(
                        rng, g2[(p, "baseline/seq")][k], g2[(p, "baseline/par")][k], g2[(p, f"{c}/seq")][k], g2[(p, f"{c}/par")][k])
                inter_tot.setdefault(c, {})[k] = interaction(
                    rng, [g2[(p, "baseline/seq")][k] for p in p2], [g2[(p, "baseline/par")][k] for p in p2],
                    [g2[(p, f"{c}/seq")][k] for p in p2], [g2[(p, f"{c}/par")][k] for p in p2])
        data["phase2"] = dict(jit=jit, variants=["seq", "par"], cells=cells2, projects=p2,
                              stats=stats_block(g2, p2, cells2), par_eff=par_eff, par_total=par_tot,
                              jit_eff=jit_eff, jit_total=jit_tot, interaction=inter, interaction_total=inter_tot,
                              flags=flags_out(fl2, p2, cells2), runs=runs_label(metas2), warmup=meta2["warmup"],
                              meta=dict(start=meta2["start"], end=meta2.get("end"), argv=meta2["argv"]))

    # SDK 10 vs SDK 11 vs main (fsc level) and whole-`dotnet build` results; computed by analyze_sdk_compare.py
    if (res / "sdk_compare" / "runs.jsonl").exists() or (res / "buildbench" / "runs.jsonl").exists():
        import contextlib, io
        import analyze_sdk_compare
        with contextlib.redirect_stdout(io.StringIO()):
            analyze_sdk_compare.main()
        sc = res / "sdk_compare" / "summary.json"
        data["sdk_compare"] = json.loads(sc.read_text()) if sc.exists() else None
    data["traces"] = load_traces(res / "traces" / "traces.jsonl")
    data["overhead"] = load_overhead(rng, res / "overhead")
    fp = out / "findings.md"
    data["findings_md"] = fp.read_text() if fp.exists() else ""

    js = json.dumps(data, separators=(",", ":"))
    (out / "data" / "summary.json").write_text(json.dumps(data, indent=1))
    (out / "data" / "report-data.js").write_text(f"window.REPORT_DATA = {js};\n")
    tpl = ROOT / "scripts" / "report_template.html"
    (out / "report.html").write_text(tpl.read_text())
    import report_md
    report_md.write(data, out)
    print(f"wrote {out}/report.md, report.html, data/summary.json, data/report-data.js, img/*.png")


def runs_label(metas):
    """Measured runs per cell; '10–20' when invocations used different counts."""
    ns = sorted({m["runs"] for m in metas})
    return str(ns[0]) if len(ns) == 1 else f"{ns[0]}–{ns[-1]}"


def build_meta(meta1, metas1, cells1):
    hw = json.loads((ROOT / "results" / "hardware.json").read_text()) if (ROOT / "results" / "hardware.json").exists() else {}
    lay = meta1.get("layouts", {}).get("layouts/baseline", {})
    commit = git(ROOT / "repos" / "baseline", "rev-parse", "HEAD")
    jf = git(ROOT / "repos" / "jit_flags", "rev-parse", "HEAD")
    cfgs = []
    for c in meta1["configs"]:
        if c["id"] in cells1:
            cfgs.append(dict(id=c["id"], desc=CONFIG_DESC.get(c["id"], ""), env=c.get("env", {}), layout=c["layout"]))
    return dict(
        title="F# compiler (fsc) benchmark: JIT flags and parallel compilation",
        generated=time.strftime("%Y-%m-%d %H:%M %Z"),
        harness_commit=git(ROOT, "rev-parse", "--short", "HEAD"),
        hardware=hw.get("hardware", []), software=hw.get("software", []) + [
            ["dotnet/fsharp commit (baseline)", commit],
            ["jit_flags branch commit", f"{jf} (https://github.com/safesparrow/fsharp/tree/jit_flags)"],
            ["Runtime", f"Microsoft.NETCore.App {lay.get('runtime', '?')}"],
            ["crossgen2", lay.get("runtime", "?") + " (same build as the runtime)"],
        ],
        configs=cfgs,
        phase1_window=[m["start"] for m in metas1[:1]] + [metas1[-1].get("end")],
        flag_rule=FLAG_RULE,
    )


def load_traces(p):
    if not p.exists():
        return None
    recs = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    by = defaultdict(list)
    for r in recs:
        by[(r["variant"], r["project"], r["config"])].append(r)
    out = defaultdict(lambda: defaultdict(dict))
    tiers = set()
    for (v, p, c), rs in by.items():
        t = defaultdict(lambda: dict(methods=[], compile_ms=[]))
        for r in rs:
            for tier, x in (r.get("jit_by_tier") or {}).items():
                t[tier]["methods"].append(x["methods"]); t[tier]["compile_ms"].append(x["compile_ms"]); tiers.add(tier)
        out[v][p][c] = dict(
            n=len(rs),
            tiers={k: dict(methods=statistics.median(x["methods"]), compile_ms=statistics.median(x["compile_ms"]))
                   for k, x in t.items()},
            jit_methods=statistics.median(r["jit_methods_total"] for r in rs),
            jit_compile_ms=statistics.median(r["jit_compile_ms_total"] for r in rs),
            gc_count=statistics.median(r["gc_count"] for r in rs),
            gc_pause_ms=statistics.median(r["gc_pause_ms_total"] for r in rs),
            gc_pause_max_ms=statistics.median(r["gc_pause_ms_max"] for r in rs),
            wall_s_traced=statistics.median(r["wall_s_traced"] for r in rs))
    return dict(by_variant=out, tiers=sorted(tiers), n_records=len(recs))


def load_overhead(rng, d):
    bare, _ = load_runs(d / "bare"); inst, _ = load_runs(d / "instrumented")
    if not bare or not inst:
        return None
    gb, _ = group(bare, lambda r: "bare"); gi, _ = group(inst, lambda r: "instrumented")
    out = {}
    for p in sorted({r["project"] for r in bare}):
        out[p] = {}
        for k in ("wall_s", "cpu_s", "maxrss_mb"):
            a = gb[(p, "bare")][k]; b = gi[(p, "instrumented")][k]
            out[p][k] = dict(bare=statistics.median(a), instrumented=statistics.median(b), n=min(len(a), len(b)),
                             **ratio(rng, a, b))
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
