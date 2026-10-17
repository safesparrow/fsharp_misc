#!/usr/bin/env python3
"""Turn benchmark runs (runs.jsonl) into reports/report.md, reports/report.html and reports/data.json.

Statistics (per project x cell, per metric):
  * median, mean, stdev, min, max, n
  * change vs reference = median(cell) / median(ref) - 1, with a 95% percentile-bootstrap CI
    (each group resampled independently, B=4000, fixed seed). "Significant" = CI excludes 0.
  * solution total = sum over projects of per-project medians (one sequential build of every
    project), CI by bootstrapping each project independently.

Usage: report.py --phase1 results/phase1 [--phase2 results/phase2] --out reports
"""
import argparse, json, math, random, statistics, subprocess, time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METRICS = {"wall_s": ("Wall time", "s"), "cpu_s": ("CPU time (user+sys)", "s"), "maxrss_mb": ("Peak RSS", "MB")}
B = 4000

CONFIG_DESC = {
    "baseline": "default runtime settings",
    "pgo0": "DOTNET_TieredPGO=0",
    "ccd0": "DOTNET_TC_CallCountingDelayMs=0",
    "pgo0_ccd0": "both env vars",
    "jit_flags": "jit_flags branch build; env as injected by its Microsoft.FSharp.Targets",
}


def load(d):
    p = Path(d) / "runs.jsonl"
    runs = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    metas = [json.loads(m.read_text()) for m in sorted(Path(d).glob("meta-*.json"))]
    return runs, metas


def q(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = math.floor(k); c = math.ceil(k)
    return xs[f] if f == c else xs[f] + (xs[c] - xs[f]) * (k - f)


def summarize(xs):
    return dict(n=len(xs), median=statistics.median(xs), mean=statistics.fmean(xs),
                sd=statistics.stdev(xs) if len(xs) > 1 else 0.0, min=min(xs), max=max(xs),
                q1=q(xs, .25), q3=q(xs, .75))


def boot_ratio(a, b, rng):
    """CI for median(b)/median(a) - 1."""
    na, nb = len(a), len(b)
    rs = []
    for _ in range(B):
        ma = statistics.median(rng.choices(a, k=na))
        mb = statistics.median(rng.choices(b, k=nb))
        rs.append(mb / ma - 1)
    rs.sort()
    return rs[int(.025 * B)], rs[int(.975 * B) - 1]


def boot_total(groups_a, groups_b, rng):
    """CI for sum(median(b_i))/sum(median(a_i)) - 1 over projects i."""
    rs = []
    for _ in range(B // 4):
        ta = sum(statistics.median(rng.choices(g, k=len(g))) for g in groups_a)
        tb = sum(statistics.median(rng.choices(g, k=len(g))) for g in groups_b)
        rs.append(tb / ta - 1)
    rs.sort()
    n = len(rs)
    return rs[int(.025 * n)], rs[int(.975 * n) - 1]


def compare(groups, ref_cell, cells, projects, rng):
    """groups[(project, cell)][metric] -> list. Returns per-project and total comparisons."""
    out = {"per_project": [], "total": []}
    for m in METRICS:
        for c in cells:
            if c == ref_cell:
                continue
            ga, gb = [], []
            for p in projects:
                a = groups.get((p, ref_cell), {}).get(m)
                b = groups.get((p, c), {}).get(m)
                if not a or not b:
                    continue
                lo, hi = boot_ratio(a, b, rng)
                d = statistics.median(b) / statistics.median(a) - 1
                out["per_project"].append(dict(project=p, cell=c, ref=ref_cell, metric=m, delta=d, lo=lo, hi=hi,
                                               sig=(lo > 0 or hi < 0), ref_median=statistics.median(a),
                                               median=statistics.median(b)))
                ga.append(a); gb.append(b)
            if ga and m != "maxrss_mb":
                lo, hi = boot_total(ga, gb, rng)
                ta = sum(statistics.median(g) for g in ga); tb = sum(statistics.median(g) for g in gb)
                out["total"].append(dict(cell=c, ref=ref_cell, metric=m, delta=tb / ta - 1, lo=lo, hi=hi,
                                         sig=(lo > 0 or hi < 0), ref_total=ta, total=tb))
    return out


def group(runs, cellf):
    g = defaultdict(lambda: defaultdict(list))
    for r in runs:
        for m in METRICS:
            g[(r["project"], cellf(r))][m].append(r[m])
    return g


def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT).stdout.strip()


def fmt_pct(d, lo, hi, sig):
    s = f"{d * 100:+.1f}% [{lo * 100:+.1f}, {hi * 100:+.1f}]"
    return f"**{s}**" if sig else s


def fmt_val(v, m):
    return f"{v:.0f}" if m == "maxrss_mb" else f"{v:.2f}"


def project_order(groups, ref_cell):
    ps = {p for p, _ in groups}
    return sorted(ps, key=lambda p: -statistics.median(groups[(p, ref_cell)]["wall_s"]) if (p, ref_cell) in groups else 0)


# ---------------------------------------------------------------- static SVG for the markdown report
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]


