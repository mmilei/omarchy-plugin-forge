#!/usr/bin/env python3
"""resources.py — data and actions for the mmilei.resources bar widget.

CLI:
  resources.py snapshot [--interval S] [--top N]   # JSON on stdout
  resources.py watch [--interval S] [--closed-interval S] [--top N] [--mode open|closed]
                                                  # one JSON object per line, until stdin closes
  resources.py kill PID START_TICKS                # SIGTERM; silent on success

`snapshot` samples /proc twice, `--interval` seconds apart (default 1.0), so
CPU percentages are real deltas without keeping state between runs.
A process's CPU is 100 per busy core (like top's Irix mode), so it can pass 100.

`watch` is the long-lived form the widget runs. It keeps the previous /proc
reading instead of sleeping between two, so a tick costs one read. Commands
arrive on stdin, one word per line: `open` (panel showing; resample now),
`closed`, `stop`; EOF also stops it. While closed it prints light objects
({"full": false, cpu.percent, ram}); while open, full ones like `snapshot`.

`kill` takes the process start time from the snapshot (field 22 of
/proc/PID/stat) and refuses when it no longer matches: that PID now belongs to
a different process.
"""

import argparse
import json
import os
import select
import signal
import sys
import time
from pathlib import Path

PROC = Path("/proc")
SYS = Path("/sys")
DEFAULT_INTERVAL = 1.0
DEFAULT_TOP = 5
# hwmon chip names that report the CPU package/die temperature, best first.
CPU_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz")
THERMAL_TYPES = ("x86_pkg_temp", "cpu", "soc", "acpitz")


# ---- /proc parsing -----------------------------------------------------------

def read_text(path):
    try:
        return Path(path).read_text()
    except OSError:
        return None


def parse_cpu_times(text):
    """Return {"cpu": (busy, total), "cpu0": (busy, total), ...} from /proc/stat text."""
    out = {}
    for line in (text or "").splitlines():
        if not line.startswith("cpu"):
            continue
        parts = line.split()
        try:
            vals = [int(v) for v in parts[1:]]
        except ValueError:
            continue
        if len(vals) < 4:
            continue
        # user nice system idle iowait irq softirq steal guest guest_nice
        # guest time is already inside user/nice, so only the first 8 count.
        vals = vals[:8] + [0] * (8 - len(vals[:8]))
        idle = vals[3] + vals[4]
        total = sum(vals)
        out[parts[0]] = (total - idle, total)
    return out


def cpu_percent(before, after):
    busy = after[0] - before[0]
    total = after[1] - before[1]
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * busy / total))


def parse_meminfo(text):
    info = {}
    for line in (text or "").splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        if fields and fields[0].isdigit():
            info[key] = int(fields[0]) * 1024
    return info


def memory_summary(info):
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    used = max(0, total - avail)
    swap_total = info.get("SwapTotal", 0)
    swap_used = max(0, swap_total - info.get("SwapFree", 0))

    def pct(a, b):
        return round(100.0 * a / b, 1) if b > 0 else 0.0

    return {
        "ram": {"used": used, "total": total, "percent": pct(used, total)},
        "swap": {"used": swap_used, "total": swap_total, "percent": pct(swap_used, swap_total)},
    }


def parse_loadavg(text):
    try:
        a, b, c = (float(x) for x in (text or "").split()[:3])
        return [a, b, c]
    except ValueError:
        return [0.0, 0.0, 0.0]


def parse_stat(text):
    """Return (comm, state, ticks, start) from /proc/PID/stat text, or None.

    comm may contain spaces and parentheses, so split on the last ')'.
    """
    if not text:
        return None
    lp, rp = text.find("("), text.rfind(")")
    if lp < 0 or rp < lp:
        return None
    comm = text[lp + 1:rp]
    rest = text[rp + 2:].split()
    try:
        # rest[0] is field 3 (state); utime/stime are fields 14/15, starttime 22.
        return comm, rest[0], int(rest[11]) + int(rest[12]), int(rest[19])
    except (IndexError, ValueError):
        return None


def scan_processes(proc_root=PROC):
    """pid -> {"name", "ticks", "start", "uid"} for every readable process."""
    procs = {}
    try:
        entries = list(os.scandir(proc_root))
    except OSError:
        return procs
    for entry in entries:
        if not entry.name.isdigit():
            continue
        parsed = parse_stat(read_text(Path(entry.path) / "stat"))
        if not parsed:
            continue
        comm, _state, ticks, start = parsed
        try:
            uid = entry.stat().st_uid
        except OSError:
            uid = -1
        procs[int(entry.name)] = {"name": comm, "ticks": ticks, "start": start, "uid": uid}
    return procs


