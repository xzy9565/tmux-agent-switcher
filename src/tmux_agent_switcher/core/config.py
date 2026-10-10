"""Terminal color palettes, ANSI escape codes, and display utilities."""

import os
import re
import unicodedata

# Basic ANSI Controls
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_ITALIC = "\033[3m"
C_STRIKE = "\033[9m"

# Standard / High-Intensity Colors
C_RED_BOLD = "\033[1;31m"
C_GREEN_BOLD = "\033[1;32m"
C_CYAN_BOLD = "\033[1;36m"
C_WHITE = "\033[97m"
C_WHITE_BOLD = "\033[1;37m"

# 24-bit TrueColor Agent Palettes
C_AGY = "\033[1;38;2;78;136;245m"        # Google Gemini Spark Blue (#4E88F5)
C_CLAUDE = "\033[1;38;2;217;119;87m"     # Anthropic Terracotta Coral (#D97757)
C_CODEX = "\033[1;38;2;16;163;127m"      # OpenAI Emerald Teal (#10A37F)
C_CURSOR = "\033[1;38;2;226;232;240m"    # Cursor Titanium Silver (#E2E8F0)
C_GROK = "\033[1;38;2;240;240;245m"      # xAI Stark Monochrome (#F0F0F5)
C_CMD = "\033[1;38;2;6;182;212m"         # Command Code Cyan (#06B6D4)
C_OPENCODE = "\033[1;38;2;34;197;94m"    # OpenCode Vibrant Green (#22C55E)
C_OMP = "\033[1;38;2;167;139;250m"       # Oh My Pi Violet (#A78BFA)
C_MONITOR = "\033[38;2;148;163;184m"     # Telemetry Slate (#94A3B8)

# Dialogue Preview Palettes
C_ICE_BLUE = "\033[1;38;2;125;207;255m"
C_LAVENDER = "\033[1;38;2;187;154;247m"
C_MINT = "\033[38;2;158;206;106m"
C_PEACH = "\033[38;2;255;195;135m"
C_SLATE_BLUE = "\033[38;2;122;162;247m"
C_BORDER = "\033[38;2;86;95;137m"
C_DIVIDER = "\033[38;2;65;72;100m"
C_MUTED = "\033[38;2;140;145;170m"
C_LINK = "\033[4;38;2;125;207;255m"
C_TD = "\033[38;2;226;232;240m"

# Conversation Turn Bubble Styling
C_USER_BG = "\033[1;48;2;76;29;149;38;2;233;213;255m"
C_USER_TXT = "\033[1;38;2;243;232;255m"
C_ACTIVE_BG = "\033[1;48;2;120;53;15;38;2;254;240;138m"
C_ACTIVE_TXT = "\033[1;38;2;254;243;199m"
C_BAR_ACTIVE = "\033[38;2;245;158;11m"
C_BAR_USER = "\033[38;2;168;85;247m"

SHELLS = ("zsh", "-zsh", "bash", "sh", "fish")


def char_width(c: str) -> int:
    """Return monospace display width for a single character (handles CJK/emojis/symbols)."""
    if unicodedata.east_asian_width(c) in ("W", "F"):
        return 2
    cp = ord(c)
    if (0x1F300 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or
        0x1100 <= cp <= 0x115F or 0x2E80 <= cp <= 0xA4CF or
        0xAC00 <= cp <= 0xD7A3 or 0xF900 <= cp <= 0xFAFF or
        0xFE10 <= cp <= 0xFE19 or 0xFE30 <= cp <= 0xFE6F or
        0xFF00 <= cp <= 0xFF60 or 0xFFE0 <= cp <= 0xFFE6 or
        0x20000 <= cp <= 0x2FA1F):
        return 2
    return 1


def str_display_width(s: str) -> int:
    """Calculate terminal visual width of a string, ignoring ANSI and OSC sequences."""
    s_clean = re.sub(r'\033\]8;;[^\033]*\033\\', '', s)
    s_clean = re.sub(r'\033\[[0-9;]*[a-zA-Z]', '', s_clean)
    return sum(char_width(c) for c in s_clean)


def pad_str(s: str, target_width: int) -> str:
    """Pad string with trailing spaces to match target display width."""
    curr_w = str_display_width(s)
    pad = max(0, target_width - curr_w)
    return s + " " * pad
