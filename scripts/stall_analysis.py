#!/usr/bin/env python3
"""For each server build of a serverbench run: replay its binlog to a diag log, then list every compilation's
client-side time vs the worker's own compilation time (from the Fsc task's low-importance message), and the slowest
projects from MSBuild's project performance summary.

Usage: stall_analysis.py <out-name, e.g. server_stall2> [--config server]
"""
import os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "repos" / "baseline" / ".dotnet" / "dotnet"
LOGS = ROOT / "work" / "serverbench_logs"
MSG = re.compile(r"Compiled by F# compiler server worker (\d+) in (\d+) ms \(compilation (-?\d+) ms\)")


def main():
    name = sys.argv[1]
    cfg = sys.argv[2] if len(sys.argv) > 2 else "server"
    env = dict(os.environ, DOTNET_ROOT=str(D.parent))
    for bl in sorted(LOGS.glob(f"{name}_{cfg}_*.binlog")):
        diag = bl.with_suffix(".diag.log")
        if not diag.exists():
            subprocess.run([str(D), "msbuild", str(bl), "-noconlog", f"-flp:v=diag;logfile={diag};performancesummary"],
                           env=env, capture_output=True)
        text = diag.read_text(errors="replace")
        comps = [(int(p), int(c), int(w)) for p, c, w in MSG.findall(text)]
        gaps = sorted(((c - w, c, w, p) for p, c, w in comps), reverse=True)
        tot = re.search(r"Time Elapsed (\S+)", text)
        print(f"== {bl.stem}: {len(comps)} compilations, elapsed {tot.group(1) if tot else '?'}; "
              f"largest client-minus-worker gaps (ms): {[g[0] for g in gaps[:4]]}; longest compilations (ms): "
              f"{sorted((w for _, _, w in comps), reverse=True)[:4]}")
        perf = text.split("Project Performance Summary:", 1)[-1].split("Target Performance Summary", 1)[0]
        rows = sorted(((int(m.group(1)), m.group(2)) for m in re.finditer(r"^\s+(\d+) ms\s+(\S+\.fsproj)", perf, re.M)), reverse=True)
        print("   slowest projects:", [(ms, Path(p).stem) for ms, p in rows[:4]])


if __name__ == "__main__":
    main()
