"""Unit tests for mlx_tui.chat streaming client via httpx.MockTransport."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from functools import partial

import httpx
import pytest

from mlx_tui.chat import error_detail, stream_turn
from tests.builders import SseStreamBuilder

Handler = Callable[[httpx.Request], httpx.Response]
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
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "Client", partial(httpx.Client, transport=transport))


def stream_response(body: bytes) -> httpx.Response:
    return httpx.Response(
        200, headers={"Content-Type": "text/event-stream"}, content=body
    )


def test_happy_path_with_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        SseStreamBuilder()
        .role_frame()
        .delta("Hello")
        .delta(" world")
        .delta(" this")
        .finish_frame()
        .usage(12, 6)
        .done()
        .build()
    )
    install_transport(monkeypatch, lambda request: stream_response(body))
    flushes: list[str] = []

    result = stream_turn(
        URL,
        {"messages": []},
        user_chars=2,
        on_flush=flushes.append,
        flush_interval=0.0,
    )

    assert isinstance(result.full_text, str)
    assert result.full_text == "Hello world this"
    assert result.tok_in_str == "12"
    assert result.tok_out_str == "6"
    assert 0 <= result.ttft
    assert result.tok_s > 0
    assert result.finish_reason == "stop"
    assert result.skipped_frames == 0
    assert flushes == ["Hello", "Hello world", "Hello world this"]


def test_estimate_fallback_without_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        SseStreamBuilder()
        .role_frame()
        .delta("a")
        .delta("b")
        .delta("c")
        .delta("d")
        .done()
        .build()
    )
    install_transport(monkeypatch, lambda request: stream_response(body))

    result = stream_turn(URL, {"messages": []}, user_chars=70, on_flush=_noop_flush)

    assert result.full_text == "abcd"
    assert result.tok_in_str == "20 (est)"
    assert result.tok_out_str == "4 (est)"


def test_skips_malformed_keepalive_and_stops_at_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = (
        SseStreamBuilder()
        .malformed()
        .keepalive(3, 10)
        .role_frame()
        .delta("A")
        .done()
        .delta("LATE")
        .build()
    )
    install_transport(monkeypatch, lambda request: stream_response(body))

    result = stream_turn(URL, {}, user_chars=1, on_flush=_noop_flush)

    assert result.full_text == "A"
    assert result.skipped_frames == 1


def test_length_finish_reason_is_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    body = (
        SseStreamBuilder()
        .role_frame()
        .delta("Partial ans")
        .finish_frame(reason="length")
        .usage(9, 3)
        .done()
        .build()
    )
    install_transport(monkeypatch, lambda request: stream_response(body))

    result = stream_turn(URL, {"messages": []}, user_chars=2, on_flush=_noop_flush)

    assert result.full_text == "Partial ans"
    assert result.finish_reason == "length"


def test_exceptions_propagate(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    install_transport(monkeypatch, boom)

    with pytest.raises(httpx.ConnectError):
        stream_turn(URL, {}, user_chars=0, on_flush=_noop_flush)


def test_http_error_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            500,
            headers={"Content-Type": "application/json"},
            content=b'{"detail": "model load failed"}',
        ),
    )

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        stream_turn(URL, {}, user_chars=0, on_flush=_noop_flush)

    # The body must survive the closed stream context so the UI can show
    # the server's explanation, not just the status code.
    assert excinfo.value.response.json() == {"detail": "model load failed"}


def test_remote_protocol_error_mid_stream_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        def body() -> Iterator[bytes]:
            yield SseStreamBuilder().role_frame().delta("Hel").build()
            raise httpx.RemoteProtocolError("peer died mid-stream")

        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, content=body()
        )

    install_transport(monkeypatch, handler)

    with pytest.raises(httpx.RemoteProtocolError):
        stream_turn(URL, {}, user_chars=0, on_flush=_noop_flush)


def test_active_response_exposed_mid_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        response = stream_response(
            SseStreamBuilder().role_frame().delta("A").done().build()
        )
        responses.append(response)
        return response

    install_transport(monkeypatch, handler)
    seen_during_flush: list[httpx.Response | None] = []
    holder: list[httpx.Response | None] = [None]

    def flush(_text: str) -> None:
        seen_during_flush.append(holder[0])

    stream_turn(
        URL,
        {},
        user_chars=1,
        on_flush=flush,
        flush_interval=0.0,
        on_active=lambda r: holder.__setitem__(0, r),
    )

    assert len(responses) == 1
    # The seam matters mid-stream (that is when cancel reads it): the flush
    # callback must observe the very response object being streamed.
    assert seen_during_flush == [responses[0]]
