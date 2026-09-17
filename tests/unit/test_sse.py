"""Unit tests for mlx_tui.sse wire-format parsing and token accounting."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

import pytest

from mlx_tui.history.tokens import estimate_tokens
from mlx_tui.sse import (
    SSEDecoder,
    cached_prompt_from_chunk,
    delta_content_from_chunk,
    delta_reasoning_from_chunk,
    delta_tool_fragments_from_chunk,
    finish_reason_from_chunk,
    token_accounting,
    usage_from_chunk,
)

DONE_SEEN = "done_seen"
CLEAN_EOF = "eof"
TRUNCATED = "truncated"


def collect_sse(lines: Iterable[str]) -> tuple[list[str], str]:
    decoder = SSEDecoder()
    payloads: list[str] = []
    for raw_line in lines:
        payload = decoder.feed(raw_line)
        if payload is None:
            continue
        if payload == "[DONE]":
            return payloads, DONE_SEEN
        payloads.append(payload)
    if decoder._buf:
        return payloads, TRUNCATED
    return payloads, CLEAN_EOF


async def _collect_async(lines: list[str]) -> list[str]:
    async def gen() -> AsyncIterator[str]:
        for line in lines:
            yield line

    decoder = SSEDecoder()
    out: list[str] = []
    async for raw_line in gen():
        payload = decoder.feed(raw_line)
        if payload is None:
            continue
        if payload == "[DONE]":
            break
        out.append(payload)
    return out


def test_decoder_dispatches_only_on_blank_line() -> None:
    decoder = SSEDecoder()
    assert decoder.feed("data: a") is None
    assert decoder.feed("data: b") is None
    assert decoder.feed("") == "a\nb"


def test_decoder_stops_at_done() -> None:
    lines = ['data: {"a":1}\n', "\n", "data: [DONE]\n", "\n"]
    assert collect_sse(lines)[0] == ['{"a":1}']


def test_decoder_skips_comments_and_non_data_fields() -> None:
    lines = [
        ": keepalive 3/10\n",
        "\n",
        "event: ping\n",
        "id: 1\n",
        "retry: 100\n",
        "unknown: x\n",
        "data: x\n",
        "\n",
    ]
    assert collect_sse(lines)[0] == ["x"]


def test_decoder_ignores_events_after_done() -> None:
    lines = ["data: a\n", "\n", "data: [DONE]\n", "\n", "data: late\n", "\n"]
    assert collect_sse(lines)[0] == ["a"]


def test_decoder_rejects_indented_field() -> None:
    assert collect_sse(["  data: y  \n", "\n"])[0] == []


def test_decoder_no_space_field() -> None:
    assert collect_sse(["data:x\n", "\n"])[0] == ["x"]
    assert collect_sse(["data:\n", "\n"])[0] == [""]


def test_decoder_joins_multiple_data_fields() -> None:
    assert collect_sse(["data: a\n", "data: b\n", "\n"])[0] == ["a\nb"]


def test_decoder_empty_data_event_yields_empty() -> None:
    assert collect_sse(["data:\n", "\n"])[0] == [""]
    assert collect_sse(["data\n", "\n"])[0] == [""]


def test_decoder_ignores_event_without_data() -> None:
    assert collect_sse(["event: ping\n", "\n"])[0] == []
    assert collect_sse(["\n"])[0] == []


def test_decoder_strips_bom_once() -> None:
    assert collect_sse(["\ufeffdata: x\n", "\n"])[0] == ["x"]


def test_decoder_preserves_payload_spaces() -> None:
    assert collect_sse(["data:  x  \n", "\n"])[0] == [" x  "]


def test_decoder_normalizes_cr_lf() -> None:
    assert collect_sse(["data: a\r\n", "\r\n"])[0] == ["a"]
    assert collect_sse(["data: a\r", "\r"])[0] == ["a"]
    assert collect_sse(["data: a\n", "\n"])[0] == ["a"]


def test_decoder_discards_unfinished_at_eof() -> None:
    assert collect_sse(["data: incomplete\n"])[0] == []
    assert collect_sse(["data: a\n", "data: b\n"])[0] == []


def test_decoder_similar_option_names_ignored() -> None:
    assert collect_sse(["data-path: x\n", "\n"])[0] == []


@pytest.mark.parametrize(
    "lines",
    [
        ["data: a\n", "\n", "data: [DONE]\n", "\n", "data: late\n", "\n"],
        ["data:a\n", "\n"],
        ["data: a\n", "data: b\n", "\n"],
        ["data:\n", "\n"],
        [": comment\n", "event: x\n", "data: x\n", "\n"],
        ["\ufeffdata: x\n", "\n"],
        ["data:  x  \n", "\n"],
        ["data: incomplete\n"],
    ],
)
async def test_aiter_matches_collector(lines: list[str]) -> None:
    assert await _collect_async(lines) == collect_sse(lines)[0]


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
        (
            {"usage": {"prompt_tokens": True, "completion_tokens": 6}},
            (None, 6),
        ),
        (
            {"usage": {"prompt_tokens": 12, "completion_tokens": False}},
            (12, None),
        ),
        (
            {"usage": {"prompt_tokens": -1, "completion_tokens": 6}},
            (None, 6),
        ),
        (
            {"usage": {"prompt_tokens": 12, "completion_tokens": -5}},
            (12, None),
        ),
        ("junk", (None, None)),
    ],
)
def test_usage_from_chunk(
    chunk: object, expected: tuple[int | None, int | None]
) -> None:
    assert usage_from_chunk(chunk) == expected


def test_cached_prompt_valid_and_rejections() -> None:
    ok = {
        "usage": {
            "prompt_tokens": 110,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 109},
        }
    }
    assert cached_prompt_from_chunk(ok, 110) == 109
    assert cached_prompt_from_chunk(ok) == 109
    inconsistent = {
        "usage": {
            "prompt_tokens": 10,
            "prompt_tokens_details": {"cached_tokens": 11},
        }
    }
    assert cached_prompt_from_chunk(inconsistent, 10) is None
    assert (
        cached_prompt_from_chunk(
            {"usage": {"prompt_tokens_details": {"cached_tokens": True}}}
        )
        is None
    )
    assert (
        cached_prompt_from_chunk(
            {"usage": {"prompt_tokens_details": {"cached_tokens": -1}}}
        )
        is None
    )
    assert cached_prompt_from_chunk({}) is None
    assert cached_prompt_from_chunk("junk") is None


def test_delta_reasoning_captured_shape() -> None:
    chunk = {"choices": [{"delta": {"reasoning": "think step"}, "finish_reason": None}]}
    assert delta_reasoning_from_chunk(chunk) == "think step"
    assert delta_reasoning_from_chunk({"choices": [{"delta": {}}]}) is None
    assert (
        delta_reasoning_from_chunk({"choices": [{"delta": {"reasoning": ""}}]}) is None
    )
    assert delta_reasoning_from_chunk({"choices": []}) is None


def test_delta_tool_fragments_captured_shape() -> None:
    chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"ci'},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }
    frags = delta_tool_fragments_from_chunk(chunk)
    assert frags == [
        {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "name": "get_weather",
            "arguments": '{"ci',
        }
    ]
    assert delta_tool_fragments_from_chunk({"choices": [{"delta": {}}]}) == []
    assert delta_tool_fragments_from_chunk({"choices": []}) == []
    bad = {"choices": [{"delta": {"tool_calls": [{"index": True}]}}]}
    assert delta_tool_fragments_from_chunk(bad) == []


def test_collect_sse_terminals() -> None:
    payloads, terminal = collect_sse(['data: {"a":1}\n', "\n", "data: [DONE]\n", "\n"])
    assert payloads == ['{"a":1}']
    assert terminal == DONE_SEEN
    payloads, terminal = collect_sse(['data: {"a":1}\n', "\n"])
    assert payloads == ['{"a":1}']
    assert terminal == CLEAN_EOF
    payloads, terminal = collect_sse(["data: incomplete\n"])
    assert payloads == []
    assert terminal == TRUNCATED


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


def test_fallback_output_scales_with_response_length() -> None:
    from mlx_tui.sse import token_accounting  # noqa: PLC0415

    long_text = "x" * 350
    acct = token_accounting(
        prompt_tokens=None,
        completion_tokens=None,
        prompt_estimate=10,
        full_text=long_text,
        elapsed=1.0,
    )
    assert acct.completion_tokens == estimate_tokens(long_text)
    assert acct.completion_tokens > 1
    assert acct.completion_estimated is True
