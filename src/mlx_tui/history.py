"""Context-window trimming + history ring buffer for the sparkline (idea doc §v3)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import ceil

Message = dict[str, str]

CHARS_PER_TOKEN_EST = 3.5

_MAX_TURNS = 64
SPARKLINE_WIDTH = 32
SPARKLINE_HEIGHT_ROWS = 2


@dataclass(frozen=True)
class TurnRecord:
    """One chat turn, frozen so the future JSONL path is mechanical."""

    ts: float  # time.time(), wall clock
    model: str  # effective_model() at record time, or "—" when unknown
    prompt_tok: int
    out_tok: int
    ttft_s: float
    tok_s: float
    ctx_len: int  # prompt-side context length (usage or estimate of trimmed payload)
    cold: bool
    cancelled: bool = False


class HistoryStore:
    """Per-model ring buffers; each model gets deque(maxlen=_MAX_TURNS).

    Not thread-safe — all mutations from ChatPane's thread=True worker
    must be hopped via App.call_from_thread (see Phase 3b). No internal lock;
    single UI-thread owner keeps the implementation stdlib-only and trivial.
    If a second writer ever appears, add a threading.Lock around
    add/series/models/all_records/clear.
    """

    def __init__(self, max_turns: int = _MAX_TURNS) -> None:
        self._max: int = max_turns
        self._by_model: dict[str, deque[TurnRecord]] = {}

    def add(self, record: TurnRecord) -> None:
        self._by_model.setdefault(record.model, deque(maxlen=self._max)).append(record)

    def series(self, model: str) -> list[TurnRecord]:
        return list(self._by_model.get(model, ()))

    def models(self) -> list[str]:
        return sorted(self._by_model)

    def all_records(self) -> list[TurnRecord]:
        flat: list[TurnRecord] = []
        for dq in self._by_model.values():
            flat.extend(dq)

        def _ts(r: TurnRecord) -> float:
            return r.ts

        flat.sort(key=_ts)
        return flat

    def clear(self, model: str | None = None) -> None:
        if model is None:
            self._by_model.clear()
        else:
            self._by_model.pop(model, None)


def _bit(col: int, row_local: int) -> int:
    if col == 0:
        return (0x01, 0x02, 0x04, 0x40)[row_local]
    return (0x08, 0x10, 0x20, 0x80)[row_local]


def _braille_char(bits: int) -> str:
    if bits == 0:
        return " "
    return chr(0x2800 + bits)


def sparkline_visible(
    records: list[TurnRecord], width: int = SPARKLINE_WIDTH
) -> list[TurnRecord]:
    """Filtered + width-capped turns for the sparkline (shared with app.py)."""
    visible = [r for r in records if not r.cold and not r.cancelled]
    return visible[-(width * 2) :] if visible else visible


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
    tok_vals = [r.tok_s for r in visible]
    lo = min(tok_vals)
    hi = max(tok_vals)
    if hi == lo:
        hi = lo + 1.0
    dot_rows = height_rows * 4
    levels: list[int] = []
    for r in visible:
        level = int((r.tok_s - lo) / (hi - lo) * (dot_rows - 1))
        if level < 0:
            level = 0
        elif level >= dot_rows:
            level = dot_rows - 1
        levels.append(level)
    # row_global per turn: 0 top .. dot_rows-1 bottom
    row_globals = [dot_rows - 1 - lv for lv in levels]
    n = len(visible)
    chars = (n + 1) // 2
    lines: list[str] = []
    for row_char in range(height_rows):
        row_start = row_char * 4
        row_end = row_start + 3
        chars_in_row: list[str] = []
        for k in range(chars):
            bits = 0
            idx_left = 2 * k
            if idx_left < n:
                rg = row_globals[idx_left]
                if row_start <= rg <= row_end:
                    row_local = rg - row_start
                    bits |= _bit(0, row_local)
            idx_right = 2 * k + 1
            if idx_right < n:
                rg = row_globals[idx_right]
                if row_start <= rg <= row_end:
                    row_local = rg - row_start
                    bits |= _bit(1, row_local)
            chars_in_row.append(_braille_char(bits))
        lines.append("".join(chars_in_row))
    braille = "\n".join(lines)
    legend = f"{visible[-1].tok_s:.1f} tok/s · ctx {visible[-1].ctx_len} · {len(visible)} turns"
    return (braille, legend)


def _tok_int(s: str) -> int:
    """Parse tok_in_str/tok_out_str; "20 (est)" -> 20, digit-only check, else 0."""
    parts = s.split(maxsplit=1)
    part = parts[0] if parts else ""
    return int(part) if part.isdigit() else 0


def estimate_tokens(text: str) -> int:
    """Estimate token count as ``chars / 3.5``, matching the stamp heuristic."""
    return ceil(len(text) / CHARS_PER_TOKEN_EST)


def trim_for_context(
    messages: list[Message],
    max_est_tokens: int,
) -> list[Message]:
    """Newest user-bound window fitting budget; newest message always kept."""
    if not messages:
        return []
    total = 0
    keep_from = len(messages) - 1
    for i in range(len(messages) - 1, -1, -1):
        total += estimate_tokens(messages[i]["content"])
        if messages[i]["role"] == "user" and total <= max_est_tokens:
            keep_from = i
    return list(messages[keep_from:])
