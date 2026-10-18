#!/usr/bin/env python3
"""Summarize results/server_fsc/runs.jsonl (scripts/fscbench.py).

Per project and config (measured rounds only; the last run of a config+tag wins if a config was re-run):
median wall and CPU, ratio vs flags and stock, median phase durations, worker GC stats, and output identity
(every config's output hashes compared with stock's).

Usage: analyze_fscbench.py [--tags e1,e2] [--phases] [--json out.json]
"""
import argparse, collections, json, statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHORT = {"FSharp.Compiler.Service__net11.0": "FCS", "FSharp.Core__netstandard2.0": "Core",
         "FSharp.Compiler.ComponentTests__net11.0": "CompTests", "FSharp.Test.Utilities__net11.0": "TestUtils",
         "FSharp.Build__netstandard2.0": "Build", "fsc__net11.0": "fsc"}
PHASES = ["Import mscorlib+FSharp.Core", "Parse inputs", "Import non-system references", "Typecheck",
          "Optimizations", "TAST -> IL", "Write .NET Binary"]


def load(path, tags):
    rows = [json.loads(l) for l in open(path)]
    if tags:
        rows = [r for r in rows if r.get("tag") in tags]
    # keep only the latest batch per config: a batch = rows with the same config and tag, latest ts wins
    latest = {}
    for r in rows:
        k = (r["config"], r.get("tag"))
        latest[k] = max(latest.get(k, 0), r["ts"])
    newest_tag = {}
    for (c, t), ts in latest.items():
        if c not in newest_tag or ts > latest[(c, newest_tag[c])]:
            newest_tag[c] = t
    return [r for r in rows if r.get("tag") == newest_tag[r["config"]]]


def summarize(rows):
    g = collections.defaultdict(list)
    for r in rows:
        if not r["warm"]:
            g[(r["config"], r["project"])].append(r)
    out = {}
    for (c, p), rs in g.items():
        out[(c, p)] = dict(
            n=len(rs),
            wall=st.median(r["wall_s"] for r in rs),
            wall_min=min(r["wall_s"] for r in rs),
            wall_max=max(r["wall_s"] for r in rs),
            cpu=st.median(r["cpu_s"] for r in rs),
            phases={ph: st.median(r["phases"].get(ph, 0.0) for r in rs) for ph in PHASES},
            gc_pause=st.median(r["stats"].get("gcPauseMs", 0) for r in rs) if rs[0]["stats"] else None,
            alloc=st.median(r["stats"].get("allocMB", 0) for r in rs) if rs[0]["stats"] else None,
            gc0=st.median(r["times_last"]["gc0"] for r in rs if r.get("times_last")) if rs[0].get("times_last") else None,
            rss=st.median(r["maxrss_mb"] for r in rs),
            outputs={k for r in rs for k in [json.dumps(r["outputs"], sort_keys=True)]},
        )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "results" / "server_fsc" / "runs.jsonl"))
    ap.add_argument("--tags", default="")
    ap.add_argument("--phases", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    rows = load(a.file, set(a.tags.split(",")) if a.tags else None)
    s = summarize(rows)
    configs = list(dict.fromkeys(r["config"] for r in rows))
    projects = list(dict.fromkeys(r["project"] for r in rows))
    print(f"{'config':16s}" + "".join(f"{SHORT.get(p, p):>22s}" for p in projects) + f"{'sum wall':>10s}{'sum cpu':>9s}")
    for c in configs:
        cells, sw, sc = [], 0, 0
        for p in projects:
            x = s.get((c, p))
            if not x:
                cells.append(f"{'-':>22s}")
                continue
            sw += x["wall"]
            sc += x["cpu"]
            ref = s.get(("flags", p))
            rel = f" {x['wall'] / ref['wall']:.2f}xF" if ref else ""
            cells.append(f"{x['wall']:7.2f}/{x['cpu']:6.2f}{rel:>8s}")
        print(f"{c:16s}" + "".join(cells) + f"{sw:10.2f}{sc:9.2f}")
    print("cells: median wall s / median CPU s, then wall relative to flags")
    # output identity vs stock
    for p in projects:
        ref = s.get(("stock", p))
        if not ref:
            continue
        bad = [c for c in configs if (c, p) in s and not s[(c, p)]["outputs"] <= ref["outputs"] | s.get(("flags", p), {}).get("outputs", set())]
        if bad:
            print(f"OUTPUT DIFFERS from stock for {SHORT.get(p, p)}: {bad}")
    if a.phases:
        for p in projects:
            print(f"\n{SHORT.get(p, p)} phases (median s), gc pause ms, alloc MB, gen0, rss MB")
            print(f"{'config':16s}" + "".join(f"{ph[:12]:>13s}" for ph in PHASES) + f"{'gcpause':>9s}{'allocMB':>9s}{'gen0':>6s}{'rss':>7s}")
            for c in configs:
                x = s.get((c, p))
                if not x:
                    continue
                print(f"{c:16s}" + "".join(f"{x['phases'][ph]:13.3f}" for ph in PHASES)
                      + f"{(x['gc_pause'] or 0):9.0f}{(x['alloc'] or 0):9.0f}{(x['gc0'] or 0):6.0f}{x['rss']:7.0f}")
    if a.json:
        Path(a.json).write_text(json.dumps({f"{c}|{p}": {k: v for k, v in x.items() if k != "outputs"} for (c, p), x in s.items()}, indent=1))


if __name__ == "__main__":
    main()
