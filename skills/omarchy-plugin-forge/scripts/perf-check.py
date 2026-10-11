#!/usr/bin/env python3
"""perf-check.py <plugin-dir> [<plugin-dir> ...] [--seconds N] [--max-cpu PCT]

What a plugin costs, measured on its own processes, not guessed.

A plugin built with this skill runs one long-lived process (`<script> watch`,
see reference/patterns.md, "Keep it cheap"). That process, plus everything it
starts (Linux adds reaped children to its cutime/cstime), is the plugin's whole
data cost, and /proc/<pid>/stat gives it exactly, without the noise of the rest
of the desktop. Measuring the shell as a whole does not work: its CPU moves by
several points with anything else on screen.

Per plugin (it must be installed and enabled; run forge-check first):
  closed   panel closed for --seconds (the cost you pay all day)
  open     panel open for --seconds

A process belongs to the plugin when its command line contains the plugin's
installed path. If none is found the check fails: a plugin that spawns a new
interpreter per refresh cannot be measured this way, and costs ~40 ms of CPU per
spawn besides.

Fails (exit 1) when the closed-panel cost exceeds --max-cpu, % of one core
(default 0.5). Drawing cost inside the shell is not included; keep panels static.
"""
import argparse
import json
import os
import subprocess
import sys
import time

TICK = os.sysconf("SC_CLK_TCK")


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def plugin_pids(paths):
    pids = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            cmd = open(f"/proc/{name}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if any(p in cmd for p in paths) and "perf-check" not in cmd:
            pids.append(int(name))
    return pids


def cpu_seconds(pid):
    try:
        fields = open(f"/proc/{pid}/stat").read().rpartition(")")[2].split()
    except OSError:
        return None
    return sum(int(f) for f in fields[11:15]) / TICK  # utime stime cutime cstime


def rss_mb(pids):
    total = 0
    for pid in pids:
        try:
            for line in open(f"/proc/{pid}/status"):
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1])
        except OSError:
            pass
    return total / 1024


def measure(paths, seconds):
    """CPU % of one core used by the plugin's processes that lived through the window."""
    pids = plugin_pids(paths)
    start = {p: cpu_seconds(p) for p in pids}
    t0 = time.monotonic()
    time.sleep(seconds)
    elapsed = time.monotonic() - t0
    used = 0.0
    alive = []
    for p, c0 in start.items():
        c1 = cpu_seconds(p)
        if c0 is not None and c1 is not None:
            used += c1 - c0
            alive.append(p)
    return 100 * used / elapsed, alive


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--max-cpu", type=float, default=0.5)
    args = ap.parse_args()

    enabled = {p["id"]: p["enabled"] for p in json.loads(sh("omarchy", "plugin", "list", "--json") or "[]")}
    rows, failed = [], False
    for d in args.dirs:
        d = os.path.realpath(d)
        pid_ = json.load(open(os.path.join(d, "manifest.json")))["id"]
        if not enabled.get(pid_):
            print(f"{pid_}: not enabled (run forge-check first)")
            failed = True
            continue
        paths = [d, os.path.expanduser(f"~/.config/omarchy/plugins/{pid_}")]
        sh("omarchy-shell", "-q", "shell", "hide", pid_)
        time.sleep(2)
        closed, alive = measure(paths, args.seconds)
        if not alive:
            print(f"{pid_}: FAIL no long-lived process found; use a `watch` subprocess (patterns.md, Keep it cheap)")
            rows.append((pid_, None, None, None, False))
            failed = True
            continue
        rss = rss_mb(alive)
        sh("omarchy-shell", "shell", "summon", pid_, "{}")
        time.sleep(2)
        opened, _ = measure(paths, args.seconds)
        sh("omarchy-shell", "-q", "shell", "hide", pid_)
        ok = closed <= args.max_cpu
        failed |= not ok
        rows.append((pid_, closed, opened, rss, ok))
        print(f"{pid_}: closed {closed:.2f}%, open {opened:.2f}%, {rss:.0f} MB {'PASS' if ok else 'FAIL'}", flush=True)

    print("\n| plugin | CPU, panel closed | CPU, panel open | RSS | result |")
    print("|---|---|---|---|---|")
    for p, closed, opened, rss, ok in rows:
        if closed is None:
            print(f"| {p} | — | — | — | FAIL (no watch process) |")
        else:
            print(f"| {p} | {closed:.2f}% | {opened:.2f}% | {rss:.0f} MB | {'PASS' if ok else 'FAIL'} |")
    print(f"\n% of one core used by the plugin's own processes over {args.seconds:.0f}s per phase. "
          f"Limit with the panel closed: {args.max_cpu}%.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
