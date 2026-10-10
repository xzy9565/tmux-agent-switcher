"""Core data types and structures for Tmux Agent Switcher."""

from dataclasses import dataclass


@dataclass
class PaneContext:
    """Metadata of a single Tmux pane."""
    pane_id: str
    pid: str
    current_command: str
    title: str
    bell_flag: str = "0"
    pane_active: str = "0"
    window_active: str = "0"
    session_attached: str = "1"
    cwd: str = ""
    tty: str = ""
    width: str = ""
    height: str = ""
    claude_sid: str = ""
    agy_tag: str = ""
    cmd_code_sid: str = ""
    oc_tag: str = ""
    omp_tag: str = ""
    pane_needs_input: str = ""


@dataclass
class PreviewResult:
    """Formatted preview content for fzf right pane."""
    source_label: str
    content: str
