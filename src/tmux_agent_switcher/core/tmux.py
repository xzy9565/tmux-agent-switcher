"""Tmux interface and process tree discovery utilities."""

import collections
import os
import re
import subprocess
from typing import Dict, List, Tuple, Optional
from .types import PaneContext

OMP_PATTERN = r"(^|[\s/])omp(\s|$)"
AGENTS_REGEX = re.compile(r"cursor-agent|codex|command-code|claude|grok|agy|opencode|" + OMP_PATTERN)


class ProcessTree:
    """Snapshot of the operating system process tree."""

    def __init__(self, tty: Optional[str] = None):
        self.my_pid = str(os.getpid())
        self.tree: Dict[str, List[Tuple[str, str]]] = collections.defaultdict(list)
        self.cmd_of: Dict[str, str] = {}
        cmd = ["ps", "-t", tty, "-o", "pid,ppid,command"] if tty else ["ps", "-eo", "pid,ppid,command"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            for line in res.stdout.strip().splitlines()[1:]:
                parts = line.strip().split(None, 2)
                if len(parts) == 3:
                    self.tree[parts[1]].append((parts[0], parts[2]))
                    self.cmd_of[parts[0]] = parts[2]
        except Exception:
            pass

    def get_descendants(self, pid: str) -> str:
        """Collect command lines of all descendant processes under pid."""
        cmds, stack = [], [str(pid)]
        while stack:
            curr = stack.pop()
            for child_pid, child_cmd in self.tree.get(curr, []):
                if child_pid != self.my_pid:
                    cmds.append(child_cmd)
                    stack.append(child_pid)
        return " ".join(cmds)

    def get_descendant_pids(self, pid: str) -> List[str]:
        """Collect all descendant PIDs under pid."""
        pids, stack = [], [str(pid)]
        while stack:
            curr = stack.pop()
            for child_pid, _ in self.tree.get(curr, []):
                if child_pid != self.my_pid:
                    pids.append(child_pid)
                    stack.append(child_pid)
        return pids

    def get_agent_heads(self, pid: str) -> str:
        """Find the shallowest process whose command name matches an agent."""
        level = [str(pid)]
        while level:
            heads = " ".join(" ".join(self.cmd_of.get(p, "").split()[:2]) for p in level)
            if AGENTS_REGEX.search(heads):
                return heads
            level = [c for p in level for c, _ in self.tree.get(p, []) if c != self.my_pid]
        return ""


def list_all_panes() -> List[PaneContext]:
    """Fetch all panes across all sessions in a single tmux list-panes call."""
    fmt = (
        "#{session_name}:#{window_index}.#{pane_index}\t#{pane_pid}\t#{pane_current_command}\t"
        "#{pane_title}\t#{window_bell_flag}\t#{pane_active}\t#{window_active}\t#{@agy-session-id}\t"
        "#{@pane-needs-input}\t#{@command-code-session-id}\t#{@claude-session-id}\t#{pane_current_path}\t"
        "#{session_attached}\t#{@opencode-session-id}\t#{@omp-session-id}\t#{pane_tty}\t"
        "#{pane_width}\t#{pane_height}"
    )
    try:
        raw = subprocess.check_output(["tmux", "list-panes", "-a", "-F", fmt]).decode("utf-8", errors="ignore").splitlines()
    except Exception:
        return []

    panes = []
    for line in raw:
        if not line.strip():
            continue
        p = (line.split("\t") + [""] * 18)[:18]
        panes.append(PaneContext(
            pane_id=p[0], pid=p[1], current_command=p[2], title=p[3], bell_flag=p[4],
            pane_active=p[5], window_active=p[6], agy_tag=p[7].strip(), pane_needs_input=p[8].strip(),
            cmd_code_sid=p[9].strip(), claude_sid=p[10].strip(), cwd=p[11], session_attached=p[12] or "1",
            oc_tag=p[13].strip(), omp_tag=p[14].strip(), tty=p[15].strip(), width=p[16].strip(), height=p[17].strip()
        ))
    return panes


def get_pane_context(pane_id: str) -> Optional[PaneContext]:
    """Retrieve metadata for a specific target pane via tmux display-message."""
    fmt = (
        "#{pane_pid}\t#{pane_current_command}\t#{pane_title}\t#{pane_width}x#{pane_height}\t"
        "#{pane_tty}\t#{@claude-session-id}\t#{@command-code-session-id}\t#{@opencode-session-id}\t"
        "#{@omp-session-id}\t#{@agy-session-id}\t#{@pane-needs-input}\t#{pane_current_path}"
    )
    try:
        raw = (subprocess.check_output(["tmux", "display-message", "-p", "-t", pane_id, fmt])
               .decode("utf-8", errors="ignore").rstrip("\n").split("\t") + [""] * 12)[:12]
        geom = raw[3]
        w, h = geom.split("x") if "x" in geom else ("", "")
        return PaneContext(
            pane_id=pane_id, pid=raw[0], current_command=raw[1] or "shell", title=raw[2] or pane_id,
            width=w, height=h, tty=raw[4], claude_sid=raw[5], cmd_code_sid=raw[6],
            oc_tag=raw[7], omp_tag=raw[8], agy_tag=raw[9], pane_needs_input=raw[10], cwd=raw[11]
        )
    except Exception:
        return PaneContext(pane_id=pane_id, pid="", current_command="shell", title=pane_id)
