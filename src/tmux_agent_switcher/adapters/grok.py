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
from .base import BaseAgentAdapter, read_tail_lines, extract_duck_turns, get_real_dialogue_time


class GrokAdapter(BaseAgentAdapter):
    name = "grok"
    display_name = "⚡ Grok"
    color = C_GROK

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        return "grok" in heads or "grok" in pane.current_command or "grok" in full_desc

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        g_files = glob.glob(os.path.expanduser("~/.grok/sessions/**/chat_history.jsonl"), recursive=True)
        mtime = max((get_real_dialogue_time(f) for f in g_files), default=0.0)
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
        grok_act = os.path.expanduser("~/.grok/active_sessions.json")
        sid, cwd = None, None

        if os.path.exists(grok_act):
            try:
                with open(grok_act) as f:
                    active = json.load(f)
                for s in active:
                    if str(s.get("pid")) in desc_pids:
                        sid, cwd = s.get("session_id"), s.get("cwd")
                        break
                if not sid and active:
                    sid, cwd = active[-1].get("session_id"), active[-1].get("cwd")
            except Exception:
                pass

        if sid and cwd:
            cwd_enc = urllib.parse.quote(cwd, safe="")
            hf = os.path.expanduser(f"~/.grok/sessions/{cwd_enc}/{sid}/chat_history.jsonl")
            if os.path.exists(hf):
                lines = read_tail_lines(hf, full=full_history)
                turns = extract_duck_turns(lines, full=full_history)
                if turns:
                    content = format_turns(turns, live=True, full_history=full_history, preview_width=preview_width)
                    return PreviewResult(
                        source_label=f"{self.display_name} · {disp_t}",
                        content=content
                    )
        return None
