#!/usr/bin/env python3
"""Record the exact test hardware/software to results/hardware.json (safe to publish: no serial
numbers, UUIDs or user names). macOS only."""
import json, re, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sh(*c):
    return subprocess.run(c, capture_output=True, text=True).stdout.strip()


def sysctl(k):
    return sh("sysctl", "-n", k)


def main():
    hw = json.loads(sh("system_profiler", "-json", "SPHardwareDataType"))["SPHardwareDataType"][0]
    mb = lambda b: f"{int(b) // 2**20} MB" if int(b) >= 2**20 else f"{int(b) // 1024} KB"
    pm = sh("pmset", "-g")
    lpm = re.search(r"lowpowermode\s+(\d)", pm)
    hardware = [
        ["Machine", f"{hw.get('machine_name')} ({hw.get('machine_model')}, model number {hw.get('model_number')})"],
        ["Chip", hw.get("chip_type")],
        ["CPU cores", f"{sysctl('hw.physicalcpu')} = {sysctl('hw.perflevel0.physicalcpu')} performance + "
                      f"{sysctl('hw.perflevel1.physicalcpu')} efficiency (no SMT; {sysctl('hw.logicalcpu')} logical)"],
        ["Caches", f"P-cluster L2 {mb(sysctl('hw.perflevel0.l2cachesize'))}, E-cluster L2 {mb(sysctl('hw.perflevel1.l2cachesize'))}, "
                   f"L1d {mb(sysctl('hw.perflevel0.l1dcachesize'))} (P) / {mb(sysctl('hw.perflevel1.l1dcachesize'))} (E), "
                   f"cache line {sysctl('hw.cachelinesize')} B"],
        ["Memory", f"{int(sysctl('hw.memsize')) // 2**30} GB unified (LPDDR5)"],
        ["Storage", "internal Apple SSD (APFS)"],
        ["Power", sh("pmset", "-g", "batt").splitlines()[0].replace("Now drawing from ", "").strip("'") +
                  f"; Low Power Mode {'on' if lpm and lpm.group(1) == '1' else 'off'}"],
        ["Thermal", "no thermal or CPU-power throttling warnings recorded (pmset -g therm)"
                    if "No performance warning level has been recorded" in sh("pmset", "-g", "therm")
                    else sh("pmset", "-g", "therm")],
    ]
    software = [
        ["OS", f"macOS {sh('sw_vers', '-productVersion')} ({sh('sw_vers', '-buildVersion')}), kernel Darwin {sh('uname', '-r')}"],
        ["Firmware", hw.get("boot_rom_version", "")],
    ]
    out = ROOT / "results" / "hardware.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(dict(hardware=hardware, software=software), indent=1) + "\n")
    for k, v in hardware + software:
        print(f"{k:10s} {v}")


if __name__ == "__main__":
    main()
