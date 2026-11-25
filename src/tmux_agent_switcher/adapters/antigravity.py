"""Google Antigravity (AGY) / Gemini adapter."""

import collections
import os
import re
import time
from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_AGY
from ..formatters.bubbles import format_turns
from .base import BaseAgentAdapter, read_tail_lines, extract_duck_turns, get_real_dialogue_time, live_tag, FULL_HISTORY_MAX_BYTES

# agy logs each conversation twice, line for line: transcript.jsonl cuts the middle out of long fields ("<truncated N bytes>",
# which broke bold and links in previews), transcript_full.jsonl keeps them. Its uncut tool output outweighs the turns many
# times over, so a byte window of it holds few turns: stream it and keep only the turn records. Some lines space the colon.
TURN_RECORD = re.compile(rb'"type": ?"(?:USER_INPUT|PLANNER_RESPONSE)"')


def read_turn_lines(path: str) -> List[str]:
    """The turn records of agy's uncut log, at most the last FULL_HISTORY_MAX_BYTES of them."""
    kept: collections.deque = collections.deque()
    size = 0
    try:
        with open(path, "rb") as f:
            for line in f:
                if TURN_RECORD.search(line, 0, 200):  # type is the third key, after step_index and source
                    kept.append(line)
                    size += len(line)
                    while size > FULL_HISTORY_MAX_BYTES:
                        size -= len(kept.popleft())
    except OSError:
        return []
    return [l.decode("utf-8", errors="ignore") for l in kept]


class AntigravityAdapter(BaseAgentAdapter):
    name = "antigravity"
    display_name = "🤖 AGY"
    color = C_AGY

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        return "agy" in heads or "agy" in pane.current_command or live_tag(pane, pane.agy_tag)

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        agy_titles = shared_state.get("agy_titles", {})
        m = re.search(r"--conversation[=\s]+([0-9a-fA-F\-]{36})", full_desc)
        cid = pane.agy_tag or (m.group(1) if m else "")
        mtime = 0.0
        rank = 2
        status = "💤 idle"
        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).strip()
        running = False

        if cid:
            # Timestamps are never cut, and the cut log's short lines fit more records in the window
            tpath = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{cid}/.system_generated/logs/transcript.jsonl")
            if os.path.exists(tpath):
                mtime = get_real_dialogue_time(tpath)
            if cid in agy_titles:
                t, idle = agy_titles[cid]
                disp_t = t
                if idle:
                    status = "⚡ run"
                    rank = 1
                    running = True
                else:
                    status = "💤 idle"

        if disp_t.startswith("Ga=") or not disp_t.strip():
            disp_t = "[AGY Session]"

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
        m = re.search(r"--conversation[=\s]+([0-9a-fA-F\-]{36})", full_desc)
        cid = pane.agy_tag or (m.group(1) if m else "")

        if not cid:
            agy_map = os.path.expanduser("~/.tmux/resurrect/agy_session_map.txt")
            if os.path.exists(agy_map):
                try:
                    with open(agy_map) as f:
                        for line in f:
                            parts = line.strip().split("\t")
                            if parts and parts[0] == pane.pane_id and len(parts) >= 2:
                                cid = parts[1]
                                break
                except Exception:
                    pass

        if cid:
            logs = os.path.expanduser(f"~/.gemini/antigravity-cli/brain/{cid}/.system_generated/logs")
            tpath = f"{logs}/transcript_full.jsonl"
            lines = read_turn_lines(tpath)
            if not lines:  # no uncut log: the cut one still holds every turn
                tpath = f"{logs}/transcript.jsonl"
                lines = read_tail_lines(tpath, full=full_history)
            turns = extract_duck_turns(lines, full=full_history)
            if turns:
                is_live = False
                try:
                    is_live = time.time() - os.path.getmtime(tpath) < 120
                except Exception:
                    pass
                content = format_turns(turns, live=is_live, full_history=full_history, preview_width=preview_width)
                return PreviewResult(
                    source_label=f"{self.display_name} · {disp_t}",
                    content=content
                )
        return None
