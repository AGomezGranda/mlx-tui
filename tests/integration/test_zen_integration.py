from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import override

import httpx
import pytest
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Collapsible, Static, TabbedContent

from mlx_tui.attachments import AttachmentSnapshot
from mlx_tui.chat_ui.pane import ChatPane
from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.sessions.models import SessionTurn
from mlx_tui.sse import TokenAccounting
from tests.conftest import AppHarness


def _zen_text(pane: ChatPane) -> str:
    content = pane.query_one("#zen-info", Static).content
    assert isinstance(content, Text)
    return content.plain


@pytest.mark.parametrize(
    ("tab", "focus_selector"),
    [
        ("models", "#models-table"),
        ("compare", "#comparison-profile-a"),
        ("chat", "#chat-input"),
        ("metrics", "#metrics-table"),
    ],
)
async def test_zen_toggle_preserves_chat_and_restores_view(
    harness: AppHarness, tab: str, focus_selector: str
) -> None:
    from mlx_tui.params import ParamsPane  # noqa: PLC0415

    app = harness.app
    tabs = app.query_one(TabbedContent)
    pane = harness.chat_pane()
    composer = pane.query_one("#chat-input", ChatInput)
    tabs.active = tab
    await harness.pilot.pause()
    focus_target = app.query_one(focus_selector)
    focus_target.focus()
    await harness.pilot.pause()

    composer.load_text("keep this\nmultiline draft")
    composer.select_line(1)
    selection = composer.selection
    attachment = AttachmentSnapshot(
        Path("notes.txt"), Path("/notes.txt"), 5, "digest", "notes"
    )
    pane._draft_attachments = (attachment,)
    params = app.query_one(ParamsPane)
    activity = app.query_one("#activity", Collapsible)
    params.collapsed = False
    activity.collapsed = False
    await harness.pilot.pause()

    await harness.pilot.press("ctrl+z")
    await harness.pilot.pause()
    assert app.zen_mode
    assert tabs.active == "chat"
    assert composer.border_subtitle == "Ctrl+Enter send · Ctrl+Z exit"
    await harness.pilot.press("f2")
    assert not activity.collapsed
    assert app.query_one(ChatPane) is pane
    assert pane.query_one("#chat-input", ChatInput) is composer

    await harness.pilot.press("ctrl+z")
    await harness.pilot.pause()
    assert not app.zen_mode
    assert tabs.active == tab
    assert composer.text == "keep this\nmultiline draft"
    assert composer.selection == selection
    assert pane._draft_attachments == (attachment,)
    expected_focus = (
        pane.query_one("#chat-transcript")
        if tab == "chat" and composer.disabled
        else focus_target
    )
    assert app.focused is expected_focus
    assert params.collapsed is False
    assert not activity.collapsed


