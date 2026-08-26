"""Unit tests for mlx_tui.history token estimation and window trimming."""

from __future__ import annotations

from mlx_tui.history import CHARS_PER_TOKEN_EST, estimate_tokens, trim_for_context


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
        estimate_tokens("two") + estimate_tokens("reply-two") + estimate_tokens("three")
    )
    trimmed = trim_for_context(messages, budget)
    assert trimmed == [u("two"), a("reply-two"), u("three")]


def test_trim_boundary_lands_on_user_message() -> None:
    messages = [u("old prompt"), a("old reply"), u("new")]
    # Budget fits only from the assistant reply onward — an illegal boundary —
    # so the window must fall forward to the next user message.
    budget = estimate_tokens("old reply") + estimate_tokens("new")
    trimmed = trim_for_context(messages, budget)
    assert trimmed[0]["role"] == "user"
    assert trimmed == [u("new")]


def test_trim_never_drops_the_newest_message() -> None:
    huge = "x" * 100_000
    messages = [u(huge), u("latest")]
    trimmed = trim_for_context(messages, 1)
    assert trimmed == [u("latest")]


def test_trim_single_huge_message_still_sent() -> None:
    huge = "y" * 100_000
    trimmed = trim_for_context([u(huge)], 1)
    assert trimmed == [u(huge)]
