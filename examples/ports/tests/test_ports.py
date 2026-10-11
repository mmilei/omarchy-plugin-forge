import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "ports.py"
spec = importlib.util.spec_from_file_location("ports", SCRIPT)
ports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ports)

SS = """\
LISTEN 0 4096 127.0.0.53%lo:53 0.0.0.0:*
LISTEN 0 5 0.0.0.0:8123 0.0.0.0:* users:(("python3",pid=@ME@,fd=3))
LISTEN 0 5 [::]:8123 [::]:* users:(("python3",pid=@ME@,fd=4))
LISTEN 0 511 127.0.0.1:3000 0.0.0.0:* users:(("node",pid=@ME@,fd=19))
LISTEN 0 128 192.168.1.5:9000 0.0.0.0:* users:(("lan",pid=@ME@,fd=5))
LISTEN 0 128 *:22 *:* users:(("sshd",pid=@OTHER@,fd=3))
LISTEN 0 128 127.0.0.1:631 0.0.0.0:*
"""


def fixture(me, other):
    return SS.replace("@ME@", str(me)).replace("@OTHER@", str(other))


class Parse(unittest.TestCase):
    def test_filters_local_only_and_dedupes_in_collect(self):
        text = fixture(10, 20)
        got = list(ports.parse_ss(text))
        self.assertIn((8123, "python3", 10), got)
        self.assertIn((3000, "node", 10), got)
        self.assertNotIn(9000, [g[0] for g in got])      # specific LAN address
        self.assertNotIn(631, [g[0] for g in got])       # no process info
        self.assertEqual(sum(1 for g in got if g[0] == 8123), 2)

    def test_project_of(self):
        self.assertEqual(ports.project_of("/home/u/code/api"), "api")
        self.assertEqual(ports.project_of("/"), "/")
        self.assertEqual(ports.project_of(""), "")


class Collect(unittest.TestCase):
    def test_own_processes_only_and_dedupe(self):
        me = os.getpid()
        # a pid that exists but is not ours: pid 1 (root)
        text = fixture(me, 1)
        rows = ports.collect(text)
        self.assertEqual([r["port"] for r in rows], [3000, 8123])
        self.assertEqual(rows[0]["pid"], me)
        self.assertEqual(rows[0]["cwd"], os.getcwd())
        self.assertEqual(rows[0]["project"], Path(os.getcwd()).name)


class Stop(unittest.TestCase):
    def test_stop_checks_start_time(self):
        p = subprocess.Popen(["sleep", "60"])
        self.addCleanup(p.kill)
        start = ports.starttime(p.pid)
        self.assertFalse(ports.stop(p.pid, start + 1))
        self.assertIsNone(p.poll())
        self.assertTrue(ports.stop(p.pid, start))
        p.wait(5)
        self.assertEqual(p.returncode, -15)
        self.assertFalse(ports.stop(p.pid, start))

    def test_cli_rejects_bad_args(self):
        self.assertEqual(ports.main(["x", "stop", "abc", "1"]), 2)
        self.assertEqual(ports.main(["x", "open", "../etc"]), 2)


if __name__ == "__main__":
    unittest.main()
