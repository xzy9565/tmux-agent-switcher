"""Unit tests for tmux-agent-switcher core, formatters, and adapters."""

import json
import os
import unittest
from tmux_agent_switcher.core.config import (
    C_RESET, C_PEACH, C_ICE_BLUE, C_MINT, str_display_width
)
from tmux_agent_switcher.core.types import PaneContext
from tmux_agent_switcher.core.tmux import ProcessTree
from tmux_agent_switcher.notifications.context import _tmux_cmd
from tmux_agent_switcher.formatters.markdown import style_inline, render_md, wrap_text
from tmux_agent_switcher.formatters.table import render_table
from tmux_agent_switcher.formatters.denoiser import denoise_terminal_text
from tmux_agent_switcher.formatters.bubbles import format_turns
from tmux_agent_switcher.adapters.base import extract_duck_turns
from tmux_agent_switcher.adapters.registry import resolve_adapter
from tmux_agent_switcher.adapters.claude import (
    ClaudeAdapter, resolve_session_chain, find_active_session_info, claude_state
)
from tmux_agent_switcher.adapters.antigravity import AntigravityAdapter
from tmux_agent_switcher.adapters.codex import CodexAdapter
from tmux_agent_switcher.adapters.command_code import CommandCodeAdapter
from tmux_agent_switcher.adapters.cursor import CursorAdapter
from tmux_agent_switcher.adapters.fallback import FallbackTuiAdapter


class TestConfigAndWidth(unittest.TestCase):
    def test_str_display_width(self):
        # ASCII characters have width 1
        self.assertEqual(str_display_width("hello"), 5)
        # CJK characters have width 2
        self.assertEqual(str_display_width("你好"), 4)
        # ANSI escape sequences must not contribute to display width
        styled = f"\033[1;31mhello\033[0m"
        self.assertEqual(str_display_width(styled), 5)


