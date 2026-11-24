"""xAI Grok adapter."""

import glob
import json
import os
import re
import urllib.parse
from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_GROK
from ..formatters.bubbles import format_turns
from .base import BaseAgentAdapter, read_tail_lines, extract_duck_turns, get_real_dialogue_time, pane_pids


def _session_history(pane: PaneContext, pids: List[str]) -> str:
    """This pane's chat_history.jsonl: the session its grok process registered, else the newest in the pane's directory."""
    root = os.path.expanduser("~/.grok/sessions")
    try:
        with open(os.path.expanduser("~/.grok/active_sessions.json")) as f:
            for s in json.load(f):
                if str(s.get("pid")) in pids and s.get("session_id") and s.get("cwd"):
                    return os.path.join(root, urllib.parse.quote(s["cwd"], safe=""), s["session_id"], "chat_history.jsonl")
    except Exception:
        pass
    try:
        files = glob.glob(os.path.join(root, urllib.parse.quote(pane.cwd, safe=""), "*", "chat_history.jsonl"))
        return max(files, key=os.path.getmtime, default="")
    except OSError:
        return ""


class GrokAdapter(BaseAgentAdapter):
    name = "grok"
    display_name = "⚡ Grok"
    color = C_GROK

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        return "grok" in heads or "grok" in pane.current_command

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        hf = _session_history(pane, pane_pids(pane, shared_state))
        mtime = get_real_dialogue_time(hf) if hf else 0.0
        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).strip()
        return self.display_name, self.color, 2, mtime, disp_t, False

    def get_preview(
        self,
        pane: PaneContext,
        full_desc: str,
        desc_pids: List[str],
        full_history: bool,
        preview_width: int
    ) -> Optional[PreviewResult]:
        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).strip()
        hf = _session_history(pane, desc_pids)
        if hf and os.path.exists(hf):
            lines = read_tail_lines(hf, full=full_history)
            turns = extract_duck_turns(lines, full=full_history)
            if turns:
                content = format_turns(turns, live=True, full_history=full_history, preview_width=preview_width)
                return PreviewResult(
                    source_label=f"{self.display_name} · {disp_t}",
                    content=content
                )
        return None