def forest_svg(rows, cells, title, colors, xlabel="change in median vs reference (%)"):
    """rows: list of (label, {cell: (d, lo, hi, sig)}). Plain static SVG, light background."""
    rowh = 14 * max(1, len(cells)) + 10
    left, right, top = 270, 30, 46
    w = 900
    h = top + rowh * len(rows) + 46
    vals = [v for _, d in rows for (dd, lo, hi, _) in d.values() for v in (lo, hi, dd)]
    lo_ = min(vals + [0]); hi_ = max(vals + [0])
    pad = (hi_ - lo_) * 0.08 or 0.01
    lo_ -= pad; hi_ += pad
    X = lambda v: left + (v - lo_) / (hi_ - lo_) * (w - left - right)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
         f'font-family="system-ui,-apple-system,Segoe UI,sans-serif" font-size="12">',
         f'<rect width="{w}" height="{h}" fill="#fcfcfb"/>',
         f'<text x="16" y="22" font-size="14" font-weight="600" fill="#0b0b0b">{title}</text>']
    # legend
    lx = 16
    for c in cells:
        o.append(f'<circle cx="{lx + 5}" cy="36" r="4" fill="{colors[c]}"/>'
                 f'<text x="{lx + 13}" y="40" fill="#52514e">{c}</text>')
        lx += 13 + 8 * len(c) + 18
    # ticks
    step = nice_step((hi_ - lo_) * 100 / 6) / 100
    t = math.ceil(lo_ / step) * step
    while t <= hi_:
        x = X(t)
        o.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{h - 40}" stroke="#e1e0d9"/>'
                 f'<text x="{x:.1f}" y="{h - 26}" text-anchor="middle" fill="#898781">{t * 100:+.0f}%</text>')
        t += step
    o.append(f'<line x1="{X(0):.1f}" x2="{X(0):.1f}" y1="{top}" y2="{h - 40}" stroke="#898781"/>')
    o.append(f'<text x="{(left + w - right) / 2:.0f}" y="{h - 8}" text-anchor="middle" fill="#52514e">{xlabel}</text>')
    for i, (label, d) in enumerate(rows):
        y0 = top + i * rowh
        if i % 2 == 0:
            o.append(f'<rect x="0" y="{y0}" width="{w}" height="{rowh}" fill="#0b0b0b" fill-opacity="0.03"/>')
        o.append(f'<text x="{left - 10}" y="{y0 + rowh / 2 + 4:.1f}" text-anchor="end" fill="#0b0b0b">{label}</text>')
        for j, c in enumerate(cells):
            if c not in d:
                continue
            dd, lo, hi, sig = d[c]
            y = y0 + 9 + j * 14
            o.append(f'<line x1="{X(lo):.1f}" x2="{X(hi):.1f}" y1="{y}" y2="{y}" stroke="{colors[c]}" stroke-width="2" stroke-linecap="round"/>')
            fill = colors[c] if sig else "#fcfcfb"
            o.append(f'<circle cx="{X(dd):.1f}" cy="{y}" r="4.5" fill="{fill}" stroke="{colors[c]}" stroke-width="2"/>')
    o.append("</svg>")
    return "\n".join(o)


