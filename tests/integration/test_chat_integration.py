"""Chat payload, transcript, reply, and prompt-history integration tests."""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest
from textual import events
from textual.widgets import TextArea

from mlx_tui import text_screen
from mlx_tui.app import MlxTuiApp
from mlx_tui.chat_turn import ChatTurn
from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.config import AppConfig
from mlx_tui.operations import OperationKind
from mlx_tui.text_screen import TextPreviewScreen
from tests.conftest import AppHarness

_STAMP_RE = re.compile(
    r"(?:▎ )?(\d+~?|—) in · (\d+~?|—) out · "
    r"first (\d+\.\d+s|—) · answer (\d+\.\d+s|—) · "
    r"total \d+\.\d+s · (?:\d+\.\d+ client request tok/s|— client request tok/s).*"
)
_REPLY = "Hello world this is MLX."


async def test_chat_multiline_event_streams_without_malformed_notice(
    stub_server_factory,  # type: ignore[no-untyped-def]
) -> None:
    from mlx_tui.app import MlxTuiApp  # noqa: PLC0415
    from tests.conftest import AppHarness  # noqa: PLC0415

    server = stub_server_factory("multiline")
    app = MlxTuiApp(
        host="127.0.0.1",
        port=int(server.server_address[1]),
        config=AppConfig(model=server.model_id),
    )
    async with app.run_test() as pilot:
        harness = AppHarness(app=app, pilot=pilot, server=server)
        await harness.app._poll()
        inp = harness.app.query_one("#chat-input", ChatInput)
        inp.text = "hi"
        inp.focus()
        await harness.pilot.press("ctrl+enter")

        def stamp_visible(a: MlxTuiApp) -> bool:
            return any("tok/s" in t for t in harness.log_lines())

        assert await harness.wait_for(stamp_visible), (
            f"stamp never appeared; log={harness.log_lines()}"
        )
        texts = harness.log_lines()
        assert _REPLY in texts
        assert not any("malformed" in t for t in texts)


async def test_chat_no_space_data_stream_parses(
    stub_server_factory,  # type: ignore[no-untyped-def]
) -> None:
    from mlx_tui.app import MlxTuiApp  # noqa: PLC0415
    from tests.conftest import AppHarness  # noqa: PLC0415

    server = stub_server_factory("nospace")
    app = MlxTuiApp(
        host="127.0.0.1",
        port=int(server.server_address[1]),
        config=AppConfig(model=server.model_id),
    )
    async with app.run_test() as pilot:
        harness = AppHarness(app=app, pilot=pilot, server=server)
        await harness.app._poll()
        inp = harness.app.query_one("#chat-input", ChatInput)
        inp.text = "hi"
        inp.focus()
        await harness.pilot.press("ctrl+enter")

        def stamp_visible(a: MlxTuiApp) -> bool:
            return any("tok/s" in t for t in harness.log_lines())

        assert await harness.wait_for(stamp_visible), (
            f"stamp never appeared; log={harness.log_lines()}"
        )
        assert _REPLY in harness.log_lines()


async def test_chat_composer_paste_newlines_and_send_button_preserve_exact_text(
    harness: AppHarness,
) -> None:
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.focus()
    pasted = 'def greet():\n\n    return "héllo"'
    await inp._on_paste(events.Paste(pasted))
    await harness.pilot.pause()
    await harness.pilot.press("enter")
    expected = f"{pasted}\n"
    assert inp.text == expected
    assert not harness.server.requests

    await harness.pilot.click("#btn-send")
    assert await harness.wait_for(lambda app: len(harness.chat_pane().messages) == 2)
    posts = [request for request in harness.server.requests if "messages" in request]
    assert len(posts) == 1
    messages = posts[0]["messages"]
    assert isinstance(messages, list)
    assert messages[-1] == {"role": "user", "content": expected}


async def test_chat_composer_ctrl_enter_submits_once(harness: AppHarness) -> None:
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "shortcut send"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda app: len(harness.chat_pane().messages) == 2)
    posts = [request for request in harness.server.requests if "messages" in request]
    assert len(posts) == 1


