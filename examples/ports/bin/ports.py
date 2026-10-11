#!/usr/bin/env python3
"""Listening TCP ports owned by the current user.

  ports.py snapshot            JSON: {"count": N, "ports": [{port, pid, start, name, cwd, project}]}
  ports.py watch [--interval S] [--closed-interval S] [--mode open|closed]
                               one JSON object per line (same shape as snapshot), until stdin closes
  ports.py open <port>         xdg-open http://localhost:<port>
  ports.py stop <pid> <start>  SIGTERM, refused (exit 1) if the pid's start time changed

Only loopback and wildcard listeners of your own user are listed. Data comes from
/proc/net/tcp and /proc/net/tcp6 (state LISTEN, uid column); socket inodes are mapped
to PIDs by reading /proc/<pid>/fd of your own processes. Stdlib only.

`watch` is the long-lived form the widget runs. Commands arrive on stdin, one word per
line: `open` (panel showing; print the current state now), `closed`, `stop`; EOF also
stops it. While closed it only reads the two /proc/net files every --closed-interval
seconds and maps inodes to processes (and prints) when the set of listening sockets
changed; while open it rescans every --interval seconds and prints when the rows changed.
"""
import argparse
import json
import os
import re
import select
import signal
import subprocess
import sys
from pathlib import Path

PROC = Path("/proc")
SOCKET = re.compile(r"socket:\[(\d+)\]")
LOOPBACK6 = "00000000000000000000000001000000"


def is_local(host):
    """host: hex address as in /proc/net/tcp (8 chars) or tcp6 (32 chars)."""
    if len(host) == 8:
        return host == "00000000" or host.endswith("7F")
    return host == "0" * 32 or host == LOOPBACK6


def read_listening(proc="/proc"):
    """{inode: port} of LISTEN sockets of this user on loopback or wildcard addresses."""
    uid = str(os.getuid())
    out = {}
    for name in ("tcp", "tcp6"):
        try:
            lines = Path(proc, "net", name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            if len(f) < 10 or f[3] != "0A" or f[7] != uid or f[9] == "0":
                continue
            host, _, port = f[1].partition(":")
            if is_local(host):
                out[int(f[9])] = int(port, 16)
    return out


def starttime(pid, proc="/proc"):
    try:
        return int(Path(proc, str(pid), "stat").read_text().rpartition(")")[2].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def owned_by_me(pid, proc="/proc"):
    try:
        return Path(proc, str(pid)).stat().st_uid == os.getuid()
    except OSError:
        return False


def project_of(cwd):
    return Path(cwd).name or cwd if cwd else ""


def socket_pids(inodes, proc="/proc"):
    """{inode: [pid, ...]} for the wanted inodes, scanning the fds of this user's processes."""
    found = {}
    try:
        entries = list(os.scandir(proc))
    except OSError:
        return found
    uid = os.getuid()
    for e in entries:
        if not e.name.isdigit():
            continue
        try:
            if e.stat().st_uid != uid:
                continue
            fds = list(os.scandir(Path(e.path, "fd")))
        except OSError:
            continue
        for fd in fds:
            try:
                m = SOCKET.fullmatch(os.readlink(fd.path))
            except OSError:
                continue
            if m and int(m.group(1)) in inodes:
                found.setdefault(int(m.group(1)), []).append(int(e.name))
    return found


def comm_of(pid, proc="/proc"):
    try:
        return Path(proc, str(pid), "comm").read_text().strip()
    except OSError:
        return ""


def collect(listening, proc="/proc"):
    seen, rows = set(), []
    for inode, pids in socket_pids(listening, proc).items():
        port = listening[inode]
        for pid in pids:
            if (port, pid) in seen:
                continue
            seen.add((port, pid))
            try:
                cwd = os.readlink(Path(proc, str(pid), "cwd"))
            except OSError:
                cwd = ""
            rows.append({"port": port, "pid": pid, "start": starttime(pid, proc),
                         "name": comm_of(pid, proc), "cwd": cwd, "project": project_of(cwd)})
    rows.sort(key=lambda r: (r["port"], r["pid"]))
    return rows


def snapshot(proc="/proc"):
    rows = collect(read_listening(proc), proc)
    return {"count": len(rows), "ports": rows}


def stdin_commands(fd=0):
    """Return wait(timeout): block up to `timeout` seconds for a command line on `fd` and
    return the last word read ("open", "closed"), or None on timeout. "stop" on EOF: the
    shell went away, so the watcher must too."""
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


def watch(out, wait, open_interval=3.0, closed_interval=5.0, mode="closed", proc="/proc"):
    """Print one snapshot per line on `out` until `wait` returns "stop".

    `wait(timeout)` is both the sleep and the control channel: None when the timeout
    elapsed, or a command ("open", "closed", "stop"). Prints once at start, then only
    when something changed (or on `open`, so the panel never waits a tick).
    """
    last = {"fp": None, "rows": None}

    def emit(force):
        listening = read_listening(proc)
        fp = frozenset(listening.items())
        if not force and mode == "closed" and fp == last["fp"]:
            return
        last["fp"] = fp
        rows = collect(listening, proc)
        if force or rows != last["rows"]:
            last["rows"] = rows
            out.write(json.dumps({"count": len(rows), "ports": rows}) + "\n")
            out.flush()

    emit(True)
    while True:
        cmd = wait(open_interval if mode == "open" else closed_interval)
        if cmd == "stop":
            return
        if cmd in ("open", "closed"):
            mode = cmd
        emit(cmd == "open")


def stop(pid, start, proc="/proc"):
    if starttime(pid, proc) != start or not owned_by_me(pid, proc):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    return True


def main(argv):
    if len(argv) > 1 and argv[1] == "watch":
        ap = argparse.ArgumentParser(prog="ports.py watch")
        ap.add_argument("--interval", type=float, default=3.0)
        ap.add_argument("--closed-interval", type=float, default=5.0)
        ap.add_argument("--mode", choices=("open", "closed"), default="closed")
        args = ap.parse_args(argv[2:])
        try:
            watch(sys.stdout, stdin_commands(), max(0.5, args.interval), max(1.0, args.closed_interval), args.mode)
        except BrokenPipeError:
            pass
        return 0
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "snapshot":
        print(json.dumps(snapshot()))
    elif cmd == "open" and len(argv) == 3 and argv[2].isdigit():
        subprocess.Popen(["xdg-open", f"http://localhost:{argv[2]}"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif cmd == "stop" and len(argv) == 4 and argv[2].isdigit() and argv[3].isdigit():
        if not stop(int(argv[2]), int(argv[3])):
            print("process changed or already gone; not stopping", file=sys.stderr)
            return 1
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
