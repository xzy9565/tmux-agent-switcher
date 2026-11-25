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

    def test_width_is_what_tmux_draws(self):
        # Measured in a tmux 3.8 pane with cursor position reports; fzf 0.75 wraps rows at the same widths
        cases = {
            "\u2713": 1, "\u2605": 1, "\u26a0": 1, "\u2611": 1, "\u2726": 1, "\u279c": 1, "\U0001f5c2": 1,  # ✓ ★ ⚠ ☑ ✦ ➜ 🗂 take one cell
            "\u2705": 2, "\u26a1": 2, "\U0001f4cd": 2, "\U0001fae0": 2,  # ✅ ⚡ 📍 🫠 take two, also past Python 3.9's Unicode 13
            "\u26a0\ufe0f": 2, "\U0001f5c2\ufe0f": 2, "1\ufe0f\u20e3": 2,  # VS16 widens the character before it: ⚠️ 🗂️ 1️⃣
            "\U0001f468\u200d\U0001f4bb": 2, "\U0001f44d\U0001f3fd": 2, "\U0001f3f3\ufe0f\u200d\U0001f308": 2,  # 👨‍💻 👍🏽 🏳️‍🌈
            "\U0001f1fa\U0001f1f8": 2, "e\u0301": 1, "a\u200bb": 2, "a\u200db": 2, "a\u00adb": 3,  # 🇺🇸, marks, ZWSP, ZWJ, soft hyphen
        }
        self.assertEqual({s: str_display_width(s) for s in cases}, cases)


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

    def test_each_row_starts_with_its_exact_target_and_a_tab(self):
        # fzf's {1} is that hidden first field: a session name can hold spaces, which split rows on whitespace
        import io, time
        from contextlib import redirect_stdout
        from unittest.mock import patch, MagicMock
        from tmux_agent_switcher import cli
        tree = MagicMock(tree={}, cmd_of={}, my_pid="1")
        tree.get_descendants.return_value = tree.get_agent_heads.return_value = ""
        out = io.StringIO()
        with patch.object(cli, "list_all_panes", return_value=[PaneContext(pane_id="my proj:1.2", pid="11", current_command="zsh", title="zsh")]), \
             patch.object(cli, "ProcessTree", return_value=tree), \
             patch.object(cli, "index_shared_state", return_value={"now": time.time(), "host": "h", "home": "/h"}), \
             patch.object(cli, "resolve_adapter", return_value=FallbackTuiAdapter()), redirect_stdout(out):
            cli.cmd_list(None)
        self.assertTrue(out.getvalue().startswith("my proj:1.2\tmy proj:1.2 "), out.getvalue()[:40])