def top_processes(before, after, cpu_delta_ticks, count, mine_only=False, uid=None):
    """Top `count` processes by CPU. cpu_delta_ticks is the all-CPU tick delta
    over the same window, so 100 * ticks / (delta / ncpu) means one full core."""
    rows = []
    for pid, now in after.items():
        prev = before.get(pid)
        if not prev or prev["start"] != now["start"]:
            continue
        used = now["ticks"] - prev["ticks"]
        if used <= 0 or cpu_delta_ticks <= 0:
            continue
        rows.append({
            "pid": pid,
            "name": now["name"],
            "start": now["start"],
            "cpu": round(100.0 * used / cpu_delta_ticks, 1),
            "mine": uid is not None and now["uid"] == uid,
        })
    rows.sort(key=lambda r: (-r["cpu"], r["pid"]))
    return rows[:count]


# ---- temperature -------------------------------------------------------------

def read_temperature(sys_root=SYS):
    """CPU temperature in °C, or None when the machine exposes no sensor."""
    best = None
    hwmon = Path(sys_root) / "class/hwmon"
    candidates = []
    if hwmon.is_dir():
        for chip in sorted(hwmon.iterdir()):
            name = (read_text(chip / "name") or "").strip()
            if name not in CPU_CHIPS:
                continue
            for f in sorted(chip.glob("temp*_input")):
                raw = (read_text(f) or "").strip()
                if raw.lstrip("-").isdigit():
                    candidates.append((CPU_CHIPS.index(name), int(raw) / 1000.0))
    if candidates:
        rank = min(c[0] for c in candidates)
        best = max(t for r, t in candidates if r == rank)
    else:
        thermal = Path(sys_root) / "class/thermal"
        if thermal.is_dir():
            for zone in sorted(thermal.glob("thermal_zone*")):
                kind = (read_text(zone / "type") or "").strip()
                raw = (read_text(zone / "temp") or "").strip()
                if kind in THERMAL_TYPES and raw.lstrip("-").isdigit():
                    t = int(raw) / 1000.0
                    best = t if best is None else max(best, t)
    if best is None or not (-30.0 < best < 150.0):
        return None
    return round(best, 1)


# ---- snapshot ----------------------------------------------------------------

def read_state(proc_root=PROC, procs=True):
    """One reading of /proc: CPU counters and, when `procs`, every process."""
    return {
        "cpu": parse_cpu_times(read_text(Path(proc_root) / "stat")),
        "procs": scan_processes(proc_root) if procs else None,
    }


def light_snapshot(a, b, proc_root=PROC):
    """What the bar shows: total CPU % and RAM, from two readings. Cheap: no
    process scan, no per-core work, no sensors."""
    mem = memory_summary(parse_meminfo(read_text(Path(proc_root) / "meminfo")))
    return {
        "full": False,
        "cpu": {"percent": round(cpu_percent(a["cpu"].get("cpu", (0, 0)), b["cpu"].get("cpu", (0, 0))), 1)},
        "ram": mem["ram"],
    }


def full_snapshot(a, b, top=DEFAULT_TOP, proc_root=PROC, sys_root=SYS, uid=None):
    """Everything the panel draws, from two readings that both include procs."""
    uid = os.getuid() if uid is None else uid
    cpu_a, cpu_b = a["cpu"], b["cpu"]
    cores = sorted((k for k in cpu_b if k != "cpu"), key=lambda k: int(k[3:]))
    total_pct = cpu_percent(cpu_a.get("cpu", (0, 0)), cpu_b.get("cpu", (0, 0)))
    per_core = [round(cpu_percent(cpu_a.get(k, (0, 0)), cpu_b[k]), 1) for k in cores]
    ncpu = max(1, len(cores))
    all_delta = cpu_b.get("cpu", (0, 0))[1] - cpu_a.get("cpu", (0, 0))[1]
    # all_delta covers every core; one core's worth is all_delta / ncpu.
    per_core_delta = all_delta / ncpu

    mem = memory_summary(parse_meminfo(read_text(Path(proc_root) / "meminfo")))
    return {
        "full": True,
        "cpu": {"percent": round(total_pct, 1), "cores": per_core},
        "ram": mem["ram"],
        "swap": mem["swap"],
        "load": parse_loadavg(read_text(Path(proc_root) / "loadavg")),
        "ncpu": ncpu,
        "temperature": read_temperature(sys_root),
        "processes": top_processes(a["procs"], b["procs"], per_core_delta, top, uid=uid),
    }


