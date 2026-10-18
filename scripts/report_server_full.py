#!/usr/bin/env python3
"""Compiler server report in the style of the JIT reports: reports/server/report.html + data/report-data.js.

Charts and styles are shared with scripts/report_template.html (the @shared region and its <style>), like report_pr.py.
Inputs:
  results/server_fsc/runs.jsonl          per-compilation runs (scripts/fscbench.py); tags e1, e2, e3, e5
  results/server_fsc/samples_FCS.json    native sampling breakdown of one FCS compilation (scripts/sample_fsc.py)
  results/server_e4, server_fantomas, server_final*, server_stall*   whole builds (scripts/serverbench.py)
Changes are median(value)/median(reference) − 1 with a 95% percentile bootstrap CI (both groups resampled); a change
counts only when its CI excludes 0. Sums over projects bootstrap every project.
"""
import collections, json, os, random, statistics as st, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "server"
B = int(os.environ.get("REPORT_B", "2000"))
rng = random.Random(20261018)

PROJ = ["FSharp.Compiler.Service__net11.0", "FSharp.Core__netstandard2.0", "FSharp.Compiler.ComponentTests__net11.0",
        "FSharp.Test.Utilities__net11.0", "FSharp.Build__netstandard2.0"]
SHORT = {"FSharp.Compiler.Service__net11.0": "FCS · net11.0", "FSharp.Core__netstandard2.0": "FSharp.Core · netstandard2.0",
         "FSharp.Compiler.ComponentTests__net11.0": "ComponentTests · net11.0", "FSharp.Test.Utilities__net11.0": "Test.Utilities · net11.0",
         "FSharp.Build__netstandard2.0": "FSharp.Build · netstandard2.0"}
PHASES = ["Import mscorlib+FSharp.Core", "Parse inputs", "Import non-system references", "Typecheck", "Optimizations",
          "TAST -> IL", "Write .NET Binary"]
VARIANTS = [  # (config, tag, label)
    ("srv_ccd0", "e5", "CallCountingDelayMs=0 (PGO kept)"),
    ("srv_nonconc", "e2", "non-concurrent GC"),
    ("srv_batch", "e3", "GC latency Batch while compiling"),
    ("srv_ccd0_nonconc", "e5", "CallCountingDelayMs=0 + non-concurrent GC (new default)"),
    ("srv_datas", "e2", "DATAS instead of 5 heaps"),
    ("srv_heaps_all", "e2", "11 heaps"),
    ("srv_heaps_all_nonconc", "e3", "11 heaps + non-concurrent GC"),
    ("srv_reuse", "e2", "keep assembly caches between compilations"),
    ("srv_noidlegc", "e3", "no compacting GC when idle"),
    ("srv_dedicated", "e1", "one worker per project (affinity)"),
    ("srv_pgo0", "e5", "TieredPGO=0"),
    ("srv_flags", "e2", "TieredPGO=0 + CallCountingDelayMs=0"),
]


def cmp(ref, val):
    """median(val)/median(ref) - 1 with a bootstrap CI."""
    d = st.median(val) / st.median(ref) - 1
    bs = sorted(st.median(rng.choices(val, k=len(val))) / st.median(rng.choices(ref, k=len(ref))) - 1 for _ in range(B))
    lo, hi = bs[int(0.025 * B)], bs[int(0.975 * B) - 1]
    return dict(d=d, lo=lo, hi=hi, sig=(lo > 0 or hi < 0))


def cmp_sum(refs, vals):
    """Sum over projects of medians; refs/vals: lists of sample lists, one per project."""
    d = sum(st.median(v) for v in vals) / sum(st.median(r) for r in refs) - 1
    bs = []
    for _ in range(B):
        sv = sum(st.median(rng.choices(v, k=len(v))) for v in vals)
        sr = sum(st.median(rng.choices(r, k=len(r))) for r in refs)
        bs.append(sv / sr - 1)
    bs.sort()
    lo, hi = bs[int(0.025 * B)], bs[int(0.975 * B) - 1]
    return dict(d=d, lo=lo, hi=hi, sig=(lo > 0 or hi < 0))