async def test_retry_keeps_newer_draft_until_explicit_replacement(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.server.mode = "error500"
    inp = harness.app.query_one("#chat-input", ChatInput)
    failed = "failed draft"
    inp.text = failed
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda app: not harness.chat_pane().has_live_turn)
    assert inp.text == failed
    requests_before = len(harness.server.requests)

    newer = "newer draft"
    inp.text = newer
    inp.focus()

    def keep_newer(prompt: str, callback: object = None) -> None:
        if callable(callback):
            callback(False)  # type: ignore[operator]

    monkeypatch.setattr(harness.app, "push_screen", keep_newer)
    pane = harness.chat_pane()
    pane._retry_request_pressed()
    await harness.pilot.pause()
    assert inp.text == newer
    assert len(harness.server.requests) == requests_before

    def replace_newer(prompt: str, callback: object = None) -> None:
        if callable(callback):
            callback(True)  # type: ignore[operator]

    monkeypatch.setattr(harness.app, "push_screen", replace_newer)
    pane._retry_request_pressed()
    await harness.pilot.pause()
    assert inp.text == failed
    assert len(harness.server.requests) == requests_before


async def test_answer_view_and_copy_report_real_clipboard_result(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], bytes]] = []

    def copy_ok(argv: list[str], **kwargs: object) -> SimpleNamespace:
        value = kwargs.get("input")
        assert isinstance(value, bytes)
        calls.append((argv, value))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(text_screen.sys, "platform", "darwin")
    monkeypatch.setattr(text_screen.subprocess, "run", copy_ok)
    pane = harness.chat_pane()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "copy me"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda app: len(pane.messages) == 2)

    turn = pane.query_one(ChatTurn)
    turn.view_button.press()
    await harness.pilot.pause()
    preview = harness.app.screen
    assert isinstance(preview, TextPreviewScreen)
    area = preview.query_one("#text-preview-area", TextArea)
    area.select_all()
    await harness.pilot.click("#btn-copy-selection")
    await harness.pilot.pause()
    assert calls[-1] == (["/usr/bin/pbcopy"], b"Hello world this is MLX.")

    def copy_fail(*args: object, **kwargs: object) -> SimpleNamespace:
        raise OSError("clipboard unavailable")

    monkeypatch.setattr(text_screen.subprocess, "run", copy_fail)
    await harness.pilot.click("#btn-copy-all")
    await harness.pilot.pause()
    status = preview.query_one("#text-preview-status").render()
    assert "copy failed" in str(status)


async def test_chat_payload_carries_effective_model(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        harness.app, "effective_model", lambda: "mlx-community/stub-test"
    )
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def reply_recorded(a: MlxTuiApp) -> bool:
        return len(harness.chat_pane().messages) == 2

    assert await harness.wait_for(reply_recorded), (
        f"turn never completed; messages={harness.chat_pane().messages}"
    )
    posts = [r for r in harness.server.requests if "messages" in r]
    assert posts, f"no chat POST captured; requests={harness.server.requests}"
    assert posts[-1].get("model") == "mlx-community/stub-test"
    assert "seed" not in posts[-1]
    assert "chat_template_kwargs" not in posts[-1]


