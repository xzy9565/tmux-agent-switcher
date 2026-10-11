"""Notification and hook observer subsystem for tmux-agent-switcher."""

from .context import resolve_notification_context
from .gateway import handle_notify, handle_hook_title, handle_clear

__all__ = [
    "resolve_notification_context",
    "handle_notify",
    "handle_hook_title",
    "handle_clear",
]