def main():
    rows = [json.loads(l) for l in open(ROOT / "results" / "server_fsc" / "runs.jsonl")]
    g = collections.defaultdict(list)
    for r in rows:
        g[(r["tag"], r["config"], r["project"], r["warm"])].append(r)

    def samples(tag, cfg, p, key):
        return [r[key] for r in g[(tag, cfg, p, False)]]

    def med(tag, cfg, p, key):
        return st.median(samples(tag, cfg, p, key))

    REF = ("e2", "flags")
    SRV = ("e5", "srv_ccd0_nonconc")  # the branch's new default
    # 1. per-compilation: tuned worker and default worker vs flags
    percomp = {}
    for p in PROJ:
        percomp[p] = {k: {"best": cmp(samples(*REF, p, k), samples(*SRV, p, k)),
                          "default": cmp(samples(*REF, p, k), samples("e3", "srv", p, k)),
                          "stock": cmp(samples(*REF, p, k), samples("e1", "stock", p, k))} for k in ("wall_s", "cpu_s")}
    totals = {k: {name: cmp_sum([samples(*REF, p, k) for p in PROJ], [samples(t, c, p, k) for p in PROJ])
                  for name, (t, c) in {"best": SRV, "default": ("e3", "srv"), "stock": ("e1", "stock")}.items()}
              for k in ("wall_s", "cpu_s")}
    abs_ = {name: {p: {k: med(t, c, p, k) for k in ("wall_s", "cpu_s")} for p in PROJ}
            for name, (t, c) in {"flags": REF, "stock": ("e1", "stock"), "default": ("e3", "srv"), "best": SRV}.items()}

    # 2. worker variants vs the default worker of the same batch (e5 has none: e3's)
    def srv_ref(tag):
        return "e3" if tag == "e5" else tag

    variants = []
    for cfg, tag, label in VARIANTS:
        rt = srv_ref(tag)
        v = {k: cmp_sum([samples(rt, "srv", p, k) for p in PROJ], [samples(tag, cfg, p, k) for p in PROJ]) for k in ("wall_s", "cpu_s")}
        fcs = cmp(samples(rt, "srv", PROJ[0], "wall_s"), samples(tag, cfg, PROJ[0], "wall_s"))
        cold = sum(g[(tag, cfg, p, True)][0]["wall_s"] for p in PROJ)
        cold_ref = sum(g[(rt, "srv", p, True)][0]["wall_s"] for p in PROJ)
        variants.append(dict(cfg=cfg, label=label, wall=v["wall_s"], cpu=v["cpu_s"], fcs=fcs,
                             sum_wall=sum(med(tag, cfg, p, "wall_s") for p in PROJ),
                             sum_cpu=sum(med(tag, cfg, p, "cpu_s") for p in PROJ), cold=cold, cold_ref=cold_ref,
                             cold_fcs=g[(tag, cfg, PROJ[0], True)][0]["wall_s"]))

    # 3. cold compilations (warm-up round)
    cold_cfgs = [("stock process", "e1", "stock"), ("stock + JIT flags", "e2", "flags"), ("worker, default", "e3", "srv"),
                 ("worker, CCD=0", "e5", "srv_ccd0"), ("worker, new default", "e5", "srv_ccd0_nonconc"),
                 ("worker, JIT flags", "e2", "srv_flags")]
    cold = [[lab, [g[(t, c, p, True)][0]["wall_s"] for p in PROJ]] for lab, t, c in cold_cfgs]
    warm = [[lab, [med(t, c, p, "wall_s") for p in PROJ]] for lab, t, c in cold_cfgs]

    # 4. phases (sum over projects of per-project medians)
    def phase_sum(t, c):
        return [sum(st.median(r["phases"].get(ph, 0.0) for r in g[(t, c, p, False)]) for p in PROJ) for ph in PHASES]
    phases = [["stock process", phase_sum("e1", "stock")], ["stock + JIT flags", phase_sum("e2", "flags")],
              ["warm worker, default", phase_sum("e3", "srv")], ["warm worker, new default", phase_sum("e5", "srv_ccd0_nonconc")]]

    # 5. profile
    smp = json.loads((ROOT / "results" / "server_fsc" / "samples_FCS.json").read_text())
    segs = ["managed", "gc", "jit", "runtime", "other"]
    profile = [[{"flags": "stock + JIT flags", "srv": "warm worker, default", "srvh11nc": "worker, 11 heaps, NC"}[n],
                [100 * v[s] / v["busy"] for s in segs]] for n, v in smp.items()]

    # 6. whole builds
    def bruns(path, cfg):
        return [json.loads(l) for l in open(path) if json.loads(l)["config"] == cfg] if path.exists() else []

    def build_block(path, ref_path, cfgs):
        ref = [r for r in bruns(ref_path, "flags") if not r["warm"]]
        out = []
        for cfg, label, p in cfgs:
            rs = bruns(p or path, cfg)
            meas = [r for r in rs if not r["warm"]]
            if not meas:
                continue
            out.append(dict(cfg=cfg, label=label, warmup=[r["wall_s"] for r in rs if r["warm"]][0],
                            walls=[r["wall_s"] for r in meas], cpus=[r["cpu_s"] for r in meas],
                            wall=cmp([r["wall_s"] for r in ref], [r["wall_s"] for r in meas]) if cfg != "flags" else None,
                            cpu=cmp([r["cpu_s"] for r in ref], [r["cpu_s"] for r in meas]) if cfg != "flags" else None,
                            workers=max(r["workers_alive"] for r in meas), rss=max(r["worker_rss_mb"] for r in meas)))
        return out

    R = ROOT / "results"
    fcs_build = build_block(R / "server_e4" / "runs.jsonl", R / "server_e4" / "runs.jsonl", [
        ("flags", "stock + JIT flags", None), ("server", "server, default workers (affinity on)", None),
        ("server_nonconc", "+ non-concurrent GC", None), ("server_ccd0_nonconc", "+ CallCountingDelayMs=0 (new default)", None),
        ("server_ccd0_nonconc_datas", "new default + DATAS", None), ("server_ccd0_nonconc_heapsall", "new default + 11 heaps", None),
        ("server", "new default, repeat (8 builds)", R / "server_stall2" / "runs.jsonl"),
        ("server", "new default, repeat 2 (10 builds)", R / "server_stall3" / "runs.jsonl")])
    fan_build = build_block(R / "server_fantomas" / "runs.jsonl", R / "server_fantomas" / "runs.jsonl", [
        ("baseline", "stock", None), ("flags", "stock + JIT flags", None), ("server_ccd0_nonconc", "server, new default", None),
        ("server_ccd0_nonconc_datas", "new default + DATAS", None), ("server", "new default, final check", R / "server_final_fantomas" / "runs.jsonl")])

    hw = json.loads((R / "hardware.json").read_text())
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT / "repos" / "server", capture_output=True, text=True).stdout.strip()
    fmt = lambda x: f"{100 * x['d']:+.0f}%"
    best_fan = next(b for b in fan_build if b["cfg"] == "server_ccd0_nonconc")
    best_fcs = next(b for b in fcs_build if b["label"].startswith("new default, repeat"))
    tot = totals
    summary = [
        f"**A warm compiler server is faster than stock fsc + JIT flags, per compilation and per build.** Five captured compilations, "
        f"medians: wall {fmt(tot['wall_s']['best'])}, CPU {fmt(tot['cpu_s']['best'])} vs stock + flags (vs stock fsc: wall "
        f"{100 * (sum(abs_['best'][p]['wall_s'] for p in PROJ) / sum(abs_['stock'][p]['wall_s'] for p in PROJ) - 1):+.0f}%).",
        f"**Whole builds:** Fantomas {fmt(best_fan['wall'])} wall / {fmt(best_fan['cpu'])} CPU vs stock + flags; "
        f"FSharp.Compiler.Service.slnx {fmt(best_fcs['wall'])} wall / {fmt(best_fcs['cpu'])} CPU (8 builds).",
        "**Two one-line worker settings made the difference in builds**, both now the branch default: `DOTNET_TC_CallCountingDelayMs=0` "
        "(Tiered PGO kept) so a new worker tiers up fast, and non-concurrent GC.",
        "**Small and medium projects gain most** (0.2–0.6× of the flags' time); FCS gains least: its JIT is only 2–3% of CPU, GC is 31–39%.",
        "**Outputs are identical to stock** for every compilation and build (assemblies, reference assemblies, embedded PDBs).",
    ]
    findings = [
        ["Compile in long-lived workers (the server)", f"Per compilation {fmt(tot['wall_s']['best'])} wall, {fmt(tot['cpu_s']['best'])} CPU vs stock + flags; builds {fmt(best_fan['wall'])} (Fantomas), {fmt(best_fcs['wall'])} (FCS slnx)", "built", "done"],
        ["Workers: CallCountingDelayMs=0, Tiered PGO kept", f"cold first FCS compilation {variants[0]['cold_fcs']:.1f} s vs {g[('e3', 'srv', PROJ[0], True)][0]['wall_s']:.1f} s; steady state {fmt(variants[0]['wall'])}", "1 line", "done (default)"],
        ["Workers: non-concurrent GC (or Batch latency per compilation)", f"per compilation CPU {fmt(variants[1]['cpu'])}, wall {fmt(variants[1]['wall'])}; FCS slnx build −12%", "1 line", "done (default)"],
        ["Workers: GC heap count", f"11 heaps: FCS alone {fmt(variants[5]['fcs'])}, but parallel builds +47% vs 5 heaps; DATAS ≈ 5 heaps with more memory on FCS slnx", "1 line", "keep cores/2"],
        ["Worker affinity", f"per compilation {fmt(variants[9]['wall'])} vs a shared warm worker; helps builds avoid cold workers", "~30 lines", "kept"],
        ["Keep assembly caches between compilations", f"{fmt(variants[7]['wall'])} (imports take ~0.05 s in a warm worker)", "small", "leave off"],
        ["TieredPGO=0 in workers", f"steady state {fmt(variants[10]['wall'])}", "1 line", "rejected"],
        ["No idle compacting GC", f"{fmt(variants[8]['wall'])}", "1 line", "rejected"],
        ["Reduce compiler allocations", "FCS allocates ~33 GB per compilation; GC 31–39% of its CPU", "large", "biggest remaining lever"],
    ]
    data = dict(
        meta=dict(title="F# compiler server", commit=commit, hardware=[[k, str(v)] for k, v in hw.items() if isinstance(v, (str, int, float))][:8],
                  configs=[{"id": "flags"}], ref_config="flags", B=B),
        metrics=[{"key": "wall_s", "digits": 2, "unit": "s"}, {"key": "cpu_s", "digits": 1, "unit": "s"}],
        projects=PROJ, short=SHORT, percomp=percomp, totals=totals, abs=abs_, variants=variants, cold=cold, warm=warm,
        phases=phases, phase_names=PHASES, profile=profile, profile_segs=["managed code", "GC", "JIT", "other runtime", "other native"],
        fcs_build=fcs_build, fan_build=fan_build, summary=summary, findings=findings)
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    (OUT / "data" / "report-data.js").write_text(f"window.REPORT_DATA = {json.dumps(data, separators=(',', ':'))};\n")
    main_t = (ROOT / "scripts" / "report_template.html").read_text()
    page = (ROOT / "scripts" / "report_server_template.html").read_text()
    page = page.replace("/*@STYLE*/", main_t[main_t.index("<style>") + 7:main_t.index("</style>")].strip("\n"))
    i = main_t.index("\n", main_t.index("/* @shared-begin")) + 1
    page = page.replace("//@SHARED_JS", main_t[i:main_t.index("/* @shared-end */", i)])
    (OUT / "report.html").write_text(page)
    # Self-contained copy (data inlined) for sharing as a single file
    js = (OUT / "data" / "report-data.js").read_text()
    (OUT / "report_standalone.html").write_text(
        page.replace('<script src="data/report-data.js"></script>', "<script>\n" + js + "</script>")
            .replace('data: <a href="data/report-data.js">data/report-data.js</a>', "data inlined"))
    print(f"wrote {OUT / 'report.html'}")


if __name__ == "__main__":
    main()