async def test_chat_refuses_when_model_selection_is_unknown(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(harness.app, "effective_model", lambda: None)
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    await harness.pilot.pause()
    assert harness.chat_pane().messages == []
    assert inp.text == "hi"
    posts = [r for r in harness.server.requests if "messages" in r]
    assert posts == []
    assert any("select a model" in line for line in harness.app_log_lines())


async def test_assistant_reply_recorded_in_history(harness: AppHarness) -> None:
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def reply_recorded(a: MlxTuiApp) -> bool:
        return len(harness.chat_pane().messages) == 2

    assert await harness.wait_for(reply_recorded), (
        f"assistant reply never recorded; messages={harness.chat_pane().messages}"
    )
    assert harness.chat_pane().messages[0] == {"role": "user", "content": "hi"}
    assert harness.chat_pane().messages[1] == {"role": "assistant", "content": _REPLY}


async def test_empty_response_warns_instead_of_silence(harness: AppHarness) -> None:
    harness.server.mode = "empty"
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def warning_visible(a: MlxTuiApp) -> bool:
        return any("model returned no text" in t for t in harness.log_lines())

    assert await harness.wait_for(warning_visible), (
        f"empty-response warning never appeared; log={harness.log_lines()}"
    )
    # A stamp still lands, but neither side enters future history so retry
    # resends the same context.
    assert "client request tok/s" in " ".join(harness.log_lines())
    assert harness.chat_pane().messages == []

    def input_enabled(a: MlxTuiApp) -> bool:
        return not a.query_one("#chat-input", ChatInput).disabled

    assert await harness.wait_for(input_enabled)
    assert inp.text == "hi"


async def test_length_capped_reply_shows_notice(harness: AppHarness) -> None:
    harness.server.mode = "length_cap"
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def cap_notice_visible(a: MlxTuiApp) -> bool:
        return any("token cap" in t for t in harness.log_lines())

    assert await harness.wait_for(cap_notice_visible), (
        f"cap notice never appeared; log={harness.log_lines()}"
    )
    # The partial reply is shown but neither side enters future history.
    assert "Partial ans" in harness.log_lines()
    assert harness.chat_pane().messages == []

    def input_enabled(a: MlxTuiApp) -> bool:
        return not a.query_one("#chat-input", ChatInput).disabled

    assert await harness.wait_for(input_enabled)
    assert inp.text == "hi"


async def test_server_error_status_shows_red_line_not_fake_stamp(
    harness: AppHarness,
) -> None:
    harness.server.mode = "error500"
    await harness.app._poll()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def error_visible(a: MlxTuiApp) -> bool:
        return any(
            t.startswith("server error") and "(HTTP 500)" in t
            for t in harness.log_lines()
        )

    assert await harness.wait_for(error_visible), (
        f"error line never appeared; log={harness.log_lines()}"
    )
    # The server's own explanation must ride along, not just the status code.
    assert any("model load failed" in t for t in harness.log_lines())

    # No fabricated stamp may land after the error line.
    await asyncio.sleep(0.3)
    texts = harness.log_lines()
    assert not any("tok/s" in t for t in texts)
    assert harness.chat_pane()._active_turn is None
    assert "Waiting for output…" not in "\n".join(harness.log_lines())

    def input_enabled(a: MlxTuiApp) -> bool:
        return not a.query_one("#chat-input", ChatInput).disabled

    assert await harness.wait_for(input_enabled)
    assert inp.text == "hi"
    assert harness.chat_pane()._pending_draft is None
    assert "Not included in next request" in harness.log_lines()


async def test_truncated_stream_red_line_and_no_stale_pane(harness: AppHarness) -> None:
    harness.server.mode = "truncated"
    await harness.app._poll()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def unreachable(a: MlxTuiApp) -> bool:
        return any(t.startswith("server unreachable") for t in harness.log_lines())

    assert await harness.wait_for(unreachable), (
        f"red line never appeared; log={harness.log_lines()}"
    )
    assert harness.chat_pane()._active_turn is None
    assert "Waiting for output…" not in "\n".join(harness.log_lines())
    assert not any("tok/s" in t for t in harness.log_lines())

    def input_enabled(a: MlxTuiApp) -> bool:
        return not a.query_one("#chat-input", ChatInput).disabled

    assert await harness.wait_for(input_enabled)
    assert inp.text == "hi"
    assert harness.chat_pane()._pending_draft is None
    assert "Not included in next request" in harness.log_lines()


async def test_chat_renders_markdown(harness: AppHarness) -> None:
    from textual.containers import VerticalScroll  # noqa: PLC0415
    from textual.widgets import TabbedContent  # noqa: PLC0415

    from mlx_tui.chat_turn import ChatTurn  # noqa: PLC0415

    harness.app.query_one(TabbedContent).active = "chat"
    pane = harness.chat_pane()
    turn = ChatTurn("[bold]literal user[/bold]")
    await pane.query_one(VerticalScroll).mount(turn)
    pane._active_turn = turn
    pane._complete_turn_ui(
        "# hi\n\n**bold**\n\n```python\nprint('hello')\n```",
        "12 in · 6 out · first 0.10s · answer 0.20s · total 1.00s · "
        "10.0 client request tok/s",
        [],
    )
    await harness.pilot.pause()
    lines = harness.log_lines()
    assert not any(line.strip() == "# hi" for line in lines)
    assert any("hi" in line for line in lines)
    assert any("bold" in line for line in lines)
    assert any("print(" in line for line in lines)
    assert any("[bold]literal user[/bold]" in line for line in lines)
    assert any("tok/s" in line for line in lines)
    assert turn.body.region.bottom <= turn.stamp.region.y
    assert pane.messages == []
    pane.end_turn()
    assert turn.finished and not turn.waiting


async def test_failed_prompt_excluded_from_next_payload(harness: AppHarness) -> None:
    harness.server.mode = "ok"
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "first good"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def two_messages(a: MlxTuiApp) -> bool:
        return len(harness.chat_pane().messages) == 2

    assert await harness.wait_for(two_messages)
    assert harness.chat_pane().messages[0] == {"role": "user", "content": "first good"}

    harness.server.mode = "error500"
    harness.server.requests.clear()
    inp.text = "will fail"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def failed(a: MlxTuiApp) -> bool:
        return any("server error" in t for t in harness.log_lines())

    assert await harness.wait_for(failed)
    assert await harness.wait_for(
        lambda a: not harness.app.query_one("#chat-input", ChatInput).disabled
    )
    assert len(harness.chat_pane().messages) == 2
    assert harness.chat_pane().messages[0]["content"] == "first good"
    assert inp.text == "will fail"

    harness.server.mode = "ok"
    harness.server.requests.clear()
    inp.text = "second good"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda a: len(harness.chat_pane().messages) == 4)
    posts = [r for r in harness.server.requests if "messages" in r]
    assert posts
    sent = posts[-1]["messages"]
    assert isinstance(sent, list)
    texts = [m.get("content") for m in sent if isinstance(m, dict)]
    assert "will fail" not in texts
    assert "first good" in texts
    assert "second good" in texts


