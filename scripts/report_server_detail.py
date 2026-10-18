#!/usr/bin/env python3
"""Detailed, interactive compiler server report: reports/server/detail.html (+ detail_standalone.html).

The page is scripts/report_template.html (the detailed JIT report: metric grid, results explorer with metric,
config and compilation selectors, forest chart, phases, every-run strips, numbers table) with a few targeted text
replacements: a "compared with" reference selector instead of the fixed baseline, server-specific texts, and no
phase 2 / SDK / trace sections. The data is the per-compilation experiments of results/server_fsc/runs.jsonl.

Changes are median(config)/median(reference) - 1 with a 95% percentile bootstrap CI; totals sum per-project medians.
"""
import collections, json, os, random, statistics as st, subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "server"
B = int(os.environ.get("REPORT_B", "1000"))
rng = random.Random(20261018)

PROJ = ["FSharp.Compiler.Service__net11.0", "FSharp.Core__netstandard2.0", "FSharp.Compiler.ComponentTests__net11.0",
        "FSharp.Test.Utilities__net11.0", "FSharp.Build__netstandard2.0"]
PHASES = ["Import mscorlib+FSharp.Core", "Parse inputs", "Import non-system references", "Typecheck", "Optimizations",
          "TAST -> IL", "Write .NET Binary"]
# id, tag, description, environment
CONFIGS = [
    ("stock", "e1", "stock fsc process per compilation", {}),
    ("flags", "e2", "stock fsc process + JIT flags", {"DOTNET_TieredPGO": "0", "DOTNET_TC_CallCountingDelayMs": "0"}),
    ("srv", "e3", "warm worker, first version (Server GC, 5 heaps)", {}),
    ("srv_new_default", "e5", "warm worker, new default: CallCountingDelayMs=0 + non-concurrent GC", {"DOTNET_TC_CallCountingDelayMs": "0", "DOTNET_gcConcurrent": "0"}),
    ("srv_ccd0", "e5", "worker: CallCountingDelayMs=0 (PGO kept)", {"DOTNET_TC_CallCountingDelayMs": "0"}),
    ("srv_nonconc", "e2", "worker: non-concurrent GC", {"DOTNET_gcConcurrent": "0"}),
    ("srv_batch", "e3", "worker: GC latency Batch while compiling", {"FSHARP_COMPILER_SERVER_GC_LATENCY": "batch"}),
    ("srv_datas", "e2", "worker: DATAS (no fixed heap count)", {"DOTNET_GCHeapCount": "(unset)"}),
    ("srv_heaps_all", "e2", "worker: 11 GC heaps", {"DOTNET_GCHeapCount": "b"}),
    ("srv_heaps_all_nonconc", "e3", "worker: 11 heaps + non-concurrent GC", {"DOTNET_GCHeapCount": "b", "DOTNET_gcConcurrent": "0"}),
    ("srv_heaps_all_batch", "e3", "worker: 11 heaps + Batch latency", {"DOTNET_GCHeapCount": "b", "FSHARP_COMPILER_SERVER_GC_LATENCY": "batch"}),
    ("srv_reuse", "e2", "worker: keep assembly caches between compilations", {"FSHARP_COMPILER_SERVER_REUSE_CACHES": "1"}),
    ("srv_noidlegc", "e3", "worker: no compacting GC when idle", {"FSHARP_COMPILER_SERVER_NO_IDLE_GC": "1"}),
    ("srv_dedicated", "e1", "one worker per project (perfect affinity)", {}),
    ("srv_pgo0", "e5", "worker: TieredPGO=0", {"DOTNET_TieredPGO": "0"}),
    ("srv_flags", "e2", "worker: TieredPGO=0 + CallCountingDelayMs=0", {"DOTNET_TieredPGO": "0", "DOTNET_TC_CallCountingDelayMs": "0"}),
]
SOURCE = {"srv_new_default": "srv_ccd0_nonconc"}  # config id in runs.jsonl
REFS = ["stock", "flags", "srv"]
DEFAULT_CFGS = ["stock", "flags", "srv", "srv_new_default"]
METRICS = [  # key, label, unit, group, digits
    ("wall_s", "Wall time", "s", "Time", 2), ("cpu_s", "CPU time", "s", "Time", 2),
    ("gc_pause_ms", "GC pause time (workers only)", "ms", "GC", 0), ("alloc_mb", "Allocated (workers only)", "MB", "GC", 0),
    ("gc0", "Gen0 GCs", "", "GC", 0), ("gc1", "Gen1 GCs", "", "GC", 0), ("gc2", "Gen2 GCs", "", "GC", 0),
] + [(f"phase:{p}", f"Phase: {p}", "s", "Compiler phases (--times)", 3) for p in PHASES]
TOTAL_KEYS = ["wall_s", "cpu_s", "gc_pause_ms", "alloc_mb"] + [f"phase:{p}" for p in PHASES]
FLAG_RULE = "other processes used more than 1.5 cores just before the run"


