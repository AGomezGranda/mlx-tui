"""Token estimation + bounded context preparation."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

Message = dict[str, str]

CHARS_PER_TOKEN_EST = 3.5
MESSAGE_OVERHEAD_TOKENS = 4
TEMPLATE_OVERHEAD_TOKENS = 2
RESPONSE_OVERHEAD_TOKENS = 2


class ContextLimitError(ValueError):
    """The complete request cannot fit in the configured context window."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ContextWindow:
    """The retained request messages and its conservative token reservation."""

    messages: tuple[Message, ...]
    input_tokens: int
    reserved_tokens: int


def estimate_tokens(text: str) -> int:
    """Estimate token count as ``chars / 3.5``, matching the stamp heuristic."""
    return ceil(len(text) / CHARS_PER_TOKEN_EST)


def estimate_message_tokens(message: Message) -> int:
    """Estimate one chat message, including role/template framing."""
    return estimate_tokens(message.get("content", "")) + MESSAGE_OVERHEAD_TOKENS


def estimate_prompt_tokens(
    messages: list[Message] | tuple[Message, ...],
    system_prompt: str | None = None,
) -> int:
    """Estimate the complete input prompt, including fixed protocol framing."""
    prompt_tokens = TEMPLATE_OVERHEAD_TOKENS + RESPONSE_OVERHEAD_TOKENS
    if system_prompt:
        prompt_tokens += estimate_message_tokens(
            {"role": "system", "content": system_prompt}
        )
    return prompt_tokens + sum(estimate_message_tokens(message) for message in messages)


def trim_for_context(
    messages: list[Message],
    max_est_tokens: int,
) -> list[Message]:
    """Keep the newest complete user-bound window that fits the message budget."""
    if not messages:
        return []
    newest_tokens = estimate_message_tokens(messages[-1])
    if newest_tokens > max_est_tokens:
        raise ContextLimitError(
            f"newest message needs {newest_tokens} estimated tokens, "
            f"but only {max_est_tokens} message tokens fit"
        )
    total = 0
    keep_from = len(messages) - 1
    for i in range(len(messages) - 1, -1, -1):
        total += estimate_message_tokens(messages[i])
        if messages[i]["role"] == "user" and total <= max_est_tokens:
            keep_from = i
    return list(messages[keep_from:])


def prepare_context(
    messages: list[Message],
    system_prompt: str | None,
    max_ctx: int,
    max_tokens: int,
) -> ContextWindow:
    """Prepare one bounded request, reserving the complete response allowance."""
    framing_tokens = TEMPLATE_OVERHEAD_TOKENS + RESPONSE_OVERHEAD_TOKENS
    if system_prompt:
        framing_tokens += estimate_message_tokens(
            {"role": "system", "content": system_prompt}
        )
    message_budget = max_ctx - max_tokens - framing_tokens
    try:
        retained = trim_for_context(messages, message_budget)
    except ContextLimitError as exc:
        raise ContextLimitError(
            f"context limit: {exc.reason}; max_ctx={max_ctx}, max_tokens={max_tokens}"
        ) from exc

    input_tokens = estimate_prompt_tokens(retained, system_prompt)
    reserved_tokens = input_tokens + max_tokens
    if reserved_tokens > max_ctx:
        raise ContextLimitError(
            f"context limit: request needs {reserved_tokens} estimated tokens "
            f"(input {input_tokens} + max_tokens {max_tokens}) but max_ctx={max_ctx}"
        )

    prepared: tuple[Message, ...] = (
        ({"role": "system", "content": system_prompt},) if system_prompt else ()
    ) + tuple(retained)
    return ContextWindow(
        messages=prepared,
        input_tokens=input_tokens,
        reserved_tokens=reserved_tokens,
    )


def _format_k(n: int) -> str:  # noqa: PLR2004
    if n < 1000:  # noqa: PLR2004
        return str(n)
    s = f"{n / 1000:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return f"{s}k"


def ctx_bar_text(ctx_len: int, max_ctx: int) -> str:
    return f"ctx {_format_k(ctx_len)}/{_format_k(max_ctx)}"


def ctx_bar_style(ctx_len: int, max_ctx: int) -> str:  # noqa: PLR2004
    if max_ctx <= 0:
        return ""
    ratio = ctx_len / max_ctx
    if ratio > 0.95:  # noqa: PLR2004
        return "red"
    if ratio > 0.8:  # noqa: PLR2004
        return "yellow"
    return ""
