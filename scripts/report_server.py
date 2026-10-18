#!/usr/bin/env python3
"""Results page for the F# compiler server benchmark (scripts/serverbench.py).

Reads results/server/runs.jsonl (the last row per config+run wins) and writes reports/server/results.html, styled like
reports/server/plan.html. Everything shown is computed from the rows: wall/CPU per build, speedups of the measured runs,
output identity against the first stock build, and diagnostics identity.
"""
import html, json, statistics, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results" / "server" / "runs.jsonl"
OUT = ROOT / "reports" / "server" / "results.html"
ORDER = ["baseline", "flags", "server", "server_long"]
LABEL = {"baseline": "baseline (stock fsc)", "flags": "baseline + JIT flags", "server": "compiler server",
         "server_long": "compiler server, longer sequence"}
COLOR = {"baseline": "var(--c1)", "flags": "var(--c2)", "server": "var(--c3)", "server_long": "var(--c4)"}


def load():
    rows = {}
    for line in RES.read_text().splitlines():
        r = json.loads(line)
        rows[(r["config"], r["run"])] = r
    return rows


def fmt(x, d=1):
    return f"{x:.{d}f}"


def main():
    rows = load()
    configs = [c for c in ORDER if any(k[0] == c for k in rows)]
    ref = rows.get(("baseline", "warmup"))
    if ref is None:
        sys.exit("no baseline warmup row")

    def runs(c):
        return sorted((r for k, r in rows.items() if k[0] == c and not r["warm"]), key=lambda r: r["run"])

    # Every hash any stock build produced, per file: stock itself is not deterministic for some outputs.
    stock_variants = {}
    for k, r in rows.items():
        if k[0] in ("baseline", "flags"):
            for f, h in r["outputs"].items():
                if h is not None:
                    stock_variants.setdefault(f, set()).add(h)
    unstable = sorted(f for f, hs in stock_variants.items() if len(hs) > 1)

    mean_wall = {c: statistics.mean(r["wall_s"] for r in runs(c)) for c in configs}
    mean_cpu = {c: statistics.mean(r["cpu_s"] for r in runs(c)) for c in configs}

    # Build table
    trs = []
    for c in configs:
        for r in [rows[(c, "warmup")]] + runs(c):
            present = {f: h for f, h in r["outputs"].items() if f in stock_variants}
            same = sum(1 for f, h in present.items() if h == ref["outputs"][f])
            total = len(stock_variants)
            diffs = [f for f, h in present.items() if h != ref["outputs"][f] and h in stock_variants[f]]
            new = [f for f, h in present.items() if h not in stock_variants[f]]
            diag_same = r["diagnostics"] == ref["diagnostics"]
            ok, bad = '<span class="pill p-ok">', '<span class="pill p-bad">'
            if new:
                new_cell = bad + str(len(new)) + "</span><br><small>" + "<br>".join(html.escape(d) for d in new) + "</small>"
            else:
                new_cell = ok + "none</span>"
            if diffs:
                new_cell += "<br><small>other stock variant: " + ", ".join(html.escape(Path(d).name) for d in diffs) + "</small>"
            diag_cell = ok + "same</span>" if diag_same else bad + "differs</span>"
            trs.append(
                f"<tr><td>{LABEL[c]}</td><td>{r['run']}</td><td class=n>{fmt(r['wall_s'])}</td><td class=n>{fmt(r['cpu_s'])}</td>"
                f"<td class=n>{fmt(r['worker_cpu_s'])}</td><td class=n>{r['workers_alive']}</td><td class=n>{r['new_workers']}</td>"
                f"<td class=n>{fmt(r['worker_rss_mb'], 0)}</td><td class=n>{same}/{total}</td>"
                f"<td>{new_cell}</td><td>{diag_cell}</td></tr>")

    # Speedup table
    srows = []
    for c in configs:
        srows.append(
            f"<tr><td>{LABEL[c]}</td><td class=n>{fmt(mean_wall[c])}</td><td class=n>{fmt(mean_cpu[c])}</td>"
            f"<td class=n>{fmt(mean_wall['baseline'] / mean_wall[c], 2)}×</td>"
            f"<td class=n>{fmt(100 * (mean_wall[c] / mean_wall['baseline'] - 1))}%</td>"
            + (f"<td class=n>{fmt(mean_wall['flags'] / mean_wall[c], 2)}×</td>" if 'flags' in mean_wall else "<td></td>")
            + f"<td class=n>{fmt(100 * (mean_cpu[c] / mean_cpu['baseline'] - 1))}%</td></tr>")

    # Chart: horizontal bars of wall time per build
    bar_h, gap, left, width = 22, 6, 300, 560
    items = []
    for c in configs:
        for r in [rows[(c, "warmup")]] + runs(c):
            items.append((c, r))
    vmax = max(r["wall_s"] for _, r in items) * 1.1
    h = len(items) * (bar_h + gap) + 40
    bars = []
    for i, (c, r) in enumerate(items):
        y = 10 + i * (bar_h + gap)
        w = r["wall_s"] / vmax * width
        op = "0.45" if r["warm"] else "1"
        bars.append(
            f'<text x="{left - 8}" y="{y + 15}" text-anchor="end">{html.escape(LABEL[c])} · {r["run"]}</text>'
            f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="3" style="fill:{COLOR[c]};opacity:{op}"/>'
            f'<text x="{left + w + 6:.1f}" y="{y + 15}" class="m">{fmt(r["wall_s"])} s</text>')
    ticks = []
    step = 30 if vmax > 120 else 10
    t = 0
    while t <= vmax:
        x = left + t / vmax * width
        ticks.append(f'<line x1="{x:.1f}" y1="6" x2="{x:.1f}" y2="{h - 26}" class="grid"/>'
                     f'<text x="{x:.1f}" y="{h - 10}" text-anchor="middle" class="m">{t}s</text>')
        t += step
    chart = f'<svg viewBox="0 0 {left + width + 80} {h}" role="img" aria-label="Wall time per build">{"".join(ticks)}{"".join(bars)}</svg>'

    meta = sorted((ROOT / "results" / "server").glob("meta-*.json"))
    m = json.loads(meta[-1].read_text()) if meta else {}
    hw = json.loads((ROOT / "results" / "hardware.json").read_text()) if (ROOT / "results" / "hardware.json").exists() else {}
    hw_text = html.escape(", ".join(f"{k}: {v}" for k, v in hw.items() if isinstance(v, (str, int, float)))[:400])

    srv = mean_wall.get("server")
    headline = ""
    if srv:
        headline = (f"<p class=lead>Whole build of <code>FSharp.Compiler.Service.slnx</code>, mean of the measured runs: "
                    f"compiler server <b>{fmt(srv)} s</b> vs stock <b>{fmt(mean_wall['baseline'])} s</b> "
                    f"(<b>{fmt(mean_wall['baseline'] / srv, 2)}×</b>)"
                    + (f" and vs stock + JIT flags <b>{fmt(mean_wall['flags'])} s</b> (<b>{fmt(mean_wall['flags'] / srv, 2)}×</b>)"
                       if "flags" in mean_wall else "") + ".</p>")

    total_outputs = len(stock_variants)
    served = ROOT / "results" / "server" / "served.json"
    served_html = ""
    if served.exists():
        sv = json.loads(served.read_text())
        served_html = "<h2>Who compiled what</h2><p>From the build logs (binlog replay): compilations served by a worker, compilations that fell back to a stock process, and how the 17 compilations spread over workers.</p><div class=tw><table><tr><th>Build</th><th>by a worker</th><th>stock fallback</th><th>per worker</th></tr>" + "".join(
            f"<tr><td>{html.escape(k)}</td><td class=n>{v['served']}</td><td class=n>{v['fallback']}</td><td>{' · '.join(str(n) for n in v['per_worker'])}</td></tr>" for k, v in sv.items()) + "</table></div>"
    findings = ROOT / "reports" / "server" / "findings.html"
    findings_html = findings.read_text() if findings.exists() else ""
    template = (ROOT / "reports" / "server" / "plan.html").read_text()
    style = template[template.index("<style>"):template.index("</style>") + 8]
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compiler Server Results</title>
{style}
<style>
:root{{--c1:#8a8f98;--c2:#d08a2c;--c3:#2f8f5b;--c4:#5b8fd0}}
td.n{{text-align:right;font-variant-numeric:tabular-nums}}
svg .grid{{stroke:var(--line);stroke-width:1}}
.lead{{font-size:16.5px}}
main{{max-width:1100px;margin:0 auto}} a{{color:var(--accent)}}
</style></head>
<body><main>
<h1>F# Compiler Server: results</h1>
<p class=sub>Compiler <code>repos/server</code> @ {html.escape(m.get('server_commit', '')[:10])}, built solution: dotnet/fsharp
<code>main</code> @ {html.escape(m.get('commit', '')[:10])}. Recorded {html.escape(m.get('start', ''))}. Plan: <a href="plan.html">plan.html</a>.</p>
{headline}
{findings_html}
<h2>Wall time per build</h2>
<figure>{chart}<figcaption>Faded bars are the warm-up builds. For the server, the warm-up build is the cold start: it
starts the workers, which then JIT the compiler. Each config ran 1 warm-up and {len(runs('baseline'))} measured builds,
sequentially, with MSBuild node reuse off. The longer server sequence (1 warm-up and {len(runs('server_long')) if 'server_long' in configs else 0}
measured builds) is an extra run, beyond the requested protocol.</figcaption></figure>

<h2>Speedup (measured runs)</h2>
<div class=tw><table><tr><th>Config</th><th>wall s</th><th>CPU s</th><th>speedup vs stock</th><th>Δ wall vs stock</th>
<th>speedup vs stock + flags</th><th>Δ CPU vs stock</th></tr>{''.join(srows)}</table></div>

<h2>Every build</h2>
<p>Outputs: the assembly (with its embedded PDB), the reference assembly and the XML doc of all 17 compilations, {total_outputs} files,
compared by SHA-256. Stock builds are not deterministic for {len(unstable)} of them ({', '.join(html.escape(Path(f).parent.parent.name + '/' + Path(f).parent.name + '/' + Path(f).name) for f in unstable)}),
so a build passes when every file matches a hash some stock build produced. Diagnostics: the distinct
<code>warning/error FS</code> lines of the build log.</p>
<div class=tw><table><tr><th>Config</th><th>Build</th><th>wall s</th><th>CPU s</th><th>worker CPU s</th><th>workers alive</th>
<th>workers started</th><th>worker RSS MB</th><th>identical to 1st stock build</th><th>outputs no stock build produced</th><th>diagnostics</th></tr>{''.join(trs)}</table></div>

{served_html}
<h2>Method</h2>
<ul>
<li>Command: <code>dotnet build FSharp.Compiler.Service.slnx -c Release --no-restore --no-incremental -nodeReuse:false</code>,
compiler, FSharp.Build and targets from a layout via <code>FSharpTargetsPath</code>, <code>FSharpPropsShim</code>,
<code>FSharpTargetsShim</code>, <code>FSharpOverridesTargetsShim</code>.</li>
<li>Stock and flags use <code>layouts/baseline</code> (main, ReadyToRun, SDK runtimeconfig). Flags are applied to the fsc processes only, through
an <code>FscToolExe</code> wrapper. The server uses <code>layouts/server</code> (the same R2R build of the server branch) with
<code>FSharpUseSharedCompilation=true</code>; workers run default JIT settings, Server GC and GCHeapCount = max(1, cores/2).</li>
<li>CPU: rusage of the build process tree plus the CPU that live workers used during the build (ps before and after).</li>
<li>Hardware: {hw_text}</li>
</ul>
</main></body></html>
"""
    OUT.write_text(page)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