def metric_values(r, worker):
    v = {"wall_s": r["wall_s"], "cpu_s": r["cpu_s"]}
    s = r.get("stats") or {}
    if worker and s:
        v.update(gc_pause_ms=s.get("gcPauseMs"), alloc_mb=s.get("allocMB"), gc0=s.get("gen0"), gc1=s.get("gen1"), gc2=s.get("gen2"))
    elif not worker and r.get("times_last"):
        tl = r["times_last"]
        v.update(gc0=tl["gc0"], gc1=tl["gc1"], gc2=tl["gc2"])
    for p in PHASES:
        v[f"phase:{p}"] = r["phases"].get(p, 0.0)
    return {k: x for k, x in v.items() if x is not None}


def q(xs, f):
    xs = sorted(xs)
    i = (len(xs) - 1) * f
    lo = int(i)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (i - lo)


def summ(xs):
    return dict(n=len(xs), median=st.median(xs), mean=st.mean(xs), sd=st.stdev(xs) if len(xs) > 1 else 0.0, min=min(xs),
                max=max(xs), q1=q(xs, .25), q3=q(xs, .75), values=[round(x, 5) for x in xs])


def boot(ref, val):
    if not ref or not val or st.median(ref) == 0:
        return None
    d = st.median(val) / st.median(ref) - 1
    bs = []
    for _ in range(B):
        mr = st.median(rng.choices(ref, k=len(ref)))
        bs.append(st.median(rng.choices(val, k=len(val))) / mr - 1 if mr else 0.0)
    bs.sort()
    lo, hi = bs[int(0.025 * B)], bs[int(0.975 * B) - 1]
    return dict(d=d, lo=lo, hi=hi, sig=lo > 0 or hi < 0)


def boot_total(refs, vals):
    if any(not r for r in refs) or any(not v for v in vals):
        return None
    R, V = sum(st.median(r) for r in refs), sum(st.median(v) for v in vals)
    if R == 0:
        return None
    bs = []
    for _ in range(B):
        bs.append(sum(st.median(rng.choices(v, k=len(v))) for v in vals) / sum(st.median(rng.choices(r, k=len(r))) for r in refs) - 1)
    bs.sort()
    lo, hi = bs[int(0.025 * B)], bs[int(0.975 * B) - 1]
    return dict(d=V / R - 1, lo=lo, hi=hi, sig=lo > 0 or hi < 0, ref=R, val=V)


