#!/usr/bin/env python3
"""agents-waiting.py — data and actions for the mmilei.agents-waiting bar widget.

Claude Code hooks call `hook`; each event updates one JSON file per session
under the state dir. The panel calls `snapshot` (prunes dead sessions, prints
JSON) and `focus SESSION_ID` (focuses the terminal window in Hyprland).

CLI:
  agents-waiting.py hook            # hook JSON on stdin; never fails, never blocks
  agents-waiting.py snapshot        # JSON on stdout
  agents-waiting.py focus ID        # exit 1 if no window can be found
  agents-waiting.py hooks-config    # the "hooks" JSON to merge into settings.json

Environment:
  AGENTS_WAITING_STATE   state dir (default ~/.local/state/mmilei.agents-waiting/sessions)
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ID = "mmilei.agents-waiting"
EVENTS = ("Notification", "Stop", "UserPromptSubmit", "SessionEnd")
ORDER = {"permission": 0, "waiting": 1, "working": 2}
WAITING_STATES = ("permission", "waiting")


def state_dir():
    env = os.environ.get("AGENTS_WAITING_STATE")
    if env:
        return Path(env)
    return Path.home() / ".local/state" / PLUGIN_ID / "sessions"


# ---- /proc helpers (proc_root is injectable for tests) ----------------------

def read_stat(pid, proc_root="/proc"):
    """Return (comm, ppid, starttime) from <proc>/<pid>/stat, or None."""
    try:
        raw = (Path(proc_root) / str(pid) / "stat").read_text()
    except OSError:
        return None
    # comm may contain spaces and parentheses: split on the last ')'.
    head, _, tail = raw.rpartition(")")
    comm = head.partition("(")[2]
    fields = tail.split()
    try:
        return comm, int(fields[1]), int(fields[19])
    except (IndexError, ValueError):
        return None


def read_cmdline(pid, proc_root="/proc"):
    try:
        raw = (Path(proc_root) / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [a for a in raw.decode(errors="replace").split("\0") if a]


def ancestors(pid, proc_root="/proc"):
    """Yield pid, then its parents, until init or a vanished process."""
    seen = set()
    while pid and pid > 1 and pid not in seen:
        seen.add(pid)
        yield pid
        stat = read_stat(pid, proc_root)
        if stat is None:
            return
        pid = stat[1]


def is_claude(pid, proc_root="/proc"):
    stat = read_stat(pid, proc_root)
    if stat is None:
        return False
    if stat[0] == "claude":
        return True
    argv = read_cmdline(pid, proc_root)
    return bool(argv) and os.path.basename(argv[0]) == "claude"


def find_agent_pid(start, proc_root="/proc"):
    """The hook runs under a short-lived shell; the session is the claude process above it."""
    for pid in ancestors(start, proc_root):
        if is_claude(pid, proc_root):
            return pid
    return start


def is_alive(pid, starttime, proc_root="/proc"):
    stat = read_stat(pid, proc_root)
    return stat is not None and stat[2] == starttime


# ---- state files -------------------------------------------------------------

def classify(event, payload):
    """Map a hook event to a state; None = leave the session untouched."""
    if event == "UserPromptSubmit":
        return "working"
    if event == "Stop":
        return "waiting"
    if event == "Notification":
        kind = payload.get("notification_type", "")
        message = str(payload.get("message", "")).lower()
        if kind == "auth_success":
            return None
        if kind == "idle_prompt" or "waiting for your input" in message:
            return "waiting"
        return "permission"
    return None


def session_path(directory, session_id):
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return Path(directory) / f"{safe or 'unknown'}.json"


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def handle_hook(payload, directory, parent_pid, proc_root="/proc", now=None):
    now = time.time() if now is None else now
    event = payload.get("hook_event_name", "")
    session_id = payload.get("session_id")
    if not session_id:
        return
    path = session_path(directory, session_id)
    if event == "SessionEnd":
        path.unlink(missing_ok=True)
        return
    state = classify(event, payload)
    if state is None:
        return
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    pid = find_agent_pid(parent_pid, proc_root)
    stat = read_stat(pid, proc_root)
    since = previous.get("since", now) if previous.get("state") == state else now
    write_atomic(path, {
        "session_id": session_id,
        "cwd": payload.get("cwd") or previous.get("cwd", ""),
        "state": state,
        "since": since,
        "pid": pid,
        "starttime": stat[2] if stat else 0,
    })


def snapshot(directory, proc_root="/proc", now=None):
    now = time.time() if now is None else now
    sessions = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            data = json.loads(path.read_text())
            if not is_alive(data["pid"], data["starttime"], proc_root):
                path.unlink(missing_ok=True)
                continue
            cwd = data.get("cwd", "")
            sessions.append({
                "session_id": data["session_id"],
                "project": os.path.basename(cwd.rstrip("/")) or cwd or "?",
                "cwd": cwd,
                "state": data["state"],
                "since": data["since"],
                "age": max(0, int(now - data["since"])),
            })
        except (OSError, ValueError, KeyError):
            continue
    sessions.sort(key=lambda s: (ORDER.get(s["state"], 9), s["age"]))
    return {
        "waiting": sum(1 for s in sessions if s["state"] in WAITING_STATES),
        "sessions": sessions,
    }


# ---- focus -------------------------------------------------------------------

def find_window(pid, clients, proc_root="/proc"):
    """First Hyprland client whose pid is `pid` or one of its ancestors."""
    by_pid = {c["pid"]: c for c in clients}
    for p in ancestors(pid, proc_root):
        if p in by_pid:
            return by_pid[p]
    return None


def hypr_clients():
    out = subprocess.run(["hyprctl", "-j", "clients"], capture_output=True, text=True, timeout=5)
    return json.loads(out.stdout)


def focus(session_id, directory, proc_root="/proc", clients=hypr_clients, dispatch=None):
    try:
        data = json.loads(session_path(directory, session_id).read_text())
    except (OSError, ValueError):
        return False
    window = find_window(data["pid"], clients(), proc_root)
    if window is None:
        return False
    run = dispatch or (lambda addr: subprocess.run(
        ["hyprctl", "dispatch", "focuswindow", f"address:{addr}"],
        capture_output=True, timeout=5))
    run(window["address"])
    return True


# ---- hook registration -------------------------------------------------------

def hooks_config(script_path):
    command = f"python3 {script_path} hook"
    return {"hooks": {
        event: [{"hooks": [{"type": "command", "command": command}]}]
        for event in EVENTS}}


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "hook":
        try:
            handle_hook(json.load(sys.stdin), state_dir(), os.getppid())
        except Exception:
            pass  # a hook must never disturb Claude Code
        return 0
    if cmd == "snapshot":
        print(json.dumps(snapshot(state_dir())))
        return 0
    if cmd == "focus" and len(argv) > 2:
        return 0 if focus(argv[2], state_dir()) else 1
    if cmd == "hooks-config":
        print(json.dumps(hooks_config(os.path.abspath(__file__)), indent=2))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
