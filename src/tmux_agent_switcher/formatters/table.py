"""Unicode box-drawing table renderer for markdown tables."""

import re
from typing import List
from ..core.config import (
    C_RESET, C_BORDER, C_ICE_BLUE, C_TD,
    str_display_width, pad_str, wrap_ansi
)


def render_table(rows: List[str], style_inline_fn, width: int = 0) -> List[str]:
    """
    Render GFM-style markdown table rows into Unicode box-drawing tables, measured in display cells.
    Given the preview width, the widest columns narrow until the table fits it and their cells wrap.
    """
    if not rows:
        return []

    table_data = []
    for r in rows:
        cells = [c.strip() for c in r.strip().strip('|').split('|')]
        table_data.append(cells)

    if not table_data:
        return []

    header = table_data[0]
    data_rows = []
    for r in table_data[1:]:
        if all(re.match(r"^:?-+:?$", c) for c in r if c):
            continue
        data_rows.append(r)

    num_cols = max(len(r) for r in table_data)
    col_widths = [0] * num_cols

    for r in [header] + data_rows:
        for i, c in enumerate(r):
            if i < num_cols:
                col_widths[i] = max(col_widths[i], str_display_width(style_inline_fn(c)))

    col_widths = [max(w, 4) for w in col_widths]
    # A row is "│ " + cells joined by " │ " + " │", and stays a cell inside the preview, as wrapped text does
    budget = width - 1 - (3 * num_cols + 1)
    while width and sum(col_widths) > budget and max(col_widths) > 4:
        col_widths[col_widths.index(max(col_widths))] -= 1

    top = C_BORDER + "┌─" + "─┬─".join("─" * w for w in col_widths) + "─┐" + C_RESET
    mid = C_BORDER + "├─" + "─┼─".join("─" * w for w in col_widths) + "─┤" + C_RESET
    bot = C_BORDER + "└─" + "─┴─".join("─" * w for w in col_widths) + "─┘" + C_RESET

    def row_lines(cells: List[str], color: str) -> List[str]:
        wrapped = [wrap_ansi(f"{color}{style_inline_fn(cells[i] if i < len(cells) else '', color)}{C_RESET}", w)
                   for i, w in enumerate(col_widths)]
        return [f"{C_BORDER}│{C_RESET} " + f" {C_BORDER}│{C_RESET} ".join(
                    pad_str(c[k] if k < len(c) else "", w) for c, w in zip(wrapped, col_widths)) + f" {C_BORDER}│{C_RESET}"
                for k in range(max(map(len, wrapped)))]

    res = [top] + row_lines(header, C_ICE_BLUE) + [mid]
    for r in data_rows:
        res += row_lines(r, C_TD)
    res.append(bot)
    return res
