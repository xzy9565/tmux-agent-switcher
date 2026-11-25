"""Terminal color palettes, ANSI escape codes, and display utilities."""

import os
import re
import unicodedata
from typing import List

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


ANSI_SEQ = re.compile(r"\033\]8;;[^\033]*\033\\|\033\[[0-9;]*[a-zA-Z]")


def char_width(c: str) -> int:
    """Cells tmux gives one code point: 2 if East Asian wide, 0 for marks and format characters, else 1."""
    cp = ord(c)
    cat = unicodedata.category(c)
    if (cat in ("Mn", "Me", "Cf") and cp != 0xAD) or 0x1160 <= cp <= 0x11FF:  # a soft hyphen draws; jamo vowels join
        return 0
    if unicodedata.east_asian_width(c) in ("W", "F"):
        return 2
    return 2 if cat == "Cn" and 0x1F000 <= cp <= 0x1FAFF else 1  # emoji newer than this Python's Unicode (3.9: 13.0)


def str_display_width(s: str) -> int:
    """Cells s takes in tmux, ignoring SGR and OSC 8: what lines a table up and decides where fzf wraps a row.

    Measured in a tmux 3.8 pane with cursor position reports, and fzf 0.75 agrees: ambiguous symbols like ✓ ★ ⚠ take 1,
    VS16 widens the character before it to 2, and after a ZWJ or a skin tone a wide emoji joins the next into its glyph.
    """
    s = ANSI_SEQ.sub("", s)
    if s.isascii():
        return len(s)
    total = last = 0  # last: cells of the previous visible character
    join = False
    for c in s:
        cp = ord(c)
        if cp == 0xFE0F:
            if last == 1:
                total, last = total + 1, 2
        elif cp == 0x200D:
            join = last == 2
        elif join or (0x1F3FB <= cp <= 0x1F3FF and last == 2):
            join = False
        else:
            w = char_width(c)
            if w:
                total, last = total + w, w
    return total


def pad_str(s: str, target_width: int) -> str:
    """Pad string with trailing spaces to match target display width."""
    curr_w = str_display_width(s)
    pad = max(0, target_width - curr_w)
    return s + " " * pad


PIECES = re.compile(r"\033\]8;;[^\033]*\033\\|\033\[[0-9;]*m| +|[^ ]")  # escape, spaces, or one character
STATE = re.compile(r"\033\]8;;([^\033]*)\033\\|(\033\[[0-9;]*m)")
CLOSERS = "，。、；：！？）】」』》〉”’…"  # never start a line
OPENERS = "（【「『《〈“‘"  # never end one


def _joins(prev: str, tok: str) -> bool:
    """True if tok is drawn as part of the character before it, so no line may break between them."""
    return tok[0] == "\033" or not char_width(tok) or prev == "\u200d" or "\U0001f3fb" <= tok <= "\U0001f3ff"


def _units(text: str) -> List[str]:
    """Split styled text into runs no line breaks inside, and the spaces between them. CJK breaks between characters."""
    units, word, prev, prev_wide = [], "", "", False
    for tok in PIECES.findall(text):
        if tok[0] == " ":
            units += [word, tok] if word else [tok]
            word, prev, prev_wide = "", "", False
            continue
        if not _joins(prev, tok):
            wide = char_width(tok) == 2
            if prev and (prev_wide or wide) and tok not in CLOSERS and prev not in OPENERS:
                units.append(word)
                word = ""
            prev, prev_wide = tok, wide
        elif tok[0] != "\033":
            prev = tok
        word += tok
    return units + [word] if word else units


def _cut(run: str, n: int):
    """Split a run wider than a line into a head at most n cells wide and the rest."""
    toks = PIECES.findall(run)
    for i in range(1, len(toks)):
        if not _joins(toks[i - 1], toks[i]) and str_display_width("".join(toks[:i + 1])) > n:
            return "".join(toks[:i]), "".join(toks[i:])
    return run, ""


def wrap_ansi(text: str, limit: int) -> List[str]:
    """Wrap styled text into lines at most limit cells wide; a run wider than a whole line is cut.

    A span or link the break falls inside closes at the line end and reopens on the next line.
    """
    lines, cur, cur_w, gap = [], "", 0, ""
    for u in _units(text.expandtabs()):
        if u[0] == " ":
            gap = u
            continue
        uw = str_display_width(u)
        if cur_w and cur_w + len(gap) + uw > limit:
            lines.append(cur)
            cur, cur_w, gap = "", 0, ""
        if cur_w or not lines:  # spaces start only the first line
            cur, cur_w = cur + gap, cur_w + len(gap)
        gap = ""
        while cur_w + uw > limit:
            head, u = _cut(u, limit - cur_w)
            lines.append(cur + head)
            cur, cur_w, uw = "", 0, str_display_width(u)
        cur, cur_w = cur + u, cur_w + uw
    lines.append(cur)

    out, sgr, link = [], "", ""
    for line in lines:
        head = sgr + (f"\033]8;;{link}\033\\" if link else "")
        for url, code in STATE.findall(line):
            if code:
                sgr = "" if code == C_RESET else sgr + code
            else:
                link = url
        out.append(head + line + ("\033]8;;\033\\" if link else "") + (C_RESET if sgr else ""))
    return out


FENCE = re.compile(r"\s*(`{3,})([^`]*)$")


def fence_after(line: str, fence: str, strict: bool = False) -> str:
    """The code fence open after this line, '' outside code: every ``` line toggles, or (strict) as in CommonMark,
    where only a bare run of at least the opening backticks closes a block."""
    if not strict:
        return ("" if fence else "```") if line.lstrip().startswith("```") else fence
    m = FENCE.match(line)
    if not m:
        return fence
    if not fence:
        return m.group(1)
    return "" if len(m.group(1)) >= len(fence) and not m.group(2).strip() else fence


def strict_fences(lines: List[str]) -> bool:
    """Whether to pair a reply's fences as CommonMark does. Toggling keeps a nested example whole (```markdown around
    ```mermaid ... ```), where CommonMark ends the outer block early; but a lone ```diff line shown inside a code block
    swaps code and prose for the rest of the reply, which then ends inside a block. Only there does CommonMark take over."""
    loose = strict = ""
    for line in lines:
        loose, strict = fence_after(line, loose), fence_after(line, strict, True)
    return bool(loose) and not strict
