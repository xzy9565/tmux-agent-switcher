"""Base interface and parsing utilities for AI Agent adapters."""

import datetime
import json
import os
import re
from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_DIM


def read_tail_lines(filepath: str, max_bytes: int = 800000, full: bool = False) -> List[str]:
    """
    Read tail lines from file without loading entire multi-gigabyte logs into RAM.
    If full is True, reads the entire file.
    """
    if not filepath or not os.path.exists(filepath):
        return []
    try:
        size = os.path.getsize(filepath)
        start = 0 if full else max(0, size - max_bytes)
        with open(filepath, "rb") as f:
            f.seek(start)
            raw = f.read().decode("utf-8", errors="ignore").strip().split("\n")
            return raw[1:] if start and len(raw) > 1 else raw
    except Exception:
        return []


def get_real_dialogue_time(filepath: str, max_bytes: int = 250000) -> float:
    """
    Extract the real timestamp of the most recent human prompt or agent reply.
    Avoids using raw mtime when loggers/watchdogs touch the file passively.
    """
    if not filepath or not os.path.exists(filepath):
        return 0.0
    try:
        size = os.path.getsize(filepath)
        with open(filepath, "rb") as f:
            f.seek(max(0, size - max_bytes))
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
        for l in reversed(lines):
            try:
                d = json.loads(l)
                t = d.get("type", "")
                r = d.get("message", {}).get("role", "") if isinstance(d.get("message"), dict) else d.get("role", "")
                if t in ("user", "assistant", "USER_INPUT", "PLANNER_RESPONSE") or r in ("user", "assistant", "human", "model"):
                    ts = d.get("timestamp") or d.get("created_at")
                    if ts:
                        if isinstance(ts, (int, float)):
                            return ts / 1000.0 if ts > 1e11 else float(ts)
                        return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
            except Exception:
                pass
        return os.path.getmtime(filepath)
    except Exception:
        return 0.0


def extract_duck_turns(lines: List[str], full: bool = False) -> List[Dict[str, str]]:
    """
    Duck-typing turn extractor supporting multiple schemas across Claude, AGY,
    Codex, Cursor, Grok, Command Code, and OMP.
    """
    turns: List[Dict[str, str]] = []
    curr = {"user": "", "assistant": ""}
    user_tags = {"user", "human", "usermessage", "user_input", "user_prompt"}
    asst_tags = {"assistant", "agent", "model", "agentmessage", "planner_response"}

    def extract_text(val: Any) -> str:
        if not val:
            return ""
        if isinstance(val, str):
            return val
        if isinstance(val, list):
            res = []
            for item in val:
                if isinstance(item, str):
                    res.append(item)
                elif isinstance(item, dict):
                    if item.get("type") == "text" and item.get("text"):
                        res.append(item["text"])
                    elif "text" in item and isinstance(item["text"], str):
                        res.append(item["text"])
                    elif "content" in item:
                        res.append(extract_text(item["content"]))
            return "\n".join(res)
        if isinstance(val, dict):
            return val.get("text") or extract_text(val.get("content")) or ""
        return str(val)

    for l in reversed(lines):
        if not l.strip():
            continue
        try:
            d = json.loads(l)
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("isMeta") or d.get("isCompactSummary"):
            continue

        msg = d.get("message") if isinstance(d.get("message"), dict) else d
        role = (msg.get("role") or d.get("type") or msg.get("type") or "").lower()
        raw_c = msg.get("content") or d.get("content") or msg.get("text") or d.get("text")

        # Claude tool results are stored with role="user" but contain tool_result items
        if isinstance(raw_c, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in raw_c):
            continue

        text = extract_text(raw_c)
        if text.lstrip().startswith(("<command-", "<local-command-")):
            continue

        text = re.sub(r"<(ADDITIONAL_METADATA|system-reminder)>.*?</\1>", "", text, flags=re.DOTALL)
        text = re.sub(r"\s*📒 Possibly-relevant notes.*", "", text, flags=re.DOTALL)
        text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|</?[a-zA-Z0-9_-]+>", "", text).strip()

        if role in asst_tags and not curr["assistant"]:
            if text:
                curr["assistant"] = text
        elif role in user_tags:
            if text:
                curr["user"] = text
                if curr["assistant"] or curr["user"]:
                    turns.append(curr)
                    curr = {"user": "", "assistant": ""}
                    if len(turns) >= 4 and not full:
                        break

    if curr["assistant"]:
        turns.append(curr)

    turns.reverse()
    return turns


class BaseAgentAdapter:
    """Base class for agent detection, status tracking, and preview rendering."""

    name: str = "base"
    display_name: str = "Base"
    color: str = C_DIM

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        """Return True if this adapter handles the given pane."""
        raise NotImplementedError

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        """
        Extract list view status for this pane.
        Returns:
            (harness_label, harness_color, rank, mtime, title, is_running)
        """
        raise NotImplementedError

    def get_preview(
        self,
        pane: PaneContext,
        full_desc: str,
        desc_pids: List[str],
        full_history: bool,
        preview_width: int
    ) -> Optional[PreviewResult]:
        """
        Extract preview content for this pane.
        Returns PreviewResult or None if unhandled.
        """
        raise NotImplementedError
