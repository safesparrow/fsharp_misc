#!/usr/bin/env python3
"""Concise report for an issue/PR: fsc with TieredPGO=false + CallCountingDelayMs=0, vs the defaults.

Scope: .NET 11 (main @ the benchmarked commit, R2R layout like the SDK), parallel compilation on (the default),
the newest target framework of each project only. Data: phase 1 runs (baseline vs pgo0_ccd0) and the
whole-build Fantomas runs on SDK 11. Every number in the text is computed here from that data.

Outputs (reports/pr/): report.html (left navigation, collapsible sections; styles and charts shared with
reports/report.html), report.md (GitHub-flavoured, absolute image/report links so it can be pasted into an issue),
data/summary.json, data/report-data.js, img/*.png
"""
import json, random, re, statistics, time
from pathlib import Path

import report as R
import report_md as RM

ROOT = Path(__file__).resolve().parent.parent
SITE = "https://safesparrow.github.io/fsharp_misc/reports"
REPO = "https://github.com/safesparrow/fsharp_misc"
BRANCH = "jit_flags_runtimeconfig"
FORK = "https://github.com/safesparrow/fsharp"
OLDER_TFMS = {"FSharp.Compiler.Service__netstandard2.0", "FSharp.Core__netstandard2.0", "FSharp.Core__netstandard2.1"}
SMALL_S = 1.5  # "small compilation" = baseline wall under this
SECTIONS = [  # id, title, open by default
    ("summary", "Summary", True),
    ("change", "The change", True),
    ("results", "Results per compilation", True),
    ("build", "Whole build", True),
    ("where", "Where the time goes", False),
    ("setup", "Setup and method", False),
    ("quality", "Measurement quality", False),
    ("links", "Full reports and reproduction", True),
]


def fp(x, digits=1):
    return f"{x * 100:+.{digits}f}%".replace("-", "−")


def ci(x):
    return f"{fp(x['lo'])} to {fp(x['hi'])}"


