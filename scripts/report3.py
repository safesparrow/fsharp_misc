#!/usr/bin/env python3
"""Phase 3 report: runtime knobs on top of base (both JIT flags + parallel compilation).

Inputs:
  results/phase3/runs.jsonl          fsc runs (bench.py, configs/phase3.json, stats hook layouts/hook2)
  results/phase3_build/runs.jsonl    whole `dotnet build` of Fantomas (buildbench3.py)
  reports/phase3/findings.md         hand-written interpretation
Outputs (reports/phase3/):
  data/summary.json, data/report-data.js, report.html (same styles, chart helpers and metric grid as
  reports/report.html: copied from the shared regions of scripts/report_template.html), report.md, img/*.png

Statistics are the same as report.py: medians, change vs base with a 95% percentile bootstrap CI.
"""
import argparse, json, random, re, statistics, time
from collections import defaultdict
from pathlib import Path

import report as R
import report_md as RM

ROOT = Path(__file__).resolve().parent.parent
REF = "base"
BUILD = "Fantomas build"
BUILD_FLAG_RULE = "host busy >= 1.0 core right before the build, or any swapout (build CPU is system-wide, so foreign CPU is not separable)"
TARGET_LABELS = {"FSharp.Compiler.Service__net11.0": "FCS net11.0", "FSharp.Core__netstandard2.0": "FSharp.Core ns2.0",
                 "FSharp.Build__netstandard2.0": "FSharp.Build ns2.0", BUILD: "Fantomas build"}
VERIFY_COLUMNS = [("ServerGC", "Server GC"), ("ConcurrentGC", "Concurrent GC"), ("HeapCount", "GC heaps"),
                  ("GCDynamicAdaptationMode", "DATAS"), ("gen0size", "gen0size env"), ("min_workers", "TP min workers"),
                  ("TieredPGO", "TieredPGO"), ("CallCountingDelayMs", "Call-counting delay"), ("mcj", "MultiCoreJit hook"),
                  ("r2r_images", "Composite R2R")]


def build_flagged(r):
    h = r["host"]
    return bool(h["pre_busy_cores"] >= 1.0 or h["swapouts"] > 0)


