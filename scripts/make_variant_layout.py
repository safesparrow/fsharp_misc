#!/usr/bin/env python3
"""Copy an fsc layout (APFS clone, so it costs no disk) and change fsc.runtimeconfig.json configProperties.

Usage: make_variant_layout.py <base_layout> <out_layout> key=json_value ...
  e.g. make_variant_layout.py layouts/baseline layouts/p3_rc_flags System.Runtime.TieredPGO=false \
           System.Runtime.TieredCompilation.CallCountingDelayMs=0
"""
import json, shutil, subprocess, sys
from pathlib import Path


def main():
    base, out, *sets = sys.argv[1:]
    base, out = Path(base).resolve(), Path(out).resolve()
    if out.exists():
        shutil.rmtree(out)
    subprocess.run(["cp", "-cR", str(base), str(out)], check=True)
    rcp = out / "fsc.runtimeconfig.json"
    rc = json.loads(rcp.read_text())
    props = rc["runtimeOptions"].setdefault("configProperties", {})
    for s in sets:
        k, v = s.split("=", 1)
        props[k] = json.loads(v)
    rcp.write_text(json.dumps(rc, indent=2) + "\n")
    meta = json.loads((out / "layout.json").read_text())
    meta.update(variant_of=str(base), configProperties_set=dict(s.split("=", 1) for s in sets), runtimeconfig=rc)
    (out / "layout.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"layout: {out}  configProperties: {props}")


if __name__ == "__main__":
    main()
