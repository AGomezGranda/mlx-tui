"""Table scan + marker refresh — plain functions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.css.query import NoMatches
from textual.widgets import Static

from mlx_tui.models import scan_models
from mlx_tui.table import ModelsTable

if TYPE_CHECKING:
    from mlx_tui.models_pane import ModelsPane


def _rescan_impl(pane: ModelsPane) -> None:
    rows = scan_models(pane.tui.latest_avail_gib)
    # Widget has no call_from_thread in textual 8.2.8 — hop via App.
    pane.tui.call_from_thread(populate, pane, rows)


def populate(pane: ModelsPane, rows: list) -> None:  # type: ignore[type-arg]
    try:
        table = pane.query_one("#models-table", ModelsTable)
    except NoMatches:
        # A rescan landing during shutdown has no table left to fill.
        return
    table.set_rows(
        rows,
        effective_model=pane.tui.effective_model(),
        avail_gib=pane.tui.latest_avail_gib,
    )
    pane.rows = rows


def refresh_markers(pane: ModelsPane) -> None:
    if not pane.rows:
        return
    try:
        table = pane.query_one("#models-table", ModelsTable)
    except NoMatches:
        return
    table.refresh_markers(
        pane.rows,
        effective_model=pane.tui.effective_model(),
        avail_gib=pane.tui.latest_avail_gib,
    )


def progress_line(pane: ModelsPane, repo_id: str, seconds: int) -> None:
    try:
        pane.query_one("#swap-progress", Static).update(
            f"waiting for {repo_id}… {seconds}s"
        )
    except NoMatches:
        return
