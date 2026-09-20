"""Chat cancellation timing and operation-exclusion integration tests."""

from __future__ import annotations

import pytest
from textual.widgets import Input

from mlx_tui.app import MlxTuiApp
from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.models import ModelRow
from mlx_tui.operations import OperationKind
from tests.conftest import AppHarness


def _current_op(app: MlxTuiApp) -> OperationKind:
    """Fresh-read helper: the checker must not narrow this across mutations."""
    return app.operations.current


async def test_cancel_closes_stream(harness: AppHarness) -> None:
    harness.server.mode = "slow"
    await harness.app._poll()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def submitted(a: MlxTuiApp) -> bool:
        return any(t.lstrip("▎ ").startswith("you ›") for t in harness.log_lines())

    assert await harness.wait_for(submitted)
    await harness.pilot.press("escape")

    def cancelled(a: MlxTuiApp) -> bool:
        return any("client request cancelled" in t for t in harness.log_lines())

    assert await harness.wait_for(cancelled)

    def input_enabled(a: MlxTuiApp) -> bool:
        return not a.query_one("#chat-input", ChatInput).disabled

    assert await harness.wait_for(input_enabled)
    assert inp.text == "hi"
    assert harness.chat_pane()._pending_draft is None


async def test_chat_lease_blocks_swap_and_delete_until_cleanup(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.server.mode = "slow"
    await harness.app._poll()
    models = harness.models_pane()
    models._populate(
        [ModelRow("mlx-community/other-model", 1_000_000_000, "4bit", ("x",))]
    )
    table = harness.app.query_one("#models-table")
    table.focus()
    await harness.pilot.pause()

    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda _a: harness.chat_pane().has_live_turn)
    assert harness.app.operations.current is OperationKind.CHATTING

    started: list[str] = []

    def record_load(_row: ModelRow) -> None:
        started.append("load")

    monkeypatch.setattr(
        models,
        "run_warm_swap",
        record_load,
    )
    models.request_load_swap()
    models.request_delete_model()

    assert started == []
    assert harness.app.operations.current is OperationKind.CHATTING
    assert inp.disabled
    assert any("operation already in progress" in t for t in harness.app_log_lines())

    await harness.pilot.press("escape")

    def cleanup_complete(_a: MlxTuiApp) -> bool:
        return (
            _current_op(harness.app) is OperationKind.IDLE
            and not harness.chat_pane().has_live_turn
        )

    assert await harness.wait_for(cleanup_complete)
    assert not inp.disabled
    assert not harness.app.query_one("#models-table").disabled

    assert inp.text == "hi"
    assert harness.chat_pane()._pending_draft is None


def _install_async_mock(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    from functools import partial  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    monkeypatch.setattr(
        httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport)
    )