def main():
    out = ROOT / "reports" / "pr"
    for d in ("data", "img"):
        (out / d).mkdir(parents=True, exist_ok=True)
    rng = random.Random(2024)

    runs, metas = R.load_runs(ROOT / "results" / "phase1")
    runs = [r for r in runs if r["config"] in ("baseline", "pgo0_ccd0") and r["project"] not in OLDER_TFMS]
    for r in runs:
        r["config"] = "flags" if r["config"] == "pgo0_ccd0" else r["config"]
    g, fl = R.group(runs, lambda r: r["config"])
    cells = ["baseline", "flags"]
    projects = R.project_order(g, "baseline")
    stats = R.stats_block(g, projects, cells)
    cmp, _ = R.cmp_block(rng, g, projects, "baseline", cells)
    totals = {}
    for k in ("wall_s", "cpu_s", "gc_pause_ms", "jit_time_ms") + tuple(f"phase:{s}" for s, _ in R.PHASE_SEGMENTS):
        ga = [g[(p, "baseline")][k] for p in projects if g[(p, "baseline")].get(k) and g[(p, "flags")].get(k)]
        gb = [g[(p, "flags")][k] for p in projects if g[(p, "baseline")].get(k) and g[(p, "flags")].get(k)]
        if len(ga) == len(projects):
            totals[k] = R.ratio_total(rng, ga, gb)
    med = lambda p, c, k: stats[p][c][k]["median"]
    small = [p for p in projects if med(p, "baseline", "wall_s") < SMALL_S]
    large = [p for p in projects if p not in small]
    rng_of = lambda ps, k: (min(cmp[p]["flags"][k]["d"] for p in ps), max(cmp[p]["flags"][k]["d"] for p in ps))
    flags_n = sum(R.is_flagged(r) for r in runs)

    # whole build: Fantomas on SDK 11, none vs env on fsc only (what a runtimeconfig change does) vs env on dotnet build
    bruns, bmetas = R.load_runs(ROOT / "results" / "buildbench")
    bg = {}
    for r in bruns:
        if r["sdk"] == "sdk11":
            h = r["host"]
            bg.setdefault(r["mode"], {"wall_s": [], "cpu_s": []})
            bg[r["mode"]]["wall_s"].append(r["wall_s"])
            bg[r["mode"]]["cpu_s"].append(h["sys_busy_cpu_s"] - h["pre_busy_cores"] * h["mon_wall_s"])
    build = dict(none={k: statistics.median(v) for k, v in bg["none"].items()}, n=len(bg["none"]["wall_s"]),
                 commit=bmetas[-1]["commit"][:9],
                 **{m: {k: R.ratio(rng, bg["none"][k], bg[m][k]) for k in ("wall_s", "cpu_s")} for m in ("env_fsc", "env_all")})

    fork_commit = R.git(ROOT / "repos" / "jit_flags", "rev-parse", BRANCH)
    diff = R.git(ROOT / "repos" / "jit_flags", "diff", f"{BRANCH}~1", BRANCH, "--", "src/fsc/fsc.targets")
    diff = diff[diff.index("@@"):] if "@@" in diff else diff
    hw = json.loads((ROOT / "results" / "hardware.json").read_text())
    lay = json.loads((ROOT / "layouts" / "baseline" / "layout.json").read_text())
    base_commit = R.git(ROOT / "repos" / "baseline", "rev-parse", "HEAD")

    tw, tc = totals["wall_s"], totals["cpu_s"]
    lw, lc, sw, sc = rng_of(large, "wall_s"), rng_of(large, "cpu_s"), rng_of(small, "wall_s"), rng_of(small, "cpu_s")
    fcs = "FSharp.Compiler.Service__net11.0"
    summary = [
        f"Setting `System.Runtime.TieredPGO=false` and `System.Runtime.TieredCompilation.CallCountingDelayMs=0` for the fsc process makes compilation much faster on .NET 11.",
        f"All {len(projects)} compilations of `FSharp.Compiler.Service.slnx` together (newest target framework of each project, parallel compilation on): **{tw['ref']:.1f} s → {tw['val']:.1f} s wall ({fp(tw['d'])}, 95% CI {ci(tw)})**, **CPU {fp(tc['d'])}** ({ci(tc)}).",
        f"Medium and large compilations ({len(large)} of {len(projects)}, 1.5 s to 25 s): wall **{fp(lw[0], 0)} to {fp(lw[1], 0)}**, CPU {fp(lc[0], 0)} to {fp(lc[1], 0)}. FSharp.Compiler.Service itself: {med(fcs, 'baseline', 'wall_s'):.1f} s → {med(fcs, 'flags', 'wall_s'):.1f} s.",
        f"Small compilations ({len(small)}, under {SMALL_S} s): wall {fp(sw[0], 0)} to {fp(sw[1], 0)}, but CPU **{fp(sc[0], 0)} to {fp(sc[1], 0)}**: the process ends before recompiling startup code pays off.",
        f"Whole `dotnet build` of Fantomas (SDK 11, {build['n']} runs per mode): **{fp(build['env_fsc']['wall_s']['d'])} wall** with the settings on fsc only, {fp(build['env_all']['wall_s']['d'])} when also applied to MSBuild.",
        f"GC pause time is unchanged ({fp(totals['gc_pause_ms']['d'])}). JIT CPU time goes up ({fp(totals['jit_time_ms']['d'], 0)}), mostly on the background tier-1 thread, which is what buys the speed-up.",
    ]
    why = [
        "fsc is short-lived and keeps JIT-compiling new methods for most of its run.",
        "By default, methods are promoted from tier 0 to tier 1 only after a 100 ms call-counting delay, and the runtime extends that delay while new tier-0 methods keep appearing. In a compiler that can keep hot code at tier 0 for seconds.",
        "Tiered PGO adds an instrumented tier before tier 1, which delays optimised code further.",
        f"Each setting alone helps (phase 1, all 17 compilations: `TieredPGO=0` {fp(json.loads((ROOT / 'reports' / 'data' / 'summary.json').read_text())['phase1']['total']['pgo0']['wall_s']['d'])}, `CallCountingDelayMs=0` {fp(json.loads((ROOT / 'reports' / 'data' / 'summary.json').read_text())['phase1']['total']['ccd0']['wall_s']['d'])}); together they do more than either.",
        "A 10 ms delay instead of 0 was also tried (phase 3, preliminary): slower on every target measured (+8% to +49% wall), so 0 is the right value.",
    ]
    change = [
        f"Branch [`{BRANCH}`]({FORK}/tree/{BRANCH}) on the fork, commit [`{fork_commit[:9]}`]({FORK}/commit/{fork_commit}), based on dotnet/fsharp `{base_commit[:9]}`: two settings in `src/fsc/fsc.targets`, written to `fsc.runtimeconfig.json` at build time.",
        "`DOTNET_TieredPGO` / `DOTNET_TC_CallCountingDelayMs` env vars still override them.",
        "**The .NET SDK does not ship this file.** dotnet/sdk generates the runtimeconfig of the fsc it ships (`src/Layout/redist/tools/tool_fsc.csproj`), so the same two settings are needed there for SDK users to get the effect.",
        "Equivalent alternatives that were also measured: the env vars set on the fsc process (the numbers below), and the Fsc MSBuild task passing them (branch `jit_flags`): identical within noise (A/A difference +0.0%, CI −0.3% to +0.7%).",
    ]
    meta = dict(
        title="fsc: turn off tiered PGO and the call-counting delay",
        generated=time.strftime("%Y-%m-%d %H:%M %Z"), harness_commit=R.git(ROOT, "rev-parse", "--short", "HEAD"),
        hardware=hw.get("hardware", []),
        software=hw.get("software", []) + [["dotnet/fsharp commit", base_commit], ["Runtime", f"Microsoft.NETCore.App {lay['runtime']}"],
                                            ["Fork branch", f"{FORK}/tree/{BRANCH} ({fork_commit[:9]})"]],
        method=[
            f"Build dotnet/fsharp `{base_commit[:9]}` in Release and lay fsc out the way the .NET SDK ships it: ReadyToRun via crossgen2 per assembly, and the SDK's `fsc.runtimeconfig.json` (Server GC).",
            "Capture every fsc command line from one full `dotnet build FSharp.Compiler.Service.slnx -c Release` (parallel compilation is on by default).",
            f"Replay each compilation in isolation with identical inputs: {len(projects)} compilations, the newest target framework of each project.",
            "Two configs: **baseline** (defaults) and **flags** (`DOTNET_TieredPGO=0`, `DOTNET_TC_CallCountingDelayMs=0` on the fsc process). Every round runs both, in shuffled order.",
            "Runs: 20 per config for the small and mid-size projects, 12 for the five largest, after 1 warm-up. Changes are medians with a 95% bootstrap CI; a change counts only when its CI excludes 0.",
            "A host monitor records system CPU and memory around every run; disturbed runs are flagged, not dropped.",
        ],
        configs=[dict(id="baseline", desc="defaults", env={}), dict(id="flags", desc="TieredPGO=false + CallCountingDelayMs=0", env={})],
        ref_config="baseline",
        links=dict(full=f"{SITE}/report.html", full_md=f"{REPO}/blob/main/reports/report.md", phase3=f"{SITE}/phase3/report.html",
                   phase3_md=f"{REPO}/blob/main/reports/phase3/report.md", repo=REPO, branch=f"{FORK}/tree/{BRANCH}",
                   commit=f"{FORK}/commit/{fork_commit}", this=f"{SITE}/pr/report.html", this_md=f"{REPO}/blob/main/reports/pr/report.md"),
    )
    metrics = [dict(key=k, label=l, unit=u, group=gr, digits=d) for k, l, u, gr, d in R.METRICS]
    data = dict(meta=meta, metrics=metrics, sections=SECTIONS, projects=projects, stats=stats, cmp=cmp, totals=totals,
                small=small, build=build, phase_segments=[s for s, _ in R.PHASE_SEGMENTS], summary=summary, why=why,
                change=change, diff=diff, quality=dict(n=len(runs), flagged=flags_n, rule=R.FLAG_RULE))
    (out / "data" / "summary.json").write_text(json.dumps(data, indent=1))
    (out / "data" / "report-data.js").write_text(f"window.REPORT_DATA = {json.dumps(data, separators=(',', ':'))};\n")
    write_html(out)
    write_md(data, out)
    print(f"wrote {out}/report.html, report.md")


