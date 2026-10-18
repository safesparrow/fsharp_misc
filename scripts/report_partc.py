#!/usr/bin/env python3
"""Report: effect of parallel (graph-based) type checking on warm compiler server workers.

Data: results/server_partc/runs.jsonl (scripts/partc_bench.py). Page: reports/server/partc.html (+ partc_standalone.html),
built with the detailed explorer of scripts/report_server_detail.py (metric grid, explorer, phases, every-run strips).
Reference = par (all parallel phases); test = seq_tc (everything parallel except type checking).
"""
import collections, json, statistics as st, subprocess, time
from pathlib import Path

import report_server_detail as R

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "server"


def main():
    rows = [json.loads(l) for l in open(ROOT / "results" / "server_partc" / "runs.jsonl")]
    meas = [r for r in rows if not r["warm"]]
    samples = collections.defaultdict(lambda: collections.defaultdict(list))
    flags = collections.defaultdict(dict)
    for r in meas:
        for k, v in R.metric_values(r, True).items():
            samples[(r["project"], r["side"])][k].append(v)
    projects = sorted({r["project"] for r in meas}, key=lambda p: -st.median(samples[(p, "par")]["wall_s"]))
    cells = ["par", "seq_tc"]
    for p in projects:
        for c in cells:
            rs = [r for r in meas if r["project"] == p and r["side"] == c]
            flags[p][c] = dict(n=len(rs), flagged=sum(1 for r in rs if (r.get("pre_busy") or 0) > 1.5))
    stats = {p: {c: {k: R.summ(v) for k, v in samples[(p, c)].items()} for c in cells} for p in projects}
    cmp = {p: {"seq_tc": {k: R.boot(samples[(p, "par")].get(k), samples[(p, "seq_tc")].get(k)) for k, *_ in R.METRICS}} for p in projects}
    tot = {"seq_tc": {k: R.boot_total([samples[(p, "par")].get(k) for p in projects], [samples[(p, "seq_tc")].get(k) for p in projects])
                      for k in R.TOTAL_KEYS}}

    # outputs: do both type checking modes produce the same assemblies?
    same, differ = [], []
    for p in projects:
        hs = {c: {json.dumps(r["outputs"], sort_keys=True) for r in meas if r["project"] == p and r["side"] == c} for c in cells}
        (same if hs["par"] == hs["seq_tc"] and len(hs["par"]) == 1 else differ).append(p)

    fp = lambda x: f"{100 * x['d']:+.1f}% (CI {100 * x['lo']:+.1f}% to {100 * x['hi']:+.1f}%)"
    T = tot["seq_tc"]
    by = sorted(((cmp[p]["seq_tc"]["wall_s"]["d"], p) for p in projects), reverse=True)
    big = [p for p in projects if stats[p]["par"]["wall_s"]["median"] >= 1.5 and not p.startswith("FSharp.Core__")]
    small = [p for p in projects if stats[p]["par"]["wall_s"]["median"] < 1.5 and not p.startswith("FSharp.Core__")]
    tsum = lambda ps, c, k="wall_s": sum(stats[p][c][k]["median"] for p in ps)
    core = [p for p in projects if p.startswith("FSharp.Core__")]
    rest = [p for p in projects if p not in core]
    tc_par, tc_seq = tsum(projects, "par", "phase:Typecheck"), tsum(projects, "seq_tc", "phase:Typecheck")
    findings = f"""## Findings
- **Turning off parallel type checking makes the 17 compilations slower but cheaper.** Sum of medians, all other parallel phases on in both: wall {fp(T['wall_s'])} ({T['wall_s']['ref']:.1f} s → {T['wall_s']['val']:.1f} s), CPU {fp(T['cpu_s'])} ({T['cpu_s']['ref']:.0f} s → {T['cpu_s']['val']:.0f} s).
- **The Typecheck phase itself:** {tc_par:.1f} s → {tc_seq:.1f} s ({100 * (tc_seq / tc_par - 1):+.0f}%), {fp(T['phase:Typecheck'])}.
- **Who gains from parallel type checking** (FSharp.Core excluded, see below): the compilations of 1.5 s or more ({len(big)}): {tsum(big, 'par'):.1f} s → {tsum(big, 'seq_tc'):.1f} s ({100 * (tsum(big, 'seq_tc') / tsum(big, 'par') - 1):+.0f}%); the {len(small)} small ones: {tsum(small, 'par'):.1f} s → {tsum(small, 'seq_tc'):.1f} s ({100 * (tsum(small, 'seq_tc') / tsum(small, 'par') - 1):+.0f}%).
- **Largest effects:** {", ".join(f"{p.replace('__', ' · ')} {100 * d:+.0f}%" for d, p in by[:4])}; smallest: {", ".join(f"{p.replace('__', ' · ')} {100 * d:+.0f}%" for d, p in by[-3:])}.
- **FSharp.Core is a built-in control:** the compiler never uses graph-based checking for FSharp.Core (`ParseAndCheckInputs.fs:1879`, `not tcConfig.compilingFSharpCore`), so its three compilations run the same code in both modes: {", ".join(f"{p.split('__')[1]} {100 * cmp[p]['seq_tc']['wall_s']['d']:+.0f}%" for p in core)}. Without them, the 14 other compilations: wall {100 * (tsum(rest, 'seq_tc') / tsum(rest, 'par') - 1):+.0f}% ({tsum(rest, 'par'):.1f} s → {tsum(rest, 'seq_tc'):.1f} s), CPU {100 * (tsum(rest, 'seq_tc', 'cpu_s') / tsum(rest, 'par', 'cpu_s') - 1):+.0f}%.
- **GC:** worker GC pause {fp(T['gc_pause_ms'])}, allocations {fp(T['alloc_mb'])}.
- **Outputs:** {len(same)} of {len(projects)} compilations produce identical assemblies in both modes{'' if not differ else '; different: ' + ', '.join(p.replace('__', ' · ') for p in differ)}.
- Code: branch [compiler_server](https://github.com/safesparrow/fsharp/tree/compiler_server) ([diff](https://github.com/safesparrow/fsharp/compare/99406265c88090c50e575afe8474f0979aa6f815...compiler_server)); summary report: [report_standalone.html](report_standalone.html).
- Setup: two warm compiler server workers (one per mode, the branch's default worker settings), each compilation 5 times per mode after a warm-up round, one compilation at a time, modes alternating.
"""
    hw = json.loads((ROOT / "results" / "hardware.json").read_text())
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT / "repos" / "server", capture_output=True, text=True).stdout.strip()
    harness = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    data = dict(
        meta=dict(title="Parallel type checking on the compiler server", generated=time.strftime("%Y-%m-%d %H:%M"),
                  harness_commit=f"{harness} (server {commit})", hardware=hw.get("hardware", []),
                  software=[x for x in hw.get("software", []) if x[0] in ("OS", "Runtime")] +
                           [["Compiler", "compiler_server branch on dotnet/fsharp main @ 99406265, ReadyToRun layout"], ["Server branch", commit]],
                  configs=[dict(id="par", desc="all parallel phases (fsc default): graph type checking, parallel optimization, IL gen, reference resolution", env={}),
                           dict(id="seq_tc", desc="the same with sequential type checking", env={"options": " ".join(
                               ["--parallelcompilation-", "--test:ParallelOptimization", "--test:ParallelIlxGen", "--parallelreferenceresolution"])})],
                  ref_config="par", refs=["par"], default_cfgs=cells, flag_rule=R.FLAG_RULE, B=R.B),
        metrics=[dict(key=k, label=l, unit=u, group=g, digits=dg) for k, l, u, g, dg in R.METRICS],
        projects=projects, phase_segments=R.PHASES, findings_md=findings,
        phase1=dict(cells=cells, stats=stats, cmp_by_ref={"par": cmp}, total_by_ref={"par": tot}, runs=5, flags=flags),
        phase2=None, sdk_compare=None, traces=None, overhead=None)
    js = f"window.REPORT_DATA = {json.dumps(data, separators=(',', ':'))};\n"
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    (OUT / "data" / "partc-data.js").write_text(js)
    page = R.build_page((ROOT / "scripts" / "report_template.html").read_text())
    page = page.replace('<script src="data/detail-data.js"></script>', '<script src="data/partc-data.js"></script>')
    page = page.replace('data: <a href="data/detail-data.js">data/detail-data.js</a>', 'data: <a href="data/partc-data.js">data/partc-data.js</a>')
    page = page.replace("<title>", "<title>Parallel Type Checking · ").replace('state = { view: 1, metric: "wall_s", cmp: "par_eff", ref: "flags",',
                                                                            'state = { view: 1, metric: "wall_s", cmp: "par_eff", ref: "par",')
    page = page.replace('''        <li>Per config: 1 warm-up round (a fresh worker compiles each project once), then 5 measured rounds over the 5 projects. Configs run one after another (batches e1–e5 on the same day and machine).</li>''',
                        '''        <li>Two warm workers, one per mode. 1 warm-up round, then 5 measured rounds over all 17 captured compilations; each compilation runs in both modes back to back (order alternating per round), one compilation at a time.</li>''')
    (OUT / "partc.html").write_text(page)
    (OUT / "partc_standalone.html").write_text(page.replace('<script src="data/partc-data.js"></script>', "<script>\n" + js + "</script>")
                                               .replace('data: <a href="data/partc-data.js">data/partc-data.js</a>', "data inlined"))
    print(findings)
    print(f"wrote {OUT / 'partc.html'}")


if __name__ == "__main__":
    main()
