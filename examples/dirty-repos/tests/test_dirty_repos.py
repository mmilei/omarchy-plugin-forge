import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "dirty-repos.py"
spec = importlib.util.spec_from_file_location("dirty_repos", SCRIPT)
dr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dr)

ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")


def g(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, env=ENV)


def init(path, remote=None):
    Path(path).mkdir(parents=True, exist_ok=True)
    g(path, "init", "-q", "-b", "main")
    (Path(path) / "a.txt").write_text("a\n")
    g(path, "add", "a.txt")
    g(path, "commit", "-q", "-m", "init")
    if remote:
        g(path, "remote", "add", "origin", str(remote))
        g(path, "push", "-q", "-u", "origin", "main")


class Status(unittest.TestCase):
    def test_parse_status(self):
        text = ("# branch.oid abc\n# branch.head feat\n# branch.upstream origin/feat\n"
                "# branch.ab +2 -1\n1 .M N... 100644 100644 100644 a b x\n? new.txt\n")
        info = dr.parse_status(text)
        self.assertEqual((info["branch"], info["upstream"], info["ahead"], info["behind"], info["changed"]),
                         ("feat", "origin/feat", 2, 1, 2))


class Scan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._env = os.environ.copy()
        os.environ.update(ENV)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))
        self.bare = self.root / "remotes"
        self.bare.mkdir()

    def remote(self, name):
        p = self.bare / (name + ".git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(p)], check=True, env=ENV)
        return p

    def test_classification(self):
        r = self.root / "work"
        init(r / "clean", self.remote("clean"))
        init(r / "dirty", self.remote("dirty"))
        (r / "dirty" / "a.txt").write_text("changed\n")
        (r / "dirty" / "new.txt").write_text("x\n")
        init(r / "ahead", self.remote("ahead"))
        (r / "ahead" / "b.txt").write_text("b\n")
        g(r / "ahead", "add", "b.txt")
        g(r / "ahead", "commit", "-q", "-m", "b")
        init(r / "unpushed")
        g(r / "unpushed", "remote", "add", "origin", str(self.remote("unpushed")))
        init(r / "localonly")                      # no remote: nothing to push to
        (r / "empty").mkdir()
        g(r / "empty", "init", "-q")               # no commits
        # behind: clone, then push from the clone, so the first copy is behind only after fetch
        init(r / "behind", self.remote("behind"))
        clone = self.root / "other"
        subprocess.run(["git", "clone", "-q", str(self.bare / "behind.git"), str(clone)], check=True, env=ENV)
        (clone / "c.txt").write_text("c\n")
        g(clone, "add", "c.txt")
        g(clone, "commit", "-q", "-m", "c")
        g(clone, "push", "-q")
        g(r / "behind", "fetch", "-q")

        snap = dr.snapshot(str(self.root / "work"), 1)
        rows = {x["name"]: x for x in snap["repos"]}
        self.assertEqual(sorted(rows), ["ahead", "behind", "dirty", "unpushed"])
        self.assertEqual(snap["scanned"], 7)
        self.assertEqual(rows["dirty"]["changed"], 2)
        self.assertEqual(rows["ahead"]["ahead"], 1)
        self.assertEqual(rows["behind"]["behind"], 1)
        self.assertTrue(rows["unpushed"]["no_upstream"])
        self.assertEqual(rows["dirty"]["branch"], "main")

    def test_depth_skips_and_no_descent(self):
        init(self.root / "a" / "deep" / "repo")
        init(self.root / "top")
        init(self.root / "top" / "nested")
        init(self.root / "node_modules" / "pkg")
        init(self.root / ".hidden" / "r")
        init(self.root / "x" / "venv" / "r")
        names = lambda d: sorted(p.name for p in dr.find_repos(self.root, d))
        self.assertEqual(names(1), ["top"])
        self.assertEqual(names(2), ["top"])          # a/deep/repo is level 3; nested is inside top
        self.assertEqual(names(3), ["repo", "top"])
        self.assertEqual(names(0), [])

    def test_root_itself_and_missing_root(self):
        init(self.root / "solo")
        self.assertEqual(dr.find_repos(self.root / "solo", 0), [self.root / "solo"])
        snap = dr.snapshot(str(self.root / "nope"), 2)
        self.assertEqual((snap["count"], snap["missing_root"]), (0, True))

    def test_worktrees_and_submodule_marker(self):
        init(self.root / "main")
        g(self.root / "main", "worktree", "add", "-q", "-b", "wt", str(self.root / "main-wt"))
        (self.root / "main-wt" / "z.txt").write_text("z\n")
        found = sorted(p.name for p in dr.find_repos(self.root, 1))
        self.assertEqual(found, ["main", "main-wt"])
        rows = {r["name"]: r for r in dr.snapshot(str(self.root), 1)["repos"]}
        self.assertEqual(rows["main-wt"]["changed"], 1)
        self.assertEqual(rows["main-wt"]["branch"], "wt")
        self.assertNotIn("main", rows)               # clean and no remote
        sub = self.root / "sub"
        sub.mkdir()
        (sub / ".git").write_text("gitdir: ../main/.git/modules/sub\n")
        self.assertNotIn(sub, dr.find_repos(self.root, 1))

    def test_read_only(self):
        init(self.root / "r")
        (self.root / "r" / "a.txt").write_text("changed\n")
        idx = self.root / "r" / ".git" / "index"
        before = (idx.stat().st_mtime_ns, idx.read_bytes())
        before_tree = sorted(str(p) for p in (self.root / "r" / ".git").rglob("*"))
        dr.snapshot(str(self.root), 1)
        self.assertEqual((idx.stat().st_mtime_ns, idx.read_bytes()), before)
        self.assertEqual(sorted(str(p) for p in (self.root / "r" / ".git").rglob("*")), before_tree)


class Watch(unittest.TestCase):
    """The watch loop with an injected clock, repo finder, git inspector and fingerprint."""

    def setUp(self):
        self.root = Path("/fake/root")
        self.repos = [Path("/fake/root/a"), Path("/fake/root/b")]
        self.dirty = {self.repos[0]: 1, self.repos[1]: 0}      # changed-file counts
        self.fp = {r: 0 for r in self.repos}
        self.t = 0.0
        self.inspected, self.finds = [], []
        self._old = dr.effective_root
        dr.effective_root = lambda arg: self.root
        self.addCleanup(setattr, dr, "effective_root", self._old)
        self._isdir = Path.is_dir
        Path.is_dir = lambda p: True if p == self.root else self._isdir(p)
        self.addCleanup(setattr, Path, "is_dir", self._isdir)

    def inspect(self, path):
        self.inspected.append(path)
        n = self.dirty[path]
        return {"name": path.name, "path": str(path), "branch": "main", "changed": n, "ahead": 0,
                "behind": 0, "upstream": "", "no_upstream": False} if n else None

    def find(self, root, depth):
        self.finds.append(root)
        return list(self.repos)

    def run_watch(self, script, **kw):
        """script: (command-or-None, seconds-elapsed, effect); one entry per wait() call."""
        out, waits, steps = io.StringIO(), [], list(script)

        def wait(timeout):
            waits.append(timeout)
            if not steps:
                return "stop"
            cmd, dt, effect = steps.pop(0)
            self.t += dt
            if effect:
                effect()
            return cmd

        dr.watch(out, wait, open_interval=60, closed_interval=15, now=lambda: self.t,
                 find=self.find, inspect_fn=self.inspect, fingerprint_fn=lambda r: self.fp[r], **kw)
        return [json.loads(line) for line in out.getvalue().splitlines()], waits

    def test_prints_once_at_start_and_stays_quiet(self):
        snaps, waits = self.run_watch([(None, 15, None), (None, 15, None)])
        self.assertEqual(len(snaps), 1)
        self.assertEqual((snaps[0]["count"], snaps[0]["scanned"]), (1, 2))
        self.assertEqual(waits, [15, 15, 15])
        self.assertEqual(self.inspected, self.repos)           # only the start scan

    def test_closed_runs_git_only_for_repos_whose_fingerprint_moved(self):
        def touch():
            self.dirty[self.repos[1]] = 3
            self.fp[self.repos[1]] = 1
        snaps, _ = self.run_watch([(None, 15, None), (None, 15, touch), (None, 15, None)])
        self.assertEqual([s["count"] for s in snaps], [1, 2])
        self.assertEqual(self.inspected, self.repos + [self.repos[1]])

    def test_closed_repo_turning_clean_is_printed(self):
        def clean():
            self.dirty[self.repos[0]] = 0
            self.fp[self.repos[0]] = 1
        snaps, _ = self.run_watch([(None, 15, clean)])
        self.assertEqual([s["count"] for s in snaps], [1, 0])

    def test_full_rescan_catches_edits_that_move_no_fingerprint(self):
        def edit():
            self.dirty[self.repos[1]] = 2
        snaps, _ = self.run_watch([(None, 100, edit), (None, 100, None), (None, 150, None)])
        self.assertEqual([s["count"] for s in snaps], [1, 2])   # seen at t=350, not before
        self.assertEqual(self.inspected.count(self.repos[1]), 2)

    def test_rediscovery_every_ten_minutes_picks_up_new_and_gone_repos(self):
        new = Path("/fake/root/c")

        def add():
            self.repos.append(new)
            self.dirty[new] = 1
            self.fp[new] = 0
        def drop():
            self.repos.remove(self.repos[0])
        snaps, _ = self.run_watch([(None, 500, add), (None, 100, None), (None, 600, drop)])
        self.assertEqual(len(self.finds), 3)
        self.assertEqual([(s["scanned"], s["count"]) for s in snaps], [(2, 1), (3, 2), (2, 1)])
        self.assertEqual([r["name"] for r in snaps[-1]["repos"]], ["c"])

    def test_open_prints_now_rescans_everything_and_uses_open_interval(self):
        snaps, waits = self.run_watch([("open", 1, None), (None, 60, None)])
        self.assertEqual(len(snaps), 2)                         # start + forced print on open
        self.assertEqual(waits, [15, 60, 60])
        self.assertEqual(len(self.inspected), 6)                # start, open, one open tick
        self.assertEqual(len(self.finds), 2)                    # rediscovered on open

    def test_open_ticks_print_only_when_something_changed(self):
        def edit():
            self.dirty[self.repos[1]] = 1                       # no fingerprint move needed
        snaps, _ = self.run_watch([("open", 1, None), (None, 60, None), (None, 60, edit)])
        self.assertEqual([s["count"] for s in snaps], [1, 1, 2])

    def test_closing_goes_back_to_slow_and_quiet(self):
        snaps, waits = self.run_watch([("open", 1, None), ("closed", 1, None), (None, 15, None)])
        self.assertEqual(waits, [15, 60, 15, 15])
        self.assertEqual(len(snaps), 2)

    def test_start_in_open_mode_and_stop(self):
        _, waits = self.run_watch([("stop", 0, None)], mode="open")
        self.assertEqual(waits, [60])

    def test_missing_root_lists_nothing(self):
        Path.is_dir = self._isdir
        self.root = Path("/nonexistent-root")
        snaps, _ = self.run_watch([])
        self.assertEqual((snaps[0]["missing_root"], snaps[0]["scanned"]), (True, 0))
        self.assertEqual(self.finds, [])

    def test_stdin_commands(self):
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        wait = dr.stdin_commands(r)
        self.assertIsNone(wait(0.01))
        os.write(w, b"closed\nopen\n")
        self.assertEqual(wait(1), "open")
        os.close(w)
        self.assertEqual(wait(1), "stop")                       # EOF


class Fingerprint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self._env = os.environ.copy()
        os.environ.update(ENV)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))

    def test_moves_on_stage_commit_branch_and_new_file_but_not_on_snapshot(self):
        r = self.root / "r"
        init(r)
        fp = dr.fingerprint(r)
        dr.snapshot(str(self.root), 1)
        self.assertEqual(dr.fingerprint(r), fp)                 # reading changes nothing
        steps = [lambda: (r / "new.txt").write_text("x\n"),
                 lambda: g(r, "add", "new.txt"),
                 lambda: g(r, "commit", "-q", "-m", "n"),
                 lambda: g(r, "branch", "feature/x"),
                 lambda: g(r, "checkout", "-q", "feature/x")]
        for step in steps:
            step()
            new = dr.fingerprint(r)
            self.assertNotEqual(new, fp)
            fp = new

    def test_linked_worktree_uses_its_own_gitdir(self):
        init(self.root / "main")
        g(self.root / "main", "worktree", "add", "-q", "-b", "wt", str(self.root / "wt"))
        wt = self.root / "wt"
        fp = dr.fingerprint(wt)
        self.assertIsNotNone(fp[0])                             # its own index
        g(wt, "checkout", "-q", "-b", "other")
        self.assertNotEqual(dr.fingerprint(wt), fp)

    def test_cli_watch_streams_lines_and_stops_on_eof(self):
        init(self.root / "r")
        (self.root / "r" / "a.txt").write_text("changed\n")
        p = subprocess.Popen([sys.executable, str(SCRIPT), "watch", "--mode", "open", "--root",
                              str(self.root), "--depth", "1"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
        try:
            snap = json.loads(p.stdout.readline())
            self.assertEqual((snap["count"], snap["repos"][0]["changed"]), (1, 1))
            p.stdin.write("closed\nopen\n")
            p.stdin.flush()
            self.assertEqual(json.loads(p.stdout.readline())["count"], 1)   # forced print on open
        finally:
            p.stdin.close()
        self.assertEqual(p.wait(timeout=20), 0)
        p.stdout.close()


class Cli(unittest.TestCase):
    def test_bad_args_and_open_refuses_non_repo(self):
        self.assertEqual(dr.main(["x"]), 2)
        self.assertEqual(dr.main(["x", "open"]), 2)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(dr.main(["x", "open", d]), 1)
        self.assertEqual(dr.main(["x", "open", "/nonexistent-dir"]), 1)

    def test_open_launches_terminal_in_repo(self):
        with tempfile.TemporaryDirectory() as d:
            init(Path(d) / "r")
            fake = Path(d) / "bin"
            fake.mkdir()
            log = Path(d) / "log"
            (fake / "xdg-terminal-exec").write_text(f'#!/bin/sh\necho "$@" > {log}\n')
            (fake / "xdg-terminal-exec").chmod(0o755)
            old = os.environ["PATH"]
            os.environ["PATH"] = f"{fake}:{old}"
            try:
                self.assertEqual(dr.main(["x", "open", str(Path(d) / "r")]), 0)
                for _ in range(50):
                    if log.exists() and log.read_text():
                        break
                    subprocess.run(["sleep", "0.1"])
            finally:
                os.environ["PATH"] = old
            self.assertEqual(log.read_text().strip(), f"--dir={Path(d) / 'r'}")

    def test_depth_clamp(self):
        self.assertEqual(dr.clamp_depth("abc"), 2)
        self.assertEqual(dr.clamp_depth("99"), dr.MAX_DEPTH)
        self.assertEqual(dr.clamp_depth(-3), 0)


if __name__ == "__main__":
    unittest.main()
