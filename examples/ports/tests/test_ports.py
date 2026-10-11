import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "ports.py"
spec = importlib.util.spec_from_file_location("ports", SCRIPT)
ports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ports)
read_listening = ports.read_listening

HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def tcp_line(host, port, inode, uid, state="0A"):
    return (f"   0: {host}:{port:04X} 00000000:0000 {state} 00000000:00000000 00:00000000 00000000 "
            f"{uid:5d}        0 {inode} 1 0000000000000000 100 0 0 10 0\n")


class Fake(unittest.TestCase):
    """A fake /proc: net/tcp, net/tcp6 and one process per entry of `procs`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name)
        (self.proc / "net").mkdir()
        self.me = os.getuid()
        self.set_net([], [])

    def set_net(self, tcp, tcp6):
        (self.proc / "net" / "tcp").write_text(HEADER + "".join(tcp))
        (self.proc / "net" / "tcp6").write_text(HEADER + "".join(tcp6))

    def add_proc(self, pid, name, inodes, cwd="/home/u/code/api"):
        d = self.proc / str(pid)
        (d / "fd").mkdir(parents=True, exist_ok=True)
        (d / "comm").write_text(name + "\n")
        (d / "stat").write_text(f"{pid} ({name}) S " + " ".join(["0"] * 18 + ["4242"] + ["0"] * 10) + "\n")
        if cwd:
            (d / "cwd").unlink(missing_ok=True)
            (d / "cwd").symlink_to(cwd)
        for i, inode in enumerate(inodes):
            (d / "fd" / str(3 + i)).unlink(missing_ok=True)
            (d / "fd" / str(3 + i)).symlink_to(f"socket:[{inode}]")


class Listening(Fake):
    def test_filters_state_uid_and_address(self):
        self.set_net(
            [tcp_line("0100007F", 3000, 11, self.me),            # 127.0.0.1
             tcp_line("00000000", 8123, 12, self.me),            # 0.0.0.0
             tcp_line("0500A8C0", 9000, 13, self.me),            # LAN address
             tcp_line("3500007F", 53, 14, self.me + 1),          # other user
             tcp_line("0100007F", 4000, 15, self.me, "01"),      # established
             tcp_line("0100007F", 631, 0, self.me)],             # no inode
            [tcp_line("0" * 32, 8123, 16, self.me),
             tcp_line("00000000000000000000000001000000", 5173, 17, self.me),
             tcp_line("0" * 24 + "0500A8C0", 7000, 18, self.me)])
        got = read_listening(self.proc)
        self.assertEqual(got, {11: 3000, 12: 8123, 16: 8123, 17: 5173})

    def test_missing_files_are_empty(self):
        self.assertEqual(read_listening(self.proc / "nowhere"), {})

    def test_project_of(self):
        self.assertEqual(ports.project_of("/home/u/code/api"), "api")
        self.assertEqual(ports.project_of("/"), "/")
        self.assertEqual(ports.project_of(""), "")


class Collect(Fake):
    def test_rows_dedupe_ports_and_skip_unowned_sockets(self):
        self.set_net([tcp_line("00000000", 8123, 12, self.me), tcp_line("0100007F", 3000, 11, self.me)],
                     [tcp_line("0" * 32, 8123, 16, self.me)])
        self.add_proc(10, "python3", [12, 16])      # same pid listens on both stacks
        self.add_proc(20, "node", [11])
        self.add_proc(30, "other", [99])             # not a listener of ours
        snap = ports.snapshot(self.proc)
        self.assertEqual(snap["count"], 2)
        self.assertEqual([(r["port"], r["pid"], r["name"]) for r in snap["ports"]],
                         [(3000, 20, "node"), (8123, 10, "python3")])
        self.assertEqual(snap["ports"][0]["project"], "api")
        self.assertEqual(snap["ports"][0]["start"], 4242)

    def test_listener_without_visible_process_is_not_counted(self):
        self.set_net([tcp_line("0100007F", 3000, 11, self.me)], [])
        self.assertEqual(ports.snapshot(self.proc), {"count": 0, "ports": []})

    def test_shared_socket_gives_one_row_per_process(self):
        self.set_net([tcp_line("0100007F", 3000, 11, self.me)], [])
        self.add_proc(10, "gunicorn", [11])
        self.add_proc(11, "gunicorn", [11])
        self.assertEqual([r["pid"] for r in ports.snapshot(self.proc)["ports"]], [10, 11])


class Watch(Fake):
    def run_watch(self, script, **kw):
        """script: list of (command-or-None, effect); wait() pops one per call, records
        the timeout it was given and runs the effect (a change of the fake /proc)."""
        out, waits, steps = io.StringIO(), [], list(script)

        def wait(timeout):
            waits.append(timeout)
            if not steps:
                return "stop"
            cmd, effect = steps.pop(0)
            if effect:
                effect()
            return cmd

        ports.watch(out, wait, open_interval=3, closed_interval=10, proc=self.proc, **kw)
        return [json.loads(line) for line in out.getvalue().splitlines()], waits

    def start_server(self, port=3000, inode=11, pid=10):
        def effect():
            self.set_net([tcp_line("0100007F", port, inode, self.me)], [])
            self.add_proc(pid, "node", [inode])
        return effect

    def count_maps(self):
        calls = []
        orig = ports.socket_pids
        ports.socket_pids = lambda *a, **k: calls.append(1) or orig(*a, **k)
        self.addCleanup(setattr, ports, "socket_pids", orig)
        return calls

    def test_prints_once_at_start_then_waits_closed_interval(self):
        snaps, waits = self.run_watch([(None, None), (None, None)])
        self.assertEqual(snaps, [{"count": 0, "ports": []}])
        self.assertEqual(waits, [10, 10, 10])

    def test_closed_maps_processes_only_when_the_listeners_change(self):
        calls = self.count_maps()
        snaps, _ = self.run_watch([(None, None), (None, None), (None, self.start_server()),
                                   (None, None), (None, None)])
        self.assertEqual([s["count"] for s in snaps], [0, 1])
        self.assertEqual(snaps[1]["ports"][0]["port"], 3000)
        self.assertEqual(len(calls), 2)               # start + the one change

    def test_closed_listener_disappearing_is_printed(self):
        self.start_server()()
        gone = lambda: self.set_net([], [])
        snaps, _ = self.run_watch([(None, None), (None, gone), (None, None)])
        self.assertEqual([s["count"] for s in snaps], [1, 0])

    def test_open_prints_now_and_uses_open_interval(self):
        self.start_server()()
        snaps, waits = self.run_watch([("open", None), (None, None)])
        self.assertEqual(len(snaps), 2)               # start + immediate on open
        self.assertEqual(waits, [10, 3, 3])

    def test_open_ticks_print_only_changed_rows(self):
        snaps, waits = self.run_watch([("open", None), (None, None), (None, self.start_server()),
                                       (None, None)])
        self.assertEqual([s["count"] for s in snaps], [0, 0, 1])

    def test_open_sees_a_process_change_with_the_same_ports(self):
        self.start_server(pid=10)()
        swap = lambda: self.add_proc(10, "node", [11], cwd="/home/u/code/other")
        snaps, _ = self.run_watch([("open", None), (None, swap)])
        self.assertEqual(snaps[-1]["ports"][0]["project"], "other")

    def test_closing_goes_back_to_slow_and_quiet(self):
        snaps, waits = self.run_watch([("open", None), ("closed", None), (None, None)])
        self.assertEqual(waits, [10, 3, 10, 10])
        self.assertEqual(len(snaps), 2)

    def test_start_in_open_mode(self):
        _, waits = self.run_watch([(None, None)], mode="open")
        self.assertEqual(waits[0], 3)

    def test_stop_ends(self):
        snaps, waits = self.run_watch([("stop", None)])
        self.assertEqual(len(snaps), 1)
        self.assertEqual(waits, [10])

    def test_stdin_commands(self):
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        wait = ports.stdin_commands(r)
        self.assertIsNone(wait(0.01))
        os.write(w, b"closed\nopen\n")
        self.assertEqual(wait(1), "open")
        os.close(w)
        self.assertEqual(wait(1), "stop")  # EOF

    def test_cli_watch_streams_lines_and_stops_on_eof(self):
        p = subprocess.Popen([sys.executable, str(SCRIPT), "watch", "--mode", "open"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            snap = json.loads(p.stdout.readline())
            self.assertEqual(snap["count"], len(snap["ports"]))
        finally:
            p.stdin.close()
        self.assertEqual(p.wait(timeout=20), 0)
        p.stdout.close()


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
