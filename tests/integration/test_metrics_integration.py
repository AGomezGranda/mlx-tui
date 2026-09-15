"""Metrics-tab and token accounting integration tests."""

from __future__ import annotations

import time

from textual.widgets import Static

from mlx_tui.app import MlxTuiApp
from mlx_tui.chat_pane import ChatInput
from mlx_tui.history.store import TurnRecord
from mlx_tui.metrics_pane import MetricsPane
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
    from textual.widgets import DataTable  # noqa: PLC0415

    from mlx_tui.history.store import MemoryRecord  # noqa: PLC0415

    def spark_text(wid_id: str) -> str:
        s = harness.app.query_one(wid_id, Static)
        try:
            return str(s.render())
        except Exception:
            return str(getattr(s, "content", ""))

    table = harness.app.query_one("#metrics-table", DataTable)
    assert table is not None
    harness.app.history.clear()
    harness.app.memory_store.clear()
    harness.app.query_one(MetricsPane).refresh_metrics()
    await harness.pilot.pause()
    assert "no history yet" in spark_text("#metrics-sparkline")
    assert "no memory samples" in spark_text("#metrics-memory-sparkline")

    harness.app.history.add(_record())
    harness.app.memory_store.append(
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
    assert table.row_count == 1
    assert "client request tok/s" in spark_text("#metrics-sparkline")
    assert "avail" in spark_text("#metrics-memory-sparkline")
    assert "no history yet" not in spark_text("#metrics-sparkline")

    harness.app.history.clear()
    harness.app.memory_store.clear()
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


async def test_unsuccessful_outcomes_filtered_from_sparkline(
    harness: AppHarness,
) -> None:
    from textual.widgets import TabbedContent  # noqa: PLC0415

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
    harness.server.mode = "ok"
    harness.app.history.clear()
