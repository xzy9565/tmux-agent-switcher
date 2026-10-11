"""Cursor agent adapter."""

import glob
import json
import os
import re
import shutil
import subprocess
import time
from typing import Dict, List, Optional, Tuple, Any
from ..core.types import PaneContext, PreviewResult
from ..core.config import C_CURSOR
from ..formatters.bubbles import format_turns
from .base import BaseAgentAdapter, read_tail_lines, extract_duck_turns


class CursorAdapter(BaseAgentAdapter):
    name = "cursor"
    display_name = "⚡ Cursor"
    color = C_CURSOR

    def detect(self, pane: PaneContext, full_desc: str, heads: str) -> bool:
        return "cursor-agent" in heads or "cursor-agent" in pane.current_command or "cursor-agent" in full_desc

    def get_status(
        self,
        pane: PaneContext,
        full_desc: str,
        heads: str,
        shared_state: Dict[str, Any]
    ) -> Tuple[str, str, int, float, str, bool]:
        t_files = glob.glob(os.path.expanduser("~/.cursor/projects/*/agent-transcripts/*/*.jsonl"))
        w_files = glob.glob(os.path.expanduser("~/.cursor/projects/**/worker.log"), recursive=True)
        mtime = max((os.path.getmtime(f) for f in (t_files + w_files)), default=0.0)

        # Prefer project-specific mtime if pane.cwd matches a known project
        proj_transcripts: List[str] = []
        if pane.cwd:
            encoded = pane.cwd.strip("/").replace("/", "-")
            proj_transcripts = glob.glob(os.path.expanduser(f"~/.cursor/projects/{encoded}/agent-transcripts/*/*.jsonl"))
            proj_logs = glob.glob(os.path.expanduser(f"~/.cursor/projects/{encoded}/**/worker.log"), recursive=True)
            if proj_transcripts or proj_logs:
                mtime = max((os.path.getmtime(f) for f in (proj_transcripts + proj_logs)), default=0.0)

        raw_title = pane.title or ""
        disp_t = re.sub(r"^[✳*★☆✦✧⠀-⣿\s]+", "", raw_title).strip()

        # Detect Cursor lifecycle state from OSC terminal title
        # Cursor CLI broadcasts: "[Chat] - ⏳ Working ...", "[Chat] - 🧭 Planning",
        #                        "[Chat] - ❓ Waiting for you", "[Chat] - ✅ Ready"
        is_running = bool(re.search(
            r"(?:Working|Planning|Running shell|Moving to cloud|Loading conversation|Reviewing changes|Reconnecting)",
            raw_title,
            re.IGNORECASE
        ))
        is_input = bool(re.search(r"(?:Waiting for you|Waiting for confirmation)", raw_title, re.IGNORECASE))

        # If not already flagged running by title, inspect terminal buffer & transcripts
        # to catch active execution (Thinking / ctrl+c to stop) or interactive choice questions
        if not is_running:
            screen = ""
            try:
                screen = subprocess.run(
                    ["tmux", "capture-pane", "-pJ", "-S", "-25", "-t", pane.pane_id],
                    capture_output=True,
                    text=True,
                    timeout=0.25
                ).stdout or ""
            except Exception:
                pass

            if screen:
                # 0. Active execution / thinking state probe on screen (overrides delayed OSC title)
                running_on_screen = bool(re.search(
                    r"(?:(?:[⠀-⣿⠋-]|\b)\s*Thinking\b|ctrl\+c\s+to\s+stop|Esc\s+to\s+cancel|\bGenerating\b|\bExecuting\s+command\b)",
                    screen,
                    re.IGNORECASE
                ))
                if running_on_screen:
                    is_running = True
                    is_input = False
                elif not is_input:
                    # 1. Interactive choice / question box (Ink / React CLI UI)
                    # Matches navigation footer: "Space select · Enter next/submit · Esc to skip" or "Enter select · Esc skip"
                    question_ui = bool(re.search(
                        r"(?:Space select|Enter next/submit|Esc to skip|Enter select · Esc skip)",
                        screen,
                        re.IGNORECASE
                    ))
                    question_body = bool(
                        re.search(r"Question \d+ of \d+", screen, re.IGNORECASE) and
                        re.search(r"›\s*\[[ x]\]", screen)
                    )
                    # 2. Tool / command execution approval prompt
                    approval_prompt = bool(re.search(
                        r"(?:Allow this command|Do you want to run|Allow once|Always allow|Allow \[y/n\]|Run command\?|Approve execution)",
                        screen,
                        re.IGNORECASE
                    ))

                    if question_ui or question_body or approval_prompt:
                        is_input = True
                    else:
                        # 3. Check active transcript if short ID found on screen or via project
                        target_transcripts = proj_transcripts
                        if not target_transcripts:
                            short_ids = set(re.findall(r"(?:·|›)\s*([0-9a-f]{8})\s*(?:·|›)", screen))
                            if short_ids:
                                target_transcripts = [tf for tf in t_files if os.path.basename(tf)[:8] in short_ids]

                        if target_transcripts:
                            latest_tf = max(target_transcripts, key=os.path.getmtime)
                            last_lines = read_tail_lines(latest_tf, max_bytes=4096, full=False)
                            if last_lines:
                                last_line = last_lines[-1]
                                if '"AskQuestion"' in last_line and '"turn_ended"' not in last_line:
                                    is_input = True

        # Strip trailing status indicator from display title
        clean_t = re.sub(
            r"\s*-\s*(?:[⏳🧭⌨️📝🔄📋❓🔐✅📤📂]\s*)?(?:Working.*|Planning|Running shell.*|Reviewing.*|Reconnecting|Queued|Waiting for.*|Ready|Moving to cloud|Loading conversation)\s*$",
            "",
            disp_t
        ).strip()
        disp_t = clean_t if clean_t else disp_t

        rank = 2
        running = False
        if is_running:
            rank = 1
            running = True
            mtime = max(mtime, time.time())
        elif is_input:
            rank = 0

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
        clean_t = re.sub(
            r"\s*-\s*(?:[⏳🧭⌨️📝🔄📋❓🔐✅📤📂]\s*)?(?:Working.*|Planning|Running shell.*|Reviewing.*|Reconnecting|Queued|Waiting for.*|Ready|Moving to cloud|Loading conversation)\s*$",
            "",
            disp_t
        ).strip()
        disp_t = clean_t if clean_t else disp_t
        try:
            pane_pids = {str(pane.pid), *desc_pids}
            cursor_pids = []
            ps_out = subprocess.run(["ps", "-eo", "pid,ppid,command"], capture_output=True).stdout.decode().splitlines()
            for line in ps_out[1:]:
                proc = line.strip().split(None, 2)
                if len(proc) == 3 and proc[0] in pane_pids and re.search(r"(?:^|[ /])cursor-agent(?:\s|$)", proc[2]):
                    cursor_pids.append(proc[0])

            session_ids = set()
            lsof = shutil.which("lsof")
            if cursor_pids and lsof:
                opened = subprocess.run(
                    [lsof, "-a", "-p", ",".join(cursor_pids), "-Fn"],
                    capture_output=True,
                    timeout=1
                ).stdout.decode(errors="replace")
                session_ids = set(re.findall(r"/\.cursor/chats/[^/]+/([0-9a-f-]{36})/store\.db(?:$|[-\n])", opened, re.MULTILINE))

            sid = next(iter(session_ids)) if len(session_ids) == 1 else ""
            if not sid:
                try:
                    screen = subprocess.check_output(
                        ["tmux", "capture-pane", "-pJ", "-S", "-20", "-t", pane.pane_id]
                    ).decode()
                    short_ids = set(re.findall(r"(?:·|›)\s*([0-9a-f]{8})\s*(?:·|›)", screen))
                    candidates = session_ids or {
                        os.path.basename(p)[:-6]
                        for p in glob.glob(os.path.expanduser("~/.cursor/projects/*/agent-transcripts/*/*.jsonl"))
                    }
                    matched = {s for s in candidates if s[:8] in short_ids}
                    if len(matched) == 1:
                        sid = next(iter(matched))
                except Exception:
                    pass

            if not sid and pane.cwd:
                encoded = pane.cwd.strip("/").replace("/", "-")
                proj_matches = glob.glob(os.path.expanduser(f"~/.cursor/projects/{encoded}/agent-transcripts/*/*.jsonl"))
                if proj_matches:
                    latest_proj = max(proj_matches, key=os.path.getmtime)
                    sid = os.path.basename(latest_proj)[:-6]

            matches = glob.glob(os.path.expanduser(f"~/.cursor/projects/*/agent-transcripts/{sid}/{sid}.jsonl")) if sid else []
            if matches:
                transcript = max(matches, key=os.path.getmtime)
                records = []
                for line in read_tail_lines(transcript, full=full_history):
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    role = record.get("role")
                    if role not in ("user", "assistant"):
                        continue
                    parts = record.get("message", {}).get("content", [])
                    text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
                    if role == "user":
                        query = re.search(r"<user_query>(.*?)</user_query>", text, re.DOTALL)
                        text = query.group(1).strip() if query else re.sub(r"<timestamp>.*?</timestamp>", "", text, flags=re.DOTALL)
                    records.append(json.dumps({"role": role, "content": text}))

                turns = extract_duck_turns(records, full=full_history)
                if turns:
                    is_live = False
                    try:
                        is_live = time.time() - os.path.getmtime(transcript) < 120
                    except Exception:
                        pass
                    content = format_turns(turns, live=is_live, full_history=full_history, preview_width=preview_width)
                    return PreviewResult(
                        source_label=f"{self.display_name} · {disp_t} · {sid[:8]}",
                        content=content
                    )
        except Exception:
            pass
        return None
