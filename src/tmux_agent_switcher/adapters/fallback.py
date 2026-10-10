"""Tier 1 Universal Fallback TUI Adapter & Shell handler."""

import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_DIM, SHELLS
from ..formatters.denoiser import denoise_terminal_text
from ..formatters.markdown import render_md
from .base import BaseAgentAdapter


class FallbackTuiAdapter(BaseAgentAdapter):
    name = "fallback"
    display_name = "💻 Shell"
    color = C_DIM

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        return True  # Fallback matches everything not caught by specialized adapters

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        host = shared_state.get("host", os.uname().nodename.split(".")[0])
        home = shared_state.get("home", os.path.expanduser("~"))

        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).strip()
        if disp_t in ("", host) + SHELLS:
            where = re.sub("^" + re.escape(home) + "(?=/|$)", "~", pane.cwd)
            where = where if len(where) <= 50 else "…" + where[-49:]
            disp_t = where if pane.current_command in SHELLS else f"{pane.current_command} · {where}"

        return self.display_name, self.color, 4, 0.0, disp_t, False

    def get_preview(
        self,
        pane: PaneContext,
        full_desc: str,
        desc_pids: List[str],
        full_history: bool,
        preview_width: int
    ) -> Optional[PreviewResult]:
        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).removeprefix("OC | ").strip()
        clean_t = disp_t.split("|")[0].strip("[]* ")
        geom = f"{pane.width}x{pane.height}" if pane.width and pane.height else ""

        try:
            scrollback = "-" if full_history else "-200"
            raw = subprocess.check_output([
                "tmux", "capture-pane", "-epJ", "-S", scrollback, "-t", pane.pane_id
            ]).decode("utf-8", errors="ignore")

            cleaned = denoise_terminal_text(raw)
            if re.search(r"```|^#{1,3}\s+|^\s*[\*\-\•]\s+", cleaned, re.MULTILINE):
                content = render_md(cleaned, preview_width)
            else:
                content = cleaned

            source_label = f"{pane.current_command} · {clean_t[:35]}"
            if geom:
                source_label += f" [{geom}]"

            return PreviewResult(
                source_label=source_label,
                content=content
            )
        except Exception as e:
            return PreviewResult(
                source_label=pane.pane_id,
                content=f"Error capturing pane: {e}"
            )