def verify_row(cfg, r):
    rt = r.get("rt") or {}
    g, s = rt.get("gc_config", {}), rt.get("switches", {})
    env = cfg.get("env", {})
    pgo = s.get("DOTNET_TieredPGO") or s.get("System.Runtime.TieredPGO")
    ccd = s.get("DOTNET_TC_CallCountingDelayMs") or s.get("System.Runtime.TieredCompilation.CallCountingDelayMs")
    src = lambda e, rc: ("env " if s.get(e) else "runtimeconfig " if s.get(rc) else "")
    return {
        "ServerGC": g.get("ServerGC"), "ConcurrentGC": g.get("ConcurrentGC"), "HeapCount": g.get("HeapCount"),
        "GCDynamicAdaptationMode": {"1": "on", "0": "off"}.get(str(g.get("GCDynamicAdaptationMode")), g.get("GCDynamicAdaptationMode")),
        "gen0size": s.get("DOTNET_GCgen0size") or "–", "min_workers": s.get("threadpool_min_workers"),
        "TieredPGO": f"{src('DOTNET_TieredPGO', 'System.Runtime.TieredPGO')}{pgo}" if pgo else "default (on)",
        "CallCountingDelayMs": f"{src('DOTNET_TC_CallCountingDelayMs', 'System.Runtime.TieredCompilation.CallCountingDelayMs')}{ccd}" if ccd else "default (100 ms)",
        "mcj": "yes" if "McjHook" in env.get("DOTNET_STARTUP_HOOKS", "") else "–",
        "r2r_images": "yes" if "composite" in cfg["layout"] else "–",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "reports" / "phase3"))
    a = ap.parse_args()
    out = Path(a.out); (out / "data").mkdir(parents=True, exist_ok=True); (out / "img").mkdir(parents=True, exist_ok=True)
    rng = random.Random(4321)
    cfgs = json.loads((ROOT / "configs" / "phase3.json").read_text())
    ids = [c["id"] for c in cfgs]

    runs, metas = R.load_runs(ROOT / "results" / "phase3")
    bruns, bmetas = R.load_runs(ROOT / "results" / "phase3_build")
    present = {r["config"] for r in runs} | {r["config"] for r in bruns}
    cfgs = [c for c in cfgs if c["id"] in present]; ids = [c["id"] for c in cfgs]

    g, fl = R.group(runs, lambda r: r["config"])
    for r in bruns:  # whole builds: wall and system-wide CPU only
        g[(BUILD, r["config"])]["wall_s"].append(r["wall_s"]); g[(BUILD, r["config"])]["cpu_s"].append(r["cpu_s"])
    targets = [t for t in TARGET_LABELS if any((t, c) in g for c in ids)]
    stats = R.stats_block(g, targets, ids)
    cmp, _ = R.cmp_block(rng, g, targets, REF, ids)

    quality = []
    for t in targets:
        if t == BUILD:
            n, k = len(bruns), sum(build_flagged(r) for r in bruns)
        else:
            rs = [r for r in runs if r["project"] == t]; n, k = len(rs), sum(R.is_flagged(r) for r in rs)
        quality.append(dict(target=t, n=n, flagged=k))
    first = {}
    for r in runs:
        first.setdefault(r["config"], r)
    verify = {c["id"]: verify_row(c, first[c["id"]]) for c in cfgs if c["id"] in first}

    hw = json.loads((ROOT / "results" / "hardware.json").read_text())
    lay = json.loads((ROOT / "layouts" / "baseline" / "layout.json").read_text())
    rc_changes = {}
    for c in cfgs:
        lj = ROOT / c["layout"] / "layout.json"
        m = json.loads(lj.read_text()) if lj.exists() else {}
        rc_changes[c["id"]] = m.get("configProperties_set") or ({"composite image": "fsc.r2r.dll"} if m.get("composite") else {})
    ns = sorted({x["wall_s"]["n"] for t in stats.values() for x in t.values() if "wall_s" in x})
    runs_label = str(ns[0]) if len(ns) == 1 else f"{ns[0]}–{ns[-1]}"
    fantomas = bmetas[-1]["commit"][:9] if bmetas else "?"
    meta = dict(
        title="fsc runtime knobs on top of the JIT flags",
        generated=time.strftime("%Y-%m-%d %H:%M %Z"), harness_commit=R.git(ROOT, "rev-parse", "--short", "HEAD"),
        hardware=hw.get("hardware", []),
        software=hw.get("software", []) + [
            ["dotnet/fsharp commit", R.git(ROOT / "repos" / "baseline", "rev-parse", "HEAD")],
            ["Runtime", f"Microsoft.NETCore.App {lay['runtime']}"],
            ["Fantomas commit", fantomas],
        ],
        method=[
            "Reference **base**: fsc from `main`, laid out like the SDK ships it (per-assembly ReadyToRun, Server GC), with `DOTNET_TieredPGO=0` and `DOTNET_TC_CallCountingDelayMs=0`, and parallel compilation on (the default).",
            "Each other config changes one thing on top of base (see the table below). `ccd10` sets both JIT settings in `fsc.runtimeconfig.json` instead of env vars (equivalent; it avoids the hex parsing of env values).",
            "fsc-level: the captured `FSharp.Compiler.Service.slnx` compilations, replayed in isolation: FCS net11.0, FSharp.Core netstandard2.0 (its first target framework) and FSharp.Build netstandard2.0 (small).",
            f"Whole build: `dotnet build fantomas.slnx -c Release --no-incremental` on SDK 11 RC1, with each config's fsc (`DotnetFscCompilerPath`) and settings applied to fsc processes only, through a wrapper. MSBuild keeps its defaults.",
            f"Runs per cell: {runs_label}, after 1 warm-up. Configs are interleaved in shuffled order every round. The warm-up also records the MultiCoreJit profile that the measured `mcj` runs use.",
            "Host monitor and quiet-wait before every run, as in phases 1 and 2; disturbed runs are flagged, not dropped.",
        ],
        configs=[dict(id=c["id"], desc=c["desc"], env=c.get("env", {}), layout=c["layout"], rc_changes=rc_changes[c["id"]]) for c in cfgs],
        ref_config=REF, flag_rule=R.FLAG_RULE, build_flag_rule=BUILD_FLAG_RULE,
    )
    metrics = [dict(key=k, label=l, unit=u, group=gr, digits=d) for k, l, u, gr, d in R.METRICS]
    fp = out / "findings.md"
    data = dict(meta=meta, metrics=metrics, targets=targets, target_labels=TARGET_LABELS, configs=ids, stats=stats, cmp=cmp,
                runs_label=runs_label, quality=quality, verify=verify, verify_columns=VERIFY_COLUMNS,
                findings_md=fp.read_text() if fp.exists() else "")
    (out / "data" / "summary.json").write_text(json.dumps(data, indent=1))
    (out / "data" / "report-data.js").write_text(f"window.REPORT_DATA = {json.dumps(data, separators=(',', ':'))};\n")
    write_html(out)
    write_md(data, out)
    print(f"wrote {out}/report.html, report.md, data/, img/")


def region(s, begin, end):
    i = s.index(begin); i = s.index("\n", i) + 1 if begin.startswith("/*") else i + len(begin)
    return s[i:s.index(end, i)]


