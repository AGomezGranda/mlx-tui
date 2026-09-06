from __future__ import annotations

import asyncio
import re
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Input, Static

from mlx_tui.app import MlxTuiApp
from mlx_tui.app.operations import OperationKind
from mlx_tui.history.store import TurnRecord
from mlx_tui.models import ModelRow
from tests.conftest import AppHarness

_STAMP_RE = re.compile(
    r"(?:▎ )?\d+( \(est\))? in · \d+( \(est\))? out · (?:\d+ prefill tok/s · [\d.]+ decode tok/s|[\d.]+ tok/s) · TTFT [\d.]+s( · cold)?"
)
_REPLY = "Hello world this is MLX."


def _current_op(app: MlxTuiApp) -> OperationKind:
    """Fresh-read helper: the checker must not narrow this across mutations."""
    return app.operations.current


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


async def test_chat_multiline_event_streams_without_malformed_notice(
    stub_server_factory,  # type: ignore[no-untyped-def]
) -> None:
    from mlx_tui.app import MlxTuiApp  # noqa: PLC0415
    from tests.conftest import AppHarness  # noqa: PLC0415

    server = stub_server_factory("multiline")
    app = MlxTuiApp(host="127.0.0.1", port=int(server.server_address[1]))
    async with app.run_test() as pilot:
        harness = AppHarness(app=app, pilot=pilot, server=server)
        await harness.app._poll()
        inp = harness.app.query_one("#chat-input", Input)
        inp.value = "hi"
        inp.focus()
        await harness.pilot.press("enter")

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
    app = MlxTuiApp(host="127.0.0.1", port=int(server.server_address[1]))
    async with app.run_test() as pilot:
        harness = AppHarness(app=app, pilot=pilot, server=server)
        await harness.app._poll()
        inp = harness.app.query_one("#chat-input", Input)
        inp.value = "hi"
        inp.focus()
        await harness.pilot.press("enter")

        def stamp_visible(a: MlxTuiApp) -> bool:
            return any("tok/s" in t for t in harness.log_lines())

        assert await harness.wait_for(stamp_visible), (
            f"stamp never appeared; log={harness.log_lines()}"
        )
        assert _REPLY in harness.log_lines()


async def test_chat_payload_carries_effective_model(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        harness.app, "effective_model", lambda: "mlx-community/stub-test"
    )
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
        return any(t.lstrip("▎ ").startswith("you ›") for t in harness.log_lines())

    assert await harness.wait_for(submitted)
    await harness.pilot.press("escape")

    def cancelled(a: MlxTuiApp) -> bool:
        return any("cancelled — request aborted" in t for t in harness.log_lines())

    assert await harness.wait_for(cancelled)

    def input_enabled(a: MlxTuiApp) -> bool:
        return not a.query_one("#chat-input", Input).disabled

    assert await harness.wait_for(input_enabled)


