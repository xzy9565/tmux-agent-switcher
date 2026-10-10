"""Google Antigravity (AGY) / Gemini adapter."""

import os
import re
import time
from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_AGY
from ..formatters.bubbles import format_turns
from .base import BaseAgentAdapter, read_tail_lines, extract_duck_turns, get_real_dialogue_time


class AntigravityAdapter(BaseAgentAdapter):
    name = "antigravity"
    display_name = "🤖 AGY"
    color = C_AGY

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        return "agy" in heads or "agy" in pane.current_command or bool(pane.agy_tag)

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        agy_titles = shared_state.get("agy_titles", {})
        m = re.search(r"--conversation[=\s]+([0-9a-fA-F\-]{36})", full_desc)
        cid = pane.agy_tag or (m.group(1) if m else "")
        mtime = 0.0
        rank = 2
        status = "💤 idle"
        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).strip()
        running = False

        if cid:
            tpath = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{cid}/.system_generated/logs/transcript.jsonl")
            if os.path.exists(tpath):
                mtime = get_real_dialogue_time(tpath)
            if cid in agy_titles:
                t, idle = agy_titles[cid]
                disp_t = t
                if idle:
                    status = "⚡ run"
                    rank = 1
                    running = True
                else:
                    status = "💤 idle"

        if disp_t.startswith("Ga=") or not disp_t.strip():
            disp_t = "[AGY Session]"

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
        m = re.search(r"--conversation[=\s]+([0-9a-fA-F\-]{36})", full_desc)
        cid = pane.agy_tag or (m.group(1) if m else "")

        if not cid:
            agy_map = os.path.expanduser("~/.tmux/resurrect/agy_session_map.txt")
            if os.path.exists(agy_map):
                try:
                    with open(agy_map) as f:
                        for line in f:
                            parts = line.strip().split("\t")
                            if parts and parts[0] == pane.pane_id and len(parts) >= 2:
                                cid = parts[1]
                                break
                except Exception:
                    pass

        if cid:
            tpath = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{cid}/.system_generated/logs/transcript.jsonl")
            if os.path.exists(tpath):
                lines = read_tail_lines(tpath, full=full_history)
                turns = extract_duck_turns(lines, full=full_history)
                if turns:
                    is_live = False
                    try:
                        is_live = time.time() - os.path.getmtime(tpath) < 120
                    except Exception:
                        pass
                    content = format_turns(turns, live=is_live, full_history=full_history, preview_width=preview_width)
                    return PreviewResult(
                        source_label=f"{self.display_name} · {disp_t}",
                        content=content
                    )
        return None
