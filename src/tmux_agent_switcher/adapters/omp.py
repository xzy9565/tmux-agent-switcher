"""Oh My Pi (OMP) adapter."""

import glob
import json
import os
import re
import time
from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_OMP
from ..formatters.bubbles import format_turns
from .base import BaseAgentAdapter, read_tail_lines, extract_duck_turns, get_real_dialogue_time

OMP_RE = re.compile(r"(^|[\s/])omp(\s|$)")


def load_omp_sessions() -> List[Tuple[str, str, str]]:
    """Scan ~/.omp/agent/sessions to index (session_id, filepath, title)."""
    idx = []
    for p in glob.glob(os.path.expanduser("~/.omp/agent/sessions/*/*.jsonl")):
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                first = f.readline()
                title = json.loads(first).get("title") or ""
                sid = p.rsplit("_", 1)[-1][:-6]
                idx.append((sid, p, title))
        except Exception:
            pass
    return idx


class OmpAdapter(BaseAgentAdapter):
    name = "omp"
    display_name = "🟣 OMP"
    color = C_OMP

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        return bool(OMP_RE.search(heads)) or pane.current_command == "omp" or bool(OMP_RE.search(full_desc))

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        omp_idx = shared_state.setdefault("omp_idx", load_omp_sessions())
        cmd_of = shared_state.get("cmd_of", {})

        m = re.match(r"π (\S)\s*(.*)", pane.title)
        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", pane.title).strip()
        state, t = m.groups() if m else (">", disp_t)

        status = "💤 idle"
        rank = 2
        running = False
        if state == "!":
            status = "🔔 input"
            rank = 0
        elif state != ">":
            status = "⚡ run"
            rank = 1
            running = True

        ids = {pane.omp_tag} | set(re.findall(r"(?:-r|--resume|resume)\s+([0-9a-f-]{36})", cmd_of.get(pane.pid, "") + " " + full_desc))
        cands = [s for s in omp_idx if s[2] == t or s[0] in ids]
        mtime = 0.0
        if cands:
            best_file = max(cands, key=lambda s: (s[2] == t, s[0] in ids, os.path.getmtime(s[1])))[1]
            mtime = get_real_dialogue_time(best_file)

        return self.display_name, self.color, rank, mtime, t, running

    def get_preview(
        self,
        pane: PaneContext,
        full_desc: str,
        desc_pids: List[str],
        full_history: bool,
        preview_width: int
    ) -> Optional[PreviewResult]:
        label = re.sub(r"^π \S\s*", "", pane.title)
        ids = {pane.omp_tag} | set(re.findall(r"(?:-r|--resume|resume)\s+([0-9a-f-]{36})", full_desc))
        best = None

        for p in glob.glob(os.path.expanduser("~/.omp/agent/sessions/*/*.jsonl")):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    st = json.loads(f.readline()).get("title") or ""
            except Exception:
                continue
            key = (st == label, p.rsplit("_", 1)[-1][:-6] in ids, os.path.getmtime(p))
            if (key[0] or key[1]) and (best is None or key > best[0]):
                best = (key, p, st)

        if best:
            turns = extract_duck_turns(read_tail_lines(best[1], full=full_history), full=full_history)
            if turns:
                is_live = False
                try:
                    is_live = time.time() - best[0][2] < 120
                except Exception:
                    pass
                content = format_turns(turns, live=is_live, full_history=full_history, preview_width=preview_width)
                return PreviewResult(
                    source_label=f"{self.display_name} · {best[2] or label}",
                    content=content
                )
        return None