async def test_chat_lease_blocks_swap_and_delete_until_cleanup(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness.server.mode = "slow"
    await harness.app._poll()
    models = harness.models_pane()
    models._populate(
        [ModelRow("mlx-community/other-model", 1_000_000_000, "4bit", True, ("x",))]
    )
    table = harness.app.query_one("#models-table")
    table.focus()

    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")
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


async def test_metrics_tab_renders_and_refreshes(harness: AppHarness) -> None:
    from textual.widgets import DataTable  # noqa: PLC0415

    from mlx_tui.history.store import MemoryRecord  # noqa: PLC0415
    from mlx_tui.metrics_pane import MetricsPane  # noqa: PLC0415

    def spark_text(wid_id: str) -> str:
        s = harness.app.query_one(wid_id, Static)
        try:
            return str(s.render())
        except Exception:
            return str(getattr(s, "content", ""))

    table = harness.app.query_one("#metrics-table", DataTable)
    assert table is not None
    # initial placeholders
    harness.app.history.clear()
    harness.app.memory_store.clear()
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    assert "no history yet" in spark_text("#metrics-sparkline")
    assert "no memory samples" in spark_text("#metrics-memory-sparkline")

    # add one turn + one memory sample
    harness.app.history.add(
        TurnRecord(
            ts=time.time(),
            model="metrics-model",
            prompt_tok=10,
            out_tok=5,
            ttft_s=0.1,
            tok_s=12.3,
            ctx_len=100,
            cold=False,
        )
    )
    harness.app.memory_store.add(
        MemoryRecord(
            ts=time.time(),
            model="metrics-model",
            rss_gib=1.2,
            avail_gib=8.0,
            total_gib=16.0,
        )
    )
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    # table capped at 64
    assert table.row_count == 1
    assert "tok/s" in spark_text("#metrics-sparkline")
    assert "avail" in spark_text("#metrics-memory-sparkline")
    assert "no history yet" not in spark_text("#metrics-sparkline")

    # cleanup
    harness.app.history.clear()
    harness.app.memory_store.clear()
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()


async def test_chat_renders_markdown(harness: AppHarness) -> None:
    from textual.widgets import RichLog, TabbedContent  # noqa: PLC0415

    from mlx_tui.chat_pane import ChatPane  # noqa: PLC0415

    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    # Directly invoke completion UI with markdown to avoid stub coupling.
    # Rendering alone must not commit to conversation history.
    pane = harness.app.query_one(ChatPane)
    assert pane.messages == []
    pane._complete_turn_ui(
        "# hi\n\n**bold**", "12 in · 6 out · 10.0 tok/s · TTFT 0.10s", False, []
    )
    await harness.pilot.pause()
    # markdown rendered: raw "# hi" should not appear, but heading text should
    lines = harness.log_lines()
    assert not any(line.strip() == "# hi" for line in lines)
    assert any("hi" in line for line in lines)
    assert any("bold" in line for line in lines)
    # stamp still present and dim style is via Text but line contains tok/s
    assert any("tok/s" in line for line in lines)
    assert pane.messages == []
    # stream cleared
    assert harness.app.query_one("#chat-stream", Static).content == ""
    # check RichLog contains markdown rendering (fallback substring check already covers)
    log = harness.app.query_one("#chat-log", RichLog)
    # ensure log has at least the heading and bold lines
    assert len(log.lines) >= 2
    # Also verify through a full turn that stream clears after real HTTP round-trip
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")

    def done(a: MlxTuiApp) -> bool:
        return len(a.query_one(ChatPane).messages) >= 2

    assert await harness.wait_for(done)
    assert harness.app.query_one("#chat-stream", Static).content == ""


async def test_params_sidebar_sends_payload(harness: AppHarness) -> None:
    from textual.widgets import TabbedContent  # noqa: PLC0415

    from mlx_tui.chat_pane import ChatPane  # noqa: PLC0415

    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    pane = harness.app.query_one(ChatPane)
    # set valid params
    harness.app.query_one("#param-temp", Input).value = "0.3"
    harness.app.query_one("#param-top-p", Input).value = "0.9"
    harness.app.query_one("#param-max-tokens", Input).value = "256"
    await harness.pilot.pause()
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")

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
    harness.app.query_one("#param-temp", Input).value = "bad"
    harness.app.query_one("#param-top-p", Input).value = "bad"
    harness.app.query_one("#param-max-tokens", Input).value = "bad"
    await harness.pilot.pause()
    # clear history to detect next turn
    harness.app.history.clear()
    pane.messages.clear()
    inp.value = "hi2"
    inp.focus()
    await harness.pilot.press("enter")
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
    from textual.widgets import Input, Static  # noqa: PLC0415

    from mlx_tui.history.tokens import _format_k, ctx_bar_text  # noqa: PLC0415

    bar = harness.app.query_one("#ctx-bar", Static)
    max_ctx = harness.app.config.max_ctx
    initial = str(bar.render())
    expected_initial = ctx_bar_text(0, max_ctx)
    assert expected_initial in initial or "ctx 0" in initial.lower()
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hello world, this is a test of context bar"
    inp.focus()
    await harness.pilot.press("enter")

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
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hello"
    inp.focus()
    await harness.pilot.press("enter")

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
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "this cannot fit"
    inp.focus()
    await harness.pilot.press("enter")

    def rejected(a: MlxTuiApp) -> bool:
        return not inp.disabled and pane.messages == []

    assert await harness.wait_for(rejected), (
        f"over-limit turn did not recover: messages={pane.messages}, "
        f"log={harness.log_lines()}"
    )
    assert not any("messages" in request for request in harness.server.requests)
    assert any("context limit" in line for line in harness.log_lines())


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


async def test_memory_bar_renders(harness: AppHarness) -> None:
    from textual.widgets import ProgressBar  # noqa: PLC0415

    await harness.pilot.pause()
    await harness.app._poll()
    await harness.pilot.pause()
    bar = harness.app.query_one("#memory-bar", ProgressBar)
    label = harness.app.query_one("#memory-label", Static)
    assert bar is not None
    assert bar.total is not None and bar.total > 0
    # progress should be 0 or rss_gib when stub green (rss None → 0)
    assert bar.progress is not None and bar.progress >= 0
    text = str(label.render())
    assert "avail" in text.lower()
    assert "RSS" in text
    # total should be around snapshot total (or default 16 when unknown)
    # we don't assert exact since psutil virtual_memory varies per CI host
    assert bar.total is not None and bar.total > 0


async def test_prefill_vs_decode_stamp_and_table(harness: AppHarness) -> None:  # noqa: PLR0915
    from textual.widgets import DataTable, Input, TabbedContent  # noqa: PLC0415

    from mlx_tui.metrics_pane import MetricsPane  # noqa: PLC0415

    # ok mode -> usage present -> prefill computed
    harness.server.mode = "ok"
    harness.app.history.clear()
    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")

    def has_one(a: MlxTuiApp) -> bool:
        return len(a.history.all_records()) == 1

    assert await harness.wait_for(has_one), f"no record; log={harness.log_lines()}"
    rec = harness.app.history.all_records()[0]
    assert rec.prompt_tok == 12
    assert rec.out_tok == 6
    assert rec.prompt_estimated is False
    assert rec.out_estimated is False
    assert rec.prefill_tok_s is not None and rec.prefill_tok_s > 0
    assert rec.tok_s > 0
    texts = harness.log_lines()
    assert any("prefill" in t for t in texts)
    assert any("decode" in t for t in texts)
    assert any("TTFT" in t for t in texts)
    assert "(est)" not in [t for t in texts if "tok/s" in t][-1]
    # metrics table shows prefill not —
    harness.app.query_one(TabbedContent).active = "metrics"
    await harness.pilot.pause()
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    table = harness.app.query_one("#metrics-table", DataTable)
    assert table.row_count == 1
    # check prefill column (key prefill) not —
    # DataTable get_cell_at not stable across textual versions; check via row data string rendering
    # We can inspect that the table's rendered rows contain prefill value; simpler assert via history record
    assert rec.prefill_tok_s is not None
    from textual.coordinate import Coordinate  # noqa: PLC0415

    assert "~" not in str(table.get_cell_at(Coordinate(0, 6)))
    assert "~" not in str(table.get_cell_at(Coordinate(0, 7)))

    # no_usage mode -> no prefill
    harness.server.mode = "no_usage"
    harness.app.history.clear()
    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi2"
    inp.focus()
    await harness.pilot.press("enter")
    assert await harness.wait_for(has_one)
    rec2 = harness.app.history.all_records()[0]
    assert rec2.prefill_tok_s is None
    assert rec2.prompt_estimated is True
    assert rec2.out_estimated is True
    texts2 = harness.log_lines()
    # last stamp should contain tok/s but not prefill (single rate)
    # find the last stamp line
    stamps = [t for t in texts2 if "tok/s" in t]
    assert stamps, f"no stamp {texts2}"
    last = stamps[-1]
    assert "prefill" not in last
    assert "tok/s" in last
    assert "(est)" in last
    # metrics prefill cell should be —
    harness.app.query_one(TabbedContent).active = "metrics"
    await harness.pilot.pause()
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    table2 = harness.app.query_one("#metrics-table", DataTable)
    assert table2.row_count == 1
    assert "~" in str(table2.get_cell_at(Coordinate(0, 6)))
    assert "~" in str(table2.get_cell_at(Coordinate(0, 7)))
    # cleanup
    harness.app.history.clear()
    harness.server.mode = "ok"


async def test_preset_cycle_drives_params_and_system(  # noqa: PLR0915
    harness: AppHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace  # noqa: PLC0415

    from textual.widgets import Input as _Input  # noqa: PLC0415
    from textual.widgets import TabbedContent  # noqa: PLC0415

    from mlx_tui.chat_pane import ChatPane  # noqa: PLC0415
    from mlx_tui.presets import load_presets  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    presets_file = tmp_path / "mlx-tui" / "presets.toml"
    presets_file.parent.mkdir(parents=True, exist_ok=True)
    presets_file.write_text(
        '[[preset]]\nname = "a"\nsystem = "You are A"\ntemperature = 0.2\n'
        '[[preset]]\nname = "b"\nsystem = "You are B"\ntop_p = 0.5\nmax_tokens = 512\n'
    )
    harness.app.presets = load_presets(presets_file)
    harness.app.preset_idx = -1
    harness.app.config = replace(harness.app.config, max_ctx=32768)
    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    # direct action kept: pilot.press for ctrl+p was unreliable in textual 8.2.8; ctrl+n forward is now reliable but direct keeps test stable
    harness.app.action_cycle_preset()
    await harness.pilot.pause()
    assert harness.app.config.max_ctx == 32768
    assert harness.app.config.system == "You are A"
    assert harness.app.query_one("#param-temp", _Input).value == "0.2"
    assert any("preset: a" in line for line in harness.app_log_lines())
    harness.app.action_cycle_preset()
    await harness.pilot.pause()
    assert harness.app.config.max_ctx == 32768
    assert harness.app.config.system == "You are B"
    assert harness.app.query_one("#param-top-p", _Input).value == "0.5"
    assert harness.app.query_one("#param-max-tokens", _Input).value == "512"
    assert any("preset: b" in line for line in harness.app_log_lines())
    harness.app.action_cycle_preset_back()
    await harness.pilot.pause()
    assert harness.app.config.max_ctx == 32768
    assert harness.app.config.system == "You are A"

    bindings: dict[str, str] = {}
    for b in harness.app.BINDINGS:
        k = getattr(b, "key", None)
        a = getattr(b, "action", None)
        if k is None and isinstance(b, tuple):
            k, a = b[0], b[1]  # type: ignore[misc]
        if isinstance(k, str) and isinstance(a, str):
            bindings[k] = a
    assert bindings.get("ctrl+n") == "cycle_preset"
    assert bindings.get("ctrl+o") == "cycle_preset_back"
    harness.server.requests.clear()
    inp = harness.app.query_one("#chat-input", _Input)
    inp.value = "hi preset"
    inp.focus()
    await harness.pilot.press("enter")

    def reply_done(a: MlxTuiApp) -> bool:
        return len(a.query_one(ChatPane).messages) >= 2

    assert await harness.wait_for(reply_done)
    posts = [r for r in harness.server.requests if "messages" in r]
    assert posts, f"no POST; {harness.server.requests}"
    last = posts[-1]
    msgs = last.get("messages", [])
    assert isinstance(msgs, list) and len(msgs) >= 2
    assert msgs[0] == {"role": "system", "content": "You are A"}


async def test_config_reload_clears_removed_system_prompt(
    harness: AppHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import nullcontext  # noqa: PLC0415
    from dataclasses import replace  # noqa: PLC0415

    import mlx_tui.app as app_mod  # noqa: PLC0415
    from mlx_tui.chat_pane import ChatPane  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_file = tmp_path / "mlx-tui" / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('system = "stale system prompt"\n')
    harness.app.config = replace(harness.app.config, system="stale system prompt")
    pane = harness.app.query_one(ChatPane)
    pane.apply_config_params(harness.app.config)
    monkeypatch.setattr(harness.app, "suspend", nullcontext)

    def fake_editor(_command: list[str], check: bool) -> SimpleNamespace:
        assert check is False
        config_file.write_text("max_tokens = 256\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(app_mod.subprocess, "run", fake_editor)
    harness.app.action_edit_config()
    await harness.pilot.pause()

    assert harness.app.config.system is None
    assert harness.app.query_one("#param-max-tokens", Input).value == "256"

    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "without system"
    inp.focus()
    await harness.pilot.press("enter")

    def reply_done(_app: MlxTuiApp) -> bool:
        return len(pane.messages) == 2

    assert await harness.wait_for(reply_done)
    posts = [request for request in harness.server.requests if "messages" in request]
    assert posts
    messages = posts[-1]["messages"]
    assert isinstance(messages, list)
    assert not any(message.get("role") == "system" for message in messages)


async def test_polling_follows_endpoint_not_cmdline(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace  # noqa: PLC0415

    import psutil  # noqa: PLC0415

    import mlx_tui.process as proc_mod  # noqa: PLC0415

    harness.server.model_id = "mlx-community/server-a"
    proc_mod._cached_identity = None  # type: ignore[attr-defined]
    proc_mod._net_denied = False  # type: ignore[attr-defined]

    fake_pid = 4242
    other_cmdline = ["python", "-m", "mlx_lm.server", "--model", "other/model"]

    class _FakeProc:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def cmdline(self) -> list[str]:
            return other_cmdline

        def create_time(self) -> float:
            return 111.0

        def memory_info(self) -> SimpleNamespace:
            return SimpleNamespace(rss=1 * 2**30)

    def _fake_conns(kind: str = "tcp") -> list[SimpleNamespace]:
        assert kind == "tcp"
        return [
            SimpleNamespace(
                pid=fake_pid, status="LISTEN", laddr=("127.0.0.1", harness.port)
            )
        ]

    monkeypatch.setattr(psutil, "Process", _FakeProc)
    monkeypatch.setattr(psutil, "net_connections", _fake_conns)

    await harness.app._poll()
    assert harness.app.status_state == "green"
    assert harness.app.server_identity.model_id == "mlx-community/server-a"
    assert harness.app.effective_model() == "mlx-community/server-a"
    assert harness.app.server_identity.pid == fake_pid

    # Loaded marker follows the endpoint ID, not the cmdline model.
    from mlx_tui.table import loaded_cell  # noqa: PLC0415

    assert loaded_cell("mlx-community/server-a", harness.app.effective_model()) == "●"
    assert loaded_cell("other/model", harness.app.effective_model()) == ""

    # Chat payload follows the endpoint ID too.
    harness.server.requests.clear()
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi"
    inp.focus()
    await harness.pilot.press("enter")

    def reply_done(a: MlxTuiApp) -> bool:
        return len(harness.chat_pane().messages) == 2

    assert await harness.wait_for(reply_done)
    posts = [r for r in harness.server.requests if "messages" in r]
    assert posts
    assert posts[-1].get("model") == "mlx-community/server-a"


async def test_external_restart_replaces_identity(harness: AppHarness) -> None:
    import mlx_tui.process as proc_mod  # noqa: PLC0415

    proc_mod._cached_identity = None  # type: ignore[attr-defined]
    proc_mod._net_denied = False  # type: ignore[attr-defined]
    harness.server.model_id = "mlx-community/v1"
    await harness.app._poll()
    assert harness.app.effective_model() == "mlx-community/v1"
    first = harness.app.server_identity
    harness.server.model_id = "mlx-community/v2"
    await harness.app._poll()
    assert harness.app.effective_model() == "mlx-community/v2"
    assert harness.app.server_identity.model_id == "mlx-community/v2"
    assert harness.app.server_identity != first


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
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi before headers"
    inp.focus()
    await harness.pilot.press("enter")
    assert await asyncio.wait_for(entered.wait(), timeout=2)
    assert pane.has_live_turn

    start = time.monotonic()
    pane.abort()
    assert any("cancellation requested" in t for t in harness.log_lines()), (
        f"log={harness.log_lines()}"
    )

    def cancelled(a: MlxTuiApp) -> bool:
        return any("cancelled — request aborted" in t for t in harness.log_lines())

    assert await harness.wait_for(cancelled)
    assert time.monotonic() - start < 2

    def cleanup(a: MlxTuiApp) -> bool:
        return (
            harness.app.operations.current is OperationKind.IDLE
            and not pane.has_live_turn
            and not harness.app.query_one("#chat-input", Input).disabled
        )

    assert await harness.wait_for(cleanup)
    assert pane.messages == []
    assert (
        len([t for t in harness.log_lines() if "cancelled — request aborted" in t]) == 1
    )
    assert not any("tok/s" in t for t in harness.log_lines())
    await asyncio.sleep(0.3)
    assert (
        len([t for t in harness.log_lines() if "cancelled — request aborted" in t]) == 1
    )
    assert harness.app.query_one("#chat-stream", Static).content == ""


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
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi idle"
    inp.focus()
    await harness.pilot.press("enter")
    assert await asyncio.wait_for(entered.wait(), timeout=2)

    start = time.monotonic()
    pane.abort()
    assert await harness.wait_for(
        lambda a: any("cancelled — request aborted" in t for t in harness.log_lines())
    )
    assert time.monotonic() - start < 2
    assert await asyncio.wait_for(closed.wait(), timeout=2)
    assert pane.messages == []
    assert harness.app.operations.current is OperationKind.IDLE
    assert not harness.app.query_one("#chat-input", Input).disabled


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
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi partial"
    inp.focus()
    await harness.pilot.press("enter")
    assert await asyncio.wait_for(started.wait(), timeout=2)
    pane.abort()
    assert await harness.wait_for(
        lambda a: any("cancelled — request aborted" in t for t in harness.log_lines())
    )
    assert await asyncio.wait_for(closed.wait(), timeout=2)
    assert pane.messages == []
    assert harness.app.operations.current is OperationKind.IDLE
    assert not harness.app.query_one("#chat-input", Input).disabled
    assert not any("tok/s" in t for t in harness.log_lines())
    await asyncio.sleep(0.3)
    assert (
        len([t for t in harness.log_lines() if "cancelled — request aborted" in t]) == 1
    )


async def test_cancel_before_task_start_makes_no_request(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx  # noqa: PLC0415
    from textual.widgets import Input as _Input  # noqa: PLC0415

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    _install_async_mock(monkeypatch, handler)
    monkeypatch.setattr(harness.app, "effective_model", lambda: "test-model")
    pane = harness.chat_pane()
    assert pane.messages == []
    inp = harness.app.query_one("#chat-input", _Input)
    event = _Input.Submitted(inp, "hi pre-start")
    pane._on_input_submitted(event)  # type: ignore[arg-type]
    pane.abort()
    assert await harness.wait_for(
        lambda a: any("cancelled — request aborted" in t for t in harness.log_lines())
    )
    assert await harness.wait_for(
        lambda a: (
            harness.app.operations.current is OperationKind.IDLE
            and not pane.has_live_turn
        )
    )
    assert not called
    assert pane.messages == []
    assert (
        len([t for t in harness.log_lines() if "cancelled — request aborted" in t]) == 1
    )


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
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hi repeat"
    inp.focus()
    await harness.pilot.press("enter")
    assert await asyncio.wait_for(entered.wait(), timeout=2)
    pane.abort()
    pane.abort()
    pane.abort()
    assert await harness.wait_for(
        lambda a: any("cancelled — request aborted" in t for t in harness.log_lines())
    )
    assert await harness.wait_for(
        lambda a: harness.app.operations.current is OperationKind.IDLE
    )
    await asyncio.sleep(0.3)
    assert (
        len([t for t in harness.log_lines() if "cancellation requested" in t]) == 1
    ), f"log={harness.log_lines()}"
    assert (
        len([t for t in harness.log_lines() if "cancelled — request aborted" in t]) == 1
    )


async def test_failed_prompt_excluded_from_next_payload(harness: AppHarness) -> None:
    harness.server.mode = "ok"
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "first good"
    inp.focus()
    await harness.pilot.press("enter")

    def two_messages(a: MlxTuiApp) -> bool:
        return len(harness.chat_pane().messages) == 2

    assert await harness.wait_for(two_messages)
    assert harness.chat_pane().messages[0] == {"role": "user", "content": "first good"}

    harness.server.mode = "error500"
    harness.server.requests.clear()
    inp.value = "will fail"
    inp.focus()
    await harness.pilot.press("enter")

    def failed(a: MlxTuiApp) -> bool:
        return any("server error" in t for t in harness.log_lines())

    assert await harness.wait_for(failed)
    assert await harness.wait_for(
        lambda a: not harness.app.query_one("#chat-input", Input).disabled
    )
    assert len(harness.chat_pane().messages) == 2
    assert harness.chat_pane().messages[0]["content"] == "first good"

    harness.server.mode = "ok"
    harness.server.requests.clear()
    inp.value = "second good"
    inp.focus()
    await harness.pilot.press("enter")
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
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "prior kept"
    inp.focus()
    await harness.pilot.press("enter")
    assert await harness.wait_for(lambda a: len(harness.chat_pane().messages) == 2)

    harness.server.mode = "slow"
    await harness.app._poll()
    harness.server.requests.clear()
    inp.value = "to cancel"
    inp.focus()
    await harness.pilot.press("enter")
    assert await harness.wait_for(lambda a: harness.chat_pane().has_live_turn)
    await harness.pilot.press("escape")
    assert await harness.wait_for(
        lambda a: any("cancelled — request aborted" in t for t in harness.log_lines())
    )
    assert await harness.wait_for(
        lambda a: harness.app.operations.current is OperationKind.IDLE
    )
    assert len(harness.chat_pane().messages) == 2
    assert harness.chat_pane().messages[0]["content"] == "prior kept"

    harness.server.mode = "ok"
    harness.server.requests.clear()
    inp.value = "after cancel"
    inp.focus()
    await harness.pilot.press("enter")
    assert await harness.wait_for(lambda a: len(harness.chat_pane().messages) == 4)
    posts = [r for r in harness.server.requests if "messages" in r]
    assert posts
    sent = posts[-1]["messages"]
    assert isinstance(sent, list)
    texts = [m.get("content") for m in sent if isinstance(m, dict)]
    assert "to cancel" not in texts
    assert "prior kept" in texts
    assert "after cancel" in texts


def _poll_failed_lines(harness: AppHarness) -> list[str]:
    return [t for t in harness.app_log_lines() if "poll failed" in t]


async def test_log_error_once_dedups_until_cleared(harness: AppHarness) -> None:
    app = harness.app
    app.log_error_once("poll", RuntimeError("boom"))
    app.log_error_once("poll", RuntimeError("boom"))
    assert len(_poll_failed_lines(harness)) == 1
    app.log_error_once("poll", ValueError("other"))
    assert len(_poll_failed_lines(harness)) == 2
    app.clear_error("poll")
    app.log_error_once("poll", RuntimeError("boom"))
    assert len(_poll_failed_lines(harness)) == 3
    assert "RuntimeError: boom" in _poll_failed_lines(harness)[-1]


async def test_poll_connection_failure_stays_red_with_single_diagnostic(
    harness: AppHarness,
) -> None:
    harness.server.shutdown()
    harness.server.server_close()
    for _ in range(3):
        await harness.app._poll()
    assert harness.app.status_state == "red"
    assert harness.app.effective_model() is None
    assert harness.app.server_identity.model_id is None
    assert harness.app._poll_in_flight is False
    lines = _poll_failed_lines(harness)
    assert len(lines) == 1, harness.app_log_lines()
    assert "ConnectError" in lines[0]
    assert harness.app.operations.current is OperationKind.IDLE
    assert not harness.app.query_one("#chat-input", Input).disabled


async def test_poll_unexpected_fault_keeps_state_and_logs_once(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await harness.app._poll()
    assert harness.app.status_state == "green"
    assert harness.app.server_identity.model_id == "mlx-community/stub-test"
    orig_probe = MlxTuiApp._fetch_probe

    async def boom_probe(self: MlxTuiApp):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    async def other_probe(self: MlxTuiApp):  # type: ignore[no-untyped-def]
        raise ValueError("other")

    monkeypatch.setattr(MlxTuiApp, "_fetch_probe", boom_probe)
    await harness.app._poll()
    assert harness.app.status_state == "green"
    assert harness.app.server_identity.model_id == "mlx-community/stub-test"
    assert harness.app._poll_in_flight is False
    assert len(_poll_failed_lines(harness)) == 1
    assert "RuntimeError: boom" in _poll_failed_lines(harness)[0]

    # Identical repeats stay silent; a changed error logs immediately.
    await harness.app._poll()
    assert len(_poll_failed_lines(harness)) == 1
    monkeypatch.setattr(MlxTuiApp, "_fetch_probe", other_probe)
    await harness.app._poll()
    assert len(_poll_failed_lines(harness)) == 2
    assert harness.app.status_state == "green"

    # Recovery clears the source so a recurrence becomes visible again.
    monkeypatch.setattr(MlxTuiApp, "_fetch_probe", orig_probe)
    await harness.app._poll()
    assert harness.app.status_state == "green"
    monkeypatch.setattr(MlxTuiApp, "_fetch_probe", boom_probe)
    await harness.app._poll()
    assert len(_poll_failed_lines(harness)) == 3

    assert harness.app.operations.current is OperationKind.IDLE
    assert not harness.app.query_one("#chat-input", Input).disabled


async def test_poll_malformed_json_is_amber_with_single_diagnostic(
    harness: AppHarness,
) -> None:
    harness.server.mode = "html"
    await harness.app._poll()
    assert harness.app.status_state == "amber"
    assert harness.app.effective_model() is None
    await harness.app._poll()
    assert len(_poll_failed_lines(harness)) == 1, harness.app_log_lines()

    harness.server.mode = "ok"
    await harness.app._poll()
    assert harness.app.status_state == "green"
    harness.server.mode = "html"
    await harness.app._poll()
    assert harness.app.status_state == "amber"
    assert len(_poll_failed_lines(harness)) == 2


@contextmanager
def _stub_suspend(entered: list[bool], exited: list[bool]) -> Generator[None]:
    entered.append(True)
    try:
        yield
    finally:
        exited.append(True)


def _config_model(app: MlxTuiApp) -> str | None:
    """Fresh-read helper: the checker must not narrow this across mutations."""
    return app.config.model


def _stub_editor(
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    returncode: int = 0,
    raise_oserror: bool = False,
) -> tuple[Path, dict[str, object], list[bool], list[bool]]:
    path = tmp_path / "mlx-tui.toml"
    monkeypatch.setattr("mlx_tui.app.config_path", lambda: path)
    monkeypatch.setenv("EDITOR", "fake-editor")
    ran: dict[str, object] = {}
    entered: list[bool] = []
    exited: list[bool] = []

    def fake_run(argv: object, **kwargs: object) -> SimpleNamespace:
        ran["argv"] = argv
        ran["kwargs"] = kwargs
        ran["suspended_during"] = (len(entered), len(exited))
        if raise_oserror:
            raise OSError("no such editor")
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(harness.app, "suspend", lambda: _stub_suspend(entered, exited))
    return path, ran, entered, exited


async def test_edit_config_missing_file_creates_template_and_reloads(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _ran, entered, exited = _stub_editor(harness, monkeypatch, tmp_path)
    assert not path.exists()
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert path.exists()
    assert "config reloaded" in harness.app_log_lines()
    assert entered == [True] and exited == [True]


async def test_edit_config_editor_argv_with_spaces(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, ran, _entered, _exited = _stub_editor(harness, monkeypatch, tmp_path)
    path.write_text('model = "a/b"\n')
    monkeypatch.setenv("EDITOR", "code --wait --new-window")
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert ran["argv"] == ["code", "--wait", "--new-window", str(path)]
    assert harness.app.config.model == "a/b"
    assert "config reloaded" in harness.app_log_lines()


async def test_edit_config_suspends_around_editor(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _path, ran, entered, exited = _stub_editor(harness, monkeypatch, tmp_path)
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert entered == [True] and exited == [True]
    assert ran["suspended_during"] == (1, 0)


async def test_edit_config_editor_oserror_keeps_config(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _ran, _entered, _exited = _stub_editor(
        harness, monkeypatch, tmp_path, raise_oserror=True
    )
    path.write_text('model = "a/b"\n')
    harness.app.action_edit_config()
    await harness.pilot.pause()
    lines = harness.app_log_lines()
    assert any("config edit failed" in t and "OSError" in t for t in lines), lines
    assert "config reloaded" not in lines
    assert harness.app.config.model is None


async def test_edit_config_editor_nonzero_keeps_config(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _ran, _entered, _exited = _stub_editor(
        harness, monkeypatch, tmp_path, returncode=3
    )
    path.write_text('model = "a/b"\n')
    harness.app.action_edit_config()
    await harness.pilot.pause()
    lines = harness.app_log_lines()
    assert any("config edit failed" in t and "exited 3" in t for t in lines), lines
    assert "config reloaded" not in lines
    assert harness.app.config.model is None


async def test_edit_config_malformed_toml_preserves_config(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _ran, _entered, _exited = _stub_editor(harness, monkeypatch, tmp_path)
    path.write_text('model = "keep/me"\n')
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert harness.app.config.model == "keep/me"
    path.write_text("<<<")
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert harness.app.config.model == "keep/me"
    assert any("config kept" in t for t in harness.app_log_lines())


async def test_edit_config_cleared_values_apply_and_presets_reset(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _ran, _entered, _exited = _stub_editor(harness, monkeypatch, tmp_path)
    path.write_text('model = "a/b"\nport = 9001\n')
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert harness.app.config.model == "a/b"
    assert harness.app.config.port == 9001
    harness.app.presets = []
    harness.app.preset_idx = 3
    path.write_text("port = 9002\n")
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert _config_model(harness.app) is None
    assert harness.app.config.port == 9002
    assert harness.app.preset_idx == -1


async def test_edit_config_host_port_change_notifies_restart(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _ran, _entered, _exited = _stub_editor(harness, monkeypatch, tmp_path)
    path.write_text('host = "0.0.0.0"\nport = 9001\n')
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert harness.app.config.host == "0.0.0.0"
    assert harness.app.config.port == 9001
    assert harness.app.host == "127.0.0.1"
    assert any("restart mlx-tui" in t for t in harness.app_log_lines())
