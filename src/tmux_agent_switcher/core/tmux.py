"""Tmux interface and process tree discovery utilities."""

import collections
import os
import re
import subprocess
from typing import Dict, List, Tuple, Set, Optional
from .types import PaneContext

OMP_PATTERN = r"(^|[\s/])omp(\s|$)"
AGENTS_REGEX = re.compile(r"cursor-agent|codex|command-code|claude|grok|agy|opencode|" + OMP_PATTERN)


class ProcessTree:
    """Represents a snapshot of the operating system process tree."""

    def __init__(self, my_pid: Optional[str] = None):
        self.my_pid = my_pid or str(os.getpid())
        self.tree: Dict[str, List[Tuple[str, str]]] = collections.defaultdict(list)
        self.cmd_of: Dict[str, str] = {}

    @classmethod
    def from_system(cls, tty: Optional[str] = None) -> "ProcessTree":
        """Capture process tree for the entire system or scoped to a specific TTY."""
        instance = cls()
        cmd = ["ps", "-t", tty, "-o", "pid,ppid,command"] if tty else ["ps", "-eo", "pid,ppid,command"]
        try:
            res = subprocess.run(cmd, capture_output=True, check=False)
            lines = res.stdout.decode("utf-8", errors="ignore").strip().splitlines()
            for line in lines[1:]:
                parts = line.strip().split(None, 2)
                if len(parts) == 3:
                    instance.tree[parts[1]].append((parts[0], parts[2]))
                    instance.cmd_of[parts[0]] = parts[2]
        except Exception:
            pass
        return instance

    def get_descendants(self, pid: str) -> str:
        """Collect command lines of all descendant processes under pid."""
        cmds: List[str] = []
        stack = [str(pid)]
        while stack:
            curr = stack.pop()
            for child_pid, child_cmd in self.tree.get(curr, []):
                if child_pid == self.my_pid:
                    continue
                cmds.append(child_cmd)
                stack.append(child_pid)
        return " ".join(cmds)

    def get_descendant_pids(self, pid: str) -> List[str]:
        """Collect all descendant PIDs under pid."""
        pids: List[str] = []
        stack = [str(pid)]
        while stack:
            curr = stack.pop()
            for child_pid, _ in self.tree.get(curr, []):
                if child_pid == self.my_pid:
                    continue
                pids.append(child_pid)
                stack.append(child_pid)
        return pids

    def get_agent_heads(self, pid: str) -> str:
        """
        Find the shallowest process whose program (first two words) names an agent.
        Deeper processes are tools whose args may accidentally mention another agent.
        """
        level = [str(pid)]
        while level:
            heads = " ".join(" ".join(self.cmd_of.get(p, "").split()[:2]) for p in level)
            if AGENTS_REGEX.search(heads):
                return heads
            level = [c for p in level for c, _ in self.tree.get(p, []) if c != self.my_pid]
        return ""


def list_all_panes() -> List[PaneContext]:
    """
    Fetch all panes across all sessions in a single tmux list-panes call.
    Zero per-pane overhead.
    """
    format_spec = (
        "#{session_name}:#{window_index}.#{pane_index}\t"
        "#{pane_pid}\t"
        "#{pane_current_command}\t"
        "#{pane_title}\t"
        "#{window_bell_flag}\t"
        "#{pane_active}\t"
        "#{window_active}\t"
        "#{@agy-session-id}\t"
        "#{@pane-needs-input}\t"
        "#{@command-code-session-id}\t"
        "#{@claude-session-id}\t"
        "#{pane_current_path}\t"
        "#{session_attached}\t"
        "#{@opencode-session-id}\t"
        "#{@omp-session-id}\t"
        "#{pane_tty}\t"
        "#{pane_width}\t"
        "#{pane_height}"
    )
    try:
        raw = subprocess.check_output([
            "tmux", "list-panes", "-a", "-F", format_spec
        ]).decode("utf-8", errors="ignore").strip().splitlines()
    except Exception:
        return []

    panes = []
    for line in raw:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 13:
            continue
        panes.append(PaneContext(
            pane_id=parts[0],
            pid=parts[1],
            current_command=parts[2],
            title=parts[3],
            bell_flag=parts[4],
            pane_active=parts[5],
            window_active=parts[6],
            agy_tag=parts[7].strip() if len(parts) > 7 else "",
            pane_needs_input=parts[8].strip() if len(parts) > 8 else "",
            cmd_code_sid=parts[9].strip() if len(parts) > 9 else "",
            claude_sid=parts[10].strip() if len(parts) > 10 else "",
            cwd=parts[11] if len(parts) > 11 else "",
            session_attached=parts[12] if len(parts) > 12 else "1",
            oc_tag=parts[13].strip() if len(parts) > 13 else "",
            omp_tag=parts[14].strip() if len(parts) > 14 else "",
            tty=parts[15].strip() if len(parts) > 15 else "",
            width=parts[16].strip() if len(parts) > 16 else "",
            height=parts[17].strip() if len(parts) > 17 else "",
        ))
    return panes


def get_pane_context(pane_id: str) -> Optional[PaneContext]:
    """Retrieve metadata for a specific target pane via tmux display-message."""
    format_spec = (
        "#{pane_pid}\t"
        "#{pane_current_command}\t"
        "#{pane_title}\t"
        "#{pane_width}x#{pane_height}\t"
        "#{pane_tty}\t"
        "#{@claude-session-id}\t"
        "#{@command-code-session-id}\t"
        "#{@opencode-session-id}\t"
        "#{@omp-session-id}\t"
        "#{@agy-session-id}\t"
        "#{@pane-needs-input}\t"
        "#{pane_current_path}"
    )
    try:
        raw_info = subprocess.check_output([
            "tmux", "display-message", "-p", "-t", pane_id, format_spec
        ]).decode("utf-8", errors="ignore").rstrip("\n").split("\t") + [""] * 12

        geom = raw_info[3]
        w, h = geom.split("x") if "x" in geom else ("", "")
        return PaneContext(
            pane_id=pane_id,
            pid=raw_info[0],
            current_command=raw_info[1] or "shell",
            title=raw_info[2] or pane_id,
            width=w,
            height=h,
            tty=raw_info[4],
            claude_sid=raw_info[5],
            cmd_code_sid=raw_info[6],
            oc_tag=raw_info[7],
            omp_tag=raw_info[8],
            agy_tag=raw_info[9],
            pane_needs_input=raw_info[10],
            cwd=raw_info[11],
            bell_flag="0",
            pane_active="1",
            window_active="1",
            session_attached="1",
        )
    except Exception:
        return PaneContext(
            pane_id=pane_id,
            pid="",
            current_command="shell",
            title=pane_id,
            bell_flag="0",
            pane_active="1",
            window_active="1",
            session_attached="1",
            cwd="",
            tty="",
        )
