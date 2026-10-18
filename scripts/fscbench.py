#!/usr/bin/env python3
"""Per-compilation benchmark: stock fsc processes vs compiler server workers, on captured compilations.

Process configs run `dotnet <layout>/fsc.dll @rsp` once per compilation. Server configs start their own worker(s)
(`dotnet layouts/<layout>/fsc.dll --compiler-server-worker ...`, with the same environment the Fsc task gives them,
plus the config's overrides) and send each compilation over the worker's socket as the Fsc task would.

Per config: 1 warm-up round, then N measured rounds; a round compiles every selected project once, in a fixed order.
Server policies: 'shared' = one worker compiles everything (a warm worker that has seen every project);
'dedicated' = one worker per project (affinity: a worker only ever compiles its project).

Recorded per compilation: wall (process: spawn to exit; server: request to response), CPU (process: rusage;
server: the worker's own TotalProcessorTime delta), the --times phase table (Duration per top-level phase, GC
counts), output hashes, and worker stats (GC pause, allocations).

Usage: fscbench.py --configs stock,flags,srv --projects FCS,Core,... [--rounds 5] [--out results/server_fsc]
"""
import argparse, hashlib, json, os, re, socket, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostmon  # noqa: E402
from bench import rewrite_rsp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOTNET = ROOT / "repos" / "baseline" / ".dotnet" / "dotnet"
CMDS = ROOT / "work" / "cmds" / "baseline"
FLAGS = {"DOTNET_TieredPGO": "0", "DOTNET_TC_CallCountingDelayMs": "0"}
NCPU = os.cpu_count()
HEAPS = max(1, NCPU // 2)

PROJECTS = {
    "FCS": "FSharp.Compiler.Service__net11.0",
    "Core": "FSharp.Core__netstandard2.0",
    "CompTests": "FSharp.Compiler.ComponentTests__net11.0",
    "TestUtils": "FSharp.Test.Utilities__net11.0",
    "Build": "FSharp.Build__netstandard2.0",
    "fsc": "fsc__net11.0",
}

WORKER_DEFAULT = {"DOTNET_gcServer": "1", "DOTNET_GCHeapCount": format(HEAPS, "x")}

# name -> (mode, layout, env overrides; for server: worker env overrides on top of WORKER_DEFAULT, None deletes)
CONFIGS = {
    "stock": ("process", "baseline", {}),
    "flags": ("process", "baseline", FLAGS),
    "flags_datasoff": ("process", "baseline", {**FLAGS, "DOTNET_GCDynamicAdaptationMode": "0"}),
    "srv": ("server", "server", {}),
    "srv_dedicated": ("server", "server", {}),
    "srv_datas": ("server", "server", {"DOTNET_GCHeapCount": None}),
    "srv_heaps_all": ("server", "server", {"DOTNET_GCHeapCount": format(NCPU, "x")}),
    "srv_heaps2": ("server", "server", {"DOTNET_GCHeapCount": "2"}),
    "srv_nonconc": ("server", "server", {"DOTNET_gcConcurrent": "0"}),
    "srv_wks": ("server", "server", {"DOTNET_gcServer": "0", "DOTNET_GCHeapCount": None}),
    "srv_flags": ("server", "server", FLAGS),
    "srv_pgo0": ("server", "server", {"DOTNET_TieredPGO": "0"}),
    "srv_r2r0": ("server", "server", {"DOTNET_ReadyToRun": "0"}),
    "srv_reuse": ("server", "server", {"FSHARP_COMPILER_SERVER_REUSE_CACHES": "1"}),
    "srv_batch": ("server", "server", {"FSHARP_COMPILER_SERVER_GC_LATENCY": "batch"}),
    "srv_sustained": ("server", "server", {"FSHARP_COMPILER_SERVER_GC_LATENCY": "sustained"}),
    "srv_noidlegc": ("server", "server", {"FSHARP_COMPILER_SERVER_NO_IDLE_GC": "1"}),
    "srv_heaps_all_batch": ("server", "server", {"DOTNET_GCHeapCount": format(NCPU, "x"),
                                                 "FSHARP_COMPILER_SERVER_GC_LATENCY": "batch"}),
    "srv_heaps_all_nonconc": ("server", "server", {"DOTNET_GCHeapCount": format(NCPU, "x"), "DOTNET_gcConcurrent": "0"}),
    "srv_datas_nonconc": ("server", "server", {"DOTNET_GCHeapCount": None, "DOTNET_gcConcurrent": "0"}),
    "srv_ccd0": ("server", "server", {"DOTNET_TC_CallCountingDelayMs": "0"}),
    "srv_ccd0_nonconc": ("server", "server", {"DOTNET_TC_CallCountingDelayMs": "0", "DOTNET_gcConcurrent": "0"}),
}


def clean_env():
    return {k: v for k, v in os.environ.items() if not k.startswith(("DOTNET_", "COMPlus_", "FSC_", "FSHARP_COMPILER"))} | {
        "DOTNET_ROOT": str(DOTNET.parent), "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}


TIMES_ROW = re.compile(r"^\|([^|>][^|]*?)\s*\|\s*([0-9.]+)\|\s*([0-9.]+)\|\s*(\d+)\|\s*(\d+)\|\s*(\d+)\|\s*(\d+)\|")


def parse_times(lines):
    """Top-level --times rows: phase -> duration (s), plus the last row's WS(MB) and GC counts."""
    phases, last = {}, None
    for l in lines:
        m = TIMES_ROW.match(l.strip())
        if m:
            name = m.group(1).strip()
            phases[name] = phases.get(name, 0.0) + float(m.group(3))
            last = dict(ws_mb=int(m.group(4)), gc0=int(m.group(5)), gc1=int(m.group(6)), gc2=int(m.group(7)))
    return phases, last


def prepare(pid, cfg, outroot):
    cap = json.loads((CMDS / f"{pid}.json").read_text())
    # One directory per project for every config: the output path is embedded in the assembly (the PDB path).
    od = outroot / pid
    od.mkdir(parents=True, exist_ok=True)
    lines = rewrite_rsp(cap["rsp"], od, "asis")
    # rewrite_rsp keeps file names, so -o and --refout would both land on <od>/<name>.dll; keep them apart
    lines = [l.replace(f"{od}/", f"{od}/ref_", 1) if l.startswith("--refout:") else l for l in lines]
    rsp = od / "args.rsp"
    rsp.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    outs = [Path(l.split(":", 1)[1]) for l in lines if l.startswith(("-o:", "--refout:"))]
    return cap["cwd"], rsp, outs


def hashes(outs):
    return {p.name:
            hashlib.sha256(p.read_bytes()).hexdigest()[:16] for p in outs if p.exists()}


class Worker:
    def __init__(self, layout, env_over, key):
        env = clean_env() | WORKER_DEFAULT
        for k, v in env_over.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        self.dir = Path(tempfile.gettempdir()) / "fsharp-compiler-server-bench" / key
        self.dir.mkdir(parents=True, exist_ok=True)
        self.key = key
        self.proc = subprocess.Popen([str(DOTNET), str(ROOT / "layouts" / layout / "fsc.dll"), "--compiler-server-worker",
                                      key, "0", str(self.dir)], env=env, cwd=self.dir, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.sock_path = os.path.join(tempfile.gettempdir(), f"CoreFxPipe_fsc-{key}-0")

    def compile(self, cwd, rsp):
        deadline = time.time() + 30
        while True:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(self.sock_path)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                s.close()
                if time.time() > deadline:
                    raise
                time.sleep(0.02)
        req = json.dumps({"v": 1, "type": "compile", "cwd": cwd, "args": [f"@{rsp}"]}) + "\n"
        t0 = time.perf_counter_ns()
        s.sendall(req.encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
        wall = (time.perf_counter_ns() - t0) / 1e9
        s.close()
        return wall, json.loads(buf)

    def rss_mb(self):
        r = subprocess.run(["ps", "-o", "rss=", "-p", str(self.proc.pid)], capture_output=True, text=True).stdout.strip()
        return int(r) / 1024 if r else 0.0

    def stop(self):
        self.proc.kill()
        self.proc.wait()


def run_process(layout, env_over, cwd, rsp):
    env = clean_env() | env_over
    t0 = time.perf_counter_ns()
    p = subprocess.Popen([str(DOTNET), str(ROOT / "layouts" / layout / "fsc.dll"), f"@{rsp}"], cwd=cwd, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout.read().decode(errors="replace")
    _, status, ru = os.wait4(p.pid, 0)
    wall = (time.perf_counter_ns() - t0) / 1e9
    return dict(rc=os.waitstatus_to_exitcode(status), wall_s=wall, cpu_s=ru.ru_utime + ru.ru_stime,
                maxrss_mb=ru.ru_maxrss / 2**20), out.splitlines()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", required=True)
    ap.add_argument("--projects", default="FCS,Core,CompTests,TestUtils,Build")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--out", default="results/server_fsc")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)
    outroot = ROOT / "work" / "fscbench_out"
    projs = [PROJECTS[p] for p in a.projects.split(",")]
    resf = (out / "runs.jsonl").open("a")
    for cfg in a.configs.split(","):
        mode, layout, env_over = CONFIGS[cfg]
        prepared = {pid: prepare(pid, cfg, outroot) for pid in projs}
        workers = {}
        if mode == "server":
            if cfg.endswith("_dedicated"):
                workers = {pid: Worker(layout, env_over, f"b{abs(hash((cfg, pid))) % 10**8}") for pid in projs}
            else:
                w = Worker(layout, env_over, f"b{abs(hash(cfg)) % 10**8}")
                workers = {pid: w for pid in projs}
        try:
            for rnd in range(a.warmup + a.rounds):
                warm = rnd < a.warmup
                for pid in projs:
                    cwd, rsp, outs = prepared[pid]
                    pre, waited = hostmon.wait_quiet(1.0, 0.5, 30.0)
                    if mode == "process":
                        r, lines = run_process(layout, env_over, cwd, rsp)
                        stats = {}
                    else:
                        w = workers[pid]
                        wall, resp = w.compile(cwd, str(rsp))
                        lines = [t for _, t in resp["lines"]]
                        stats = resp.get("stats", {})
                        r = dict(rc=resp["exitCode"], wall_s=wall, cpu_s=stats.get("cpuMs", 0) / 1000,
                                 maxrss_mb=w.rss_mb(), retire=resp["retire"])
                    phases, last = parse_times(lines)
                    row = dict(tag=a.tag, config=cfg, project=pid, round=rnd, warm=warm, ts=time.time(), pre_busy=pre,
                               phases=phases, times_last=last, stats=stats, outputs=hashes(outs), **r)
                    resf.write(json.dumps(row) + "\n")
                    resf.flush()
                    print(f"{cfg:14s} {pid:42s} r{rnd}{'w' if warm else ' '} rc={r['rc']} wall={r['wall_s']:6.2f}s "
                          f"cpu={r['cpu_s']:6.2f}s rss={r['maxrss_mb']:6.0f}MB", flush=True)
                    if r["rc"] != 0:
                        print("\n".join(lines[-30:]))
                        sys.exit(f"compilation failed: {cfg} {pid}")
        finally:
            for w in set(workers.values()):
                w.stop()


if __name__ == "__main__":
    main()