class TestRunCommand(unittest.TestCase):
    def test_enter_switches_to_a_session_whose_name_has_spaces(self):
        # the Enter pipeline cut rows at the first space, so "my proj:1.2" became the target "my"
        import argparse, sys, tempfile
        from unittest.mock import patch
        from tmux_agent_switcher import cli
        with tempfile.TemporaryDirectory() as d:
            calls = os.path.join(d, "calls")
            for name, body in (("switcher", "printf 'my proj:1.2\\tmy proj:1.2  ▶ ⚡ Claude\\n'"),  # its `list`
                               ("fzf", "head -1"),  # Enter on the first row
                               ("tmux", f"printf '%s|' \"$@\" >> '{calls}'")):
                with open(os.path.join(d, name), "w") as f:
                    f.write(f"#!/bin/sh\n{body}\n")
                os.chmod(os.path.join(d, name), 0o755)
            with patch.object(sys, "argv", [os.path.join(d, "switcher")]), \
                 patch.dict(os.environ, {"PATH": d + os.pathsep + os.environ.get("PATH", "")}):
                cli.cmd_run(argparse.Namespace(preview_width="58%"))
            with open(calls) as f:
                self.assertEqual(f.read(), "switch-client|-t|my proj:1.2|")

    def test_ctrl_x_kills_only_after_a_yes_naming_what_runs_there_now(self):
        # one keystroke killed the pane, a running agent included
        import argparse
        from unittest.mock import patch
        from tmux_agent_switcher import cli
        with patch.object(cli.subprocess, "run") as run:
            cli.cmd_run(argparse.Namespace(preview_width="58%"))
        self.assertIn(" kill {1})+reload(", run.call_args[0][0][2])
        pane = PaneContext(pane_id="misc:1.2", pid="9", current_command="claude.exe", title="Fix build")
        for answer, killed in (("y", True), ("", False), ("n", False), (EOFError(), False)):
            with patch.object(cli, "get_pane_context", return_value=pane), \
                 patch("builtins.input", side_effect=[answer]) as ask, patch.object(cli.subprocess, "run") as run:
                cli.cmd_kill(argparse.Namespace(pane_id="misc:1.2"))
            self.assertIn("misc:1.2 (claude.exe · Fix build)", ask.call_args[0][0])
            self.assertEqual(run.call_args[0][0] if run.called else None,
                             ["tmux", "kill-pane", "-t", "misc:1.2"] if killed else None, repr(answer))


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

    def test_the_label_names_the_adapter_whose_preview_is_shown(self):
        # the first adapter to claim the pane labelled it, even when it found no session and another produced the preview
        import argparse, io
        from contextlib import redirect_stdout
        from unittest.mock import patch, MagicMock
        from tmux_agent_switcher import cli
        from tmux_agent_switcher.core.types import PreviewResult
        empty, producer = MagicMock(display_name="🟠 Claude"), MagicMock(display_name="⚡ Codex")
        empty.name, producer.name = "claude", "codex"
        empty.get_preview.return_value = None
        producer.get_preview.return_value = PreviewResult(source_label="codex", content="codex turns")
        tree = MagicMock()
        tree.get_descendant_pids.return_value = []
        out = io.StringIO()
        with patch.object(cli, "ADAPTERS", [empty, producer]), patch.object(cli, "ProcessTree", return_value=tree), \
             patch.object(cli, "get_pane_context", return_value=PaneContext(pane_id="%9", pid="1", current_command="node", title="Fix build")), \
             redirect_stdout(out):
            cli.cmd_preview(argparse.Namespace(pane_id="%9"))
        self.assertIn("⚡ Codex · Fix build", out.getvalue())

    def test_a_closed_pane_previews_nothing_to_copy(self):
        # a stand-in context for a pane closed since the list was drawn made its capture error the content: Ctrl-Y copied it
        import argparse, io
        from contextlib import redirect_stdout, redirect_stderr
        from unittest.mock import patch
        from tmux_agent_switcher import cli
        out, err = io.StringIO(), io.StringIO()
        gone = ("\t".join(["", "", "", "x"] + [""] * 8) + "\n").encode()  # tmux 3.8, exit 0: every field empty, WxH just "x"
        with patch("subprocess.check_output", return_value=gone), redirect_stdout(out), redirect_stderr(err):
            cli.cmd_preview(argparse.Namespace(pane_id="misc:1.9"))
        self.assertEqual(out.getvalue(), "")
        self.assertIn("misc:1.9: no such pane", err.getvalue())


