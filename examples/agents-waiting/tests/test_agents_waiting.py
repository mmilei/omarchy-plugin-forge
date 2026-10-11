import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "agents-waiting.py"
spec = importlib.util.spec_from_file_location("agents_waiting", SCRIPT)
aw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aw)

NOW = 1_000_000.0


def fake_proc(root, pid, comm, ppid, start=1000, cmdline=None, environ=None):
    d = Path(root) / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    fields = ["S", str(ppid)] + ["0"] * 17 + [str(start)] + ["0"] * 10
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields))
    (d / "cmdline").write_bytes(("\0".join(cmdline or [comm]) + "\0").encode())
    if environ is not None:
        (d / "environ").write_bytes(("\0".join(f"{k}={v}" for k, v in environ.items()) + "\0").encode())


def claude_file(claude, pid, sid, status="idle", start=1000, kind="interactive", cwd="/home/u/proj",
                name="", updated_ms=(NOW - 60) * 1000):
    d = Path(claude) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": sid, "cwd": cwd, "kind": kind, "name": name, "status": status,
        "statusUpdatedAt": updated_ms, "procStart": str(start)}))


def transcript(claude, sid, lines):
    d = Path(claude) / "projects" / "-home-u-proj"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.jsonl").write_text("\n".join(lines) + "\n")


def pane(pane_id, status="unknown", cwd="/home/u/x", agent=None, session=None, title=None, seq=1):
    p = {"pane_id": pane_id, "agent_status": status, "cwd": cwd, "terminal_title": "t",
         "terminal_title_stripped": title or "", "state_change_seq": seq}
    if agent:
        p["agent"] = agent
    if session:
        p["agent_session"] = {"source": "hook", "agent": agent or "x", "kind": "id", "value": session}
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.proc, self.state, self.claude = t / "proc", t / "state", t / "claude"
        self.herdr_snap = None
        self.calls = []

    def ctx(self, **kw):
        kw.setdefault("herdr", lambda: self.herdr_snap)
        kw.setdefault("now", NOW)
        kw.setdefault("clients", lambda: [])
        kw.setdefault("claude_agents", lambda: [])
        kw.setdefault("runner", lambda cmd: self.calls.append(cmd) or True)
        kw.setdefault("popen", lambda cmd: self.calls.append(cmd))
        return aw.Ctx(state=self.state, claude_dir=self.claude, proc_root=self.proc, **kw)

    def snap(self, **kw):
        return aw.snapshot(self.ctx(**kw))

    def report(self, source, agent, state, seq, parent=None, env=None, **kw):
        return aw.do_report(self.state, source, agent, state, seq, parent_pid=parent,
                            proc_root=self.proc, env=env or {}, now=NOW - 30, **kw)


class ProcHelpers(Base):
    def test_stat_with_parens_in_comm(self):
        fake_proc(self.proc, 5, "we ) ird (x", 4, start=77)
        self.assertEqual(aw.read_stat(5, self.proc), ("we ) ird (x", 4, 77))

    def test_agent_pid_skips_hook_shell(self):
        fake_proc(self.proc, 100, "kitty", 1)
        fake_proc(self.proc, 120, "hermes", 100)
        fake_proc(self.proc, 130, "sh", 120, cmdline=["sh", "-c", "agents-waiting.py report --agent hermes"])
        self.assertEqual(aw.find_agent_pid(130, "hermes", self.proc), 120)

    def test_agent_pid_python_script(self):
        fake_proc(self.proc, 120, "python3", 1, cmdline=["/usr/bin/python3", "/opt/hermes", "chat"])
        fake_proc(self.proc, 130, "sh", 120)
        self.assertEqual(aw.find_agent_pid(130, "hermes", self.proc), 120)

    def test_agent_pid_none_when_absent(self):
        fake_proc(self.proc, 130, "sh", 1)
        self.assertIsNone(aw.find_agent_pid(130, "hermes", self.proc))

    def test_env_var(self):
        fake_proc(self.proc, 7, "x", 1, environ={"A": "1", "HERDR_PANE_ID": "w1:p2"})
        self.assertEqual(aw.read_env_var(7, "HERDR_PANE_ID", self.proc), "w1:p2")
        self.assertIsNone(aw.read_env_var(7, "NOPE", self.proc))
        self.assertIsNone(aw.read_env_var(99, "A", self.proc))


