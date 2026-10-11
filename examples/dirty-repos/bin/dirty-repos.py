#!/usr/bin/env python3
"""Git repositories under a root folder with work not yet saved or pushed.

  dirty-repos.py snapshot [root] [depth]
      JSON: {"root", "depth", "scanned", "count", "repos": [{name, path, branch,
      changed, ahead, behind, upstream, no_upstream}]}. Defaults: ~/git, depth 2.
      Only repos with something pending are listed.
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
import json
import os
import subprocess
import sys
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


def snapshot(root_arg=None, depth_arg=None):
    root, depth = effective_root(root_arg), clamp_depth(depth_arg)
    repos = find_repos(root, depth) if root.is_dir() else []
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [r for r in pool.map(inspect, repos) if r]
    rows.sort(key=lambda r: (r["name"].lower(), r["path"]))
    return {"root": str(root), "depth": depth, "scanned": len(repos), "count": len(rows),
            "missing_root": not root.is_dir(), "repos": rows}


def open_terminal(path):
    p = Path(path)
    if not p.is_dir() or git(p, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        return False
    subprocess.Popen(["xdg-terminal-exec", f"--dir={p}"], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
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