def main():
    rows = [json.loads(l) for l in open(ROOT / "results" / "server_fsc" / "runs.jsonl")]
    samples = collections.defaultdict(lambda: collections.defaultdict(list))  # (p, cfg) -> key -> values
    flags = collections.defaultdict(dict)
    for cid, tag, _, _ in CONFIGS:
        src = SOURCE.get(cid, cid)
        worker = src.startswith("srv")
        for p in PROJ:
            rs = [r for r in rows if r["tag"] == tag and r["config"] == src and r["project"] == p and not r["warm"]]
            for r in rs:
                for k, v in metric_values(r, worker).items():
                    samples[(p, cid)][k].append(v)
            flags[p][cid] = dict(n=len(rs), flagged=sum(1 for r in rs if (r.get("pre_busy") or 0) > 1.5))
    cells = [c[0] for c in CONFIGS]
    stats = {p: {c: {k: summ(v) for k, v in samples[(p, c)].items()} for c in cells} for p in PROJ}
    cmp_by_ref, total_by_ref = {}, {}
    for ref in REFS:
        cmp_by_ref[ref] = {p: {c: {k: boot(samples[(p, ref)].get(k), samples[(p, c)].get(k)) for k, _, _, _, _ in METRICS}
                               for c in cells if c != ref} for p in PROJ}
        total_by_ref[ref] = {c: {k: boot_total([samples[(p, ref)].get(k) for p in PROJ], [samples[(p, c)].get(k) for p in PROJ])
                                 for k in TOTAL_KEYS} for c in cells if c != ref}
    hw = json.loads((ROOT / "results" / "hardware.json").read_text())
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT / "repos" / "server", capture_output=True, text=True).stdout.strip()
    harness = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    t = total_by_ref
    fp = lambda x: f"{100 * x['d']:+.1f}% (CI {100 * x['lo']:+.1f}% to {100 * x['hi']:+.1f}%)"
    findings = f"""## Findings
- **A warm compiler server beats stock fsc + JIT flags on every compilation.** Five compilations, sum of medians: wall {fp(t['flags']['srv_new_default']['wall_s'])}, CPU {fp(t['flags']['srv_new_default']['cpu_s'])} with the new worker defaults; vs stock fsc: wall {fp(t['stock']['srv_new_default']['wall_s'])}.
- **Gains are largest for small and medium projects** (FSharp.Build, Test.Utilities, ComponentTests); FCS gains least because its JIT cost is already small with the flags and GC dominates.
- **New worker defaults** (CallCountingDelayMs=0 with Tiered PGO kept, non-concurrent GC) vs the first-version worker: wall {fp(t['srv']['srv_new_default']['wall_s'])}, CPU {fp(t['srv']['srv_new_default']['cpu_s'])}, and a fresh worker's first compilation is much faster (see the investigation notes).
- **Rejected:** TieredPGO=0 in workers (wall {fp(t['srv']['srv_pgo0']['wall_s'])}), keeping assembly caches ({fp(t['srv']['srv_reuse']['wall_s'])}), no idle GC ({fp(t['srv']['srv_noidlegc']['wall_s'])}). 11 heaps help one compilation but hurt parallel builds.
- Use **Compared with** to switch the reference between stock, stock + JIT flags and the first-version worker; GC pause and allocations are measured inside workers only.
- Whole builds and the ranked findings: [report_standalone.html](report_standalone.html); investigation notes: [investigation.html](investigation.html).
"""
    data = dict(
        meta=dict(title="F# compiler server: per-compilation results", generated=time.strftime("%Y-%m-%d %H:%M"),
                  harness_commit=f"{harness} (server {commit})",
                  hardware=hw.get("hardware", []),
                  software=[x for x in hw.get("software", []) if x[0] in ("OS", "Runtime", "crossgen2")] +
                           [["Compiler", "dotnet/fsharp main @ 99406265 (stock) + compiler_server branch, ReadyToRun layouts"],
                            ["Server branch", commit]],
                  configs=[dict(id=c, desc=d, env=e) for c, _, d, e in CONFIGS], ref_config="stock", refs=REFS,
                  default_cfgs=DEFAULT_CFGS, flag_rule=FLAG_RULE, B=B),
        metrics=[dict(key=k, label=l, unit=u, group=g, digits=dg) for k, l, u, g, dg in METRICS],
        projects=PROJ, phase_segments=PHASES, findings_md=findings,
        phase1=dict(cells=cells, stats=stats, cmp_by_ref=cmp_by_ref, total_by_ref=total_by_ref, runs=5, flags=flags),
        phase2=None, sdk_compare=None, traces=None, overhead=None)
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    js = f"window.REPORT_DATA = {json.dumps(data, separators=(',', ':'))};\n"
    (OUT / "data" / "detail-data.js").write_text(js)
    page = build_page((ROOT / "scripts" / "report_template.html").read_text())
    (OUT / "detail.html").write_text(page)
    (OUT / "detail_standalone.html").write_text(page.replace('<script src="data/detail-data.js"></script>', "<script>\n" + js + "</script>")
                                                .replace('data: <a href="data/detail-data.js">data/detail-data.js</a>', "data inlined"))
    print(f"wrote {OUT / 'detail.html'} and detail_standalone.html")


