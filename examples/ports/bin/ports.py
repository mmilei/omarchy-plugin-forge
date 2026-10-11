#!/usr/bin/env python3
"""Listening TCP ports owned by the current user.

  ports.py snapshot            JSON: {"count": N, "ports": [{port, pid, start, name, cwd, project}]}
  ports.py open <port>         xdg-open http://localhost:<port>
  ports.py stop <pid> <start>  SIGTERM, refused (exit 1) if the pid's start time changed

Only loopback and wildcard listeners are listed. Data comes from `ss -ltnpH`,
which only reveals the process of sockets the caller owns. Stdlib only.
"""
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

LOCAL_HOSTS = {"*", "0.0.0.0", "::", "[::]", "[::1]", "::1", "localhost"}
USERS = re.compile(r'\("((?:[^"\\]|\\.)*)",pid=(\d+),fd=\d+\)')


def is_local(host):
    return host in LOCAL_HOSTS or host.startswith("127.")


def parse_ss(text):
    """Yield (port, name, pid) from `ss -ltnpH` output; one entry per socket/process."""
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 5:
            continue
        host, _, port = cols[3].rpartition(":")
        host = host.split("%")[0]
        if not port.isdigit() or not is_local(host):
            continue
        for name, pid in USERS.findall(line):
            yield int(port), name, int(pid)


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


def collect(ss_text, proc="/proc"):
    seen, rows = set(), []
    for port, name, pid in parse_ss(ss_text):
        if (port, pid) in seen or not owned_by_me(pid, proc):
            continue
        seen.add((port, pid))
        try:
            cwd = os.readlink(Path(proc, str(pid), "cwd"))
        except OSError:
            cwd = ""
        rows.append({"port": port, "pid": pid, "start": starttime(pid, proc), "name": name,
                     "cwd": cwd, "project": project_of(cwd)})
    rows.sort(key=lambda r: r["port"])
    return rows


def snapshot():
    out = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True, check=True).stdout
    rows = collect(out)
    return {"count": len(rows), "ports": rows}


def stop(pid, start, proc="/proc"):
    if starttime(pid, proc) != start or not owned_by_me(pid, proc):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    return True


def main(argv):
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
