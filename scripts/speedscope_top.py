#!/usr/bin/env python3
"""Aggregate a dotnet-trace speedscope JSON: self and inclusive time per frame, summed over all threads.

EventPipe's sample profiler samples every managed thread, including blocked ones, so frames for waiting
(e.g. Monitor.Wait, WaitHandle, UNMANAGED_CODE_TIME) are reported separately rather than dropped.

Usage: speedscope_top.py <file.speedscope.json> [--top 40] [--filter regex] [--compare other.speedscope.json]
"""
import argparse, json, re, collections

PSEUDO = {"CPU_TIME", "UNMANAGED_CODE_TIME"}


def aggregate(path):
    d = json.load(open(path))
    frames = [f["name"] for f in d["shared"]["frames"]]
    self_t, incl_t = collections.Counter(), collections.Counter()
    total = 0.0
    for p in d["profiles"]:
        if p["type"] == "evented":
            stack, last = [], None
            for e in p["events"]:
                at = e["at"]
                if last is not None and stack:
                    dt = at - last
                    leaf = frames[stack[-1]]
                    if leaf in PSEUDO and len(stack) > 1:
                        # dotnet-trace puts the thread state under the method: charge the method, split by state
                        self_t[f"[{leaf}] " + frames[stack[-2]]] += dt
                    else:
                        self_t[leaf] += dt
                    for f in set(stack):
                        incl_t[frames[f]] += dt
                    total += dt
                last = at
                if e["type"] == "O":
                    stack.append(e["frame"])
                elif stack:
                    # close events may not be perfectly nested in converted traces; pop the matching frame
                    if stack[-1] == e["frame"]:
                        stack.pop()
                    elif e["frame"] in stack:
                        stack.remove(e["frame"])
        else:
            for s, w in zip(p["samples"], p["weights"]):
                if s:
                    self_t[frames[s[-1]]] += w
                    for f in set(s):
                        incl_t[frames[f]] += w
                total += w
    return self_t, incl_t, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--filter", default="")
    ap.add_argument("--incl", action="store_true")
    a = ap.parse_args()
    self_t, incl_t, total = aggregate(a.file)
    src = incl_t if a.incl else self_t
    rx = re.compile(a.filter) if a.filter else None
    print(f"total thread time {total / 1000:.1f} s ({'inclusive' if a.incl else 'self'} time per frame)")
    n = 0
    for name, t in src.most_common():
        if rx and not rx.search(name):
            continue
        print(f"{t / 1000:9.3f} s {100 * t / total:6.2f}%  {name[:160]}")
        n += 1
        if n >= a.top:
            break


if __name__ == "__main__":
    main()
