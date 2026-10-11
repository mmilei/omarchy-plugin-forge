#!/usr/bin/env python3
"""agents-waiting.py — data and actions for the mmilei.agents-waiting bar widget.

An inbox of coding agents waiting for you, from three sources:
  1. Claude Code, natively: ~/.claude/sessions/<pid>.json (undocumented format;
     unparseable files are skipped and `claude agents --json` is the fallback,
     at most once every 30 s).
  2. herdr: `herdr api snapshot` when its server runs (silently empty otherwise).
  3. Reported state: `report` / `release` subcommands, one JSON file per source.

The same agent can be seen by several sources; rows are joined so each agent is
listed once (see join_rows).

CLI:
  agents-waiting.py snapshot                       # JSON on stdout
  agents-waiting.py open KEY                       # ack a done row, then focus it
  agents-waiting.py report --source ID --agent NAME --state idle|working|blocked|done
                           --seq N [--message TEXT] [--cwd DIR] [--session-id ID]
  agents-waiting.py release --source ID --seq N
`report` and `release` always exit 0 and never print: hooks call them.

Environment (overrides, mostly for tests and screenshots):
  AGENTS_WAITING_STATE           state root (default ~/.local/state/mmilei.agents-waiting)
  AGENTS_WAITING_CLAUDE_DIR      Claude config dir (default ~/.claude)
  AGENTS_WAITING_HERDR_SNAPSHOT  read this file instead of running herdr
Plugin-local pointer: if .forge/demo-root exists, its content is a directory with
state/, claude/ and herdr.json that replace the three defaults (written by
.forge/seed, removed by .forge/unseed). Environment variables win over it.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ID = "mmilei.agents-waiting"
STATE_ORDER = {"blocked": 0, "waiting": 1, "done": 2, "working": 3}
ATTENTION = ("blocked", "waiting")
CLAUDE_STATUS = {"busy": "working", "idle": "waiting", "waiting": "blocked", "blocked": "blocked"}
HERDR_STATUS = {"idle": "waiting", "working": "working", "blocked": "blocked", "done": "done"}
REPORT_STATUS = HERDR_STATUS
AGENT_LABELS = {"claude": "Claude Code", "hermes": "Hermes", "codex": "Codex"}
FALLBACK_EVERY = 30       # seconds between `claude agents --json` calls
REPORT_TTL = 6 * 3600     # a report without a PID goes stale after this
TOMBSTONE_TTL = 3600
TAIL_BYTES = 512 * 1024
SHELLS = {"sh", "bash", "zsh", "fish", "dash", "env", "timeout", "nohup"}
INTERPRETERS = {"python", "python3", "node", "bun", "deno", "ruby", "perl"}


# ---- context -----------------------------------------------------------------

class Ctx:
    """Everything that touches the outside world, injectable for tests."""

    def __init__(self, state=None, claude_dir=None, proc_root="/proc", herdr=None,
                 clients=None, now=None, claude_agents=None, runner=None, popen=None):
        root = Path(os.environ["AGENTS_WAITING_STATE"]) if os.environ.get("AGENTS_WAITING_STATE") else None
        claude = Path(os.environ["AGENTS_WAITING_CLAUDE_DIR"]) if os.environ.get("AGENTS_WAITING_CLAUDE_DIR") else None
        herdr_file = os.environ.get("AGENTS_WAITING_HERDR_SNAPSHOT")
        demo = demo_root()
        if demo:
            root = root or demo / "state"
            claude = claude or demo / "claude"
            herdr_file = herdr_file or str(demo / "herdr.json")
        self.state = Path(state) if state else (root or Path.home() / ".local/state" / PLUGIN_ID)
        self.claude_dir = Path(claude_dir) if claude_dir else (claude or Path.home() / ".claude")
        self.proc_root = proc_root
        self.herdr_file = herdr_file
        self.herdr = herdr or self._herdr
        self.clients = clients or hypr_clients
        self._now = now
        self.claude_agents = claude_agents or run_claude_agents
        self.run = runner or (lambda cmd: subprocess.run(
            cmd, capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL).returncode == 0)
        self.popen = popen or (lambda cmd: subprocess.Popen(
            cmd, start_new_session=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    @property
    def now(self):
        return time.time() if self._now is None else self._now

    @property
    def reported_dir(self):
        return self.state / "reported"

    def _herdr(self):
        """Parsed `herdr api snapshot` (or the override file), or None."""
        try:
            if self.herdr_file:
                data = json.loads(Path(self.herdr_file).read_text())
            else:
                out = subprocess.run(["herdr", "api", "snapshot"], capture_output=True, text=True,
                                     timeout=3, stdin=subprocess.DEVNULL)
                if out.returncode != 0:
                    return None
                data = json.loads(out.stdout)
            return data["result"]["snapshot"]
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            return None


def demo_root():
    try:
        pointer = Path(__file__).resolve().parent.parent / ".forge" / "demo-root"
        text = pointer.read_text().strip()
        return Path(text) if text and Path(text).is_dir() else None
    except OSError:
        return None


def run_claude_agents():
    out = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True,
                         timeout=8, stdin=subprocess.DEVNULL)
    return json.loads(out.stdout)


# ---- /proc helpers -----------------------------------------------------------

def read_stat(pid, proc_root="/proc"):
    """(comm, ppid, starttime) from <proc>/<pid>/stat, or None."""
    try:
        raw = (Path(proc_root) / str(pid) / "stat").read_text()
    except (OSError, ValueError):
        return None
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


def read_env_var(pid, name, proc_root="/proc"):
    """One variable from /proc/<pid>/environ; None when unreadable or absent."""
    try:
        raw = (Path(proc_root) / str(pid) / "environ").read_bytes()
    except OSError:
        return None
    prefix = (name + "=").encode()
    for item in raw.split(b"\0"):
        if item.startswith(prefix):
            return item[len(prefix):].decode(errors="replace")
    return None


def ancestors(pid, proc_root="/proc"):
    seen = set()
    while pid and pid > 1 and pid not in seen:
        seen.add(pid)
        yield pid
        stat = read_stat(pid, proc_root)
        if stat is None:
            return
        pid = stat[1]


def is_alive(pid, starttime, proc_root="/proc"):
    stat = read_stat(pid, proc_root)
    return stat is not None and stat[2] == starttime


def matches_agent(pid, name, proc_root="/proc"):
    """Is `pid` the agent called `name`? Looks at comm and argv[0] (argv[1] for
    interpreters) only: a hook's `sh -c '... --agent name'` must not match."""
    stat = read_stat(pid, proc_root)
    if stat is None or stat[0] in SHELLS:
        return False
    name = name.lower()
    argv = read_cmdline(pid, proc_root)
    names = [stat[0].lower()]
    if argv:
        base = os.path.basename(argv[0]).lower()
        names.append(base)
        if base.rstrip("0123456789.") in INTERPRETERS and len(argv) > 1:
            names.append(os.path.basename(argv[1]).lower())
    return any(name in n for n in names)


