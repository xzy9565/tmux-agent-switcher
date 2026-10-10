"""Adapter registry and priority dispatcher."""

from typing import List, Optional
from ..core.types import PaneContext
from .base import BaseAgentAdapter
from .osc_7501 import Osc7501Adapter
from .cursor import CursorAdapter
from .codex import CodexAdapter
from .command_code import CommandCodeAdapter
from .claude import ClaudeAdapter
from .grok import GrokAdapter
from .antigravity import AntigravityAdapter
from .opencode import OpenCodeAdapter
from .omp import OmpAdapter
from .system_monitor import SystemMonitorAdapter
from .fallback import FallbackTuiAdapter

ADAPTERS: List[BaseAgentAdapter] = [
    Osc7501Adapter(),
    CursorAdapter(),
    CodexAdapter(),
    CommandCodeAdapter(),
    ClaudeAdapter(),
    GrokAdapter(),
    AntigravityAdapter(),
    OpenCodeAdapter(),
    OmpAdapter(),
    SystemMonitorAdapter(),
    FallbackTuiAdapter(),
]


def resolve_adapter(pane: PaneContext, full_desc: str, heads: str) -> BaseAgentAdapter:
    """Find the highest-priority adapter capable of handling this pane."""
    for adapter in ADAPTERS:
        try:
            if adapter.detect(pane, full_desc, heads):
                return adapter
        except Exception:
            continue
    return FallbackTuiAdapter()
