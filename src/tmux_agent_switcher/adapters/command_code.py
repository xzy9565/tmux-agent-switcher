"""Command Code adapter."""

import glob
import os
import re
from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_CMD
from ..formatters.bubbles import format_turns
from .base import BaseAgentAdapter, read_tail_lines, extract_duck_turns, get_real_dialogue_time


class CommandCodeAdapter(BaseAgentAdapter):
    name = "command-code"
    display_name = "⚡ Cmd"
    color = C_CMD

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        return "command-code" in heads or bool(pane.cmd_code_sid) or "command-code" in full_desc

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        mtime = 0.0
        if pane.cmd_code_sid:
            c_files = glob.glob(os.path.expanduser(f"~/.commandcode/projects/**/{pane.cmd_code_sid}*.jsonl"), recursive=True)
            if c_files:
                mtime = get_real_dialogue_time(c_files[0])
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
        if pane.cmd_code_sid and not ("codex" in full_desc or "@openai/codex" in full_desc):
            c_files = glob.glob(os.path.expanduser(f"~/.commandcode/projects/**/{pane.cmd_code_sid}*.jsonl"), recursive=True)
            if c_files:
                lines = read_tail_lines(c_files[0], full=full_history)
                turns = extract_duck_turns(lines, full=full_history)
                if turns:
                    content = format_turns(turns, live=True, full_history=full_history, preview_width=preview_width)
                    return PreviewResult(
                        source_label=f"{self.display_name} · {disp_t}",
                        content=content
                    )
        return None
