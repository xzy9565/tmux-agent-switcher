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
from .base import BaseAgentAdapter, extract_duck_turns


class OpenCodeAdapter(BaseAgentAdapter):
    name = "opencode"
    display_name = "🟢 OpenCode"
    color = C_OPENCODE

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        return "opencode" in heads or "opencode" in pane.current_command or bool(pane.oc_tag)

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        oc_sessions = shared_state.get("oc_sessions", [])
        oc_db = shared_state.get("oc_db")

        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).removeprefix("OC | ").strip()
        mtime = 0.0
        rank = 2
        running = False

        cands = [s for s in oc_sessions if s[0] == pane.oc_tag or (disp_t and disp_t in (s[1] or ""))]
        if cands:
            sid, _, t_upd = max(cands, key=lambda s: (s[1] == disp_t, s[0] == pane.oc_tag, s[2] or 0))
            mtime = (t_upd or 0) / 1000.0
            if oc_db:
                try:
                    last = oc_db.execute(
                        "SELECT json_extract(data, '$.role'), json_extract(data, '$.time.completed') "
                        "FROM message WHERE session_id = ? ORDER BY time_created DESC LIMIT 1",
                        (sid,)
                    ).fetchone()
                    running = bool(last) and (last[0] == "user" or last[1] is None)
                    if running:
                        rank = 1
                except Exception:
                    pass

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
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                row = conn.execute(
                    'SELECT id, title, time_updated FROM session WHERE parent_id IS NULL AND '
                    '(id = ? OR title LIKE ? OR ? LIKE "%" || title || "%") '
                    'ORDER BY title = ? DESC, id = ? DESC, time_updated DESC LIMIT 1',
                    (pane.oc_tag, f"%{clean_t}%", clean_t, disp_t, pane.oc_tag)
                ).fetchone()

                if row:
                    sid, stitle, t_upd = row
                    # 300 part limit keeps query latency ~40ms even on huge sessions
                    limit = -1 if full_history else 300
                    recs = [
                        json.dumps({"role": r, "content": txt})
                        for r, txt in conn.execute(
                            "SELECT json_extract(m.data, '$.role'), json_extract(p.data, '$.text') "
                            "FROM part p JOIN message m ON m.id = p.message_id "
                            "WHERE p.id IN (SELECT id FROM part WHERE session_id = ? ORDER BY time_created DESC LIMIT ?) "
                            "AND json_extract(p.data, '$.type') = 'text' AND json_extract(p.data, '$.synthetic') IS NOT 1 "
                            "ORDER BY p.time_created",
                            (sid, limit)
                        ) if txt
                    ]
                    turns = extract_duck_turns(recs, full=full_history)
                    if turns:
                        is_live = False
                        if t_upd:
                            is_live = (time.time() - t_upd / 1000.0) < 120
                        content = format_turns(turns, live=is_live, full_history=full_history, preview_width=preview_width)
                        conn.close()
                        return PreviewResult(
                            source_label=f"{self.display_name} · {stitle}",
                            content=content
                        )
                conn.close()
            except Exception:
                pass
        return None
