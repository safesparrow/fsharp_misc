#!/usr/bin/env python3
"""Variant of a make_layout.py layout where the compiler's own assemblies form ONE composite ReadyToRun image
(crossgen2 --composite --inputbubble): cross-assembly inlining and pre-compiled generic instantiations across
fsc, FSharp.Compiler.Service, FSharp.Core and FSharp.DependencyManager.Nuget. Everything else stays as in the base
layout (per-assembly R2R, like the SDK).

Usage: make_composite_layout.py <clone_dir> <base_layout> <out_layout>
"""
import json, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_layout import pe_info  # noqa: E402

BUBBLE = ["fsc.dll", "FSharp.Compiler.Service.dll", "FSharp.Core.dll", "FSharp.DependencyManager.Nuget.dll"]


def main():
    clone, base, out = (Path(x).resolve() for x in sys.argv[1:4])
    meta = json.loads((base / "layout.json").read_text())
    cg, rt = meta["crossgen2"], meta["runtime"]
    fw = clone / ".dotnet" / "shared" / "Microsoft.NETCore.App" / rt
    il = Path(meta["source"])  # IL build output the base layout was made from
    if out.exists():
        shutil.rmtree(out)
    subprocess.run(["cp", "-cR", str(base), str(out)], check=True)
    tmp = out.parent / (out.name + ".r2rtmp")
    shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir()
    # References: the IL copies of everything else in the layout dir, plus the shared framework.
    cmd = [cg, "--composite", "--inputbubble", *[str(il / b) for b in BUBBLE], "-o", str(tmp / "fsc.r2r.dll"),
           "--targetos", "osx", "--targetarch", "arm64", "-r", f"{il}/*.dll", "-r", f"{fw}/*.dll"]
    print(" ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"crossgen2 composite failed:\n{r.stdout[-4000:]}\n{r.stderr[-4000:]}")
    produced = sorted(p.name for p in tmp.glob("*.dll"))
    print("produced:", produced)
    for name in produced:
        shutil.copy2(tmp / name, out / name)
    shutil.rmtree(tmp)
    missing = [b for b in BUBBLE if not pe_info(out / b)]
    if missing:
        sys.exit(f"not R2R after composite build: {missing}")
    meta.update(variant_of=str(base), composite=dict(bubble=BUBBLE, image="fsc.r2r.dll", produced=produced, cmd=cmd))
    (out / "layout.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"layout: {out}")


if __name__ == "__main__":
    main()
