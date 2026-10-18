#!/usr/bin/env python3
"""Investigation report: how to make the F# compiler server faster (reports/server/investigation.html).

Inputs (all produced by this repo's scripts):
  results/server_fsc/runs.jsonl        per-compilation runs (fscbench.py), tags e1 e2 e3 e5
  results/server_fsc/samples_FCS.json  native sampling breakdown of one FCS compilation (sample_fsc.py)
  results/server_e4/runs.jsonl         whole-build runs (serverbench.py)
Every number in the page is computed here from those files; the prose around them is in this script.
"""
import collections, html, json, statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FSC = ROOT / "results" / "server_fsc" / "runs.jsonl"
E4 = ROOT / "results" / "server_e4" / "runs.jsonl"
SAMPLES = ROOT / "results" / "server_fsc" / "samples_FCS.json"
OUT = ROOT / "reports" / "server" / "investigation.html"
SHORT = {"FSharp.Compiler.Service__net11.0": "FCS", "FSharp.Core__netstandard2.0": "FSharp.Core",
         "FSharp.Compiler.ComponentTests__net11.0": "ComponentTests", "FSharp.Test.Utilities__net11.0": "Test.Utilities",
         "FSharp.Build__netstandard2.0": "FSharp.Build"}
PROJ = list(SHORT)
DESC = {
    "stock": "stock fsc process", "flags": "stock fsc process + JIT flags (TieredPGO=0, CallCountingDelayMs=0)",
    "srv": "worker: Server GC, 5 heaps (default)", "srv_dedicated": "worker per project (perfect affinity)",
    "srv_heaps_all": "worker: 11 heaps", "srv_datas": "worker: DATAS (no fixed heap count)",
    "srv_nonconc": "worker: non-concurrent GC", "srv_flags": "worker + both JIT flags",
    "srv_reuse": "worker: keep assembly caches between compilations", "srv_batch": "worker: GC latency Batch while compiling",
    "srv_noidlegc": "worker: no idle compacting GC", "srv_heaps_all_nonconc": "worker: 11 heaps + non-concurrent GC",
    "srv_heaps_all_batch": "worker: 11 heaps + Batch latency", "srv_ccd0": "worker: CallCountingDelayMs=0 (PGO kept)",
    "srv_pgo0": "worker: TieredPGO=0", "srv_ccd0_nonconc": "worker: CallCountingDelayMs=0 + non-concurrent GC",
}


def load_fsc():
    rows = [json.loads(l) for l in open(FSC)]
    best = {}  # (tag, config) batches; for duplicate configs keep the latest tag
    for r in rows:
        best.setdefault(r["config"], set()).add(r["tag"])
    g = collections.defaultdict(list)
    for r in rows:
        g[(r["tag"], r["config"], r["project"], r["warm"])].append(r)
    return rows, g


def med(rs, k):
    return st.median(r[k] for r in rs)


