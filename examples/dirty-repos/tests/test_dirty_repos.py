import importlib.util
import os
import subprocess
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
