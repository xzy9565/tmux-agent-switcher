"""
OSC 7501 Program Status Protocol Adapter (placeholder).
Specification: https://mitchellh.com/writing/program-status-osc7501

A program reports its state with colon-separated keys, msg base64-encoded:
    \033]7501;state=working:app=codex:msg=<base64>\033\\
state is idle, working, done, blocked or error; state=clear drops it.

tmux 3.8 discards the sequence. tmux master parses it into #{pane_program_state}, #{pane_program_name} and
#{pane_program_message}: once a release ships them, this adapter can read them with list-panes instead of scraping
agent stores. Codex CLI already emits it; to check another agent, run rg -a '7501;' on its native binary.
"""

from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_CYAN_BOLD
from .base import BaseAgentAdapter


class Osc7501Adapter(BaseAgentAdapter):
    name = "osc-7501"
    display_name = "⚡ OSC 7501"
    color = C_CYAN_BOLD

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        # Nothing to read until a tmux release parses OSC 7501 (see the module docstring)
        return False

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        return self.display_name, self.color, 2, 0.0, pane.title, False

    def get_preview(
        self,
        pane: PaneContext,
        full_desc: str,
        desc_pids: List[str],
        full_history: bool,
        preview_width: int
    ) -> Optional[PreviewResult]:
        return None