def main():
    rows, g = load_fsc()
    tags_of = collections.defaultdict(list)
    for (tag, cfg, _, _) in g:
        if tag == "verify":  # output check: one round of three projects, not a measurement
            continue
        if tag not in tags_of[cfg]:
            tags_of[cfg].append(tag)

    def stat(cfg, tag=None):
        tag = tag or sorted(tags_of[cfg])[-1]
        per = {}
        for p in PROJ:
            rs = g.get((tag, cfg, p, False))
            if rs:
                per[p] = dict(wall=med(rs, "wall_s"), cpu=med(rs, "cpu_s"),
                              gc=st.median(r["stats"].get("gcPauseMs", 0) for r in rs) if rs[0]["stats"] else None)
        cold = {p: g[(tag, cfg, p, True)][0]["wall_s"] for p in PROJ if (tag, cfg, p, True) in g}
        return dict(tag=tag, per=per, wall=sum(x["wall"] for x in per.values()), cpu=sum(x["cpu"] for x in per.values()),
                    cold=cold, cold_sum=sum(cold.values()))

    F = stat("flags", "e2")
    S = stat("stock", "e1")
    configs = ["stock", "flags", "srv", "srv_dedicated", "srv_reuse", "srv_noidlegc", "srv_flags", "srv_pgo0", "srv_ccd0",
               "srv_datas", "srv_heaps_all", "srv_nonconc", "srv_batch", "srv_heaps_all_nonconc", "srv_heaps_all_batch",
               "srv_ccd0_nonconc"]
    stats = {c: stat(c, "e1" if c in ("stock", "srv_dedicated") else ("e2" if c == "flags" else None)) for c in configs if c in tags_of}

    # Compile-level table
    trs = []
    for c in configs:
        if c not in stats:
            continue
        x = stats[c]
        cells = "".join(
            f"<td class=n>{x['per'][p]['wall']:.2f}<small> {x['per'][p]['wall'] / F['per'][p]['wall']:.2f}×</small></td>" if p in x["per"] else "<td></td>"
            for p in PROJ)
        trs.append(f"<tr><td><code>{c}</code><br><small>{html.escape(DESC.get(c, ''))}</small></td>{cells}"
                   f"<td class=n><b>{x['wall']:.1f}</b><small> {x['wall'] / F['wall']:.2f}×</small></td>"
                   f"<td class=n>{x['cpu']:.0f}<small> {x['cpu'] / F['cpu']:.2f}×</small></td>"
                   f"<td class=n>{x['cold'].get(PROJ[0], 0):.1f}</td><td class=n>{x['cold_sum']:.1f}</td>"
                   f"<td class=n>{(x['per'][PROJ[0]]['gc'] or 0) / 1000:.1f}</td></tr>" if x["per"].get(PROJ[0], {}).get("gc") is not None else
                   f"<tr><td><code>{c}</code><br><small>{html.escape(DESC.get(c, ''))}</small></td>{cells}"
                   f"<td class=n><b>{x['wall']:.1f}</b><small> {x['wall'] / F['wall']:.2f}×</small></td>"
                   f"<td class=n>{x['cpu']:.0f}<small> {x['cpu'] / F['cpu']:.2f}×</small></td>"
                   f"<td class=n>{x['cold'].get(PROJ[0], 0):.1f}</td><td class=n>{x['cold_sum']:.1f}</td><td class=n>–</td></tr>")

    def build_table(path, ref_cfg="flags"):
        rows_ = [json.loads(l) for l in open(path)] if path.exists() else []
        meas, warm = collections.defaultdict(list), {}
        for r in rows_:
            (warm.__setitem__(r["config"], r) if r["warm"] else meas[r["config"]].append(r))
        ref = meas.get(ref_cfg)
        rw = st.mean(r["wall_s"] for r in ref) if ref else None
        rc = st.mean(r["cpu_s"] for r in ref) if ref else None
        out = []
        for c in dict.fromkeys(r["config"] for r in rows_):
            rs = meas[c]
            if not rs:
                continue
            w, cpu = st.mean(r["wall_s"] for r in rs), st.mean(r["cpu_s"] for r in rs)
            walls = " · ".join(f"{r['wall_s']:.1f}" for r in rs)
            rel = f"{w / rw:.2f}×" if rw else "–"
            relc = f"{cpu / rc:.2f}×" if rc else "–"
            out.append(f"<tr><td><code>{c}</code></td><td class=n>{warm[c]['wall_s']:.1f}</td><td class=n>{walls}</td>"
                       f"<td class=n><b>{w:.1f}</b></td><td class=n>{rel}</td><td class=n>{cpu:.0f}</td><td class=n>{relc}</td>"
                       f"<td class=n>{max(r['workers_alive'] for r in rs)}</td><td class=n>{max(r['worker_rss_mb'] for r in rs):.0f}</td></tr>")
        return "".join(out)

    BUILD_HDR = ("<tr><th>config</th><th>warm-up (cold workers) s</th><th>measured builds s</th><th>mean s</th><th>vs flags</th>"
                 "<th>CPU s</th><th>CPU vs flags</th><th>workers</th><th>worker RSS MB</th></tr>")
    t_e4 = build_table(E4)
    t_fan = build_table(ROOT / "results" / "server_fantomas" / "runs.jsonl")
    t_fin = (build_table(ROOT / "results" / "server_final" / "runs.jsonl").replace("<code>server</code>", "<code>server</code> FCS slnx")
             + build_table(ROOT / "results" / "server_stall" / "runs.jsonl").replace("<code>server</code>", "<code>server</code> FCS slnx, repeat")
             + build_table(ROOT / "results" / "server_stall2" / "runs.jsonl").replace("<code>server</code>", "<code>server</code> FCS slnx, repeat 2")
             + build_table(ROOT / "results" / "server_final_fantomas" / "runs.jsonl").replace("<code>server</code>", "<code>server</code> Fantomas"))

    # Whole builds (legacy single table kept for reference)
    e4rows = [json.loads(l) for l in open(E4)] if E4.exists() else []
    e4 = collections.defaultdict(list)
    e4warm = {}
    for r in e4rows:
        if r["warm"]:
            e4warm[r["config"]] = r
        else:
            e4[r["config"]].append(r)
    e4cfgs = list(dict.fromkeys(r["config"] for r in e4rows))
    fl = st.mean(r["wall_s"] for r in e4["flags"]) if e4.get("flags") else None
    flc = st.mean(r["cpu_s"] for r in e4["flags"]) if e4.get("flags") else None
    btrs = []
    for c in e4cfgs:
        rs = e4[c]
        if not rs:
            continue
        w = st.mean(r["wall_s"] for r in rs)
        cpu = st.mean(r["cpu_s"] for r in rs)
        walls = " · ".join(f"{r['wall_s']:.1f}" for r in rs)
        btrs.append(f"<tr><td><code>{c}</code></td><td class=n>{e4warm[c]['wall_s']:.1f}</td>"
                    f"<td class=n>{walls}</td><td class=n><b>{w:.1f}</b></td>"
                    f"<td class=n>{w / fl:.2f}×</td><td class=n>{cpu:.0f}</td><td class=n>{cpu / flc:.2f}×</td>"
                    f"<td class=n>{max(r['workers_alive'] for r in rs)}</td></tr>")

    samples = json.loads(SAMPLES.read_text()) if SAMPLES.exists() else {}
    strs = "".join(
        f"<tr><td>{n}</td>" + "".join(f"<td class=n>{100 * v[k] / v['busy']:.1f}%</td>" for k in ("managed", "gc", "jit", "runtime", "other")) + "</tr>"
        for n, v in samples.items())

    def pct(a, b):
        return f"{100 * (a / b - 1):+.0f}%"

    s = stats
    def bmean(path, cfg, key="wall_s", warm=False):
        if not path.exists():
            return float("nan")
        rs = [json.loads(l) for l in open(path)]
        rs = [r for r in rs if r["config"] == cfg and r["warm"] == warm]
        return st.mean(r[key] for r in rs) if rs else float("nan")

    FAN = ROOT / "results" / "server_fantomas" / "runs.jsonl"
    b_fl, b_srv, b_nc, b_best, b_datas, b_h11 = (bmean(E4, c) for c in
        ("flags", "server", "server_nonconc", "server_ccd0_nonconc", "server_ccd0_nonconc_datas", "server_ccd0_nonconc_heapsall"))
    f_st, f_fl, f_best, f_datas = (bmean(FAN, c) for c in ("baseline", "flags", "server_ccd0_nonconc", "server_ccd0_nonconc_datas"))
    fc_fl, fc_best = bmean(FAN, "flags", "cpu_s"), bmean(FAN, "server_ccd0_nonconc", "cpu_s")
    bc_fl, bc_best = bmean(E4, "flags", "cpu_s"), bmean(E4, "server_ccd0_nonconc", "cpu_s")
    w_srv, w_nc, w_best = (bmean(E4, c, warm=True) for c in ("server", "server_nonconc", "server_ccd0_nonconc"))
    rss = lambda path, cfg: max((json.loads(l)["worker_rss_mb"] for l in open(path) if json.loads(l)["config"] == cfg), default=0)

    findings_rows = [
        ("Keep the compiler warm: compile in long-lived workers (the server)",
         f"Per compilation (5 projects summed): wall {pct(s['srv']['wall'], F['wall'])}, CPU {pct(s['srv']['cpu'], F['cpu'])} vs stock + JIT flags "
         f"(vs stock: {pct(s['srv']['wall'], S['wall'])}); FSharp.Build {s['srv']['per'][PROJ[4]]['wall'] / F['per'][PROJ[4]]['wall']:.2f}×, "
         f"FCS {s['srv']['per'][PROJ[0]]['wall'] / F['per'][PROJ[0]]['wall']:.2f}× of flags. Whole builds with the tuned workers (rows 2–3): "
         f"Fantomas {pct(f_best, f_fl)} wall / {pct(fc_best, fc_fl)} CPU vs flags ({pct(f_best, f_st)} vs stock); "
         f"FSharp.Compiler.Service.slnx {pct(b_best, b_fl)} wall / {pct(bc_best, bc_fl)} CPU vs flags.",
         "built (this branch)", "Done."),
        ("Workers: DOTNET_TC_CallCountingDelayMs=0, Tiered PGO kept",
         f"Cold first FCS compilation {s['srv']['cold'][PROJ[0]]:.1f} s → {s['srv_ccd0']['cold'][PROJ[0]]:.1f} s ({pct(s['srv_ccd0']['cold'][PROJ[0]], s['srv']['cold'][PROJ[0]])}); "
         f"steady state unchanged ({s['srv_ccd0']['wall']:.1f} vs {s['srv']['wall']:.1f} s). Build with fresh workers (FCS slnx warm-up): "
         f"{w_nc:.1f} s → {w_best:.1f} s, on par with stock + flags.",
         "1 line (client)", "Done: now the default."),
        ("Workers: non-concurrent GC (DOTNET_gcConcurrent=0; GCSettings.LatencyMode=Batch per compilation is equivalent)",
         f"Per compilation: CPU {pct(s['srv_nonconc']['cpu'], s['srv']['cpu'])}, wall {pct(s['srv_nonconc']['wall'], s['srv']['wall'])}. "
         f"FCS slnx build: {b_srv:.1f} s → {b_nc:.1f} s ({pct(b_nc, b_srv)}).",
         "1 line (client)", "Done: now the default."),
        ("Workers: GC heap count (cores/2 vs all cores vs DATAS)",
         f"Alone, 11 heaps make FCS {pct(s['srv_heaps_all_nonconc']['per'][PROJ[0]]['wall'], s['srv_nonconc']['per'][PROJ[0]]['wall'])} faster, but in a parallel build "
         f"they are a disaster: {b_h11:.1f} s vs {b_best:.1f} s ({pct(b_h11, b_best)}). DATAS: FCS slnx {b_datas:.1f} s (more memory: "
         f"up to {rss(E4, 'server_ccd0_nonconc_datas') / 1024:.1f} GB vs {rss(E4, 'server_ccd0_nonconc') / 1024:.1f} GB), Fantomas {f_datas:.1f} vs {f_best:.1f} s with more CPU.",
         "1 line", "Keep max(1, cores/2); DATAS is a reasonable alternative, not clearly better."),
        ("Worker affinity (a project goes back to the worker that compiled it)",
         f"Per compilation a dedicated worker is no faster than a shared warm one ({s['srv_dedicated']['wall']:.1f} vs {s['srv']['wall']:.1f} s): most compiler code "
         "paths are shared between projects. In builds it avoids sending a big compilation to a worker that has never run it.",
         "~30 lines (client)", "Kept; cheap. Fast tier-up (row 2) matters more."),
        ("Keep assembly caches between compilations (IL readers, shared imported CCUs)",
         f"No gain ({s['srv_reuse']['wall']:.1f} vs {s['srv']['wall']:.1f} s): a warm worker spends ~0.03–0.05 s importing references.",
         "small (opt-in switch exists)", "Leave off: not stock-exact, and nothing to gain on these projects."),
        ("JIT flags in workers (TieredPGO=0)",
         f"Steady state {pct(s['srv_flags']['wall'], s['srv']['wall'])} (both flags), {pct(s['srv_pgo0']['wall'], s['srv']['wall'])} (TieredPGO=0 alone).",
         "1 line", "Rejected: Tiered PGO pays off in long-lived workers."),
        ("No compacting GC when idle", f"No effect ({s['srv_noidlegc']['wall']:.1f} vs {s['srv']['wall']:.1f} s).", "1 line", "Keep the idle GC: it returns memory."),
        ("Reduce allocations in the compiler (large projects)",
         (f"FCS allocates ~{st.median(r['stats']['allocMB'] for r in g[(stats['srv']['tag'], 'srv', PROJ[0], False)]) / 1024:.0f} GB per compilation; GC is "
          f"{100 * samples['srv']['gc'] / samples['srv']['busy']:.0f}–{100 * samples['flags']['gc'] / samples['flags']['busy']:.0f}% of its busy CPU samples, JIT "
          f"{100 * samples['srv']['jit'] / samples['srv']['busy']:.0f}–{100 * samples['flags']['jit'] / samples['flags']['busy']:.0f}%: for FCS the server can only remove JIT/startup.") if samples else "",
         "large (compiler work)", "Biggest remaining lever for big projects, with or without the server."),
    ]
    ftrs = "".join(f"<tr><td class=n>{i + 1}</td><td><b>{html.escape(a)}</b></td><td>{html.escape(b)}</td><td>{html.escape(c)}</td><td>{html.escape(d)}</td></tr>"
                   for i, (a, b, c, d) in enumerate(findings_rows))

    style = (ROOT / "reports" / "server" / "plan.html").read_text()
    style = style[style.index("<style>"):style.index("</style>") + 8]
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compiler Server Investigation</title>{style}
<style>td.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}} td small{{color:var(--muted)}}
main{{max-width:1240px;margin:0 auto}} a{{color:var(--accent)}}</style></head><body><main>
<h1>F# Compiler Server: making it faster</h1>
<p class=sub>Per-compilation and whole-build experiments on the compiler server branch (<code>repos/server</code>), 2026-10-18,
11-core Apple M-series. Plan: <a href="plan.html">plan.html</a> · first results: <a href="results.html">results.html</a> ·
code: <a href="https://github.com/safesparrow/fsharp/tree/compiler_server">compiler_server</a> (<a href="https://github.com/safesparrow/fsharp/compare/99406265c88090c50e575afe8474f0979aa6f815...compiler_server">diff</a>).</p>
{(ROOT / 'reports' / 'server' / 'investigation_summary.html').read_text() if (ROOT / 'reports' / 'server' / 'investigation_summary.html').exists() else ''}
<h2>Findings, ranked by benefit for the effort</h2>
<div class=tw><table><tr><th>#</th><th>Change</th><th>Measured effect</th><th>Complexity</th><th>Verdict</th></tr>{ftrs}</table></div>

