"""Regression tests for pane ownership resolution and notification suppression."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from tmux_agent_switcher.notifications.context import (
    resolve_agent_pane,
    get_pane_processes,
    resolve_client_context,
    resolve_notification_context,
)
from tmux_agent_switcher.notifications.gateway import handle_notify, handle_focus_out


class TestPaneContextResolution(unittest.TestCase):
    def setUp(self):
        self.current = {
            "pane": "%45",
            "location": "misc:1.3",
            "pid": "100",
            "pane_tty": "/dev/test-current",
            "cwd": "/project",
            "title": "⠋ [Tmux] | project",
        }
        self.other = {
            "pane": "%42",
            "location": "misc:3.4",
            "pid": "300",
            "pane_tty": "/dev/test-other",
            "cwd": "/other",
            "title": "[GPU] | other",
        }
        self.live = [self.current, self.other]
        self.procs = {
            "100": ("1", "test-current", "zsh"),
            "200": ("100", "test-current", "node /bin/codex resume"),
            "201": ("200", "test-current", "/bin/codex resume"),
            "300": ("1", "test-other", "zsh"),
            "400": ("300", "test-other", "/bin/codex resume"),
        }
        self.meta = {"name": "[Tmux]", "cwd": "/project", "source": "cli"}
        self.payload = {"session_id": "root-thread", "hook_event_name": "Stop"}

    def resolve(self, **kw):
        return resolve_agent_pane("Codex", self.payload, self.live, self.procs, self.meta, **kw)

    def test_closed_daemon_pane_is_replaced_by_live_session(self):
        res = self.resolve(inherited="%19")
        self.assertIsNotNone(res)
        self.assertEqual(res["pane"], "%45")
        res_empty = self.resolve(inherited="")
        self.assertIsNotNone(res_empty)
        self.assertEqual(res_empty["pane"], "%45")

    def test_reused_pane_and_bad_tag_on_another_agent_are_not_ownership(self):
        wrong = dict(self.other, pane="%19", pid="500", title="AGY", cwd="/project", codex_session_id="root-thread")
        self.live.append(wrong)
        self.procs["500"] = ("1", "test-other", "/bin/agy")
        res = self.resolve(inherited="%19")
        self.assertIsNotNone(res)
        self.assertEqual(res["pane"], "%45")

    def test_duplicate_title_is_ambiguous_even_with_old_resume_argv(self):
        self.live.append(dict(self.current, pane="%99", pid="900", pane_tty="/dev/test-third"))
        self.procs["900"] = ("1", "test-third", "/bin/codex resume")
        self.assertIsNone(self.resolve(inherited="%19"))
        self.procs["201"] = ("200", "test-current", "/bin/codex resume root-thread")
        self.assertIsNone(self.resolve())

    def test_changed_thread_and_non_cli_thread_do_not_use_old_context(self):
        self.current["title"] = "[Another conversation] | project"
        self.procs["201"] = ("200", "test-current", "/bin/codex resume root-thread")
        self.assertIsNone(self.resolve(inherited="%45"))
        self.meta["source"] = "exec"
        self.assertIsNone(self.resolve(inherited="%45"))

    def test_non_interactive_codex_and_wrong_tty_are_not_clients(self):
        self.procs["200"] = ("100", "test-current", "/bin/codex review")
        self.procs["201"] = ("200", "other-tty", "/bin/codex resume")
        self.assertIsNone(self.resolve(inherited="%45"))

    def test_explicit_target_must_exist(self):
        self.assertIsNone(self.resolve(explicit="%19"))
        res = self.resolve(explicit="misc:3.4")
        self.assertIsNotNone(res)
        self.assertEqual(res["pane"], "%42")

    def test_process_lookup_is_bounded_to_candidate_ttys(self):
        with patch("tmux_agent_switcher.notifications.context._tmux_cmd", return_value="100 1 test-current zsh\n") as run:
            result = get_pane_processes([self.current])
        self.assertEqual(result["100"], ("1", "test-current", "zsh"))
        self.assertEqual(run.call_args[0][0], ["ps", "-t", "test-current", "-o", "pid=,ppid=,tty=,command="])

    def test_only_focused_client_viewing_this_pane_suppresses(self):
        focused = [["%45", "/dev/client", "attached,focused,UTF-8", "misc"]]
        self.assertTrue(resolve_client_context(self.current, focused)["focused"])
        self.assertFalse(resolve_client_context(self.other, focused)["focused"])
        self.assertFalse(resolve_client_context(self.current, [["%45", "/dev/client", "attached,UTF-8", "misc"]])["focused"])
        self.assertEqual(resolve_client_context(None, focused)["location"], "")

    def test_background_remote_pane_uses_only_its_own_session_client(self):
        clients = [["%45", "/dev/client", "attached,focused,UTF-8", "misc"]]
        result = resolve_client_context(self.other, clients)
        self.assertFalse(result["focused"])
        self.assertEqual(result["client_tty"], "/dev/client")
        self.assertEqual(resolve_client_context(self.other, clients + [["%9", "/dev/second", "attached", "misc"]])["client_tty"], "")

    def test_main_tags_resolved_client_instead_of_daemon_pane(self):
        calls = []
        def fake_tmux_cmd(args, timeout=0.5):
            calls.append(args)
            if args[1] == "list-clients":
                return "%45\t/dev/client\tattached,focused,UTF-8\tmisc\n"
            return ""

        with patch("tmux_agent_switcher.notifications.context._tmux_cmd", side_effect=fake_tmux_cmd), \
             patch("tmux_agent_switcher.notifications.context.list_context_panes", return_value=self.live), \
             patch("tmux_agent_switcher.notifications.context.get_pane_processes", return_value=self.procs), \
             patch("tmux_agent_switcher.notifications.context.get_codex_thread_metadata", return_value=self.meta), \
             patch.dict(os.environ, {"TMUX_PANE": "%19"}):
            ctx = resolve_notification_context("Codex", self.payload, tag_session=True)

        self.assertEqual(ctx["pane"], "%45")
        self.assertIn(["tmux", "set-option", "-p", "-t", "%45", "@codex-session-id", "root-thread"], calls)
        self.assertFalse(any("%19" in call for call in calls))


class TestNotificationGatewayRegression(unittest.TestCase):
    def setUp(self):
        self.tmux_calls = []
        self.popen_calls = []

    def _mock_run(self, cmd, *args, **kwargs):
        self.tmux_calls.append(cmd)
        m = MagicMock()
        m.stdout = ""
        m.returncode = 0
        return m

    def _mock_popen(self, cmd, *args, **kwargs):
        self.popen_calls.append(cmd)
        m = MagicMock()
        return m

    def test_focused_session_emits_no_banner_or_attention_marker(self):
        mock_ctx = {"pane": "%45", "location": "misc:1.3", "focused": True, "pane_tty": "", "client_tty": ""}
        with patch("tmux_agent_switcher.notifications.gateway.resolve_notification_context", return_value=mock_ctx), \
             patch("tmux_agent_switcher.notifications.gateway._tmux_option", side_effect=lambda k, d: "all" if "notify" in k else "on"), \
             patch("subprocess.run", side_effect=self._mock_run), \
             patch("subprocess.Popen", side_effect=self._mock_popen):
            handle_notify("Codex", "Done", target_pane="%45", force=False)

        self.assertEqual(len(self.popen_calls), 0)
        self.assertFalse(any("pane-border-style" in cmd for cmd in self.tmux_calls))

    def test_background_session_notifies_and_marks_correct_pane(self):
        mock_ctx = {"pane": "%45", "location": "misc:1.3", "focused": False, "pane_tty": "", "client_tty": ""}
        with patch("tmux_agent_switcher.notifications.gateway.resolve_notification_context", return_value=mock_ctx), \
             patch("tmux_agent_switcher.notifications.gateway._tmux_option", side_effect=lambda k, d: "all" if "notify" in k else "on"), \
             patch("subprocess.run", side_effect=self._mock_run), \
             patch("subprocess.Popen", side_effect=self._mock_popen), \
             patch("platform.system", return_value="Darwin"):
            handle_notify("Codex", "Done", target_pane="%45", force=False)

        # Marked border on %45
        self.assertTrue(any("%45" in cmd and "fg=yellow" in cmd for cmd in self.tmux_calls))
        # Sent desktop banner mentioning Codex and misc:1.3
        self.assertEqual(len(self.popen_calls), 1)
        osascript_arg = self.popen_calls[0][2]
        self.assertIn("🔵 Codex", osascript_arg)
        self.assertIn("misc:1.3", osascript_arg)

    def test_unresolved_context_does_not_create_bogus_location_or_mark_wrong_pane(self):
        mock_ctx = {"pane": "", "location": "", "focused": False, "pane_tty": "", "client_tty": ""}
        with patch("tmux_agent_switcher.notifications.gateway.resolve_notification_context", return_value=mock_ctx), \
             patch("tmux_agent_switcher.notifications.gateway._tmux_option", side_effect=lambda k, d: "all" if "notify" in k else "on"), \
             patch("subprocess.run", side_effect=self._mock_run), \
             patch("subprocess.Popen", side_effect=self._mock_popen), \
             patch("platform.system", return_value="Darwin"):
            handle_notify("Codex", "Done", force=False)

        # No pane marked
        self.assertFalse(any("pane-border-style" in cmd for cmd in self.tmux_calls))
        # Sent desktop banner without ':.'
        self.assertEqual(len(self.popen_calls), 1)
        osascript_arg = self.popen_calls[0][2]
        self.assertNotIn(":.", osascript_arg)

    def test_headless_run_inside_a_pane_stays_silent(self):
        stop_payload = {
            "hook_event_name": "Stop",
            "session_id": "nested",
            "background_tasks": [],
            "last_assistant_message": "probe done"
        }
        with patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}), \
             patch("subprocess.run", side_effect=self._mock_run), \
             patch("subprocess.Popen", side_effect=self._mock_popen):
            handle_notify("Claude", "Done", stdin_data=json.dumps(stop_payload))

        self.assertEqual(len(self.tmux_calls), 0)
        self.assertEqual(len(self.popen_calls), 0)

    def test_only_busy_teammates_hold_stop_alert(self):
        # Teammates stay "running" in background_tasks after going idle. Only one put back to work holds the alert.
        idle = '<teammate-message teammate_id="{0}">\n{{"type":"idle_notification","from":"{0}"}}\n</teammate-message>'
        spawn = [{"type": "tool_use", "name": "Agent", "input": {"name": n, "description": "Plan " + n}} for n in "ab"]
        resend = [{"type": "tool_use", "name": "SendMessage", "input": {"to": "b", "message": "send the report"}}]
        stop = {"hook_event_name": "Stop", "background_tasks": [
            {"id": "t" + n, "type": "teammate", "status": "running", "description": "Plan " + n} for n in "ab"]}
        waits = []
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "cli", "AGENT_NOTIFY_SETTLE_S": "3", "AGENT_NOTIFY_HOLD_S": "1800"}), \
             patch("tmux_agent_switcher.notifications.gateway._dispatch_claude_settle_daemon",
                   side_effect=lambda tp, mark, wait_s, *a: waits.append(wait_s)):
            stop["transcript_path"] = os.path.join(tmp, "t.jsonl")
            for extra in ([], [("assistant", resend)]):
                turns = [("assistant", spawn), ("user", idle.format("a")), ("user", idle.format("b"))] + extra
                with open(stop["transcript_path"], "w") as f:
                    # Claude Code writes compact JSON lines
                    f.writelines(json.dumps({"type": r, "uuid": str(i), "message": {"content": c}}, separators=(",", ":")) + "\n" for i, (r, c) in enumerate(turns))
                handle_notify("Claude", stdin_data=json.dumps(stop))

        self.assertEqual(waits, [3.0, 1800.0])

    def test_turn_seen_ending_alerts_once_you_leave_without_replying(self):
        # A turn that ends while you watch is held in @pane-alert-pending, and leaving the pane raises it
        held = []
        def run(cmd, *args, **kwargs):
            m = self._mock_run(cmd)
            if cmd[:2] == ["tmux", "set"] and cmd[-2] == "@pane-alert-pending":
                held.append(cmd[-1])
            if cmd[:2] == ["tmux", "show"]:
                m.stdout = held[-1]
            return m
        ctx = lambda focused: patch("tmux_agent_switcher.notifications.gateway.resolve_notification_context",
                                    return_value={"pane": "%45", "location": "misc:1.3", "focused": focused, "pane_tty": "", "client_tty": ""})
        marked = lambda: any("%45" in cmd and "fg=yellow" in cmd for cmd in self.tmux_calls)
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "cli"}), \
             patch("tmux_agent_switcher.notifications.gateway._tmux_option", side_effect=lambda k, d: "all" if "notify" in k else "on"), \
             patch("subprocess.run", side_effect=run), \
             patch("subprocess.Popen", side_effect=self._mock_popen), \
             patch("platform.system", return_value="Darwin"):
            stop = {"hook_event_name": "Stop", "settled": True, "transcript_path": os.path.join(tmp, "t.jsonl"), "last_assistant_message": "Done"}
            with open(stop["transcript_path"], "w") as f:
                f.write('{"type":"user","uuid":"1"}\n{"type":"assistant","uuid":"2"}\n')
            with ctx(True):
                handle_notify("Claude", stdin_data=json.dumps(stop))
            self.assertFalse(marked())
            with ctx(False):
                handle_focus_out("%45")
            self.assertTrue(marked())

            # A reply before leaving adds a turn, so leaving raises nothing
            self.tmux_calls.clear()
            with ctx(True):
                handle_notify("Claude", stdin_data=json.dumps(stop))
            with open(stop["transcript_path"], "a") as f:
                f.write('{"type":"user","uuid":"3"}\n')
            with ctx(False):
                handle_focus_out("%45")
            self.assertFalse(marked())

    def test_explicit_force_still_notifies(self):
        mock_ctx = {"pane": "%45", "location": "misc:1.3", "focused": True, "pane_tty": "", "client_tty": ""}
        with patch("tmux_agent_switcher.notifications.gateway.resolve_notification_context", return_value=mock_ctx), \
             patch("tmux_agent_switcher.notifications.gateway._tmux_option", side_effect=lambda k, d: "all" if "notify" in k else "on"), \
             patch("subprocess.run", side_effect=self._mock_run), \
             patch("subprocess.Popen", side_effect=self._mock_popen), \
             patch("platform.system", return_value="Darwin"):
            handle_notify("Codex", "Done", target_pane="%45", force=True)

        self.assertTrue(any("%45" in cmd and "fg=yellow" in cmd for cmd in self.tmux_calls))
        self.assertEqual(len(self.popen_calls), 1)
        self.assertIn("🔵 Codex", self.popen_calls[0][2])


if __name__ == "__main__":
    unittest.main()
