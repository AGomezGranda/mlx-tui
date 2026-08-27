"""Status bar rendering thin wrapper."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

from mlx_tui.process import memory_snapshot
from mlx_tui.status import MemorySnapshot, format_status_line

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
    line = format_status_line(
        state=app.status_state,
        model=model,
        rss_gib=rss_gib,
        memory=snapshot,
        port=app.port,
    )
    if app.status_state == "red" and app.config.start_cmd:
        line += " · [dim]ctrl+s to start[/]"
    app.query_one("#status-bar", Static).update(line)