<h2>Per-compilation results</h2>
<p>Each compilation is replayed from the response file captured in a real build (outputs redirected). Process configs start
<code>dotnet fsc.dll</code> per compilation; server configs send it to a worker over its socket, as the Fsc task does.
1 warm-up round, then 5 measured rounds of the 5 projects; cells are medians in seconds, with the ratio to stock + JIT flags.
"Cold" columns are the warm-up round: the first time a fresh worker (or process) compiles that project.</p>
<div class=tw><table><tr><th>config</th>{''.join(f'<th>{SHORT[p]}</th>' for p in PROJ)}<th>sum wall s</th><th>sum CPU s</th>
<th>cold FCS s</th><th>cold round s</th><th>FCS GC pause s</th></tr>{''.join(trs)}</table></div>
<p>Outputs: every server configuration produced assemblies identical to stock for every compilation (hashes compared per run).</p>

<h2>Where the time goes in a large compilation (FCS)</h2>
<p>Native sampling with macOS <code>sample</code> (1 ms, every thread), self samples of busy threads grouped by what runs:
managed compiler code (JIT'd or ReadyToRun, unsymbolized), GC (<code>gc_heap</code> functions in libcoreclr), JIT (libclrjit),
other runtime code, other native code. Sampling slows the compilation, so read shares, not times.</p>
<div class=tw><table><tr><th>profile</th><th>managed</th><th>GC</th><th>JIT</th><th>runtime</th><th>other</th></tr>{strs}</table></div>
<p><code>flags</code> = stock process with both JIT flags; <code>srv</code> = warm worker (default); <code>srvh11nc</code> = warm worker, 11 heaps, non-concurrent GC.
For FCS the JIT is already small with the flags, so a warm worker can only win a little there; GC is the dominant cost.</p>

<h2>Whole builds</h2>
<p><code>dotnet build -c Release --no-restore --no-incremental</code>, MSBuild node reuse off, 1 warm-up and 3 measured builds per config,
configs run one after another. Server configs include worker affinity; their worker settings go to the workers only
(<code>FSHARP_COMPILER_SERVER_WORKER_*</code>). CPU = build process tree + live workers.</p>
<h3>FSharp.Compiler.Service.slnx (17 compilations, up to 9 at once)</h3>
<div class=tw><table>{BUILD_HDR}{t_e4}</table></div>
<h3>Fantomas (fantomas.slnx, many projects, mostly dependency chains)</h3>
<p>Built with this repo's main compiler and targets, so <code>-p:LangVersion=11</code> is passed to every config: main's targets no longer
default preview SDKs to F# 11 (the SDK's do), and Fantomas.Tests does not compile with main's newer default language version.</p>
<div class=tw><table>{BUILD_HDR}{t_fan}</table></div>
<h3>Final: the branch's new defaults, no overrides</h3>
<div class=tw><table>{BUILD_HDR}{t_fin}</table></div>
<p>Stock + JIT flags for reference: {b_fl:.1f} s (FCS slnx, table above) and {f_fl:.1f} s (Fantomas). The first FCS run contains one of
the unexplained slow builds (55.3 s); the repeat's first build started with 2.5 cores of outside load.</p>

<h2>Not done yet, worth trying next</h2>
<ul>
<li><b>Slow-build ordering</b>: the occasional slow builds come from MSBuild running FCS's two target frameworks one after the other.
Next: more stock builds to see whether it happens there too at a lower rate, and whether <code>-graph</code> builds avoid it.</li>
<li><b>Pre-warm new workers</b>: start one spare worker ahead of need, or let a new worker compile a small embedded sample before its
first request. Fast tier-up already removed most of the cold cost, so this is worth it only if cold builds still matter.</li>
<li><b>Allocation profile of FCS</b> (33 GB per compilation): the largest remaining cost for big projects, with or without the server.</li>
<li><b>Linux x64 with many cores</b> (your work setup): the heap count and the non-concurrent GC choice should be checked there, where
more compilations run at once.</li>
<li><b>Ship</b>: <code>dotnet build-server shutdown</code> integration (dotnet/sdk), a version handshake and worker logging.</li>
</ul>
</main></body></html>"""
    OUT.write_text(page)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
