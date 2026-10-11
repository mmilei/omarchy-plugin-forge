#!/usr/bin/env python3
"""Git repositories under a root folder with work not yet saved or pushed.

  dirty-repos.py snapshot [root] [depth]
      JSON: {"root", "depth", "scanned", "count", "repos": [{name, path, branch,
      changed, ahead, behind, upstream, no_upstream}]}. Defaults: ~/git, depth 2.
      Only repos with something pending are listed.
  dirty-repos.py watch [--root R] [--depth D] [--interval S] [--closed-interval S] [--mode open|closed]
      One snapshot per line (same shape), until stdin closes. Stdin words: `open` (panel
      showing: rediscover, rescan everything and print now), `closed`, `stop`; EOF stops it.
      Open: full rescan every --interval seconds. Closed: every --closed-interval seconds
      stat each repo's .git/index, HEAD, refs and the work-tree root, and run `git status`
      only for repos whose fingerprint moved; plus a full rescan every FULL_EVERY seconds
      (editing a tracked file does not touch those) and re-discovery every DISCOVER_EVERY.
      Prints at start, on `open`, and afterwards only when the snapshot changed.
  dirty-repos.py open <path>
      Terminal in <path> (xdg-terminal-exec); refuses anything that is not a git work tree.

Read-only on the repos: only `git status` (run with --no-optional-locks, so it does not
even refresh the index) and `git remote`. Never fetches: ahead/behind are relative to the
last fetch. Depth 1 = direct children of root; depth 0 = root itself. The scan does not
descend into a repo it found, into dot-directories, symlinks or dependency/build folders.
Submodules are skipped (their parent reports them). Linked worktrees are listed as their
own repo: they have their own working tree and their own branch, so nothing is counted twice.

Demo hook for .forge/seed: if <plugin>/.forge/demo-root exists, its first line replaces the
root setting. Stdlib only.
"""
import argparse
import json
import os
import select
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_ROOT = "~/git"
DEFAULT_DEPTH = 2
MAX_DEPTH = 6
SKIP = {
    "node_modules", "venv", "site-packages", "__pycache__", "target", "build", "dist",
    "vendor", "bower_components", "Pods",
}
GIT_TIMEOUT = 15
FULL_EVERY = 300          # closed: full rescan, for edits that touch no fingerprinted file
DISCOVER_EVERY = 600      # closed: look for new/removed repos
DEMO_ROOT = Path(__file__).resolve().parent.parent / ".forge" / "demo-root"


def git(path, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C", GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", "--no-optional-locks", "-C", str(path), *args],
                          capture_output=True, text=True, timeout=GIT_TIMEOUT, env=env)


def is_submodule(dotgit):
    """A `.git` file whose gitdir sits under .../modules/ belongs to a submodule."""
    try:
        target = dotgit.read_text().split(":", 1)[1].strip()
    except (OSError, IndexError):
        return False
    return "/modules/" in target.replace("\\", "/")


def find_repos(root, depth):
    """Work-tree directories under root, at most `depth` levels down."""
    found = []

    def walk(d, level):
        dotgit = d / ".git"
        if dotgit.is_dir() or (dotgit.is_file() and not is_submodule(dotgit)):
            found.append(d)
            return                      # nested checkouts are not this repo's business
        if dotgit.is_file() or level >= depth:
            return
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if (e.name.startswith(".") or e.name in SKIP
                    or e.is_symlink() or not e.is_dir(follow_symlinks=False)):
                continue
            walk(Path(e.path), level + 1)

    walk(root, 0)
    return found


def parse_status(text):
    """Parse `git status --porcelain=v2 --branch` into a dict."""
    info = {"branch": "", "oid": "", "upstream": "", "ahead": 0, "behind": 0, "changed": 0}
    for line in text.splitlines():
        if line.startswith("# branch.head "):
            info["branch"] = line[14:]
        elif line.startswith("# branch.oid "):
            info["oid"] = line[13:]
        elif line.startswith("# branch.upstream "):
            info["upstream"] = line[18:]
        elif line.startswith("# branch.ab "):
            plus, minus = line[12:].split()
            info["ahead"], info["behind"] = int(plus[1:]), int(minus[1:])
        elif line[:2] in ("1 ", "2 ", "u ", "? "):
            info["changed"] += 1
    return info


def inspect(path):
    """Row for one repo, or None when it is clean (or unreadable)."""
    try:
        res = git(path, "status", "--porcelain=v2", "--branch", "--untracked-files=normal")
        if res.returncode != 0:
            return None
        info = parse_status(res.stdout)
        # A branch that was never pushed counts, but only where there is a remote to push to,
        # and not for a detached HEAD or a repo without commits.
        no_upstream = False
        if not info["upstream"] and info["branch"] != "(detached)" and info["oid"] != "(initial)":
            no_upstream = bool(git(path, "remote").stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return None
    if not (info["changed"] or info["ahead"] or info["behind"] or no_upstream):
        return None
    return {"name": path.name, "path": str(path), "branch": info["branch"],
            "changed": info["changed"], "ahead": info["ahead"], "behind": info["behind"],
            "upstream": info["upstream"], "no_upstream": no_upstream}


def effective_root(arg):
    if DEMO_ROOT.is_file():
        lines = DEMO_ROOT.read_text().splitlines()
        if lines and lines[0].strip():
            return Path(lines[0].strip()).expanduser()
    return Path(arg or DEFAULT_ROOT).expanduser()


def clamp_depth(value):
    try:
        return max(0, min(MAX_DEPTH, int(float(value))))
    except (TypeError, ValueError):
        return DEFAULT_DEPTH


def build(root, depth, repos, rows):
    rows = sorted((r for r in rows if r), key=lambda r: (r["name"].lower(), r["path"]))
    return {"root": str(root), "depth": depth, "scanned": len(repos), "count": len(rows),
            "missing_root": not root.is_dir(), "repos": rows}


def inspect_all(repos, inspect_fn=inspect):
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(inspect_fn, repos))


