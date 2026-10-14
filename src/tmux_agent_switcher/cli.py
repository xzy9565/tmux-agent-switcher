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
    C_RESET, C_DIM, C_GREEN_BOLD, C_RED_BOLD, C_CYAN_BOLD, C_WHITE, pad_str
)
from .core.types import PreviewResult
from .core.tmux import ProcessTree, list_all_panes, get_pane_context
from .adapters.registry import ADAPTERS, resolve_adapter
from .adapters.fallback import FallbackTuiAdapter
from .notifications.gateway import handle_notify, handle_hook_title, handle_clear, handle_focus_out
from .notifications.context import resolve_notification_context


def index_shared_state() -> Dict[str, Any]:
    """Pre-scan private databases and session indexes once per listing."""
    state: Dict[str, Any] = {
        "now": time.time(),
        "host": os.uname().nodename.split(".")[0],
        "home": os.path.expanduser("~"),
        "agy_titles": {}, "codex_threads": {}, "codex_cwd": {},
        "oc_sessions": [], "oc_db": None, "claude_idx": {}, "cc_status": {}
    }

    # AGY summaries
    agy_db = os.path.expanduser("~/.gemini/antigravity-cli/conversation_summaries.db")
    if os.path.exists(agy_db):
        try:
            with sqlite3.connect(f"file:{agy_db}?mode=ro", uri=True) as conn:
                for cid, title, idle in conn.execute("SELECT conversation_id, title, not_fully_idle FROM conversation_summaries"):
                    state["agy_titles"][cid] = (title, idle)
        except Exception:
            pass

    # Codex state
    codex_s = os.path.expanduser("~/.codex/state_5.sqlite")
    if os.path.exists(codex_s):
        try:
            with sqlite3.connect(f"file:{codex_s}?mode=ro", uri=True) as conn:
                for tid, name, u_ms, cwd in conn.execute("SELECT id, name, updated_at_ms, cwd FROM threads"):
                    if name:
                        state["codex_threads"][name.strip()] = (tid, (u_ms or 0) / 1000.0)
                    state["codex_cwd"][cwd] = max(state["codex_cwd"].get(cwd, 0.0), (u_ms or 0) / 1000.0)
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
    state["cc_sessions"] = {}
    for f in glob.glob(os.path.expanduser("~/.claude*/sessions/*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            pid_s = str(d.get("pid", ""))
            state["cc_status"][pid_s] = d.get("status")
            state["cc_sessions"][pid_s] = d
        except Exception:
            pass

    return state


def cmd_list(args: argparse.Namespace) -> None:
    """Generate ANSI-styled, priority-sorted pane list for fzf."""
    tree = ProcessTree()
    panes = list_all_panes()
    state = index_shared_state()
    state.update({"tree": tree.tree, "cmd_of": tree.cmd_of, "my_pid": tree.my_pid})
    now = state["now"]
    out: List[Tuple[int, float, str]] = []

    for pane in panes:
        desc_cmd = tree.get_descendants(pane.pid)
        heads = tree.get_agent_heads(pane.pid)
        adapter = resolve_adapter(pane, desc_cmd, heads)
        harness, h_col, rank, mtime, title, running = adapter.get_status(pane, desc_cmd, heads, state)

        is_current = (pane.window_active == "1" and pane.pane_active == "1" and pane.session_attached != "0")
        is_monitor = (adapter.name == "system-monitor")
        status = "💤 idle"

        if running and (now - mtime < 1800):
            if isinstance(running, str) and running in ("🤖 subagent", "🐚 shell"):
                status = running
            else:
                status = "⚡ run"
            rank = 1

        if is_monitor:
            rank, age_str = 3, "LIVE"
        else:
            if not running and (rank == 0 or pane.pane_needs_input == "1" or (pane.bell_flag == "1" and pane.pane_active == "1")):
                status, rank = "🔔 input", 0

            if running and pane.pane_needs_input == "1":
                try:
                    subprocess.run(["tmux", "set", "-pu", "-t", pane.pane_id, "@pane-needs-input"], capture_output=True)
                    subprocess.run(["tmux", "set", "-pu", "-t", pane.pane_id, "pane-border-style"], capture_output=True)
                    subprocess.run(["tmux", "set", "-pu", "-t", pane.pane_id, "pane-active-border-style"], capture_output=True)
                except Exception:
                    pass

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
            "🔔 input": C_RED_BOLD, "⚡ run": C_GREEN_BOLD,
            "🤖 subagent": C_CYAN_BOLD, "🐚 shell": C_CYAN_BOLD
        }.get(status, C_DIM)

        mark = f"{C_GREEN_BOLD}▶{C_RESET}" if is_current else " "
        id_w = max((len(p.pane_id) for p in panes), default=8)
        id_w = max(id_w, 8)
        pad_id = f"{pane.pane_id:<{id_w}}"
        pad_h = pad_str(harness, 13)
        pad_s = pad_str(status, 10)
        formatted = (
            f"{pad_id} {mark} {h_col}{pad_h}{C_RESET} "
            f"{s_col}{pad_s}{C_RESET} {C_DIM}{age_str:>4}{C_RESET}  "
            f"{C_WHITE}{title[:60]}{C_RESET}"
        )
        out.append((rank, mtime, formatted))

    if state.get("oc_db"):
        try:
            state["oc_db"].close()
        except Exception:
            pass

    out.sort(key=lambda x: (x[0], -x[1]))
    for _, _, line in out:
        print(line)


def cmd_preview(args: argparse.Namespace) -> None:
    """Render preview dialogue bubbles or fallback terminal buffer for target pane."""
    if not args.pane_id:
        return
    pane = get_pane_context(args.pane_id)
    if not pane:
        return

    tree = ProcessTree(tty=pane.tty if pane.tty else None)
    desc_cmd = tree.get_descendants(pane.pid)
    desc_pids = tree.get_descendant_pids(pane.pid)
    heads = tree.get_agent_heads(pane.pid)

    full = "full history" in os.environ.get("FZF_PREVIEW_LABEL", "")
    w = int(os.environ.get("FZF_PREVIEW_COLUMNS") or 0)

    result: Optional[PreviewResult] = None
    matched_adapter = None
    for adapter in ADAPTERS:
        try:
            if adapter.detect(pane, desc_cmd, heads):
                if not matched_adapter:
                    matched_adapter = adapter
                result = adapter.get_preview(pane, desc_cmd, desc_pids, full, w)
                if result:
                    break
        except Exception:
            continue

    if not result:
        result = FallbackTuiAdapter().get_preview(pane, desc_cmd, desc_pids, full, w)

    if result and matched_adapter and matched_adapter.name != "fallback":
        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).strip()
        clean_t = re.sub(
            r"\s*-\s*(?:[⏳🧭⌨️📝🔄📋❓🔐✅📤📂]\s*)?(?:Working.*|Planning|Running shell.*|Reviewing.*|Reconnecting|Queued|Waiting for.*|Ready|Moving to cloud|Loading conversation)\s*$",
            "",
            disp_t
        ).strip()
        disp_t = clean_t if clean_t else disp_t
        result.source_label = f"{matched_adapter.display_name} · {disp_t or pane.pane_id}"

    if result:
        print(f"\033[1;36m━━━ {args.pane_id} · {result.source_label} ━━━\033[0m\n\n{result.content}")


