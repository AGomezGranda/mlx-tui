"""Textual coordinator: shared state, polling, bindings, cross-pane orchestration."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import override

import httpx
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.widgets import (
    Collapsible,
    Footer,
    RichLog,
    TabbedContent,
    TabPane,
)

import mlx_tui.app.ops as _app_ops
import mlx_tui.app.polling as _app_polling
import mlx_tui.app.state as _app_state
import mlx_tui.app.ui as _app_ui
from mlx_tui.chat_ui.pane import ChatPane
from mlx_tui.compare.pane import ComparePane
from mlx_tui.comparison import ComparisonResult, SavedChoice
from mlx_tui.config import AppConfig
from mlx_tui.history.store import HistoryStore, MemoryRecord
from mlx_tui.managed.runtime import ManagedRuntime
from mlx_tui.metrics_pane import MetricsPane
from mlx_tui.models_pane import ModelsPane
from mlx_tui.operations import OperationCoordinator
from mlx_tui.presets import Preset, load_presets
from mlx_tui.process import ProcessIdentity
from mlx_tui.profiles import (
    ProfileEntry,
    ProfileValidationError,
    load_coding_profiles,
)
from mlx_tui.status import MemorySnapshot, ServerIdentity, ServerProbe
from mlx_tui.status_bar import StatusBar

_COMPACT_HEIGHT = 32


class MlxTuiApp(App[None]):
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("escape", "cancel_chat", "Cancel"),
        Binding("ctrl+s", "cold_start", "Start server", show=False),
        ("ctrl+g", "edit_config", "Config"),
        Binding("ctrl+n", "cycle_preset", "Preset", show=False),
        Binding("ctrl+o", "cycle_preset_back", "Preset back", show=False),
        ("f2", "toggle_activity", "Activity"),
        ("f3", "endpoint_info", "Endpoint"),
    ]

    DEFAULT_CSS = """
    Screen > TabbedContent { height: 1fr; padding: 0; }
    TabbedContent > ContentSwitcher { height: 1fr; }
    TabbedContent TabPane { height: 1fr; padding: 0; }
    #activity {
        height: auto;
        padding: 0;
        border: none;
    }
    #activity > CollapsibleTitle {
        height: 3;
        width: 100%;
        padding: 1 2;
    }
    #activity > Contents { padding: 0; height: auto; overflow: hidden; }
    #activity.-collapsed { height: 3; overflow: hidden; }
    .compact #activity > CollapsibleTitle { height: 1; padding: 0 2; }
    .compact #activity.-collapsed { height: 1; }
    #activity.-collapsed > Contents {
        display: block;
    }
    #app-log { height: 6; padding: 0 2; }
    .compact #app-log { height: 3; }
    .compact #chat-input { height: 2; min-height: 2; }
    .compact #chat-composer-row Button { height: 2; min-height: 2; }
    Footer { height: 1; padding: 0 1; }
    """

    _http: httpx.AsyncClient

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        config: AppConfig | None = None,
        *,
        needs_setup: bool = False,
        config_error: str | None = None,
    ) -> None:
        super().__init__()
        if (
            config is not None
            and config.runtime_mode == "managed"
            and (
                host,
                port,
            )
            == ("127.0.0.1", 8080)
        ):
            host, port = "127.0.0.1", 18080
        self.host = host
        self.port = port
        self.config = config if config is not None else AppConfig()
        self.needs_setup = needs_setup
        self.config_error = config_error
        self._poll_in_flight: bool = False
        self._observation_seq = 0
        self._last_errors: dict[str, str] = {}
        self._latest_event = ""
        self._unseen_notice: str | None = None
        self.status_state: str = "red"
        self.server_identity = ServerIdentity(
            host, port, selected_model=self.config.model
        )
        self.latest_avail_gib: float | None = None
        self.operations = OperationCoordinator()
        self.history = HistoryStore()
        self.memory_store: deque[MemoryRecord] = deque(maxlen=256)
        self.presets: list[Preset] = load_presets()
        self.preset_idx: int = -1
        try:
            self.profile_entries = load_coding_profiles()
            self.profile_error: str | None = None
        except ProfileValidationError as exc:
            self.profile_entries = []
            self.profile_error = str(exc)
        ids = tuple(entry.profile.id for entry in self.profile_entries[:2])
        self.comparison_profile_ids: tuple[str, ...] = ids
        self.last_comparison: ComparisonResult | None = None
        self.saved_choice: SavedChoice | None = None
        self.saved_choice_error: str | None = None
        self.active_profile_id: str | None = None
        self.active_profile_modified = False
        self.setup_preferences: dict[str, str] = {
            "context_budget": "unknown",
            "memory_priority": "unknown",
        }
        self._applying_profile = False
        self._closing = False
        self.managed_runtime = (
            ManagedRuntime(host=self.host, port=self.port)
            if self.config.runtime_mode == "managed"
            else None
        )
        self._load_saved_choice()

    @property
    def swap_busy(self) -> bool:
        return _app_state.swap_busy(self)

    def _load_saved_choice(self) -> None:
        return _app_state._load_saved_choice(self)

    def profile_entry(self, profile_id: str) -> ProfileEntry | None:
        return _app_state.profile_entry(self, profile_id)

    def selected_comparison_profiles(self) -> tuple[ProfileEntry, ...]:
        return _app_state.selected_comparison_profiles(self)

    def update_comparison_candidate(self, slot: int, profile_id: str) -> None:
        return _app_state.update_comparison_candidate(self, slot, profile_id)

    def refresh_profile_views(self) -> None:
        return _app_state.refresh_profile_views(self)

    def mark_active_profile_modified(self) -> None:
        return _app_state.mark_active_profile_modified(self)

    def apply_coding_profile(
        self, profile_id: str, *, expected_fingerprint: str | None = None
    ) -> None:
        return _app_state.apply_coding_profile(
            self, profile_id, expected_fingerprint=expected_fingerprint
        )

    @override
    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar", port=self.port)
        with TabbedContent(initial="models"):
            with TabPane("Models", id="models"):
                yield ModelsPane(id="models-pane")
            with TabPane("Compare", id="compare"):
                yield ComparePane(id="compare-pane")
            with TabPane("Chat", id="chat"):
                yield ChatPane(id="chat-pane")
            with TabPane("Metrics", id="metrics"):
                yield MetricsPane(id="metrics-pane")
        with Collapsible(id="activity", title="Activity", collapsed=True):
            yield RichLog(id="app-log", markup=False, wrap=True)
        yield Footer(compact=True, show_command_palette=False)

    def on_resize(self, event: events.Resize) -> None:
        self.screen_stack[0].set_class(event.size.height < _COMPACT_HEIGHT, "compact")
        self.refresh_activity()

    def on_mount(self) -> None:
        self.screen.set_class(self.size.height < _COMPACT_HEIGHT, "compact")
        self.query_one("#app-log", RichLog).can_focus = False
        rendered = f"[{self.host}]" if ":" in self.host else self.host
        self._http = httpx.AsyncClient(
            base_url=f"http://{rendered}:{self.port}",
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
        if self.config_error:
            self.log_app(f"config needs repair: {self.config_error}", "red")
        elif self.needs_setup:
            from mlx_tui.setup_screen import SetupScreen  # noqa: PLC0415

            self.push_screen(SetupScreen())

    def _refresh_metrics(self) -> None:
        return _app_polling._refresh_metrics(self)

    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        return _app_ui._on_tab_activated(self, event)

    async def on_unmount(self) -> None:  # noqa: PLR0912
        self._closing = True
        from mlx_tui.setup_screen import SetupScreen  # noqa: PLC0415

        def report_shutdown_error(label: str, exc: Exception) -> None:
            with suppress(Exception):
                self.log_app(
                    f"shutdown {label} failed: {exc.__class__.__name__}", "red"
                )

        try:
            setup = self.query_one(SetupScreen)
        except NoMatches:
            setup = None
        if setup is not None:
            try:
                await setup.cancel_install_and_wait()
            except Exception as exc:
                report_shutdown_error("installer cleanup", exc)
        try:
            compare = self.query_one(ComparePane)
        except NoMatches:
            compare = None
        if compare is not None:
            try:
                compare.abort()
                await compare.wait_for_cleanup()
            except Exception as exc:
                report_shutdown_error("comparison cleanup", exc)
        chat = self._chat_pane_or_none()
        if chat is not None:
            try:
                chat.abort()
            except Exception as exc:
                report_shutdown_error("chat abort", exc)
            try:
                await chat.wait_for_cleanup()
            except Exception as exc:
                report_shutdown_error("chat cleanup", exc)
            # Idempotent best-effort flush for signal-driven exits; SIGKILL /
            # power loss recovers only acknowledged checkpoints (no zero-loss).
            try:
                await chat.flush_for_shutdown()
            except Exception as exc:
                report_shutdown_error("chat flush", exc)
        if self.managed_runtime is not None:
            try:
                await asyncio.to_thread(self.managed_runtime.close)
            except Exception as exc:
                report_shutdown_error("managed runtime cleanup", exc)
        if hasattr(self, "_http"):
            try:
                await self._http.aclose()
            except Exception as exc:
                report_shutdown_error("HTTP cleanup", exc)

    @override
    async def action_quit(self) -> None:
        """Normal quit: abort/await/flush, offering Retry on save failure."""
        chat = self._chat_pane_or_none()
        if chat is not None:
            chat.abort()
            await chat.wait_for_cleanup()
            try:
                ok = await chat.flush_for_shutdown()
            except Exception:
                ok = False
            if not ok and chat.save_failed:
                from mlx_tui.confirm import ConfirmScreen  # noqa: PLC0415

                def _done(result: bool | None) -> None:
                    if result is True:
                        # Retry save; stay open on failure, exit on success.
                        async def _retry_then_exit() -> None:
                            try:
                                succeeded = await chat._retry_save()
                            except Exception:
                                succeeded = False
                            if succeeded:
                                self.exit()
                            else:
                                self.log_app(
                                    "save still failing — retry or discard",
                                    "red",
                                )

                        asyncio.ensure_future(_retry_then_exit())
                    else:
                        # Explicit quit-with-unsaved-work.
                        self.exit()

                self.push_screen(
                    ConfirmScreen("Save failed — retry save? (keep = quit anyway)"),
                    _done,
                )
                return
        self.exit()

    async def _fetch_probe(self) -> ServerProbe:
        return await _app_polling._fetch_probe(self)

    def _advance_observation(self) -> int:
        return _app_polling._advance_observation(self)

    async def _poll(self) -> None:  # noqa: PLR0912
        return await _app_polling._poll(self)

    def update_server_identity(
        self,
        probe: ServerProbe,
        process_identity: ProcessIdentity | None,
        *,
        poll_sequence: int | None = None,
        endpoint_changed: bool = False,
    ) -> bool:
        return _app_polling.update_server_identity(
            self,
            probe,
            process_identity,
            poll_sequence=poll_sequence,
            endpoint_changed=endpoint_changed,
        )

    def select_model(self, model: str) -> None:
        return _app_polling.select_model(self, model)

    def restore_request_state(self, config: AppConfig, model: str | None) -> None:
        return _app_polling.restore_request_state(self, config, model)

    def record_generation_success(
        self, selected_model: str, response_model: str | None
    ) -> None:
        return _app_polling.record_generation_success(
            self, selected_model, response_model
        )

    def record_generation_failure(self, *, cancelled: bool = False) -> None:
        return _app_polling.record_generation_failure(self, cancelled=cancelled)

    def effective_model(self) -> str | None:
        return _app_polling.effective_model(self)

    def endpoint_preview_text(self) -> str:
        return _app_polling.endpoint_preview_text(self)

    def action_endpoint_info(self) -> None:
        return _app_polling.action_endpoint_info(self)

    def refresh_models(self) -> None:
        return _app_polling.refresh_models(self)

    def _apply_preset(self, preset: Preset) -> None:
        return _app_ui._apply_preset(self, preset)

    def _cycle_preset(self, step: int) -> None:
        return _app_ui._cycle_preset(self, step)

    def action_cycle_preset(self) -> None:
        return _app_ui.action_cycle_preset(self)

    def action_cycle_preset_back(self) -> None:
        return _app_ui.action_cycle_preset_back(self)

    async def _classify_liveness(self) -> str:
        return await _app_polling._classify_liveness(self)

    def _render_status(
        self,
        *,
        rss_gib: float | None,
        snapshot: MemorySnapshot | None = None,
        ownership_state: str | None = None,
    ) -> None:
        return _app_polling._render_status(
            self,
            rss_gib=rss_gib,
            snapshot=snapshot,
            ownership_state=ownership_state,
        )

    def set_operation_ui(self, busy: bool) -> None:
        return _app_ops.set_operation_ui(self, busy)

    async def action_cold_start(self) -> None:  # noqa: PLR0911
        return await _app_ops.action_cold_start(self)

    def ensure_managed_runtime(self) -> ManagedRuntime:
        return _app_ops.ensure_managed_runtime(self)

    def set_runtime_mode(self, mode: str) -> None:
        return _app_ops.set_runtime_mode(self, mode)

    def _managed_target(self) -> object:
        return _app_ops._managed_target(self)

    def _action_managed_start(self) -> None:
        return _app_ops._action_managed_start(self)

    def start_managed(
        self, model: Path, *, on_line: Callable[[str], None]
    ) -> ServerProbe:
        return _app_ops.start_managed(self, model, on_line=on_line)

    def _restart_config_model(self, pid: int) -> bool:
        return _app_ops._restart_config_model(self, pid)

    def _chat_pane_or_none(self) -> ChatPane | None:
        return _app_ops._chat_pane_or_none(self)

    def cancel_chat_for_swap(self) -> None:
        return _app_ops.cancel_chat_for_swap(self)

    def action_cancel_chat(self) -> None:
        return _app_ops.action_cancel_chat(self)

    def log_app(self, message: str, style: str | None = None) -> None:
        return _app_ui.log_app(self, message, style)

    @override
    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        return _app_ui.check_action(self, action, parameters)

    def action_toggle_activity(self) -> None:
        return _app_ui.action_toggle_activity(self)

    @on(Collapsible.Toggled, "#activity")
    def _activity_toggled(self, event: Collapsible.Toggled) -> None:
        return _app_ui._activity_toggled(self, event)

    def refresh_activity(self) -> None:
        return _app_ui.refresh_activity(self)

    def log_error_once(self, source: str, exc: Exception) -> None:
        return _app_ui.log_error_once(self, source, exc)

    def clear_error(self, source: str) -> None:
        return _app_ui.clear_error(self, source)

    def action_edit_config(self) -> None:
        return _app_ui.action_edit_config(self)
