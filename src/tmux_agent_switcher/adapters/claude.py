"""Anthropic Claude Code adapter."""

import datetime
import glob
import json
import os
import re
import subprocess
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


def resolve_session_chain(sid: str, claude_idx: Dict[str, str]) -> str:
    """Follow continued-in session pointers to find the tip of the conversation."""
    seen = {sid}
    curr = sid
    while curr in claude_idx:
        p = claude_idx[curr]
        next_sid = None
        try:
            with open(p, "rb") as f:
                f.seek(max(0, os.path.getsize(p) - 8192))
                lines = f.read().decode("utf-8", errors="ignore").splitlines()
            for l in reversed(lines):
                if '"continued-in"' in l:
                    try:
                        d = json.loads(l)
                        if d.get("type") == "continued-in" and d.get("continuedInSessionId"):
                            next_sid = d["continuedInSessionId"]
                            break
                    except Exception:
                        pass
        except Exception:
            pass
        if next_sid and next_sid not in seen and next_sid in claude_idx:
            seen.add(next_sid)
            curr = next_sid
        else:
            break
    return curr


def find_active_session_info(
    pane: PaneContext,
    claude_idx: Dict[str, str],
    cc_sessions: Dict[str, Any],
    desc_pids: List[str]
) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve active session ID and its reported execution status across:
    - Parked background jobs in child processes (jobId / parkedJobId)
    - Directly recorded session processes
    - Pane claude_sid tags
    - Terminal buffer screen capture fallback
    - Session continuation chains (continuedInSessionId)
    """
    candidate = pane.claude_sid
    active_st = None

    # Check child processes for parked background jobs or active sessions
    for p in desc_pids:
        if p in cc_sessions:
            sess = cc_sessions[p]
            parked = sess.get("parkedJobId")
            if parked:
                for other_p, other_sess in cc_sessions.items():
                    if other_sess.get("jobId") == parked or (other_sess.get("sessionId") and other_sess["sessionId"].startswith(parked)):
                        candidate = other_sess.get("sessionId")
                        active_st = other_sess.get("status")
                        break
                if not candidate:
                    for target_sid in claude_idx:
                        if target_sid.startswith(parked):
                            candidate = target_sid
                            break
            elif not candidate or candidate not in claude_idx:
                candidate = sess.get("sessionId")
                active_st = sess.get("status")

    if not candidate or candidate not in claude_idx:
        for p in desc_pids:
            if p in cc_sessions:
                candidate = cc_sessions[p].get("sessionId")
                if candidate in claude_idx:
                    active_st = cc_sessions[p].get("status")
                    break

    # Screen fallback for active session short ID (e.g. 🔗 d10be121)
    if not candidate or candidate not in claude_idx:
        try:
            screen = subprocess.run(
                ["tmux", "capture-pane", "-pJ", "-S", "-15", "-t", pane.pane_id],
                capture_output=True,
                text=True,
                timeout=0.25
            ).stdout or ""
            m = re.search(r"🔗\s*([0-9a-f]{8})", screen)
            if m:
                short_id = m.group(1)
                for sid in claude_idx:
                    if sid.startswith(short_id):
                        candidate = sid
                        break
        except Exception:
            pass

    if candidate:
        resolved = resolve_session_chain(candidate, claude_idx)
        if resolved != candidate:
            for other_sess in cc_sessions.values():
                if other_sess.get("sessionId") == resolved:
                    active_st = other_sess.get("status")
                    break
        return resolved, active_st

    return None, None


class ClaudeAdapter(BaseAgentAdapter):
    name = "claude"
    display_name = "🟠 Claude"
    color = C_CLAUDE

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        if heads:
            return "claude" in heads
        return "claude" in pane.current_command or "claude" in full_desc or bool(pane.claude_sid)

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, Any]:
        claude_idx = shared_state.get("claude_idx", {})
        cc_status = shared_state.get("cc_status", {})
        cc_sessions = shared_state.get("cc_sessions", {})
        tree = shared_state.get("tree", {})
        my_pid = shared_state.get("my_pid", "")

        desc_pids = [str(pane.pid)]
        stack = [str(pane.pid)]
        while stack:
            curr = stack.pop()
            for cp, _ in tree.get(curr, []):
                if cp != my_pid and cp not in desc_pids:
                    desc_pids.append(cp)
                    stack.append(cp)

        active_sid, st = find_active_session_info(pane, claude_idx, cc_sessions, desc_pids)

        mtime = 0.0
        running = False
        rank = 2

        if active_sid and active_sid in claude_idx:
            mtime, running = claude_state(claude_idx[active_sid])
            if running:
                rank = 1

        if not st:
            for p in desc_pids:
                if p in cc_status:
                    st = cc_status[p]
                    break

        if st:
            if st in ("busy", "agents", "shell"):
                rank = 1
                if st == "agents":
                    running = "🤖 subagent"
                elif st == "shell":
                    running = "🐚 shell"
                else:
                    running = True
                if not mtime or (time.time() - mtime > 1800):
                    mtime = time.time()
            elif st == "waiting":
                rank = 0
                running = False
            elif st == "idle":
                if not running:
                    rank = 2
                    running = False

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
        claude_paths = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
        claude_idx = {os.path.basename(p)[:-6]: p for p in claude_paths}

        cc_sessions = {}
        for f in glob.glob(os.path.expanduser("~/.claude*/sessions/*.json")):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
                cc_sessions[str(d.get("pid", ""))] = d
            except Exception:
                pass

        active_sid, _ = find_active_session_info(pane, claude_idx, cc_sessions, desc_pids)

        filepath = None
        if active_sid and active_sid in claude_idx:
            filepath = claude_idx[active_sid]
        elif pane.claude_sid:
            matches = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{pane.claude_sid}.jsonl"))
            if matches:
                filepath = matches[0]

        if filepath and os.path.exists(filepath):
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
