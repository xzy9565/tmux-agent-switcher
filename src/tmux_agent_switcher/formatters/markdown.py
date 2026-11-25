"""Markdown parser and ANSI syntax highlighter."""

import os
import re
from typing import List, Optional
from ..core.config import (
    C_RESET, C_BOLD, C_ITALIC, C_STRIKE,
    C_ICE_BLUE, C_MINT, C_PEACH, C_SLATE_BLUE,
    C_BORDER, C_DIVIDER, C_MUTED, C_LINK, C_WHITE_BOLD, wrap_ansi
)
from .table import render_table


def wrap_text(text: str, width: int = 0, indent: int = 0) -> List[str]:
    """Wrap styled text to the preview width in display cells, so continuation lines keep their indent or rail.

    Style before wrapping: a span or link the break falls inside closes at the line end and reopens on the next line.
    """
    effective_w = width or int(os.environ.get("FZF_PREVIEW_COLUMNS") or 0)
    if not effective_w or effective_w - indent < 20:
        return [text]
    return wrap_ansi(text, effective_w - indent - 1)


def style_inline(text: str, base_col: str = "") -> str:
    """Apply inline markdown ANSI formatting: code, links, bold, italic, strikethrough.

    Each span turns off only what it turned on (then restores base_col), so a span inside another keeps the outer style.
    """
    s = text
    code_tokens = []

    def save_code(m):
        code_tokens.append(f"{C_PEACH}{m.group(1)}\033[39m{base_col}")
        return f"\x00CODE_{len(code_tokens)-1}\x00"

    s = re.sub(r"`([^`]+)`", save_code, s)
    # Inline HTML in markdown: <br> breaks the line (a space on one row), formatting tags only style their text
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"</?(?:b|i|u|s|em|strong|kbd|code|mark|sub|sup|small|span|p|div|del|ins|details|summary)>", "", s, flags=re.I)

    def render_link(m):
        txt, url = m.group(1), m.group(2)
        code_tokens.append(f"\033]8;;{url}\033\\")  # held back like code, or emphasis rewrites the _ and * in URLs
        return f"\x00CODE_{len(code_tokens)-1}\x00{C_LINK}{txt}\033[24;39m{base_col}\033]8;;\033\\"

    s = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+|file://[^\)]+|/[^\)]+)\)", render_link, s)

    s = re.sub(r"\*\*\*([^\*]+?)\*\*\*", rf"{C_WHITE_BOLD}{C_ITALIC}\1\033[22;23;39m{base_col}", s)
    s = re.sub(r"___([^_]+?)___", rf"{C_WHITE_BOLD}{C_ITALIC}\1\033[22;23;39m{base_col}", s)
    # Bold may hold single * or _: an italic inside it
    s = re.sub(r"\*\*((?:[^*]|\*(?!\*))+?)\*\*", rf"{C_WHITE_BOLD}\1\033[22;39m{base_col}", s)
    s = re.sub(r"(?<!\w)__((?:[^_]|_(?!_))+?)__(?!\w)", rf"{C_WHITE_BOLD}\1\033[22;39m{base_col}", s)
    s = re.sub(r"(?<!\*)\*([^\*\s][^\*]*?[^\*\s]|[^\*\s])\*(?!\*)", rf"{C_ITALIC}\1\033[23m", s)
    s = re.sub(r"(?<!\w)_([^_\s][^_]*?[^_\s]|[^_\s])_(?!\w)", rf"{C_ITALIC}\1\033[23m", s)
    s = re.sub(r"~~([^~]+?)~~", rf"{C_STRIKE}{C_MUTED}\1\033[29;39m{base_col}", s)

    for i, tok in enumerate(code_tokens):
        s = s.replace(f"\x00CODE_{i}\x00", tok)
    return s


def render_md(text: str, preview_width: int = 0) -> str:
    """Render block Markdown to richly styled ANSI terminal output."""
    out = []
    in_code = False
    table_buf: List[str] = []

    def flush_table():
        nonlocal table_buf
        if table_buf:
            out.extend(render_table(table_buf, style_inline, preview_width))
            table_buf = []

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            flush_table()
            in_code = not in_code
            lang = line.strip().lstrip("`")
            out.append(
                f"{C_BORDER}─── {lang or 'code'} ──────────────────────────────────────────{C_RESET}"
                if in_code else f"{C_BORDER}{'─' * 50}{C_RESET}"
            )
            continue

        if in_code:
            out.append(f"  {C_MINT}{line}{C_RESET}")
            continue

        if line.strip().startswith("|") and line.strip().endswith("|"):
            table_buf.append(line.strip())
            continue
        else:
            flush_table()

        m_hdr = re.match(r"^(#{1,6})\s*(.+)$", line)
        if m_hdr:
            col, mark = {1: (C_ICE_BLUE, "✦"), 2: (C_ICE_BLUE, "■"), 3: (C_SLATE_BLUE, "▲")}.get(len(m_hdr.group(1)), (C_MINT, "▸"))
            body = style_inline(m_hdr.group(2).strip().rstrip("#").strip(), col + C_BOLD)  # spans end back in the header style
            out.append(f"\n{col}{C_BOLD}{mark} {body}{C_RESET}")
            continue

        if line.strip().startswith(">"):
            for q in wrap_text(style_inline(line.strip().lstrip("> "), C_MUTED), preview_width, 4):
                out.append(f"  {C_BORDER}│{C_RESET} {C_MUTED}{C_ITALIC}{q}{C_RESET}")
            continue

        m_task = re.match(r"^(\s*)[\*\-\+]\s+\[([ xX])\]\s+(.*)$", line)
        if m_task:
            indent = m_task.group(1)
            checked = m_task.group(2).lower() == 'x'
            badge = f"{C_MINT}☑{C_RESET}" if checked else f"{C_MUTED}☐{C_RESET}"
            for i, b in enumerate(wrap_text(style_inline(m_task.group(3)), preview_width, len(indent) + 2)):
                out.append(f"{indent}{badge if i == 0 else ' '} {b}")
            continue

        if re.match(r"^\s*[\*\-\•]\s+", line):
            indent = len(line) - len(line.lstrip())
            body = re.sub(r"^\s*[\*\-\•]\s+", "", line)
            for i, b in enumerate(wrap_text(style_inline(body), preview_width, indent + 2)):
                out.append(" " * indent + (f"{C_SLATE_BLUE}•{C_RESET} " if i == 0 else "  ") + b)
            continue

        m_num = re.match(r"^(\s*\d+\.\s+)(.+)$", line)
        if m_num:
            p = m_num.group(1)
            for i, b in enumerate(wrap_text(style_inline(m_num.group(2)), preview_width, len(p))):
                out.append((f"{C_ICE_BLUE}{p}{C_RESET}" if i == 0 else " " * len(p)) + b)
            continue

        if re.match(r"^(\-{3,}|\_{3,}|\*{3,})$", line.strip()):
            out.append(f"{C_DIVIDER}─────────────────────────────────────────────────────────────────{C_RESET}")
            continue

        lead = len(line) - len(line.lstrip())
        for b in wrap_text(style_inline(line.strip()), preview_width, lead):
            out.append(" " * lead + b)

    flush_table()
    return "\n".join(out)
