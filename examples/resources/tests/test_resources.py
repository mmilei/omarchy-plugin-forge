import importlib.util
import json
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "resources.py"
spec = importlib.util.spec_from_file_location("resources", SCRIPT)
res = importlib.util.module_from_spec(spec)
spec.loader.exec_module(res)


def stat_line(pid, comm, ticks=0, start=1000):
    # fields 3.. : state ppid + 9 zeros (to field 13), utime, stime, 4 zeros, start at field 22
    utime, stime = ticks, 0
    rest = ["S", "1"] + ["0"] * 9 + [str(utime), str(stime)] + ["0"] * 6 + [str(start)] + ["0"] * 10
    return f"{pid} ({comm}) " + " ".join(rest)


def write_proc(root, stat_cpu, meminfo, loadavg, procs):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "stat").write_text(stat_cpu)
    (root / "meminfo").write_text(meminfo)
    (root / "loadavg").write_text(loadavg)
    for pid, (comm, ticks, start) in procs.items():
        d = root / str(pid)
        d.mkdir(exist_ok=True)
        (d / "stat").write_text(stat_line(pid, comm, ticks, start))


MEMINFO = """MemTotal:       16000000 kB
MemFree:         1000000 kB
MemAvailable:    4000000 kB
SwapTotal:       2000000 kB
SwapFree:        1500000 kB
"""


class Parsing(unittest.TestCase):
    def test_cpu_times_busy_excludes_idle_and_iowait(self):
        text = "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 50 0 50 350 50 0 0 0 0 0\nintr 5\n"
        t = res.parse_cpu_times(text)
        self.assertEqual(t["cpu"], (200, 1000))
        self.assertEqual(t["cpu0"], (100, 500))
        self.assertNotIn("intr", t)

    def test_cpu_percent(self):
        self.assertEqual(res.cpu_percent((0, 0), (50, 100)), 50.0)
        self.assertEqual(res.cpu_percent((10, 100), (10, 100)), 0.0)  # no time elapsed
        self.assertEqual(res.cpu_percent((50, 100), (40, 200)), 0.0)  # counter went backwards

    def test_memory_summary(self):
        m = res.memory_summary(res.parse_meminfo(MEMINFO))
        self.assertEqual(m["ram"]["total"], 16000000 * 1024)
        self.assertEqual(m["ram"]["used"], 12000000 * 1024)
        self.assertEqual(m["ram"]["percent"], 75.0)
        self.assertEqual(m["swap"]["percent"], 25.0)

    def test_no_swap_is_zero_percent(self):
        m = res.memory_summary(res.parse_meminfo("MemTotal: 100 kB\nMemAvailable: 50 kB\n"))
        self.assertEqual(m["swap"], {"used": 0, "total": 0, "percent": 0.0})

    def test_loadavg(self):
        self.assertEqual(res.parse_loadavg("0.50 1.25 2.00 1/300 1234\n"), [0.5, 1.25, 2.0])
        self.assertEqual(res.parse_loadavg(""), [0.0, 0.0, 0.0])

    def test_stat_with_spaces_and_parens_in_comm(self):
        comm, state, ticks, start = res.parse_stat(stat_line(7, "Web Content (x)", ticks=42, start=999))
        self.assertEqual((comm, state, ticks, start), ("Web Content (x)", "S", 42, 999))

    def test_stat_garbage(self):
        self.assertIsNone(res.parse_stat(""))
        self.assertIsNone(res.parse_stat("1 (x) S 1"))


