"""Unit tests for mlx_tui.sse wire-format parsing and token accounting."""

from __future__ import annotations

import pytest

from mlx_tui.history.tokens import estimate_tokens
from mlx_tui.sse import (
    delta_content_from_chunk,
    finish_reason_from_chunk,
    iter_sse_data,
    token_accounting,
    usage_from_chunk,
)


def test_iter_sse_data_stops_at_done() -> None:
    lines = ['data: {"a":1}\n', "data: [DONE]\n"]
    assert list(iter_sse_data(lines)) == ['{"a":1}']


def test_iter_sse_data_skips_empty_keepalive_and_non_data_lines() -> None:
    lines = ["", "\n", ": keepalive 3/10\n", "event: ping\n", "data: x\n"]
    assert list(iter_sse_data(lines)) == ["x"]


def test_iter_sse_data_ignores_frames_after_done() -> None:
    lines = ["data: a\n", "data: [DONE]\n", "data: late\n"]
    assert list(iter_sse_data(lines)) == ["a"]


def test_iter_sse_data_tolerates_surrounding_whitespace() -> None:
    assert list(iter_sse_data(["  data: y  \n"])) == ["y"]


@pytest.mark.parametrize(
    ("chunk", "expected"),
    [
        (
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 6}},
            (12, 6),
        ),
        ({}, (None, None)),
        ({"usage": None}, (None, None)),
        (
            {"usage": {"prompt_tokens": "x", "completion_tokens": 6}},
            (None, 6),
        ),
        ("junk", (None, None)),
    ],
)
def test_usage_from_chunk(
    chunk: object, expected: tuple[int | None, int | None]
) -> None:
    assert usage_from_chunk(chunk) == expected


@pytest.mark.parametrize(
    ("chunk", "expected"),
    [
        ({"choices": [{"delta": {"content": "Hi"}}]}, "Hi"),
        ({"choices": [{"delta": {"content": ""}}]}, None),
        ({"choices": [{"delta": {"role": "assistant"}}]}, None),
        ({"choices": []}, None),
        ({"choices": [{"delta": {}, "finish_reason": "stop"}]}, None),
        ("junk", None),
    ],
)
def test_delta_content_from_chunk(chunk: object, expected: str | None) -> None:
    assert delta_content_from_chunk(chunk) == expected


@pytest.mark.parametrize(
    ("chunk", "expected"),
    [
        ({"choices": [{"delta": {}, "finish_reason": "stop"}]}, "stop"),
        ({"choices": [{"delta": {}, "finish_reason": "length"}]}, "length"),
        ({"choices": [{"delta": {"content": "x"}, "finish_reason": None}]}, None),
        ({"choices": []}, None),
        ({"choices": ["junk"]}, None),
        ({}, None),
        ("junk", None),
    ],
)
def test_finish_reason_from_chunk(chunk: object, expected: str | None) -> None:
    assert finish_reason_from_chunk(chunk) == expected


def test_token_accounting_with_usage() -> None:
    acct = token_accounting(
        prompt_tokens=12,
        completion_tokens=6,
        prompt_estimate=999,
        full_text="x" * 10_000,
        elapsed=2.0,
    )
    assert acct.prompt_tokens == 12
    assert acct.completion_tokens == 6
    assert acct.prompt_estimated is False
    assert acct.completion_estimated is False
    assert acct.tok_s == 3.0


def test_token_accounting_estimate_path() -> None:
    acct = token_accounting(
        prompt_tokens=None,
        completion_tokens=None,
        prompt_estimate=20,
        full_text="abcd",
        elapsed=2.0,
    )
    assert acct.prompt_tokens == 20
    assert acct.prompt_estimated is True
    assert acct.completion_tokens == estimate_tokens("abcd")
    assert acct.completion_estimated is True
    assert acct.tok_s == estimate_tokens("abcd") / 2.0


def test_token_accounting_mixed_estimated_prompt_known_completion() -> None:
    acct = token_accounting(
        prompt_tokens=None,
        completion_tokens=6,
        prompt_estimate=20,
        full_text="abcd",
        elapsed=2.0,
    )
    assert acct.prompt_tokens == 20
    assert acct.prompt_estimated is True
    assert acct.completion_tokens == 6
    assert acct.completion_estimated is False
    assert acct.tok_s == 3.0


def test_token_accounting_zero_elapsed_yields_zero_rate() -> None:
    acct = token_accounting(
        prompt_tokens=12,
        completion_tokens=6,
        prompt_estimate=20,
        full_text="abcd",
        elapsed=0.0,
    )
    assert acct.prompt_tokens == 12
    assert acct.completion_tokens == 6
    assert acct.tok_s == 0.0


def test_token_accounting_single_long_delta() -> None:
    full_text = "x" * 350
    acct = token_accounting(
        prompt_tokens=None,
        completion_tokens=None,
        prompt_estimate=10,
        full_text=full_text,
        elapsed=2.0,
    )
    assert acct.completion_tokens == estimate_tokens(full_text)
    assert acct.completion_tokens > 1
    assert acct.completion_estimated is True
    assert acct.prompt_estimated is True
    assert acct.tok_s > 0


def test_token_accounting_frame_count_independent() -> None:
    full_text = "Hello world, this is a longer response for estimation."
    single = token_accounting(
        prompt_tokens=None,
        completion_tokens=None,
        prompt_estimate=10,
        full_text=full_text,
        elapsed=2.0,
    )
    split = token_accounting(
        prompt_tokens=None,
        completion_tokens=None,
        prompt_estimate=10,
        full_text="".join(
            ["Hello ", "world, ", "this is ", "a longer response ", "for estimation."]
        ),
        elapsed=2.0,
    )
    assert single.completion_tokens == split.completion_tokens
    assert single.completion_tokens == estimate_tokens(full_text)
    assert single.tok_s == split.tok_s
