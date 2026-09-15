"""Unit tests for mlx_tui.chat streaming client via httpx.MockTransport."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any, override

import httpx
import pytest

from mlx_tui.chat import TurnProgress, error_detail, stream_turn
from tests.builders import sse_frames, sse_multiline_event

SyncHandler = Callable[[httpx.Request], httpx.Response]
AsyncHandler = Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]]
Handler = SyncHandler | AsyncHandler
URL = "http://stub/v1/chat/completions"


def _noop_flush(_text: str) -> None:
    return None


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (500, {"detail": "boom"}, ": boom"),
        (500, {"error": {"message": "nope"}}, ": nope"),
        (500, {"error": "flat"}, ": flat"),
        (500, {"detail": "   "}, ""),
    ],
)
def test_error_detail_extracts_server_explanation(
    status: int, body: object, expected: str
) -> None:
    response = httpx.Response(status, json=body)
    assert error_detail(response) == expected


def test_error_detail_non_json_body_yields_empty() -> None:
    response = httpx.Response(502, text="<html>")
    assert error_detail(response) == ""


def install_transport(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    monkeypatch.setattr(
        httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport)
    )
    # serverctl unit tests reuse this helper but exercise sync httpx.Client;
    # keep both patched so Phase 1 regressions stay green. Async handlers are
    # never used by the sync path.
    try:
        monkeypatch.setattr(httpx, "Client", partial(httpx.Client, transport=transport))
    except Exception:
        pass


def stream_response(body: bytes) -> httpx.Response:
    return httpx.Response(
        200, headers={"Content-Type": "text/event-stream"}, content=body
    )


def _install_clock(monkeypatch: pytest.MonkeyPatch, times: list[float]) -> None:
    it = iter(times)

    def _now() -> float:
        try:
            return next(it)
        except StopIteration:
            return times[-1]

    monkeypatch.setattr("mlx_tui.chat.time.monotonic", _now)


async def test_happy_path_with_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    body = sse_frames(deltas=["Hello", " world", " this"], usage=(12, 6))
    install_transport(monkeypatch, lambda request: stream_response(body))
    _install_clock(monkeypatch, [100.0, 100.5, 100.6, 100.7, 101.0])
    flushes: list[str] = []

    result = await stream_turn(
        URL,
        {"messages": []},
        prompt_estimate=20,
        on_flush=flushes.append,
        flush_interval=0.0,
    )

    assert isinstance(result.full_text, str)
    assert result.full_text == "Hello world this"
    assert result.accounting.prompt_tokens == 12
    assert result.accounting.completion_tokens == 6
    assert result.accounting.prompt_estimated is False
    assert result.accounting.completion_estimated is False
    assert result.first_output_s == pytest.approx(0.5)
    assert result.answer_started_s == pytest.approx(0.6)
    assert result.total_s == pytest.approx(1.0)
    assert result.accounting.tok_s == pytest.approx(6.0)
    assert result.stream_complete is True
    assert result.finish_reason == "stop"
    assert result.skipped_frames == 0
    assert flushes == ["Hello", "Hello world", "Hello world this"]


async def test_reasoning_before_answer_sets_first_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = sse_frames(
        deltas=["answer"], reasoning=["think step"], usage=(12, 6), finish="stop"
    )
    install_transport(monkeypatch, lambda request: stream_response(body))
    _install_clock(monkeypatch, [50.0, 50.2, 50.4, 50.9])
    activities: list[str] = []
    result = await stream_turn(
        URL,
        {"messages": []},
        prompt_estimate=20,
        on_flush=_noop_flush,
        flush_interval=0.0,
        on_activity=activities.append,
    )
    assert result.reasoning_text == "think step"
    assert result.full_text == "answer"
    assert result.first_output_s == pytest.approx(0.2)
    assert result.answer_started_s == pytest.approx(0.4)
    assert result.stream_complete is True
    assert activities and "think step" in activities[0]


async def test_tool_fragments_merge_by_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frags: list[dict[str, object]] = [
        {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "'},
        },
        {
            "index": 0,
            "function": {"arguments": 'Madrid"}'},
        },
    ]
    body = sse_frames(deltas=[], tool_calls=frags, usage=(12, 6), finish="stop")
    install_transport(monkeypatch, lambda request: stream_response(body))
    result = await stream_turn(URL, {}, prompt_estimate=1, on_flush=_noop_flush)
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call["name"] == "get_weather"
    assert call["arguments"] == '{"city": "Madrid"}'
    assert result.full_text == ""
    assert result.stream_complete is False


async def test_empty_output_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    body = sse_frames(finish=None, usage=(12, 0), model="test-model")
    install_transport(monkeypatch, lambda request: stream_response(body))
    result = await stream_turn(URL, {}, prompt_estimate=1, on_flush=_noop_flush)
    assert result.full_text == ""
    assert result.stream_complete is False
    assert result.first_output_s is None
    assert result.answer_started_s is None


async def test_missing_done_is_premature_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    body = sse_frames(deltas=["Hi"], finish="stop", done=False)
    install_transport(monkeypatch, lambda request: stream_response(body))
    result = await stream_turn(URL, {}, prompt_estimate=1, on_flush=_noop_flush)
    assert result.full_text == "Hi"
    assert result.stream_complete is False


async def test_invalid_usage_rejected_and_cached_inconsistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = sse_frames(
        deltas=["Hi"],
        raw_usage={
            "prompt_tokens": 10,
            "completion_tokens": True,
            "prompt_tokens_details": {"cached_tokens": 999},
        },
    )
    install_transport(monkeypatch, lambda request: stream_response(body))
    result = await stream_turn(URL, {}, prompt_estimate=7, on_flush=_noop_flush)
    assert result.accounting.prompt_tokens == 10
    assert result.accounting.prompt_estimated is False
    assert result.accounting.completion_estimated is True
    assert result.cached_prompt_tokens is None
    assert result.stream_complete is True


async def test_cached_usage_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    body = sse_frames(deltas=["Hi"], usage=(110, 5), cached=109)
    install_transport(monkeypatch, lambda request: stream_response(body))
    result = await stream_turn(URL, {}, prompt_estimate=1, on_flush=_noop_flush)
    assert result.cached_prompt_tokens == 109
    assert result.accounting.cached_prompt_tokens == 109


async def test_estimate_fallback_without_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    body = sse_frames(deltas=["a", "b", "c", "d"], finish=None)
    install_transport(monkeypatch, lambda request: stream_response(body))

    result = await stream_turn(
        URL, {"messages": []}, prompt_estimate=20, on_flush=_noop_flush
    )

    from mlx_tui.history.tokens import estimate_tokens  # noqa: PLC0415

    assert result.full_text == "abcd"
    assert result.accounting.prompt_tokens == 20
    assert result.accounting.prompt_estimated is True
    assert result.accounting.completion_tokens == estimate_tokens("abcd")
    assert result.accounting.completion_estimated is True
    assert result.accounting.completion_tokens > 1


async def test_skips_malformed_keepalive_and_stops_at_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = sse_frames(deltas=["A"], finish=None, malformed=True, keepalive=(3, 10))
    install_transport(monkeypatch, lambda request: stream_response(body))

    result = await stream_turn(URL, {}, prompt_estimate=1, on_flush=_noop_flush)

    assert result.full_text == "A"
    assert result.skipped_frames == 1


async def test_no_space_data_stream_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    body = sse_frames(deltas=["Hello", " world"], usage=(12, 6), no_space=True)
    install_transport(monkeypatch, lambda request: stream_response(body))

    result = await stream_turn(URL, {}, prompt_estimate=20, on_flush=_noop_flush)

    assert result.full_text == "Hello world"
    assert result.skipped_frames == 0


async def test_multiline_json_event_joins_with_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk: dict[str, object] = {
        "choices": [{"delta": {"content": "Hi"}, "finish_reason": None}]
    }
    body = (
        b'data: {"choices": [{"delta": {"role": "assistant"}, "finish_reason": null}]}\n\n'
        + sse_multiline_event(chunk)
        + b"data: [DONE]\n\n"
    )
    install_transport(monkeypatch, lambda request: stream_response(body))

    result = await stream_turn(URL, {}, prompt_estimate=1, on_flush=_noop_flush)

    assert result.full_text == "Hi"
    assert result.skipped_frames == 0


async def test_length_finish_reason_is_surfaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = sse_frames(deltas=["Partial ans"], finish="length", usage=(9, 3))
    install_transport(monkeypatch, lambda request: stream_response(body))

    result = await stream_turn(
        URL, {"messages": []}, prompt_estimate=9, on_flush=_noop_flush
    )

    assert result.full_text == "Partial ans"
    assert result.finish_reason == "length"


async def test_exceptions_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    install_transport(monkeypatch, boom)

    with pytest.raises(httpx.ConnectError):
        await stream_turn(URL, {}, prompt_estimate=0, on_flush=_noop_flush)


async def test_http_error_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            500,
            headers={"Content-Type": "application/json"},
            content=b'{"detail": "model load failed"}',
        ),
    )

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await stream_turn(URL, {}, prompt_estimate=0, on_flush=_noop_flush)

    # The body must survive the closed stream context so the UI can show
    # the server's explanation, not just the status code.
    assert excinfo.value.response.json() == {"detail": "model load failed"}


async def test_remote_protocol_error_mid_stream_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def body():  # type: ignore[no-untyped-def]
        yield sse_frames(deltas=["Hel"], finish=None, done=False)
        raise httpx.RemoteProtocolError("peer died mid-stream")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, content=body()
        )

    install_transport(monkeypatch, handler)

    with pytest.raises(httpx.RemoteProtocolError):
        await stream_turn(URL, {}, prompt_estimate=0, on_flush=_noop_flush)


async def test_stalled_transport_cancellation_closes_within_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = asyncio.Event()
    entered = asyncio.Event()

    class _Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            entered.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            yield b"data: [DONE]\n\n"

        @override
        async def aclose(self) -> None:
            closed.set()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=_Stalled(),
        )

    install_transport(monkeypatch, handler)
    task = asyncio.create_task(
        stream_turn(URL, {}, prompt_estimate=1, on_flush=_noop_flush)
    )
    assert await asyncio.wait_for(entered.wait(), timeout=2)
    start = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=2)
    assert time.monotonic() - start < 2
    assert await asyncio.wait_for(closed.wait(), timeout=2)


async def test_cancel_before_headers_closes_within_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.sleep(30)
        return stream_response(b"data: [DONE]\n\n")

    install_transport(monkeypatch, handler)
    task = asyncio.create_task(
        stream_turn(URL, {}, prompt_estimate=1, on_flush=_noop_flush)
    )
    assert await asyncio.wait_for(entered.wait(), timeout=2)
    start = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=2)
    assert time.monotonic() - start < 2


async def test_progress_deltas_carry_answer_reasoning_tools_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frags: list[dict[str, object]] = [
        {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "'},
        },
        {"index": 0, "function": {"arguments": 'Madrid"}'}},
    ]
    body = sse_frames(
        deltas=["Hello", " world"],
        reasoning=["think "],
        tool_calls=frags,
        usage=(12, 6),
        model="resp-model",
    )
    install_transport(monkeypatch, lambda request: stream_response(body))
    seen: list[TurnProgress] = []
    result = await stream_turn(
        URL, {}, prompt_estimate=1, on_flush=_noop_flush, on_progress=seen.append
    )
    answers = "".join(p.answer_delta for p in seen)
    reasoning = "".join(p.reasoning_delta for p in seen)
    assert "Hello" in answers and "world" in answers
    assert "think" in reasoning
    assert any(p.tool_fragments for p in seen)
    assert any(p.response_model == "resp-model" for p in seen)
    # Final success still comes only from TurnResult, never from progress.
    assert result.full_text == "Hello world"
    assert result.reasoning_text == "think "
    assert result.response_model == "resp-model"
    assert result.stream_complete is True


async def test_progress_survives_cancellation_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = sse_frames(deltas=["partial"], finish=None, done=False, model="m")
    install_transport(monkeypatch, lambda request: stream_response(body))
    seen: list[TurnProgress] = []
    result = await stream_turn(
        URL, {}, prompt_estimate=1, on_flush=_noop_flush, on_progress=seen.append
    )
    assert "".join(p.answer_delta for p in seen) == "partial"
    assert result.stream_complete is False

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    install_transport(monkeypatch, boom)
    seen.clear()
    with pytest.raises(httpx.ConnectError):
        await stream_turn(
            URL, {}, prompt_estimate=0, on_flush=_noop_flush, on_progress=seen.append
        )
    assert seen == []