class ClaudeSource(Base):
    def test_states_title_and_liveness(self):
        fake_proc(self.proc, 10, "claude", 1, start=111)
        fake_proc(self.proc, 11, "claude", 1, start=222)
        fake_proc(self.proc, 12, "claude", 1, start=333)
        claude_file(self.claude, 10, "s-busy", "busy", 111, name="named")
        claude_file(self.claude, 11, "s-wait", "waiting", 222, cwd="/home/u/other")
        claude_file(self.claude, 12, "s-reused", "idle", 999)        # PID reuse: procStart differs
        claude_file(self.claude, 13, "s-dead", "idle", 1)             # no such process
        transcript(self.claude, "s-busy", ['{"type":"ai-title","aiTitle":"old"}', '{"type":"user"}',
                                          '{"type":"ai-title","aiTitle":"Fix the parser"}'])
        rows = {r["session_id"]: r for r in self.snap()["rows"]}
        self.assertEqual(set(rows), {"s-busy", "s-wait"})
        self.assertEqual(rows["s-busy"]["state"], "working")
        self.assertEqual(rows["s-busy"]["title"], "Fix the parser")
        self.assertEqual(rows["s-wait"]["state"], "blocked")
        self.assertEqual(rows["s-wait"]["title"], "other")     # no transcript, no name: cwd basename
        self.assertEqual(rows["s-busy"]["age"], 60)

    def test_title_falls_back_to_name(self):
        fake_proc(self.proc, 10, "claude", 1, start=111)
        claude_file(self.claude, 10, "s1", "idle", 111, name="my-name")
        self.assertEqual(self.snap()["rows"][0]["title"], "my-name")

    def test_title_reads_only_the_tail(self):
        transcript(self.claude, "big", ['{"type":"ai-title","aiTitle":"early"}'] + ['{"x":"%s"}' % ("a" * 100)] * 50)
        self.assertEqual(aw.ai_title(self.claude, "big", tail_bytes=1000), "")
        transcript(self.claude, "big2", ['{"x":"%s"}' % ("a" * 100)] * 50 + ['{"type":"ai-title","aiTitle":"late"}'])
        self.assertEqual(aw.ai_title(self.claude, "big2", tail_bytes=1000), "late")

    def test_idle_is_waiting_and_counts(self):
        fake_proc(self.proc, 10, "claude", 1, start=111)
        claude_file(self.claude, 10, "s1", "idle", 111)
        snap = self.snap()
        self.assertEqual((snap["rows"][0]["state"], snap["waiting"], snap["total"]), ("waiting", 1, 1))

    def test_unparseable_file_is_skipped_and_triggers_fallback_once(self):
        fake_proc(self.proc, 10, "claude", 1, start=111)
        fake_proc(self.proc, 20, "claude", 1, start=222)
        claude_file(self.claude, 10, "good", "idle", 111)
        (self.claude / "sessions" / "20.json").write_text('{"pid": 20, "weird": true}')
        (self.claude / "sessions" / "junk.json").write_text("not json")
        calls = []

        def agents():
            calls.append(1)
            return [{"pid": 20, "id": "x", "sessionId": "from-cli", "cwd": "/w/app", "kind": "bg",
                     "name": "bgjob", "status": "idle", "state": "idle"},
                    {"pid": 10, "sessionId": "good", "status": "idle", "cwd": "/"}]
        rows = {r["session_id"]: r for r in self.snap(claude_agents=agents)["rows"]}
        self.assertEqual(set(rows), {"good", "from-cli"})
        self.assertEqual(rows["from-cli"]["kind"], "bg")
        self.snap(claude_agents=agents)                      # within 30 s: cached
        self.assertEqual(len(calls), 1)
        later = aw.snapshot(self.ctx(claude_agents=agents, now=NOW + 31))
        self.assertEqual(len(calls), 2)
        self.assertEqual(later["total"], 2)

    def test_fallback_failure_is_harmless(self):
        (self.claude / "sessions").mkdir(parents=True)
        (self.claude / "sessions" / "1.json").write_text("{")

        def boom():
            raise OSError("no claude")
        self.assertEqual(self.snap(claude_agents=boom)["total"], 0)

    def test_no_claude_dir_and_no_cli_is_empty(self):
        self.assertEqual(self.snap(claude_agents=lambda: [])["rows"], [])


