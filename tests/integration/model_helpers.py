"""Shared model-table helpers for integration tests."""

from __future__ import annotations

from textual.widgets import TabbedContent

from mlx_tui.models import ModelRow
from mlx_tui.models_pane import ModelsPane
from mlx_tui.process import ProcessIdentity
from mlx_tui.table import ModelsTable
from tests.conftest import AppHarness

ROW = ModelRow("mlx-community/stub-test", 1_000_000_000, "4bit", ("abc123",))


def _stub_rows() -> list[ModelRow]:
    return [ROW]


def _no_process(host: str, port: int) -> ProcessIdentity | None:
    return None


def _fake_process_4242(host: str, port: int) -> ProcessIdentity | None:
    return ProcessIdentity(4242, 111.0)


async def _select_row(harness: AppHarness) -> None:
    """Switch to the Models tab, wait for the stub row, focus the table."""
    harness.app.query_one(TabbedContent).active = "models"
    harness.app.query_one(ModelsPane).rescan()
    assert await harness.wait_for(lambda a: a.query_one(ModelsPane).rows == [ROW])
    harness.app.query_one("#models-table", ModelsTable).focus()


def _log_text(harness: AppHarness) -> str:
    return "\n".join(harness.app_log_lines())
