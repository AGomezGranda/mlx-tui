"""Textual coordinator: shared state, polling, bindings, cross-pane
orchestration; tab UIs and the swap/boot workers live in the pane modules."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from typing import override

import httpx
import psutil
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Input, RichLog, Static, TabbedContent, TabPane

from mlx_tui.chat_pane import ChatPane
from mlx_tui.config import (
    AppConfig,
    ConfigParseError,
    config_path,
    load_config,
    parse_config,
    write_template,
)
from mlx_tui.models_pane import ModelsPane
from mlx_tui.process import ServerProcessFinder, memory_snapshot, model_from_cmdline
from mlx_tui.status import ColdTracker, classify_liveness, format_status_line
from mlx_tui.swap import BootPlan, SwapMachine, SwapState
from mlx_tui.table import ModelsTable


class MlxTuiApp(App[None]):
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("escape", "cancel_chat", "Cancel"),
        ("ctrl+s", "cold_start", "Start server"),
        ("ctrl+g", "edit_config", "Edit config"),
    ]

    DEFAULT_CSS = """
    #status-bar {
        dock: top;
        width: 100%;
    }
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
        self._process_finder = ServerProcessFinder()
        self.status_state: str = "red"
        self.cold_tracker = ColdTracker()
        self._tracked_model: str | None = None
        self.latest_avail_gib: float | None = None
        self.swap_machine = SwapMachine()
        # Set synchronously on ctrl+s so a second press cannot slip through
        # during the await inside the async action (busy alone is too late).
        self._cold_start_in_flight = False

    @override
    def compose(self) -> ComposeResult:
        yield Static(f"● :{self.port}", id="status-bar")
        with TabbedContent(initial="models"):
            with TabPane("Models", id="models"):
                yield ModelsPane(id="models-pane")
            with TabPane("Chat", id="chat"):
                yield ChatPane(id="chat-pane")
        yield RichLog(id="app-log", markup=False, wrap=True)

    def on_mount(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=f"http://{self.host}:{self.port}",
            timeout=httpx.Timeout(0.5),
        )
        self.set_interval(2.0, self._poll)
        self.query_one(ModelsPane).rescan()

    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.tabbed_content.active == "models":
            self.query_one(ModelsPane).rescan()

    async def on_unmount(self) -> None:
        await self._http.aclose()

    async def _poll(self) -> None:
        if self._poll_in_flight:
            return
        self._poll_in_flight = True
        try:
            state = await self._classify_liveness()
            self.status_state = state
            self.cold_tracker.observe(state)
            self.latest_avail_gib = memory_snapshot().avail_gib
            rss_gib: float | None = None
            model: str | None = None
            pid = self._process_finder.find(self.config.pidfile)
            if pid is not None:
                try:
                    rss_gib = psutil.Process(pid).memory_info().rss / 2**30
                except psutil.NoSuchProcess:
                    pass
                # Effective is authoritative for the marker/status text:
                # warm swaps shadow stale cmdline, while restarts keep both
                # in sync. Gate on pid so a down server (red, no pid) shows
                # "—" rather than a stale tracked value.
                model = self.effective_model()
            self._render_status(model=model, rss_gib=rss_gib)
            self.refresh_models()
        finally:
            self._poll_in_flight = False

    def effective_model(self) -> str | None:
        """The union view: tracked warm-swaps are authoritative, cmdline is fallback.

        Warm in-server loads never update the server's argv, so a successful
        probe-load's ``_tracked_model`` is more truthful than ``--model``.
        Restart/cold paths keep both values in sync, so priority is moot there.
        """
        if self._tracked_model is not None:
            return self._tracked_model
        pid = self._process_finder.find(self.config.pidfile)
        if pid is not None:
            try:
                cmdline_model = model_from_cmdline(psutil.Process(pid))
            except psutil.NoSuchProcess:
                cmdline_model = None
            if cmdline_model is not None:
                return cmdline_model
        return None

    def refresh_models(self) -> None:
        """Update fits/loaded markers via the pane; a missing pane is fine."""
        try:
            self.query_one(ModelsPane).refresh_markers()
        except NoMatches:
            return

    async def _classify_liveness(self) -> str:
        try:
            resp = await self._http.get("/v1/models")
        except Exception:
            return "red"
        body: object = None
        try:
            body = resp.json()
        except ValueError:
            pass
        return classify_liveness(resp.status_code, body)

    def _render_status(self, *, model: str | None, rss_gib: float | None) -> None:
        snapshot = memory_snapshot()
        line = format_status_line(
            state=self.status_state,
            model=model,
            rss_gib=rss_gib,
            memory=snapshot,
            port=self.port,
        )
        if self.status_state == "red" and self.config.start_cmd:
            line += " · [dim]ctrl+s to start[/]"
        self.query_one("#status-bar", Static).update(line)

    def set_swap_ui(self, busy: bool) -> None:
        """Disable/restore table+input around a swap; False clears progress."""
        try:
            self.query_one("#models-table", ModelsTable).disabled = busy
            # A warm swap finishing under a live chat turn must not hand the
            # input back mid-turn: the turn owns it until end_turn.
            live_turn = self.chat_has_live_turn()
            self.query_one("#chat-input", Input).disabled = busy or live_turn
            if not busy:
                self.query_one("#swap-progress", Static).update("")
        except NoMatches:
            return

    async def action_cold_start(self) -> None:
        # Same busy guard as the pane's load action: a second ctrl+s inside a
        # running boot would raise InvalidTransition (starting -> starting)
        # and crash the app. The in-flight flag covers the window before the
        # machine leaves IDLE (the action awaits below).
        if self._cold_start_in_flight or self.swap_machine.busy:
            self.log_app("swap already in progress", "yellow")
            return
        if not self.config.start_cmd:
            self.log_app("set start_cmd in the config to enable cold start", "yellow")
            return
        self._cold_start_in_flight = True
        try:
            # The 2s poll leaves startup windows where status_state still
            # carries its initial "red" against an already-running server;
            # decide on a fresh classification, not the last render.
            state = await self._classify_liveness()
            if state == "green":
                self.status_state = state
                self.log_app("server is already up", "dim")
                return
            # A server process may exist without answering health yet (still
            # loading, or wedged): blind-spawning a second instance cannot
            # bind the port and dies with an OSError traceback. Restart it
            # through the configured commands instead.
            pid = self._process_finder.find(self.config.pidfile)
            if pid is not None:
                self._restart_config_model(pid)
                return
            if state == "amber":
                self.log_app(
                    "something else is answering on this port — "
                    "not starting the server",
                    "yellow",
                )
                return
            try:
                pane = self.query_one(ModelsPane)
            except NoMatches:
                return
            self.swap_machine.transition(SwapState.STARTING)
            self.set_swap_ui(True)
            pane.run_boot(
                BootPlan(
                    model_id=self.config.model,
                    size_on_disk=pane.row_size(self.config.model),
                    stop_first=False,
                    success_line="✓ server is up",
                )
            )
        finally:
            self._cold_start_in_flight = False

    def _restart_config_model(self, pid: int) -> None:
        """Restart path for ctrl+s over an existing-but-unhealthy process."""
        try:
            pane = self.query_one(ModelsPane)
        except NoMatches:
            return
        if not (self.config.start_cmd and self.config.stop_cmd):
            self.log_app(
                f"a server process (pid {pid}) is running but not healthy — "
                "set stop_cmd/start_cmd to let mlx-tui restart it",
                "red",
            )
            return
        if self.chat_has_live_turn():
            self.cancel_chat_for_swap()
        self.swap_machine.transition(SwapState.STOPPING)
        self.set_swap_ui(True)
        pane.run_boot(
            BootPlan(
                model_id=self.config.model,
                size_on_disk=pane.row_size(self.config.model),
                stop_first=True,
                success_line="✓ server is up",
            )
        )

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
        path = config_path()
        if not path.exists():
            write_template(path)
        before = (self.config.host, self.config.port)
        editor = os.environ.get("EDITOR", "vi")
        with self.suspend():
            subprocess.run([*shlex.split(editor), str(path)], check=False)
        try:
            self.config = parse_config(path)
        except ConfigParseError as exc:
            # A typo must not silently wipe the session's commands/model;
            # keep the previous config instead of adopting all-defaults.
            self.log_app(f"config kept — parse failed: {exc}", "red")
            return
        self.log_app("config reloaded")
        if (self.config.host, self.config.port) != before:
            self.log_app("restart mlx-tui to apply host/port", "yellow")


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
