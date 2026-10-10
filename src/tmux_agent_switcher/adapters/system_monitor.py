"""System Monitor adapter (token-monitor, btop, htop, mactop, top)."""

from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_MONITOR
from .base import BaseAgentAdapter

MONITORS = ("mactop", "token-monitor", "btop", "htop")


class SystemMonitorAdapter(BaseAgentAdapter):
    name = "system-monitor"
    display_name = "📊 Monitor"
    color = C_MONITOR

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        cmd = pane.current_command
        return cmd in ("top", *MONITORS) or any(m in full_desc for m in MONITORS)

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        cmd = pane.current_command
        harness = "📊 " + cmd[:8].capitalize()
        title = f"System Monitor ({cmd})"
        return harness, self.color, 3, 0.0, title, False

    def get_preview(
        self,
        pane: PaneContext,
        full_desc: str,
        desc_pids: List[str],
        full_history: bool,
        preview_width: int
    ) -> Optional[PreviewResult]:
        # Let FallbackTuiAdapter capture terminal buffer with de-noising
        return None
