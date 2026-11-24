"""Terminal output semantic de-noiser."""

import re

# Only agent TUI chrome. Rules for shell prompts, clock times and NxN sizes went: on real captures they matched nothing
# but output (git@github.com:…, react@18, 1920x1080, 0x10, 12:30 PM), and stripping a leading > turned >= into =.
DENOISE_PATTERNS = [
    r"^.*(?:Token Monitor|AI Ledger|Shift\+Tab|Ctrl\+\.|\d+K\s*/\s*\d+K).*$",
    r"^.*(?:always-approve|Worked for \d+|［hooks：|\bhooks:\b).*$",
    r"^[│┌┐└┘├┤┬┴┼━┃┏┓┗┛┣┫┳┻╋═║╔╗╚╝╠╣╦╩╬╭╮╯╰─\s]+$",
    r"^\s*[L•]\s*$",
    r"^.*(?:<truncated \d+ bytes>|truncated \d+ bytes).*$",
    r"^.*(?:Transfer-Encoding:\s*chunked|\bchunked\s+\d+\s*bytes\b).*$",
]
COMBINED_DENOISE_RE = re.compile("|".join(f"(?:{p})" for p in DENOISE_PATTERNS), re.IGNORECASE)


def denoise_terminal_text(text: str) -> str:
    """
    Filter raw terminal screen captures into clean, readable text.
    Strips agent TUI noise (status bars, key hints, spinners, orphan border lines) and collapses blank runs.
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
        lines.append(line)

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)