class TestCopyCommand(unittest.TestCase):
    """Ctrl-Y: what reaches the tmux buffer and the system clipboard."""

    def _copy(self, preview_out, broken=()):
        """Run cmd_copy with every subprocess faked; tools named in broken fail (load-buffer) or are missing."""
        import argparse, subprocess
        from unittest.mock import patch
        from tmux_agent_switcher import cli
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            if cmd[0] in broken:
                raise FileNotFoundError(cmd[0])
            return subprocess.CompletedProcess(cmd, 1 if cmd[1:2] == ["load-buffer"] and "load-buffer" in broken else 0,
                                               stdout=preview_out if "preview" in cmd else "")
        with patch.object(cli.subprocess, "run", side_effect=run), \
             patch.object(cli.subprocess, "check_output", return_value=b""):
            cli.cmd_copy(argparse.Namespace(pane_id="%9"))
        return calls

    def test_the_toast_says_where_the_text_went_or_that_it_did_not(self):
        def toast(preview_out, broken=()):
            return [cmd[2] for cmd, _ in self._copy(preview_out, broken) if cmd[:2] == ["tmux", "display-message"]]
        text = "━━━ %9 · Claude ━━━\n\nhello"
        self.assertEqual(toast(text), ["📋 Copied %9 to system clipboard"])
        self.assertEqual(toast(text, {"pbcopy", "wl-copy", "xclip"}), ["📋 Copied %9 to tmux buffer · no pbcopy, wl-copy or xclip"])
        self.assertEqual(toast(text, {"load-buffer", "pbcopy", "wl-copy", "xclip"}),
                         ["⚠ Copy failed: tmux load-buffer and every clipboard tool failed for %9"])
        self.assertEqual(toast(""), ["📋 Nothing to copy from %9"])

    def test_nothing_a_terminal_acts_on_reaches_the_clipboard(self):
        # \e[201~ ends a bracketed paste early: whatever follows would run as typed input
        hostile = ("━━━ %9 · Claude ━━━\n\nrun this\x1b[201~echo pwned\x1b[200~ \x1b]52;c;ZXZpbA==\x07\x1b]0;title\x07"
                   "\x1b[4:3mcurly\x1b[0m \x1b]8;id=1;https://x.y\x1b\\link\x1b]8;;\x1b\\ \x9b31m\r\n\ttab\x1bPq#0\x1b\\end")
        clip = [kw["input"].decode() for cmd, kw in self._copy(hostile) if cmd[:2] == ["tmux", "load-buffer"] or cmd == ["pbcopy"]]
        self.assertEqual(clip, ["run thisecho pwned curly link 31m\n\ttabend"] * 2)

    def test_the_dialogue_is_copied_as_written_not_as_rendered(self):
        # the rendered bubbles dropped every link URL and indented code by two spaces
        from unittest.mock import patch
        reply = "See [the docs](https://example.com/docs):\n\n```python\ndef f():\n    return 1\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |"
        calls = self._copy("━━━ %9 · Claude ━━━\n\nanything")
        self.assertEqual(next(kw["env"] for cmd, kw in calls if "preview" in cmd).get("AGENT_SWITCHER_COPY"), "1")
        with patch.dict(os.environ, {"AGENT_SWITCHER_COPY": "1"}):
            out = format_turns([{"user": "How?", "assistant": reply}], live=False, full_history=False)
        self.assertEqual(out, f"👤 YOU\nHow?\n\n🤖 AGENT\n{reply}")


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

    def test_emphasis_never_rewrites_a_link_url(self):
        styled = style_inline("see [init](/repo/pkg/__init__.py) and [x](https://e.com/a_b_c*d*)")
        self.assertIn("\033]8;;/repo/pkg/__init__.py\033\\", styled)
        self.assertIn("\033]8;;https://e.com/a_b_c*d*\033\\", styled)
        self.assertEqual(str_display_width(styled), len("see init and x"))

    def test_a_span_inside_bold_leaves_no_markers_and_the_bold_on_after_it(self):
        import re
        def bold_text(styled):  # the characters drawn bold, replaying the SGR codes in order
            on, out = False, []
            for code, ch in re.findall(r"\033\[([0-9;]*)m|([^\033])", styled):
                if ch:
                    out.append(ch if on else " ")
                for p in code.split(";") if code else []:
                    on = True if p == "1" else False if p in ("0", "22") else on
            return " ".join("".join(out).split())
        s = style_inline("**bold *italic* bold** and **Fix `foo()`:** next")
        self.assertEqual(re.sub(r"\033\[[0-9;]*m", "", s), "bold italic bold and Fix foo(): next")
        self.assertEqual(bold_text(s), "bold italic bold Fix foo():")
        # A header's own bold also survives the spans in it
        self.assertEqual(bold_text(render_md("## Run `make` **now** please")), "■ Run make now please")

    def test_inline_html_renders_as_text_except_in_code(self):
        import re
        styled = style_inline("cell<br>two <kbd>Ctrl</kbd>+<b>C</b>, not `<b>`")
        self.assertEqual(re.sub(r"\033\[[0-9;]*m", "", styled), "cell two Ctrl+C, not <b>")

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

    def test_wrapping_counts_display_cells_and_keeps_spans_whole(self):
        import re
        plain = lambda s: re.sub(r"\033\]8;;[^\033]*\033\\|\033\[[0-9;]*m", "", s)
        # Chinese has no spaces to break at: every line still fits, and none starts with a comma or full stop
        zh = render_md("中文句子里没有空格，按字数折行的话，一行会宽到预览的两倍。" * 4, preview_width=40).split("\n")
        self.assertEqual([l for l in zh if str_display_width(l) > 39 or plain(l)[:1] in "，。"], [])
        # A link or italic the break falls inside stays styled on both lines instead of showing its markdown
        md = "See the [documentation page for every option](https://example.com/docs) and *an italic phrase that wraps here* too."
        lines = render_md(md, preview_width=40).split("\n")
        self.assertEqual([l for l in lines if str_display_width(l) > 39 or re.search(r"[\[\]()*]", plain(l))], [])
        self.assertEqual(sum("\033]8;;https://example.com/docs\033\\" in l for l in lines), 2)
        italic = False  # the style the third line starts in, replaying the codes before its text
        for code in re.findall(r"\033\[([0-9;]*)m", re.match(r"(?:\033\[[0-9;]*m)*", lines[2]).group(0)):
            for p in code.split(";"):
                italic = True if p == "3" else False if p in ("0", "23") else italic
        self.assertTrue(italic, lines)

    def test_a_table_wider_than_the_preview_fits_it_and_wraps_its_cells(self):
        import re
        plain = lambda s: re.sub(r"\033\]8;;[^\033]*\033\\|\033\[[0-9;]*m", "", s)
        rows = [["Item", "Notes", "状态"],
                ["width", "every symbol was measured in a tmux pane, then the widest columns shrink to fit", "已完成，真实表格全部对齐"],
                ["wrap", "cells wrap inside their own column", "进行中"]]
        md = "\n".join("| " + " | ".join(r) + " |" for r in rows[:1] + [["---"] * 3] + rows[1:])
        lines = [plain(l) for l in render_md(md, preview_width=60).split("\n")]
        borders = lambda l: tuple(str_display_width(l[:i]) for i, ch in enumerate(l) if ch in "┌┬┐├┼┤└┴┘│")
        # fzf wraps nothing: every row is a cell inside the preview, and every border lines up
        self.assertEqual({str_display_width(l) for l in lines}, {59})
        self.assertEqual({borders(l) for l in lines}, {borders(lines[0])})
        # Each column reads the same as its cells, only broken over more lines
        body = lines[next(i for i, l in enumerate(lines) if l.startswith("├")) + 1:-1]
        for i in range(3):
            self.assertEqual("".join(l.split("│")[i + 1].replace(" ", "") for l in body),
                             "".join(r[i].replace(" ", "") for r in rows[1:]))


