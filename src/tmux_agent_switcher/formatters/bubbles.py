"""Conversational turn bubble formatter for preview panel."""

import os
from typing import List, Dict, Any, Optional
from ..core.config import (
    C_RESET, C_DIVIDER, C_MUTED,
    C_USER_BG, C_USER_TXT, C_ACTIVE_BG, C_ACTIVE_TXT,
    C_BAR_ACTIVE, C_BAR_USER
)
from .markdown import render_md, wrap_text


def format_turns(
    turns: List[Dict[str, str]],
    live: bool = True,
    full_history: Optional[bool] = None,
    preview_width: int = 0
) -> str:
    """
    Format conversational turns into styled bubbles.
    - User turns: styled pills with rounded left-rail box.
    - Assistant turns: markdown rendered with tables and syntax highlighting.
    - Live indicator: indicates actively running turn if unanswered.
    """
    if full_history is None:
        full_history = "full history" in os.environ.get("FZF_PREVIEW_LABEL", "")

    effective_w = preview_width or int(os.environ.get("FZF_PREVIEW_COLUMNS") or 0)
    parts = []
    total = len(turns)

    for i, t in enumerate(turns):
        u, a = t.get("user", "").strip(), t.get("assistant", "").strip()
        is_latest = (i == total - 1)
        turn_num = i + 1
        turn_badge = f"{C_DIVIDER}─── ✦ Turn {turn_num} of {total} ──────────────────────────────────────{C_RESET}"
        turn_parts = [turn_badge]

        if u:
            is_active = is_latest and not a and live
            pill = f"{C_ACTIVE_BG} ⚡ YOU (Active) {C_RESET}" if is_active else f"{C_USER_BG} 👤 YOU {C_RESET}"
            txt_col = C_ACTIVE_TXT if is_active else C_USER_TXT
            bar_col = C_BAR_ACTIVE if is_active else C_BAR_USER

            u_lines = [w for ul in u.split("\n") for w in wrap_text(ul, effective_w, 4)]
            if len(u_lines) > 12 and not full_history:
                u_lines = u_lines[:12] + [f"{C_MUTED}… +{len(u_lines) - 12} more lines{C_RESET}"]

            user_box = [f"{pill}"]
            for ul in u_lines:
                user_box.append(f"  {bar_col}│{C_RESET} {txt_col}{ul}{C_RESET}")
            user_box.append(f"  {bar_col}└──{C_RESET}")
            turn_parts.append("\n".join(user_box))

        if a:
            turn_parts.append(render_md(a, effective_w))
        elif is_latest and u and live:
            turn_parts.append(f"\n  \033[1;33m⚡ Agent is actively running...\033[0m\n")

        parts.append("\n\n".join(turn_parts))

    if full_history:
        parts.append(f"{C_DIVIDER}── 📜 full history · {total} turns · ctrl-o: recent only ──{C_RESET}")
    elif total >= 4:
        parts.append(f"{C_DIVIDER}── last {total} turns · ctrl-o: full history ──{C_RESET}")

    return "\n\n".join(parts)
