"""Braille sparkline rendering + ctx shading."""

from __future__ import annotations

import statistics

from mlx_tui.history.store import MemoryRecord, TurnRecord

SPARKLINE_WIDTH = 32
SPARKLINE_HEIGHT_ROWS = 2


_BRAILLE_LEFT = (0x01, 0x02, 0x04, 0x40)
_BRAILLE_RIGHT = (0x08, 0x10, 0x20, 0x80)


def _braille_char(bits: int) -> str:
    return chr(0x2800 + bits) if bits else " "


def sparkline_visible(
    records: list[TurnRecord], width: int = SPARKLINE_WIDTH
) -> list[TurnRecord]:
    """Filtered + width-capped turns for the sparkline (shared with app.py)."""
    visible = [r for r in records if not r.cold and not r.cancelled]
    return visible[-(width * 2) :] if visible else visible


def _braille_levels(values: list[float | None], dot_rows: int) -> list[int | None]:
    """Map values to 0..dot_rows-1; None stays None for blank columns."""
    vals = [v for v in values if v is not None]
    if not vals:
        return [None] * len(values)
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1.0
    span = hi - lo
    out: list[int | None] = []
    for v in values:
        if v is None:
            out.append(None)
        else:
            lv = int((v - lo) / span * (dot_rows - 1))
            out.append(max(0, min(dot_rows - 1, lv)))
    return out


def _render_braille(levels: list[int | None], height_rows: int) -> str:
    """Pack per-turn levels (or None for blank) into braille chars."""
    dot_rows = height_rows * 4
    row_globals = [(dot_rows - 1 - lv) if lv is not None else None for lv in levels]
    n = len(levels)
    chars = (n + 1) // 2
    lines: list[str] = []
    for row_char in range(height_rows):
        row_start = row_char * 4
        row_end = row_start + 3
        chars_in_row: list[str] = []
        for k in range(chars):
            bits = 0
            for col, idx in enumerate((2 * k, 2 * k + 1)):
                if idx < n:
                    rg = row_globals[idx]
                    if rg is not None and row_start <= rg <= row_end:
                        bits |= (_BRAILLE_LEFT if col == 0 else _BRAILLE_RIGHT)[
                            rg - row_start
                        ]
            chars_in_row.append(_braille_char(bits))
        lines.append("".join(chars_in_row))
    return "\n".join(lines)


def render_sparkline(
    records: list[TurnRecord],
    *,
    width: int = SPARKLINE_WIDTH,
    height_rows: int = SPARKLINE_HEIGHT_ROWS,
) -> tuple[str, str]:
    """Braille sparkline for the sparkline strip.

    Filters ``cold``/``cancelled`` turns, buckets ``tok_s`` into
    ``height_rows * 4`` dot rows, encodes 2×4 dots per braille char
    (U+2800 base), shade is caller-applied via ctx quartiles (see
    app.py helper). Returns ``(braille_lines, legend)`` where
    ``braille_lines`` is ``height_rows`` lines joined by ``\\n`` and
    ``legend`` is ``f"{tok_s:.1f} tok/s · ctx {ctx_len} · {n} turns"``
    for the latest visible turn. Empty/filtered input →
    ``("", "no history yet — chat to build it")``.
    """
    visible = sparkline_visible(records, width)
    if not visible:
        return ("", "no history yet — chat to build it")
    dot_rows = height_rows * 4
    levels = _braille_levels([r.tok_s for r in visible], dot_rows)
    braille = _render_braille(levels, height_rows)
    legend = f"{visible[-1].tok_s:.1f} tok/s · ctx {visible[-1].ctx_len} · {len(visible)} turns"
    return (braille, legend)


def render_memory_sparkline(  # noqa: PLR0912
    records: list[MemoryRecord],
    *,
    width: int = SPARKLINE_WIDTH,
    height_rows: int = SPARKLINE_HEIGHT_ROWS,
) -> tuple[str, str]:
    """Braille sparkline for RSS history.

    Buckets ``rss_gib`` into ``height_rows * 4`` dot rows, encodes 2×4 dots
    per braille char (U+2800 base). ``None`` rss produces a blank column so
    legend ``· {n} samples`` counts all records including no-pid ticks.
    Returns ``(braille_lines, legend)`` where ``braille_lines`` is
    ``height_rows`` lines joined by ``\\n`` and ``legend`` is
    ``f"{rss:.1f} GB RSS · avail {avail:.1f}/{total:.1f} · {n} samples"``
    for the latest non-``None`` rss. Empty or all-``None`` →
    ``("", "no memory samples yet")``.
    """
    if not records:
        return ("", "no memory samples yet")
    visible = records[-(width * 2) :] if len(records) > width * 2 else records
    rss_vals = [r.rss_gib for r in visible]
    if all(v is None for v in rss_vals):
        return ("", "no memory samples yet")
    dot_rows = height_rows * 4
    levels = _braille_levels(rss_vals, dot_rows)
    braille = _render_braille(levels, height_rows)
    n = len(visible)
    latest: MemoryRecord | None = None
    for r in reversed(visible):
        if r.rss_gib is not None:
            latest = r
            break
    assert latest is not None
    legend = f"{latest.rss_gib:.1f} GB RSS · avail {latest.avail_gib:.1f}/{latest.total_gib:.1f} · {n} samples"
    return (braille, legend)


def _shade_for_ctx(ctx_lens: list[int]) -> list[str]:
    """Map ctx lengths to Rich styles via quartiles: dim / \"\" / bold."""
    if not ctx_lens:
        return []
    try:
        q1, _, q3 = statistics.quantiles(sorted(ctx_lens), n=4)
    except statistics.StatisticsError:
        s = sorted(ctx_lens)
        q1, q3 = s[len(s) // 4], s[3 * len(s) // 4]
    return ["dim" if c <= q1 else "bold" if c >= q3 else "" for c in ctx_lens]
