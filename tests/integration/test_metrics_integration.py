"""Metrics-tab and token accounting integration tests."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import pytest
from textual.widgets import Static

from mlx_tui.app import MlxTuiApp, polling
from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.history.store import ResourceSample, TurnRecord
from mlx_tui.metrics_pane import MetricsPane
from mlx_tui.operations import OperationKind
from mlx_tui.process import ProcessIdentity
from tests.conftest import AppHarness


def _record(**overrides: object) -> TurnRecord:
    base: dict[str, object] = {
        "ts": time.time(),
        "model": "metrics-model",
        "prompt_tok": 10,
        "out_tok": 5,
        "first_output_s": 0.1,
        "answer_started_s": 0.2,
        "total_s": 1.0,
        "req_tok_s": 12.3,
        "ctx_len": 100,
        "outcome": "success",
    }
    base.update(overrides)
    return TurnRecord(**base)  # type: ignore[arg-type]


async def test_metrics_tab_renders_and_refreshes(harness: AppHarness) -> None:
    import time as _time  # noqa: PLC0415

    from textual.widgets import DataTable, TabbedContent  # noqa: PLC0415

    def pane_text(wid_id: str) -> str:
        s = harness.app.query_one(wid_id, Static)
        try:
            return str(s.render())
        except Exception:
            content = getattr(s, "content", "")
            return str(content)

    harness.app.query_one(TabbedContent).active = "metrics"
    await harness.pilot.pause()
    polling.stop_resource_sampler(harness.app)
    harness.app.history.clear()
    harness.app.resource_store.clear()
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    table = harness.app.query_one("#metrics-table", DataTable)
    assert table.row_count == 0
    assert "Live" in pane_text("#metrics-context")
    assert "CPU" in pane_text("#metrics-cpu")
    assert "Memory" in pane_text("#metrics-memory")
    assert "App activity" in pane_text("#metrics-activity")

    harness.app.history.add(_record())
    now = _time.monotonic()
    harness.app.resource_store.append(
        ResourceSample(
            ts=now - 1,
            operation=OperationKind.IDLE,
            process_identity=None,
            cpu_percent=12.5,
            rss_gib=None,
            avail_gib=8.0,
            total_gib=16.0,
            swap_gib=0.5,
        )
    )
    harness.app.resource_store.append(
        ResourceSample(
            ts=now,
            operation=OperationKind.CHATTING,
            process_identity=None,
            cpu_percent=25.0,
            rss_gib=None,
            avail_gib=7.5,
            total_gib=16.0,
            swap_gib=0.5,
        )
    )
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    assert table.row_count == 1
    cpu_text = pane_text("#metrics-cpu")
    assert "This Mac" in cpu_text
    assert "scale 0–100%" in cpu_text
    assert "excludes GPU" in cpu_text
    mem_text = pane_text("#metrics-memory")
    assert "RSS" in mem_text
    assert "process-wide" in mem_text
    act_text = pane_text("#metrics-activity")
    assert "Generating" in act_text or "G" in act_text
    assert "no request from this app" in act_text

    harness.app.history.clear()
    harness.app.resource_store.clear()
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()


async def test_success_stamp_and_table_show_client_rate(  # noqa: PLR0915
    harness: AppHarness,
) -> None:
    from textual.widgets import DataTable, TabbedContent  # noqa: PLC0415

    harness.server.mode = "ok"
    harness.app.history.clear()
    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def has_one(a: MlxTuiApp) -> bool:
        return len(a.history.all_records()) == 1

    assert await harness.wait_for(has_one), f"no record; log={harness.log_lines()}"
    rec = harness.app.history.all_records()[0]
    assert rec.prompt_tok == 12
    assert rec.out_tok == 6
    assert rec.prompt_estimated is False
    assert rec.out_estimated is False
    assert rec.req_tok_s is not None and rec.req_tok_s > 0
    assert rec.outcome == "success"
    texts = harness.log_lines()
    joined = " ".join(texts)
    assert "client request tok/s" in joined
    assert "first" in joined
    assert "answer" in joined
    assert "~" not in joined
    harness.app.query_one(TabbedContent).active = "metrics"
    await harness.pilot.pause()
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    table = harness.app.query_one("#metrics-table", DataTable)
    assert table.row_count == 1
    assert rec.req_tok_s is not None
    from textual.coordinate import Coordinate  # noqa: PLC0415

    assert "—" not in str(table.get_cell_at(Coordinate(0, 5)))
    assert "~" not in str(table.get_cell_at(Coordinate(0, 7)))
    assert "~" not in str(table.get_cell_at(Coordinate(0, 8)))

    harness.server.mode = "no_usage"
    harness.app.history.clear()
    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi2"
    inp.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(has_one)
    rec2 = harness.app.history.all_records()[0]
    assert rec2.prompt_estimated is True
    assert rec2.out_estimated is True
    assert rec2.outcome == "success"
    assert rec2.req_tok_s is not None
    texts2 = harness.log_lines()
    joined2 = " ".join(texts2)
    assert "client request tok/s" in joined2, f"no stamp {texts2}"
    assert "~" in joined2
    harness.app.query_one(TabbedContent).active = "metrics"
    await harness.pilot.pause()
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    table2 = harness.app.query_one("#metrics-table", DataTable)
    assert table2.row_count == 1
    assert "~" in str(table2.get_cell_at(Coordinate(0, 7)))
    assert "~" in str(table2.get_cell_at(Coordinate(0, 8)))
    harness.app.history.clear()
    harness.server.mode = "ok"


async def test_unsuccessful_outcomes_retained_in_request_history(
    harness: AppHarness,
) -> None:
    from textual.widgets import DataTable, TabbedContent  # noqa: PLC0415

    harness.server.mode = "length_cap"
    harness.app.history.clear()
    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def has_one(a: MlxTuiApp) -> bool:
        return len(a.history.all_records()) == 1

    assert await harness.wait_for(has_one)
    rec = harness.app.history.all_records()[0]
    assert rec.outcome == "length_capped"
    assert rec.req_tok_s is None
    assert harness.chat_pane().messages == []
    # Cancelled/failed requests retain their table outcomes.
    harness.app.query_one(TabbedContent).active = "metrics"
    await harness.pilot.pause()
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    table = harness.app.query_one("#metrics-table", DataTable)
    assert table.row_count == 1
    from textual.coordinate import Coordinate  # noqa: PLC0415

    assert "length_capped" in str(table.get_cell_at(Coordinate(0, 8)))
    harness.server.mode = "ok"
    harness.app.history.clear()


def _fake_sample(
    operation: OperationKind,
    ident: ProcessIdentity | None,
    tick: int,
) -> ResourceSample:
    return ResourceSample(
        ts=1000.0 + tick,
        operation=operation,
        process_identity=ident,
        cpu_percent=10.0 + tick,
        rss_gib=None if ident is None else 1.5,
        avail_gib=8.0,
        total_gib=16.0,
        swap_gib=0.5,
    )


async def test_resource_sampler_runs_hidden_without_http_probes(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from textual.widgets import TabbedContent  # noqa: PLC0415

    assert harness.app._resource_thread is not None
    assert harness.app._resource_thread.is_alive()
    assert harness.app.resource_store.maxlen == 121
    polling.stop_resource_sampler(harness.app)
    assert harness.app._resource_thread is None

    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    harness.app.resource_store.clear()
    harness.app._resource_first = False

    ticks = {"n": 0}
    seen_idents: list[ProcessIdentity | None] = []

    def fake_sample_resources(
        ident: ProcessIdentity | None, op: OperationKind
    ) -> ResourceSample:
        seen_idents.append(ident)
        ticks["n"] += 1
        return _fake_sample(op, ident, ticks["n"])

    monkeypatch.setattr("mlx_tui.process.sample_resources", fake_sample_resources)
    monkeypatch.setattr(
        "mlx_tui.app.polling.process.sample_resources", fake_sample_resources
    )

    http_calls = {"n": 0}
    real_get = harness.app._http.get

    async def counting_get(*args: Any, **kwargs: Any) -> Any:
        http_calls["n"] += 1
        return await real_get(*args, **kwargs)

    monkeypatch.setattr(harness.app._http, "get", counting_get)

    assert harness.app.query_one(TabbedContent).active != "metrics"
    polling.sample_and_store(harness.app)
    polling.sample_and_store(harness.app)
    await harness.pilot.pause()

    assert len(harness.app.resource_store) == 2
    assert ticks["n"] == 2
    assert http_calls["n"] == 0
    ops = [s.operation for s in harness.app.resource_store]
    assert ops == [OperationKind.IDLE, OperationKind.IDLE]


async def test_resource_sampling_continues_during_busy_operations(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    polling.stop_resource_sampler(harness.app)
    harness.app.resource_store.clear()
    harness.app._resource_first = False

    ticks = {"n": 0}

    def fake_sample_resources(
        ident: ProcessIdentity | None, op: OperationKind
    ) -> ResourceSample:
        ticks["n"] += 1
        return _fake_sample(op, ident, ticks["n"])

    monkeypatch.setattr("mlx_tui.process.sample_resources", fake_sample_resources)
    monkeypatch.setattr(
        "mlx_tui.app.polling.process.sample_resources", fake_sample_resources
    )

    assert harness.app.operations.try_acquire(OperationKind.COMPARING)
    try:
        polling.sample_and_store(harness.app)
    finally:
        harness.app.operations.release(OperationKind.COMPARING)
    assert harness.app.operations.try_acquire(OperationKind.RESTARTING)
    try:
        sample = polling.sample_and_store(harness.app)
    finally:
        harness.app.operations.release(OperationKind.RESTARTING)

    assert ticks["n"] == 2
    stored = list(harness.app.resource_store)
    assert [s.operation for s in stored] == [
        OperationKind.COMPARING,
        OperationKind.RESTARTING,
    ]
    # Restarting never attributes RSS to a process.
    assert stored[-1].process_identity is None
    assert stored[-1].rss_gib is None
    assert sample.process_identity is None


async def test_resource_sampler_first_cpu_unknown_and_clean_stop(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    polling.stop_resource_sampler(harness.app)
    harness.app.resource_store.clear()

    def fake_sample_resources(
        ident: ProcessIdentity | None, op: OperationKind
    ) -> ResourceSample:
        return _fake_sample(op, ident, 1)

    monkeypatch.setattr("mlx_tui.process.sample_resources", fake_sample_resources)
    monkeypatch.setattr(
        "mlx_tui.app.polling.process.sample_resources", fake_sample_resources
    )

    harness.app._resource_first = True
    first = polling.sample_and_store(harness.app)
    assert first.cpu_percent is None
    second = polling.sample_and_store(harness.app)
    assert second.cpu_percent == 11.0
    assert len(harness.app.resource_store) == 2

    # Unknown RSS never appears as zero.
    assert first.rss_gib is None or first.rss_gib != 0.0

    # Buffer stays bounded at 121 observations.
    for _ in range(130):
        polling.sample_and_store(harness.app)
    assert len(harness.app.resource_store) == 121

    # Closing blocks further appends and stop is idempotent.
    polling.start_resource_sampler(harness.app, interval=0.05)
    assert harness.app._resource_thread is not None
    polling.stop_resource_sampler(harness.app)
    assert harness.app._resource_thread is None
    polling.stop_resource_sampler(harness.app)
    harness.app._closing = True
    try:
        before = len(harness.app.resource_store)
        polling.sample_and_store(harness.app)
        # sample is built but never stored once closing.
        assert len(harness.app.resource_store) == before
    finally:
        harness.app._closing = False


async def test_resource_failures_record_unknown_with_bounded_diagnostic(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    polling.stop_resource_sampler(harness.app)
    harness.app.resource_store.clear()
    harness.app._resource_first = False

    def failing_sample(
        ident: ProcessIdentity | None, op: OperationKind
    ) -> ResourceSample:
        raise OSError("sensor down")

    monkeypatch.setattr("mlx_tui.process.sample_resources", failing_sample)
    monkeypatch.setattr("mlx_tui.app.polling.process.sample_resources", failing_sample)

    sample = polling.sample_and_store(harness.app)
    assert sample.cpu_percent is None
    assert sample.rss_gib is None
    assert len(harness.app.resource_store) == 1
    polling.sample_and_store(harness.app)
    assert len(harness.app.resource_store) == 2
    # Bounded: identical repeats stay silent behind one log entry.
    assert harness.app._last_errors.get("resource-sample") is not None

    good = _fake_sample(OperationKind.IDLE, None, 9)

    def _good_sample(
        ident: ProcessIdentity | None, op: OperationKind
    ) -> ResourceSample:
        return replace(good, operation=op, process_identity=ident)

    monkeypatch.setattr(
        "mlx_tui.process.sample_resources",
        _good_sample,
    )
    monkeypatch.setattr(
        "mlx_tui.app.polling.process.sample_resources",
        _good_sample,
    )
    polling.sample_and_store(harness.app)
    assert harness.app._last_errors.get("resource-sample") is None


async def test_empty_history_with_live_telemetry(harness: AppHarness) -> None:
    import time as _time  # noqa: PLC0415

    from textual.widgets import DataTable, TabbedContent  # noqa: PLC0415

    def pane_text(wid_id: str) -> str:
        s = harness.app.query_one(wid_id, Static)
        try:
            return str(s.render())
        except Exception:
            return str(getattr(s, "content", ""))

    polling.stop_resource_sampler(harness.app)
    harness.app.query_one(TabbedContent).active = "metrics"
    await harness.pilot.pause()
    harness.app.history.clear()
    harness.app.resource_store.clear()
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    assert harness.app.query_one("#metrics-table", DataTable).row_count == 0
    assert "no samples yet" in pane_text("#metrics-cpu")
    assert "no samples yet" in pane_text("#metrics-memory")

    now = _time.monotonic()
    harness.app.resource_store.append(
        ResourceSample(
            ts=now,
            operation=OperationKind.IDLE,
            process_identity=None,
            cpu_percent=33.3,
            rss_gib=None,
            avail_gib=8.0,
            total_gib=16.0,
            swap_gib=0.5,
        )
    )
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    assert harness.app.query_one("#metrics-table", DataTable).row_count == 0
    assert "33.3%" in pane_text("#metrics-cpu")
    assert "Live" in pane_text("#metrics-context")
    harness.app.history.clear()
    harness.app.resource_store.clear()


async def test_known_cpu_unknown_rss_shows_dash(harness: AppHarness) -> None:
    import time as _time  # noqa: PLC0415

    from textual.widgets import TabbedContent  # noqa: PLC0415

    def pane_text(wid_id: str) -> str:
        s = harness.app.query_one(wid_id, Static)
        try:
            return str(s.render())
        except Exception:
            return str(getattr(s, "content", ""))

    polling.stop_resource_sampler(harness.app)
    harness.app.query_one(TabbedContent).active = "metrics"
    await harness.pilot.pause()
    harness.app.resource_store.clear()
    now = _time.monotonic()
    harness.app.resource_store.append(
        ResourceSample(
            ts=now,
            operation=OperationKind.IDLE,
            process_identity=None,
            cpu_percent=42.5,
            rss_gib=None,
            avail_gib=8.0,
            total_gib=16.0,
            swap_gib=0.5,
        )
    )
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    assert "42.5%" in pane_text("#metrics-cpu")
    mem_text = pane_text("#metrics-memory")
    assert "RSS —" in mem_text
    assert "RSS 0.0" not in mem_text
    harness.app.resource_store.clear()


async def test_identity_loss_and_replacement_keeps_gaps(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace as _replace  # noqa: PLC0415

    polling.stop_resource_sampler(harness.app)
    harness.app.resource_store.clear()
    harness.app._resource_first = False

    def echo_sample(ident: ProcessIdentity | None, op: OperationKind) -> ResourceSample:
        import time as _time  # noqa: PLC0415

        return ResourceSample(
            ts=_time.monotonic(),
            operation=op,
            process_identity=ident,
            cpu_percent=10.0,
            rss_gib=None if ident is None else 2.0,
            avail_gib=8.0,
            total_gib=16.0,
            swap_gib=0.5,
        )

    monkeypatch.setattr("mlx_tui.process.sample_resources", echo_sample)
    monkeypatch.setattr("mlx_tui.app.polling.process.sample_resources", echo_sample)

    harness.app.server_identity = _replace(
        harness.app.server_identity, pid=111, pid_create_time=222.0
    )
    polling.sample_and_store(harness.app)
    harness.app.server_identity = _replace(
        harness.app.server_identity, pid=None, pid_create_time=None
    )
    polling.sample_and_store(harness.app)
    harness.app.server_identity = _replace(
        harness.app.server_identity, pid=333, pid_create_time=444.0
    )
    polling.sample_and_store(harness.app)

    stored = list(harness.app.resource_store)
    assert [s.process_identity.pid if s.process_identity else None for s in stored] == [
        111,
        None,
        333,
    ]
    assert [s.rss_gib for s in stored] == [2.0, None, 2.0]
    # Earlier samples keep their original identity (never relabelled).
    assert stored[0].process_identity is not None
    assert stored[0].process_identity.pid == 111

    # Restarting never attributes RSS even with a known identity.
    assert harness.app.operations.try_acquire(OperationKind.RESTARTING)
    try:
        restarting = polling.sample_and_store(harness.app)
    finally:
        harness.app.operations.release(OperationKind.RESTARTING)
    assert restarting.process_identity is None
    assert restarting.rss_gib is None


async def test_sampled_operation_transitions(harness: AppHarness) -> None:
    import time as _time  # noqa: PLC0415

    from mlx_tui.history.charts import render_activity_ribbon  # noqa: PLC0415

    polling.stop_resource_sampler(harness.app)
    harness.app.resource_store.clear()
    harness.app._resource_first = False
    base = _time.monotonic()
    for offset, op in (
        (-2.0, OperationKind.IDLE),
        (-1.0, OperationKind.CHATTING),
        (0.0, OperationKind.IDLE),
    ):
        harness.app.resource_store.append(
            ResourceSample(
                ts=base + offset,
                operation=op,
                process_identity=None,
                cpu_percent=10.0,
                rss_gib=None,
                avail_gib=8.0,
                total_gib=16.0,
                swap_gib=0.5,
            )
        )
    ops = [s.operation for s in harness.app.resource_store]
    assert ops == [OperationKind.IDLE, OperationKind.CHATTING, OperationKind.IDLE]
    ribbon = render_activity_ribbon(
        list(harness.app.resource_store), now=base, width=120
    )
    assert "G" in ribbon.plain
    assert "·" in ribbon.plain
    harness.app.resource_store.clear()


async def test_select_model_does_not_relabel_samples(
    harness: AppHarness,
) -> None:
    import time as _time  # noqa: PLC0415

    from textual.widgets import TabbedContent  # noqa: PLC0415

    polling.stop_resource_sampler(harness.app)
    harness.app.query_one(TabbedContent).active = "metrics"
    await harness.pilot.pause()
    harness.app.resource_store.clear()
    ident = ProcessIdentity(pid=555, create_time=666.0)
    harness.app.resource_store.append(_fake_sample(OperationKind.IDLE, ident, 3))
    before = list(harness.app.resource_store)
    polling.select_model(harness.app, "other-model")
    after = list(harness.app.resource_store)
    assert after == before
    assert after[0].process_identity is not None
    assert after[0].process_identity.pid == 555
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    ctx = harness.app.query_one("#metrics-context", Static)
    try:
        ctx_text = str(ctx.render())
    except Exception:
        ctx_text = str(getattr(ctx, "content", ""))
    assert "residency unknown" in ctx_text
    assert "resident" not in ctx_text.replace("residency unknown", "")
    assert _time.monotonic() > 0  # monotonic clock anchors chart bins
    harness.app.resource_store.clear()


async def test_sampling_only_updates_preserve_table_selection_and_scroll(
    harness: AppHarness,
) -> None:
    import time as _time  # noqa: PLC0415

    from textual.widgets import DataTable, TabbedContent  # noqa: PLC0415

    polling.stop_resource_sampler(harness.app)
    harness.app.query_one(TabbedContent).active = "metrics"
    await harness.pilot.pause()
    harness.app.history.clear()
    harness.app.resource_store.clear()
    for i in range(5):
        harness.app.history.add(_record(ts=_time.time() + i, ctx_len=10 + i))
    pane = harness.app.query_one(MetricsPane)
    pane.refresh_metrics()
    await harness.pilot.pause()
    table = harness.app.query_one("#metrics-table", DataTable)
    assert table.row_count == 5
    table.move_cursor(row=1)
    await harness.pilot.pause()
    before_cursor = table.cursor_row
    before_scroll = (table.scroll_x, table.scroll_y)
    now = _time.monotonic()
    harness.app.resource_store.append(
        ResourceSample(
            ts=now,
            operation=OperationKind.IDLE,
            process_identity=None,
            cpu_percent=20.0,
            rss_gib=None,
            avail_gib=8.0,
            total_gib=16.0,
            swap_gib=0.5,
        )
    )
    pane.refresh_metrics()
    await harness.pilot.pause()
    assert table.row_count == 5
    assert table.cursor_row == before_cursor
    assert (table.scroll_x, table.scroll_y) == before_scroll
