"""Chat parameter and context-budget integration tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Input, TabbedContent, TextArea

import mlx_tui.chat_ui.widgets as chat_widgets
from mlx_tui.app import MlxTuiApp
from mlx_tui.attachments import read_attachment, render_user_content
from mlx_tui.chat_ui.widgets import ChatInput
from tests.conftest import AppHarness


async def test_params_sidebar_sends_payload(harness: AppHarness) -> None:
    from textual.widgets import TabbedContent  # noqa: PLC0415

    from mlx_tui.chat_ui.pane import ChatPane  # noqa: PLC0415

    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    pane = harness.app.query_one(ChatPane)
    # set valid params
    harness.app.query_one("#param-temp", Input).value = "0.3"
    harness.app.query_one("#param-top-p", Input).value = "0.9"
    harness.app.query_one("#param-max-tokens", Input).value = "256"
    await harness.pilot.pause()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def has_history(a: MlxTuiApp) -> bool:
        return len(a.history.series(a.effective_model() or "—")) >= 1

    assert await harness.wait_for(has_history), (
        f"history not recorded; series={harness.app.history.series(harness.app.effective_model() or '\u2014')}"
    )
    # check payload
    posts = [r for r in harness.server.requests if "messages" in r]
    assert posts, f"no chat POST captured; requests={harness.server.requests}"
    last = posts[-1]
    assert last.get("temperature") == 0.3, last
    assert last.get("top_p") == 0.9, last
    assert last.get("max_tokens") == 256, last
    # invalid values should fallback to defaults
    await harness.pilot.pause()
    harness.app.query_one("#param-temp", Input).value = "nan"
    harness.app.query_one("#param-top-p", Input).value = "inf"
    harness.app.query_one("#param-max-tokens", Input).value = "bad"
    await harness.pilot.pause()
    # clear history to detect next turn
    harness.app.history.clear()
    pane.messages.clear()
    inp.text = "hi2"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(has_history)
    posts2 = [r for r in harness.server.requests if "messages" in r]
    last2 = posts2[-1]
    assert last2.get("temperature") == 0.7, last2
    assert last2.get("top_p") == 1.0, last2
    assert last2.get("max_tokens") == 1024, last2


async def test_ctx_bar_shows_usage_and_amber(harness: AppHarness) -> None:
    from textual.widgets import Static  # noqa: PLC0415

    from mlx_tui.history.tokens import ctx_bar_text  # noqa: PLC0415

    bar = harness.app.query_one("#ctx-bar", Static)
    text = str(bar.render())
    expected = ctx_bar_text(0, harness.app.config.max_ctx)
    # expected like "ctx 0/8.2k" for default 8192
    assert "ctx" in text.lower() and expected.split("/")[1] in text, (
        f"initial bar {text!r} missing {expected}"
    )
    # at 0/max style is default (not amber/red)
    assert bar.has_class("ctx-bar-amber") is False
    assert bar.has_class("ctx-bar-red") is False


async def test_ctx_bar_amber_and_red_thresholds(harness: AppHarness) -> None:
    from textual.widgets import Static  # noqa: PLC0415

    from mlx_tui.history.tokens import _format_k  # noqa: PLC0415

    pane = harness.chat_pane()
    bar = harness.app.query_one("#ctx-bar", Static)
    max_ctx = harness.app.config.max_ctx

    # >80% -> amber
    amber_len = int(max_ctx * 0.85)
    pane.update_ctx_bar(amber_len)
    await harness.pilot.pause()
    text = str(bar.render())
    assert _format_k(amber_len) in text and _format_k(max_ctx) in text, (
        f"{amber_len} text {text!r}"
    )
    assert bar.has_class("ctx-bar-amber") is True
    assert bar.has_class("ctx-bar-red") is False

    # >95% -> red
    red_len = int(max_ctx * 0.96)
    pane.update_ctx_bar(red_len)
    await harness.pilot.pause()
    text = str(bar.render())
    assert _format_k(red_len) in text
    assert bar.has_class("ctx-bar-red") is True

    # back to low -> not amber/red
    pane.update_ctx_bar(100)
    await harness.pilot.pause()
    assert bar.has_class("ctx-bar-amber") is False
    assert bar.has_class("ctx-bar-red") is False


async def test_ctx_bar_updates_after_turn(harness: AppHarness) -> None:
    from textual.widgets import Static  # noqa: PLC0415

    from mlx_tui.history.tokens import _format_k, ctx_bar_text  # noqa: PLC0415

    bar = harness.app.query_one("#ctx-bar", Static)
    max_ctx = harness.app.config.max_ctx
    initial = str(bar.render())
    expected_initial = ctx_bar_text(0, max_ctx)
    assert expected_initial in initial or "ctx 0" in initial.lower()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hello world, this is a test of context bar"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def bar_updated(a: MlxTuiApp) -> bool:
        t = str(a.query_one("#ctx-bar", Static).render())
        return expected_initial not in t and "ctx" in t.lower()

    assert await harness.wait_for(bar_updated), (
        f"bar never updated: {str(bar.render())}"
    )
    # after turn, bar should show non-zero ctx and still max
    text = str(bar.render())
    assert _format_k(max_ctx) in text
    assert expected_initial not in text


async def test_context_reservation_bounds_outgoing_payload(harness: AppHarness) -> None:
    from dataclasses import replace  # noqa: PLC0415
    from typing import cast  # noqa: PLC0415

    from mlx_tui.history.tokens import estimate_prompt_tokens  # noqa: PLC0415

    harness.app.config = replace(
        harness.app.config,
        max_ctx=2048,
        max_tokens=320,
        system="You are a concise assistant for this test.",
    )
    pane = harness.chat_pane()
    pane.apply_config_params(harness.app.config)
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hello"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def request_recorded(a: MlxTuiApp) -> bool:
        return any("messages" in request for request in harness.server.requests)

    assert await harness.wait_for(request_recorded)
    payload = [r for r in harness.server.requests if "messages" in r][-1]
    messages = cast(list[dict[str, str]], payload["messages"])
    max_tokens = cast(int, payload["max_tokens"])
    assert messages[0] == {
        "role": "system",
        "content": "You are a concise assistant for this test.",
    }
    assert estimate_prompt_tokens(messages) + max_tokens <= harness.app.config.max_ctx


async def test_over_limit_chat_rolls_back_without_post(harness: AppHarness) -> None:
    from dataclasses import replace  # noqa: PLC0415

    harness.app.config = replace(
        harness.app.config,
        max_ctx=1024,
        max_tokens=1024,
        system="x" * 4_000,
    )
    pane = harness.chat_pane()
    pane.apply_config_params(harness.app.config)
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "this cannot fit"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def rejected(a: MlxTuiApp) -> bool:
        return not inp.disabled and pane.messages == []

    assert await harness.wait_for(rejected), (
        f"over-limit turn did not recover: messages={pane.messages}, "
        f"log={harness.log_lines()}"
    )
    assert not any("messages" in request for request in harness.server.requests)
    assert any("context limit" in line for line in harness.log_lines())
    assert inp.text == "this cannot fit"
    assert pane._pending_draft is None


async def test_ctx_bar_progress_updates(harness: AppHarness) -> None:
    from dataclasses import replace  # noqa: PLC0415

    from textual.widgets import ProgressBar, TabbedContent  # noqa: PLC0415

    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    bar = harness.app.query_one("#ctx-progress", ProgressBar)
    assert bar.total == 8192
    pane = harness.chat_pane()
    pane.update_ctx_bar(0)
    await harness.pilot.pause()
    assert bar.progress == 0
    assert bar.has_class("ctx-bar-amber") is False
    assert bar.has_class("ctx-bar-red") is False
    pane.update_ctx_bar(int(0.81 * 8192))
    await harness.pilot.pause()
    assert bar.has_class("ctx-bar-amber") is True
    pane.update_ctx_bar(int(0.96 * 8192))
    await harness.pilot.pause()
    assert bar.has_class("ctx-bar-red") is True
    # alias test: config max_ctx 4096
    harness.app.config = replace(harness.app.config, max_ctx=4096)
    pane.update_ctx_bar(4096)
    await harness.pilot.pause()
    assert bar.progress == bar.total
    # reset to default for other tests isolation
    harness.app.config = replace(harness.app.config, max_ctx=8192)
    pane.update_ctx_bar(0)
    await harness.pilot.pause()


async def test_collapsed_summary_tracks_normalized_values(harness: AppHarness) -> None:
    from mlx_tui.config import AppConfig  # noqa: PLC0415
    from mlx_tui.params import ParamsPane  # noqa: PLC0415

    params = harness.app.query_one(ParamsPane)
    params.apply_config(AppConfig(temperature=0.2, top_p=0.8, max_tokens=64))
    await harness.pilot.pause()
    assert params.collapsed
    assert params.title == "Params · temp 0.2 · top-p 0.8 · max 64"
    temp = params.query_one("#param-temp", Input)
    temp.value = "-"
    await harness.pilot.pause()
    assert "temp 0.7" in params.title
    assert temp.value == "-"
    assert not harness.server.requests


async def test_attachment_preview_matches_post_and_survives_source_change(
    harness: AppHarness, tmp_path: Path
) -> None:
    from mlx_tui.text_screen import TextPreviewScreen  # noqa: PLC0415

    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    source = tmp_path / "snippet.py"
    source.write_text("old = 'snapshot'\n", encoding="utf-8")
    pane = harness.chat_pane()
    pane._attachment_added(read_attachment(source))
    snapshot = pane._draft_attachments[0]
    source.write_text("new = 'live source'\n", encoding="utf-8")

    prompt = "Review this file"
    harness.app.query_one("#chat-input", ChatInput).text = prompt
    await harness.pilot.click("#btn-context-preview")
    await harness.pilot.pause()
    preview = harness.app.screen
    assert isinstance(preview, TextPreviewScreen)
    preview_text = preview.query_one("#text-preview-area", TextArea).text
    expected = render_user_content(prompt, (snapshot,))
    assert expected in preview_text
    assert str(harness.app.port) in preview_text
    await harness.pilot.press("escape")

    harness.app.query_one("#chat-input", ChatInput).focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda _app: not pane.has_live_turn)
    posts = [request for request in harness.server.requests if "messages" in request]
    assert posts
    post = posts[-1]
    assert isinstance(post, dict)
    messages = post.get("messages")
    assert isinstance(messages, list) and messages
    last_message = messages[-1]
    assert isinstance(last_message, dict)
    sent = last_message.get("content")
    assert isinstance(sent, str)
    assert sent == expected
    assert "live source" not in sent
    assert pane._session is not None
    assert pane._session.attempts[-1].attachments == (snapshot,)


async def test_add_file_uses_native_macos_picker(
    harness: AppHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "chosen.py"
    source.write_text("chosen = True\n", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []
    outputs = iter((f"{source}\n", "\n"))

    def choose(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=next(outputs))

    monkeypatch.setattr(chat_widgets.subprocess, "run", choose)
    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    await harness.pilot.click("#btn-add-attachment")
    pane = harness.chat_pane()
    assert await harness.wait_for(lambda _app: len(pane._draft_attachments) == 1)
    assert calls[0][0][:2] == ["/usr/bin/osascript", "-e"]
    assert calls[0][1] == {
        "capture_output": True,
        "text": True,
        "check": False,
    }
    assert pane._draft_attachments[0].selected_path == source
    await harness.pilot.click("#btn-add-attachment")
    assert await harness.wait_for(lambda _app: not pane._attachment_picker_active)
    assert len(pane._draft_attachments) == 1


async def test_attachment_send_is_rejected_for_non_loopback_destination(
    harness: AppHarness, tmp_path: Path
) -> None:
    source = tmp_path / "snippet.txt"
    source.write_text("private", encoding="utf-8")
    pane = harness.chat_pane()
    pane._attachment_added(read_attachment(source))
    harness.app.host = "example.com"
    harness.app.query_one("#chat-input", ChatInput).text = "send this"
    before = len(
        [request for request in harness.server.requests if "messages" in request]
    )
    await harness.pilot.press("ctrl+enter")
    await harness.pilot.pause()
    after = len(
        [request for request in harness.server.requests if "messages" in request]
    )
    assert after == before