async def test_cancelled_prompt_excluded_retains_prior(harness: AppHarness) -> None:
    harness.server.mode = "ok"
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "prior kept"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda a: len(harness.chat_pane().messages) == 2)

    harness.server.mode = "slow"
    await harness.app._poll()
    harness.server.requests.clear()
    inp.text = "to cancel"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda a: harness.chat_pane().has_live_turn)
    await harness.pilot.press("escape")
    assert await harness.wait_for(
        lambda a: any("client request cancelled" in t for t in harness.log_lines())
    )
    assert await harness.wait_for(
        lambda a: harness.app.operations.current is OperationKind.IDLE
    )
    assert len(harness.chat_pane().messages) == 2
    assert harness.chat_pane().messages[0]["content"] == "prior kept"

    harness.server.mode = "ok"
    harness.server.requests.clear()
    inp.text = "after cancel"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda a: len(harness.chat_pane().messages) == 4)
    posts = [r for r in harness.server.requests if "messages" in r]
    assert posts
    sent = posts[-1]["messages"]
    assert isinstance(sent, list)
    texts = [m.get("content") for m in sent if isinstance(m, dict)]
    assert "to cancel" not in texts
    assert "prior kept" in texts
    assert "after cancel" in texts


@pytest.mark.parametrize("previews", [False, True])
async def test_response_identity_and_authoritative_final(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, previews: bool
) -> None:
    from collections.abc import Callable  # noqa: PLC0415

    from textual.widgets import TabbedContent  # noqa: PLC0415

    from mlx_tui.chat import TurnResult  # noqa: PLC0415
    from mlx_tui.chat_turn import ChatTurn  # noqa: PLC0415
    from mlx_tui.sse import token_accounting  # noqa: PLC0415

    bodies: list[object] = []

    async def stream(  # noqa: PLR0913
        url: str,
        payload: dict[str, object],
        *,
        prompt_estimate: int,
        on_flush: Callable[[str], None],
        on_activity: Callable[[str], None] | None = None,
        on_progress: Callable[[object], None] | None = None,
    ) -> TurnResult:
        turn = harness.chat_pane()._active_turn
        assert turn is not None
        bodies.append(turn.body)
        if previews:
            for text in ("first", "first and second", "preview ending"):
                on_flush(text)
                await harness.pilot.pause()
                assert turn.body is bodies[0]
        return TurnResult(
            full_text="authoritative final",
            accounting=token_accounting(
                prompt_tokens=2,
                completion_tokens=3,
                prompt_estimate=2,
                full_text="authoritative final",
                elapsed=0.2,
            ),
            finish_reason="stop",
            response_model=str(payload["model"]),
            first_output_s=0.1,
            answer_started_s=0.1,
            total_s=0.2,
            stream_complete=True,
        )

    monkeypatch.setattr("mlx_tui.chat_ui.turns.stream_turn", stream)
    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    pane = harness.chat_pane()
    inp = harness.app.query_one("#chat-input", ChatInput)
    pane._on_input_submitted(ChatInput.Submitted(inp, "one attempt"))
    # Direct references work before Textual's mount pass.
    assert pane._active_turn is not None
    if previews:
        pane._update_stream("before mounting")
    assert await harness.wait_for(lambda app: not pane.has_live_turn)
    turns = list(pane.query(ChatTurn))
    assert len(turns) == 1
    assert turns[0].body is bodies[0]
    assert harness.log_lines().count("authoritative final") == 1
    assert not any("preview ending" in line for line in harness.log_lines())
    assert turns[0].user.region.bottom <= turns[0].assistant.region.y
    assert turns[0].assistant.region.bottom <= turns[0].body.region.y
    assert pane.messages == [
        {"role": "user", "content": "one attempt"},
        {"role": "assistant", "content": "authoritative final"},
    ]


