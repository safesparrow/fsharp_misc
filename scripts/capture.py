#!/usr/bin/env python3
"""Capture the exact fsc command line of every F# project in FSharp.Compiler.Service.slnx.

Runs one full (non-incremental) `dotnet build` of the solution with the repo's pinned SDK, with
the Fsc task's host swapped for scripts/fsc-capture-wrapper.sh. The wrapper records cwd, argv,
the response file and DOTNET_* env, then runs the real compiler, so the build completes normally
and every referenced obj/ output exists afterwards.

Writes <out>/<project>__<tfm>.json = {id, project, tfm, cwd, compiler, rsp: [lines], env}.

Usage: capture.py <clone_dir> <out_dir> [--no-build] [--slnx FSharp.Compiler.Service.slnx]
"""
import argparse, json, os, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clone")
    ap.add_argument("out")
    ap.add_argument("--slnx", default="FSharp.Compiler.Service.slnx")
    ap.add_argument("--config", default="Release")
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args()

    clone = Path(a.clone).resolve()
    out = Path(a.out).resolve()
    raw = out / "_raw"
    dotnet = clone / ".dotnet" / "dotnet"
    if not a.no_build:
        if raw.exists():
            shutil.rmtree(raw)
        raw.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DOTNET_", "COMPlus_", "MSBuild"))}
        env.update(FSC_CAPTURE_DIR=str(raw), FSC_CAPTURE_DOTNET=str(dotnet), DOTNET_ROOT=str(dotnet.parent),
                   PATH=f"{dotnet.parent}:{env['PATH']}", DOTNET_CLI_TELEMETRY_OPTOUT="1",
                   DOTNET_NOLOGO="1", MSBUILDDISABLENODEREUSE="1")
        subprocess.run([str(dotnet), "build-server", "shutdown"], env=env, cwd=clone, capture_output=True)
        cmd = [str(dotnet), "build", a.slnx, "-c", a.config, "--no-incremental", "-nodeReuse:false",
               f"-p:FscToolPath={ROOT / 'scripts'}", "-p:FscToolExe=fsc-capture-wrapper.sh",
               f"-bl:{out / 'capture.binlog'}"]
        print("+", " ".join(cmd), flush=True)
        log = out / "capture-build.log"
        with log.open("w") as lf:
            r = subprocess.run(cmd, cwd=clone, env=env, stdout=lf, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            print(log.read_text()[-4000:])
            sys.exit(f"capture build failed (rc={r.returncode}), log: {log}")

    for old in out.glob("*.json"):
        old.unlink()
    seen = {}
    for rspf in sorted(raw.glob("*.rsp")):
        stem = rspf.with_suffix("")
        cwd = Path(str(stem) + ".cwd").read_text().strip()
        argv = Path(str(stem) + ".argv").read_text().splitlines()
        env = Path(str(stem) + ".env").read_text().splitlines()
        lines = [l for l in rspf.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        outp = next((l.split(":", 1)[1].strip('"') for l in lines if l.startswith(("-o:", "--out:"))), None)
        if outp is None:
            sys.exit(f"no output option in {rspf}")
        op = Path(cwd, outp)
        tfm = op.parent.name
        if re.fullmatch(r"(osx|linux|win)-\w+", tfm):
            tfm = f"{op.parent.parent.name}-{tfm}"
        proj = op.stem
        pid = f"{proj}__{tfm}"
        if pid in seen:
            print(f"warning: {pid} compiled more than once; keeping the last one ({rspf.name})")
        seen[pid] = dict(id=pid, project=proj, tfm=tfm, cwd=cwd,
                         compiler=next((x for x in argv if x.endswith(".dll")), None),
                         rsp=lines, env=env, n_sources=sum(1 for l in lines if not l.startswith("-")))
    for pid, d in seen.items():
        (out / f"{pid}.json").write_text(json.dumps(d, indent=1) + "\n")
        print(f"{pid:60s} sources={d['n_sources']:5d} cwd={d['cwd']}")
    print(f"{len(seen)} fsc invocations captured -> {out}")


if __name__ == "__main__":
    main()
