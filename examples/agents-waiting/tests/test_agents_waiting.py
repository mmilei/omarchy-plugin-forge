import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "agents-waiting.py"
spec = importlib.util.spec_from_file_location("agents_waiting", SCRIPT)
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)


def fake_proc(root, pid, comm, ppid, start=1000, cmdline=None):
    d = Path(root) / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    # 22 fields; comm may contain spaces/parens on purpose
    fields = ["S", str(ppid)] + ["0"] * 17 + [str(start)] + ["0"] * 10
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields))
    (d / "cmdline").write_bytes(("\0".join(cmdline or [comm]) + "\0").encode())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name) / "proc"
        self.state = Path(self.tmp.name) / "state"
        # kitty(100) -> bash(110) -> claude(120) -> sh(130, the hook's parent)
        fake_proc(self.proc, 100, "kitty", 1)
        fake_proc(self.proc, 110, "bash", 100)
        fake_proc(self.proc, 120, "claude", 110, start=5555)
        fake_proc(self.proc, 130, "sh", 120)

    def hook(self, event, session="s1", cwd="/home/u/proj", now=1000.0, parent=130, **extra):
        payload = {"hook_event_name": event, "session_id": session, "cwd": cwd, **extra}
        aw.handle_hook(payload, self.state, parent, self.proc, now=now)

    def snap(self, now=1000.0):
        return aw.snapshot(self.state, self.proc, now=now)


class ProcTests(Base):
    def test_stat_with_parens_in_comm(self):
        fake_proc(self.proc, 200, "weird) (name", 110, start=77)
        self.assertEqual(aw.read_stat(200, self.proc), ("weird) (name", 110, 77))

    def test_finds_claude_above_shell(self):
        self.assertEqual(aw.find_agent_pid(130, self.proc), 120)

    def test_claude_found_by_cmdline_when_comm_is_node(self):
        fake_proc(self.proc, 121, "node", 110, cmdline=["/opt/bin/claude", "--x"])
        fake_proc(self.proc, 131, "sh", 121)
        self.assertEqual(aw.find_agent_pid(131, self.proc), 121)

    def test_falls_back_to_parent_without_claude(self):
        self.assertEqual(aw.find_agent_pid(110, self.proc), 110)

    def test_pid_reuse_is_not_alive(self):
        self.assertTrue(aw.is_alive(120, 5555, self.proc))
        self.assertFalse(aw.is_alive(120, 9999, self.proc))
        self.assertFalse(aw.is_alive(999, 1, self.proc))


class HookTests(Base):
    def test_state_mapping(self):
        self.hook("UserPromptSubmit")
        self.assertEqual(self.snap()["sessions"][0]["state"], "working")
        self.hook("Notification", message="Claude needs your permission to use Bash")
        self.assertEqual(self.snap()["sessions"][0]["state"], "permission")
        self.hook("Stop")
        self.assertEqual(self.snap()["sessions"][0]["state"], "waiting")
        self.hook("Notification", notification_type="idle_prompt")
        self.assertEqual(self.snap()["sessions"][0]["state"], "waiting")
        self.hook("Notification", notification_type="permission_prompt")
        self.assertEqual(self.snap()["sessions"][0]["state"], "permission")

    def test_auth_success_is_ignored(self):
        self.hook("Stop")
        self.hook("Notification", notification_type="auth_success")
        self.assertEqual(self.snap()["sessions"][0]["state"], "waiting")

    def test_records_claude_pid_not_shell(self):
        self.hook("Stop")
        data = json.loads(next(self.state.glob("*.json")).read_text())
        self.assertEqual((data["pid"], data["starttime"]), (120, 5555))

    def test_session_end_removes_file(self):
        self.hook("Stop")
        self.hook("SessionEnd")
        self.assertEqual(list(self.state.glob("*.json")), [])
        self.hook("SessionEnd")  # idempotent

    def test_since_kept_while_state_unchanged(self):
        self.hook("Stop", now=1000)
        self.hook("Notification", notification_type="idle_prompt", now=1060)
        s = self.snap(now=1100)["sessions"][0]
        self.assertEqual((s["since"], s["age"]), (1000, 100))

    def test_since_resets_on_change(self):
        self.hook("Stop", now=1000)
        self.hook("UserPromptSubmit", now=1060)
        self.assertEqual(self.snap(now=1100)["sessions"][0]["age"], 40)

    def test_missing_session_id_and_hostile_id(self):
        aw.handle_hook({"hook_event_name": "Stop"}, self.state, 130, self.proc)
        self.assertFalse(self.state.exists())
        self.hook("Stop", session="../../evil")
        files = [p for p in Path(self.tmp.name).rglob("*.json")]
        self.assertTrue(all(self.state in p.parents for p in files))


