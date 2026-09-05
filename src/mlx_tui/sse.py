"""SSE parsing for mlx-lm chat streams."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from mlx_tui.history.tokens import estimate_tokens


@dataclass(frozen=True)
class TokenAccounting:
    prompt_tokens: int
    completion_tokens: int
    prompt_estimated: bool
    completion_estimated: bool
    tok_s: float


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
    prompt_estimate: int,
    full_text: str,
    elapsed: float,
) -> TokenAccounting:
    """Resolve typed counts; estimates from complete request/response text."""
    resolved_prompt = prompt_tokens if prompt_tokens is not None else prompt_estimate
    prompt_estimated = prompt_tokens is None
    resolved_completion = (
        completion_tokens
        if completion_tokens is not None
        else estimate_tokens(full_text)
    )
    completion_estimated = completion_tokens is None
    tok_s = float(resolved_completion) / elapsed if elapsed > 0 else 0.0
    return TokenAccounting(
        prompt_tokens=resolved_prompt,
        completion_tokens=resolved_completion,
        prompt_estimated=prompt_estimated,
        completion_estimated=completion_estimated,
        tok_s=tok_s,
    )
