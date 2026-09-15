"""Textual coordinator: shared state, polling, bindings, cross-pane
orchestration; tab UIs and the swap/boot workers live in the pane modules."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import signal
import subprocess
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import override

import httpx
import psutil
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from mlx_tui import process
from mlx_tui.chat_pane import ChatPane
from mlx_tui.compare_pane import ComparePane
from mlx_tui.comparison import (
    ComparisonResult,
    SavedChoice,
    choice_is_committed,
    choice_path,
    load_choice,
    load_comparison,
    verify_profile_snapshot,
)
from mlx_tui.comparison_contracts import ComparisonValidationError, parse_loopback_url
from mlx_tui.config import (
    AppConfig,
    ConfigParseError,
    config_path,
    load_config,
    parse_config,
    write_template,
)
from mlx_tui.diagnostics import DiagnosticsError, write_diagnostics
from mlx_tui.history.store import HistoryStore, MemoryRecord
from mlx_tui.managed import ManagedRuntime
from mlx_tui.metrics_pane import MetricsPane
from mlx_tui.models import resolve_cached_snapshot
from mlx_tui.models_pane import ModelsPane
from mlx_tui.operations import OperationCoordinator, OperationKind
from mlx_tui.params import ParamsPane
from mlx_tui.presets import Preset, load_presets
from mlx_tui.process import LOOPBACK_HOSTS, ProcessIdentity, memory_snapshot
from mlx_tui.profiles import ProfileEntry, ProfileValidationError, load_coding_profiles
from mlx_tui.status import (
    MemorySnapshot,
    ServerIdentity,
    ServerProbe,
    health_state_from_response,
    probe_from_response,
)
from mlx_tui.status_bar import StatusBar
from mlx_tui.swap import BootPlan
from mlx_tui.table import ModelsTable
from mlx_tui.text_screen import TextPreviewScreen

_COMPACT_HEIGHT = 32
_PROFILE_COUNT = 2


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
        return self.operations.is_swap_busy

    def _load_saved_choice(self) -> None:
        path = choice_path()
        if not path.exists():
            return
        try:
            choice = load_choice(path)
            if not choice_is_committed(choice):
                raise ValueError("saved choice is pending or its run does not match")
            self.last_comparison = load_comparison(choice.result_path)
        except (OSError, ValueError) as exc:
            self.saved_choice_error = str(exc)
            return
        self.saved_choice = choice

    def profile_entry(self, profile_id: str) -> ProfileEntry | None:
        return next(
            (entry for entry in self.profile_entries if entry.profile.id == profile_id),
            None,
        )

    def selected_comparison_profiles(self) -> tuple[ProfileEntry, ...]:
        entries = tuple(
            entry
            for profile_id in self.comparison_profile_ids
            if (entry := self.profile_entry(profile_id)) is not None
        )
        return entries

    def update_comparison_candidate(self, slot: int, profile_id: str) -> None:
        if self.profile_entry(profile_id) is None or slot not in {0, 1}:
            return
        ids = list(self.comparison_profile_ids)
        if len(ids) != _PROFILE_COUNT:
            return
        other = 1 - slot
        if ids[other] == profile_id:
            ids[other] = ids[slot]
        ids[slot] = profile_id
        self.comparison_profile_ids = tuple(ids)
        self.refresh_profile_views()

    def refresh_profile_views(self) -> None:
        for pane_type in (ComparePane,):
            try:
                pane = self.query_one(pane_type)
                pane.refresh_profile_state()
            except NoMatches:
                pass

    def mark_active_profile_modified(self) -> None:
        if self.active_profile_id is None or self._applying_profile:
            return
        self.active_profile_modified = True
        self.refresh_profile_views()

    def apply_coding_profile(
        self, profile_id: str, *, expected_fingerprint: str | None = None
    ) -> None:
        entry = self.profile_entry(profile_id)
        if entry is None:
            raise ValueError(f"unknown coding profile: {profile_id}")
        profile = entry.profile
        if (
            expected_fingerprint is not None
            and profile.fingerprint != expected_fingerprint
        ):
            raise ValueError("saved profile no longer matches the packaged snapshot")
        snapshot = resolve_cached_snapshot(profile.repo_id, profile.revision)
        verify_profile_snapshot(entry, snapshot)
        self._applying_profile = True
        try:
            self.config = replace(
                self.config,
                model=str(snapshot),
                temperature=profile.temperature,
                top_p=profile.top_p,
                max_tokens=profile.max_tokens,
                seed=profile.seed,
                enable_thinking=profile.enable_thinking,
                system=profile.system or None,
                max_ctx=profile.max_ctx,
            )
            self.select_model(str(snapshot))
            pane = self._chat_pane_or_none()
            if pane is not None:
                pane.apply_config_params(self.config)
                pane.refresh_context_bar()
            self.active_profile_id = profile.id
            self.active_profile_modified = False
        finally:
            self._applying_profile = False
        self.refresh_models()
        self.refresh_profile_views()
        pane = self._chat_pane_or_none()
        if pane is not None:
            pane.mark_next_request_dirty()
        self.log_app(
            (
                f"applied {profile.id}; managed activation is ready"
                if self.config.runtime_mode == "managed"
                else f"applied {profile.id}; launch/restart remains operator-managed"
            ),
            "yellow",
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
        try:
            self.query_one(MetricsPane).refresh_metrics()
        except NoMatches:
            pass

    @on(TabbedContent.TabActivated)
    def _on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        pane = self._chat_pane_or_none()
        if pane is not None:
            pane._follow_from = None
            pane._restore_composer_focus = False
        if event.tabbed_content.active == "models":
            self.query_one(ModelsPane).rescan()
        elif event.tabbed_content.active == "metrics":
            self._refresh_metrics()

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
        health_state = "red"
        try:
            health = await self._http.get("/health")
        except httpx.HTTPError as exc:
            self.log_error_once("poll-health", exc)
        else:
            try:
                health_body: object = health.json()
            except ValueError as exc:
                self.log_error_once("poll-health", exc)
                health_state = "amber"
            else:
                health_state = health_state_from_response(
                    health.status_code, health_body
                )
                self.clear_error("poll-health")

        catalogue = ServerProbe(state="red", model_id=None, catalogue_state="red")
        try:
            response = await self._http.get("/v1/models")
        except httpx.HTTPError as exc:
            self.log_error_once("poll-catalogue", exc)
        else:
            try:
                catalogue_body: object = response.json()
            except ValueError as exc:
                self.log_error_once("poll-catalogue", exc)
                catalogue = ServerProbe(
                    state="amber", model_id=None, catalogue_state="amber"
                )
            else:
                catalogue = probe_from_response(response.status_code, catalogue_body)
                self.clear_error("poll-catalogue")
        return ServerProbe(
            state=health_state,
            model_id=None,
            available_models=catalogue.available_models,
            catalogue_state=catalogue.catalogue_state,
        )

    def _advance_observation(self) -> int:
        self._observation_seq += 1
        return self._observation_seq

    async def _poll(self) -> None:  # noqa: PLR0912
        if (
            self._closing
            or self._poll_in_flight
            or self.operations.current is OperationKind.COMPARING
        ):
            return
        self._poll_in_flight = True
        poll_sequence = self._advance_observation()
        prev_state = self.status_state
        prev_identity = self.server_identity
        try:
            probe = await self._fetch_probe()
            if poll_sequence != self._observation_seq:
                return
            self.status_state = probe.state
            snapshot = memory_snapshot()
            self.latest_avail_gib = snapshot.avail_gib
            ownership_state: str | None = None
            if self.managed_runtime is not None:
                if not self.managed_runtime.child_is_running():
                    if self.managed_runtime.identity is not None:
                        ownership_state = "owned child exited"
                        self.status_state = "red"
                        probe = replace(probe, state="red")
                    proc_ident = None
                elif self.managed_runtime.listener_matches_child():
                    proc_ident = self.managed_runtime.identity
                else:
                    ownership_state = "ownership mismatch"
                    self.status_state = "amber"
                    probe = replace(probe, state="amber")
                    proc_ident = None
            elif self.host in LOOPBACK_HOSTS:
                proc_ident = process.find_server_process(self.host, self.port)
            else:
                proc_ident = None
            self.update_server_identity(
                probe,
                proc_ident,
                poll_sequence=poll_sequence,
                endpoint_changed=probe.state != prev_state,
            )
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
            self._render_status(
                rss_gib=rss_gib,
                snapshot=snapshot,
                ownership_state=ownership_state,
            )
            self.refresh_models()
            rec = MemoryRecord(
                ts=time.time(),
                model=None,
                rss_gib=rss_gib,
                avail_gib=snapshot.avail_gib,
                total_gib=snapshot.total_gib,
            )
            self.memory_store.append(rec)
            self._refresh_metrics()
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
        *,
        poll_sequence: int | None = None,
        endpoint_changed: bool = False,
    ) -> bool:
        """Apply endpoint/process facts without deriving selection from catalogue."""
        if poll_sequence is not None and poll_sequence != self._observation_seq:
            return False
        pid = process_identity.pid if process_identity is not None else None
        ctime = process_identity.create_time if process_identity is not None else None
        previous = self.server_identity
        endpoint_changed = endpoint_changed or (
            previous.host,
            previous.port,
            previous.pid,
            previous.pid_create_time,
        ) != (self.host, self.port, pid, ctime)
        generation_state = "unknown" if endpoint_changed else previous.generation_state
        selected_model = previous.selected_model
        last_response_model = previous.last_response_model
        last_success_at = previous.last_success_at
        if probe.model_id is not None:
            selected_model = probe.model_id
            last_response_model = probe.model_id
            last_success_at = time.time()
            generation_state = "succeeded"
            if poll_sequence is None:
                self._advance_observation()
        self.server_identity = replace(
            self.server_identity,
            host=self.host,
            port=self.port,
            selected_model=selected_model,
            last_response_model=last_response_model,
            last_success_at=last_success_at,
            generation_state=generation_state,
            available_models=probe.available_models,
            catalogue_state=probe.catalogue_state,
            pid=pid,
            pid_create_time=ctime,
        )
        return True

    def select_model(self, model: str) -> None:
        """Set the explicit request target and invalidate current-success claims."""
        self._advance_observation()
        self.server_identity = replace(
            self.server_identity,
            selected_model=model,
            generation_state="unknown",
        )
        self.mark_active_profile_modified()
        pane = self._chat_pane_or_none()
        if pane is not None and not self._applying_profile:
            pane.mark_next_request_dirty()

    def restore_request_state(self, config: AppConfig, model: str | None) -> None:
        """Restore the exact pre-comparison request controls as unverified state."""
        self.config = config
        self._advance_observation()
        self.server_identity = replace(
            self.server_identity,
            selected_model=model,
            generation_state="unknown",
        )
        pane = self._chat_pane_or_none()
        self._applying_profile = True
        try:
            if pane is not None:
                pane.apply_config_params(config)
                pane.refresh_context_bar()
        finally:
            self._applying_profile = False
        self.refresh_models()
        if pane is not None:
            pane.mark_next_request_dirty()

    def record_generation_success(
        self, selected_model: str, response_model: str | None
    ) -> None:
        """Record dated response evidence; identity must match when supplied."""
        self._advance_observation()
        state = (
            "succeeded"
            if response_model is not None and response_model == selected_model
            else "failed"
        )
        self.server_identity = replace(
            self.server_identity,
            selected_model=selected_model,
            last_response_model=(
                response_model
                if response_model is not None
                else self.server_identity.last_response_model
            ),
            last_success_at=(
                time.time()
                if state == "succeeded"
                else self.server_identity.last_success_at
            ),
            generation_state=state,
        )

    def record_generation_failure(self, *, cancelled: bool = False) -> None:
        self._advance_observation()
        self.server_identity = replace(
            self.server_identity,
            generation_state="client_cancelled" if cancelled else "failed",
        )

    def effective_model(self) -> str | None:
        """Return the explicit request target, independent of endpoint liveness."""
        return self.server_identity.selected_model

    def endpoint_preview_text(self) -> str:
        """Build a fresh endpoint snapshot; never cached across changes."""
        rendered = f"[{self.host}]" if ":" in self.host else self.host
        base_v1 = f"http://{rendered}:{self.port}/v1"
        chat_raw = f"http://{rendered}:{self.port}/v1/chat/completions"
        try:
            chat_url = parse_loopback_url(chat_raw).url
        except ComparisonValidationError:
            chat_url = None
        selected = self.effective_model()
        identity = self.server_identity
        last_response = identity.last_response_model
        last_success_at = identity.last_success_at
        if last_success_at is not None:
            success_label = (
                datetime.fromtimestamp(last_success_at, tz=UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
        else:
            success_label = "— (never)"
        response_label = last_response if last_response is not None else "—"
        reach_word = {
            "green": "Reachable",
            "amber": "Unexpected",
            "red": "Offline",
            "starting": "Starting",
            "stopping": "Stopping",
            "failed": "Failed",
        }.get(self.status_state, "Unknown")
        lines = [
            "Endpoint — shared serving unqualified",
            f"base URL: {base_v1}",
            f"chat URL: {chat_raw}",
            (
                f"selected request model: {selected}"
                if selected is not None
                else "selected request model: — (select a model first)"
            ),
            f"last response model: {response_label}",
            f"last verified success: {success_label}",
            f"generation state: {identity.generation_state}",
            (
                f"reachability: {reach_word} "
                f"(health {self.status_state}; health only, not generation readiness)"
            ),
            "residency: unknown (health/catalogue never prove the loaded model)",
            (
                "catalogue: "
                f"{identity.catalogue_state} "
                f"{list(identity.available_models)} "
                "(catalogue never implies residency)"
            ),
            (
                "shared serving: blocked — stock mlx_lm.server 74e7cf9 accepts "
                "per-request model/draft_model/adapters and loads a changed tuple; "
                "wrong-target rejection and lifecycle drain are not enforceable. "
                "Qualification only."
            ),
            "shutdown (managed): stops its owned child",
            "shutdown (attach): never stops the operator server",
            f"current runtime mode: {self.config.runtime_mode}",
        ]
        if (
            selected is not None
            and last_response is not None
            and selected != last_response
            and last_success_at is not None
        ):
            lines.append(
                "note: mismatched response is not the verified success; "
                "the timestamp above is an older success, not this response time."
            )
        if selected is None:
            lines.append("select a model first — runnable examples omitted.")
        elif chat_url is None:
            lines.append(
                "unsupported scope: non-loopback endpoint — "
                "local-client recipes omitted (loopback only)."
            )
        else:
            payload = {
                "model": selected,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 32,
                "stream": False,
            }
            curl_argv = [
                "curl",
                "-sS",
                "--max-time",
                "30",
                chat_url,
                "-H",
                "Content-Type: application/json",
                "-d",
                json.dumps(payload),
            ]
            opencode_config = {
                "provider": {
                    "mlx-tui-local": {
                        "npm": "@ai-sdk/openai-compatible",
                        "options": {"baseURL": base_v1},
                        "models": {"local": {"name": selected}},
                    }
                }
            }
            lines.extend(
                [
                    "unqualified examples — no retained evidence; "
                    "copying does not execute requests or write settings.",
                    f"curl: {shlex.join(curl_argv)}",
                    f"opencode config: {json.dumps(opencode_config, indent=2)}",
                ]
            )
        return "\n".join(lines)

    def action_endpoint_info(self) -> None:
        """Show a fresh endpoint snapshot for inspection and exact copy."""
        self.push_screen(
            TextPreviewScreen(
                self.endpoint_preview_text(),
                title="Endpoint — shared serving unqualified",
            )
        )

    def refresh_models(self) -> None:
        """Update runtime/loaded markers via the pane; a missing pane is fine."""
        try:
            self.query_one(ModelsPane).refresh_markers()
        except NoMatches:
            return

    def _apply_preset(self, preset: Preset) -> None:
        try:
            pane = self.query_one(ChatPane)
            params = pane.query_one(ParamsPane)
        except NoMatches:
            return
        params.apply_values(preset.temperature, preset.top_p, preset.max_tokens)
        # sync config snapshot
        temp, top_p, max_tok = params.read_values()
        self.config = replace(
            self.config,
            temperature=temp,
            top_p=top_p,
            max_tokens=max_tok,
            system=preset.system.strip() or None,
        )
        self.mark_active_profile_modified()
        params.apply_config(self.config)
        self.log_app(f"preset: {preset.name}", "dim")
        pane.mark_next_request_dirty()

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
        rss_gib: float | None,
        snapshot: MemorySnapshot | None = None,
        ownership_state: str | None = None,
    ) -> None:
        if snapshot is None:
            snapshot = memory_snapshot()
        display_state = self.status_state
        if self.config.runtime_mode == "managed":
            if self.managed_runtime is not None and self.managed_runtime.shutting_down:
                display_state = "stopping"
            elif self.operations.current is OperationKind.RESTARTING:
                display_state = "starting"
            elif ownership_state == "owned child exited":
                display_state = "failed"
        try:
            self.query_one(StatusBar).show_status(
                state=display_state,
                selected_model=self.server_identity.selected_model,
                last_response_model=self.server_identity.last_response_model,
                generation_state=self.server_identity.generation_state,
                catalogue_disagrees=(
                    self.server_identity.catalogue_state == "green"
                    and self.server_identity.selected_model is not None
                    and self.server_identity.selected_model
                    not in self.server_identity.available_models
                ),
                port=self.port,
                can_start=bool(self.config.start_cmd),
                rss_gib=rss_gib,
                snapshot=snapshot,
                runtime_mode=self.config.runtime_mode,
                ownership_state=ownership_state,
            )
        except NoMatches:
            pass

    def set_operation_ui(self, busy: bool) -> None:
        """Disable or restore controls for a model lifecycle operation."""
        from mlx_tui.chat_pane import ChatInput  # noqa: PLC0415

        self.refresh_activity()
        try:
            self.query_one("#models-table", ModelsTable).disabled = busy
            self.query_one("#chat-input", ChatInput).disabled = (
                busy or self.operations.is_busy
            )
            try:
                self.query_one("#btn-send", Button).disabled = (
                    busy or self.operations.is_busy
                )
            except NoMatches:
                pass
            if not busy:
                self.query_one("#swap-progress", Static).update("")
        except NoMatches:
            pass
        try:
            self.query_one(ComparePane).set_comparison_busy()
        except NoMatches:
            pass

    async def action_cold_start(self) -> None:  # noqa: PLR0911
        """Check the endpoint, then own a complete cold-start worker lifetime."""
        if self.config.runtime_mode == "managed":
            self._action_managed_start()
            return
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
            proc_ident = process.find_server_process(self.host, self.port)
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
            if self.config.model is not None:
                self.select_model(self.config.model)
            pane.run_boot(
                BootPlan(
                    model_id=self.config.model,
                    size_on_disk=pane.row_size(self.config.model),
                    stop_first=False,
                    success_line="✓ generation verified; residency unknown",
                )
            )
            worker_started = True
        finally:
            if not worker_started:
                self.operations.release(OperationKind.RESTARTING)
                self.set_operation_ui(False)

    def ensure_managed_runtime(self) -> ManagedRuntime:
        if self.managed_runtime is None or self.managed_runtime.closed:
            self.managed_runtime = ManagedRuntime(host=self.host, port=self.port)
        return self.managed_runtime

    def set_runtime_mode(self, mode: str) -> None:
        if mode not in ("attach", "managed"):
            mode = "attach"
        self.config = replace(self.config, runtime_mode=mode)  # type: ignore[arg-type]
        if mode == "managed":
            self.ensure_managed_runtime()

    def _managed_target(self) -> object:
        model = self.config.model
        if not model:
            return None
        path = Path(model).expanduser()
        if path.is_dir():
            return path
        for entry in self.profile_entries:
            if entry.profile.repo_id == model:
                try:
                    return resolve_cached_snapshot(model, entry.profile.revision)
                except (OSError, ValueError):
                    return None
        return None

    def _action_managed_start(self) -> None:
        target = self._managed_target()
        if not isinstance(target, Path):
            self.log_app(
                "managed setup needs a verified cached model; open Setup to install/download it",
                "yellow",
            )
            return
        try:
            pane = self.query_one(ModelsPane)
        except NoMatches:
            return
        if self.operations.current is OperationKind.CHATTING:
            self.cancel_chat_for_swap()
            return
        if not self.operations.try_acquire(OperationKind.RESTARTING):
            self.log_app("operation already in progress", "yellow")
            return
        self.select_model(str(target))
        self.set_operation_ui(True)
        pane.run_managed_boot(target, str(target))

    def start_managed(
        self, model: Path, *, on_line: Callable[[str], None]
    ) -> ServerProbe:
        return self.ensure_managed_runtime().start(model, on_line=on_line)

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
            if self.config.model is not None:
                self.select_model(self.config.model)
            pane.run_boot(
                BootPlan(
                    model_id=self.config.model,
                    size_on_disk=pane.row_size(self.config.model),
                    stop_first=True,
                    success_line="✓ generation verified; residency unknown",
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
        if self.operations.current is OperationKind.COMPARING:
            try:
                self.query_one(ComparePane).abort()
            except NoMatches:
                pass
            return
        pane = self._chat_pane_or_none()
        if pane is not None:
            pane.abort()

    def log_app(self, message: str, style: str | None = None) -> None:
        text = Text(message) if style is None else Text(message, style=style)
        self.query_one("#app-log", RichLog).write(text)
        self._latest_event = message
        activity = self.query_one("#activity", Collapsible)
        if activity.collapsed and style in ("red", "yellow"):
            label = "Last error" if style == "red" else "Last warning"
            self._unseen_notice = f"{label}: {message}"
        self.refresh_activity()

    @override
    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "cancel_chat":
            if self.operations.current is OperationKind.COMPARING:
                try:
                    pane = self.query_one(ComparePane)
                except NoMatches:
                    return False
                return pane.has_live_comparison and not pane.cancel_requested
            pane = self._chat_pane_or_none()
            return (
                len(self.screen_stack) == 1
                and pane is not None
                and pane.has_live_turn
                and not pane._cancel_requested
            )
        return True

    def action_toggle_activity(self) -> None:
        activity = self.query_one("#activity", Collapsible)
        activity.collapsed = not activity.collapsed

    @on(Collapsible.Toggled, "#activity")
    def _activity_toggled(self, event: Collapsible.Toggled) -> None:
        activity = event.collapsible
        if not activity.collapsed:
            self._unseen_notice = None
        elif self.focused is activity.query_one(RichLog):
            activity.query_one("CollapsibleTitle").focus()
        activity.query_one(RichLog).can_focus = not activity.collapsed
        self.refresh_activity()

    def refresh_activity(self) -> None:
        """Present existing operation/log state without owning its lifetime."""
        if not self.is_mounted:
            return
        try:
            activity = self.query_one("#activity", Collapsible)
        except NoMatches:
            return
        kind = self.operations.current
        title = "Activity" if kind is OperationKind.IDLE else f"{kind.value} · Activity"
        event = self._unseen_notice or self._latest_event
        if event:
            title += " · " + " ".join(event.split())
        summary = Text(title)
        summary.truncate(max(1, self.size.width - 4), overflow="ellipsis")
        activity.title = summary.plain
        self.refresh_bindings()
        try:
            self.query_one(ComparePane)._update_controls()
        except NoMatches:
            pass

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
        before = (self.config.host, self.config.port)
        editor = os.environ.get("EDITOR", "vi")
        try:
            editor_argv = shlex.split(editor)
        except ValueError as exc:
            self.log_app(
                f"config edit failed: {exc.__class__.__name__}: {exc}"[:200], "red"
            )
            return
        if not editor_argv:
            self.log_app("config edit failed: EDITOR is empty", "red")
            return
        try:
            if not path.exists():
                write_template(path)
            with self.suspend():
                proc = subprocess.run([*editor_argv, str(path)], check=False)
        except OSError as exc:
            self.log_app(
                f"config edit failed: {exc.__class__.__name__}: {exc}"[:200], "red"
            )
            return
        if proc.returncode != 0:
            self.log_app(f"config edit failed: editor exited {proc.returncode}", "red")
            return
        try:
            new_config = parse_config(path)
        except ConfigParseError as exc:
            # A typo must not silently wipe the session's commands/model;
            # keep the previous config instead of adopting all-defaults.
            self.log_app(f"config kept — parse failed: {exc}", "red")
            return
        self.config = new_config
        self.mark_active_profile_modified()
        if new_config.model is not None and new_config.model != self.effective_model():
            self.select_model(new_config.model)
        self.log_app("config reloaded")
        try:
            pane = self.query_one(ChatPane)
            pane.apply_config_params(self.config)
            pane.refresh_context_bar()
            pane.mark_next_request_dirty()
        except NoMatches:
            pass
        self.presets = load_presets()
        self.preset_idx = -1
        if (self.config.host, self.config.port) != before:
            self.log_app("restart mlx-tui to apply host/port", "yellow")


def main() -> None:
    parser = argparse.ArgumentParser(prog="mlx-tui")
    parser.add_argument(
        "--version", action="version", version=package_version("mlx-tui")
    )
    parser.add_argument("--diagnostics", metavar="PATH")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", default=None, type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--managed", action="store_true", help="use the app-owned runtime"
    )
    mode.add_argument(
        "--attach", action="store_true", help="use an operator-managed server"
    )
    args = parser.parse_args()
    if args.diagnostics is not None:
        if (
            args.host is not None
            or args.port is not None
            or args.managed
            or args.attach
        ):
            parser.error("--diagnostics cannot be combined with endpoint or mode flags")
        try:
            write_diagnostics(Path(args.diagnostics))
        except DiagnosticsError as exc:
            parser.error(str(exc))
        return
    path = config_path()
    config_error: str | None = None
    cfg = load_config()
    if path.exists():
        try:
            parse_config(path)
        except ConfigParseError as exc:
            config_error = str(exc)
    if args.managed:
        cfg = replace(cfg, runtime_mode="managed")
    elif args.attach:
        cfg = replace(cfg, runtime_mode="attach")
    # CLI flags win only when explicitly passed; otherwise the config file's
    # values apply (which already carry the hardcoded defaults).
    host = args.host if args.host is not None else cfg.host
    port = args.port if args.port is not None else cfg.port
    if cfg.runtime_mode == "managed" and args.host is None and args.port is None:
        host, port = "127.0.0.1", 18080
    app = MlxTuiApp(
        host=host,
        port=port,
        config=cfg,
        needs_setup=not path.exists() and not (args.managed or args.attach),
        config_error=config_error,
    )
    old_handlers = {
        sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)
    }

    def request_shutdown(_signum: int, _frame: object) -> None:
        app.exit()

    try:
        for sig in old_handlers:
            signal.signal(sig, request_shutdown)
        app.run()
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        if app.managed_runtime is not None:
            app.managed_runtime.close()
