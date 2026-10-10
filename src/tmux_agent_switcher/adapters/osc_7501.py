"""
OSC 7501 Semantic Agent Protocol Adapter (Forward-Looking Stub).

OSC 7501 is the emerging terminal multiplexer protocol for autonomous AI agents
to stream structured state machines directly over terminal escape sequences:
    \033]7501;state=running;agent=claude;sid=<uuid>;turn=3\033\\

This adapter establishes the standard protocol bridge, allowing future terminal agents
implementing OSC 7501 to bypass heuristic disk scraping (JSONL/SQLite) and register
zero-latency semantic lifecycle events natively.
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
        # Activated when tmux pane option @osc-7501-state or environmental signal exists
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