def write_html(out):
    main = (ROOT / "scripts" / "report_template.html").read_text()
    page = (ROOT / "scripts" / "report3_template.html").read_text()
    style = main[main.index("<style>") + 7:main.index("</style>")].strip("\n")
    page = page.replace("/*@STYLE*/", style)
    page = page.replace("//@SHARED_JS", region(main, "/* @shared-begin", "/* @shared-end */"))
    page = page.replace("//@GRID_JS", region(main, "/* @grid-begin", "/* @grid-end */"))
    page = page.replace("<!--@GRID_HTML-->", region(main, "<!-- @grid-html-begin -->", "<!-- @grid-html-end -->"))
    (out / "report.html").write_text(page)


def write_md(data, out):
    meta, targets, ids = data["meta"], data["targets"], data["configs"]
    TL = data["target_labels"]
    others = [c for c in ids if c != REF]
    colors = {TL[t]: RM.SLOTS[i % len(RM.SLOTS)] for i, t in enumerate(targets)}
    chip = dict(meta["hardware"]).get("Chip", "")
    sub = f"{chip} · base = TieredPGO=0 + CallCountingDelayMs=0, parallel compilation on · median, 95% bootstrap CI"
    imgs = []
    for k in ("wall_s", "cpu_s"):
        lab = next(m["label"] for m in data["metrics"] if m["key"] == k)
        rows = [(c, {TL[t]: data["cmp"].get(t, {}).get(c, {}).get(k) for t in targets}) for c in others]
        rows = [(c, {s: x for s, x in d.items() if x}) for c, d in rows]
        series = [TL[t] for t in targets if any(TL[t] in d for _, d in rows)]
        name = f"phase3_{k}.png"
        RM.forest_png(out / "img" / name, rows, series, colors, f"{lab}: change vs base, per config", sub,
                      "median(config) / median(base) − 1 (negative = lower)")
        imgs.append((lab, name))
    L = [f"# {meta['title']}", "",
         f"_Generated {meta['generated']} by `scripts/report3.py` (harness commit `{meta['harness_commit']}`). "
         "Interactive version: `reports/phase3/report.html`. All numbers: `reports/phase3/data/summary.json`._", ""]
    if data["findings_md"]:
        L += [data["findings_md"].strip(), ""]
    L += ["## Setup", "", "Hardware:", ""] + [f"- **{k}**: {v}" for k, v in meta["hardware"]]
    L += ["", "Software:", ""] + [f"- **{k}**: {v}" for k, v in meta["software"]]
    L += ["", "Method:", ""] + [f"{i}. {m}" for i, m in enumerate(meta["method"], 1)]
    L += ["", "| Config | What it is |", "|---|---|"] + [f"| `{c['id']}` | {c['desc']} |" for c in meta["configs"]]
    L += ["", "## Results", ""]
    for lab, name in imgs:
        L += [f"![{lab}: change vs base](img/{name})", ""]
    for k in ("wall_s", "cpu_s", "gc_pause_ms", "jit_time_ms"):
        m = next(x for x in data["metrics"] if x["key"] == k)
        ts = [t for t in targets if data["stats"].get(t, {}).get(REF, {}).get(k)]
        if not ts:
            continue
        unit = f" ({m['unit']})" if m["unit"] else ""
        L += [f"**{m['label']}**: change vs base (95% CI); bold = CI excludes 0", "",
              "| Config | " + " | ".join(TL[t] for t in ts) + " |", "|---|" + "---|" * len(ts),
              f"| `base` median{unit} | " + " | ".join(f"{data['stats'][t][REF][k]['median']:.{m['digits']}f}" for t in ts) + " |"]
        for c in others:
            L.append(f"| `{c}` | " + " | ".join(RM.pct(data["cmp"].get(t, {}).get(c, {}).get(k)) if data["cmp"].get(t, {}).get(c, {}).get(k) else "–" for t in ts) + " |")
        L.append("")
    L += ["## Did each setting take effect?", "", "Read inside each fsc process at exit by the stats hook (first measured run of each config).", "",
          "| Config | " + " | ".join(h for _, h in VERIFY_COLUMNS) + " |", "|---|" + "---|" * len(VERIFY_COLUMNS)]
    for c in ids:
        v = data["verify"].get(c, {})
        L.append(f"| `{c}` | " + " | ".join(str(v.get(k, "–")) for k, _ in VERIFY_COLUMNS) + " |")
    L += ["", "## Measurement quality", ""] + [f"- **{TL[q['target']]}**: {q['flagged']} of {q['n']} runs flagged." for q in data["quality"]]
    L += [f"- Flag rule, fsc runs: {meta['flag_rule']}.", f"- Flag rule, whole builds: {meta['build_flag_rule']}.", "",
          "## Reproduce", "", "```", "scripts/run_phase3.sh all      # prep (hooks, layouts), fsc runs, Fantomas builds",
          ".venv/bin/python scripts/report3.py", "```", ""]
    (out / "report.md").write_text("\n".join(L))


if __name__ == "__main__":
    main()