class TestProcessListing(unittest.TestCase):
    """macOS ps prints argv bytes raw: one non-UTF-8 argv used to fail decoding and blank every pane's detection."""

    def test_process_tree_survives_non_utf8_argv(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            ps = os.path.join(d, "ps")
            with open(ps, "w") as f:
                f.write("#!/bin/sh\nprintf '  PID  PPID COMMAND\\n    7     1 claude\\n    8     1 perl caf\\351\\377\\n'\n")
            os.chmod(ps, 0o755)
            with patch.dict(os.environ, {"PATH": d + os.pathsep + os.environ.get("PATH", "")}):
                tree = ProcessTree()
        self.assertEqual(tree.cmd_of.get("7"), "claude")
        self.assertIn("8", tree.cmd_of)

    def test_notification_ps_helper_survives_non_utf8(self):
        self.assertEqual(_tmux_cmd(["printf", "a\\351b"]), "a\ufffdb")


class TestListCommand(unittest.TestCase):
    def test_one_failing_adapter_does_not_empty_the_list(self):
        # e.g. OMP stat()s a session file deleted after indexing; every other pane must still be listed
        import io, time
        from contextlib import redirect_stdout
        from unittest.mock import patch, MagicMock
        from tmux_agent_switcher import cli
        panes = [PaneContext(pane_id="misc:1.1", pid="10", current_command="omp", title="OMP work"),
                 PaneContext(pane_id="misc:1.2", pid="11", current_command="zsh", title="zsh", cwd="/tmp")]
        broken = MagicMock(name="omp")
        broken.name = "omp"
        broken.get_status.side_effect = FileNotFoundError("session vanished")
        tree = MagicMock(tree={}, cmd_of={}, my_pid="1")
        tree.get_descendants.return_value = tree.get_agent_heads.return_value = ""
        out = io.StringIO()
        with patch.object(cli, "list_all_panes", return_value=panes), \
             patch.object(cli, "ProcessTree", return_value=tree), \
             patch.object(cli, "index_shared_state", return_value={"now": time.time(), "host": "h", "home": "/h"}), \
             patch.object(cli, "resolve_adapter", side_effect=[broken, FallbackTuiAdapter()]), \
             redirect_stdout(out):
            cli.cmd_list(None)
        self.assertIn("misc:1.2", out.getvalue())
        self.assertIn("⚠ omp FileNotFoundError", out.getvalue())

    def test_background_work_keeps_the_alert_and_a_running_turn_clears_it(self):
        # Main thread idle with a background shell: the alert raised by notify must survive opening the switcher
        import io, re, time
        from contextlib import redirect_stdout
        from unittest.mock import patch, MagicMock
        from tmux_agent_switcher import cli
        now = time.time()
        panes = [PaneContext(pane_id="misc:3.2", pid="10", current_command="claude.exe", title="bg shell", pane_needs_input="1"),
                 PaneContext(pane_id="misc:3.3", pid="11", current_command="claude.exe", title="new turn", pane_needs_input="1")]
        def adapter(running):
            a = MagicMock()
            a.name = "claude"
            a.get_status.return_value = ("🟠 Claude", "", 1, now, "t", running)
            return a
        tree = MagicMock(tree={}, cmd_of={}, my_pid="1")
        tree.get_descendants.return_value = tree.get_agent_heads.return_value = ""
        out, tmux = io.StringIO(), MagicMock()
        with patch.object(cli, "list_all_panes", return_value=panes), \
             patch.object(cli, "ProcessTree", return_value=tree), \
             patch.object(cli, "index_shared_state", return_value={"now": now, "host": "h", "home": "/h"}), \
             patch.object(cli, "resolve_adapter", side_effect=[adapter("🐚 shell"), adapter(True)]), \
             patch.object(cli.subprocess, "run", tmux), redirect_stdout(out):
            cli.cmd_list(None)
        rows = {r.split()[0]: r for r in re.sub(r"\x1b\[[0-9;]*m", "", out.getvalue()).splitlines()}
        self.assertIn("🔔 input", rows["misc:3.2"])
        cleared = {c.args[0][4] for c in tmux.call_args_list}
        self.assertEqual(cleared, {"misc:3.3"})


class TestPreviewCommand(unittest.TestCase):
    def test_agent_running_as_the_pane_process_itself(self):
        # `tmux new-window claude` makes claude the pane's own pid: list always counted it, preview did not
        import argparse, io, tempfile
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from tmux_agent_switcher import cli
        sid = "5e55e55e-0000-4000-8000-000000000001"
        with tempfile.TemporaryDirectory() as home:
            for d in ("projects/-tmp", "sessions"):
                os.makedirs(os.path.join(home, ".claude", d))
            with open(os.path.join(home, ".claude", "projects", "-tmp", f"{sid}.jsonl"), "w") as f:
                for role, content in (("user", "hello root"), ("assistant", [{"type": "text", "text": "Root reply."}])):
                    f.write(json.dumps({"type": role, "timestamp": "2026-11-23T10:00:00Z",
                                        "message": {"role": role, "content": content}}) + "\n")
            with open(os.path.join(home, ".claude", "sessions", "900001.json"), "w") as f:
                json.dump({"pid": 900001, "sessionId": sid, "status": "idle"}, f)
            bin_dir = os.path.join(home, "bin")
            os.makedirs(bin_dir)
            for name, body in (("ps", "printf '  PID  PPID COMMAND\\n900001     1 claude\\n'"), ("tmux", "exit 0")):
                with open(os.path.join(bin_dir, name), "w") as f:
                    f.write(f"#!/bin/sh\n{body}\n")
                os.chmod(os.path.join(bin_dir, name), 0o755)
            pane = PaneContext(pane_id="%9", pid="900001", current_command="claude.exe", title="Root Claude")
            out = io.StringIO()
            with patch.dict(os.environ, {"HOME": home, "PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}), \
                 patch.object(cli, "get_pane_context", return_value=pane), redirect_stdout(out):
                cli.cmd_preview(argparse.Namespace(pane_id="%9"))
        self.assertIn("Root reply.", out.getvalue())


class TestMarkdownFormatters(unittest.TestCase):
    def test_style_inline_code(self):
        styled = style_inline("Run `ls -la` now")
        self.assertIn("ls -la", styled)
        self.assertIn(C_PEACH, styled)

    def test_style_inline_bold_italic(self):
        bold = style_inline("**important**")
        self.assertIn("important", bold)

    def test_style_inline_links(self):
        link = style_inline("[GitHub](https://github.com)")
        self.assertIn("GitHub", link)
        self.assertIn("\033]8;;https://github.com\033\\", link)

    def test_render_table(self):
        table_raw = [
            "| Command | Description |",
            "|---------|-------------|",
            "| list    | Show agents |",
            "| preview | Show turns  |"
        ]
        rendered = render_table(table_raw, style_inline)
        self.assertTrue(len(rendered) > 4)
        self.assertTrue(rendered[0].startswith("\033[38;2;86;95;137m┌"))
        self.assertTrue(rendered[-1].endswith("┘\033[0m"))

    def test_render_md_blocks(self):
        md_text = "# Header 1\n\n- [x] Done task\n- [ ] Pending task\n\n```python\nprint(1)\n```"
        rendered = render_md(md_text, preview_width=80)
        self.assertIn("Header 1", rendered)
        self.assertIn("☑", rendered)
        self.assertIn("☐", rendered)
        self.assertIn("python", rendered)


class TestDenoiser(unittest.TestCase):
    def test_denoise_terminal_text(self):
        raw = (
            "Token Monitor | 100K / 200K\n"
            "user@hostname\n"
            "❯ real terminal output line\n"
            "Transfer-Encoding: chunked\n"
            "another content line\n"
        )
        denoised = denoise_terminal_text(raw)
        self.assertNotIn("Token Monitor", denoised)
        self.assertNotIn("user@hostname", denoised)
        self.assertNotIn("chunked", denoised)
        self.assertIn("real terminal output line", denoised)
        self.assertIn("another content line", denoised)


class TestDuckTurnsExtraction(unittest.TestCase):
    def test_extract_duck_turns(self):
        lines = [
            '{"type": "user", "message": {"role": "user", "content": "How do I fix this?"}}',
            '{"type": "assistant", "message": {"role": "assistant", "content": "Here is the fix."}}',
        ]
        turns = extract_duck_turns(lines, full=True)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["user"], "How do I fix this?")
        self.assertEqual(turns[0]["assistant"], "Here is the fix.")

    def test_claude_request_copies_and_task_notifications_are_not_dialogue(self):
        # Shapes taken from real Claude transcripts: request copies duplicate turns, notifications pose as YOU
        lines = [json.dumps(r) for r in (
            {"type": "user", "message": {"role": "user", "content": "fix the bug"}},
            {"type": "api-request-blob", "message": {"role": "user", "content": "fix the bug"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Fixed."}]}},
            {"type": "user", "origin": {"kind": "task-notification"},
             "message": {"role": "user", "content": "<task-notification><task-id>b1</task-id><status>completed</status></task-notification>"}},
        )]
        self.assertEqual(extract_duck_turns(lines), [{"user": "fix the bug", "assistant": "Fixed."}])

    def test_format_turns(self):
        turns = [{"user": "Hello agent", "assistant": "Hello user"}]
        bubble = format_turns(turns, live=True, full_history=False, preview_width=80)
        self.assertIn("Turn 1 of 1", bubble)
        self.assertIn("YOU", bubble)
        self.assertIn("Hello agent", bubble)
        self.assertIn("Hello user", bubble)


class TestAdapterResolution(unittest.TestCase):
    def test_resolve_claude(self):
        pane = PaneContext(
            pane_id="%1", pid="100", current_command="claude", title="Claude Code",
            bell_flag="0", pane_active="0", window_active="0", session_attached="1",
            cwd="/tmp", tty=""
        )
        adapter = resolve_adapter(pane, full_desc="node claude", heads="node claude")
        self.assertIsInstance(adapter, ClaudeAdapter)

    def test_resolve_antigravity(self):
        pane = PaneContext(
            pane_id="%2", pid="101", current_command="agy", title="AGY",
            bell_flag="0", pane_active="0", window_active="0", session_attached="1",
            cwd="/tmp", tty=""
        )
        adapter = resolve_adapter(pane, full_desc="agy --conversation test", heads="agy")
        self.assertIsInstance(adapter, AntigravityAdapter)

    def test_resolve_codex(self):
        pane = PaneContext(
            pane_id="%3", pid="102", current_command="node", title="Codex",
            bell_flag="0", pane_active="0", window_active="0", session_attached="1",
            cwd="/tmp", tty=""
        )
        adapter = resolve_adapter(pane, full_desc="codex exec", heads="codex")
        self.assertIsInstance(adapter, CodexAdapter)

    def test_resolve_cursor(self):
        pane = PaneContext(
            pane_id="%4", pid="103", current_command="cursor-agent", title="Cursor",
            bell_flag="0", pane_active="0", window_active="0", session_attached="1",
            cwd="/tmp", tty=""
        )
        adapter = resolve_adapter(pane, full_desc="cursor-agent", heads="cursor-agent")
        self.assertIsInstance(adapter, CursorAdapter)

    def test_resolve_fallback(self):
        pane = PaneContext(
            pane_id="%5", pid="104", current_command="zsh", title="Zsh",
            bell_flag="0", pane_active="0", window_active="0", session_attached="1",
            cwd="/tmp", tty=""
        )
        adapter = resolve_adapter(pane, full_desc="-zsh", heads="")
        self.assertIsInstance(adapter, FallbackTuiAdapter)


class TestAgentDetection(unittest.TestCase):
    """Detection end to end through a fake `ps` table: the agent is the program a pane runs, not text in arguments."""

    def _resolve_all(self, rows):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "table"), "w") as f:
                f.write("  PID  PPID COMMAND\n" + "".join(f"{pid} {ppid} {cmd}\n" for pid, ppid, cmd in rows))
            with open(os.path.join(d, "ps"), "w") as f:
                f.write(f"#!/bin/sh\ncat '{d}/table'\n")
            os.chmod(os.path.join(d, "ps"), 0o755)
            with patch.dict(os.environ, {"PATH": d + os.pathsep + os.environ.get("PATH", "")}):
                tree = ProcessTree()
        roots = [pid for pid, ppid, _ in rows if ppid == "1"]
        panes = {pid: PaneContext(pane_id="%" + pid, pid=pid, current_command="zsh", title="t") for pid in roots}
        return {pid: resolve_adapter(p, tree.get_descendants(pid), tree.get_agent_heads(pid)).name for pid, p in panes.items()}

    def test_agent_is_the_program_not_text_in_arguments(self):
        found = self._resolve_all([
            ("900000", "1", "-zsh"),  # Claude whose Bash tool command mentions other agents
            ("900001", "900000", "claude --resume"),
            ("900002", "900001", "/bin/zsh -c source ~/.zshrc && eval 'rg cursor-agent codex' < /dev/null"),
            ("900010", "1", "-zsh"),  # a shell editing Claude's config
            ("900011", "900010", "vim /Users/x/.claude/CLAUDE.md"),
            ("900020", "1", "-zsh"),  # AGY driving a codex child
            ("900021", "900020", "agy --conversation 0f0e0d0c-0b0a-0908-0706-050403020100"),
            ("900022", "900021", "codex exec fix-tests"),
            ("900030", "1", "-zsh"),  # an agent launched through node with flags
            ("900031", "900030", "node --no-warnings /opt/homebrew/lib/node_modules/opencode/bin/opencode"),
        ])
        self.assertEqual(found, {"900000": "claude", "900010": "fallback", "900020": "antigravity", "900030": "opencode"})

    def test_session_tags_count_only_while_something_besides_a_shell_runs(self):
        # Tags are never cleared: once the agent exits, its pane is a plain shell again
        stale = PaneContext(pane_id="%1", pid="1", current_command="zsh", title="t",
                            claude_sid="s1", agy_tag="a1", oc_tag="o1", cmd_code_sid="c1")
        self.assertEqual(resolve_adapter(stale, "", "").name, "fallback")
        # Command Code started as `cmd` has no recognisable program name; its tag still identifies it
        live = PaneContext(pane_id="%2", pid="2", current_command="node", title="t", cmd_code_sid="c1")
        self.assertEqual(resolve_adapter(live, "", "").name, "command-code")


class TestCursorAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = CursorAdapter()

    def test_cursor_running_status(self):
        pane = PaneContext(
            pane_id="%1", pid="101", current_command="cursor-agent",
            title="[Whisper] - ⏳ Working on fix",
            bell_flag="0", pane_active="1", window_active="1", session_attached="1",
            cwd="/tmp", tty=""
        )
        name, color, rank, mtime, title, running = self.adapter.get_status(pane, "", "", {})
        self.assertTrue(running)
        self.assertEqual(rank, 1)
        self.assertEqual(title, "[Whisper]")

    def test_cursor_waiting_title_status(self):
        pane = PaneContext(
            pane_id="%1", pid="101", current_command="cursor-agent",
            title="[Whisper] - ❓ Waiting for you",
            bell_flag="0", pane_active="1", window_active="1", session_attached="1",
            cwd="/tmp", tty=""
        )
        name, color, rank, mtime, title, running = self.adapter.get_status(pane, "", "", {})
        self.assertFalse(running)
        self.assertEqual(rank, 0)
        self.assertEqual(title, "[Whisper]")

    def test_project_age_uses_cursors_own_slug(self):
        # Cursor turns every run of non-alphanumerics into one dash: /tmp/my_proj.v2 -> tmp-my-proj-v2
        import tempfile
        from unittest.mock import patch, MagicMock
        with tempfile.TemporaryDirectory() as home:
            for slug, sid, mtime in (("tmp-my-proj-v2", "s1", 1000), ("elsewhere", "s2", 2000)):
                d = os.path.join(home, ".cursor", "projects", slug, "agent-transcripts", sid)
                os.makedirs(d)
                path = os.path.join(d, f"{sid}.jsonl")
                open(path, "w").close()
                os.utime(path, (mtime, mtime))
            pane = PaneContext(pane_id="%1", pid="101", current_command="cursor-agent",
                               title="[Proj] - ✅ Ready", cwd="/tmp/my_proj.v2")
            with patch.dict(os.environ, {"HOME": home}), patch("subprocess.run", return_value=MagicMock(stdout="")):
                mtime = self.adapter.get_status(pane, "", "", {})[3]
        self.assertEqual(mtime, 1000)

    def test_dialog_footers_and_conversation_words_are_not_running(self):
        # "Esc to cancel" footers dialogs that wait on you; "Thinking"/"Generating" in conversation text is no spinner
        import tempfile
        from unittest.mock import patch, MagicMock
        cases = [
            ("[Plan] - ❓ Waiting for confirmation", "Ready to proceed?\n  Enter to continue • Esc to cancel\n", 0, False),
            ("[Doc] - ✅ Ready", "Thinking about the design, I kept the old API.\nGenerating the report is next.\n  → Add a follow-up\n", 2, False),
        ]
        with tempfile.TemporaryDirectory() as home:
            for title, screen, rank, running in cases:
                pane = PaneContext(pane_id="%1", pid="101", current_command="cursor-agent", title=title, cwd="/tmp")
                with patch.dict(os.environ, {"HOME": home}), patch("subprocess.run", return_value=MagicMock(stdout=screen)):
                    got = self.adapter.get_status(pane, "", "", {})
                self.assertEqual((got[2], got[5]), (rank, running), title)

    def test_cursor_screen_question_detected(self):
        from unittest.mock import patch, MagicMock
        pane = PaneContext(
            pane_id="%1", pid="101", current_command="cursor-agent",
            title="[Whisper Keyboard] - ✅ Ready",
            bell_flag="0", pane_active="1", window_active="1", session_attached="1",
            cwd="/tmp", tty=""
        )
        fake_screen = (
            "Question 1 of 1\n"
            "1. 键盘扩展不能用麦克风，语音输入改成哪种方式？\n"
            "  › [ ] 主 App 负责录音\n"
            "    [ ] 只在 Whisper 主 App 里说话\n"
            "↑/↓ option · ←/→ question · Space select · Enter next/submit · Esc to skip\n"
        )
        mock_proc = MagicMock()
        mock_proc.stdout = fake_screen
        with patch("subprocess.run", return_value=mock_proc):
            name, color, rank, mtime, title, running = self.adapter.get_status(pane, "", "", {})
            self.assertFalse(running)
            self.assertEqual(rank, 0)  # Rank 0 corresponds to 🔔 input
            self.assertEqual(title, "[Whisper Keyboard]")

    def test_cursor_screen_approval_detected(self):
        from unittest.mock import patch, MagicMock
        pane = PaneContext(
            pane_id="%1", pid="101", current_command="cursor-agent",
            title="[Project] - ✅ Ready",
            bell_flag="0", pane_active="1", window_active="1", session_attached="1",
            cwd="/tmp", tty=""
        )
        fake_screen = (
            "Run shell command: npm test\n"
            "Do you want to run this command?\n"
            "› [ ] Allow once\n"
            "  [ ] Always allow\n"
            "Enter select · Esc skip\n"
        )
        mock_proc = MagicMock()
        mock_proc.stdout = fake_screen
        with patch("subprocess.run", return_value=mock_proc):
            name, color, rank, mtime, title, running = self.adapter.get_status(pane, "", "", {})
            self.assertFalse(running)
            self.assertEqual(rank, 0)

    def test_cursor_screen_idle_at_prompt(self):
        from unittest.mock import patch, MagicMock
        pane = PaneContext(
            pane_id="%1", pid="101", current_command="cursor-agent",
            title="[Whisper Keyboard] - ✅ Ready",
            bell_flag="0", pane_active="1", window_active="1", session_attached="1",
            cwd="/tmp", tty=""
        )
        fake_screen = (
            "Here is the summary of the fix.\n"
            "  → Add a follow-up\n"
            "  1 task\n"
        )
        mock_proc = MagicMock()
        mock_proc.stdout = fake_screen
        with patch("subprocess.run", return_value=mock_proc):
            name, color, rank, mtime, title, running = self.adapter.get_status(pane, "", "", {})
            self.assertFalse(running)
            self.assertEqual(rank, 2)  # Rank 2 corresponds to 💤 idle

    def test_cursor_screen_thinking_detected(self):
        from unittest.mock import patch, MagicMock
        pane = PaneContext(
            pane_id="%1", pid="101", current_command="cursor-agent",
            title="[Whisper Keyboard] - ✅ Ready",
            bell_flag="0", pane_active="1", window_active="1", session_attached="1",
            cwd="/tmp", tty=""
        )
        fake_screen = (
            " ⠰⠰ Thinking  4.88k tokens\n"
            " ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄\n"
            "  → Add a follow-up                                ctrl+c to stop\n"
            " ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
        )
        mock_proc = MagicMock()
        mock_proc.stdout = fake_screen
        with patch("subprocess.run", return_value=mock_proc):
            name, color, rank, mtime, title, running = self.adapter.get_status(pane, "", "", {})
            self.assertTrue(running)
            self.assertEqual(rank, 1)  # Rank 1 corresponds to ⚡ run
            self.assertEqual(title, "[Whisper Keyboard]")


class TestCodexAdapter(unittest.TestCase):
    def _codex_home(self, home, threads, items, state_v=5, history_v=1):
        # threads: (id, name, updated_at_ms, cwd, source, archived); items: (thread_id, "u"/"a", text)
        import sqlite3
        d = os.path.join(home, ".codex")
        os.makedirs(d, exist_ok=True)
        db = sqlite3.connect(os.path.join(d, f"state_{state_v}.sqlite"))
        db.execute("CREATE TABLE threads (id TEXT, name TEXT, updated_at_ms INTEGER, cwd TEXT, source TEXT, archived INTEGER)")
        db.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)", threads)
        db.commit()
        db.close()
        db = sqlite3.connect(os.path.join(d, f"thread_history_{history_v}.sqlite"))
        db.execute("CREATE TABLE thread_items (thread_id TEXT, item_type TEXT, item_json TEXT, created_at_ms INTEGER)")
        for i, (tid, kind, text) in enumerate(items):
            item = ("userMessage", {"content": [{"type": "text", "text": text}]}) if kind == "u" else ("agentMessage", {"text": text})
            db.execute("INSERT INTO thread_items VALUES (?, ?, ?, ?)", (tid, item[0], json.dumps(item[1]), (i + 1) * 1000))
        db.commit()
        db.close()

    def _preview(self, home, title, cwd="/tmp"):
        from unittest.mock import patch
        pane = PaneContext(pane_id="%1", pid="1", current_command="codex", title=title, cwd=cwd)
        with patch.dict(os.environ, {"HOME": home, "CODEX_HOME": ""}):
            result = CodexAdapter().get_preview(pane, "", [], False, 0)
        return result.content if result else ""

    def test_preview_pairs_turns_in_chronological_order(self):
        # thread_items is queried newest-first; each prompt must pair with its own reply and the newest reply must show
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            self._codex_home(home, [("t1", "Fix login", 4000, "/tmp", "cli", 0)],
                             [("t1", "u", "first question"), ("t1", "a", "first answer"),
                              ("t1", "u", "second question"), ("t1", "a", "second answer")])
            content = self._preview(home, "Fix login")
        order = [content.index(s) for s in ("first question", "first answer", "second question", "second answer")]
        self.assertEqual(order, sorted(order))

    def test_newest_database_versions_are_used(self):
        # A Codex upgrade bumps state_N / thread_history_N, and an old empty state_1 may linger beside them
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            self._codex_home(home, [("t1", "Fix login", 4000, "/tmp", "cli", 0)],
                             [("t1", "u", "ship it"), ("t1", "a", "Shipped.")], state_v=6, history_v=2)
            open(os.path.join(home, ".codex", "state_1.sqlite"), "w").close()
            content = self._preview(home, "Fix login")
        self.assertIn("Shipped.", content)

    def test_thread_is_matched_by_exact_name_then_directory(self):
        # Bracketed names, a name that prefixes another, a reused name, a spinner-only title, subagent and exec threads
        import tempfile
        from unittest.mock import patch
        from tmux_agent_switcher import cli
        threads = [("t1", "[Business] CheapSW", 1000, "/a", "vscode", 0),
                   ("t2", "[Business] CheapSW 2", 2000, "/a", "cli", 0),
                   ("t3", "[Business] CheapSW", 500, "/a", "cli", 0),
                   ("t4", "Audit", 9000, "/a", '{"subagent":{"other":"guardian"}}', 0),
                   ("t5", "Bench", 8000, "/a", "exec", 0)]
        items = [("t1", "u", "cheap one"), ("t1", "a", "Reply for t1."), ("t2", "u", "cheap two"), ("t2", "a", "Reply for t2.")]
        with tempfile.TemporaryDirectory() as home:
            self._codex_home(home, threads, items)
            with patch.dict(os.environ, {"HOME": home, "CODEX_HOME": ""}):
                state = cli.index_shared_state()

            def age(title):
                pane = PaneContext(pane_id="%1", pid="1", current_command="codex", title=title, cwd="/a")
                return CodexAdapter().get_status(pane, "", "codex", state)[3]
            self.assertEqual(age("[Business] CheapSW | tracker"), 1.0)  # exact name, newest of its two threads
            self.assertEqual(age("⠋"), 2.0)  # no name yet: newest interactive thread in the directory
            self.assertIn("Reply for t1.", self._preview(home, "[Business] CheapSW | tracker", cwd="/a"))
            self.assertIn("Reply for t2.", self._preview(home, "⠋", cwd="/a"))


class TestOpenCodeAdapter(unittest.TestCase):
    """session_v2 / session_message schema: the legacy session/message/part tables stopped updating."""

    def _db(self, home, sessions, messages):
        import sqlite3
        d = os.path.join(home, ".local", "share", "opencode")
        os.makedirs(d)
        db = sqlite3.connect(os.path.join(d, "opencode.db"))
        db.execute("CREATE TABLE session_v2 (id TEXT, title TEXT, parent_id TEXT, time_updated INTEGER, time_busy INTEGER, time_idle INTEGER)")
        db.execute("CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INTEGER, data TEXT)")
        db.executemany("INSERT INTO session_v2 VALUES (?, ?, NULL, ?, ?, ?)", sessions)
        db.executemany("INSERT INTO session_message VALUES (?, ?, ?, ?, ?)",
                       [(f"m{i}", sid, t, i, json.dumps(data)) for i, (sid, t, data) in enumerate(messages)])
        db.commit()
        db.close()

    def test_preview_reads_v2_messages_and_only_reply_text(self):
        import tempfile
        from unittest.mock import patch
        from tmux_agent_switcher.adapters.opencode import OpenCodeAdapter
        msgs = [
            ("ses_1", "user", {"text": "first question"}),
            ("ses_1", "assistant", {"content": [{"type": "reasoning", "text": "private thoughts"},
                                                 {"type": "tool", "name": "shell", "state": {"output": "tool noise"}},
                                                 {"type": "text", "text": "first answer"}]}),
            ("ses_1", "synthetic", {"text": "<shell state=completed>"}),
            ("ses_1", "user", {"text": "second question"}),
            ("ses_1", "assistant", {"content": [{"type": "text", "text": "second answer"}]}),
        ]
        with tempfile.TemporaryDirectory() as home:
            self._db(home, [("ses_1", "[Coder]", 3000, 1000, 2000)], msgs)
            pane = PaneContext(pane_id="%1", pid="1", current_command="opencode.exe", title="OC | [Coder]", oc_tag="ses_1")
            with patch.dict(os.environ, {"HOME": home}):
                content = OpenCodeAdapter().get_preview(pane, "", [], False, 0).content
        order = [content.index(s) for s in ("first question", "first answer", "second question", "second answer")]
        self.assertEqual(order, sorted(order))
        for noise in ("private thoughts", "tool noise", "shell state"):
            self.assertNotIn(noise, content)

    def test_status_comes_from_the_session_busy_and_idle_times(self):
        # Busy after its last idle: a turn is in flight; the age is the last prompt or reply, not time_updated
        import tempfile
        from unittest.mock import patch
        from tmux_agent_switcher import cli
        from tmux_agent_switcher.adapters.opencode import OpenCodeAdapter
        with tempfile.TemporaryDirectory() as home:
            self._db(home, [("ses_1", "[Coder]", 9000, 5000, 4000)], [])
            with patch.dict(os.environ, {"HOME": home}):
                state = cli.index_shared_state()
        pane = PaneContext(pane_id="%1", pid="1", current_command="opencode.exe", title="OC | [Coder]", oc_tag="ses_1")
        _, _, rank, mtime, _, running = OpenCodeAdapter().get_status(pane, "", "opencode", state)
        self.assertEqual((rank, mtime, running), (1, 5.0, True))


class TestGrokAdapter(unittest.TestCase):
    def test_each_pane_gets_its_own_session(self):
        # Status took the newest session of any directory for every pane; preview fell back to another pane's session
        import tempfile, urllib.parse
        from unittest.mock import patch
        from tmux_agent_switcher.adapters.grok import GrokAdapter
        with tempfile.TemporaryDirectory() as home:
            def session(cwd, sid, reply, mtime):
                d = os.path.join(home, ".grok", "sessions", urllib.parse.quote(cwd, safe=""), sid)
                os.makedirs(d)
                path = os.path.join(d, "chat_history.jsonl")
                with open(path, "w") as f:  # real records carry no timestamps, so age is the file's mtime
                    f.write(json.dumps({"type": "user", "content": [{"type": "text", "text": "hi"}]}) + "\n")
                    f.write(json.dumps({"type": "assistant", "content": reply}) + "\n")
                os.utime(path, (mtime, mtime))
            session("/a", "s1", "Reply in a.", 1000)
            session("/b", "s2", "Reply in b.", 2000)
            with open(os.path.join(home, ".grok", "active_sessions.json"), "w") as f:
                json.dump([{"pid": 999, "session_id": "s2", "cwd": "/b"}], f)  # another pane's grok
            pane = PaneContext(pane_id="%1", pid="10", current_command="grok", title="grok", cwd="/a")
            with patch.dict(os.environ, {"HOME": home}):
                age = GrokAdapter().get_status(pane, "", "grok", {"tree": {}, "my_pid": "1"})[3]
                content = GrokAdapter().get_preview(pane, "", ["10"], False, 0).content
        self.assertEqual(age, 1000)
        self.assertIn("Reply in a.", content)


class TestCommandCodeAdapter(unittest.TestCase):
    def test_session_file_is_chosen_over_its_checkpoints_sibling(self):
        # Every session sits next to <sid>.checkpoints.jsonl, which holds no dialogue
        import tempfile
        from unittest.mock import patch
        sid = "1cf138dc-171b-45b3-a3c7-1b3c5ee60136"
        with tempfile.TemporaryDirectory() as home:
            proj = os.path.join(home, ".commandcode", "projects", "private-tmp")
            os.makedirs(proj)
            with open(os.path.join(proj, f"{sid}.checkpoints.jsonl"), "w") as f:
                f.write(json.dumps({"id": "c1", "turnNumber": 1, "prompt": "look at tmux", "messageCount": 0, "files": []}) + "\n")
            with open(os.path.join(proj, f"{sid}.jsonl"), "w") as f:
                for role, text in (("user", "look at tmux"), ("assistant", "Tmux looks fine.")):
                    f.write(json.dumps({"type": "message", "timestamp": "2026-10-29T00:03:02.787Z",
                                        "message": {"role": role, "content": [{"type": "text", "text": text}]}}) + "\n")
            pane = PaneContext(pane_id="%1", pid="1", current_command="command-code", title="tmux", cmd_code_sid=sid)
            with patch.dict(os.environ, {"HOME": home}):
                result = CommandCodeAdapter().get_preview(pane, "", [], False, 0)
        self.assertIsNotNone(result)
        self.assertIn("Tmux looks fine.", result.content)


class TestClaudeAdapter(unittest.TestCase):
    def setUp(self):
        self.adapter = ClaudeAdapter()

    def test_claude_resolve_continuation_chain(self):
        import tempfile, json
        with tempfile.NamedTemporaryFile("w+", delete=False) as f1, \
             tempfile.NamedTemporaryFile("w+", delete=False) as f2:
            f1.write(json.dumps({"type": "continued-in", "continuedInSessionId": "sess-2"}) + "\n")
            f1.flush()
            f2.write(json.dumps({"type": "user", "message": {"content": "hi"}, "timestamp": "2026-10-11T12:00:00Z"}) + "\n")
            f2.flush()
            
            claude_idx = {"sess-1": f1.name, "sess-2": f2.name}
            resolved = resolve_session_chain("sess-1", claude_idx)
            self.assertEqual(resolved, "sess-2")

    def test_claude_running_status_from_cc_status(self):
        pane = PaneContext(
            pane_id="%1", pid="101", current_command="claude",
            title="[Whisper Keyboard]",
            bell_flag="0", pane_active="1", window_active="1", session_attached="1",
            cwd="/tmp", tty="", claude_sid="sess-1"
        )
        shared_state = {
            "claude_idx": {},
            "cc_status": {"101": "busy"},
            "cc_sessions": {},
            "tree": {},
            "my_pid": "999"
        }
        name, color, rank, mtime, title, running = self.adapter.get_status(pane, "", "", shared_state)
        self.assertTrue(running)
        self.assertEqual(rank, 1)

    def test_claude_waiting_status(self):
        pane = PaneContext(
            pane_id="%1", pid="101", current_command="claude",
            title="[Whisper Keyboard]",
            bell_flag="0", pane_active="1", window_active="1", session_attached="1",
            cwd="/tmp", tty="", claude_sid="sess-1"
        )
        shared_state = {
            "claude_idx": {},
            "cc_status": {"101": "waiting"},
            "cc_sessions": {},
            "tree": {},
            "my_pid": "999"
        }
        name, color, rank, mtime, title, running = self.adapter.get_status(pane, "", "", shared_state)
        self.assertFalse(running)
        self.assertEqual(rank, 0)

    def _transcript(self, *records):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("".join(json.dumps(r) + "\n" for r in records))
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_transcripts_of_every_claude_profile_are_indexed(self):
        # A profile run through CLAUDE_CONFIG_DIR=~/.claude-personal keeps both sessions/ and projects/ there
        import tempfile
        from unittest.mock import patch
        from tmux_agent_switcher import cli
        sid = "5e55e55e-0000-4000-8000-000000000002"
        with tempfile.TemporaryDirectory() as home:
            profile = os.path.join(home, ".claude-personal")
            os.makedirs(os.path.join(profile, "projects", "-tmp"))
            os.makedirs(os.path.join(profile, "sessions"))
            with open(os.path.join(profile, "projects", "-tmp", f"{sid}.jsonl"), "w") as f:
                f.write(json.dumps({"type": "user", "timestamp": "2026-11-23T10:00:00Z",
                                    "message": {"role": "user", "content": "hi"}}) + "\n")
            with open(os.path.join(profile, "sessions", "101.json"), "w") as f:
                json.dump({"pid": 101, "sessionId": sid, "status": "idle"}, f)
            with patch.dict(os.environ, {"HOME": home}):
                state = cli.index_shared_state()
                state.update({"tree": {}, "my_pid": "1"})
                pane = PaneContext(pane_id="%1", pid="101", current_command="claude", title="t")
                mtime = self.adapter.get_status(pane, "", "claude", state)[3]
        self.assertIn(sid, state["claude_idx"])
        self.assertGreater(mtime, 0)  # unindexed, the age was blank

    def test_own_process_session_beats_a_stale_tag(self):
        # The tag is rewritten only when a turn ends; after /clear or /resume the process's session file is current
        pane = PaneContext(pane_id="%1", pid="101", current_command="claude", title="t", claude_sid="old-sess")
        idx = {"old-sess": "/nonexistent/old.jsonl", "new-sess": "/nonexistent/new.jsonl"}
        sessions = {"101": {"sessionId": "new-sess", "status": "idle"}}
        self.assertEqual(find_active_session_info(pane, idx, sessions, ["101"]), ("new-sess", "idle"))

    def test_compaction_summary_does_not_open_a_turn(self):
        # "Compacted while idle" leaves a user-type summary as the last dialogue record
        path = self._transcript(
            {"type": "assistant", "timestamp": "2026-11-22T05:58:42Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}], "stop_reason": "end_turn"}},
            {"type": "user", "isCompactSummary": True, "timestamp": "2026-11-22T06:53:38Z",
             "message": {"role": "user", "content": "This session is being continued from a previous conversation..."}},
        )
        self.assertFalse(claude_state(path)[1])

    def test_cli_idle_status_beats_transcript_inference(self):
        # A crashed or resumed turn can end on a tool_result; the live CLI reporting idle must win
        path = self._transcript(
            {"type": "user", "timestamp": "2026-11-20T10:00:00Z",
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        )
        self.assertTrue(claude_state(path)[1])
        pane = PaneContext(pane_id="%1", pid="101", current_command="claude", title="[Sub Price]")
        state = {"claude_idx": {"sess-1": path}, "cc_status": {"101": "idle"},
                 "cc_sessions": {"101": {"sessionId": "sess-1", "status": "idle"}}, "tree": {}, "my_pid": "999"}
        _, _, rank, _, _, running = self.adapter.get_status(pane, "", "", state)
        self.assertEqual((rank, running), (2, False))


class TestNotificationSubsystem(unittest.TestCase):
    def test_context_resolution_empty_payload(self):
        from tmux_agent_switcher.notifications.context import resolve_notification_context
        ctx = resolve_notification_context("AI Agent", {})
        self.assertIn("pane", ctx)
        self.assertIn("focused", ctx)
        self.assertIn("location", ctx)

    def test_handle_clear_no_throw(self):
        from tmux_agent_switcher.notifications.gateway import handle_clear
        # Ensure handle_clear runs without throwing even if tmux pane is non-existent
        handle_clear("%999999")

    def test_handle_notify_no_throw(self):
        from unittest.mock import patch
        from tmux_agent_switcher.notifications.gateway import handle_notify
        with patch("subprocess.Popen"):
            handle_notify("Claude", "Done", target_pane="%999999", force=False)

    def test_handle_hook_title_deprecated_noop(self):
        from unittest.mock import patch
        from tmux_agent_switcher.notifications.gateway import handle_hook_title

        with patch("subprocess.run") as mock_run:
            handle_hook_title("%42")
            # Deprecated: Pane title sniffing removed in favor of native agent hooks (0 subprocess calls)
            self.assertEqual(mock_run.call_count, 0)

    def test_osc9_raw_for_a_terminal_wrapped_for_a_nested_tmux(self):
        from tmux_agent_switcher.notifications.gateway import _osc9
        # Control bytes in agent text cannot end the alert or smuggle in another sequence (here OSC 52).
        self.assertEqual(_osc9("hi\x1b]52;c;eA==\x07", "xterm-ghostty"), "\x1b]9;hi ]52;c;eA== \x1b\\")
        # Passthrough needs every inner ESC doubled, including the one in the closing ST.
        self.assertEqual(_osc9("hi", "tmux-256color"), "\x1bPtmux;\x1b\x1b]9;hi\x1b\x1b\\\x1b\\")

    def test_cmd_hook_title_always_exits_zero(self):
        import argparse
        from unittest.mock import patch
        from tmux_agent_switcher.cli import cmd_hook_title

        with patch("tmux_agent_switcher.cli.handle_hook_title", side_effect=RuntimeError("Simulated error")):
            with self.assertRaises(SystemExit) as cm:
                cmd_hook_title(argparse.Namespace(pane_id="%42"))
            self.assertEqual(cm.exception.code, 0)

    def test_read_stdin_payload_nonblocking(self):
        import io, os, select, subprocess, sys
        from tmux_agent_switcher.cli import read_stdin_payload

        # TTY check returns immediately
        with unittest.mock.patch("sys.stdin.isatty", return_value=True):
            self.assertEqual(read_stdin_payload(), "")

        # Test pipe reading
        r, w = os.pipe()
        os.write(w, b'{"test": "payload"}')
        # Do NOT close w (unclosed pipe)
        saved_stdin = sys.stdin
        pipe_io = io.TextIOWrapper(os.fdopen(r, "rb"))
        try:
            sys.stdin = pipe_io
            val = read_stdin_payload(timeout=0.05)
            self.assertEqual(val, '{"test": "payload"}')
        finally:
            sys.stdin = saved_stdin
            pipe_io.close()
            os.close(w)

    def test_claude_last_turn_uuid(self):
        import tempfile
        from tmux_agent_switcher.notifications.gateway import _get_claude_last_turn_uuid

        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write(json.dumps({"type": "user", "uuid": "u-123"}) + "\n")
            f.write(json.dumps({"type": "assistant", "uuid": "a-456"}) + "\n")
            f.write(json.dumps({"type": "cost-state"}) + "\n")
            f_path = f.name
        try:
            self.assertEqual(_get_claude_last_turn_uuid(f_path), "a-456")
        finally:
            os.unlink(f_path)

    def test_claude_stop_hold_when_background_tasks_active(self):
        import tempfile
        from unittest.mock import patch
        from tmux_agent_switcher.notifications.gateway import handle_notify

        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write(json.dumps({"type": "user", "uuid": "u-1"}) + "\n")
            f.write(json.dumps({"type": "assistant", "uuid": "a-2"}) + "\n")
            f_path = f.name
        try:
            payload = {
                "hook_event_name": "Stop",
                "background_tasks": ["shell_task_1"],
                "last_assistant_message": "I will run the build in the background and notify you when finished.",
                "transcript_path": f_path,
            }
            with patch("tmux_agent_switcher.notifications.gateway._dispatch_claude_settle_daemon") as mock_dispatch:
                handle_notify("Claude", "Done", target_pane="%1", stdin_data=json.dumps(payload))
                mock_dispatch.assert_called_once()
                args, _ = mock_dispatch.call_args
                self.assertEqual(args[0], "a-2")  # mark
                self.assertEqual(args[1], 1800.0) # wait_s (held for 30m)
        finally:
            os.unlink(f_path)

    def test_claude_stop_settle_when_question_asked(self):
        import tempfile
        from unittest.mock import patch
        from tmux_agent_switcher.notifications.gateway import handle_notify

        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write(json.dumps({"type": "user", "uuid": "u-1"}) + "\n")
            f.write(json.dumps({"type": "assistant", "uuid": "a-2"}) + "\n")
            f_path = f.name
        try:
            payload = {
                "hook_event_name": "Stop",
                "background_tasks": ["shell_task_1"],
                "last_assistant_message": "Build started in background.\n\nWould you like me to run tests as well?",
                "transcript_path": f_path,
            }
            with patch("tmux_agent_switcher.notifications.gateway._dispatch_claude_settle_daemon") as mock_dispatch:
                handle_notify("Claude", "Done", target_pane="%1", stdin_data=json.dumps(payload))
                mock_dispatch.assert_called_once()
                args, _ = mock_dispatch.call_args
                self.assertEqual(args[0], "a-2") # mark
                self.assertEqual(args[1], 3.0)   # wait_s (settle 3s only, not held for 30m!)
        finally:
            os.unlink(f_path)

    def test_settle_sleeper_reruns_notify_with_its_payload_on_stdin(self):
        # Runs the generated sleeper for real: argv keeps all three positionals, the payload carries settle_mark
        import sys, tempfile, time
        from unittest.mock import patch
        from tmux_agent_switcher.notifications import gateway
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "tmux-agent-switcher")
            with open(fake, "w") as f:
                f.write(f"#!/bin/sh\nprintf '%s|' \"$@\" > '{d}/args.tmp' && mv '{d}/args.tmp' '{d}/args'\ncat > '{d}/stdin'\n")
            os.chmod(fake, 0o755)
            with patch.object(sys, "argv", [fake]):
                gateway._dispatch_claude_settle_daemon("u-9", 0.01, {"hook_event_name": "Stop"}, "", "Done", "%3")
            deadline = time.time() + 10
            while not os.path.exists(os.path.join(d, "args")) and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.2)
            with open(os.path.join(d, "args")) as f:
                args = f.read()
            with open(os.path.join(d, "stdin")) as f:
                sent = json.loads(f.read())
        self.assertEqual(args, "notify||Done|%3|")
        self.assertEqual((sent["settled"], sent["settle_mark"]), (True, "u-9"))

    def test_settled_alert_is_dropped_once_a_newer_turn_started(self):
        import tempfile
        from unittest.mock import patch
        from tmux_agent_switcher.notifications import gateway
        with tempfile.TemporaryDirectory() as d:
            tp = os.path.join(d, "t.jsonl")
            with open(tp, "w") as f:
                f.write('{"type":"user","uuid":"u-1"}\n{"type":"assistant","uuid":"u-2"}\n')
            with patch.object(gateway, "resolve_notification_context", return_value={"pane": "", "location": ""}) as resolve, \
                 patch.object(gateway, "_tmux_option", return_value="none"):
                for mark in ("u-2", "u-1"):  # unchanged transcript notifies; a newer turn since the mark does not
                    gateway.handle_notify(stdin_data=json.dumps(
                        {"hook_event_name": "Stop", "settled": True, "settle_mark": mark, "transcript_path": tp}))
        self.assertEqual(resolve.call_count, 1)

    def test_notify_tolerates_unexpected_payload_shapes(self):
        # A non-object payload or a bare-string permission_request used to raise and exit 1 inside the hook
        from unittest.mock import patch, MagicMock
        from tmux_agent_switcher.notifications import gateway
        opts = {"@agent-switcher-notify": "desktop", "@agent-switcher-mark-input": "off"}
        popen = MagicMock()
        with patch.object(gateway, "resolve_notification_context", return_value={"pane": "", "location": "", "local_client": True}), \
             patch.object(gateway, "_tmux_option", side_effect=lambda o, d: opts.get(o, d)), \
             patch.object(gateway.platform, "system", return_value="Darwin"), \
             patch.object(gateway.subprocess, "Popen", popen):
            gateway.handle_notify(stdin_data="[]")
            gateway.handle_notify(stdin_data='{"permission_request": "Bash"}')
        self.assertIn("Approval needed: Bash", popen.call_args[0][0][2])

    def test_context_tolerates_non_object_payload(self):
        import argparse, io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from tmux_agent_switcher import cli
        with patch.object(cli, "read_stdin_payload", return_value="[]"), \
             patch.object(cli, "resolve_notification_context", return_value={}) as resolve, \
             redirect_stdout(io.StringIO()):
            cli.cmd_context(argparse.Namespace(agent="Codex", pane_id="", tag_session=True))
        self.assertEqual(resolve.call_args.kwargs["payload"], {})


if __name__ == "__main__":
    unittest.main()
