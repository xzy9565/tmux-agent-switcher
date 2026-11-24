"""OpenCode adapter."""

import json
import os
import re
import sqlite3
import time
from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_OPENCODE
from ..formatters.bubbles import format_turns
from .base import BaseAgentAdapter, extract_duck_turns, live_tag


class OpenCodeAdapter(BaseAgentAdapter):
    name = "opencode"
    display_name = "🟢 OpenCode"
    color = C_OPENCODE

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        return "opencode" in heads or "opencode" in pane.current_command or live_tag(pane, pane.oc_tag)

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        oc_sessions = shared_state.get("oc_sessions", [])

        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).removeprefix("OC | ").strip()
        mtime = 0.0
        rank = 2
        running = False

        cands = [s for s in oc_sessions if s[0] == pane.oc_tag or (disp_t and disp_t in s[1])]
        if cands:
            # The pane's own session tag beats a title several sessions may share
            _, _, last_ms, busy = max(cands, key=lambda s: (s[0] == pane.oc_tag, s[1] == disp_t, s[2] or 0))
            mtime = (last_ms or 0) / 1000.0
            running = bool(busy)
            rank = 1 if running else 2

        return self.display_name, self.color, rank, mtime, disp_t, running

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
        db_path = os.path.expanduser("~/.local/share/opencode/opencode.db")

        if os.path.exists(db_path):
            # ponytail: session_v2 schema only; the legacy session/message/part tables stopped updating when OpenCode moved off them
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    row = conn.execute(
                        "SELECT id, title, time_updated FROM session_v2 WHERE parent_id IS NULL AND "
                        "(id = ? OR title LIKE ? OR ? LIKE '%' || title || '%') "
                        "ORDER BY id = ? DESC, title = ? DESC, time_updated DESC LIMIT 1",
                        (pane.oc_tag, f"%{clean_t}%", clean_t, pane.oc_tag, disp_t)
                    ).fetchone()
                    if row:
                        sid, stitle, t_upd = row
                        # Newest messages first via the (session_id, type, seq) index; only reply text, never reasoning or tool output
                        rows = conn.execute(
                            "SELECT type, CASE type WHEN 'user' THEN json_extract(data, '$.text') ELSE "
                            "(SELECT group_concat(json_extract(value, '$.text'), char(10)) FROM json_each(data, '$.content') "
                            "WHERE json_extract(value, '$.type') = 'text') END "
                            "FROM session_message WHERE session_id = ? AND type IN ('user', 'assistant') "
                            "ORDER BY seq DESC LIMIT ?",
                            (sid, -1 if full_history else 300)
                        ).fetchall()
                        recs = [json.dumps({"role": t, "content": txt}) for t, txt in reversed(rows) if txt]
                        turns = extract_duck_turns(recs, full=full_history)
                        if turns:
                            is_live = bool(t_upd) and (time.time() - t_upd / 1000.0) < 120
                            content = format_turns(turns, live=is_live, full_history=full_history, preview_width=preview_width)
                            return PreviewResult(
                                source_label=f"{self.display_name} · {stitle or disp_t}",
                                content=content
                            )
                finally:
                    conn.close()
            except Exception:
                pass
        return None