def snapshot(interval=DEFAULT_INTERVAL, top=DEFAULT_TOP, proc_root=PROC, sys_root=SYS,
             sleep=time.sleep, uid=None):
    a = read_state(proc_root)
    sleep(interval)
    b = read_state(proc_root)
    return full_snapshot(a, b, top, proc_root, sys_root, uid)


def stdin_commands(fd=0):
    """Return wait(timeout): block up to `timeout` seconds for a command line
    on `fd` and return the last word read ("open", "closed"), or None on
    timeout. "stop" on EOF: the shell went away, so the watcher must too."""
    def wait(timeout):
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
        data = os.read(fd, 4096)
        if not data:
            return "stop"
        words = data.decode(errors="replace").split()
        return words[-1] if words else None
    return wait


def watch(out, wait, open_interval=2.0, closed_interval=10.0, top=DEFAULT_TOP, mode="closed",
          proc_root=PROC, sys_root=SYS, uid=None, window=DEFAULT_INTERVAL):
    """Print one JSON object per line on `out` until `wait` returns "stop".

    `wait(timeout)` is both the sleep and the control channel: it returns None
    when the timeout elapsed, or a command: "open" (the panel is showing; also
    means "resample now"), "closed", "stop". While closed, a tick is light
    (total CPU and RAM); while open it is full, and the first full tick after
    opening samples a fresh `window` seconds, so process numbers are real deltas.
    """
    prev = read_state(proc_root, procs=False)
    cmd = wait(window)
    while cmd != "stop":
        if cmd in ("open", "closed"):
            mode = cmd
            if mode == "closed":
                prev["procs"] = None
        if mode == "open":
            if prev["procs"] is None:
                prev = read_state(proc_root)
                cmd = wait(window)
                continue
            cur = read_state(proc_root)
            snap = full_snapshot(prev, cur, top, proc_root, sys_root, uid)
            interval = open_interval
        else:
            cur = read_state(proc_root, procs=False)
            snap = light_snapshot(prev, cur, proc_root)
            interval = closed_interval
        prev = cur
        out.write(json.dumps(snap) + "\n")
        out.flush()
        cmd = wait(interval)


# ---- actions -----------------------------------------------------------------

def kill_process(pid, start, proc_root=PROC, uid=None, send=os.kill, own_pid=None):
    """SIGTERM `pid` if it is still the process that started at `start` ticks.

    Returns None on success, or an error message."""
    uid = os.getuid() if uid is None else uid
    own_pid = os.getpid() if own_pid is None else own_pid
    if pid <= 1 or pid == own_pid:
        return f"refusing to signal pid {pid}"
    parsed = parse_stat(read_text(Path(proc_root) / str(pid) / "stat"))
    if not parsed:
        return None  # already gone: the goal is met
    if parsed[3] != start:
        return f"pid {pid} is now a different process; refresh and try again"
    try:
        if (Path(proc_root) / str(pid)).stat().st_uid != uid:
            return f"pid {pid} belongs to another user"
    except OSError:
        return None
    try:
        send(pid, signal.SIGTERM)
    except ProcessLookupError:
        return None
    except PermissionError:
        return f"not allowed to signal pid {pid}"
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="resources.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    s.add_argument("--top", type=int, default=DEFAULT_TOP)
    w = sub.add_parser("watch")
    w.add_argument("--interval", type=float, default=2.0, help="seconds between ticks while the panel is open")
    w.add_argument("--closed-interval", type=float, default=10.0, help="seconds between ticks while closed")
    w.add_argument("--top", type=int, default=DEFAULT_TOP)
    w.add_argument("--mode", choices=("open", "closed"), default="closed")
    k = sub.add_parser("kill")
    k.add_argument("pid", type=int)
    k.add_argument("start", type=int)
    args = ap.parse_args(argv)

    if args.cmd == "snapshot":
        json.dump(snapshot(max(0.05, args.interval), max(1, args.top)), sys.stdout)
        sys.stdout.write("\n")
        return 0
    if args.cmd == "watch":
        try:
            watch(sys.stdout, stdin_commands(), max(0.5, args.interval), max(1.0, args.closed_interval),
                  max(1, args.top), args.mode)
        except BrokenPipeError:
            pass
        return 0
    err = kill_process(args.pid, args.start)
    if err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