class Temperature(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sys = Path(self.tmp.name)

    def chip(self, n, name, **temps):
        d = self.sys / "class/hwmon" / f"hwmon{n}"
        d.mkdir(parents=True)
        (d / "name").write_text(name + "\n")
        for k, v in temps.items():
            (d / f"{k}_input").write_text(str(v))

    def test_no_sensor(self):
        self.chip(0, "ADP0")
        self.assertIsNone(res.read_temperature(self.sys))
        self.assertIsNone(res.read_temperature(self.sys / "missing"))

    def test_coretemp_hottest(self):
        self.chip(0, "nvme", temp1=70000)
        self.chip(1, "coretemp", temp1=45000, temp2=52500)
        self.assertEqual(res.read_temperature(self.sys), 52.5)

    def test_thermal_zone_fallback(self):
        z = self.sys / "class/thermal/thermal_zone0"
        z.mkdir(parents=True)
        (z / "type").write_text("x86_pkg_temp\n")
        (z / "temp").write_text("61000\n")
        self.assertEqual(res.read_temperature(self.sys), 61.0)

    def test_absurd_value_is_ignored(self):
        self.chip(0, "k10temp", temp1=-300000)
        self.assertIsNone(res.read_temperature(self.sys))


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name) / "proc"

    def test_snapshot_deltas(self):
        # 2 cores. Core 0 goes 100% busy, core 1 idle, over 100 ticks each.
        before = "cpu  0 0 0 0 0 0 0 0\ncpu0 0 0 0 0 0 0 0 0\ncpu1 0 0 0 0 0 0 0 0\n"
        after = "cpu  100 0 0 100 0 0 0 0\ncpu0 100 0 0 0 0 0 0 0\ncpu1 0 0 0 100 0 0 0 0\n"
        write_proc(self.proc, before, MEMINFO, "1.00 0.50 0.25 1/1 1\n",
                   {10: ("busy", 0, 500), 11: ("idle", 5, 600), 12: ("old", 0, 700)})

        def sleeper(_):
            write_proc(self.proc, after, MEMINFO, "1.00 0.50 0.25 1/1 1\n",
                       {10: ("busy", 100, 500), 11: ("idle", 5, 600),
                        12: ("old", 300, 701)})  # pid 12 was reused: start differs

        snap = res.snapshot(proc_root=self.proc, sys_root=self.proc / "nosys", sleep=sleeper, uid=1000)
        self.assertEqual(snap["cpu"]["percent"], 50.0)
        self.assertEqual(snap["cpu"]["cores"], [100.0, 0.0])
        self.assertEqual(snap["ncpu"], 2)
        self.assertEqual(snap["load"], [1.0, 0.5, 0.25])
        self.assertIsNone(snap["temperature"])
        self.assertEqual([p["pid"] for p in snap["processes"]], [10])  # idle skipped, reused pid skipped
        self.assertEqual(snap["processes"][0]["cpu"], 100.0)  # one full core
        self.assertEqual(snap["processes"][0]["start"], 500)
        json.dumps(snap)

    def test_top_is_sorted_and_limited(self):
        before = {i: {"name": f"p{i}", "ticks": 0, "start": 1, "uid": 0} for i in range(1, 9)}
        after = {i: {"name": f"p{i}", "ticks": i * 10, "start": 1, "uid": 0} for i in range(1, 9)}
        rows = res.top_processes(before, after, 100, 5)
        self.assertEqual([r["pid"] for r in rows], [8, 7, 6, 5, 4])

    def test_cli_snapshot_runs_on_this_machine(self):
        out = subprocess.run([sys.executable, str(SCRIPT), "snapshot", "--interval", "0.1"],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        snap = json.loads(out.stdout)
        self.assertEqual(len(snap["cpu"]["cores"]), snap["ncpu"])
        self.assertLessEqual(len(snap["processes"]), 5)


class Kill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name)
        write_proc(self.proc, "", "", "", {500: ("victim", 0, 4242)})
        self.sent = []

    def send(self, pid, sig):
        self.sent.append((pid, sig))

    def uid(self):
        return (self.proc / "500").stat().st_uid

    def kill(self, pid, start, **kw):
        return res.kill_process(pid, start, proc_root=self.proc, uid=self.uid(), send=self.send,
                                own_pid=99999, **kw)

    def test_sends_sigterm_when_start_matches(self):
        self.assertIsNone(self.kill(500, 4242))
        self.assertEqual(self.sent, [(500, signal.SIGTERM)])

    def test_refuses_reused_pid(self):
        self.assertIn("different process", self.kill(500, 1))
        self.assertEqual(self.sent, [])

    def test_gone_process_is_success(self):
        self.assertIsNone(self.kill(501, 1))
        self.assertEqual(self.sent, [])

    def test_refuses_other_users_process(self):
        err = res.kill_process(500, 4242, proc_root=self.proc, uid=self.uid() + 1,
                               send=self.send, own_pid=99999)
        self.assertIn("another user", err)
        self.assertEqual(self.sent, [])

    def test_refuses_init_and_self(self):
        self.assertIn("refusing", self.kill(1, 1))
        self.assertIn("refusing", res.kill_process(99999, 1, proc_root=self.proc, send=self.send, own_pid=99999))
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