def nice_step(raw):
    if raw <= 0:
        return 1
    e = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * e:
            return m * e
    return 10 * e


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase1", required=True)
    ap.add_argument("--phase2", default=None)
    ap.add_argument("--out", default=str(ROOT / "reports"))
    a = ap.parse_args()
    out = Path(a.out); (out / "img").mkdir(parents=True, exist_ok=True)
    rng = random.Random(1234)

    runs1, metas1 = load(a.phase1)
    runs1 = [r for r in runs1 if r["variant"] == "par"]
    g1 = group(runs1, lambda r: r["config"])
    cfg_order = [c["id"] for c in metas1[-1]["configs"]]
    cells1 = [c for c in cfg_order if any(k[1] == c for k in g1)]
    projects = project_order(g1, "baseline")
    cmp1 = compare(g1, "baseline", cells1, projects, rng)
    colors = {"baseline": "#898781"}
    for i, c in enumerate([c for c in cells1 if c != "baseline"]):
        colors[c] = SLOTS[i]

    data = dict(generated=time.strftime("%Y-%m-%d %H:%M %Z"), repo_commit=git("rev-parse", "--short", "HEAD"),
                metrics={k: dict(label=v[0], unit=v[1]) for k, v in METRICS.items()},
                config_desc=CONFIG_DESC, colors=colors, projects=projects,
                phase1=dict(cells=cells1, meta=metas1[-1], runs=runs1, cmp=cmp1,
                            summary=[dict(project=p, cell=c, metric=m, **summarize(g1[(p, c)][m]))
                                     for p in projects for c in cells1 if (p, c) in g1 for m in METRICS]))

    if a.phase2:
        runs2, metas2 = load(a.phase2)
        g2 = group(runs2, lambda r: f'{r["config"]}/{r["variant"]}')
        jit = [c["id"] for c in metas2[-1]["configs"]]
        cells2 = [f"{c}/{v}" for c in jit for v in ("seq", "par")]
        p2 = [p for p in projects if any((p, c) in g2 for c in cells2)]
        # speedup of par over seq within each JIT config: median(par)/median(seq) - 1 (negative = faster)
        par_eff = []
        for c in jit:
            r = compare(g2, f"{c}/seq", [f"{c}/seq", f"{c}/par"], p2, rng)
            for x in r["per_project"] + r["total"]:
                x["jit"] = c
            par_eff.append(r)
        # JIT config effect within each variant
        jit_eff = {}
        for v in ("par", "seq"):
            jit_eff[v] = compare(g2, f"baseline/{v}", [f"{c}/{v}" for c in jit], p2, rng)
        data["phase2"] = dict(jit=jit, cells=cells2, meta=metas2[-1], runs=runs2, projects=p2,
                              par_eff=dict(per_project=[x for r in par_eff for x in r["per_project"]],
                                           total=[x for r in par_eff for x in r["total"]]),
                              jit_eff=jit_eff,
                              summary=[dict(project=p, cell=c, metric=m, **summarize(g2[(p, c)][m]))
                                       for p in p2 for c in cells2 if (p, c) in g2 for m in METRICS])

    (out / "data.json").write_text(json.dumps(data, indent=0))
    write_md(data, out, colors)
    tpl = (ROOT / "scripts" / "report_template.html").read_text()
    (out / "report.html").write_text(tpl.replace("/*__DATA__*/null", json.dumps(data)))
    print(f"wrote {out}/report.md, report.html, data.json")