def cmd_copy(args: argparse.Namespace) -> None:
    """Extract plain text preview and load into OSC 52 + system clipboard."""
    if not args.pane_id:
        return
    env = os.environ.copy()
    env["FZF_PREVIEW_COLUMNS"] = ""

    try:
        proc = subprocess.run(
            [sys.executable, sys.argv[0], "preview", args.pane_id],
            capture_output=True, text=True, env=env, check=False
        )
        raw = proc.stdout
    except Exception:
        raw = ""

    lines = raw.split("\n")
    content = "\n".join(lines[2:]) if len(lines) > 2 else raw
    content = re.sub(r'\033\[[0-9;]*m', '', content)
    content = re.sub(r'\033\]8;;[^\033]*\033\\', '', content)

    if not content.strip():
        try:
            content = subprocess.check_output(
                ["tmux", "capture-pane", "-pJ", "-S", "-100", "-t", args.pane_id]
            ).decode("utf-8", errors="ignore")
        except Exception:
            content = ""

    # Load into tmux buffer with OSC 52 flag (-w)
    try:
        subprocess.run(["tmux", "load-buffer", "-w", "-"], input=content.encode("utf-8"), check=False)
    except Exception:
        pass

    for tool in [["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]]:
        try:
            subprocess.run(tool, input=content.encode("utf-8"), check=True, stderr=subprocess.DEVNULL)
            break
        except Exception:
            continue

    try:
        subprocess.run(["tmux", "display-message", f"📋 Copied {args.pane_id} to system clipboard"], check=False)
    except Exception:
        pass


def cmd_run(args: argparse.Namespace) -> None:
    """Run interactive fzf switcher inside the popup."""
    self_path = os.path.abspath(sys.argv[0])
    pw = getattr(args, "preview_width", "58%") or "58%"

    fzf_cmd = (
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
        subprocess.run(["sh", "-c", fzf_cmd])
    except Exception:
        pass


def cmd_popup(args: argparse.Namespace) -> None:
    """Directly trigger tmux popup window."""
    self_path = os.path.abspath(sys.argv[0])
    w = getattr(args, "width", "88%") or "88%"
    h = getattr(args, "height", "82%") or "82%"
    pw = getattr(args, "preview_width", "58%") or "58%"

    subprocess.run([
        "tmux", "display-popup", "-w", w, "-h", h, "-E",
        f"{self_path} run --preview-width '{pw}'"
    ])


def read_stdin_payload(timeout: float = 0.05) -> str:
    """Read available payload from stdin without blocking indefinitely on unclosed pipes."""
    if sys.stdin.isatty():
        return ""
    try:
        import select
        chunks = []
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        while r:
            chunk = os.read(sys.stdin.fileno(), 65536)
            if not chunk:
                break
            chunks.append(chunk)
            r, _, _ = select.select([sys.stdin], [], [], 0.005)
        return b"".join(chunks).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def cmd_notify(args: argparse.Namespace) -> None:
    stdin_data = read_stdin_payload()
    handle_notify(
        agent_name=args.agent or "",
        message=args.message or "",
        target_pane=args.pane_id or "",
        force=args.force,
        stdin_data=stdin_data
    )
    print("{}")


def cmd_hook_title(args: argparse.Namespace) -> None:
    try:
        pane_id = args.pane_id or os.environ.get("TMUX_PANE", "")
        handle_hook_title(pane_id)
    except Exception:
        pass
    sys.exit(0)


def cmd_clear(args: argparse.Namespace) -> None:
    try:
        pane_id = args.pane_id or os.environ.get("TMUX_PANE", "")
        handle_clear(pane_id)
    except Exception:
        pass
    sys.exit(0)


def cmd_focus_out(args: argparse.Namespace) -> None:
    try:
        pane_id = args.pane_id or os.environ.get("TMUX_PANE", "")
        handle_focus_out(pane_id)
    except Exception:
        pass
    sys.exit(0)


def cmd_context(args: argparse.Namespace) -> None:
    raw = read_stdin_payload()
    payload = {}
    if raw:
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {}
    ctx = resolve_notification_context(
        agent_name=args.agent or "",
        payload=payload,
        target_pane=args.pane_id or "",
        tag_session=getattr(args, "tag_session", False)
    )
    print(json.dumps(ctx))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="tmux-agent-switcher",
        description="Unified multiplexing & dialogue preview center for AI coding agents in Tmux."
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List and rank all active agents across panes")
    p_prev = sub.add_parser("preview", help="Render formatted preview for a pane")
    p_prev.add_argument("pane_id", help="Target tmux pane identifier (e.g. %%43)")

    p_copy = sub.add_parser("copy", help="Copy unwrapped dialogue to clipboard via OSC 52")
    p_copy.add_argument("pane_id", help="Target tmux pane identifier (e.g. %%43)")

    p_run = sub.add_parser("run", help="Run interactive fzf switcher pipeline")
    p_run.add_argument("--preview-width", default="58%", help="Preview panel width (default: 58%%)")

    p_pop = sub.add_parser("popup", help="Directly trigger tmux popup window")
    p_pop.add_argument("--width", default="88%", help="Popup width (default: 88%%)")
    p_pop.add_argument("--height", default="82%", help="Popup height (default: 82%%)")
    p_pop.add_argument("--preview-width", default="58%", help="Preview panel width (default: 58%%)")

    p_not = sub.add_parser("notify", help="Universal AI agent notification dispatcher")
    p_not.add_argument("agent", nargs="?", default="", help="Agent name (e.g. Claude, Cursor, AGY)")
    p_not.add_argument("message", nargs="?", default="", help="Notification message summary")
    p_not.add_argument("pane_id", nargs="?", default="", help="Target tmux pane identifier (e.g. %%43)")
    p_not.add_argument("-f", "--force", action="store_true", help="Force notification even if focused")

    p_hook = sub.add_parser("hook-title", help="Handle tmux pane-title-changed event")
    p_hook.add_argument("pane_id", nargs="?", default="", help="Target tmux pane identifier (e.g. %%43)")

    p_clr = sub.add_parser("clear", help="Clear visual yellow border and input alerts on pane")
    p_clr.add_argument("pane_id", nargs="?", default="", help="Target tmux pane identifier (e.g. %%43)")

    p_fo = sub.add_parser("focus-out", help="Raise an alert held back while the pane had focus")
    p_fo.add_argument("pane_id", nargs="?", default="", help="Target tmux pane identifier (e.g. %%43)")

    p_ctx = sub.add_parser("context", help="Resolve notification context for an agent")
    p_ctx.add_argument("agent", nargs="?", default="", help="Agent name")
    p_ctx.add_argument("pane_id", nargs="?", default="", help="Target tmux pane identifier")
    p_ctx.add_argument("--tag-session", action="store_true", help="Tag tmux pane with session metadata")

    args = parser.parse_args()
    dispatch = {
        "list": cmd_list, "preview": cmd_preview, "copy": cmd_copy,
        "run": cmd_run, "popup": cmd_popup, "notify": cmd_notify,
        "hook-title": cmd_hook_title, "clear": cmd_clear, "focus-out": cmd_focus_out, "context": cmd_context
    }
    if args.command in dispatch:
        dispatch[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
