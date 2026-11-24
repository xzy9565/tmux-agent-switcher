"""OpenAI Codex adapter."""

import glob
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


# Threads a pane can be showing: interactive roots, not subagents ({"subagent": ...}), `codex exec` runs, or archived ones
CODEX_ROOTS = "COALESCE(archived, 0) = 0 AND COALESCE(source, '') NOT LIKE '{%' AND COALESCE(source, '') <> 'exec'"


def codex_db(kind: str) -> str:
    """Newest versioned Codex database of a kind (state_5.sqlite today), so a version bump can't silently strand us."""
    root = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    found = [(int(m.group(1)), p) for p in glob.glob(os.path.join(root, f"{kind}_*.sqlite"))
             if (m := re.search(rf"{kind}_(\d+)\.sqlite$", p))]
    return max(found)[1] if found else ""


class CodexAdapter(BaseAgentAdapter):
    name = "codex"
    display_name = "⚡ Codex"
    color = C_CODEX

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        return "codex" in heads

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
        # Codex titles a pane "<thread name> | <dir>": that exact name, else the newest thread in the pane's directory
        mtime = codex_threads.get(disp_t.split(" | ")[0].strip(), (None, 0.0))[1] or codex_cwd.get(pane.cwd, 0.0)

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
        name = disp_t.split(" | ")[0].strip()

        s_db, h_db = codex_db("state"), codex_db("thread_history")

        if s_db and h_db:
            try:
                conn_s = sqlite3.connect(f"file:{s_db}?mode=ro", uri=True)
                row = conn_s.execute(
                    "SELECT id, name FROM threads WHERE " + CODEX_ROOTS + " AND (name = ? OR cwd = ?) "
                    "ORDER BY name = ? DESC, updated_at_ms DESC LIMIT 1",
                    (name, pane.cwd, name)
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
                    for itype, ijson in reversed(items):  # query is newest-first; extract_duck_turns reads oldest-first
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