def write_md(data, out, colors):
    p1 = data["phase1"]; meta = p1["meta"]; host = meta["host"]
    L = []
    L.append("# F# compiler (fsc): JIT flags benchmark\n")
    L.append(f"_Generated {data['generated']} from `results/` by `scripts/report.py`. "
             f"Interactive version: [report.html](report.html)._\n")
    lay = next(iter(meta.get("layouts", {}).values()), {})
    L.append("## Setup\n")
    L.append(f"- Machine: {host['cpu']}, {host['ncpu']} logical CPUs, {int(host['mem_bytes']) // 2**30} GB, macOS {host['os']}, AC power")
    L.append(f"- Compiler: dotnet/fsharp `main` @ `{COMMIT}`, Release, ReadyToRun via crossgen2 the way the .NET SDK ships it, "
             f"runtime {lay.get('runtime', '?')}, Server GC")
    L.append(f"- Workload: every fsc invocation of a full `dotnet build FSharp.Compiler.Service.slnx -c Release`, replayed in isolation "
             f"with the identical response file. Outputs go to a scratch dir.")
    L.append(f"- {meta['runs']} measured runs + {meta['warmup']} warm-up per (project, config). Configs are interleaved in shuffled order every round.")
    L.append(f"- Numbers are medians. Brackets are the 95% bootstrap CI of the change. **Bold** = CI excludes 0.\n")
    L.append("| config | meaning |\n|---|---|")
    for c in p1["cells"]:
        L.append(f"| `{c}` | {data['config_desc'].get(c, '')} |")
    L.append("")

    L.append("## Phase 1: JIT flags (parallel compilation on, as in the real build)\n")
    L.append("### Whole solution (sum of per-project medians)\n")
    L.append("| config | wall | CPU |\n|---|---|---|")
    tot = {(t["cell"], t["metric"]): t for t in p1["cmp"]["total"]}
    bt = {m: next(t["ref_total"] for t in p1["cmp"]["total"] if t["metric"] == m) for m in ("wall_s", "cpu_s")}
    L.append(f"| `baseline` | {bt['wall_s']:.1f} s | {bt['cpu_s']:.1f} s |")
    for c in p1["cells"]:
        if c == "baseline":
            continue
        w = tot[(c, "wall_s")]; u = tot[(c, "cpu_s")]
        L.append(f"| `{c}` | {w['total']:.1f} s, {fmt_pct(w['delta'], w['lo'], w['hi'], w['sig'])} "
                 f"| {u['total']:.1f} s, {fmt_pct(u['delta'], u['lo'], u['hi'], u['sig'])} |")
    L.append("")
    cells = [c for c in p1["cells"] if c != "baseline"]
    pp = defaultdict(dict)
    for x in p1["cmp"]["per_project"]:
        pp[(x["project"], x["metric"])][x["cell"]] = x
    base = {(s["project"], s["metric"]): s for s in p1["summary"] if s["cell"] == "baseline"}
    for m, (label, unit) in METRICS.items():
        rows = [(p, {c: (pp[(p, m)][c]["delta"], pp[(p, m)][c]["lo"], pp[(p, m)][c]["hi"], pp[(p, m)][c]["sig"])
                     for c in cells if c in pp[(p, m)]}) for p in data["projects"]]
        fn = f"img/phase1_{m}.svg"
        (out / fn).write_text(forest_svg(rows, cells, f"{label}: change vs baseline (filled = significant)", colors))
        L.append(f"### {label}\n")
        L.append(f"![{label} change vs baseline]({fn})\n")
        L.append("| project | baseline median (" + unit + ") | " + " | ".join(f"`{c}`" for c in cells) + " |")
        L.append("|---|---:|" + "---:|" * len(cells))
        for p in data["projects"]:
            b = base.get((p, m))
            cols = []
            for c in cells:
                x = pp[(p, m)].get(c)
                cols.append(fmt_pct(x["delta"], x["lo"], x["hi"], x["sig"]) if x else "")
            L.append(f"| {p} | {fmt_val(b['median'], m) if b else ''} ± {fmt_val(b['sd'], m) if b else ''} | " + " | ".join(cols) + " |")
        L.append("")

    if "phase2" in data:
        p2 = data["phase2"]
        L.append("## Phase 2: parallel compilation on/off, by JIT config\n")
        L.append("`par` = `--parallelcompilation+` (as captured), `seq` = `--parallelcompilation-`. "
                 "Change = median(par)/median(seq) − 1, so negative means parallel compilation is faster.\n")
        L.append("### Whole solution\n")
        L.append("| JIT config | seq wall | par wall | par vs seq wall | par vs seq CPU |\n|---|---:|---:|---|---|")
        tt = {(t["jit"], t["metric"]): t for t in p2["par_eff"]["total"]}
        for c in p2["jit"]:
            w = tt[(c, "wall_s")]; u = tt[(c, "cpu_s")]
            L.append(f"| `{c}` | {w['ref_total']:.1f} s | {w['total']:.1f} s | {fmt_pct(w['delta'], w['lo'], w['hi'], w['sig'])} "
                     f"| {fmt_pct(u['delta'], u['lo'], u['hi'], u['sig'])} |")
        L.append("")
        L.append("JIT config effect within each mode (whole solution, vs `baseline` in the same mode):\n")
        L.append("| mode | " + " | ".join(f"`{c}` wall" for c in p2["jit"] if c != "baseline") + " |")
        L.append("|---|" + "---|" * (len(p2["jit"]) - 1))
        for v in ("seq", "par"):
            t = {x["cell"]: x for x in p2["jit_eff"][v]["total"] if x["metric"] == "wall_s"}
            L.append(f"| {v} | " + " | ".join(fmt_pct(t[f'{c}/{v}']['delta'], t[f'{c}/{v}']['lo'], t[f'{c}/{v}']['hi'], t[f'{c}/{v}']['sig'])
                                               for c in p2["jit"] if c != "baseline") + " |")
        L.append("")
        pe = defaultdict(dict)
        for x in p2["par_eff"]["per_project"]:
            pe[(x["project"], x["metric"])][x["jit"]] = x
        pcol = {"baseline": "#898781"}
        for i, c in enumerate([c for c in p2["jit"] if c != "baseline"]):
            pcol[c] = colors.get(c, SLOTS[i])
        for m in ("wall_s", "cpu_s"):
            label = METRICS[m][0]
            rows = [(p, {c: (pe[(p, m)][c]["delta"], pe[(p, m)][c]["lo"], pe[(p, m)][c]["hi"], pe[(p, m)][c]["sig"])
                         for c in p2["jit"] if c in pe[(p, m)]}) for p in p2["projects"]]
            fn = f"img/phase2_{m}.svg"
            (out / fn).write_text(forest_svg(rows, p2["jit"], f"{label}: parallel vs sequential compilation, per JIT config",
                                             pcol, "median(par)/median(seq) - 1 (%)"))
            L.append(f"### {label}: par vs seq\n")
            L.append(f"![{label} par vs seq]({fn})\n")
            L.append("| project | " + " | ".join(f"`{c}`" for c in p2["jit"]) + " |")
            L.append("|---|" + "---:|" * len(p2["jit"]))
            for p in p2["projects"]:
                L.append(f"| {p} | " + " | ".join(
                    fmt_pct(pe[(p, m)][c]["delta"], pe[(p, m)][c]["lo"], pe[(p, m)][c]["hi"], pe[(p, m)][c]["sig"])
                    if c in pe[(p, m)] else "" for c in p2["jit"]) + " |")
            L.append("")

    L.append(Path(ROOT / "reports" / "findings.md").read_text() if (ROOT / "reports" / "findings.md").exists() else "")
    L.append("## Reproduce\n")
    L.append("See [README.md](../README.md). Raw runs are in `results/*/runs.jsonl` and run metadata in `results/*/meta-*.json`.\n")
    (out / "report.md").write_text("\n".join(L) + "\n")


COMMIT = "?"
if __name__ == "__main__":
    import sys
    cf = ROOT / "results" / "compiler_commit.txt"
    if cf.exists():
        COMMIT = cf.read_text().strip()
    main()
