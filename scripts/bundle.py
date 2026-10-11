#!/usr/bin/env python3
"""
Bundle modular tmux_agent_switcher package into a zero-dependency standalone CLI script:
    bin/tmux-agent-switcher
"""

import ast
import os
import re

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_DIR, "src", "tmux_agent_switcher")
TARGET_BIN = os.path.join(REPO_DIR, "bin", "tmux-agent-switcher")

MODULES_IN_ORDER = [
    ("core/config.py", "Configuration & Terminal ANSI Styling"),
    ("core/types.py", "Core Data Types"),
    ("core/tmux.py", "Process Tree & Tmux Introspection"),
    ("formatters/table.py", "Unicode Table Renderer"),
    ("formatters/markdown.py", "Markdown Syntax Styler"),
    ("formatters/denoiser.py", "Terminal Screen De-Noiser"),
    ("formatters/bubbles.py", "Dialogue Turn Bubbles"),
    ("adapters/base.py", "Agent Adapter Interface & Duck Parsing"),
    ("adapters/osc_7501.py", "OSC 7501 Protocol Adapter Stub"),
    ("adapters/cursor.py", "Cursor Adapter"),
    ("adapters/codex.py", "OpenAI Codex Adapter"),
    ("adapters/command_code.py", "Command Code Adapter"),
    ("adapters/claude.py", "Anthropic Claude Code Adapter"),
    ("adapters/grok.py", "xAI Grok Adapter"),
    ("adapters/antigravity.py", "Google Antigravity / AGY Adapter"),
    ("adapters/opencode.py", "OpenCode Adapter"),
    ("adapters/omp.py", "Oh My Pi (OMP) Adapter"),
    ("adapters/system_monitor.py", "System Monitors Adapter"),
    ("adapters/fallback.py", "Tier 1 Fallback TUI Adapter"),
    ("adapters/registry.py", "Adapter Registry"),
    ("notifications/context.py", "Pane Context & Session Resolver"),
    ("notifications/gateway.py", "Universal Notification Gateway"),
    ("cli.py", "CLI Entrypoints & Subcommand Dispatcher"),
]


def clean_source(content: str) -> str:
    """Remove relative package imports and duplicate standard library imports."""
    cleaned = re.sub(r'from \.[a-zA-Z0-9_\. ]+ import \([^)]+\)', '', content, flags=re.MULTILINE)
    cleaned = re.sub(r'from \.[a-zA-Z0-9_\. ]+ import [^\n]+', '', cleaned)
    
    std_imports = [
        r'import (?:argparse|collections|datetime|glob|json|os|platform|re|select|shutil|sqlite3|subprocess|sys|textwrap|time|unicodedata|urllib\.parse)[^\n]*',
        r'from dataclasses import [^\n]+',
        r'from pathlib import [^\n]+',
        r'from typing import [^\n]+',
    ]
    for pat in std_imports:
        cleaned = re.sub(rf'^{pat}\n?', '', cleaned, flags=re.MULTILINE)

    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def build_bundle() -> None:
    header = """#!/usr/bin/env python3
# ==============================================================================
# tmux-agent-switcher — Unified AI Agent Multiplexing & Dialogue Preview Center
#
# Standalone, zero-dependency Python 3 CLI tool for Tmux & FZF.
# Supports: Claude Code, Google Antigravity (AGY), OpenAI Codex, Cursor,
#           OpenCode, xAI Grok, Command Code, OMP, and System Monitors.
#
# Homepage: https://github.com/xzy9565/tmux-agent-switcher
# License: MIT
# ==============================================================================

import argparse
import collections
import datetime
import glob
import json
import os
from pathlib import Path
import platform
import re
import select
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Set
"""

    parts = [header]

    for rel_path, section_name in MODULES_IN_ORDER:
        full_path = os.path.join(SRC_DIR, rel_path)
        with open(full_path, "r", encoding="utf-8") as f:
            code = f.read()

        cleaned = clean_source(code)
        banner = f"\n# --- {section_name} ({rel_path}) ---\n"
        parts.append(banner + cleaned)

    full_script = "\n\n".join(parts) + "\n"
    ast.parse(full_script)

    with open(TARGET_BIN, "w", encoding="utf-8") as f:
        f.write(full_script)

    os.chmod(TARGET_BIN, 0o755)
    print(f"✅ Successfully bundled & verified standalone executable: {TARGET_BIN}")


if __name__ == "__main__":
    build_bundle()
