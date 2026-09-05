"""Status bar rendering thin wrapper."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.css.query import NoMatches
from textual.widgets import ProgressBar, Static

from mlx_tui.process import memory_snapshot
from mlx_tui.status import (  # noqa: F401 - kept for test fallback
    MemorySnapshot,
    format_status_line,
)

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


def render_status(
    app: MlxTuiApp,
    *,
    model: str | None,
    rss_gib: float | None,
    snapshot: MemorySnapshot | None = None,
) -> None:
    if snapshot is None:
        snapshot = memory_snapshot()
    try:
        app.query_one("#status-dot", Static).update(
            f"[{'yellow' if app.status_state == 'amber' else app.status_state}]●[/]"
        )
        app.query_one("#status-model", Static).update(model if model else "—")
        app.query_one("#status-port", Static).update(
            f":{app.port}"
            + (
                " · [dim]ctrl+s to start[/]"
                if app.status_state == "red" and app.config.start_cmd
                else ""
            )
        )
    except NoMatches:
        pass
    try:
        bar = app.query_one("#memory-bar", ProgressBar)
        bar.update(
            total=snapshot.total_gib if snapshot.total_gib > 0 else 16,
            progress=rss_gib if rss_gib is not None else 0,
        )
    except NoMatches:
        pass
    try:
        avail = snapshot.avail_gib
        total = snapshot.total_gib
        rss_part = f"{rss_gib:.1f}" if rss_gib is not None else "—"
        app.query_one("#memory-label", Static).update(
            f"RSS {rss_part} GB · avail {avail:.1f}/{total:.1f} GB"
        )
    except NoMatches:
        pass
