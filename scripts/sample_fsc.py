#!/usr/bin/env python3
"""Native sampling (macOS `sample`) of one compilation: a stock fsc process vs a warm compiler server worker.

`sample` takes stacks of every thread every 1 ms and reports the top-of-stack functions with their binary. Samples are
grouped into: JIT (libclrjit), GC (libcoreclr gc_heap/GCHeap functions), other runtime (rest of libcoreclr),
managed code (frames sample cannot symbolize: JIT'd or R2R code), waiting (kernel wait/sleep primitives) and other.

Usage: sample_fsc.py --project FCS --name flags --mode process [--env K=V,...]
       sample_fsc.py --project FCS --name srv --mode worker --warm 2 [--env K=V,...]
"""
import argparse, collections, json, os, re, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fscbench import PROJECTS, Worker, prepare, clean_env, DOTNET, ROOT  # noqa: E402

WAIT = re.compile(r"wait|_nocancel|mach_msg|__workq_kernreturn|kevent|__select|__sigsuspend|__accept")


def classify(func, binary):
    if WAIT.search(func):
        return "waiting"
    if "libclrjit" in binary:
        return "jit"
    if "libcoreclr" in binary:
        if re.search(r"gc_heap|GCHeap|SVR::|WKS::|gc_mechanisms|mark_|plan_phase|relocate|compact|sweep|card", func):
            return "gc"
        return "runtime"
    if binary in ("", "???", "<unknown binary>") or func.startswith("???"):
        return "managed"
    return "other:" + binary


LINE = re.compile(r"^([\s+!:|]*?)(\d+) (.*?)\s+\(in (.*?)\)")


def parse(path):
    """Self samples per function from the call graph: a node's count minus its children's counts."""
    text = Path(path).read_text(errors="replace")
    graph = text.split("Call graph:", 1)[1].split("Total number in stack", 1)[0]
    cats, selfs = collections.Counter(), collections.Counter()
    stack = []  # (depth, count, key, child_sum)

    def close(entry):
        depth, count, key, child = entry
        n = count - child
        if n > 0:
            selfs[key] += n

    for line in graph.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        depth, count, func, binary = len(m.group(1)), int(m.group(2)), m.group(3).strip(), m.group(4)
        while stack and stack[-1][0] >= depth:
            close(stack.pop())
        if stack:
            stack[-1][3] += count
        stack.append([depth, count, (func, binary), 0])
    while stack:
        close(stack.pop())
    rows = []
    for (func, binary), n in selfs.items():
        c = classify(func, binary)
        cats[c] += n
        rows.append((n, c, func, binary))
    return cats, rows


def parse_top_of_stack(path):
    """Top-of-stack section: lines like '        1234       func  (in binary)' or '???  (in <unknown binary>)'."""
    text = Path(path).read_text(errors="replace")
    sec = text.split("Sort by top of stack, same collapsed (when >= 5):", 1)
    if len(sec) < 2:
        return {}, []
    cats, rows = collections.Counter(), []
    for line in sec[1].splitlines():
        m = re.match(r"\s+(.*?)\s+\(in (.*?)\)(?:\s+\[0x[0-9a-f]+\])?\s+(\d+)\s*$", line)
        if not m:
            continue
        func, binary, n = m.group(1), m.group(2), int(m.group(3))
        c = classify(func, binary)
        cats[c] += n
        rows.append((n, c, func, binary))
    return cats, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--mode", choices=["process", "worker"], required=True)
    ap.add_argument("--env", default="")
    ap.add_argument("--warm", type=int, default=2)
    ap.add_argument("--duration", type=int, default=15, help="worker mode: seconds to sample (cover the compilation)")
    ap.add_argument("--out", default=str(ROOT / "work" / "samples"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pid = PROJECTS[a.project]
    env_over = dict(kv.split("=", 1) for kv in a.env.split(",") if kv)
    cwd, rsp, _ = prepare(pid, "sample_" + a.name, ROOT / "work" / "fscbench_out")
    sfile = out / f"{a.name}_{a.project}.txt"
    if a.mode == "process":
        p = subprocess.Popen([str(DOTNET), str(ROOT / "layouts" / "baseline" / "fsc.dll"), f"@{rsp}"], cwd=cwd,
                             env=clean_env() | env_over, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        s = subprocess.run(["sample", str(p.pid), "120", "1", "-mayDie", "-file", str(sfile)], capture_output=True, text=True)
        p.wait()
    else:
        w = Worker("server", env_over, f"s{os.getpid()}")
        try:
            for i in range(a.warm):
                wall, _ = w.compile(cwd, str(rsp))
                print(f"warm {i}: {wall:.2f}s")
            sp = subprocess.Popen(["sample", str(w.proc.pid), str(a.duration), "1", "-file", str(sfile)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(0.5)
            wall, resp = w.compile(cwd, str(rsp))
            print(f"sampled worker compilation: {wall:.2f}s")
            sp.wait()
        finally:
            w.stop()
    cats, rows = parse(sfile)
    total = sum(v for k, v in cats.items() if k != "waiting")
    print(f"{a.name} {a.project}: busy samples {total} (1 ms each, summed over threads)")
    for k, v in cats.most_common():
        print(f"  {k:28s} {v:8d}  {100 * v / max(total, 1):5.1f}% of busy" if k != "waiting" else f"  {k:28s} {v:8d}")
    print("top busy functions:")
    for n, c, f, b in sorted((r for r in rows if r[1] != "waiting"), reverse=True)[:15]:
        print(f"  {n:7d} {c:10s} {f[:90]}  ({b})")
    (out / f"{a.name}_{a.project}.json").write_text(json.dumps(dict(cats=cats, total_busy=total)))


if __name__ == "__main__":
    main()