async def test_reasoning_renders_separately_before_answer(
    stub_server_factory,  # type: ignore[no-untyped-def]
) -> None:
    from mlx_tui.app import MlxTuiApp  # noqa: PLC0415
    from tests.conftest import AppHarness  # noqa: PLC0415

    server = stub_server_factory("reasoning")
    app = MlxTuiApp(
        host="127.0.0.1",
        port=int(server.server_address[1]),
        config=AppConfig(model=server.model_id),
    )
    async with app.run_test() as pilot:
        harness = AppHarness(app=app, pilot=pilot, server=server)
        await harness.app._poll()
        inp = harness.app.query_one("#chat-input", ChatInput)
        inp.text = "hi"
        inp.focus()
        await harness.pilot.press("ctrl+enter")

        def reasoning_visible(a: MlxTuiApp) -> bool:
            return any("think step" in t for t in harness.log_lines())

        assert await harness.wait_for(reasoning_visible), (
            f"reasoning never appeared; log={harness.log_lines()}"
        )
        assert "blue" in harness.log_lines()
        assert harness.chat_pane().messages[-1] == {
            "role": "assistant",
            "content": "blue",
        }


async def test_tool_only_displays_unexecuted_without_commit(
    stub_server_factory,  # type: ignore[no-untyped-def]
) -> None:
    from mlx_tui.app import MlxTuiApp  # noqa: PLC0415
    from tests.conftest import AppHarness  # noqa: PLC0415

    server = stub_server_factory("tools")
    app = MlxTuiApp(
        host="127.0.0.1",
        port=int(server.server_address[1]),
        config=AppConfig(model=server.model_id),
    )
    async with app.run_test() as pilot:
        harness = AppHarness(app=app, pilot=pilot, server=server)
        await harness.app._poll()
        inp = harness.app.query_one("#chat-input", ChatInput)
        inp.text = "hi"
        inp.focus()
        await harness.pilot.press("ctrl+enter")

        def tool_visible(a: MlxTuiApp) -> bool:
            return any("not executed" in t for t in harness.log_lines())

        assert await harness.wait_for(tool_visible), (
            f"tool notice never appeared; log={harness.log_lines()}"
        )
        assert harness.chat_pane().messages == []
        assert inp.text == "hi"


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_stream_scroll_intent_and_geometry(  # noqa: PLR0915
    harness: AppHarness, size: tuple[int, int]
) -> None:
    from textual.containers import VerticalScroll  # noqa: PLC0415
    from textual.widgets import TabbedContent  # noqa: PLC0415

    from mlx_tui.chat_turn import ChatTurn  # noqa: PLC0415

    await harness.pilot.resize_terminal(*size)
    tabs = harness.app.query_one(TabbedContent)
    tabs.active = "chat"
    await harness.pilot.pause()
    pane = harness.chat_pane()
    viewport = pane.query_one(VerticalScroll)
    turn = ChatTurn("scroll test")
    await viewport.mount(turn)
    pane._active_turn = turn
    await harness.pilot.pause()
    composer = pane.query_one("#chat-input").region
    assert pane.query_one("#chat-attachment-index").content_region.height == 1
    body = turn.body
    text = "\n\n".join(f"Paragraph {i}" for i in range(100))
    pane._update_stream(text)
    await harness.pilot.pause()
    assert viewport.max_scroll_y > 0
    assert viewport.is_vertical_scroll_end
    assert pane.query_one("#chat-input").region == composer
    assert viewport.region.bottom <= composer.y
    assert body is turn.body
    viewport.scroll_to(y=5, animate=False, immediate=True)
    await harness.pilot.pause()
    before = viewport.scroll_y
    pane._update_stream(text + "\n\nnext paragraph")
    await harness.pilot.pause()
    assert viewport.scroll_y == before
    # A queued follow must not override an upward scroll before layout.
    viewport.scroll_end(animate=False, immediate=True)
    pane._update_stream(text + "\n\nqueued follow")
    viewport.scroll_to(y=3, animate=False, immediate=True)
    await harness.pilot.pause()
    assert viewport.scroll_y == 3
    for tab, dimensions in (("metrics", (120, 40)), ("chat", size)):
        tabs.active = tab
        await harness.pilot.resize_terminal(*dimensions)
        pane._update_stream(text + "\n\nwhile switching")
        await harness.pilot.pause()
    assert viewport.scroll_y == 3
    pane._complete_turn_ui(text + "\n\nfinished", "measurement", [])
    await harness.pilot.pause()
    assert viewport.scroll_y == 3
    assert turn.body is body
    # Following resumes when the reader explicitly reaches the bottom.
    second = ChatTurn("next")
    await viewport.mount(second)
    pane._active_turn = second
    await harness.pilot.pause()
    viewport.scroll_end(animate=False, immediate=True)
    await harness.pilot.pause()
    pane._update_stream(text)
    await harness.pilot.pause()
    assert viewport.is_vertical_scroll_end
    assert pane.query_one("#chat-input").region == composer
    pane.end_turn()


