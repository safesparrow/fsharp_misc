"""Markdown report + PNG charts (matplotlib). Called by report.py with the computed data dict.

The MD is the short, shareable version: plain tables, numbered/bulleted lists, PNG charts that
carry their own title, subtitle and legend so they survive being copied into an issue or PR."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GRAY = "#898781"
INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#fcfcfb"


def config_colors(cells):
    out = {"baseline": GRAY}
    for i, c in enumerate([c for c in cells if c != "baseline"]):
        out[c] = SLOTS[i % len(SLOTS)]
    return out


def _style(ax):
    ax.set_facecolor(SURF)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


HEADER_IN = 1.05  # inches reserved at the top for title, subtitle and legend


def _title(fig, title, subtitle):
    h = fig.get_size_inches()[1]
    fig.text(0.01, 1 - 0.12 / h, title, ha="left", va="top", fontsize=13, fontweight="semibold", color=INK)
    fig.text(0.01, 1 - 0.40 / h, subtitle, ha="left", va="top", fontsize=9, color=INK2)


def _top(fig):
    return 1 - HEADER_IN / fig.get_size_inches()[1]


def _pct_fmt(v, _):
    if abs(v) < 1e-9:
        return "0%"
    s = f"{v:+.1f}".rstrip("0").rstrip(".")
    return s.replace("-", "\u2212") + "%"


def forest_png(path, rows, series, colors, title, subtitle, xlabel):
    """rows: [(label, {series: dict(d, lo, hi, sig)})]. Dodged dots + CI whiskers; filled = significant."""
    n = len(rows); k = len(series)
    h = max(3.0, 0.18 * n * max(1, k) + 0.25 * n + HEADER_IN + 0.7)
    fig, ax = plt.subplots(figsize=(10, h), dpi=150)
    fig.patch.set_facecolor(SURF)
    _style(ax)
    band = 0.8
    for i, (label, d) in enumerate(rows):
        if i % 2 == 0:
            ax.axhspan(i - 0.5, i + 0.5, color=INK, alpha=0.03, linewidth=0)
        for j, s in enumerate(series):
            x = d.get(s)
            if not x:
                continue
            y = i - band / 2 + band * (j + 0.5) / k
            ax.plot([x["lo"] * 100, x["hi"] * 100], [y, y], color=colors[s], linewidth=2, solid_capstyle="round")
            ax.scatter([x["d"] * 100], [y], s=36, zorder=3, color=colors[s] if x["sig"] else SURF,
                       edgecolors=colors[s], linewidths=1.6)
    ax.axvline(0, color=MUTED, linewidth=1)
    ax.set_yticks(range(n)); ax.set_yticklabels([r[0] for r in rows], fontsize=9, color=INK)
    ax.set_ylim(n - 0.5, -0.5)
    ax.set_xlabel(xlabel, color=INK2, fontsize=9)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(_pct_fmt))
    handles = [plt.Line2D([], [], marker="o", color=colors[s], markerfacecolor=colors[s], linewidth=2, markersize=6)
               for s in series]
    handles.append(plt.Line2D([], [], marker="o", color=MUTED, markerfacecolor=SURF, linewidth=0, markersize=6))
    _title(fig, title, subtitle)
    fig.legend(handles, list(series) + ["hollow = 95% CI includes 0"], loc="upper left",
               bbox_to_anchor=(0.005, 1 - 0.62 / h), ncol=min(len(series) + 1, 6), frameon=False, fontsize=9,
               labelcolor=INK2, handlelength=2.2, columnspacing=1.6)
    fig.tight_layout(rect=(0, 0, 1, _top(fig)))
    fig.savefig(path, facecolor=SURF); plt.close(fig)


def stacked_png(path, bars, segs, title, subtitle, xlabel):
    """bars: [(label, {segment: seconds})] horizontal stacked bars with 2px surface gaps."""
    fig, ax = plt.subplots(figsize=(10, 0.5 * len(bars) + HEADER_IN + 1.0), dpi=150)
    fig.patch.set_facecolor(SURF)
    _style(ax)
    for i, (label, d) in enumerate(bars):
        left = 0
        for j, s in enumerate(segs):
            v = d.get(s, 0)
            ax.barh(i, v, left=left, height=0.55, color=SLOTS[j % 8], edgecolor=SURF, linewidth=1.5)
            left += v
        ax.text(left, i, f"  {left:.1f} s", va="center", fontsize=9, color=INK2)
    ax.set_yticks(range(len(bars))); ax.set_yticklabels([b[0] for b in bars], fontsize=9, color=INK)
    ax.set_ylim(len(bars) - 0.5, -0.5)
    ax.set_xlabel(xlabel, color=INK2, fontsize=9)
    handles = [plt.Rectangle((0, 0), 1, 1, color=SLOTS[j % 8]) for j in range(len(segs))]
    _title(fig, title, subtitle)
    h = fig.get_size_inches()[1]
    fig.legend(handles, segs, loc="upper left", bbox_to_anchor=(0.005, 1 - 0.62 / h), ncol=len(segs), frameon=False,
               fontsize=8.5, labelcolor=INK2, handlelength=1.2, columnspacing=1.2)
    fig.tight_layout(rect=(0, 0, 1, _top(fig)))
    fig.savefig(path, facecolor=SURF); plt.close(fig)


def pct(x, bold=True):
    if not x:
        return "n/a"
    s = f"{x['d'] * 100:+.1f}% ({x['lo'] * 100:+.1f} to {x['hi'] * 100:+.1f})"
    return f"**{s}**" if (bold and x["sig"]) else s


def write(data, out: Path):
    meta = data["meta"]; p1 = data["phase1"]; projects = data["projects"]
    cells = p1["cells"]; others = [c for c in cells if c != "baseline"]
    colors = config_colors(cells)
    hw = dict(meta["hardware"])
    chip = hw.get("Chip", "?")
    sub = f"fsc replaying FSharp.Compiler.Service.slnx compilations · {chip}, {hw.get('CPU cores', '').split(' (')[0]} · " \
          f"{p1['runs']} runs per cell · median, 95% bootstrap CI"
    L = []
    L += [f"# {meta['title']}", "",
          f"_Generated {meta['generated']} by `scripts/report.py` (harness commit `{meta['harness_commit']}`). "
          "Interactive version with every metric: `reports/report.html`. All numbers: `reports/data/summary.json`._", ""]

    if data.get("findings_md"):
        L += [data["findings_md"].strip(), ""]

    L += ["## Setup", "", "Hardware:", ""]
    L += [f"- **{k}**: {v}" for k, v in meta["hardware"]]
    L += ["", "Software:", ""]
    L += [f"- **{k}**: {v}" for k, v in meta["software"]]
    L += ["", "Method:", "",
          "1. Build dotnet/fsharp in Release (`./build.sh -c Release`).",
          "2. Make an SDK-like layout: crossgen2 (ReadyToRun) each assembly the way dotnet/sdk does, and use the SDK's `fsc.runtimeconfig.json` (Server GC).",
          "3. Do one full `dotnet build FSharp.Compiler.Service.slnx -c Release` with an fsc wrapper, to capture every fsc command line (17 compilations, 14 projects).",
          "4. Replay each compilation in isolation (`dotnet fsc.dll @rsp`). Inputs are identical in every config, and outputs go to a scratch dir.",
          f"5. For each compilation: {p1['warmup']} warm-up, then {p1['runs']} measured rounds. Every round runs all configs in shuffled order, so drift hits every config equally.",
          "6. Per run, record:",
          "   - wall and CPU time and peak RSS (`wait4`)",
          "   - GC pause, GC count, allocations, JIT time and methods (startup hook: `GC.GetTotalPauseDuration`, `JitInfo`)",
          "   - compiler phases (`--times:<csv>`)",
          "   - host activity (system-wide CPU ticks, paging)",
          f"7. Flag a run as host-disturbed if: {meta.get('flag_rule', '')}. Headline totals are shown with and without flagged runs.",
          "", "Configurations:", "", "| config | what |", "|---|---|"]
    L += [f"| `{c['id']}` | {c['desc']} |" for c in meta["configs"]]
    L += [""]

    # ---- phase 1 headline
    t, tc = p1["total"], p1.get("total_clean", {})
    L += ["## Phase 1: JIT flags (parallel compilation on, as in a normal build)", "",
          "### Whole solution (sum of per-compilation medians)", "",
          "| config | wall | CPU | GC pause | JIT time | wall, flagged runs excluded |",
          "|---|---|---|---|---|---|"]
    b = {k: t[others[0]][k]["ref"] for k in ("wall_s", "cpu_s", "gc_pause_ms", "jit_time_ms") if others and k in t[others[0]]}
    L.append(f"| `baseline` | {b.get('wall_s', 0):.1f} s | {b.get('cpu_s', 0):.1f} s | {b.get('gc_pause_ms', 0) / 1000:.1f} s "
             f"| {b.get('jit_time_ms', 0) / 1000:.1f} s | |")
    for c in others:
        x = t.get(c, {})
        L.append(f"| `{c}` | {pct(x.get('wall_s'))} | {pct(x.get('cpu_s'))} | {pct(x.get('gc_pause_ms'))} "
                 f"| {pct(x.get('jit_time_ms'))} | {pct(tc.get(c, {}).get('wall_s'))} |")
    L += ["", "Values are the change vs `baseline`, with the 95% CI in parentheses. **Bold** = the CI excludes 0.", ""]
    if p1.get("aa"):
        a = p1["aa"]["total"]
        L += [f"Noise floor: `jit_flags` vs `pgo0_ccd0` is the same compiler code with the same env, built twice. "
              f"Whole-solution wall {pct(a['wall_s'], False)}, CPU {pct(a['cpu_s'], False)}.", ""]

    for k, lab in (("wall_s", "Wall time"), ("cpu_s", "CPU time")):
        rows = [(p, {c: p1["cmp"].get(p, {}).get(c, {}).get(k) for c in others}) for p in projects]
        fn = f"img/phase1_{k}.png"
        forest_png(out / fn, rows, others, colors, f"{lab}: change vs baseline, per compilation", sub,
                   f"{lab} change vs baseline (negative = faster)")
        L += [f"### {lab} per compilation", "", f"![{lab} change vs baseline]({fn})", ""]

    L += ["### Wall time per compilation", "",
          "| compilation | baseline median | " + " | ".join(f"`{c}`" for c in others) + " |",
          "|---|---:|" + "---|" * len(others)]
    for p in projects:
        s = p1["stats"].get(p, {}).get("baseline", {}).get("wall_s")
        L.append(f"| {p} | {s['median']:.2f} s ± {s['sd']:.2f} | " +
                 " | ".join(pct(p1['cmp'].get(p, {}).get(c, {}).get('wall_s')) for c in others) + " |")
    L += [""]

    segs = data["phase_segments"]
    bars = []
    for c in cells:
        d = {}
        for s in segs:
            k = f"phase:{s}"
            d[s] = sum(p1["stats"][p][c][k]["median"] for p in projects if k in p1["stats"].get(p, {}).get(c, {}))
        bars.append((c, d))
    fn = "img/phase1_phases.png"
    stacked_png(out / fn, bars, segs, "Where the time goes: compiler phases, summed over all compilations", sub,
                "seconds (sum of per-compilation medians)")
    L += ["### Compiler phases (whole solution)", "", f"![phase breakdown]({fn})", "",
          "| phase | baseline | " + " | ".join(f"`{c}`" for c in others) + " |", "|---|---:|" + "---|" * len(others)]
    for s in segs:
        k = f"phase:{s}"
        bt = next((t[c][k]["ref"] for c in others if k in t.get(c, {})), None)
        L.append(f"| {s} | {bt:.1f} s | " + " | ".join(pct(t.get(c, {}).get(k)) for c in others) + " |" if bt is not None else f"| {s} | | |")
    L += [""]

    # ---- phase 2
    p2 = data.get("phase2")
    if p2:
        jit = p2["jit"]; jc = config_colors(jit)
        L += ["## Phase 2: parallel compilation on/off, by JIT config", "",
              "- `par` = `--parallelcompilation+`, the Fsc task default: graph-based type checking, parallel optimization, parallel IL gen and parallel reference resolution.",
              "- `seq` = `--parallelcompilation-`.",
              "- \"par vs seq\" = median(par) / median(seq) − 1. Negative means parallel compilation is faster.",
              "- \"Interaction\" = how much the config changes the parallel speedup: (par/seq under config) / (par/seq under baseline) − 1. Negative means parallel compilation helps *more* under that config.",
              "", "### Whole solution", "",
              "| JIT config | seq wall | par wall | par vs seq (wall) | par vs seq (CPU) | interaction (wall) |",
              "|---|---:|---:|---|---|---|"]
        for c in jit:
            x = p2["par_total"][c]
            it = p2["interaction_total"].get(c, {}).get("wall_s")
            L.append(f"| `{c}` | {x['wall_s']['ref']:.1f} s | {x['wall_s']['val']:.1f} s | {pct(x['wall_s'])} | {pct(x['cpu_s'])} | "
                     f"{pct(it) if it else '(reference)'} |")
        L += ["", "JIT config effect within each mode (whole-solution wall, vs `baseline` in the same mode):", "",
              "| mode | " + " | ".join(f"`{c}`" for c in jit if c != "baseline") + " |", "|---|" + "---|" * (len(jit) - 1)]
        for v in ("seq", "par"):
            L.append(f"| {v} | " + " | ".join(pct(p2['jit_total'][v].get(c, {}).get('wall_s')) for c in jit if c != 'baseline') + " |")
        L += [""]
        sub2 = sub.replace(f"{p1['runs']} runs", f"{p2['runs']} runs")
        rows = [(p, {c: p2["par_eff"].get(p, {}).get(c, {}).get("wall_s") for c in jit}) for p in p2["projects"]]
        fn = "img/phase2_wall_s.png"
        forest_png(out / fn, rows, jit, jc, "Parallel vs sequential compilation: wall time, per JIT config", sub2,
                   "median(par)/median(seq) − 1 (negative = parallel compilation is faster)")
        L += ["### Parallel speedup per compilation", "", f"![par vs seq]({fn})", "",
              "| compilation | " + " | ".join(f"`{c}` par vs seq" for c in jit) + " |", "|---|" + "---|" * len(jit)]
        for p in p2["projects"]:
            L.append(f"| {p} | " + " | ".join(pct(p2['par_eff'].get(p, {}).get(c, {}).get('wall_s')) for c in jit) + " |")
        L += [""]

    # ---- SDK comparison and whole-build test
    sc = data.get("sdk_compare") or {}
    fsc = sc.get("fsc")
    if fsc:
        comps = [c for c in ("sdk10", "sdk11", "main") if c in fsc["total"]]
        names = {"sdk10": "SDK 10.0.302 (fsc 15.2.302, .NET 10.0.10)", "sdk11": "SDK 11 RC1 (fsc 15.2.101, .NET 11 RC1)",
                 "main": "main @ 99406265, SDK-like R2R layout (.NET 11 RC1)"}
        L += ["## Do the flags behave differently on SDK 10 and SDK 11?", "",
              "The compilers as shipped in each SDK (no changes to SDK files), run on the same captured compilations. "
              "The flags are set as env vars on the fsc process, and configs are interleaved.", "",
              "Compilers:", ""] + [f"- `{c}`: {names[c]}" for c in comps] + [
              "", f"### All measured compilations together ({len(fsc['projects'])} compilations, sum of medians)", "",
              "| compiler | wall, no flags | wall, flags | Δ wall | Δ CPU |", "|---|---:|---:|---|---|"]
        for c in comps:
            x = fsc["total"][c]
            L.append(f"| `{c}` | {x['wall_s']['ref']:.1f} s | {x['wall_s']['val']:.1f} s | {pct(x['wall_s'])} | {pct(x['cpu_s'])} |")
        L += [""]
        sc_cols = {"sdk10": SLOTS[0], "sdk11": SLOTS[1], "main": SLOTS[2]}
        for k, lab in (("wall_s", "Wall time"), ("cpu_s", "CPU time")):
            rows = [(p, {c: fsc["per_project"][p].get(c, {}).get(k) for c in comps}) for p in fsc["projects"]]
            fn = f"img/sdk_flags_{k}.png"
            forest_png(out / fn, rows, comps, sc_cols, f"{lab}: effect of the two flags, per compiler",
                       f"DOTNET_TieredPGO=0 + DOTNET_TC_CallCountingDelayMs=0 vs none · {chip} · median, 95% bootstrap CI",
                       f"{lab}: median(flags)/median(none) − 1 (negative = faster)")
            L += [f"![{lab} effect of flags per compiler]({fn})", ""]
        L += ["| compilation | " + " | ".join(f"`{c}` no flags" for c in comps) + " | " +
              " | ".join(f"`{c}` Δ wall" for c in comps) + " |", "|---|" + "---:|" * len(comps) + "---|" * len(comps)]
        for p in fsc["projects"]:
            r = fsc["per_project"][p]
            L.append(f"| {p} | " + " | ".join(f"{r[c]['median_none']['wall_s']:.2f} s" if c in r else "–" for c in comps) + " | " +
                     " | ".join(pct(r.get(c, {}).get("wall_s")) for c in comps) + " |")
        L += [""]
    bb = sc.get("build")
    if bb:
        L += [f"## Whole `dotnet build` (Fantomas @ `{(bb.get('commit') or '')[:9]}`, 12 projects)", "",
              "- `none`: plain `dotnet build -c Release --no-restore --no-incremental -nodeReuse:false`.",
              "- `env_all`: `DOTNET_TieredPGO=0 DOTNET_TC_CallCountingDelayMs=0 dotnet build ...`. This affects MSBuild and every fsc.",
              "- `env_fsc`: the same flags on fsc processes only, applied through a wrapper. MSBuild keeps its defaults.",
              "- Build servers are shut down before every run, and the modes are interleaved in shuffled rounds.", "",
              "| SDK | none: wall | none: CPU | env_all: Δ wall | env_all: Δ CPU | env_fsc: Δ wall | env_fsc: Δ CPU |",
              "|---|---:|---:|---|---|---|---|"]
        for sdk, row in bb["per_sdk"].items():
            L.append(f"| {sdk} | {row['none']['wall_s']:.1f} s | {row['none']['cpu_s']:.1f} s | " +
                     " | ".join(pct(row.get(m, {}).get(k)) for m in ("env_all", "env_fsc") for k in ("wall_s", "cpu_s")) + " |")
        L += [""]

    # ---- measurement quality
    L += ["## Measurement quality", ""]
    ov = data.get("overhead")
    if ov:
        L += ["Instrumentation overhead (startup hook + `--times:<csv>` vs a bare run, baseline config):", ""]
        for p, x in ov.items():
            L.append(f"- {p}: wall {pct(x['wall_s'], False)}, CPU {pct(x['cpu_s'], False)} (n={x['wall_s']['n']} per side)")
        L += [""]
    fl = p1["flags"]
    n = sum(f["n"] for p in fl for f in fl[p].values()); k = sum(f["flagged"] for p in fl for f in fl[p].values())
    L += [f"Host-disturbed runs (phase 1): {k} of {n} ({100 * k / max(n, 1):.0f}%). Rule: {meta.get('flag_rule', '')}.", ""]
    L += ["## Caveats", "",
          "- One machine (Apple Silicon laptop, desktop session). Results may differ on x64, Linux, Windows, or CI agents.",
          "- Each compilation runs in isolation, one at a time. In a real `dotnet build`, several fsc processes run concurrently and compete for cores, which can change how much background JIT and parallel compilation help.",
          "- The replayed compilations come from the repo's own build. Their command lines are realistic, but they use the repo's bootstrap compiler settings (`--realsig+`, `LangVersion=preview` and so on).",
          "- Env vars set through the Fsc task override any user setting of the same variables; the branch keeps an opt-out (`DisableFscJitTuning=true`).",
          "", "## Reproduce", "", "See `README.md`. Raw runs: `results/*/runs.jsonl`. Run metadata: `results/*/meta-*.json`.", ""]
    (out / "report.md").write_text("\n".join(L) + "\n")
