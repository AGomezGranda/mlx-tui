"""UI-free context-window management over the in-memory transcript.

No tokenizer dependency (deliberate, same stance as the stamp heuristic):
budgets are estimated at ``chars / 3.5`` tokens. When a real ``ctx n/limit``
reading arrives from the server side later, only :func:`estimate_tokens` and
the budget constant move — the trimming shape stays.
"""

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
    """Return the newest window of ``messages`` fitting ``max_est_tokens``.

    Windows may only start on a user message: a window opening mid-turn (say,
    on an assistant reply whose user prompt got trimmed) sends the server a
    role sequence chat templates reject, so cut boundaries are user-message
    indices exclusively. The newest message is always kept even when it alone
    exceeds the budget — answering the current prompt outranks history.
    """
    if not messages:
        return []
    suffix = [0] * (len(messages) + 1)
    for i in range(len(messages) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + estimate_tokens(messages[i]["content"])
    keep_from = len(messages) - 1
    for i in range(len(messages)):
        if messages[i]["role"] == "user" and suffix[i] <= max_est_tokens:
            keep_from = i
            break  # earliest fitting boundary = most history kept
    return list(messages[keep_from:])