class SnapshotTests(Base):
    def test_dead_sessions_pruned(self):
        self.hook("Stop", session="a")
        (self.proc / "120" / "stat").unlink()
        self.assertEqual(self.snap()["sessions"], [])
        self.assertEqual(list(self.state.glob("*.json")), [])

    def test_order_count_and_project(self):
        fake_proc(self.proc, 121, "claude", 110, start=6)
        fake_proc(self.proc, 122, "claude", 110, start=7)
        self.hook("UserPromptSubmit", session="w", cwd="/x/working", parent=120)
        self.hook("Stop", session="i", cwd="/x/idle/", parent=121, now=900)
        self.hook("Notification", session="p", cwd="/x/perm", parent=122, message="needs permission")
        snap = self.snap()
        self.assertEqual([s["project"] for s in snap["sessions"]], ["perm", "idle", "working"])
        self.assertEqual(snap["waiting"], 2)
        self.assertEqual(snap["sessions"][1]["age"], 100)

    def test_empty_and_corrupt(self):
        self.assertEqual(self.snap(), {"waiting": 0, "sessions": []})
        self.state.mkdir(parents=True)
        (self.state / "bad.json").write_text("{nope")
        self.assertEqual(self.snap()["sessions"], [])


class FocusTests(Base):
    def test_focuses_window_of_ancestor(self):
        self.hook("Stop")
        clients = [{"pid": 999, "address": "0xdead"}, {"pid": 100, "address": "0xkitty"}]
        calls = []
        ok = aw.focus("s1", self.state, self.proc, clients=lambda: clients, dispatch=calls.append)
        self.assertTrue(ok)
        self.assertEqual(calls, ["0xkitty"])

    def test_no_window_or_unknown_session(self):
        self.hook("Stop")
        self.assertFalse(aw.focus("s1", self.state, self.proc, clients=lambda: [], dispatch=print))
        self.assertFalse(aw.focus("nope", self.state, self.proc, clients=lambda: [], dispatch=print))


class CliTests(unittest.TestCase):
    def run_cli(self, args, stdin="", state=None):
        env = dict(os.environ, AGENTS_WAITING_STATE=state)
        return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin,
                              capture_output=True, text=True, env=env)

    def test_hook_swallows_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.run_cli(["hook"], "not json", d)
            self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_hook_then_snapshot_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            # parent of the script is this test process: no claude above, falls back to it
            payload = json.dumps({"hook_event_name": "Stop", "session_id": "e2e", "cwd": "/a/b"})
            self.run_cli(["hook"], payload, d)
            snap = json.loads(self.run_cli(["snapshot"], state=d).stdout)
            self.assertEqual(snap["sessions"][0]["project"], "b")

    def test_hooks_config_has_absolute_path_and_all_events(self):
        r = self.run_cli(["hooks-config"], state="/tmp")
        cfg = json.loads(r.stdout)
        self.assertEqual(set(cfg["hooks"]), set(aw.EVENTS))
        cmd = cfg["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertIn(str(SCRIPT), cmd)
        self.assertTrue(cmd.endswith(" hook"))

    def test_usage_exit_code(self):
        self.assertEqual(self.run_cli([], state="/tmp").returncode, 2)


if __name__ == "__main__":
    unittest.main()
