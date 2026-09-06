"""Textual coordinator: shared state, polling, bindings, cross-pane
orchestration; tab UIs and the swap/boot workers live in the pane modules."""

from __future__ import annotations

import argparse
import dataclasses
import os
import shlex
import subprocess
import time
from dataclasses import replace
from typing import override

import httpx
import psutil
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import Input, ProgressBar, RichLog, Static, TabbedContent, TabPane

from mlx_tui import process
from mlx_tui.chat_pane import ChatPane
from mlx_tui.config import (
    AppConfig,
    ConfigParseError,
    config_path,
    load_config,
    parse_config,
    write_template,
)
from mlx_tui.history.store import HistoryStore, MemoryRecord, MemoryStore
from mlx_tui.metrics_pane import MetricsPane
from mlx_tui.models_pane import ModelsPane
from mlx_tui.presets import Preset, load_presets
from mlx_tui.process import LOOPBACK_HOSTS, ProcessIdentity, memory_snapshot
from mlx_tui.status import (
    ColdTracker,
    MemorySnapshot,
    ServerIdentity,
    ServerProbe,
    probe_from_response,
)
from mlx_tui.swap import BootPlan
from mlx_tui.table import ModelsTable

from .operations import OperationCoordinator, OperationKind


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
        self._last_errors: dict[str, str] = {}
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

    async def _fetch_probe(self) -> ServerProbe:
        try:
            resp = await self._http.get("/v1/models")
        except httpx.HTTPError as exc:
            self.log_error_once("poll", exc)
            return ServerProbe(state="red", model_id=None)
        try:
            body: object = resp.json()
        except ValueError as exc:
            self.log_error_once("poll", exc)
            return ServerProbe(state="amber", model_id=None)
        return probe_from_response(resp.status_code, body)

    async def _poll(self) -> None:
        if self._poll_in_flight:
            return
        self._poll_in_flight = True
        prev_state = self.status_state
        prev_identity = self.server_identity
        try:
            probe = await self._fetch_probe()
            self.status_state = probe.state
            self.cold_tracker.observe(probe.state)
            snapshot = memory_snapshot()
            self.latest_avail_gib = snapshot.avail_gib
            if self.host in LOOPBACK_HOSTS:
                proc_ident = process.find_server_process(
                    self.host, self.port, self.config.pidfile
                )
            else:
                proc_ident = None
            self.update_server_identity(probe, proc_ident)
            rss_gib: float | None = None
            if proc_ident is not None:
                try:
                    rss_gib = psutil.Process(proc_ident.pid).memory_info().rss / 2**30
                except (
                    psutil.NoSuchProcess,
                    psutil.AccessDenied,
                    psutil.ZombieProcess,
                ):
                    pass
            model = self.effective_model()
            self._render_status(model=model, rss_gib=rss_gib, snapshot=snapshot)
            self.refresh_models()
            rec = MemoryRecord(
                ts=time.time(),
                model=model,
                rss_gib=rss_gib,
                avail_gib=snapshot.avail_gib,
                total_gib=snapshot.total_gib,
            )
            self.memory_store.add(rec)
            self._refresh_metrics()
            if probe.state == "green":
                self.clear_error("poll")
        except Exception as exc:
            # Unexpected worker fault: keep the last known state instead of
            # fabricating ordinary server downtime, and surface one bounded
            # diagnostic (repeats stay silent until recovery).
            self.status_state = prev_state
            self.server_identity = prev_identity
            self.log_error_once("poll", exc)
        finally:
            self._poll_in_flight = False

    def update_server_identity(
        self,
        probe: ServerProbe,
        process_identity: ProcessIdentity | None,
    ) -> None:
        """Preserve a verified selection when the endpoint only returns a catalog."""
        model = probe.model_id if probe.state == "green" else None
        pid = process_identity.pid if process_identity is not None else None
        ctime = process_identity.create_time if process_identity is not None else None
        previous = self.server_identity
        if (
            probe.state == "green"
            and model is None
            and previous.model_id in probe.available_models
            and (previous.host, previous.port, previous.pid, previous.pid_create_time)
            == (self.host, self.port, pid, ctime)
        ):
            model = previous.model_id
        self.server_identity = dataclasses.replace(
            self.server_identity,
            host=self.host,
            port=self.port,
            model_id=model,
            pid=pid,
            pid_create_time=ctime,
        )

    def effective_model(self) -> str | None:
        """Only the model in the latest green endpoint probe."""
        if self.status_state != "green":
            return None
        return self.server_identity.model_id

    def refresh_models(self) -> None:
        """Update fits/loaded markers via the pane; a missing pane is fine."""
        try:
            self.query_one(ModelsPane).refresh_markers()
        except NoMatches:
            return

    def _apply_preset(self, preset: Preset) -> None:
        try:
            pane = self.query_one(ChatPane)
        except NoMatches:
            return
        pane._apply_params_to_inputs(
            preset.temperature, preset.top_p, preset.max_tokens
        )
        # sync config snapshot
        temp, top_p, max_tok = pane._parse_params()
        self.config = replace(
            self.config,
            temperature=temp,
            top_p=top_p,
            max_tokens=max_tok,
            system=preset.system.strip() or None,
        )
        pane.apply_config_params(self.config)
        self.log_app(f"preset: {preset.name}", "dim")

    def _cycle_preset(self, step: int) -> None:
        if not self.presets:
            self.log_app("no presets — create ~/.config/mlx-tui/presets.toml", "yellow")
            return
        self.preset_idx = (self.preset_idx + step) % len(self.presets)
        self._apply_preset(self.presets[self.preset_idx])

    def action_cycle_preset(self) -> None:
        self._cycle_preset(1)

    def action_cycle_preset_back(self) -> None:
        self._cycle_preset(-1)

    async def _classify_liveness(self) -> str:
        return (await self._fetch_probe()).state

    def _render_status(
        self,
        *,
        model: str | None,
        rss_gib: float | None,
        snapshot: MemorySnapshot | None = None,
    ) -> None:
        if snapshot is None:
            snapshot = memory_snapshot()
        try:
            self.query_one("#status-dot", Static).update(
                f"[{'yellow' if self.status_state == 'amber' else self.status_state}]●[/]"
            )
            self.query_one("#status-model", Static).update(model if model else "—")
            self.query_one("#status-port", Static).update(
                f":{self.port}"
                + (
                    " · [dim]ctrl+s to start[/]"
                    if self.status_state == "red" and self.config.start_cmd
                    else ""
                )
            )
        except NoMatches:
            pass
        try:
            bar = self.query_one("#memory-bar", ProgressBar)
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
            self.query_one("#memory-label", Static).update(
                f"RSS {rss_part} GB · avail {avail:.1f}/{total:.1f} GB"
            )
        except NoMatches:
            pass

    def set_operation_ui(self, busy: bool) -> None:
        """Disable or restore controls for a model lifecycle operation."""
        try:
            self.query_one("#models-table", ModelsTable).disabled = busy
            self.query_one("#chat-input", Input).disabled = (
                busy or self.operations.current is OperationKind.CHATTING
            )
            if not busy:
                self.query_one("#swap-progress", Static).update("")
        except NoMatches:
            return

    async def action_cold_start(self) -> None:  # noqa: PLR0911
        """Check the endpoint, then own a complete cold-start worker lifetime."""
        if not self.config.start_cmd:
            self.log_app("set start_cmd in the config to enable cold start", "yellow")
            return
        if self.operations.current is OperationKind.CHATTING:
            self.cancel_chat_for_swap()
            return
        if not self.operations.try_acquire(OperationKind.RESTARTING):
            self.log_app("operation already in progress", "yellow")
            return

        worker_started = False
        try:
            # The 2s poll leaves startup windows where status_state still carries
            # its initial red against an already-running server.
            live_state = await self._classify_liveness()
            if live_state == "green":
                self.status_state = live_state
                self.log_app("server is already up", "dim")
                return

            # A process may exist without answering health yet. Restart it through
            # configured commands instead of racing a second instance onto the port.
            proc_ident = process.find_server_process(
                self.host, self.port, self.config.pidfile
            )
            if proc_ident is not None:
                worker_started = self._restart_config_model(proc_ident.pid)
                return
            if live_state == "amber":
                self.log_app(
                    "something else is answering on this port — not starting the server",
                    "yellow",
                )
                return
            try:
                pane = self.query_one(ModelsPane)
            except NoMatches:
                return
            self.set_operation_ui(True)
            pane.run_boot(
                BootPlan(
                    model_id=self.config.model,
                    size_on_disk=pane.row_size(self.config.model),
                    stop_first=False,
                    success_line="✓ server is up",
                )
            )
            worker_started = True
        finally:
            if not worker_started:
                self.operations.release(OperationKind.RESTARTING)
                self.set_operation_ui(False)

    def _restart_config_model(self, pid: int) -> bool:
        """Launch the restart worker for an existing unhealthy server process."""
        try:
            pane = self.query_one(ModelsPane)
        except NoMatches:
            return False
        if self.config.swap_policy == "warm":
            self.log_app(
                'cannot restart: swap_policy is "warm" (requires green, no restarts)',
                "red",
            )
            return False
        if not (self.config.start_cmd and self.config.stop_cmd):
            self.log_app(
                f"a server process (pid {pid}) is running but not healthy — "
                "set stop_cmd/start_cmd to let mlx-tui restart it",
                "red",
            )
            return False
        if self.operations.current is OperationKind.CHATTING:
            self.cancel_chat_for_swap()
            return False
        acquired_here = False
        if self.operations.current is not OperationKind.RESTARTING:
            if not self.operations.try_acquire(OperationKind.RESTARTING):
                self.log_app("operation already in progress", "yellow")
                return False
            acquired_here = True
        try:
            self.set_operation_ui(True)
            pane.run_boot(
                BootPlan(
                    model_id=self.config.model,
                    size_on_disk=pane.row_size(self.config.model),
                    stop_first=True,
                    success_line="✓ server is up",
                )
            )
        except Exception:
            if acquired_here:
                self.operations.release(OperationKind.RESTARTING)
                self.set_operation_ui(False)
            raise
        return True

    def _chat_pane_or_none(self) -> ChatPane | None:
        try:
            return self.query_one(ChatPane)
        except NoMatches:
            return None

    def cancel_chat_for_swap(self) -> None:
        pane = self._chat_pane_or_none()
        if pane is not None:
            pane.abort()
            self.log_app("cancellation requested — retry after cleanup", "yellow")

    def action_cancel_chat(self) -> None:
        pane = self._chat_pane_or_none()
        if pane is not None:
            pane.abort()

    def log_app(self, message: str, style: str | None = None) -> None:
        text = Text(message) if style is None else Text(message, style=style)
        self.query_one("#app-log", RichLog).write(text)

    def log_error_once(self, source: str, exc: Exception) -> None:
        """Log one bounded diagnostic per distinct failure for a source.

        Identical repeats stay silent until recovery; a changed error logs
        immediately. Callers clear the source via clear_error() on success so
        a recurrence becomes visible again.
        """
        key = f"{exc.__class__.__name__}: {exc}"
        if self._last_errors.get(source) == key:
            return
        self._last_errors[source] = key
        self.log_app(f"{source} failed: {key}"[:300], "red")

    def clear_error(self, source: str) -> None:
        """Forget a source's last failure after a successful fetch."""
        self._last_errors.pop(source, None)

    def action_edit_config(self) -> None:
        path = config_path()
        if not path.exists():
            write_template(path)
        before = (self.config.host, self.config.port)
        editor = os.environ.get("EDITOR", "vi")
        try:
            with self.suspend():
                proc = subprocess.run([*shlex.split(editor), str(path)], check=False)
        except OSError as exc:
            self.log_app(
                f"config edit failed: {exc.__class__.__name__}: {exc}"[:200], "red"
            )
            return
        if proc.returncode != 0:
            self.log_app(f"config edit failed: editor exited {proc.returncode}", "red")
            return
        try:
            self.config = parse_config(path)
        except ConfigParseError as exc:
            # A typo must not silently wipe the session's commands/model;
            # keep the previous config instead of adopting all-defaults.
            self.log_app(f"config kept — parse failed: {exc}", "red")
            return
        self.log_app("config reloaded")
        try:
            pane = self.query_one(ChatPane)
            pane.apply_config_params(self.config)
            pane.refresh_context_bar()
        except NoMatches:
            pass
        self.presets = load_presets()
        self.preset_idx = -1
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
