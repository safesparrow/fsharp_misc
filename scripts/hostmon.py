"""Lightweight host monitoring for benchmark runs (macOS, via Mach host_statistics; no subprocesses).

* cpu_ticks(): system-wide user+system+nice / idle ticks (all CPUs, 100 Hz).
* vm(): free/compressor pages and cumulative paging counters.
* RunMonitor: context manager around one benchmark run. It records the system busy CPU-seconds
  during the run (so foreign CPU = busy - the benchmarked process's own CPU), paging/swap deltas,
  and the minimum free memory, sampled every 0.5 s on a background thread (cost: microseconds per sample).
* wait_quiet(): before a run, wait until the box is idle (busy CPU < threshold over a 1 s window).
* top_procs(): top processes by CPU (one `ps` call; only used outside the timed region).
"""
import ctypes, ctypes.util, os, subprocess, threading, time

_libc = ctypes.CDLL(ctypes.util.find_library("c"))
_libc.mach_host_self.restype = ctypes.c_uint
_HOST = _libc.mach_host_self()
_PAGE = os.sysconf("SC_PAGE_SIZE")
TICKS_PER_S = 100  # CLK_TCK for host_statistics CPU load ticks


class _CpuLoad(ctypes.Structure):
    _fields_ = [("ticks", ctypes.c_uint * 4)]  # user, system, idle, nice


class _Vm64(ctypes.Structure):
    _fields_ = [("free_count", ctypes.c_uint), ("active_count", ctypes.c_uint),
                ("inactive_count", ctypes.c_uint), ("wire_count", ctypes.c_uint),
                ("zero_fill_count", ctypes.c_uint64), ("reactivations", ctypes.c_uint64),
                ("pageins", ctypes.c_uint64), ("pageouts", ctypes.c_uint64),
                ("faults", ctypes.c_uint64), ("cow_faults", ctypes.c_uint64),
                ("lookups", ctypes.c_uint64), ("hits", ctypes.c_uint64), ("purges", ctypes.c_uint64),
                ("purgeable_count", ctypes.c_uint), ("speculative_count", ctypes.c_uint),
                ("decompressions", ctypes.c_uint64), ("compressions", ctypes.c_uint64),
                ("swapins", ctypes.c_uint64), ("swapouts", ctypes.c_uint64),
                ("compressor_page_count", ctypes.c_uint), ("throttled_count", ctypes.c_uint),
                ("external_page_count", ctypes.c_uint), ("internal_page_count", ctypes.c_uint),
                ("total_uncompressed_pages_in_compressor", ctypes.c_uint64)]


def cpu_ticks():
    info = _CpuLoad()
    cnt = ctypes.c_uint(4)
    if _libc.host_statistics(_HOST, 3, ctypes.byref(info), ctypes.byref(cnt)) != 0:  # HOST_CPU_LOAD_INFO
        raise OSError("host_statistics failed")
    u, s, i, n = info.ticks
    return u + s + n, i


def vm():
    v = _Vm64()
    cnt = ctypes.c_uint(ctypes.sizeof(_Vm64) // 4)
    if _libc.host_statistics64(_HOST, 4, ctypes.byref(v), ctypes.byref(cnt)) != 0:  # HOST_VM_INFO64
        raise OSError("host_statistics64 failed")
    return v


def free_mb(v=None):
    v = v or vm()
    return (v.free_count + v.speculative_count) * _PAGE / 2**20


def busy_cores(window_s=1.0):
    b0, i0 = cpu_ticks(); time.sleep(window_s); b1, i1 = cpu_ticks()
    return (b1 - b0) / TICKS_PER_S / window_s


def wait_quiet(threshold_cores=0.3, window_s=1.0, max_wait_s=60.0):
    """Block until busy CPU over `window_s` is below `threshold_cores`. Returns (busy_cores, waited_s)."""
    t0 = time.time()
    while True:
        b = busy_cores(window_s)
        waited = time.time() - t0
        if b < threshold_cores or waited >= max_wait_s:
            return b, waited


def top_procs(n=5, exclude_pids=()):
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,%cpu=,rss=,comm="], capture_output=True, text=True).stdout
    except Exception:
        return []
    rows = []
    for l in out.splitlines():
        p = l.split(None, 3)
        if len(p) < 4 or int(p[0]) in exclude_pids:
            continue
        rows.append(dict(pid=int(p[0]), cpu=float(p[1]), rss_mb=int(p[2]) / 1024, name=os.path.basename(p[3])))
    rows.sort(key=lambda r: -r["cpu"])
    return rows[:n]


class RunMonitor:
    def __init__(self, interval_s=0.5):
        self.interval = interval_s
        self._stop = threading.Event()

    def __enter__(self):
        self.v0 = vm(); self.b0, self.i0 = cpu_ticks(); self.t0 = time.perf_counter()
        self.min_free = free_mb(self.v0)
        self._th = threading.Thread(target=self._loop, daemon=True); self._th.start()
        return self

    def _loop(self):
        while not self._stop.wait(self.interval):
            self.min_free = min(self.min_free, free_mb())

    def __exit__(self, *exc):
        self._stop.set(); self._th.join()
        b1, i1 = cpu_ticks(); v1 = vm(); dt = time.perf_counter() - self.t0
        self.result = dict(
            sys_busy_cpu_s=(b1 - self.b0) / TICKS_PER_S,
            sys_idle_cpu_s=(i1 - self.i0) / TICKS_PER_S,
            free_mb_start=free_mb(self.v0), free_mb_min=min(self.min_free, free_mb(v1)),
            swapins=v1.swapins - self.v0.swapins, swapouts=v1.swapouts - self.v0.swapouts,
            pageouts=v1.pageouts - self.v0.pageouts, compressions=v1.compressions - self.v0.compressions,
            mon_wall_s=dt)
        return False

    def finish(self, own_cpu_s):
        """Add foreign CPU (busy CPU not accounted for by the benchmarked process)."""
        r = self.result
        r["foreign_cpu_s"] = r["sys_busy_cpu_s"] - own_cpu_s
        r["foreign_cores_avg"] = r["foreign_cpu_s"] / r["mon_wall_s"] if r["mon_wall_s"] > 0 else 0.0
        return r