def snapshot(root_arg=None, depth_arg=None):
    root, depth = effective_root(root_arg), clamp_depth(depth_arg)
    repos = find_repos(root, depth) if root.is_dir() else []
    return build(root, depth, repos, inspect_all(repos))


def _stat(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def fingerprint(path):
    """Cheap change marker for one work tree: the stats of .git/index, HEAD, packed-refs and
    the refs directories, plus the work-tree root. Linked worktrees keep index/HEAD in their
    own gitdir and refs in the common one."""
    gitdir = common = path / ".git"
    if gitdir.is_file():
        try:
            gitdir = common = (path / gitdir.read_text().split(":", 1)[1].strip()).resolve()
            common = (gitdir / Path((gitdir / "commondir").read_text().strip())).resolve() \
                if (gitdir / "commondir").is_file() else gitdir
        except (OSError, IndexError):
            pass
    return (_stat(gitdir / "index"), _stat(gitdir / "HEAD"), _stat(common / "packed-refs"),
            _stat(common / "refs"), _stat(common / "refs" / "heads"),
            _stat(common / "refs" / "remotes"), _stat(path))


def stdin_commands(fd=0):
    """wait(timeout): block up to `timeout` s for a command line on `fd`; return the last
    word ("open", "closed"), None on timeout, or "stop" on EOF (the shell went away)."""
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


def watch(out, wait, root_arg=None, depth_arg=None, open_interval=60.0, closed_interval=15.0,
          mode="closed", now=time.monotonic, find=find_repos, inspect_fn=inspect,
          fingerprint_fn=fingerprint):
    """Print one snapshot per line on `out` until `wait` returns "stop".

    `wait(timeout)` is both the sleep and the control channel: None when the timeout
    elapsed, or "open" / "closed" / "stop". Every external dependency is a parameter.
    """
    st = {"repos": [], "fps": {}, "rows": {}, "discovered": None, "full": None, "last": None,
          "root": None, "depth": None}

    def rescan(repos):
        for r, row in zip(repos, inspect_all(repos, inspect_fn)):
            st["rows"][r] = row
            st["fps"][r] = fingerprint_fn(r)

    def tick(force):
        t = now()
        full = force or mode == "open" or st["full"] is None or t - st["full"] >= FULL_EVERY
        if force or st["discovered"] is None or t - st["discovered"] >= DISCOVER_EVERY:
            st["root"], st["depth"] = effective_root(root_arg), clamp_depth(depth_arg)
            st["repos"] = list(find(st["root"], st["depth"])) if st["root"].is_dir() else []
            st["discovered"] = t
            gone = set(st["rows"]) - set(st["repos"])
            for r in gone:
                del st["rows"][r], st["fps"][r]
        if full:
            st["full"] = t
            rescan(st["repos"])
        else:
            rescan([r for r in st["repos"] if r not in st["fps"] or fingerprint_fn(r) != st["fps"][r]])
        snap = build(st["root"], st["depth"], st["repos"], [st["rows"].get(r) for r in st["repos"]])
        if force or snap != st["last"]:
            st["last"] = snap
            out.write(json.dumps(snap) + "\n")
            out.flush()

    tick(True)
    while True:
        cmd = wait(open_interval if mode == "open" else closed_interval)
        if cmd == "stop":
            return
        if cmd in ("open", "closed"):
            mode = cmd
        tick(cmd == "open")


def open_terminal(path):
    p = Path(path)
    if not p.is_dir() or git(p, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        return False
    subprocess.Popen(["xdg-terminal-exec", f"--dir={p}"], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "watch":
        ap = argparse.ArgumentParser(prog="dirty-repos.py watch")
        ap.add_argument("--root")
        ap.add_argument("--depth")
        ap.add_argument("--interval", type=float, default=60.0)
        ap.add_argument("--closed-interval", type=float, default=15.0)
        ap.add_argument("--mode", choices=("open", "closed"), default="closed")
        a = ap.parse_args(argv[2:])
        try:
            watch(sys.stdout, stdin_commands(), a.root, a.depth, max(5.0, a.interval),
                  max(2.0, a.closed_interval), a.mode)
        except BrokenPipeError:
            pass
        return 0
    if cmd == "snapshot" and len(argv) <= 4:
        print(json.dumps(snapshot(*argv[2:4])))
    elif cmd == "open" and len(argv) == 3:
        if not open_terminal(argv[2]):
            print("not a git work tree: " + argv[2], file=sys.stderr)
            return 1
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
