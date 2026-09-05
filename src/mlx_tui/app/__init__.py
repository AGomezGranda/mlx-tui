"""Textual coordinator: shared state, polling, bindings, cross-pane
orchestration; tab UIs and the swap/boot workers live in the pane modules."""

from __future__ import annotations

import argparse
from typing import override

import httpx
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import ProgressBar, RichLog, Static, TabbedContent, TabPane

from mlx_tui.chat_pane import ChatPane
from mlx_tui.config import AppConfig, load_config
from mlx_tui.history.store import HistoryStore, MemoryStore
from mlx_tui.metrics_pane import MetricsPane
from mlx_tui.models_pane import ModelsPane
from mlx_tui.presets import Preset, load_presets
from mlx_tui.status import ColdTracker, MemorySnapshot, ServerIdentity

from . import config_edit, polling, presets_ctrl, state, status_bar, swap_ctrl
from .operations import OperationCoordinator


class MlxTuiApp(App[None]):
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("escape", "cancel_chat", "Cancel"),
        ("ctrl+s", "cold_start", "Start server"),
        ("ctrl+g", "edit_config", "Edit config"),
        ("ctrl+n", "cycle_preset", "Preset"),
        ("ctrl+o", "cycle_preset_back", "Preset back"),
    ]

    DEFAULT_CSS = """
    #status-bar {
        dock: top;
        width: 100%;
        height: 3;
        layout: horizontal;
        background: $surface;
        padding: 1 1;
    }
    #status-dot { width: auto; margin-right: 1; }
    #status-model { width: 1fr; }
    #status-port { width: auto; }
    #memory-bar {
        width: 16;
        height: 1;
        margin: 0 1;
    }
    #memory-label {
        width: auto;
        content-align: left middle;
    }
    TabbedContent { padding-top: 1; }
    #app-log {
        dock: bottom;
        height: 6;
        border-top: solid $primary;
    }
    #chat-log {
        height: 1fr;
    }
    #swap-progress {
        height: auto;
    }
    #metrics-sparkline, #metrics-memory-sparkline {
        height: 3;
        border-bottom: solid $primary;
        padding: 0 1;
    }
    #metrics-table {
        height: 1fr;
    }
    #params-collapsible {
        height: auto;
    }
    .param-row {
        height: auto;
        align: center middle;
    }
    .param-row Label {
        width: 1fr;
        content-align: left middle;
        padding: 0 1;
    }
    .param-row Input {
        width: 16;
    }
    #ctx-progress {
        height: 1;
        width: 100%;
        margin: 0 1;
    }
    #ctx-bar {
        height: 1;
        padding: 0 1;
        content-align: left middle;
    }
    #ctx-progress.ctx-bar-amber Bar > .bar--bar {
        color: $warning;
    }
    #ctx-progress.ctx-bar-red Bar > .bar--bar {
        color: $error;
    }
    .ctx-bar-amber {
        color: $warning;
    }
    .ctx-bar-red {
        color: $error;
    }
    """

    _http: httpx.AsyncClient

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        config: AppConfig | None = None,
    ) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.config = config if config is not None else AppConfig()
        self._poll_in_flight: bool = False
        self.status_state: str = "red"
        self.cold_tracker = ColdTracker()
        self.server_identity = ServerIdentity(host, port)
        self.latest_avail_gib: float | None = None
        self.operations = OperationCoordinator()
        self.history = HistoryStore()
        self.memory_store = MemoryStore()
        self.presets: list[Preset] = load_presets()
        self.preset_idx: int = -1

    @property
    def swap_busy(self) -> bool:
        return self.operations.is_swap_busy

    @override
    def compose(self) -> ComposeResult:
        with Horizontal(id="status-bar"):
            yield Static("●", id="status-dot")
            yield Static("—", id="status-model")
            yield ProgressBar(
                total=16, show_percentage=False, show_eta=False, id="memory-bar"
            )
            yield Static("avail —/— GB · RSS — GB", id="memory-label")
            yield Static(f":{self.port}", id="status-port")
        with TabbedContent(initial="models"):
            with TabPane("Models", id="models"):
                yield ModelsPane(id="models-pane")
            with TabPane("Chat", id="chat"):
                yield ChatPane(id="chat-pane")
            with TabPane("Metrics", id="metrics"):
                yield MetricsPane(id="metrics-pane")
        yield RichLog(id="app-log", markup=False, wrap=True)

    def on_mount(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=f"http://{self.host}:{self.port}",
            timeout=httpx.Timeout(0.5),
        )
        self.set_interval(2.0, self._poll)
        self.query_one(ModelsPane).rescan()
        try:
            self.query_one(MetricsPane).refresh_metrics()
        except NoMatches:
            pass
        try:
            self.query_one(ChatPane).apply_config_params(self.config)
        except NoMatches:
            pass

    def _refresh_metrics(self) -> None:
        try:
            self.query_one(MetricsPane).refresh_metrics()
        except NoMatches:
            pass

    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.tabbed_content.active == "models":
            self.query_one(ModelsPane).rescan()
        elif event.tabbed_content.active == "metrics":
            self._refresh_metrics()

    async def on_unmount(self) -> None:
        await self._http.aclose()

    async def _poll(self) -> None:
        if self._poll_in_flight:
            return
        self._poll_in_flight = True
        try:
            await polling.poll_tick(self)  # pyrefly: ignore[bad-argument-type]
        finally:
            self._poll_in_flight = False

    def effective_model(self) -> str | None:
        return state.effective_model(self)  # pyrefly: ignore[bad-argument-type]

    def refresh_models(self) -> None:
        """Update fits/loaded markers via the pane; a missing pane is fine."""
        try:
            self.query_one(ModelsPane).refresh_markers()
        except NoMatches:
            return

    def _apply_preset(self, preset: Preset) -> None:
        return presets_ctrl.apply_preset(self, preset)  # pyrefly: ignore[bad-argument-type]

    def _cycle_preset(self, step: int) -> None:
        return presets_ctrl.cycle_preset(self, step)  # pyrefly: ignore[bad-argument-type]

    def action_cycle_preset(self) -> None:
        self._cycle_preset(1)

    def action_cycle_preset_back(self) -> None:
        self._cycle_preset(-1)

    async def _classify_liveness(self) -> str:
        return await polling.classify_liveness_for(self)  # pyrefly: ignore[bad-argument-type]

    def _render_status(
        self,
        *,
        model: str | None,
        rss_gib: float | None,
        snapshot: MemorySnapshot | None = None,
    ) -> None:
        return status_bar.render_status(
            self,  # pyrefly: ignore[bad-argument-type]
            model=model,
            rss_gib=rss_gib,
            snapshot=snapshot,
        )

    def set_operation_ui(self, busy: bool) -> None:
        swap_ctrl.set_operation_ui(self, busy)  # pyrefly: ignore[bad-argument-type]

    async def action_cold_start(self) -> None:
        return await swap_ctrl.cold_start(self)  # pyrefly: ignore[bad-argument-type]

    def _restart_config_model(self, pid: int) -> bool:
        return swap_ctrl.restart_config_model(self, pid)  # pyrefly: ignore[bad-argument-type]

    def _chat_pane_or_none(self) -> ChatPane | None:
        try:
            return self.query_one(ChatPane)
        except NoMatches:
            return None

    def chat_has_live_turn(self) -> bool:
        pane = self._chat_pane_or_none()
        return pane is not None and pane.has_live_turn

    def cancel_chat_for_swap(self) -> None:
        pane = self._chat_pane_or_none()
        if pane is not None:
            pane.abort()
            self.log_app("cancelled — model swapping", "yellow")

    def action_cancel_chat(self) -> None:
        pane = self._chat_pane_or_none()
        if pane is not None:
            pane.abort()

    def log_app(self, message: str, style: str | None = None) -> None:
        text = Text(message) if style is None else Text(message, style=style)
        self.query_one("#app-log", RichLog).write(text)

    def action_edit_config(self) -> None:
        return config_edit.edit_config(self)  # pyrefly: ignore[bad-argument-type]


def main() -> None:
    parser = argparse.ArgumentParser(prog="mlx-tui")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", default=None, type=int)
    args = parser.parse_args()
    cfg = load_config()
    # CLI flags win only when explicitly passed; otherwise the config file's
    # values apply (which already carry the hardcoded defaults).
    host = args.host if args.host is not None else cfg.host
    port = args.port if args.port is not None else cfg.port
    MlxTuiApp(host=host, port=port, config=cfg).run()
