"""Core data types and structures for Tmux Agent Switcher."""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class PaneContext:
    """Represents full contextual metadata of a single Tmux pane."""
    pane_id: str                      # e.g. "%43" or "session:1.2"
    pid: str                          # Root PID of the pane (shell or direct process)
    current_command: str              # e.g. "zsh", "claude", "python3"
    title: str                        # Pane title (from terminal escape or tmux)
    bell_flag: str                    # "1" if bell was triggered
    pane_active: str                  # "1" if pane is active in its window
    window_active: str                # "1" if window is active in its session
    session_attached: str             # "1" if session has a client attached
    cwd: str                          # Current working directory of pane
    tty: str                          # /dev/ttys00x
    width: str = ""
    height: str = ""
    # Extended hook tags
    claude_sid: str = ""              # #{@claude-session-id}
    agy_tag: str = ""                 # #{@agy-session-id}
    cmd_code_sid: str = ""            # #{@command-code-session-id}
    oc_tag: str = ""                  # #{@opencode-session-id}
    omp_tag: str = ""                 # #{@omp-session-id}
    pane_needs_input: str = ""        # #{@pane-needs-input}


@dataclass
class AgentStatusInfo:
    """Result of agent detection and status extraction for list view."""
    harness: str                      # e.g. "🟠 Claude", "🤖 AGY", "💻 Shell"
    harness_color: str                # ANSI color escape sequence
    status: str                       # e.g. "🔔 input", "⚡ run", "💤 idle"
    status_color: str                 # ANSI color escape sequence
    rank: int                         # Sort rank: 0 (input), 1 (run), 2 (idle), 3 (monitor), 4 (shell)
    mtime: float                      # Epoch timestamp of last user/agent dialogue
    age_str: str                      # e.g. "5s", "12m", "2h", "LIVE", " - "
    display_title: str                # Title for the list column (up to 60 chars)
    is_current: bool                  # True if currently focused pane
    formatted_line: str = ""          # Complete ANSI formatted string for fzf


@dataclass
class TurnMessage:
    """Represents a conversational turn."""
    user: str = ""
    assistant: str = ""


@dataclass
class PreviewResult:
    """Formatted preview content for fzf right pane."""
    source_label: str                 # Header label, e.g. "🟠 Claude · feature-auth"
    content: str                      # Rendered ANSI content (bubbles, markdown, etc.)
