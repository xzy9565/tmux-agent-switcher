"""Unit tests for tmux-agent-switcher core, formatters, and adapters."""

import unittest
from tmux_agent_switcher.core.config import (
    C_RESET, C_PEACH, C_ICE_BLUE, C_MINT, str_display_width
)
from tmux_agent_switcher.core.types import PaneContext
from tmux_agent_switcher.formatters.markdown import style_inline, render_md, wrap_text
from tmux_agent_switcher.formatters.table import render_table
from tmux_agent_switcher.formatters.denoiser import denoise_terminal_text
from tmux_agent_switcher.formatters.bubbles import format_turns
from tmux_agent_switcher.adapters.base import extract_duck_turns
from tmux_agent_switcher.adapters.registry import resolve_adapter
from tmux_agent_switcher.adapters.claude import (
    ClaudeAdapter, resolve_session_chain, find_active_session_info
)
from tmux_agent_switcher.adapters.antigravity import AntigravityAdapter
from tmux_agent_switcher.adapters.codex import CodexAdapter
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

    def test_claude_subagent_status(self):
        pane = PaneContext(
            pane_id="%1", pid="101", current_command="claude",
            title="[Whisper Keyboard]",
            bell_flag="0", pane_active="1", window_active="1", session_attached="1",
            cwd="/tmp", tty="", claude_sid="sess-1"
        )
        shared_state = {
            "claude_idx": {},
            "cc_status": {"101": "agents"},
            "cc_sessions": {},
            "tree": {},
            "my_pid": "999"
        }
        name, color, rank, mtime, title, running = self.adapter.get_status(pane, "", "", shared_state)
        self.assertEqual(running, "🤖 subagent")
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


if __name__ == "__main__":
    unittest.main()
