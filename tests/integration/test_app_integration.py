from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import pytest
from textual.widgets import Input, Static

from mlx_tui.app import MlxTuiApp
from mlx_tui.history.store import TurnRecord
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
    # Directly invoke completion UI with markdown to avoid stub coupling
    pane = harness.app.query_one(ChatPane)
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
        return len(a.query_one(ChatPane).messages) >= 3

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

    bar = harness.app.query_one("#ctx-bar", Static)
    text = str(bar.render())
    assert "ctx" in text.lower() and "8k" in text, f"initial bar {text!r} missing ctx/8k"
    # at 0/8k style is default (not amber/red)
    assert bar.has_class("ctx-bar-amber") is False
    assert bar.has_class("ctx-bar-red") is False


async def test_ctx_bar_amber_and_red_thresholds(harness: AppHarness) -> None:
    from textual.widgets import Static  # noqa: PLC0415

    pane = harness.chat_pane()
    bar = harness.app.query_one("#ctx-bar", Static)

    # 7000/8000 -> amber (>80%)
    pane.update_ctx_bar(7000)
    await harness.pilot.pause()
    text = str(bar.render())
    assert "7k" in text and "8k" in text, f"7000 text {text!r}"
    assert bar.has_class("ctx-bar-amber") is True
    assert bar.has_class("ctx-bar-red") is False

    # 7700/8000 -> red (>95%)
    pane.update_ctx_bar(7700)
    await harness.pilot.pause()
    text = str(bar.render())
    assert "7.7k" in text
    assert bar.has_class("ctx-bar-red") is True

    # back to low -> not amber/red
    pane.update_ctx_bar(100)
    await harness.pilot.pause()
    assert bar.has_class("ctx-bar-amber") is False
    assert bar.has_class("ctx-bar-red") is False


async def test_ctx_bar_updates_after_turn(harness: AppHarness) -> None:
    from textual.widgets import Input, Static  # noqa: PLC0415

    bar = harness.app.query_one("#ctx-bar", Static)
    initial = str(bar.render())
    assert "0/8k" in initial or "0 / 8k" in initial or "ctx 0" in initial.lower()
    inp = harness.app.query_one("#chat-input", Input)
    inp.value = "hello world, this is a test of context bar"
    inp.focus()
    await harness.pilot.press("enter")

    def bar_updated(a: MlxTuiApp) -> bool:
        t = str(a.query_one("#ctx-bar", Static).render())
        return "0/8k" not in t and "ctx" in t.lower()

    assert await harness.wait_for(bar_updated), f"bar never updated: {str(bar.render())}"
    # after turn, bar should show non-zero ctx and still 8k max
    text = str(bar.render())
    assert "8k" in text
    assert "0/8k" not in text


async def test_preset_cycle_drives_params_and_system(
    harness: AppHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    pane = harness.app.query_one(ChatPane)
    # direct action kept: pilot.press for ctrl+p was unreliable in textual 8.2.8; ctrl+n forward is now reliable but direct keeps test stable
    harness.app.action_cycle_preset()
    await harness.pilot.pause()
    assert pane._system_prompt == "You are A"
    assert harness.app.query_one("#param-temp", _Input).value == "0.2"
    assert any("preset: a" in line for line in harness.app_log_lines())
    harness.app.action_cycle_preset()
    await harness.pilot.pause()
    assert pane._system_prompt == "You are B"
    assert harness.app.query_one("#param-top-p", _Input).value == "0.5"
    assert harness.app.query_one("#param-max-tokens", _Input).value == "512"
    assert any("preset: b" in line for line in harness.app_log_lines())
    harness.app.action_cycle_preset_back()
    await harness.pilot.pause()
    assert pane._system_prompt == "You are A"

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
