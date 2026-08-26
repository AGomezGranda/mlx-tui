"""SSE wire-format parsing and token accounting for OpenAI-style chat streams.

mlx-lm streams each chat-completion chunk as an SSE ``data: {json}`` frame and
ends the stream with a ``data: [DONE]`` sentinel. During long prompt
processing it also emits keepalive comment frames of the form
``": keepalive {processed}/{total}"`` (see upstream ``mlx_lm/server.py``);
those lines carry no ``data: `` prefix, which is exactly why every line that
is empty or lacks the prefix is skipped below rather than treated as data.

Note this parser accepts only the ``data: ``-with-trailing-space form mlx-lm
emits; the SSE spec also permits spaceless ``data:{json}``, so "OpenAI-style"
here means mlx-lm-compatible, not full SSE-spec compliance.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator


def iter_sse_data(lines: Iterable[str]) -> Iterator[str]:
    """Yield the JSON payload string of each ``data: `` frame.

    Iteration stops at the ``[DONE]`` sentinel; frames after it are ignored,
    as are lines that are empty or lack the ``data: `` prefix (keepalives).
    """
    for raw_line in lines:
        line = raw_line.strip()
        if not line.startswith("data: "):
            continue
        data = line[len("data: ") :]
        if data == "[DONE]":
            break
        yield data


def usage_from_chunk(chunk: object) -> tuple[int | None, int | None]:
    """Extract ``(prompt_tokens, completion_tokens)`` from a usage chunk.

    Defensive ``isinstance`` narrowing throughout: malformed frames degrade to
    ``(None, None)`` instead of raising, so one bad frame never kills a turn.
    """
    usage = chunk.get("usage") if isinstance(chunk, dict) else None
    if not isinstance(usage, dict):
        return None, None
    pt = usage.get("prompt_tokens")
    ct = usage.get("completion_tokens")
    return (
        pt if isinstance(pt, int) else None,
        ct if isinstance(ct, int) else None,
    )


def delta_content_from_chunk(chunk: object) -> str | None:
    """Return the text content carried by a chunk's first choice delta.

    Role-only first deltas (the stream opener) and empty strings yield
    ``None`` — only real text counts as content.
    """
    choices = chunk.get("choices") if isinstance(chunk, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    delta = first.get("delta") if isinstance(first, dict) else None
    content = delta.get("content") if isinstance(delta, dict) else None
    if isinstance(content, str) and content:
        return content
    return None


def finish_reason_from_chunk(chunk: object) -> str | None:
    """Return the finish reason carried by a chunk's first choice, if any.

    Per-delta frames carry ``finish_reason: null`` and yield ``None``; only
    the terminal frame (``"stop"``, ``"length"``, …) yields a value. A
    ``"length"`` reason is how the server reports the reply hit the
    max-tokens cap mid-sentence.
    """
    choices = chunk.get("choices") if isinstance(chunk, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
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
    """Compute the ``(in-str, out-str, tok-s)`` stamp values for a turn.

    When server usage is missing, counts are estimated from what was observed:
    output from the number of text deltas received, input from the heuristic
    ``user_chars / 3.5``. The ``(est)`` label is attached to the number only.
    ``tok_s`` is ``0.0`` when ``elapsed <= 0`` to avoid dividing by zero.
    """
    if completion_tokens is not None:
        tok_out_str = str(completion_tokens)
        rate = float(completion_tokens)
    else:
        tok_out_str = f"{counted_deltas} (est)"
        rate = float(counted_deltas)
    tok_in_str = (
        str(prompt_tokens)
        if prompt_tokens is not None
        else f"{user_chars / 3.5:.0f} (est)"
    )
    tok_s = rate / elapsed if elapsed > 0 else 0.0
    return tok_in_str, tok_out_str, tok_s
