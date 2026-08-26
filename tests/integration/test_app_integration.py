from __future__ import annotations

import asyncio
import re

import pytest
from textual.widgets import Input, Static

from mlx_tui.app import MlxTuiApp
from tests.conftest import AppHarness

_STAMP_RE = re.compile(
    r"\d+( \(est\))? in · \d+( \(est\))? out · [\d.]+ tok/s · TTFT [\d.]+s( · cold)?"
)
_REPLY = "Hello world this is MLX."


async def test_status_green_and_chat_stamp_over_stub_http(harness: AppHarness) -> None:
    await harness.app._poll()
    assert harness.app.status_state == "green"
    assert harness.app.cold_tracker.ever_green is True
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")
    assert inp.value == ""

    def stamp_visible(a: MlxTuiApp) -> bool:
        return any("tok/s" in t for t in harness.log_lines())

    assert await harness.wait_for(stamp_visible), (
        f"stamp never appeared; log={harness.log_lines()}"
    )
    texts = harness.log_lines()
    assert _REPLY in texts
    stamps = [t for t in texts if "tok/s" in t]
    assert len(stamps) == 1
    stamp_line = stamps[0]
    assert _STAMP_RE.fullmatch(stamp_line), stamp_line
    assert "12 in · 6 out" in stamp_line
    assert "(est)" not in stamp_line
    assert "cold" not in stamp_line
    stream = harness.app.query_one("#chat-stream", Static)
    assert stream.content == ""


async def test_chat_payload_carries_effective_model(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(harness.app, "effective_model", lambda: "mlx-community/stub-test")
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")

    def reply_recorded(a: MlxTuiApp) -> bool:
        return len(harness.chat_pane().messages) == 2

    assert await harness.wait_for(reply_recorded), (
        f"turn never completed; messages={harness.chat_pane().messages}"
    )
    posts = [r for r in harness.server.requests if "messages" in r]
    assert posts, f"no chat POST captured; requests={harness.server.requests}"
    assert posts[-1].get("model") == "mlx-community/stub-test"


async def test_chat_payload_omits_model_when_unknown(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(harness.app, "effective_model", lambda: None)
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")

    def reply_recorded(a: MlxTuiApp) -> bool:
        return len(harness.chat_pane().messages) == 2

    assert await harness.wait_for(reply_recorded), (
        f"turn never completed; messages={harness.chat_pane().messages}"
    )
    posts = [r for r in harness.server.requests if "messages" in r]
    assert posts, f"no chat POST captured; requests={harness.server.requests}"
    assert "model" not in posts[-1]


async def test_assistant_reply_recorded_in_history(harness: AppHarness) -> None:
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")

    def reply_recorded(a: MlxTuiApp) -> bool:
        return len(harness.chat_pane().messages) == 2

    assert await harness.wait_for(reply_recorded), (
        f"assistant reply never recorded; messages={harness.chat_pane().messages}"
    )
    assert harness.chat_pane().messages[0] == {"role": "user", "content": "hi"}
    assert harness.chat_pane().messages[1] == {"role": "assistant", "content": _REPLY}


async def test_empty_response_warns_instead_of_silence(harness: AppHarness) -> None:
    harness.server.mode = "empty"
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")

    def warning_visible(a: MlxTuiApp) -> bool:
        return any("model returned no text" in t for t in harness.log_lines())

    assert await harness.wait_for(warning_visible), (
        f"empty-response warning never appeared; log={harness.log_lines()}"
    )
    # A stamp still lands (the turn did complete), but nothing was appended.
    assert any("tok/s" in t for t in harness.log_lines())
    assert harness.chat_pane().messages == [{"role": "user", "content": "hi"}]

    def input_enabled(a: MlxTuiApp) -> bool:
        return not a.query_one("#chat-input", Input).disabled

    assert await harness.wait_for(input_enabled)


async def test_length_capped_reply_shows_notice(harness: AppHarness) -> None:
    harness.server.mode = "length_cap"
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")

    def cap_notice_visible(a: MlxTuiApp) -> bool:
        return any("token cap" in t for t in harness.log_lines())

    assert await harness.wait_for(cap_notice_visible), (
        f"cap notice never appeared; log={harness.log_lines()}"
    )
    # The partial reply is still a real answer: shown and recorded.
    assert "Partial ans" in harness.log_lines()
    assert harness.chat_pane().messages[-1] == {
        "role": "assistant",
        "content": "Partial ans",
    }

    def input_enabled(a: MlxTuiApp) -> bool:
        return not a.query_one("#chat-input", Input).disabled

    assert await harness.wait_for(input_enabled)


async def test_non_mlx_http_shows_amber(harness: AppHarness) -> None:
    harness.server.mode = "html"
    await harness.app._poll()
    assert harness.app.status_state == "amber"


async def test_cancel_closes_stream(harness: AppHarness) -> None:
    harness.server.mode = "slow"
    await harness.app._poll()
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")

    def submitted(a: MlxTuiApp) -> bool:
        return any(t.startswith("you ›") for t in harness.log_lines())

    assert await harness.wait_for(submitted)
    await harness.pilot.press("escape")

    def cancelled(a: MlxTuiApp) -> bool:
        return any("cancelled — request aborted" in t for t in harness.log_lines())

    assert await harness.wait_for(cancelled)

    def input_enabled(a: MlxTuiApp) -> bool:
        return not a.query_one("#chat-input", Input).disabled

    assert await harness.wait_for(input_enabled)


async def test_server_error_status_shows_red_line_not_fake_stamp(
    harness: AppHarness,
) -> None:
    harness.server.mode = "error500"
    await harness.app._poll()
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")

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
    stream = harness.app.query_one("#chat-stream", Static)
    assert stream.content == ""

    def input_enabled(a: MlxTuiApp) -> bool:
        return not a.query_one("#chat-input", Input).disabled

    assert await harness.wait_for(input_enabled)


async def test_truncated_stream_red_line_and_no_stale_pane(harness: AppHarness) -> None:
    harness.server.mode = "truncated"
    await harness.app._poll()
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")

    def unreachable(a: MlxTuiApp) -> bool:
        return any(t.startswith("server unreachable") for t in harness.log_lines())

    assert await harness.wait_for(unreachable), (
        f"red line never appeared; log={harness.log_lines()}"
    )
    stream = harness.app.query_one("#chat-stream", Static)
    assert stream.content == ""
    assert not any("tok/s" in t for t in harness.log_lines())

    def input_enabled(a: MlxTuiApp) -> bool:
        return not a.query_one("#chat-input", Input).disabled

    assert await harness.wait_for(input_enabled)