@pytest.mark.parametrize("fault", ["prepare", "mount", "start"])
async def test_setup_failure_restores_exact_draft_and_lease(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from textual.containers import VerticalScroll  # noqa: PLC0415
    from textual.widgets import TabbedContent  # noqa: PLC0415

    from mlx_tui.params import ParamsPane  # noqa: PLC0415

    pane = harness.chat_pane()
    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    inp = pane.query_one("#chat-input", ChatInput)
    inp.focus()
    await harness.pilot.pause()

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("setup fault")

    if fault == "prepare":
        monkeypatch.setattr(pane.query_one(ParamsPane), "read_values", fail)
    elif fault == "mount":
        monkeypatch.setattr(pane.query_one(VerticalScroll), "mount", fail)
    else:
        monkeypatch.setattr(pane, "_run_turn", fail)
    draft = "  preserve my whitespace  "
    pane._on_input_submitted(ChatInput.Submitted(inp, draft))
    await harness.pilot.pause()
    assert inp.text == draft
    assert not inp.disabled
    assert harness.app.focused is inp
    assert pane._pending_draft is None
    assert pane._active_turn is None
    assert not pane.has_live_turn
    assert harness.app.operations.current is OperationKind.IDLE
    assert pane.messages == []
    assert not harness.server.requests
    inp.text = "new draft"
    pane.end_turn()
    assert inp.text == "new draft"


async def test_explicit_retry_sends_restored_draft_once(harness: AppHarness) -> None:
    inp = harness.app.query_one("#chat-input", ChatInput)
    pane = harness.chat_pane()
    inp.text = "prior success"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda app: len(pane.messages) == 2)
    harness.server.mode = "error500"
    draft = "  please retry me  "
    inp.text = draft
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda app: not pane.has_live_turn)
    assert inp.text == draft
    assert len(harness.server.requests) == 2
    assert len(pane.messages) == 2
    await harness.pilot.pause()
    assert len(harness.server.requests) == 2
    harness.server.mode = "ok"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda app: len(pane.messages) == 4)
    assert len(harness.server.requests) == 3
    sent = harness.server.requests[-1]["messages"]
    assert isinstance(sent, list)
    assert sent == [*pane.messages[:2], {"role": "user", "content": draft}]
    assert inp.text == ""


@pytest.mark.parametrize("newer", [False, True])
async def test_unexpected_failure_respects_newer_draft(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, newer: bool
) -> None:
    from mlx_tui.chat import TurnResult  # noqa: PLC0415

    entered = asyncio.Event()
    finish = asyncio.Event()

    async def fail(*args: object, **kwargs: object) -> TurnResult:
        entered.set()
        await finish.wait()
        raise ValueError("unexpected failure")

    monkeypatch.setattr("mlx_tui.chat_ui.turns.stream_turn", fail)
    pane = harness.chat_pane()
    inp = pane.query_one("#chat-input", ChatInput)
    pane._on_input_submitted(ChatInput.Submitted(inp, "  original  "))
    await asyncio.wait_for(entered.wait(), timeout=2)
    if newer:
        inp.text = "newer draft"
    finish.set()
    assert await harness.wait_for(lambda app: not pane.has_live_turn)
    assert inp.text == ("newer draft" if newer else "  original  ")
    assert pane.messages == []
    assert pane._pending_draft is None
    assert "Not included in next request" in harness.log_lines()
