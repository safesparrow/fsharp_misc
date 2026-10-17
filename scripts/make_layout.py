#!/usr/bin/env python3
"""Build a production-like fsc layout from a dotnet/fsharp clone.

Mirrors how the .NET SDK assembles sdk/<v>/FSharp/ (dotnet/sdk: src/Layout/redist/tools/tool_fsc.csproj,
targets/GenerateLayout.targets, targets/Crossgen.targets, src/Tasks/sdk-tasks/Crossgen.cs):
  * copy the fsc publish/build output
  * crossgen2 every managed PE except FSharp.Build.dll and *.resources.dll, one by one (not composite):
      crossgen2 in.dll -o out.dll --targetos <os> --targetarch <arch> -r <dir>/*.dll -r <sharedfw>/*.dll --perfmap
  * write fsc.runtimeconfig.json with the SDK's configProperties (Server GC etc.)

Usage: make_layout.py <clone_dir> <out_dir> [--opt none|O]
"""
import argparse, json, platform, shutil, struct, subprocess, sys
from pathlib import Path

SDK_CONFIG_PROPERTIES = {
    "System.GC.Server": True,
    "System.Reflection.Metadata.MetadataUpdater.IsSupported": False,
    "System.Runtime.Serialization.EnableUnsafeBinaryFormatterSerialization": False,
}


def pe_info(path: Path):
    """Return None if not a managed PE, else (is_r2r: bool)."""
    d = path.read_bytes()
    if len(d) < 0x40 or d[:2] != b"MZ":
        return None
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    if d[pe:pe + 4] != b"PE\0\0":
        return None
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", d, opt)[0]
    dd = opt + (96 if magic == 0x10B else 112)
    clirva, clisz = struct.unpack_from("<II", d, dd + 14 * 8)
    if clisz == 0:
        return None
    secs = [struct.unpack_from("<8sIIII", d, opt + optsz + i * 40) for i in range(nsec)]
    for _, vs, va, rs, rp in secs:
        if va <= clirva < va + max(vs, rs):
            c = clirva - va + rp
            break
    else:
        return None
    _, mnsz = struct.unpack_from("<II", d, c + 64)
    return mnsz > 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clone")
    ap.add_argument("out")
    ap.add_argument("--opt", default="none", choices=["none", "O"],
                    help="none = exactly what the SDK Crossgen task passes; O = add -O")
    ap.add_argument("--config", default="Release")
    a = ap.parse_args()

    clone = Path(a.clone).resolve()
    out = Path(a.out).resolve()
    fscs = sorted((clone / "artifacts" / "bin" / "fsc" / a.config).glob("*/fsc.dll"))
    if len(fscs) != 1:
        sys.exit(f"expected exactly one fsc.dll under artifacts/bin/fsc/{a.config}/*/, got {fscs}")
    src = fscs[0].parent

    dotnet_root = clone / ".dotnet"
    rc = json.loads((src / "fsc.runtimeconfig.json").read_text())
    fw_ver = rc["runtimeOptions"]["framework"]["version"]
    fws = sorted((dotnet_root / "shared" / "Microsoft.NETCore.App").iterdir())
    # The runtime fsc will actually run on (rollForward picks the highest patch of the same major.minor).
    fw = [f for f in fws if f.name.split("-")[0].rsplit(".", 1)[0] == fw_ver.split("-")[0].rsplit(".", 1)[0]][-1]
    os_ = {"Darwin": "osx", "Linux": "linux", "Windows": "windows"}[platform.system()]
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "AMD64": "x64"}[platform.machine()]
    cg = Path.home() / ".nuget/packages" / f"microsoft.netcore.app.crossgen2.{os_}-{arch}" / fw.name / "tools" / "crossgen2"
    if not cg.exists():
        sys.exit(f"crossgen2 for runtime {fw.name} not found at {cg}")

    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(src, out)

    meta = dict(source=str(src), runtime=fw.name, crossgen2=str(cg), opt=a.opt, compiled=[], skipped=[])
    tmp = out.parent / (out.name + ".r2rtmp")
    tmp.mkdir(exist_ok=True)
    for f in sorted(out.rglob("*.dll")):
        rel = f.relative_to(out)
        info = pe_info(f)
        if info is None:
            continue
        if f.name == "FSharp.Build.dll" or f.name.endswith(".resources.dll"):
            meta["skipped"].append(str(rel)); continue
        if info:  # already R2R
            meta["skipped"].append(f"{rel} (already R2R)"); continue
        dst = tmp / f.name
        cmd = [str(cg), str(f), "-o", str(dst), "--targetos", os_, "--targetarch", arch,
               "-r", f"{out}/*.dll", "-r", f"{fw}/*.dll", "--perfmap", "--perfmap-path", str(tmp)]
        if a.opt == "O":
            cmd.append("-O")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"crossgen2 failed for {rel}:\n{r.stdout}\n{r.stderr}")
        shutil.move(dst, f)
        meta["compiled"].append(str(rel))
        print(f"r2r {rel}", flush=True)
    shutil.rmtree(tmp)

    rc["runtimeOptions"]["configProperties"] = dict(SDK_CONFIG_PROPERTIES)
    (out / "fsc.runtimeconfig.json").write_text(json.dumps(rc, indent=2) + "\n")
    meta["runtimeconfig"] = rc
    (out / "layout.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"layout: {out}  ({len(meta['compiled'])} R2R-compiled, runtime {fw.name})")


if __name__ == "__main__":
    main()