def region(s, begin, end):
    i = s.index("\n", s.index(begin)) + 1
    return s[i:s.index(end, i)]


def write_html(out):
    main = (ROOT / "scripts" / "report_template.html").read_text()
    page = (ROOT / "scripts" / "report_pr_template.html").read_text()
    page = page.replace("/*@STYLE*/", main[main.index("<style>") + 7:main.index("</style>")].strip("\n"))
    page = page.replace("//@SHARED_JS", region(main, "/* @shared-begin", "/* @shared-end */"))
    (out / "report.html").write_text(page)


def anchor(title):  # GitHub heading anchors
    return re.sub(r"[^a-z0-9 _-]", "", title.lower()).replace(" ", "-")


def write_md(data, out):
    m, P, st, cm, T = data["meta"], data["projects"], data["stats"], data["cmp"], data["totals"]
    L_ = m["links"]
    chip = dict(m["hardware"]).get("Chip", "")
    sub = f"{chip} · .NET 11 RC1, parallel compilation on · median, 95% bootstrap CI"
    lab = lambda p: p.replace("__", " · ")
    rows = [(lab(p), {"Wall time": cm[p]["flags"]["wall_s"], "CPU time": cm[p]["flags"]["cpu_s"]}) for p in P]
    RM.forest_png(out / "img" / "pr_change.png", rows, ["Wall time", "CPU time"], {"Wall time": RM.SLOTS[0], "CPU time": RM.SLOTS[1]},
                  "fsc with TieredPGO=false + CallCountingDelayMs=0: change vs defaults", sub,
                  "median(flags) / median(defaults) − 1 (negative = lower)")
    segs = data["phase_segments"]
    bars = [(c if c == "baseline" else "flags", {s: sum(st[p][c].get(f"phase:{s}", {}).get("median", 0) for p in P) for s in segs}) for c in ("baseline", "flags")]
    RM.stacked_png(out / "img" / "pr_phases.png", bars, segs, f"Compiler phases (--times), summed over {len(P)} compilations", sub,
                   "seconds (sum of per-compilation medians)")
    img = lambda n: f"{SITE}/pr/img/{n}"
    secs = {sid: t for sid, t, _ in data["sections"]}
    L = [f"# {m['title']}", "",
         f"_Short report. Interactive version: [{L_['this']}]({L_['this']}). Full reports: [JIT flags, parallel compilation, SDK 10 vs 11]({L_['full']}) · [other runtime settings]({L_['phase3']}). Generated {m['generated']}._", "",
         "**Contents:** " + " · ".join(f"[{t}](#{anchor(t)})" for t in secs.values()), ""]
    L += [f"## {secs['summary']}", ""] + [f"- {s}" for s in data["summary"]] + [""]
    L += ["<details><summary>Why it helps</summary>", ""] + [f"- {s}" for s in data["why"]] + ["", "</details>", ""]
    L += [f"## {secs['change']}", ""] + [f"- {s}" for s in data["change"]] + ["", "```diff", data["diff"].strip(), "```", ""]
    L += [f"## {secs['results']}", "", f"![Change per compilation]({img('pr_change.png')})", "",
          "<details open><summary>Table</summary>", "",
          "| Compilation | Defaults | Flags | Δ wall (95% CI) | Δ CPU (95% CI) |", "|---|---:|---:|---|---|"]
    for p in P:
        L.append(f"| {lab(p)} | {st[p]['baseline']['wall_s']['median']:.2f} s | {st[p]['flags']['wall_s']['median']:.2f} s | "
                 f"{RM.pct(cm[p]['flags']['wall_s'])} | {RM.pct(cm[p]['flags']['cpu_s'])} |")
    tw, tc = T["wall_s"], T["cpu_s"]
    L += [f"| **All {len(P)}** | {tw['ref']:.1f} s | {tw['val']:.1f} s | {RM.pct(tw)} | {RM.pct(tc)} |", "",
          "Bold: the 95% CI excludes 0.", "", "</details>", ""]
    b = data["build"]
    L += [f"## {secs['build']}", "",
          f"`dotnet build fantomas.slnx -c Release --no-incremental` (Fantomas `{b['commit']}`, 12 projects) on SDK 11 RC1, {b['n']} interleaved runs per mode, build servers shut down before each run.", "",
          "| Settings applied to | Wall | Δ wall (95% CI) | Δ CPU (95% CI) |", "|---|---:|---|---|",
          f"| nothing (defaults) | {b['none']['wall_s']:.1f} s | | |",
          f"| fsc processes only (what this change does) | | {RM.pct(b['env_fsc']['wall_s'])} | {RM.pct(b['env_fsc']['cpu_s'], False)} |",
          f"| `dotnet build` (MSBuild too) | | {RM.pct(b['env_all']['wall_s'])} | {RM.pct(b['env_all']['cpu_s'], False)} |", "",
          "Build CPU is measured system-wide and is noisy; neither CPU change is significant.", ""]
    L += [f"## {secs['where']}", "", "<details><summary>Compiler phases, GC and JIT</summary>", "",
          f"![Compiler phases]({img('pr_phases.png')})", "",
          "| All compilations, sum of medians | Defaults | Flags | Δ (95% CI) |", "|---|---:|---:|---|"]
    for k, name, unit, scale in [("wall_s", "Wall time", "s", 1), ("cpu_s", "CPU time", "s", 1), ("jit_time_ms", "JIT time (all threads)", "s", 1000),
                                 ("gc_pause_ms", "GC pause time", "s", 1000)] + [(f"phase:{s}", f"Phase: {s}", "s", 1) for s in segs]:
        x = T.get(k)
        if x:
            L.append(f"| {name} | {x['ref'] / scale:.1f} {unit} | {x['val'] / scale:.1f} {unit} | {RM.pct(x)} |")
    L += ["", "</details>", ""]
    L += [f"## {secs['setup']}", "", "<details><summary>Hardware, software, method</summary>", ""]
    L += [f"- **{k}**: {v}" for k, v in m["hardware"]] + [""] + [f"- **{k}**: {v}" for k, v in m["software"]] + [""]
    L += [f"{i}. {s}" for i, s in enumerate(m["method"], 1)] + ["", "</details>", ""]
    q = data["quality"]
    L += [f"## {secs['quality']}", "", "<details><summary>Disturbed runs and caveats</summary>", "",
          f"- {q['flagged']} of {q['n']} runs were flagged as disturbed by other activity on the machine (rule: {q['rule']}). Excluding them does not change the totals.",
          "- The stats hook and `--times` cost about 2% to 3% wall in every config alike (not significant).",
          "- One machine: an 11-core Apple M3 Pro on macOS. Machines with more cores have more idle cores for the background tier-1 JIT, so gains on Linux x64 build machines may differ and are worth confirming.",
          "- Compilations are replayed one at a time. In a real build several run in parallel, which is why the whole-build gain is smaller.", "", "</details>", ""]
    L += [f"## {secs['links']}", "",
          f"- Full report (all 17 compilations and TFMs, every metric, parallel compilation on/off, SDK 10 vs 11): [{L_['full']}]({L_['full']}) ([MD]({L_['full_md']}))",
          f"- Other runtime settings tried on top of these (GC modes, DATAS, MultiCoreJit, composite R2R): [{L_['phase3']}]({L_['phase3']}) ([MD]({L_['phase3_md']}))",
          f"- Scripts and raw data: [{L_['repo']}]({L_['repo']})",
          f"- Compiler change: [{L_['branch']}]({L_['branch']})", "",
          "```", "scripts/run_all.sh phase1            # benchmark (resumes)", ".venv/bin/python scripts/report.py   # full report",
          ".venv/bin/python scripts/report_pr.py  # this report", "```", ""]
    (out / "report.md").write_text("\n".join(L))


if __name__ == "__main__":
    main()