class HermesHook(Base):
    def hook(self, event, now, session="h1"):
        return aw.hermes_hook(self.state, {"hook_event_name": event, "session_id": session,
                                           "cwd": "/home/u/notes"}, now=now)

    def test_lifecycle(self):
        self.assertEqual(self.hook("pre_llm_call", NOW - 50), "working")
        self.assertEqual(self.hook("pre_approval_request", NOW - 40), "blocked")
        self.assertEqual(self.snap()["rows"][0]["state"], "blocked")
        self.assertEqual(self.hook("post_approval_response", NOW - 30), "working")
        self.assertEqual(self.hook("agent_loop_stopped", NOW - 20), "idle")
        row = self.snap()["rows"][0]
        self.assertEqual((row["state"], row["agent"], row["session_id"]), ("waiting", "hermes", "h1"))
        self.assertEqual(self.hook("on_session_end", NOW - 10), "release")
        self.assertEqual(self.snap()["rows"], [])

    def test_unknown_event_and_missing_session_ignored(self):
        self.assertIsNone(self.hook("post_tool_call", NOW))
        self.assertIsNone(self.hook("pre_llm_call", NOW, session=""))
        self.assertEqual(self.snap()["rows"], [])


class ReportedSource(Base):
    def test_seq_gate_and_release(self):
        self.assertTrue(self.report("s", "hermes", "working", 5))
        self.assertFalse(self.report("s", "hermes", "idle", 5))
        self.assertFalse(self.report("s", "hermes", "idle", 4))
        self.assertEqual(self.snap()["rows"][0]["state"], "working")
        self.assertTrue(self.report("s", "hermes", "idle", 6, message="your turn"))
        row = self.snap()["rows"][0]
        self.assertEqual((row["state"], row["title"], row["agent_label"]), ("waiting", "your turn", "Hermes"))
        self.assertFalse(aw.do_release(self.state, "s", 6))
        self.assertTrue(aw.do_release(self.state, "s", 7))
        self.assertEqual(self.snap()["rows"], [])
        self.assertFalse(self.report("s", "hermes", "idle", 7))     # stale report cannot resurrect it

    def test_invalid_state_ignored(self):
        self.assertFalse(self.report("s", "x", "permission", 1))

    def test_since_kept_while_state_unchanged(self):
        self.report("s", "x", "idle", 1)
        aw.do_report(self.state, "s", "x", "idle", 2, now=NOW, proc_root=self.proc, env={})
        self.assertEqual(self.snap()["rows"][0]["age"], 30)

    def test_walks_to_agent_pid_and_dies_with_it(self):
        fake_proc(self.proc, 100, "kitty", 1)
        fake_proc(self.proc, 120, "hermes", 100, start=4242)
        fake_proc(self.proc, 130, "sh", 120)
        self.report("s", "hermes", "idle", 1, parent=130)
        data = json.loads((self.state / "reported" / "s.json").read_text())
        self.assertEqual((data["pid"], data["pstart"]), (120, 4242))
        self.assertEqual(self.snap()["total"], 1)
        fake_proc(self.proc, 120, "other", 100, start=9999)          # PID reused
        self.assertEqual(self.snap()["total"], 0)
        self.assertFalse((self.state / "reported" / "s.json").exists())

    def test_stores_herdr_pane_from_env(self):
        self.report("s", "x", "idle", 1, env={"HERDR_PANE_ID": "w1:p1"})
        self.assertEqual(json.loads((self.state / "reported" / "s.json").read_text())["herdr_pane"], "w1:p1")

    def test_cli_report_never_fails(self):
        old = dict(os.environ)
        os.environ["AGENTS_WAITING_STATE"] = str(self.state)
        try:
            self.assertEqual(aw.main(["x", "report", "--bogus"]), 0)
            self.assertEqual(aw.main(["x", "report", "--source", "a", "--agent", "g", "--state", "idle", "--seq", "3"]), 0)
            self.assertEqual(aw.main(["x", "report", "--source", "a", "--agent", "g", "--state", "idle", "--seq", "notint"]), 0)
            self.assertEqual(aw.main(["x", "release", "--source", "a", "--seq", "4"]), 0)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(json.loads((self.state / "reported" / "a.json").read_text())["released"], True)