def find_agent_pid(start, name, proc_root="/proc"):
    for pid in ancestors(start, proc_root):
        if matches_agent(pid, name, proc_root):
            return pid
    return None


# ---- source 1: Claude Code ---------------------------------------------------

def ai_title(claude_dir, session_id, tail_bytes=TAIL_BYTES):
    """Last "ai-title" of the transcript, reading only the file's tail."""
    for path in glob.glob(str(Path(claude_dir) / "projects" / "*" / f"{glob.escape(session_id)}.jsonl")):
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - tail_bytes))
                chunk = fh.read()
        except OSError:
            continue
        for line in reversed(chunk.decode(errors="replace").splitlines()):
            if '"ai-title"' not in line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue  # the first line of the tail may be cut
            if data.get("type") == "ai-title" and isinstance(data.get("aiTitle"), str) and data["aiTitle"].strip():
                return data["aiTitle"].strip()
    return ""


def fallback_agents(ctx):
    """`claude agents --json`, rate limited by a timestamp in the state dir."""
    cache = ctx.state / "claude-agents-cache.json"
    try:
        saved = json.loads(cache.read_text())
        if ctx.now - saved["ts"] < FALLBACK_EVERY:
            return saved["data"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        data = ctx.claude_agents()
        if not isinstance(data, list):
            data = []
    except Exception:
        data = []
    try:
        write_atomic(cache, {"ts": ctx.now, "data": data})
    except OSError:
        pass
    return data


def claude_rows(ctx):
    """Rows for live Claude sessions. Returns (rows, used_fallback)."""
    rows, pids, sids = [], set(), set()
    bad = False
    sessions_dir = ctx.claude_dir / "sessions"
    try:
        files = sorted(sessions_dir.glob("*.json"))
    except OSError:
        files = []
        bad = True
    if not sessions_dir.is_dir():
        bad = True
    for path in files:
        try:
            data = json.loads(path.read_text())
            pid, sid = int(data["pid"]), str(data["sessionId"])
            status = CLAUDE_STATUS[data["status"]]
            proc_start = str(data["procStart"])
            since = float(data.get("statusUpdatedAt") or data.get("updatedAt") or 0) / 1000
            cwd = str(data.get("cwd") or "")
            kind = data.get("kind") if data.get("kind") in ("interactive", "bg") else "interactive"
            name = data.get("name") if isinstance(data.get("name"), str) else ""
        except (OSError, ValueError, KeyError, TypeError):
            bad = True
            continue
        stat = read_stat(pid, ctx.proc_root)
        if stat is None or str(stat[2]) != proc_start:
            continue  # gone, or the PID was reused
        pids.add(pid)
        sids.add(sid)
        rows.append(make_row("claude", sid, "claude", status, since or ctx.now, cwd, pid=pid,
                             session_id=sid, kind=kind, title=ai_title(ctx.claude_dir, sid) or name))
    used_fallback = False
    if bad:
        used_fallback = True
        for item in fallback_agents(ctx):
            try:
                pid, sid = int(item["pid"]), str(item["sessionId"])
                status = CLAUDE_STATUS.get(item.get("status")) or CLAUDE_STATUS.get(item.get("state"))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if status is None or pid in pids or sid in sids or read_stat(pid, ctx.proc_root) is None:
                continue
            rows.append(make_row("claude", sid, "claude", status, ctx.now, str(item.get("cwd") or ""),
                                 pid=pid, session_id=sid,
                                 kind="bg" if item.get("kind") == "bg" else "interactive",
                                 title=ai_title(ctx.claude_dir, sid) or str(item.get("name") or "")))
    return rows, used_fallback


# ---- source 2: herdr ---------------------------------------------------------

def session_ids(info):
    """Session ids a herdr agent_session may stand for (value, or path stem)."""
    sess = (info or {}).get("agent_session") or {}
    out = set()
    value = sess.get("value")
    if isinstance(value, str) and value:
        out.add(value)
        out.add(Path(value).stem)
    return out


def herdr_entries(snap):
    """pane_id -> merged pane/agent info, only for panes that hold an agent."""
    if not isinstance(snap, dict):
        return {}
    merged = {}
    for key in ("panes", "agents"):
        for item in snap.get(key) or []:
            if isinstance(item, dict) and item.get("pane_id"):
                merged.setdefault(item["pane_id"], {}).update(item)
    out = {}
    for pane, info in merged.items():
        has_agent = bool(info.get("agent") or info.get("display_agent") or info.get("name")
                         or info.get("agent_session"))
        if info.get("agent_status") in HERDR_STATUS or has_agent:
            out[pane] = info
    return out


def herdr_row(pane, info, ctx):
    cwd = info.get("cwd") or info.get("foreground_cwd") or ""
    agent = str(info.get("agent") or info.get("display_agent") or info.get("name") or "agent").lower()
    title = info.get("title") or info.get("name") or info.get("terminal_title_stripped") or ""
    status = HERDR_STATUS.get(info.get("agent_status"))
    row = make_row("herdr", pane, agent, status or "working", ctx.now, cwd, pane=pane, title=title)
    row["sig"] = f"{info.get('agent_status')}:{info.get('state_change_seq', 0)}"
    row["known_state"] = status is not None
    return row


# ---- source 3: reported ------------------------------------------------------

def safe_name(text):
    return "".join(c for c in str(text) if c.isalnum() or c in "-_.") or "unknown"


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def do_report(state_dir, source, agent, state, seq, message="", cwd="", session_id="",
              parent_pid=None, proc_root="/proc", env=None, now=None):
    """Write one source's entry unless seq is not higher than the accepted one."""
    if state not in REPORT_STATUS:
        return False
    now = time.time() if now is None else now
    env = os.environ if env is None else env
    path = Path(state_dir) / "reported" / f"{safe_name(source)}.json"
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    if isinstance(previous.get("seq"), int) and seq <= previous["seq"]:
        return False
    pid = find_agent_pid(parent_pid, agent, proc_root) if parent_pid else None
    stat = read_stat(pid, proc_root) if pid else None
    since = previous.get("since", now) if previous.get("state") == state and not previous.get("released") else now
    write_atomic(path, {
        "source": source, "agent": agent, "state": state, "seq": seq,
        "message": message or "", "cwd": cwd or previous.get("cwd", ""),
        "session_id": session_id or "", "since": since, "ts": now,
        "pid": pid, "pstart": stat[2] if stat else None,
        "herdr_pane": env.get("HERDR_PANE_ID") or None,
    })
    return True


def do_release(state_dir, source, seq, now=None):
    now = time.time() if now is None else now
    path = Path(state_dir) / "reported" / f"{safe_name(source)}.json"
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    if isinstance(previous.get("seq"), int) and seq <= previous["seq"]:
        return False
    write_atomic(path, {"source": source, "released": True, "seq": seq, "ts": now})
    return True


def reported_rows(ctx):
    rows = []
    for path in sorted(ctx.reported_dir.glob("*.json")) if ctx.reported_dir.is_dir() else []:
        try:
            data = json.loads(path.read_text())
            if data.get("released"):
                if ctx.now - float(data.get("ts", 0)) > TOMBSTONE_TTL:
                    path.unlink(missing_ok=True)
                continue
            status = REPORT_STATUS[data["state"]]
            pid, pstart = data.get("pid"), data.get("pstart")
            if pid:
                if not is_alive(int(pid), pstart, ctx.proc_root):
                    path.unlink(missing_ok=True)
                    continue
            elif ctx.now - float(data.get("ts", 0)) > REPORT_TTL:
                path.unlink(missing_ok=True)
                continue
            row = make_row("reported", str(data["source"]), str(data.get("agent") or "agent").lower(),
                           status, float(data.get("since") or ctx.now), str(data.get("cwd") or ""),
                           pid=int(pid) if pid else None, session_id=str(data.get("session_id") or ""),
                           title=str(data.get("message") or ""))
            row["stored_pane"] = data.get("herdr_pane")
            rows.append(row)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return rows


# ---- join --------------------------------------------------------------------

def make_row(source, ident, agent, state, since, cwd, pid=None, pane=None, session_id="", kind="interactive", title=""):
    return {
        "key": f"{source}:{ident}", "source": source, "via": [source], "agent": agent,
        "title": title or "", "cwd": cwd, "project": os.path.basename(cwd.rstrip("/")) or cwd or "",
        "state": state, "since": since, "pid": pid, "pane": pane, "session_id": session_id,
        "kind": kind,
    }


def join_rows(claude, reported, herdr_panes, herdr_rows, ctx):
    """One row per agent. Claude and reported rows come first and keep their
    state (more precise than herdr's screen detection); herdr only adds the pane
    to focus and, for panes nobody else claimed, a row of its own.

    Join key, strongest first: session id (herdr agent_session == Claude
    sessionId or reported session id), then the herdr pane id (stored report,
    or HERDR_PANE_ID in /proc/<pid>/environ). Two rows from the same source
    never merge: two Claude sessions in one pane stay two rows."""
    sid_to_pane = {}
    for pane, info in herdr_panes.items():
        for sid in session_ids(info):
            sid_to_pane.setdefault(sid, pane)

    def pane_for(row):
        pane = sid_to_pane.get(row["session_id"]) if row["session_id"] else None
        if pane:
            return pane
        found = row.get("stored_pane")
        if not found and row["pid"]:
            found = read_env_var(row["pid"], "HERDR_PANE_ID", ctx.proc_root)
        return found if found in herdr_panes else None

    rows, by_pane, by_sid = [], {}, {}
    for row in claude + reported:
        pane = pane_for(row)
        host = None
        for cand in (by_sid.get(row["session_id"]) if row["session_id"] else None,
                     by_pane.get(pane) if pane else None):
            if cand is not None and cand["source"] != row["source"]:
                host = cand
                break
        if host is not None:
            host["via"].append(row["source"])
            host["pane"] = host["pane"] or pane
            host["title"] = host["title"] or row["title"]
            host["cwd"] = host["cwd"] or row["cwd"]
            host["pid"] = host["pid"] or row["pid"]
            host["project"] = host["project"] or row["project"]
            continue
        row["pane"] = pane
        rows.append(row)
        if pane:
            by_pane.setdefault(pane, row)
        if row["session_id"]:
            by_sid.setdefault(row["session_id"], row)
    for row in rows:
        if row["pane"]:
            row["via"].append("herdr")
    claimed = {r["pane"] for r in rows if r["pane"]}
    for row in herdr_rows:
        if row["pane"] not in claimed:
            rows.append(row)
    return rows


# ---- snapshot ----------------------------------------------------------------

def load_json(path, default):
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def snapshot(ctx):
    claude, _ = claude_rows(ctx)
    reported = reported_rows(ctx)
    herdr_snap = ctx.herdr()
    panes = herdr_entries(herdr_snap)
    herdr = [herdr_row(p, i, ctx) for p, i in panes.items()]
    herdr = [r for r in herdr if r["known_state"] or r["title"] or r["cwd"]]
    rows = join_rows(claude, reported, panes, herdr, ctx)

    # herdr rows have no timestamp of their own: remember when each state was first seen
    seen_path = ctx.state / "seen.json"
    seen = load_json(seen_path, {})
    new_seen = {}
    for row in rows:
        if row["source"] == "herdr":
            old = seen.get(row["key"], {})
            row["since"] = old["since"] if old.get("sig") == row["sig"] and "since" in old else ctx.now
            new_seen[row["key"]] = {"sig": row["sig"], "since": row["since"]}
    if new_seen != seen:
        try:
            write_atomic(seen_path, new_seen)
        except OSError:
            pass

    # a done row stays unread (highlighted) until clicked
    acks_path = ctx.state / "acks.json"
    acks = load_json(acks_path, {})
    live_acks = {}
    for row in rows:
        sig = str(int(row["since"]))
        row["unread"] = row["state"] == "done" and acks.get(row["key"]) != sig
        if row["state"] == "done" and not row["unread"]:
            live_acks[row["key"]] = sig
    if live_acks != acks:
        try:
            write_atomic(acks_path, live_acks)
        except OSError:
            pass

    for row in rows:
        row["age"] = max(0, int(ctx.now - row["since"]))
        row["project"] = row["project"] or "?"
        row["agent_label"] = AGENT_LABELS.get(row["agent"], row["agent"])
        row["title"] = row["title"] or row["project"]
        for private in ("sig", "known_state", "stored_pane"):
            row.pop(private, None)
    rows.sort(key=lambda r: (STATE_ORDER.get(r["state"], 9), r["age"]))
    return {
        "waiting": sum(1 for r in rows if r["state"] in ATTENTION),
        "total": len(rows),
        "rows": rows,
    }


# ---- open (ack + focus) --------------------------------------------------------

def hypr_clients():
    out = subprocess.run(["hyprctl", "-j", "clients"], capture_output=True, text=True, timeout=5)
    return json.loads(out.stdout)


def find_window(pid, clients, proc_root="/proc"):
    """First Hyprland client whose pid is `pid` or one of its ancestors."""
    by_pid = {c["pid"]: c for c in clients}
    for p in ancestors(pid, proc_root):
        if p in by_pid:
            return by_pid[p]
    return None


def open_row(ctx, key):
    snap = snapshot(ctx)
    row = next((r for r in snap["rows"] if r["key"] == key), None)
    if row is None:
        return False
    if row["state"] == "done":
        acks_path = ctx.state / "acks.json"
        acks = load_json(acks_path, {})
        acks[key] = str(int(row["since"]))
        write_atomic(acks_path, acks)
    if row["pane"] and ctx.run(["herdr", "agent", "focus", row["pane"]]):
        return True
    if row["source"] == "claude" and row["kind"] == "bg":
        ctx.popen(["xdg-terminal-exec", "claude", "agents"])
        return True
    if row["pid"]:
        window = find_window(row["pid"], ctx.clients(), ctx.proc_root)
        if window is not None:
            return ctx.run(["hyprctl", "dispatch", "focuswindow", f"address:{window['address']}"])
    return False


# ---- CLI -----------------------------------------------------------------------

class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


# Hermes Agent shell hooks pipe {"hook_event_name", "session_id", "cwd", ...} on stdin.
HERMES_EVENTS = {
    "pre_llm_call": "working",
    "post_approval_response": "working",
    "on_human_input_resolved": "working",
    "pre_approval_request": "blocked",
    "on_human_input_request": "blocked",
    "agent_loop_stopped": "idle",
}


def hermes_hook(state_dir, payload, parent_pid=None, now=None):
    """Map one Hermes hook payload to report/release. Returns what it did."""
    session = str(payload.get("session_id") or "")
    if not session:
        return None
    source = f"hermes-{session}"
    seq = int((time.time() if now is None else now) * 1000)
    event = payload.get("hook_event_name", "")
    if event in ("on_session_end", "on_session_finalize"):
        do_release(state_dir, source, seq, now=now)
        return "release"
    state = HERMES_EVENTS.get(event)
    if state is None:
        return None
    do_report(state_dir, source, "hermes", state, seq, cwd=payload.get("cwd") or "",
              session_id=session, parent_pid=parent_pid, now=now)
    return state


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "hermes-hook":
        try:  # same contract as report: exit 0 fast, say nothing
            hermes_hook(Ctx().state, json.load(sys.stdin), parent_pid=os.getppid())
        except BaseException:
            pass
        return 0
    if cmd in ("report", "release"):
        try:  # hooks call these: exit 0 fast, say nothing
            p = QuietParser(add_help=False)
            p.add_argument("--source", required=True)
            p.add_argument("--seq", type=int, default=int(time.time() * 1000))
            if cmd == "report":
                p.add_argument("--agent", required=True)
                p.add_argument("--state", required=True)
                p.add_argument("--message", default="")
                p.add_argument("--cwd", default="")
                p.add_argument("--session-id", default="")
            args, _ = p.parse_known_args(argv[2:])
            ctx = Ctx()
            if cmd == "report":
                do_report(ctx.state, args.source, args.agent, args.state, args.seq, args.message,
                          args.cwd, args.session_id, parent_pid=os.getppid())
            else:
                do_release(ctx.state, args.source, args.seq)
        except BaseException:
            pass
        return 0
    if cmd == "snapshot":
        print(json.dumps(snapshot(Ctx())))
        return 0
    if cmd == "open" and len(argv) > 2:
        return 0 if open_row(Ctx(), argv[2]) else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