class TestDenoiser(unittest.TestCase):
    def test_agent_chrome_goes_and_real_output_stays_as_is(self):
        raw = (
            "Token Monitor | 100K / 200K\n"
            "  ⏵⏵ auto mode on (shift+tab to cycle)\n"
            "Transfer-Encoding: chunked\n"
            "❯ real terminal output line\n"
            # each of these was dropped or rewritten as noise
            "git@github.com:xzy9565/tmux-agent-switcher.git\n"
            "Resolution: 1920x1080, mask 0x10\n"
            "12:30 PM\n"
            ">= 5 items\n"
        )
        self.assertEqual(denoise_terminal_text(raw), "❯ real terminal output line\ngit@github.com:xzy9565/tmux-agent-switcher.git\n"
                                                     "Resolution: 1920x1080, mask 0x10\n12:30 PM\n>= 5 items")

    def test_a_shell_pane_preview_is_its_own_text_not_rendered_markdown(self):
        # a diff's - lines became bullets, # comments headings, and ` and ** vanished
        from unittest.mock import patch
        screen = "$ git diff\n- old **bold** line\n+ new `code` line\n# a shell comment"
        pane = PaneContext(pane_id="%9", pid="1", current_command="zsh", title="zsh")
        with patch("subprocess.check_output", return_value=screen.encode()):
            self.assertEqual(FallbackTuiAdapter().get_preview(pane, "", [], False, 80).content, screen)


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

    def test_only_wrapper_tag_lines_are_dropped_never_tags_in_text(self):
        # every <word> used to be deleted: placeholders, generics, vim keys, HTML in code
        user = "<USER_REQUEST>\nrun git checkout <branch> on Vec<String>\n</USER_REQUEST>"
        reply = "Map <Leader>y to copy:\n```html\n<div>\n</div>\n```"
        lines = [json.dumps({"type": role, "message": {"role": role, "content": text}}) for role, text in (
            ("user", user), ("assistant", reply),
            ("user", "<bash-input>ls</bash-input>"), ("assistant", "Listed."))]  # ! shell mode, which the agent answers
        self.assertEqual(extract_duck_turns(lines), [{"user": "run git checkout <branch> on Vec<String>", "assistant": reply},
                                                     {"user": "ls", "assistant": "Listed."}])

    def test_the_notes_block_goes_but_a_reply_mentioning_it_stays_whole(self):
        # a mention of the marker used to delete the rest of the reply
        header = "📒 Possibly-relevant notes (pointer-push — Read the path if useful):"
        prompt = f"Fix the build.\n\n{header}\n- Build  ·  Areas/Build.md  (rel 100%)\n- CI  ·  CI.md  (rel 67%)"
        reply = f"Your hook appends this line:\n{header}\nand the rest of a reply used to vanish.\n\nFixed."
        lines = [json.dumps({"type": role, "message": {"role": role, "content": text}})
                 for role, text in (("user", prompt), ("assistant", reply))]
        self.assertEqual(extract_duck_turns(lines), [{"user": "Fix the build.", "assistant": reply}])

    def test_full_history_reads_a_bounded_tail(self):
        # Whole-file reads of 300 MB transcripts took ~2 GB of RAM in the preview popup
        import tempfile
        from unittest.mock import patch
        from tmux_agent_switcher.adapters import base
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("".join(json.dumps({"n": i, "text": "你好" * 5}, ensure_ascii=False) + "\n" for i in range(50)))
        self.addCleanup(os.unlink, f.name)
        with patch.object(base, "FULL_HISTORY_MAX_BYTES", 300):
            lines = base.read_tail_lines(f.name, full=True)
        self.assertEqual(json.loads(lines[-1]), {"n": 49, "text": "你好" * 5})
        self.assertLess(len(lines), 50)
        self.assertTrue(all(json.loads(l) for l in lines))  # the partial first line is dropped

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