class Dedupe(Base):
    """The same agent seen by several sources is one row."""

    def test_claude_in_herdr_joined_by_session_id(self):
        fake_proc(self.proc, 10, "claude", 1, start=111)
        claude_file(self.claude, 10, "sess-A", "waiting", 111)
        transcript(self.claude, "sess-A", ['{"type":"ai-title","aiTitle":"Refactor billing"}'])
        self.herdr_snap = {"panes": [pane("w1:p1", "working", agent="claude", session="sess-A")], "agents": []}
        snap = self.snap()
        self.assertEqual(snap["total"], 1)
        row = snap["rows"][0]
        self.assertEqual((row["state"], row["title"], row["pane"]), ("blocked", "Refactor billing", "w1:p1"))
        self.assertEqual(row["via"], ["claude", "herdr"])

    def test_claude_in_herdr_joined_by_environ_when_no_session_id(self):
        fake_proc(self.proc, 10, "claude", 1, start=111, environ={"HERDR_PANE_ID": "w1:p1"})
        claude_file(self.claude, 10, "sess-A", "idle", 111)
        self.herdr_snap = {"panes": [pane("w1:p1", "working", agent="claude")], "agents": []}
        snap = self.snap()
        self.assertEqual(snap["total"], 1)
        self.assertEqual((snap["rows"][0]["state"], snap["rows"][0]["pane"]), ("waiting", "w1:p1"))

    def test_session_id_beats_environ(self):
        fake_proc(self.proc, 10, "claude", 1, start=111, environ={"HERDR_PANE_ID": "w1:p2"})
        claude_file(self.claude, 10, "sess-A", "idle", 111)
        self.herdr_snap = {"panes": [pane("w1:p1", "idle", agent="claude", session="sess-A"),
                                     pane("w1:p2", "idle", agent="codex")], "agents": []}
        rows = {r["key"]: r for r in self.snap()["rows"]}
        self.assertEqual(rows["claude:sess-A"]["pane"], "w1:p1")
        self.assertIn("herdr:w1:p2", rows)
        self.assertEqual(len(rows), 2)

    def test_unreadable_environ_without_session_is_tolerated(self):
        fake_proc(self.proc, 10, "claude", 1, start=111)               # no environ file
        claude_file(self.claude, 10, "sess-A", "idle", 111)
        self.herdr_snap = {"panes": [pane("w1:p1", "idle", agent="claude")], "agents": []}
        self.assertEqual(self.snap()["total"], 2)   # cannot prove they are the same: no guess

    def test_claude_outside_herdr(self):
        fake_proc(self.proc, 10, "claude", 1, start=111, environ={"PATH": "/bin"})
        claude_file(self.claude, 10, "sess-A", "idle", 111)
        self.herdr_snap = {"panes": [pane("w1:p1")], "agents": []}     # plain shell pane
        snap = self.snap()
        self.assertEqual(snap["total"], 1)
        self.assertIsNone(snap["rows"][0]["pane"])

    def test_hermes_in_herdr_with_report_by_stored_pane(self):
        fake_proc(self.proc, 120, "hermes", 1, start=55)
        self.report("hermes-1", "hermes", "idle", 3, parent=120, env={"HERDR_PANE_ID": "w1:p3"})
        self.herdr_snap = {"panes": [pane("w1:p3", "working", agent="hermes")], "agents": []}
        snap = self.snap()
        self.assertEqual(snap["total"], 1)
        row = snap["rows"][0]
        self.assertEqual((row["state"], row["pane"], row["source"]), ("waiting", "w1:p3", "reported"))

    def test_hermes_in_herdr_with_report_by_session_id(self):
        self.report("hermes-1", "hermes", "blocked", 3, session_id="h-77")
        self.herdr_snap = {"panes": [pane("w1:p3", "idle", agent="hermes", session="h-77")], "agents": []}
        snap = self.snap()
        self.assertEqual((snap["total"], snap["rows"][0]["state"]), (1, "blocked"))

    def test_herdr_only_codex_pane(self):
        self.herdr_snap = {"panes": [pane("w1:p4", "blocked", agent="codex", title="fix tests"),
                                     pane("w1:p5")], "agents": []}
        snap = self.snap()
        self.assertEqual(snap["total"], 1)
        row = snap["rows"][0]
        self.assertEqual((row["state"], row["agent_label"], row["pane"], row["key"]),
                         ("blocked", "Codex", "w1:p4", "herdr:w1:p4"))

    def test_all_four_cases_together(self):
        fake_proc(self.proc, 10, "claude", 1, start=111)
        fake_proc(self.proc, 11, "claude", 1, start=112, environ={})
        fake_proc(self.proc, 120, "hermes", 1, start=55)
        claude_file(self.claude, 10, "in-herdr", "idle", 111)
        claude_file(self.claude, 11, "outside", "busy", 112)
        self.report("h", "hermes", "idle", 1, parent=120, env={"HERDR_PANE_ID": "w1:p2"})
        self.herdr_snap = {"panes": [pane("w1:p1", "idle", agent="claude", session="in-herdr"),
                                     pane("w1:p2", "idle", agent="hermes"),
                                     pane("w1:p3", "done", agent="codex"), pane("w1:p9")], "agents": []}
        snap = self.snap()
        self.assertEqual(snap["total"], 4)
        self.assertEqual(sorted(r["agent"] for r in snap["rows"]), ["claude", "claude", "codex", "hermes"])

    def test_two_claude_sessions_in_one_pane_stay_two_rows(self):
        fake_proc(self.proc, 10, "claude", 1, start=1, environ={"HERDR_PANE_ID": "w1:p1"})
        fake_proc(self.proc, 11, "claude", 1, start=2, environ={"HERDR_PANE_ID": "w1:p1"})
        claude_file(self.claude, 10, "a", "idle", 1)
        claude_file(self.claude, 11, "b", "idle", 2)
        self.herdr_snap = {"panes": [pane("w1:p1", "idle", agent="claude")], "agents": []}
        self.assertEqual(self.snap()["total"], 2)

    def test_agents_array_merges_with_panes(self):
        self.herdr_snap = {"panes": [pane("w1:p1")],
                           "agents": [{"pane_id": "w1:p1", "agent_status": "idle", "agent": "pi", "title": "Hello"}]}
        row = self.snap()["rows"][0]
        self.assertEqual((row["title"], row["state"]), ("Hello", "waiting"))


