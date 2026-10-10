"""Terminal output semantic de-noiser."""

import re

DENOISE_PATTERNS = [
    r"^.*(?:Token Monitor|AI Ledger|Shift\+Tab|Ctrl\+\.|\b\d+x\d+\b|\d+K\s*/\s*\d+K).*$",
    r"^.*(?:always-approve|Worked for \d+|［hooks：|\bhooks:\b).*$",
    r"^[│┌┐└┘├┤┬┴┼━┃┏┓┗┛┣┫┳┻╋═║╔╗╚╝╠╣╦╩╬╭╮╯╰─\s]+$",
    r"^[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+.*$",
    r"^\s*[L•]\s*$",
    r"^\s*\d+:\d+\s*(?:AM|PM)?\s*$",
    r"^.*(?:<truncated \d+ bytes>|truncated \d+ bytes).*$",
    r"^.*(?:Transfer-Encoding:\s*chunked|\bchunked\s+\d+\s*bytes\b).*$",
]
COMBINED_DENOISE_RE = re.compile("|".join(f"(?:{p})" for p in DENOISE_PATTERNS), re.IGNORECASE)


def denoise_terminal_text(text: str) -> str:
    """
    Filter raw terminal screen captures into clean, readable text.
    Strips noise like spinners, status bars, timestamps, shell prompt indicators,
    and orphan border characters.
    """
    lines = []
    for l in text.split("\n"):
        line = l.rstrip()
        if not line.strip():
            if lines and lines[-1]:
                lines.append("")
            continue
        if COMBINED_DENOISE_RE.match(line):
            continue
        clean_l = re.sub(r"^[〉❯>›]\s*", "", line)
        lines.append(clean_l)

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)
