"""Context-window trimming, no tokenizer."""

from __future__ import annotations

from math import ceil

Message = dict[str, str]

CHARS_PER_TOKEN_EST = 3.5


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