class DoneAndSeen(Base):
    def test_done_stays_unread_until_opened(self):
        self.herdr_snap = {"panes": [pane("w1:p1", "done", agent="codex")], "agents": []}
        self.assertTrue(self.snap()["rows"][0]["unread"])
        self.assertTrue(self.snap()["rows"][0]["unread"])           # still, on later refreshes
        self.assertEqual(self.snap()["waiting"], 0)                  # done never counts
        self.assertTrue(aw.open_row(self.ctx(), "herdr:w1:p1"))
        self.assertFalse(self.snap()["rows"][0]["unread"])

    def test_new_done_after_work_is_unread_again(self):
        self.herdr_snap = {"panes": [pane("w1:p1", "done", agent="codex", seq=1)], "agents": []}
        aw.open_row(self.ctx(), "herdr:w1:p1")
        self.herdr_snap = {"panes": [pane("w1:p1", "working", agent="codex", seq=2)], "agents": []}
        self.snap()
        self.herdr_snap = {"panes": [pane("w1:p1", "done", agent="codex", seq=3)], "agents": []}
        self.assertTrue(aw.snapshot(self.ctx(now=NOW + 100))["rows"][0]["unread"])

    def test_reported_done_unread_then_ack(self):
        self.report("s", "x", "done", 1)
        self.assertTrue(self.snap()["rows"][0]["unread"])
        aw.open_row(self.ctx(), "reported:s")
        self.assertFalse(self.snap()["rows"][0]["unread"])

    def test_herdr_since_tracks_first_seen(self):
        self.herdr_snap = {"panes": [pane("w1:p1", "idle", agent="codex", seq=1)], "agents": []}
        self.snap()
        later = aw.snapshot(self.ctx(now=NOW + 90))
        self.assertEqual(later["rows"][0]["age"], 90)

    def test_sort_order(self):
        self.herdr_snap = {"panes": [pane("a", "working", agent="x"), pane("b", "done", agent="x"),
                                     pane("c", "idle", agent="x"), pane("d", "blocked", agent="x")], "agents": []}
        self.assertEqual([r["state"] for r in self.snap()["rows"]], ["blocked", "waiting", "done", "working"])

    def test_herdr_missing_is_silently_empty(self):
        self.herdr_snap = None
        self.assertEqual(self.snap()["total"], 0)