async def test_cancel_before_headers_event_transport(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio  # noqa: PLC0415
    import time  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    entered = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.sleep(30)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    _install_async_mock(monkeypatch, handler)
    monkeypatch.setattr(harness.app, "effective_model", lambda: "test-model")
    pane = harness.chat_pane()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi before headers"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await asyncio.wait_for(entered.wait(), timeout=2)
    assert pane.has_live_turn

    start = time.monotonic()
    pane.abort()
    assert any("cancellation requested" in t for t in harness.log_lines()), (
        f"log={harness.log_lines()}"
    )

    def cancelled(a: MlxTuiApp) -> bool:
        return any("client request cancelled" in t for t in harness.log_lines())

    assert await harness.wait_for(cancelled)
    assert time.monotonic() - start < 2

    def cleanup(a: MlxTuiApp) -> bool:
        return (
            harness.app.operations.current is OperationKind.IDLE
            and not pane.has_live_turn
            and not harness.app.query_one("#chat-input", ChatInput).disabled
        )

    assert await harness.wait_for(cleanup)
    assert pane.messages == []
    assert len([t for t in harness.log_lines() if "client request cancelled" in t]) == 1
    assert not any("tok/s" in t for t in harness.log_lines())
    await asyncio.sleep(0.3)
    assert len([t for t in harness.log_lines() if "client request cancelled" in t]) == 1
    assert harness.chat_pane()._active_turn is None
    assert "Waiting for output…" not in "\n".join(harness.log_lines())
    assert inp.text == "hi before headers"
    assert harness.chat_pane()._pending_draft is None


async def test_cancel_during_idle_stream_closes(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio  # noqa: PLC0415
    import time  # noqa: PLC0415
    from typing import override  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    entered = asyncio.Event()
    closed = asyncio.Event()

    class _Idle(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            entered.set()
            yield b'data: {"choices": [{"delta": {"role": "assistant"}, "finish_reason": null}]}\n\n'
            await asyncio.sleep(30)
            yield b"data: [DONE]\n\n"

        @override
        async def aclose(self) -> None:
            closed.set()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=_Idle(),
        )

    _install_async_mock(monkeypatch, handler)
    monkeypatch.setattr(harness.app, "effective_model", lambda: "test-model")
    pane = harness.chat_pane()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi idle"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await asyncio.wait_for(entered.wait(), timeout=2)

    start = time.monotonic()
    pane.abort()
    assert await harness.wait_for(
        lambda a: any("client request cancelled" in t for t in harness.log_lines())
    )
    assert time.monotonic() - start < 2
    assert await asyncio.wait_for(closed.wait(), timeout=2)
    assert pane.messages == []
    assert _current_op(harness.app) is OperationKind.IDLE
    assert not harness.app.query_one("#chat-input", ChatInput).disabled
    assert inp.text == "hi idle"
    assert harness.chat_pane()._pending_draft is None


async def test_cancel_after_partial_text_no_commit(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio  # noqa: PLC0415
    from typing import override  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    from tests.builders import sse_frames  # noqa: PLC0415

    started = asyncio.Event()
    closed = asyncio.Event()
    head = sse_frames(deltas=["Hello"], finish=None, done=False)

    class _Partial(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            started.set()
            await asyncio.sleep(0.12)
            yield head
            await asyncio.sleep(30)
            yield b"data: [DONE]\n\n"

        @override
        async def aclose(self) -> None:
            closed.set()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=_Partial(),
        )

    _install_async_mock(monkeypatch, handler)
    monkeypatch.setattr(harness.app, "effective_model", lambda: "test-model")
    pane = harness.chat_pane()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi partial"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await asyncio.wait_for(started.wait(), timeout=2)
    assert await harness.wait_for(lambda app: "Hello" in harness.log_lines())
    pane.abort()
    assert await harness.wait_for(
        lambda a: any("client request cancelled" in t for t in harness.log_lines())
    )
    assert await asyncio.wait_for(closed.wait(), timeout=2)
    assert "Hello" in harness.log_lines()
    assert "Not included in next request" in harness.log_lines()
    assert pane._active_turn is None
    assert pane.messages == []
    assert _current_op(harness.app) is OperationKind.IDLE
    assert not harness.app.query_one("#chat-input", ChatInput).disabled
    assert not any("tok/s" in t for t in harness.log_lines())
    await asyncio.sleep(0.3)
    assert len([t for t in harness.log_lines() if "client request cancelled" in t]) == 1
    assert inp.text == "hi partial"
    assert harness.chat_pane()._pending_draft is None


async def test_cancel_before_task_start_makes_no_request(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx  # noqa: PLC0415

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    _install_async_mock(monkeypatch, handler)
    monkeypatch.setattr(harness.app, "effective_model", lambda: "test-model")
    pane = harness.chat_pane()
    assert pane.messages == []
    inp = harness.app.query_one("#chat-input", ChatInput)
    event = ChatInput.Submitted(inp, "hi pre-start")
    pane._on_input_submitted(event)  # type: ignore[arg-type]
    pane.abort()
    assert await harness.wait_for(
        lambda a: any("client request cancelled" in t for t in harness.log_lines())
    )
    assert await harness.wait_for(
        lambda a: (
            harness.app.operations.current is OperationKind.IDLE
            and not pane.has_live_turn
        )
    )
    assert not called
    assert pane.messages == []
    assert len([t for t in harness.log_lines() if "client request cancelled" in t]) == 1
    assert inp.text == "hi pre-start"
    assert harness.chat_pane()._pending_draft is None


async def test_repeated_escape_single_outcome(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    entered = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.sleep(30)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    _install_async_mock(monkeypatch, handler)
    monkeypatch.setattr(harness.app, "effective_model", lambda: "test-model")
    pane = harness.chat_pane()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi repeat"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await asyncio.wait_for(entered.wait(), timeout=2)
    pane.abort()
    pane.abort()
    pane.abort()
    assert await harness.wait_for(
        lambda a: any("client request cancelled" in t for t in harness.log_lines())
    )
    assert await harness.wait_for(
        lambda a: harness.app.operations.current is OperationKind.IDLE
    )
    await asyncio.sleep(0.3)
    assert (
        len([t for t in harness.log_lines() if "cancellation requested" in t]) == 1
    ), f"log={harness.log_lines()}"
    assert len([t for t in harness.log_lines() if "client request cancelled" in t]) == 1
    assert inp.text == "hi repeat"
    assert harness.chat_pane()._pending_draft is None


@pytest.mark.parametrize(
    "destination", ["stay", "params", "models", "metrics", "search"]
)
@pytest.mark.parametrize("cancel", [False, True])
async def test_cleanup_respects_navigation_and_focus(  # noqa: PLR0915
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, destination: str, cancel: bool
) -> None:
    import asyncio  # noqa: PLC0415

    from textual.widgets import TabbedContent  # noqa: PLC0415

    from mlx_tui.chat import TurnResult  # noqa: PLC0415
    from mlx_tui.params import ParamsPane  # noqa: PLC0415
    from mlx_tui.search_screen import SearchScreen  # noqa: PLC0415
    from mlx_tui.sse import token_accounting  # noqa: PLC0415

    entered, finish, cancelling = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def stream(*args: object, **kwargs: object) -> TurnResult:
        nonlocal calls
        calls += 1
        entered.set()
        try:
            await finish.wait()
        except asyncio.CancelledError:
            cancelling.set()
            await finish.wait()
            raise
        return TurnResult(
            "reply",
            token_accounting(
                prompt_tokens=1,
                completion_tokens=1,
                prompt_estimate=1,
                full_text="reply",
                elapsed=0.1,
            ),
            finish_reason="stop",
            response_model=harness.app.effective_model(),
            first_output_s=0.1,
            answer_started_s=0.1,
            total_s=0.1,
            stream_complete=True,
        )

    monkeypatch.setattr("mlx_tui.chat_ui.turns.stream_turn", stream)
    tabs = harness.app.query_one(TabbedContent)
    tabs.active = "chat"
    await harness.pilot.pause()
    pane = harness.chat_pane()
    inp = pane.query_one("#chat-input", ChatInput)
    inp.text = "  recover me  "
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    await asyncio.wait_for(entered.wait(), timeout=2)
    if cancel:
        await harness.pilot.press("escape")
        await asyncio.wait_for(cancelling.wait(), timeout=2)
    target = None
    if destination == "params":
        target = pane.query_one(ParamsPane).query_one("CollapsibleTitle")
    elif destination in ("models", "metrics"):
        tabs.active = destination
        await harness.pilot.pause()
        target = harness.app.query_one(f"#{destination}-table")
    elif destination == "search":
        harness.app.push_screen(SearchScreen())
        await harness.pilot.pause()
        target = harness.app.screen.query_one("#search-input", Input)
    if target is not None:
        target.focus()
        await harness.pilot.pause()
    assert inp.disabled
    assert inp.text == ""
    assert harness.app.operations.current is OperationKind.CHATTING
    finish.set()
    assert await harness.wait_for(lambda app: not pane.has_live_turn)
    assert inp.text == ("  recover me  " if cancel else "")
    assert not inp.disabled
    assert _current_op(harness.app) is OperationKind.IDLE
    assert calls == 1
    assert harness.app.focused is (inp if destination == "stay" else target)
    if destination == "search":
        await harness.pilot.press("escape")
    if destination in ("models", "metrics"):
        tabs.active = "chat"
        await harness.pilot.pause()
        assert inp.text == ("  recover me  " if cancel else "")
    pane.end_turn()
    assert calls == 1


async def test_unmounted_cleanup_releases_lease(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio  # noqa: PLC0415

    from mlx_tui.chat import TurnResult  # noqa: PLC0415

    entered = asyncio.Event()

    async def stream(*args: object, **kwargs: object) -> TurnResult:
        entered.set()
        await asyncio.sleep(30)
        raise AssertionError("expected cancellation")

    monkeypatch.setattr("mlx_tui.chat_ui.turns.stream_turn", stream)
    pane = harness.chat_pane()
    inp = pane.query_one("#chat-input", ChatInput)
    pane._on_input_submitted(ChatInput.Submitted(inp, "unmount"))
    await asyncio.wait_for(entered.wait(), timeout=2)
    await pane.remove()
    assert await harness.wait_for(
        lambda app: app.operations.current is OperationKind.IDLE
    )
    pane.end_turn()
    assert pane._pending_draft is None
    assert pane._active_turn is None
