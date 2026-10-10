"""Command-line interface and subcommand entrypoints for tmux-agent-switcher."""

import argparse
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from typing import Dict, List, Tuple, Any

from .core.config import (
    C_RESET, C_DIM, C_GREEN_BOLD, C_RED_BOLD, C_CYAN_BOLD, C_WHITE,
    SHELLS
)
from .core.types import PaneContext, PreviewResult
from .core.tmux import ProcessTree, list_all_panes, get_pane_context
from .adapters.registry import ADAPTERS, resolve_adapter
from .adapters.fallback import FallbackTuiAdapter


def index_shared_state() -> Dict[str, Any]:
    """Pre-scan private databases and session indexes once per listing."""
    state: Dict[str, Any] = {
        "now": time.time(),
        "host": os.uname().nodename.split(".")[0],
        "home": os.path.expanduser("~"),
        "agy_titles": {},
        "codex_threads": {},
        "codex_cwd": {},
        "oc_sessions": [],
        "oc_db": None,
        "claude_idx": {},
        "cc_status": {},
    }

    # AGY summaries
    agy_db = os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db")
    if os.path.exists(agy_db):
        try:
            conn = sqlite3.connect(f"file:{agy_db}?mode=ro", uri=True)
            for cid, title, idle in conn.execute("SELECT conversation_id, title, not_fully_idle FROM conversation_summaries"):
                state["agy_titles"][cid] = (title, idle)
            conn.close()
        except Exception:
            pass

    # Codex state
    codex_s_db = os.path.expanduser("~/.codex/state_5.sqlite")
    if os.path.exists(codex_s_db):
        try:
            conn = sqlite3.connect(f"file:{codex_s_db}?mode=ro", uri=True)
            for tid, tname, u_ms, tcwd in conn.execute("SELECT id, name, updated_at_ms, cwd FROM threads"):
                if tname:
                    state["codex_threads"][tname.strip()] = (tid, (u_ms or 0) / 1000.0)
                state["codex_cwd"][tcwd] = max(state["codex_cwd"].get(tcwd, 0.0), (u_ms or 0) / 1000.0)
            conn.close()
        except Exception:
            pass

    # OpenCode state
    oc_path = os.path.expanduser("~/.local/share/opencode/opencode.db")
    if os.path.exists(oc_path):
        try:
            conn = sqlite3.connect(f"file:{oc_path}?mode=ro", uri=True)
            state["oc_db"] = conn
            state["oc_sessions"] = conn.execute("SELECT id, title, time_updated FROM session WHERE parent_id IS NULL").fetchall()
        except Exception:
            pass

    # Claude indices
    claude_paths = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    state["claude_idx"] = {os.path.basename(p)[:-6]: p for p in claude_paths}

    for f in glob.glob(os.path.expanduser("~/.claude*/sessions/*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            state["cc_status"][str(d["pid"])] = d.get("status")
        except Exception:
            pass

    return state


def cmd_list(args: argparse.Namespace) -> None:
    """Generate ANSI-styled, priority-sorted pane list for fzf."""
    tree = ProcessTree.from_system()
    panes = list_all_panes()
    shared_state = index_shared_state()
    shared_state["tree"] = tree.tree
    shared_state["cmd_of"] = tree.cmd_of
    shared_state["my_pid"] = tree.my_pid

    now = shared_state["now"]
    out: List[Tuple[int, float, str]] = []

    for pane in panes:
        desc_cmd = tree.get_descendants(pane.pid)
        heads = tree.get_agent_heads(pane.pid)

        adapter = resolve_adapter(pane, desc_cmd, heads)
        harness, h_col, rank, mtime, title, running = adapter.get_status(
            pane, desc_cmd, heads, shared_state
        )

        is_current = (pane.window_active == "1" and pane.pane_active == "1" and pane.session_attached != "0")
        is_monitor = (adapter.name == "system-monitor")

        status = "💤 idle"
        if running:
            # 30-min watchdog cap: turn cut off by kill should not read running forever
            if now - mtime < 1800:
                status = "⚡ run"
                rank = 1

        if is_monitor:
            rank = 3
            age_str = "LIVE"
        else:
            if pane.pane_needs_input == "1" or (pane.bell_flag == "1" and pane.pane_active == "1"):
                status = "🔔 input"
                rank = 0

            if mtime:
                diff = max(0, int(now - mtime))
                if diff < 60:
                    age_str = f"{diff}s"
                elif diff < 3600:
                    age_str = f"{diff // 60}m"
                elif diff < 86400:
                    age_str = f"{diff // 3600}h"
                else:
                    age_str = f"{diff // 86400}d"
            else:
                age_str = " - "

        s_col = {
            "🔔 input": C_RED_BOLD,
            "⚡ run": C_GREEN_BOLD,
            "🤖 subagent": C_CYAN_BOLD,
            "🐚 shell": C_CYAN_BOLD
        }.get(status, C_DIM)

        mark = f"{C_GREEN_BOLD}▶{C_RESET}" if is_current else " "
        formatted = (
            f"{pane.pane_id:<10} {mark} {h_col}{harness:<12}{C_RESET} "
            f"{s_col}{status:<10}{C_RESET} {C_DIM}{age_str:>4}{C_RESET}  "
            f"{C_WHITE}{title[:60]}{C_RESET}"
        )
        out.append((rank, mtime, formatted))

    # Clean up SQLite connection if opened
    if shared_state.get("oc_db"):
        try:
            shared_state["oc_db"].close()
        except Exception:
            pass

    out.sort(key=lambda x: (x[0], -x[1]))
    for _, _, line in out:
        print(line)


def cmd_preview(args: argparse.Namespace) -> None:
    """Render preview dialogue bubbles or fallback terminal buffer for target pane."""
    pane_id = args.pane_id
    if not pane_id:
        sys.exit(0)

    pane = get_pane_context(pane_id)
    if not pane:
        sys.exit(0)

    # Scoped process tree lookup by TTY for high performance
    tree = ProcessTree.from_system(tty=pane.tty if pane.tty else None)
    desc_cmd = tree.get_descendants(pane.pid)
    desc_pids = tree.get_descendant_pids(pane.pid)
    heads = tree.get_agent_heads(pane.pid)

    full_history = "full history" in os.environ.get("FZF_PREVIEW_LABEL", "")
    preview_width = int(os.environ.get("FZF_PREVIEW_COLUMNS") or 0)

    result: Optional[PreviewResult] = None
    # Try resolving via adapter chain
    for adapter in ADAPTERS:
        try:
            if adapter.detect(pane, desc_cmd, heads):
                result = adapter.get_preview(
                    pane, desc_cmd, desc_pids, full_history, preview_width
                )
                if result:
                    break
        except Exception:
            continue

    if not result:
        result = FallbackTuiAdapter().get_preview(
            pane, desc_cmd, desc_pids, full_history, preview_width
        )

    if result:
        header = f"\033[1;36m━━━ {pane_id} · {result.source_label} ━━━\033[0m\n\n"
        print(header + result.content)


def cmd_copy(args: argparse.Namespace) -> None:
    """Extract plain text preview and load into OSC 52 + system clipboard."""
    pane_id = args.pane_id
    if not pane_id:
        sys.exit(0)

    # Temporarily unset FZF_PREVIEW_COLUMNS to prevent hard-wrapping in clipboard content
    env = os.environ.copy()
    env["FZF_PREVIEW_COLUMNS"] = ""

    try:
        proc = subprocess.run(
            [sys.executable, sys.argv[0], "preview", pane_id],
            capture_output=True,
            text=True,
            env=env,
            check=False
        )
        raw_output = proc.stdout
    except Exception:
        raw_output = ""

    # Strip header line (━━━ ...)
    lines = raw_output.split("\n")
    content = "\n".join(lines[2:]) if len(lines) > 2 else raw_output

    # Strip ANSI color escapes and OSC 8 hyperlinks
    content = re.sub(r'\033\[[0-9;]*m', '', content)
    content = re.sub(r'\033\]8;;[^\033]*\033\\', '', content)

    # Fallback to direct terminal capture if content is empty
    if not content.strip():
        try:
            content = subprocess.check_output(
                ["tmux", "capture-pane", "-pJ", "-S", "-100", "-t", pane_id]
            ).decode("utf-8", errors="ignore")
        except Exception:
            content = ""

    # Load into tmux paste buffer with OSC 52 flag (-w)
    try:
        subprocess.run(["tmux", "load-buffer", "-w", "-"], input=content.encode("utf-8"), check=False)
    except Exception:
        pass

    # Copy to native system clipboard
    copied = False
    for clip_tool in [["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]]:
        try:
            subprocess.run(clip_tool, input=content.encode("utf-8"), check=True, stderr=subprocess.DEVNULL)
            copied = True
            break
        except Exception:
            continue

    try:
        subprocess.run(["tmux", "display-message", f"📋 Copied {pane_id} to system clipboard"], check=False)
    except Exception:
        pass


def cmd_run(args: argparse.Namespace) -> None:
    """Run interactive fzf switcher inside the popup."""
    self_path = os.path.abspath(sys.argv[0])
    pw = getattr(args, "preview_width", "58%") or "58%"

    popup_shell_cmd = (
        f"{self_path} list | fzf --ansi --reverse "
        f"--prompt='🔎 Agent Switcher > ' "
        f"--header='Enter: Jump | Tab: Preview | Ctrl-o: Full History | Shift-↑/↓ / Ctrl-u/d: Scroll | Ctrl-y: Copy (OSC 52) | Ctrl-x: Kill' "
        f"--preview-window 'right,{pw},border-left,wrap,follow' "
        f"--preview '{self_path} preview {{1}}' "
        f"--bind 'tab:toggle-preview' "
        f"--bind 'ctrl-o:transform-preview-label([ -n \"$FZF_PREVIEW_LABEL\" ] || echo \" full history \")+refresh-preview' "
        f"--bind 'focus:change-preview-label()' "
        f"--bind 'ctrl-d:preview-half-page-down,ctrl-u:preview-half-page-up' "
        f"--bind 'shift-up:preview-up,shift-down:preview-down' "
        f"--bind 'ctrl-b:preview-page-up,ctrl-f:preview-page-down' "
        f"--bind 'alt-up:preview-top,alt-down:preview-bottom' "
        f"--bind 'ctrl-y:execute-silent({self_path} copy {{1}})' "
        f"--bind 'alt-y:execute-silent(echo -n {{1}} | tmux load-buffer -w -; tmux display-message \"📋 Copied {{1}}\")' "
        f"--bind 'ctrl-x:execute(tmux kill-pane -t {{1}})+reload({self_path} list)' "
        f"| awk '{{print $1}}' | xargs -r tmux switch-client -t"
    )

    try:
        subprocess.run(["sh", "-c", popup_shell_cmd])
    except Exception:
        pass


def cmd_popup(args: argparse.Namespace) -> None:
    """Directly trigger tmux popup window."""
    self_path = os.path.abspath(sys.argv[0])
    width = getattr(args, "width", "88%") or "88%"
    height = getattr(args, "height", "82%") or "82%"
    pw = getattr(args, "preview_width", "58%") or "58%"

    subprocess.run([
        "tmux", "display-popup", "-w", width, "-h", height, "-E",
        f"{self_path} run --preview-width '{pw}'"
    ])


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tmux-agent-switcher",
        description="Unified multiplexing & dialogue preview center for AI coding agents in Tmux."
    )
    subparsers = parser.add_subparsers(dest="command")

    p_list = subparsers.add_parser("list", help="List and rank all active agents across panes")
    p_preview = subparsers.add_parser("preview", help="Render formatted preview for a pane")
    p_preview.add_argument("pane_id", help="Target tmux pane identifier (e.g. %%43 or session:1.2)")

    p_copy = subparsers.add_parser("copy", help="Copy unwrapped dialogue to clipboard via OSC 52")
    p_copy.add_argument("pane_id", help="Target tmux pane identifier (e.g. %%43)")

    p_run = subparsers.add_parser("run", help="Run interactive fzf switcher pipeline")
    p_run.add_argument("--preview-width", default="58%", help="Preview panel width (default: 58%%)")

    p_popup = subparsers.add_parser("popup", help="Directly trigger tmux popup window")
    p_popup.add_argument("--width", default="88%", help="Popup width (default: 88%%)")
    p_popup.add_argument("--height", default="82%", help="Popup height (default: 82%%)")
    p_popup.add_argument("--preview-width", default="58%", help="Preview panel width (default: 58%%)")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "preview":
        cmd_preview(args)
    elif args.command == "copy":
        cmd_copy(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "popup":
        cmd_popup(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
