#!/usr/bin/env python3
"""Summarize results/sdk_compare (fsc-level, identical inputs) and results/buildbench (whole dotnet build).

Prints markdown tables:
  * per compilation x compiler: median wall/CPU without flags, and the flags' effect (with 95% CI)
  * the compilers against each other (no flags)
  * build-level: per SDK, env_all and env_fsc vs none
Also writes results/sdk_compare/summary.json for the reports.
"""
import json, random, statistics, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report as R  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def pct(x):
    if not x:
        return "–"
    s = f"{x['d'] * 100:+.1f}% ({x['lo'] * 100:+.1f} to {x['hi'] * 100:+.1f})"
    return f"**{s}**" if x["sig"] else s


def main():
    rng = random.Random(7)
    out = {}
    runs, _ = R.load_runs(ROOT / "results" / "sdk_compare")
    if runs:
        g = defaultdict(lambda: defaultdict(list))
        nflag = 0
        for r in runs:
            nflag += R.is_flagged(r)
            for k in ("wall_s", "cpu_s"):
                g[(r["project"], r["config"])][k].append(r[k])
            if r.get("rt"):
                g[(r["project"], r["config"])]["jit_time_ms"].append(r["rt"]["jit_time_ms"])
                g[(r["project"], r["config"])]["gc_pause_ms"].append(r["rt"]["gc_pause_ms"])
        projects = sorted({p for p, _ in g}, key=lambda p: -statistics.median(g[(p, "main")]["wall_s"]))
        comps = ["sdk10", "sdk11", "main"]
        print(f"## fsc level: identical inputs ({len(runs)} runs, {nflag} host-flagged)\n")
        print("Effect of DOTNET_TieredPGO=0 + DOTNET_TC_CallCountingDelayMs=0, per compiler (median(flags)/median(none) - 1):\n")
        print("| compilation | " + " | ".join(f"{c} wall (none)" for c in comps) + " | " +
              " | ".join(f"{c}: flags Δwall" for c in comps) + " | " + " | ".join(f"{c}: flags ΔCPU" for c in comps) + " |")
        print("|---|" + "---:|" * 3 + "---|" * 6)
        res = {}
        for p in projects:
            row = {}
            for c in comps:
                a, b = g.get((p, c)), g.get((p, f"{c}_flags"))
                if a and b:
                    row[c] = {k: R.ratio(rng, a[k], b[k]) for k in a if k in b}
                    row[c]["median_none"] = {k: statistics.median(v) for k, v in a.items()}
                    row[c]["median_flags"] = {k: statistics.median(v) for k, v in b.items()}
            res[p] = row
            print(f"| {p} | " + " | ".join(f"{row[c]['median_none']['wall_s']:.2f} s" if c in row else "–" for c in comps) + " | " +
                  " | ".join(pct(row.get(c, {}).get("wall_s")) for c in comps) + " | " +
                  " | ".join(pct(row.get(c, {}).get("cpu_s")) for c in comps) + " |")
        print("\nCompilers against each other (both without flags, and both with flags):\n")
        print("| compilation | sdk11 vs sdk10 | main vs sdk10 | sdk11_flags vs sdk10_flags | main_flags vs sdk10_flags |")
        print("|---|---|---|---|---|")
        cross = {}
        for p in projects:
            x = {}
            for a_, b_ in (("sdk10", "sdk11"), ("sdk10", "main"), ("sdk10_flags", "sdk11_flags"), ("sdk10_flags", "main_flags")):
                if (p, a_) in g and (p, b_) in g:
                    x[f"{b_}_vs_{a_}"] = R.ratio(rng, g[(p, a_)]["wall_s"], g[(p, b_)]["wall_s"])
            cross[p] = x
            print(f"| {p} | " + " | ".join(pct(x.get(k)) for k in ("sdk11_vs_sdk10", "main_vs_sdk10", "sdk11_flags_vs_sdk10_flags",
                                                                    "main_flags_vs_sdk10_flags")) + " |")
        tot = {}
        for c in comps:
            ga = [g[(p, c)]["wall_s"] for p in projects if (p, c) in g and (p, f"{c}_flags") in g]
            gb = [g[(p, f"{c}_flags")]["wall_s"] for p in projects if (p, c) in g and (p, f"{c}_flags") in g]
            ca = [g[(p, c)]["cpu_s"] for p in projects if (p, c) in g and (p, f"{c}_flags") in g]
            cb = [g[(p, f"{c}_flags")]["cpu_s"] for p in projects if (p, c) in g and (p, f"{c}_flags") in g]
            if ga:
                tot[c] = dict(wall_s=R.ratio_total(rng, ga, gb), cpu_s=R.ratio_total(rng, ca, cb))
        print("\nAll measured compilations together (sum of medians):\n")
        print("| compiler | wall none | wall flags | Δ wall | Δ CPU |\n|---|---:|---:|---|---|")
        for c, x in tot.items():
            print(f"| {c} | {x['wall_s']['ref']:.1f} s | {x['wall_s']['val']:.1f} s | {pct(x['wall_s'])} | {pct(x['cpu_s'])} |")
        out["fsc"] = dict(projects=projects, per_project=res, cross=cross, total=tot, n_runs=len(runs), n_flagged=nflag)

    bruns, bmetas = R.load_runs(ROOT / "results" / "buildbench")
    if bruns:
        g = defaultdict(lambda: defaultdict(list))
        for r in bruns:
            for k in ("wall_s", "cpu_s"):
                g[(r["sdk"], r["mode"])][k].append(r[k])
        print(f"\n## Build level: `dotnet build` of {Path(bmetas[-1]['repo']).name} ({len(bruns)} runs)\n")
        print("| SDK | none wall | none CPU | env_all Δwall | env_all ΔCPU | env_fsc Δwall | env_fsc ΔCPU |\n|---|---:|---:|---|---|---|---|")
        b = {}
        for sdk in sorted({s for s, _ in g}):
            n = g.get((sdk, "none"))
            if not n:
                continue
            row = {m: {k: R.ratio(rng, n[k], g[(sdk, m)][k]) for k in ("wall_s", "cpu_s")} for m in ("env_all", "env_fsc") if (sdk, m) in g}
            row["none"] = {k: statistics.median(v) for k, v in n.items()}
            b[sdk] = row
            print(f"| {sdk} | {row['none']['wall_s']:.1f} s | {row['none']['cpu_s']:.1f} s | " +
                  " | ".join(pct(row.get(m, {}).get(k)) for m in ("env_all", "env_fsc") for k in ("wall_s", "cpu_s")) + " |")
        out["build"] = dict(per_sdk=b, repo=bmetas[-1]["repo"], commit=bmetas[-1].get("commit"), n_runs=len(bruns))
    (ROOT / "results" / "sdk_compare").mkdir(parents=True, exist_ok=True)
    (ROOT / "results" / "sdk_compare" / "summary.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