class TestAntigravityAdapter(unittest.TestCase):
    def test_previews_show_uncut_replies_and_every_recent_turn(self):
        # agy cuts long fields out of transcript.jsonl and keeps them in transcript_full.jsonl, where uncut tool output
        # (1 MB per call here) leaves a byte window of the file with less than one turn
        import re
        import tempfile
        from unittest.mock import patch
        cid = "0f0e0d0c-0b0a-0908-0706-050403020100"
        reply = "See **the fix** in [the doc](https://example.com/a_b) " + "x" * 3000
        records = lambda text, output: [r for i in range(5) for r in (
            {"step_index": 3 * i, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": f"<USER_REQUEST>\nask {i}\n</USER_REQUEST>"},
            {"step_index": 3 * i + 1, "source": "MODEL", "type": "GENERIC", "content": output},
            {"step_index": 3 * i + 2, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": text})]
        plain = lambda s: re.sub(r"\x1b\]8;;[^\x1b]*\x1b\\|\x1b\[[0-9;]*m", "", s)
        pane = PaneContext(pane_id="%1", pid="101", current_command="agy", title="AGY", agy_tag=cid)
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"HOME": home}):
            logs = os.path.join(home, ".gemini", "antigravity-cli", "brain", cid, ".system_generated", "logs")
            os.makedirs(logs)
            with open(os.path.join(logs, "transcript.jsonl"), "w") as f:
                for r in records(reply[:30] + "\n<truncated 3030 bytes>\n" + reply[-20:], "o" * 100):
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
            with open(os.path.join(logs, "transcript_full.jsonl"), "w") as f:
                for n, r in enumerate(records(reply, "o" * (1 << 20))):
                    f.write(json.dumps(r, separators=(",", ":") if n % 2 else None) + "\n")  # both spellings occur
            recent, full = (plain(AntigravityAdapter().get_preview(pane, "", [], h, 200).content) for h in (False, True))
            os.unlink(os.path.join(logs, "transcript_full.jsonl"))
            fallback = plain(AntigravityAdapter().get_preview(pane, "", [], False, 200).content)
        for ask in ("ask 1", "ask 2", "ask 3", "ask 4"):
            self.assertIn(ask, recent)
        self.assertIn("See the fix in the doc", recent)
        self.assertNotIn("<truncated", recent + full)
        self.assertIn("ask 0", full)
        self.assertIn("ask 4", fallback)  # no uncut log: the cut one still shows every turn


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

    def test_osc9_raw_for_a_terminal_wrapped_for_a_nested_tmux(self):
        from tmux_agent_switcher.notifications.gateway import _osc9
        # Control bytes in agent text cannot end the alert or smuggle in another sequence (here OSC 52).
        self.assertEqual(_osc9("hi\x1b]52;c;eA==\x07", "xterm-ghostty"), "\x1b]9;hi ]52;c;eA== \x1b\\")
        # Passthrough needs every inner ESC doubled, including the one in the closing ST.
        self.assertEqual(_osc9("hi", "tmux-256color"), "\x1bPtmux;\x1b\x1b]9;hi\x1b\x1b\\\x1b\\")

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


class TestBundle(unittest.TestCase):
    """bin/tmux-agent-switcher is what actually runs (the TPM plugin, the curl install); the rest of the suite tests src."""

    def setUp(self):
        import importlib.util
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location("bundle", os.path.join(root, "scripts", "bundle.py"))
        self.bundle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bundle)
        self.bin = os.path.join(root, "bin", "tmux-agent-switcher")

    def test_committed_bin_is_the_bundle_of_src(self):
        with open(self.bin, encoding="utf-8") as f:
            self.assertEqual(f.read(), self.bundle.bundle_source(), "bin/ is stale: run python3 scripts/bundle.py")

    def test_every_module_is_bundled_and_no_two_define_one_name(self):
        # the bundle is one namespace: a name defined twice silently resolves to the later module everywhere
        import ast, collections
        src = self.bundle.SRC_DIR
        modules = {os.path.relpath(os.path.join(d, f), src) for d, _, fs in os.walk(src)
                   for f in fs if f.endswith(".py") and f not in ("__init__.py", "__main__.py")}
        self.assertEqual(modules, {rel for rel, _ in self.bundle.MODULES_IN_ORDER})
        owners = collections.defaultdict(set)
        for rel in modules:
            with open(os.path.join(src, rel), encoding="utf-8") as f:
                for node in ast.parse(f.read()).body:
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                        owners[node.name].add(rel)
                    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                        for t in getattr(node, "targets", [getattr(node, "target", None)]):
                            if isinstance(t, ast.Name):
                                owners[t.id].add(rel)
        self.assertEqual({n: m for n, m in owners.items() if len(m) > 1}, {})

    def test_the_bundled_cli_lists_a_pane(self):
        # the shipped script end to end (dispatch, shared index, every adapter's detect) against a fake one-pane tmux
        import subprocess, sys, tempfile
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, "bin"))
            with open(os.path.join(home, "bin", "tmux"), "w") as f:
                f.write("#!/bin/sh\n[ \"$1\" = list-panes ] && printf "
                        "'t:1.1\\t999999\\tzsh\\tzsh\\t0\\t1\\t1\\t\\t\\t\\t\\t/tmp\\t1\\t\\t\\t\\t80\\t24\\n'\nexit 0\n")
            os.chmod(os.path.join(home, "bin", "tmux"), 0o755)
            env = dict(os.environ, HOME=home, PATH=os.path.join(home, "bin") + os.pathsep + os.environ.get("PATH", ""))
            r = subprocess.run([sys.executable, self.bin, "list"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("t:1.1", r.stdout)


if __name__ == "__main__":
    unittest.main()
