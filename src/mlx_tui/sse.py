"""SSE parsing for mlx-lm chat streams."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from mlx_tui.history.tokens import CHARS_PER_TOKEN_EST


def iter_sse_data(lines: Iterable[str]) -> Iterator[str]:
    """Yield JSON payload of each ``data: `` frame, stop at ``[DONE]``."""
    for raw_line in lines:
        line = raw_line.strip()
        if not line.startswith("data: "):
            continue
        data = line[len("data: ") :]
        if data == "[DONE]":
            break
        yield data


def usage_from_chunk(chunk: object) -> tuple[int | None, int | None]:
    """Extract ``(prompt_tokens, completion_tokens)``; malformed → ``(None, None)``."""
    usage = chunk.get("usage") if isinstance(chunk, dict) else None
    if not isinstance(usage, dict):
        return None, None
    pt = usage.get("prompt_tokens")
    ct = usage.get("completion_tokens")
    return (
        pt if isinstance(pt, int) else None,
        ct if isinstance(ct, int) else None,
    )


def _first_choice(chunk: object) -> dict[str, object] | None:
    choices = chunk.get("choices") if isinstance(chunk, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    return first if isinstance(first, dict) else None


def delta_content_from_chunk(chunk: object) -> str | None:
    """Text content of first choice delta; role-only/empty → ``None``."""
    first = _first_choice(chunk)
    if first is None:
        return None
    delta = first.get("delta") if isinstance(first, dict) else None
    content = delta.get("content") if isinstance(delta, dict) else None
    return content if isinstance(content, str) and content else None


def finish_reason_from_chunk(chunk: object) -> str | None:
    """Finish reason of first choice; per-delta ``null`` → ``None``."""
    first = _first_choice(chunk)
    if first is None:
        return None
    reason = first.get("finish_reason")
    return reason if isinstance(reason, str) else None


def token_accounting(
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    counted_deltas: int,
    user_chars: int,
    elapsed: float,
) -> tuple[str, str, float]:
    """Compute ``(in-str, out-str, tok/s)``; estimates when usage missing."""
    if completion_tokens is not None:
        tok_out_str = str(completion_tokens)
        rate = float(completion_tokens)
    else:
        tok_out_str = f"{counted_deltas} (est)"
        rate = float(counted_deltas)
    tok_in_str = (
        str(prompt_tokens)
        if prompt_tokens is not None
        else f"{user_chars / CHARS_PER_TOKEN_EST:.0f} (est)"
    )
    tok_s = rate / elapsed if elapsed > 0 else 0.0
    return tok_in_str, tok_out_str, tok_s