async def test_zen_toggle_respects_modal_and_stream(  # noqa: PLR0915
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mlx_tui.chat import TurnResult  # noqa: PLC0415

    class ProbeModal(ModalScreen[None]):
        @override
        def compose(self) -> ComposeResult:
            yield Vertical(Static("modal"))

    app = harness.app
    app.push_screen(ProbeModal())
    await harness.pilot.pause()
    await harness.pilot.press("ctrl+z")
    assert not app.zen_mode
    app.pop_screen()
    await harness.pilot.pause()

    entered = asyncio.Event()
    calls = 0

    async def controlled_stream(*_args: object, **_kwargs: object) -> TurnResult:
        nonlocal calls
        calls += 1
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr("mlx_tui.chat_ui.turns.stream_turn", controlled_stream)
    await app._poll()
    pane = harness.chat_pane()
    viewport = pane.query_one("#chat-transcript", VerticalScroll)
    await viewport.mount(*(Static(f"previous turn {index}") for index in range(80)))
    await harness.pilot.pause()
    viewport.scroll_end(animate=False, immediate=True)
    assert viewport.is_vertical_scroll_end
    composer = pane.query_one("#chat-input", ChatInput)
    composer.text = "hold this request"
    composer.focus()
    await harness.pilot.press("ctrl+enter")
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert pane.has_live_turn
    viewport.scroll_y = 0
    await harness.pilot.pause()
    assert viewport.scroll_y == 0

    await harness.pilot.press("ctrl+z")
    await harness.pilot.pause()
    assert app.zen_mode
    assert composer.border_subtitle == "Esc stop · Ctrl+Z exit"
    assert app.focused is pane.query_one("#chat-transcript")
    assert viewport.scroll_y == 0
    assert pane.has_live_turn
    assert calls == 1
    await harness.pilot.press("ctrl+z")
    await harness.pilot.pause()
    assert not app.zen_mode
    assert app.focused is pane.query_one("#chat-transcript")
    assert viewport.scroll_y == 0
    assert pane.has_live_turn
    assert calls == 1

    await harness.pilot.press("escape")
    assert await harness.wait_for(lambda _app: not pane.has_live_turn)
    assert calls == 1
    assert any("client request cancelled" in line for line in harness.log_lines())


async def test_zen_forwards_warnings_as_literal_notifications(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    notices: list[tuple[str, str, str]] = []

    def notify(
        message: str,
        *,
        title: str = "",
        severity: str = "information",
        **_kwargs: object,
    ) -> None:
        notices.append((message, title, severity))

    monkeypatch.setattr(harness.app, "notify", notify)
    await harness.pilot.press("ctrl+z")
    harness.app.log_app("[red]literal warning[/]", "yellow")
    harness.app.log_app("ordinary update")
    assert notices == [("[red]literal warning[/]", "Last warning", "warning")]


async def test_zen_info_tracks_attempt_context_and_restored_sessions(  # noqa: PLR0915
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mlx_tui.chat import TurnProgress, TurnResult  # noqa: PLC0415

    pane = harness.chat_pane()
    composer = pane.query_one("#chat-input", ChatInput)
    app = harness.app
    app.config = replace(app.config, max_ctx=512, max_tokens=64)
    pane.apply_config_params(app.config)
    pane._start_empty_session(temporary=True)
    pane.messages = [
        message
        for _ in range(12)
        for message in (
            {"role": "user", "content": "u" * 90},
            {"role": "assistant", "content": "a" * 90},
        )
    ]
    attachment = AttachmentSnapshot(
        Path("notes.txt"), Path("/notes.txt"), 5, "digest", "notes"
    )
    pane._draft_attachments = (attachment,)
    composer.text = "review this"
    pane.refresh_context_bar()
    prepared = pane._current_window()
    assert prepared is not None
    _, window = prepared
    assert window.reserved_tokens == window.input_tokens + 64
    assert window.excluded_turns > 0

    await harness.pilot.press("ctrl+z")
    await harness.pilot.pause()
    info = pane.query_one("#zen-info", Static)
    assert f"ctx ≈{round(window.reserved_tokens / 512 * 100)}%" in _zen_text(pane)
    assert f"{window.excluded_turns} excluded" in _zen_text(pane)
    assert "1 file" in _zen_text(pane)
    assert isinstance(info.tooltip, str)
    assert "reserved maximum output tokens" in info.tooltip

    thinking = asyncio.Event()
    responding = asyncio.Event()
    finish_first = asyncio.Event()
    finish_result = asyncio.Event()
    second_started = asyncio.Event()
    fail_second = asyncio.Event()
    payloads: list[dict[str, object]] = []
    calls = 0

    async def stream_attempt(
        _url: str,
        payload: dict[str, object],
        *,
        prompt_estimate: int,
        on_flush: Callable[[str], None],
        on_activity: Callable[[str], None] | None,
        on_progress: Callable[[TurnProgress], None] | None,
    ) -> TurnResult:
        nonlocal calls
        calls += 1
        payloads.append(payload)
        if calls == 2:
            second_started.set()
            await fail_second.wait()
            raise httpx.ConnectError("offline")
        if on_progress is not None:
            on_progress(TurnProgress(reasoning_delta="thinking"))
        thinking.set()
        await finish_first.wait()
        if on_progress is not None:
            on_progress(TurnProgress(answer_delta="answer"))
        on_flush("answer")
        responding.set()
        await finish_result.wait()
        return TurnResult(
            full_text="answer",
            accounting=TokenAccounting(12, 6, False, False, 1.8),
            response_model=app.effective_model(),
            total_s=3.24,
            stream_complete=True,
        )

    monkeypatch.setattr("mlx_tui.chat_ui.turns.stream_turn", stream_attempt)
    composer.focus()
    await harness.pilot.press("ctrl+enter")
    assert await asyncio.wait_for(thinking.wait(), timeout=2)
    assert "Thinking…" in _zen_text(pane)
    assert "6 out" not in _zen_text(pane)
    request_messages = payloads[0]["messages"]
    assert isinstance(request_messages, list) and isinstance(request_messages[-1], dict)
    request_content = request_messages[-1].get("content")
    assert isinstance(request_content, str) and "notes" in request_content

    finish_first.set()
    assert await asyncio.wait_for(responding.wait(), timeout=2)
    assert "Responding…" in _zen_text(pane)
    finish_result.set()
    assert await harness.wait_for(lambda _app: not pane.has_live_turn)
    assert "6 out · 3.2s" in _zen_text(pane)
    assert pane._session is not None and not pane._session.attempts
    assert pane.messages[-1] == {"role": "assistant", "content": "answer"}

    composer.text = "next attempt"
    composer.focus()
    await harness.pilot.press("ctrl+enter")
    assert await asyncio.wait_for(second_started.wait(), timeout=2)
    assert "Thinking…" in _zen_text(pane)
    assert "6 out" not in _zen_text(pane)
    fail_second.set()
    assert await harness.wait_for(lambda _app: not pane.has_live_turn)
    assert "6 out" not in _zen_text(pane)
    assert any("server unreachable" in line for line in harness.log_lines())

    settings = pane.capture_request_settings()
    restored_turn = SessionTurn(
        turn_id="restored-turn",
        created_at=pane._now_iso(),
        original_draft="restored prompt",
        sent_content="restored prompt",
        settings=settings,
        answer="saved answer",
        outcome="success",
        completion_tokens=11,
        total_s=1.26,
        stream_complete=True,
    )
    assert pane._session is not None
    pane._render_session(
        replace(pane._session, draft="restored draft", attempts=(restored_turn,))
    )
    await harness.pilot.pause()
    assert "≈11 out · 1.3s" in _zen_text(pane)


async def test_zen_blockers_remain_actionable(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = harness.chat_pane()
    app = harness.app
    pane._start_empty_session(temporary=True)
    await harness.pilot.press("ctrl+z")
    await harness.pilot.pause()
    info = pane.query_one("#zen-info", Static)
    pane._draft_attachments = (
        AttachmentSnapshot(Path("notes.txt"), Path("/notes.txt"), 5, "digest", "notes"),
    )
    pane._refresh_attachment_ui()
    await harness.pilot.pause()
    assert "1 file" in _zen_text(pane)

    pane._read_only = True
    pane._update_action_visibility()
    await harness.pilot.pause()
    assert "read-only" in _zen_text(pane) and "1 file attached" in _zen_text(pane)
    row = pane.query_one("#chat-session-row")
    assert row.display, (
        f"display={row.display}, css={row.styles.display}, visible={row.visible}"
    )
    assert pane.query_one("#btn-sessions").display
    assert pane.query_one("#btn-new-session").display

    pane._read_only = False
    pane._save_failed = True
    pane._save_error = "disk full · permission denied " * 8
    pane._update_action_visibility()
    await harness.pilot.pause()
    assert "disk full" in _zen_text(pane)
    assert "permission denied" in _zen_text(pane)
    assert info.region.height > 1
    assert pane.query_one("#chat-save-row").display
    assert pane.query_one("#btn-retry-save").display
    assert pane.query_one("#btn-discard-unsaved").display

    def confirm_discard(_screen: object, callback: object = None) -> None:
        if callable(callback):
            callback(True)

    monkeypatch.setattr(app, "push_screen", confirm_discard)
    pane._discard_pressed()
    assert not pane._save_failed
    pane._pending_reconcile = pane.capture_request_settings()
    pane._update_action_visibility()
    await harness.pilot.pause()
    assert "settings differ" in _zen_text(pane)
    assert pane.query_one("#chat-reconcile-row").display

    pane._keep_current_pressed()
    await harness.pilot.pause()
    assert pane._pending_reconcile is None
    assert "settings differ" not in _zen_text(pane)

    app.log_app("[yellow]literal warning[/]", "yellow")
    assert _zen_text(pane).startswith("Last warning: [yellow]literal warning[/]")
    assert "1 file attached" in _zen_text(pane)


@pytest.mark.parametrize("initial", [(80, 24), (120, 40), (160, 48)])
async def test_zen_layout_and_resize(
    harness: AppHarness, initial: tuple[int, int]
) -> None:
    from textual.widgets import Footer, TabbedContent  # noqa: PLC0415

    from mlx_tui.params import ParamsPane  # noqa: PLC0415

    app = harness.app
    pane = harness.chat_pane()
    await harness.pilot.resize_terminal(*initial)
    app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    app.server_identity = replace(
        app.server_identity,
        selected_model="publisher/" + "very-long-model-name-" * 8,
    )
    pane._draft_attachments = (
        AttachmentSnapshot(Path("notes.txt"), Path("/notes.txt"), 5, "digest", "notes"),
    )
    await harness.pilot.press("ctrl+z")
    await harness.pilot.pause()

    sizes = [(80, 24), (120, 40), (160, 48)]
    sequence = [initial, *[size for size in sizes if size != initial], initial]
    pane.query_one("#chat-input", ChatInput).focus()
    for size in sequence:
        await harness.pilot.resize_terminal(*size)
        await harness.pilot.pause()
        info = pane.query_one("#zen-info", Static)
        transcript = pane.query_one("#chat-transcript")
        composer = pane.query_one("#chat-input")
        composer_row = pane.query_one("#chat-composer-row")
        assert pane.region.width <= 96
        assert abs(2 * pane.region.x + pane.region.width - size[0]) <= 1
        assert pane.region.x >= 2 and pane.region.x + pane.region.width <= size[0] - 2
        assert pane.region.y >= 1 and pane.region.y + pane.region.height <= size[1] - 1
        assert info.region.height == 1
        assert transcript.region.y + transcript.region.height <= info.region.y
        assert info.region.y + info.region.height <= composer_row.region.y
        assert composer_row.region.y + composer_row.region.height == (
            pane.region.y + pane.region.height
        )
        assert composer.border_subtitle == "Ctrl+Enter send · Ctrl+Z exit"
        assert "ctx ≈" in _zen_text(pane) and "1 file" in _zen_text(pane)
        assert "…" in _zen_text(pane)
        assert pane.query_one("#btn-send").display

        hidden = (
            pane.query_one(ParamsPane),
            pane.query_one("#chat-context"),
            pane.query_one("#chat-session-row"),
            pane.query_one("#chat-attachment-row"),
            app.query_one("#status-bar"),
            app.query_one("#activity"),
            app.query_one(Footer),
            app.query_one(TabbedContent).children[0],
            pane.query_one("#btn-sessions"),
            pane.query_one("#btn-add-attachment"),
        )
        focus_chain = app.screen.focus_chain
        assert all(widget not in focus_chain for widget in hidden)

    await harness.pilot.press("ctrl+z")
    await harness.pilot.pause()
    assert not app.zen_mode


async def test_zen_keyboard_journey(harness: AppHarness) -> None:
    from textual.widgets import TabbedContent  # noqa: PLC0415

    from mlx_tui.chat_turn import ChatTurn  # noqa: PLC0415
    from mlx_tui.text_screen import TextPreviewScreen  # noqa: PLC0415

    app = harness.app
    pane = harness.chat_pane()
    composer = pane.query_one("#chat-input", ChatInput)
    app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    composer.focus()
    await harness.pilot.press("ctrl+z")
    await harness.pilot.pause()
    await harness.pilot.press(*"keyboard journey")
    assert composer.text == "keyboard journey"
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda _app: not pane.has_live_turn)
    assert pane.messages[-1] == {
        "role": "assistant",
        "content": "Hello world this is MLX.",
    }
    turn = pane.query_one(ChatTurn)
    assert turn.body.display
    assert turn.stamp.region.height == 0, f"css={turn.stamp.styles.display}"
    assert turn.view_button.region.height == 0
    turn.add_notice("actionable notice", "yellow")
    assert turn.notices.display
    assert isinstance(turn.notices.content, Text)
    assert "actionable notice" in turn.notices.content.plain
    posts = [request for request in harness.server.requests if "messages" in request]
    assert len(posts) == 1

    await harness.pilot.press("f3")
    await harness.pilot.pause()
    assert isinstance(app.screen, TextPreviewScreen)
    assert app.zen_mode
    await harness.pilot.press("escape")
    await harness.pilot.pause()
    assert app.zen_mode
    assert app.screen is app.screen_stack[0]
    await harness.pilot.press("ctrl+z")
    await harness.pilot.pause()
    assert not app.zen_mode
    assert app.query_one(TabbedContent).active == "chat"
    assert turn.stamp.region.height > 0
    assert turn.view_button.region.height > 0
