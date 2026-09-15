"""Unit tests for token estimation, context trimming, and context bars."""

from __future__ import annotations

import pytest

from mlx_tui.history.tokens import (
    CHARS_PER_TOKEN_EST,
    ContextLimitError,
    ctx_bar_style,
    ctx_bar_text,
    estimate_message_tokens,
    estimate_prompt_tokens,
    estimate_tokens,
    prepare_context,
    trim_for_context,
)


def u(text: str) -> dict[str, str]:
    return {"role": "user", "content": text}


def a(text: str) -> dict[str, str]:
    return {"role": "assistant", "content": text}


def test_chars_per_token_constant_is_pinned() -> None:
    # Both history.py and sse.py depend on this value's stability.
    assert CHARS_PER_TOKEN_EST == 3.5


def test_estimate_tokens_matches_chars_over_three_point_five() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("ab") == 1
    assert estimate_tokens("abcd") == 2  # ceil(4/3.5)


def test_trim_returns_empty_for_empty_history() -> None:
    assert trim_for_context([], 100) == []


def test_trim_keeps_everything_when_budget_allows() -> None:
    messages = [u("hi"), a("hello"), u("more")]
    assert trim_for_context(messages, 10_000) == messages


def test_trim_drops_oldest_turns_first() -> None:
    messages = [u("one"), a("reply-one"), u("two"), a("reply-two"), u("three")]
    # Budget fits "two"/"reply-two"/"three" but not turn one.
    budget = (
        estimate_message_tokens(u("two"))
        + estimate_message_tokens(a("reply-two"))
        + estimate_message_tokens(u("three"))
    )
    trimmed = trim_for_context(messages, budget)
    assert trimmed == [u("two"), a("reply-two"), u("three")]


def test_trim_boundary_lands_on_user_message() -> None:
    messages = [u("old prompt"), a("old reply"), u("new")]
    # Budget fits only from the assistant reply onward — an illegal boundary —
    # so the window must fall forward to the next user message.
    budget = estimate_message_tokens(a("old reply")) + estimate_message_tokens(u("new"))
    trimmed = trim_for_context(messages, budget)
    assert trimmed[0]["role"] == "user"
    assert trimmed == [u("new")]


def test_trim_rejects_newest_message_that_cannot_fit() -> None:
    newest = u("latest")
    with pytest.raises(ContextLimitError, match="newest message"):
        trim_for_context([newest], estimate_message_tokens(newest) - 1)


def test_prepare_context_rejects_oversize_newest() -> None:
    huge = "y" * 100_000
    with pytest.raises(ContextLimitError) as exc_info:
        prepare_context([u(huge)], None, max_ctx=1024, max_tokens=256)
    assert "newest message" in exc_info.value.reason
    assert "max_ctx=1024" in exc_info.value.reason


def test_estimate_prompt_includes_system_and_framing_overhead() -> None:
    messages = [u("hello")]
    system = "You are concise."
    assert estimate_prompt_tokens(messages, system) == (
        estimate_prompt_tokens(messages)
        + estimate_message_tokens({"role": "system", "content": system})
    )


def test_prepare_context_reserves_max_tokens() -> None:
    messages = [u("hello"), a("reply")]
    input_tokens = estimate_prompt_tokens(messages)
    window = prepare_context(
        messages,
        None,
        max_ctx=input_tokens + 128,
        max_tokens=128,
    )
    assert window.input_tokens == input_tokens
    assert window.reserved_tokens == input_tokens + 128
    assert window.reserved_tokens <= input_tokens + 128


def test_prepare_context_trims_complete_turns_and_honors_boundary() -> None:
    messages = [u("old"), a("old reply"), u("new")]
    retained = [u("new")]
    input_tokens = estimate_prompt_tokens(retained)
    window = prepare_context(
        messages,
        None,
        max_ctx=input_tokens + 1,
        max_tokens=1,
    )
    assert window.messages == tuple(retained)


def test_ctx_bar_text_formats_k() -> None:
    assert ctx_bar_text(9200, 32000) == "ctx 9.2k/32k est"
    assert ctx_bar_text(800, 8000) == "ctx 800/8k est"
    assert ctx_bar_text(0, 8000) == "ctx 0/8k est"
    assert ctx_bar_text(1000, 1000) == "ctx 1k/1k est"


def test_ctx_bar_text_shows_excluded() -> None:
    assert ctx_bar_text(800, 8000, excluded=2) == "ctx 800/8k est · 2 excl"
    assert ctx_bar_text(800, 8000, excluded=0) == "ctx 800/8k est"


def test_prepare_context_reports_excluded() -> None:
    messages = [u("old"), a("old reply"), u("new")]
    retained = [u("new")]
    input_tokens = estimate_prompt_tokens(retained)
    window = prepare_context(
        messages,
        None,
        max_ctx=input_tokens + 1,
        max_tokens=1,
    )
    assert window.excluded_turns == len(messages) - len(retained)
    assert window.excluded_turns == 2


def test_ctx_bar_style_thresholds() -> None:
    assert ctx_bar_style(0, 8000) == ""
    assert ctx_bar_style(6400, 8000) == ""  # 80% exact not amber
    assert ctx_bar_style(6401, 8000) == "yellow"  # >80% amber
    assert ctx_bar_style(7600, 8000) == "yellow"  # 95% still yellow
    assert ctx_bar_style(7601, 8000) == "red"  # >95% red
    assert ctx_bar_style(8000, 8000) == "red"