class OpenAction(Base):
    def test_joined_row_focuses_via_herdr(self):
        fake_proc(self.proc, 10, "claude", 1, start=111)
        claude_file(self.claude, 10, "sess-A", "idle", 111)
        self.herdr_snap = {"panes": [pane("w1:p1", "idle", agent="claude", session="sess-A")], "agents": []}
        self.assertTrue(aw.open_row(self.ctx(), "claude:sess-A"))
        self.assertEqual(self.calls, [["herdr", "agent", "focus", "w1:p1"]])

    def test_herdr_only_row(self):
        self.herdr_snap = {"panes": [pane("w1:p4", "idle", agent="codex")], "agents": []}
        self.assertTrue(aw.open_row(self.ctx(), "herdr:w1:p4"))
        self.assertEqual(self.calls, [["herdr", "agent", "focus", "w1:p4"]])

    def test_interactive_claude_focuses_terminal_window(self):
        fake_proc(self.proc, 100, "kitty", 1)
        fake_proc(self.proc, 110, "bash", 100)
        fake_proc(self.proc, 10, "claude", 110, start=111)
        claude_file(self.claude, 10, "s", "idle", 111)
        ctx = self.ctx(clients=lambda: [{"pid": 100, "address": "0xabc", "class": "kitty"}])
        self.assertTrue(aw.open_row(ctx, "claude:s"))
        self.assertEqual(self.calls, [["hyprctl", "dispatch", "focuswindow", "address:0xabc"]])

    def test_interactive_claude_without_window_fails(self):
        fake_proc(self.proc, 10, "claude", 1, start=111)
        claude_file(self.claude, 10, "s", "idle", 111)
        self.assertFalse(aw.open_row(self.ctx(), "claude:s"))

    def test_background_claude_opens_claude_agents(self):
        fake_proc(self.proc, 10, "claude", 1, start=111)
        claude_file(self.claude, 10, "s", "idle", 111, kind="bg")
        self.assertTrue(aw.open_row(self.ctx(), "claude:s"))
        self.assertEqual(self.calls, [["xdg-terminal-exec", "claude", "agents"]])

    def test_hermes_desktop_app_window(self):
        fake_proc(self.proc, 300, "hermes-desktop", 1, start=8)
        fake_proc(self.proc, 310, "hermes", 300, start=9)
        fake_proc(self.proc, 320, "sh", 310)
        self.report("hd", "hermes", "idle", 1, parent=320)
        ctx = self.ctx(clients=lambda: [{"pid": 300, "address": "0xh", "class": "Hermes"},
                                        {"pid": 5, "address": "0xk", "class": "kitty"}])
        self.assertTrue(aw.open_row(ctx, "reported:hd"))
        self.assertEqual(self.calls, [["hyprctl", "dispatch", "focuswindow", "address:0xh"]])

    def test_herdr_focus_failure_falls_back_to_window(self):
        fake_proc(self.proc, 100, "kitty", 1)
        fake_proc(self.proc, 10, "claude", 100, start=111)
        claude_file(self.claude, 10, "sess-A", "idle", 111)
        self.herdr_snap = {"panes": [pane("w1:p1", "idle", agent="claude", session="sess-A")], "agents": []}
        results = iter([False, True])
        ctx = self.ctx(runner=lambda cmd: self.calls.append(cmd) or next(results),
                       clients=lambda: [{"pid": 100, "address": "0xk", "class": "kitty"}])
        self.assertTrue(aw.open_row(ctx, "claude:sess-A"))
        self.assertEqual(self.calls[-1], ["hyprctl", "dispatch", "focuswindow", "address:0xk"])

    def test_unknown_key(self):
        self.assertFalse(aw.open_row(self.ctx(), "nope"))


if __name__ == "__main__":
    unittest.main()
