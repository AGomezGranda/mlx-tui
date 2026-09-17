"""SSE parsing for mlx-lm chat streams."""

from __future__ import annotations

from dataclasses import dataclass, field

from mlx_tui.history.tokens import estimate_tokens


@dataclass(frozen=True)
class TokenAccounting:
    prompt_tokens: int
    completion_tokens: int
    prompt_estimated: bool
    completion_estimated: bool
    tok_s: float
    cached_prompt_tokens: int | None = None


@dataclass
class SSEDecoder:
    """Incremental SSE data-event framing.

    Feed split lines; blank line dispatches joined ``data`` fields.
    """

    _buf: list[str] = field(default_factory=list)
    _first: bool = True

    def feed(self, line: str) -> str | None:  # noqa: PLR0911
        """Process one line; return dispatched payload or ``None``."""
        if self._first:
            self._first = False
            if line.startswith("\ufeff"):
                line = line[1:]
        if line.endswith("\r\n"):
            line = line[:-2]
        elif line.endswith("\n") or line.endswith("\r"):
            line = line[:-1]
        if line == "":
            if not self._buf:
                return None
            payload = "\n".join(self._buf)
            self._buf.clear()
            return payload
        if line.startswith(":"):
            return None
        if ":" in line:
            field_name, value = line.split(":", 1)
            if value.startswith(" "):
                value = value[1:]
            if field_name != "data":
                return None
            self._buf.append(value)
            return None
        if line != "data":
            return None
        self._buf.append("")
        return None


def _valid_count(value: object) -> int | None:
    if type(value) is not int:
        return None
    return value if value >= 0 else None


def usage_from_chunk(chunk: object) -> tuple[int | None, int | None]:
    """Extract ``(prompt_tokens, completion_tokens)``; malformed → ``(None, None)``.

    Rejects booleans (``bool`` subclasses ``int``) and negative counts.
    """
    usage = chunk.get("usage") if isinstance(chunk, dict) else None
    if not isinstance(usage, dict):
        return None, None
    return (
        _valid_count(usage.get("prompt_tokens")),
        _valid_count(usage.get("completion_tokens")),
    )


def cached_prompt_from_chunk(
    chunk: object, prompt_tokens: int | None = None
) -> int | None:
    """Server-reported cached prompt tokens, or ``None`` when absent/invalid.

    Inconsistent counts (cached > prompt when prompt is known) are rejected.
    """
    usage = chunk.get("usage") if isinstance(chunk, dict) else None
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        return None
    cached = _valid_count(details.get("cached_tokens"))
    if cached is None:
        return None
    if prompt_tokens is not None and cached > prompt_tokens:
        return None
    return cached


def _first_choice(chunk: object) -> dict[str, object] | None:
    choices = chunk.get("choices") if isinstance(chunk, dict) else None
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    return first if isinstance(first, dict) else None


def _delta_field(chunk: object, key: str) -> str | None:
    first = _first_choice(chunk)
    if first is None:
        return None
    delta = first.get("delta") if isinstance(first, dict) else None
    if not isinstance(delta, dict):
        return None
    value = delta.get(key)
    return value if isinstance(value, str) and value else None


def delta_content_from_chunk(chunk: object) -> str | None:
    """Text content of first choice delta; role-only/empty → ``None``."""
    return _delta_field(chunk, "content")


def delta_reasoning_from_chunk(chunk: object) -> str | None:
    """Pinned-runtime ``delta.reasoning`` text; role-only/empty → ``None``."""
    return _delta_field(chunk, "reasoning")


def delta_tool_fragments_from_chunk(chunk: object) -> list[dict[str, object]]:
    """Indexed tool-call fragments from the first choice delta, unexecuted.

    Matches the captured OpenAI shape
    ``delta.tool_calls[i] = {index, id, type, function: {name, arguments}}``.
    Malformed entries are skipped; argument text is retained verbatim.
    """
    first = _first_choice(chunk)
    if first is None:
        return []
    delta = first.get("delta") if isinstance(first, dict) else None
    if not isinstance(delta, dict):
        return []
    raw_calls = delta.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    out: list[dict[str, object]] = []
    for entry in raw_calls:
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        if type(index) is not int or index < 0:
            continue
        fragment: dict[str, object] = {"index": index}
        for key in ("id", "type"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                fragment[key] = value
        fn = entry.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str) and name:
                fragment["name"] = name
            args = fn.get("arguments")
            if isinstance(args, str) and args:
                fragment["arguments"] = args
        if len(fragment) > 1:
            out.append(fragment)
    return out


def finish_reason_from_chunk(chunk: object) -> str | None:
    """Finish reason of first choice; per-delta ``null`` → ``None``."""
    first = _first_choice(chunk)
    if first is None:
        return None
    reason = first.get("finish_reason")
    return reason if isinstance(reason, str) else None


def token_accounting(  # noqa: PLR0913
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    prompt_estimate: int,
    full_text: str,
    elapsed: float,
    cached_prompt_tokens: int | None = None,
) -> TokenAccounting:
    """Resolve typed counts; estimates from complete request/response text.

    ``elapsed`` is the full client-observed request duration and ``tok_s``
    is the client request rate (all server completion tokens / full
    duration), explicitly not engine decode speed.
    """
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
        cached_prompt_tokens=cached_prompt_tokens,
    )
