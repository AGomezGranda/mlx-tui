"""Status bar widget for endpoint state and memory telemetry."""

from __future__ import annotations

from typing import Any, override

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import ProgressBar, Static

from mlx_tui.status import MemorySnapshot


class StatusBar(Horizontal):
    """Render status values supplied by the app coordinator."""

    DEFAULT_CSS = """
    StatusBar {
        dock: top;
        width: 100%;
        height: 3;
        layout: horizontal;
        background: $surface;
        padding: 1 2;
    }
    .compact StatusBar { height: 1; padding: 0 2; }
    #status-dot { width: auto; margin-right: 1; }
    #status-model { width: 1fr; min-width: 0; height: 1; }
    #status-port {
        width: 7;
        max-width: 7;
        margin-left: 2;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    #memory-bar {
        width: 16;
        height: 1;
        margin: 0 1;
    }
    #memory-label {
        width: 1fr;
        min-width: 0;
        overflow: hidden;
        content-align: left middle;
    }
    """

    def __init__(self, port: int = 8080, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._initial_port = port

    @override
    def compose(self) -> ComposeResult:
        yield Static("● Unknown", id="status-dot")
        yield Static("—", id="status-model")
        yield ProgressBar(
            total=16, show_percentage=False, show_eta=False, id="memory-bar"
        )
        yield Static("process RSS — GiB · avail —/— GiB", id="memory-label")
        yield Static(f":{self._initial_port}", id="status-port")

    def show_status(  # noqa: PLR0913
        self,
        *,
        state: str,
        selected_model: str | None,
        last_response_model: str | None,
        generation_state: str,
        catalogue_disagrees: bool,
        port: int,
        can_start: bool,
        rss_gib: float | None,
        snapshot: MemorySnapshot,
        runtime_mode: str = "attach",
        ownership_state: str | None = None,
    ) -> None:  # noqa: PLR0913
        """Paint a complete status snapshot without consulting app state."""
        color = "yellow" if state in {"amber", "starting", "stopping"} else state
        word = {
            "green": "Reachable",
            "amber": "Unexpected",
            "red": "Offline",
            "starting": "Starting",
            "stopping": "Stopping",
            "failed": "Failed",
        }.get(state, "Unknown")
        self.query_one("#status-dot", Static).update(f"[{color}]● {word}[/]")
        model_label = self.query_one("#status-model", Static)
        model_text = (
            f"selected {selected_model or '—'} · "
            f"last response {last_response_model or '—'} · {generation_state}"
        )
        if runtime_mode == "managed":
            model_text += " · managed · stops on quit"
        if ownership_state:
            model_text += f" · {ownership_state}"
        if catalogue_disagrees:
            model_text += " · not in catalogue"
        model_label.update(Text(model_text, overflow="ellipsis", no_wrap=True))
        model_label.tooltip = model_text
        port_label = f"{'M' if runtime_mode == 'managed' else 'A'} :{port}"
        port_widget = self.query_one("#status-port", Static)
        port_widget.update(port_label)
        port_widget.tooltip = (
            f"{'Managed' if runtime_mode == 'managed' else 'Attach'} · :{port}"
            + (" · ctrl+s to start" if state == "red" and can_start else "")
        )
        bar = self.query_one("#memory-bar", ProgressBar)
        bar.update(
            total=snapshot.total_gib if snapshot.total_gib > 0 else 16,
            progress=rss_gib if rss_gib is not None else 0,
        )
        rss_part = f"{rss_gib:.1f}" if rss_gib is not None else "—"
        self.query_one("#memory-label", Static).update(
            f"process RSS {rss_part} GiB · "
            f"avail {snapshot.avail_gib:.1f}/{snapshot.total_gib:.1f} GiB"
        )
