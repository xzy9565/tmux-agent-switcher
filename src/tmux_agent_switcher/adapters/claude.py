"""Anthropic Claude Code adapter."""

import datetime
import glob
import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_CLAUDE
from ..formatters.bubbles import format_turns
from .base import BaseAgentAdapter, read_tail_lines, extract_duck_turns


def claude_state(fpath: str, max_bytes: int = 250000) -> Tuple[float, bool]:
    """
    Determine last dialogue timestamp and whether the turn is still open.
    A finished turn ends in stop_hook_summary or turn_duration.
    """
    try:
        with open(fpath, "rb") as f:
            f.seek(max(0, os.path.getsize(fpath) - max_bytes))
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
    except Exception:
        return 0.0, False

    ended = False
    for l in reversed(lines):
        try:
            d = json.loads(l)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        t = d.get("type")
        if t == "system" and d.get("subtype") in ("stop_hook_summary", "turn_duration"):
            ended = True
        if t not in ("user", "assistant") or not d.get("timestamp"):
            continue
        ts = datetime.datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
        m = d.get("message") or {}
        c = json.dumps(m.get("content"))
        if ended or d.get("isMeta") or "[Request interrupted" in c or c.startswith(("\"<command-", "\"<local-command-")):
            return ts, False
        return ts, t == "user" or m.get("stop_reason") in (None, "tool_use")

    try:
        return os.path.getmtime(fpath), False
    except Exception:
        return 0.0, False


class ClaudeAdapter(BaseAgentAdapter):
    name = "claude"
    display_name = "🟠 Claude"
    color = C_CLAUDE

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        if heads:
            return "claude" in heads
        return "claude" in pane.current_command or "claude" in full_desc

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        claude_idx = shared_state.get("claude_idx", {})
        cc_status = shared_state.get("cc_status", {})
        tree = shared_state.get("tree", {})
        my_pid = shared_state.get("my_pid", "")

        mtime = 0.0
        running = False
        rank = 2
        status = "💤 idle"

        if pane.claude_sid in claude_idx:
            mtime, running = claude_state(claude_idx[pane.claude_sid])

        # Find shallowest process registered in cc_status
        st = None
        level = [str(pane.pid)]
        while level:
            for p in level:
                if p in cc_status:
                    st = cc_status[p]
                    break
            if st:
                break
            level = [c for p in level for c, _ in tree.get(p, []) if c != my_pid]

        if st:
            if st == "busy" and pane.claude_sid in claude_idx and not running:
                st = "agents"
            running = False
            status_map = {
                "busy": ("⚡ run", 1),
                "agents": ("🤖 subagent", 1),
                "shell": ("🐚 shell", 1),
                "waiting": ("🔔 input", 0)
            }
            if st in status_map:
                status, rank = status_map[st]

        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).strip()
        return self.display_name, self.color, rank, mtime, disp_t, running

    def get_preview(
        self,
        pane: PaneContext,
        full_desc: str,
        desc_pids: List[str],
        full_history: bool,
        preview_width: int
    ) -> Optional[PreviewResult]:
        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).strip()
        matches = []
        if pane.claude_sid:
            matches = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{pane.claude_sid}.jsonl"))

        if not matches:
            # Fallback: find by newest jsonl in ~/.claude/projects
            all_files = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
            if all_files:
                matches = [max(all_files, key=os.path.getmtime)]

        if matches:
            filepath = matches[0]
            lines = read_tail_lines(filepath, max_bytes=300000, full=full_history)
            turns = extract_duck_turns(lines, full=full_history)
            if len(turns) < 4 and not full_history and os.path.exists(filepath) and os.path.getsize(filepath) > 300000:
                turns = extract_duck_turns(read_tail_lines(filepath, max_bytes=3000000), full=False)

            if turns:
                is_live = False
                try:
                    is_live = time.time() - os.path.getmtime(filepath) < 120
                except Exception:
                    pass
                content = format_turns(turns, live=is_live, full_history=full_history, preview_width=preview_width)
                return PreviewResult(
                    source_label=f"{self.display_name} · {disp_t}",
                    content=content
                )
        return None
