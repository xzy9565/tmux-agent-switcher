"""AI Agent adapters for tmux-agent-switcher."""

from .base import BaseAgentAdapter, read_tail_lines, extract_duck_turns, get_real_dialogue_time
from .claude import ClaudeAdapter
from .antigravity import AntigravityAdapter
from .codex import CodexAdapter
from .cursor import CursorAdapter
from .opencode import OpenCodeAdapter
from .grok import GrokAdapter
from .command_code import CommandCodeAdapter
from .omp import OmpAdapter
from .system_monitor import SystemMonitorAdapter
from .fallback import FallbackTuiAdapter
from .osc_7501 import Osc7501Adapter
from .registry import ADAPTERS, resolve_adapter

__all__ = [
    "BaseAgentAdapter",
    "read_tail_lines",
    "extract_duck_turns",
    "get_real_dialogue_time",
    "ClaudeAdapter",
    "AntigravityAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "OpenCodeAdapter",
    "GrokAdapter",
    "CommandCodeAdapter",
    "OmpAdapter",
    "SystemMonitorAdapter",
    "FallbackTuiAdapter",
    "Osc7501Adapter",
    "ADAPTERS",
    "resolve_adapter",
]
