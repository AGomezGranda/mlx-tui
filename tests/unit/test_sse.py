"""Unit tests for mlx_tui.sse wire-format parsing and token accounting."""

from __future__ import annotations

import pytest

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
    assert token_accounting(
        prompt_tokens=12,
        completion_tokens=6,
        counted_deltas=99,
        user_chars=999,
        elapsed=2.0,
    ) == ("12", "6", 3.0)


def test_token_accounting_estimate_path() -> None:
    assert token_accounting(
        prompt_tokens=None,
        completion_tokens=None,
        counted_deltas=4,
        user_chars=70,
        elapsed=2.0,
    ) == ("20 (est)", "4 (est)", 2.0)


def test_token_accounting_mixed_estimated_prompt_known_completion() -> None:
    assert token_accounting(
        prompt_tokens=None,
        completion_tokens=6,
        counted_deltas=9,
        user_chars=70,
        elapsed=2.0,
    ) == ("20 (est)", "6", 3.0)


def test_token_accounting_zero_elapsed_yields_zero_rate() -> None:
    assert token_accounting(
        prompt_tokens=12,
        completion_tokens=6,
        counted_deltas=9,
        user_chars=70,
        elapsed=0.0,
    ) == ("12", "6", 0.0)
