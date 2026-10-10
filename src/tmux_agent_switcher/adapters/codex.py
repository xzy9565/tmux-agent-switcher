"""OpenAI Codex adapter."""

import json
import os
import re
import sqlite3
import time
from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_CODEX
from ..formatters.bubbles import format_turns
from .base import BaseAgentAdapter, extract_duck_turns


class CodexAdapter(BaseAgentAdapter):
    name = "codex"
    display_name = "⚡ Codex"
    color = C_CODEX

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        return "codex" in heads or "codex" in full_desc or "@openai/codex" in full_desc

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        codex_threads = shared_state.get("codex_threads", {})
        codex_cwd = shared_state.get("codex_cwd", {})

        spinning = bool(re.match(r"\s*[⠀-⣿]", pane.title))
        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).strip()
        clean_t = disp_t.split("|")[0].strip("[]* ")

        mtime = 0.0
        for k, (tid, u_time) in codex_threads.items():
            if k == clean_t or k in clean_t or clean_t in k:
                mtime = max(mtime, u_time)

        mtime = mtime or codex_cwd.get(pane.cwd, 0.0)

        status = "💤 idle"
        rank = 2
        running = False
        if spinning:
            status = "⚡ run"
            rank = 1
            running = True

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
        clean_t = disp_t.split("|")[0].strip("[]* ")

        s_db = os.path.expanduser("~/.codex/state_5.sqlite")
        h_db = os.path.expanduser("~/.codex/thread_history_1.sqlite")

        if os.path.exists(s_db) and os.path.exists(h_db):
            try:
                conn_s = sqlite3.connect(f"file:{s_db}?mode=ro", uri=True)
                row = conn_s.execute(
                    'SELECT id, name FROM threads WHERE name = ? OR name LIKE ? OR ? LIKE "%" || name || "%" ORDER BY updated_at_ms DESC LIMIT 1',
                    (clean_t, f"%{clean_t}%", clean_t)
                ).fetchone()
                conn_s.close()

                if row:
                    tid, _ = row
                    conn_h = sqlite3.connect(f"file:{h_db}?mode=ro", uri=True)
                    items = conn_h.execute(
                        'SELECT item_type, item_json FROM thread_items WHERE thread_id = ? AND item_type IN ("userMessage", "agentMessage") ORDER BY created_at_ms DESC LIMIT ?',
                        (tid, -1 if full_history else 15)
                    ).fetchall()
                    conn_h.close()

                    raw_records = []
                    for itype, ijson in items:
                        try:
                            d = json.loads(ijson)
                        except Exception:
                            continue
                        r = "assistant" if itype == "agentMessage" else "user"
                        raw_records.append(json.dumps({"role": r, "content": d.get("content") or d.get("text")}))

                    turns = extract_duck_turns(raw_records, full=full_history)
                    if turns:
                        content = format_turns(turns, live=True, full_history=full_history, preview_width=preview_width)
                        return PreviewResult(
                            source_label=f"{self.display_name} · {disp_t}",
                            content=content
                        )
            except Exception:
                pass
        return None