def build_page(t):
    def rep(a, b, count=1):
        nonlocal t
        assert t.count(a) == count, (t.count(a), a[:80])
        t = t.replace(a, b)

    rep('<script src="data/report-data.js"></script>', '<script src="data/detail-data.js"></script>')
    rep('data: <a href="data/summary.json">data/summary.json</a>', 'data: <a href="data/detail-data.js">data/detail-data.js</a>')
    rep('<a href="pr/report.html">Short report (issue/PR)</a><a href="phase3/report.html">Other runtime settings</a>',
        '<a href="report_standalone.html">Summary report</a><a href="investigation.html">Investigation notes</a><a href="plan.html">Design and plan</a>')
    rep('<div class="eyebrow">dotnet/fsharp · performance</div>', '<div class="eyebrow">dotnet/fsharp · performance · compiler server</div>')
    # method
    i, j = t.index('<ol id="method">'), t.index('</ol>', t.index('<ol id="method">'))
    t = t[:i] + '''<ol id="method">
        <li>Lay out the stock compiler (main @ 99406265) and the server branch the way the .NET SDK ships fsc: ReadyToRun per assembly, Server GC runtimeconfig.</li>
        <li>Replay each compilation captured from a real <code>dotnet build FSharp.Compiler.Service.slnx</code>. Process configs run <code>dotnet fsc.dll @rsp</code>; server configs send the same response file to a worker over its socket, exactly as the Fsc task does.</li>
        <li>Per config: 1 warm-up round (a fresh worker compiles each project once), then 5 measured rounds over the 5 projects. Configs run one after another (batches e1–e5 on the same day and machine).</li>
        <li>Record per run: wall and CPU time (process: rusage; worker: its own CPU delta), the compiler's <code>--times</code> phases and, for workers, GC pause, allocations and GC counts measured inside the worker.</li>
        <li>Report medians with a 95% bootstrap CI on every change; a change counts only when its CI excludes zero. Every server config produced assemblies identical to stock.</li>
      ''' + t[j:]
    # explorer: one experiment, plus the reference selector
    rep('<button type="button" id="v1" aria-pressed="true">JIT flags</button>', '<button type="button" id="v1" aria-pressed="true">Per compilation</button>')
    rep('<button type="button" id="v2" aria-pressed="false">Parallel compilation on/off</button>', '<button type="button" id="v2" aria-pressed="false" hidden>–</button>')
    rep('''      <div class="row">
        <label class="lbl" for="metricSel">Metric</label>''', '''      <div class="row">
        <label class="lbl" for="refSel">Compared with</label>
        <select id="refSel"></select>
      </div>
      <div class="row">
        <label class="lbl" for="metricSel">Metric</label>''')
    # reference-aware accessors
    rep('const state = { view: 1, metric: "wall_s", cmp: "par_eff", cfgs: new Set(D.meta.configs.map((c) => c.id)), projects: new Set(D.projects) };',
        'const state = { view: 1, metric: "wall_s", cmp: "par_eff", ref: "flags", cfgs: new Set(D.meta.default_cfgs), projects: new Set(D.projects) };\n'
        'const CMP = () => D.phase1.cmp_by_ref[state.ref], TOT = () => D.phase1.total_by_ref[state.ref];')
    rep('  if (state.view === 1) return ((D.phase1.cmp[p] || {})[c] || {})[k];', '  if (state.view === 1) return ((CMP()[p] || {})[c] || {})[k];')
    rep('  if (state.view === 1) return (D.phase1.total[c] || {})[k];', '  if (state.view === 1) return (TOT()[c] || {})[k];')
    rep('''  if (state.view === 1 || state.cmp === "jit_seq" || state.cmp === "jit_par" || state.cmp === "inter") return cs.filter((c) => c !== "baseline");''',
        '''  if (state.view === 1 || state.cmp === "jit_seq" || state.cmp === "jit_par" || state.cmp === "inter") return cs.filter((c) => c !== state.ref);''')
    rep('  if (state.view === 1) return [`${m}: change vs baseline, per compilation`, "change in median vs baseline (negative = lower)"];',
        '  if (state.view === 1) return [`${m}: change vs ${state.ref}, per compilation`, `change in median vs ${state.ref} (negative = lower)`];')
    rep('''    const a = ((D.phase1.stats[p] || {}).baseline || {})[k], b = ((D.phase1.stats[p] || {})[c] || {})[k];
    return a && b ? `<br>baseline ${fmtV(a.median, k)} → ${fmtV(b.median, k)}` : "";''',
        '''    const a = ((D.phase1.stats[p] || {})[state.ref] || {})[k], b = ((D.phase1.stats[p] || {})[c] || {})[k];
    return a && b ? `<br>${esc(state.ref)} ${fmtV(a.median, k)} → ${fmtV(b.median, k)}` : "";''')
    rep('''    if (state.view === 1) x = key !== "baseline" ? ((D.phase1.cmp[p] || {})[key] || {})[k] : null;''',
        '''    if (state.view === 1) x = key !== state.ref ? ((CMP()[p] || {})[key] || {})[k] : null;''')
    rep('''  const deltaHead = state.view === 1 ? "vs baseline" : "par: vs seq · seq: vs baseline";''',
        '''  const deltaHead = state.view === 1 ? `vs ${state.ref}` : "par: vs seq · seq: vs baseline";''')
    rep('''    delta: (p, c, k) => (c === "baseline" ? null : { x: ((D.phase1.cmp[p] || {})[c] || {})[k], vs: "baseline" }),''',
        '''    delta: (p, c, k) => (c === state.ref ? null : { x: ((CMP()[p] || {})[c] || {})[k], vs: state.ref }),''')
    rep('    name: "JIT flags", cells: D.phase1.cells,', '    name: "Per compilation", cells: D.phase1.cells,')
    rep('''  sel.onchange = () => { state.metric = sel.value; renderAll(); };''', '''  sel.onchange = () => { state.metric = sel.value; renderAll(); };
  const rs = $("refSel");
  rs.innerHTML = D.meta.refs.map((r) => `<option value="${r}">${esc((D.meta.configs.find((c) => c.id === r) || {}).desc || r)}</option>`).join("");
  rs.value = state.ref;
  rs.onchange = () => { state.ref = rs.value; state.cfgs.add(state.ref); renderAll(); };''')
    # grid defaults: configs and metrics that exist here
    rep('  G.cells[G.ds] = G.cells[G.ds] || new Set(S.cells);', '  G.cells[G.ds] = G.cells[G.ds] || new Set(G.ds === 1 ? D.meta.default_cfgs : S.cells);')
    rep('const G = { ds: 1, cells: {}, projects: {}, metrics: new Set(["wall_s", "cpu_s", "gc_pause_ms", "jit_time_ms"]), scale: "cell" };',
        'const G = { ds: 1, cells: {}, projects: {}, metrics: new Set(["wall_s", "cpu_s", "gc_pause_ms", "alloc_mb"]), scale: "cell" };')
    rep('  $("gmDef").onclick = () => { G.metrics = new Set(["wall_s", "cpu_s", "gc_pause_ms", "jit_time_ms"]); renderGrid(); };',
        '  $("gmDef").onclick = () => { G.metrics = new Set(["wall_s", "cpu_s", "gc_pause_ms", "alloc_mb"]); renderGrid(); };')
    # measurement quality: texts that are true for this data set
    rep('''  if (aa) li.push(''', '''  if (false) li.push(''')
    rep('''  li.push(`<li><b>Instrumentation:</b> every timed run loads a startup hook (GC and JIT counters) and passes <code>--times:&lt;csv&gt;</code>, in all configs alike. The cost vs a bare run is in the table below.</li>`);''',
        '''  li.push(`<li><b>Batches:</b> configs ran one after another, not interleaved; the reference configs were re-measured in later batches with the same results (stock + JIT flags: 25.0 s in two batches).</li>`);''')
    rep('const subtitleBase = () => {', 'const subtitleBase0 = () => {')
    rep('''  return `${hw.Chip || ""} · ${String(hw["CPU cores"] || "").split(" (")[0]} · median of ${state.view === 1 ? D.phase1.runs : (D.phase2 || {}).runs} runs, 95% bootstrap CI`;
};''', '''  return `${hw.Chip || ""} · ${String(hw["CPU cores"] || "").split(" (")[0]} · median of ${state.view === 1 ? D.phase1.runs : (D.phase2 || {}).runs} runs, 95% bootstrap CI`;
};
const subtitleBase = () => subtitleBase0() + ` · vs ${state.ref}`;''')
    rep('<div class="fig" id="figTiers"></div>', '')
    rep('<div class="tablebox"><table id="ovTable"></table></div>', '<div class="tablebox" hidden><table id="ovTable"></table></div>')
    rep('${k} of ${n} phase 1 runs', '${k} of ${n} measured runs')
    rep('renderChips(); tiles(); forest(); phases(); tiers(); strips(); numTable();', 'renderChips(); tiles(); forest(); phases(); strips(); numTable();')
    return t


if __name__ == "__main__":
    main()
