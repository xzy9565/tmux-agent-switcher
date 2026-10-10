"""Unicode box-drawing table renderer for markdown tables."""

import re
from typing import List
from ..core.config import (
    C_RESET, C_BORDER, C_ICE_BLUE, C_TD,
    str_display_width, pad_str
)


def render_table(rows: List[str], style_inline_fn) -> List[str]:
    """
    Render GFM-style markdown table rows into beautiful Unicode box-drawing tables.
    Accurately accounts for CJK double-width characters and inline styles.
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

    top = C_BORDER + "┌─" + "─┬─".join("─" * w for w in col_widths) + "─┐" + C_RESET
    mid = C_BORDER + "├─" + "─┼─".join("─" * w for w in col_widths) + "─┤" + C_RESET
    bot = C_BORDER + "└─" + "─┴─".join("─" * w for w in col_widths) + "─┘" + C_RESET

    res = [top]
    h_cells = []
    for i in range(num_cols):
        raw = header[i] if i < len(header) else ""
        styled = style_inline_fn(raw, C_ICE_BLUE)
        h_cells.append(pad_str(f"{C_ICE_BLUE}{styled}{C_RESET}", col_widths[i]))
    res.append(f"{C_BORDER}│{C_RESET} " + f" {C_BORDER}│{C_RESET} ".join(h_cells) + f" {C_BORDER}│{C_RESET}")
    res.append(mid)

    for r in data_rows:
        d_cells = []
        for i in range(num_cols):
            raw = r[i] if i < len(r) else ""
            styled = style_inline_fn(raw, C_TD)
            d_cells.append(pad_str(f"{C_TD}{styled}{C_RESET}", col_widths[i]))
        res.append(f"{C_BORDER}│{C_RESET} " + f" {C_BORDER}│{C_RESET} ".join(d_cells) + f" {C_BORDER}│{C_RESET}")

    res.append(bot)
    return res
