"""Keyboard-first managed/attach setup entry point."""

from __future__ import annotations

import asyncio
import platform
import shutil
import threading
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, override

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TabbedContent

from mlx_tui.config import config_path, create_config
from mlx_tui.managed import COMPLETION_MARKER, install_runtime, runtime_root
from mlx_tui.models import resolve_cached_snapshot, verify_cached_assets
from mlx_tui.operations import OperationKind
from mlx_tui.search_screen import SearchScreen

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


class SetupScreen(ModalScreen[None]):
    """The smallest setup flow that can reach the existing Compare tab."""

    BINDINGS = [("escape", "close_screen", "Close")]

    DEFAULT_CSS = """
    SetupScreen { align: center middle; }
    #setup-box {
        width: 92%; max-height: 90%; padding: 1 2;
        border: round $primary; background: $surface;
    }
    #setup-box Static { height: auto; margin-bottom: 1; }
    #setup-box .row { height: auto; }
    #setup-box Button { width: 1fr; margin-right: 1; }
    #setup-box Button:last-child { margin-right: 0; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cancel_event: threading.Event | None = None
        self._install_done = threading.Event()
        self._install_done.set()
        self._installing = False

    @property
    def tui(self) -> MlxTuiApp:
        return cast("MlxTuiApp", self.app)

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="setup-box"):
            yield Static("mlx-tui setup", classes="heading")
            yield Static(
                "Choose how this app may reach the local MLX server.", id="setup-mode"
            )
            yield Static("", id="setup-hardware")
            yield Static("", id="setup-candidates")
            with Horizontal(classes="row"):
                yield Button("Context budget: unknown", id="setup-context")
                yield Button("Memory priority: unknown", id="setup-memory")
            with Horizontal(classes="row"):
                yield Button("Managed runtime", id="setup-managed")
                yield Button("Attach server", id="setup-attach")
            with Horizontal(classes="row"):
                yield Button("Install / repair runtime", id="setup-install")
                yield Button("Download candidates", id="setup-download")
            with Horizontal(classes="row"):
                yield Button("Start", id="setup-start")
                yield Button("Retry", id="setup-retry")
                yield Button("Reload previous model", id="setup-reload")
                yield Button("Open Compare", id="setup-compare")
            yield Static("", id="setup-status")

    def on_mount(self) -> None:
        self._update_view()
        self.query_one("#setup-managed", Button).focus()

    def _set_status(self, message: str, style: str | None = None) -> None:
        try:
            self.query_one("#setup-status", Static).update(
                Text(message) if style is None else Text(message, style=style)
            )
        except NoMatches:
            pass

    def _cached_snapshot(self, entry: object) -> Path | None:
        profile = getattr(entry, "profile", None)
        if profile is None:
            return None
        try:
            snapshot = resolve_cached_snapshot(profile.repo_id, profile.revision)
            verify_cached_assets(snapshot, profile.template_assets)
        except (OSError, ValueError):
            return None
        return snapshot

    def _update_view(self) -> None:
        mode = self.tui.config.runtime_mode
        root = runtime_root()
        runtime = (
            "installation recorded; verification required"
            if (root / COMPLETION_MARKER).is_file()
            else "not installed"
        )
        hardware = (
            f"hardware: {platform.system()} {platform.machine()} · "
            f"tested tier: local-m4-16gib only · free disk: "
            f"{shutil.disk_usage(root.parent if root.parent.exists() else Path.home()).free / 2**30:.1f} GiB"
        )
        candidates: list[str] = []
        for entry in self.tui.profile_entries[:2]:
            profile = entry.profile
            cached = self._cached_snapshot(entry)
            candidates.append(
                f"{profile.name} · rev {profile.revision[:12]} · "
                f"{'verified cache' if cached else 'download needed'} · "
                f"evidence {entry.status('local-m4-16gib')}"
            )
        details = (
            "mode: "
            + ("Managed (owned child stops on quit)" if mode == "managed" else "Attach")
            + "\n"
            + hardware
            + f"\nruntime: {runtime} · context budget: 8192 · output budget: 32"
            + "\n"
            + "\n".join(candidates or ["no pinned candidates available"])
            + "\nMemory fit is unknown; larger-context advice is untested."
        )
        self.query_one("#setup-mode", Static).update(details)
        self.query_one("#setup-hardware", Static).update(
            "Installation time and model-download time are recorded separately."
        )
        self.query_one("#setup-candidates", Static).update(
            "Choose a mode, then install the runtime and download the two pinned candidates."
        )
        self.query_one(
            "#setup-context", Button
        ).label = f"Context budget: {self.tui.setup_preferences['context_budget']}"
        self.query_one(
            "#setup-memory", Button
        ).label = f"Memory priority: {self.tui.setup_preferences['memory_priority']}"
        busy = self.tui.operations.is_busy or self._installing
        for selector in (
            "#setup-context",
            "#setup-memory",
            "#setup-managed",
            "#setup-attach",
            "#setup-install",
            "#setup-download",
            "#setup-start",
            "#setup-retry",
            "#setup-reload",
            "#setup-compare",
        ):
            try:
                self.query_one(selector, Button).disabled = busy and selector not in {
                    "#setup-retry"
                }
            except NoMatches:
                pass

    def _toggle_preference(self, key: str, values: tuple[str, ...], label: str) -> None:
        current = self.tui.setup_preferences.get(key, values[0])
        next_value = values[(values.index(current) + 1) % len(values)]
        self.tui.setup_preferences[key] = next_value
        self._set_status(f"{label}: {next_value} (advisory only)")
        self._update_view()

    @on(Button.Pressed, "#setup-context")
    def _context_preference(self) -> None:
        self._toggle_preference(
            "context_budget",
            ("unknown", "meets intended work", "needs more"),
            "Context",
        )

    @on(Button.Pressed, "#setup-memory")
    def _memory_preference(self) -> None:
        self._toggle_preference(
            "memory_priority", ("unknown", "priority", "not a priority"), "Memory"
        )

    def _persist_new_mode(self) -> None:
        if not config_path().exists():
            create_config(
                config_path(),
                runtime_mode=self.tui.config.runtime_mode,
                host=self.tui.host,
                port=self.tui.port,
            )
        self.tui.needs_setup = False

    def _select_mode(self, mode: str, note: str) -> None:
        self.tui.set_runtime_mode(mode)  # type: ignore[arg-type]
        self._persist_new_mode()
        self._set_status(note)
        self._update_view()

    @on(Button.Pressed, "#setup-managed")
    def _select_managed(self) -> None:
        self._select_mode(
            "managed", "Managed selected; the TUI owns only children it starts."
        )

    @on(Button.Pressed, "#setup-attach")
    def _select_attach(self) -> None:
        self._select_mode(
            "attach",
            "Attach selected; existing server commands remain operator-managed.",
        )

    @on(Button.Pressed, "#setup-install, #setup-retry")
    def _install(self) -> None:
        if self.tui.config.runtime_mode != "managed":
            self._set_status("Choose Managed runtime first.", "yellow")
            return
        if self._installing:
            return
        if not self.tui.operations.try_acquire(OperationKind.INSTALLING):
            self._set_status("another operation is already running", "yellow")
            return
        self._installing = True
        self._cancel_event = threading.Event()
        self._install_done.clear()
        self._set_status("installing runtime…")
        self._run_install(self._cancel_event)

    @work(exclusive=True, group="managed-install", thread=True)
    def _run_install(self, cancel_event: threading.Event) -> None:
        try:
            try:
                install_runtime(
                    on_line=lambda line: self.app.call_from_thread(
                        self._set_status, line
                    ),
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                self.app.call_from_thread(
                    self._finish_install, f"runtime failed: {exc}", "red"
                )
            else:
                self.app.call_from_thread(
                    self._finish_install, "runtime installation verified", None
                )
        finally:
            self._install_done.set()

    def _finish_install(self, message: str, style: str | None) -> None:
        self._installing = False
        self._cancel_event = None
        self.tui.operations.release(OperationKind.INSTALLING)
        if self.is_mounted:
            self._set_status(message, style)
            self._update_view()

    async def cancel_install_and_wait(self) -> None:
        """Cancel the installer and wait until its process group is reaped."""
        if not self._installing:
            return
        if self._cancel_event is not None:
            self._cancel_event.set()
        await asyncio.to_thread(self._install_done.wait)
        self._installing = False
        self._cancel_event = None
        self.tui.operations.release(OperationKind.INSTALLING)

    @on(Button.Pressed, "#setup-download")
    def _download(self) -> None:
        candidates = tuple(
            (entry.profile.repo_id, entry.profile.revision)
            for entry in self.tui.profile_entries[:2]
        )
        self.app.push_screen(SearchScreen(candidates))

    @on(Button.Pressed, "#setup-start")
    async def _start(self) -> None:
        self.tui.set_runtime_mode("managed")
        await self.tui.action_cold_start()
        self._set_status("managed start requested")
        self._update_view()

    @on(Button.Pressed, "#setup-reload")
    async def _reload(self) -> None:
        manager = self.tui.managed_runtime
        if manager is None or manager.previous_model is None:
            self._set_status("no verified previous model is available", "yellow")
            return
        self.tui.config = replace(
            self.tui.config, model=str(manager.previous_model), runtime_mode="managed"
        )
        await self.tui.action_cold_start()

    @on(Button.Pressed, "#setup-compare")
    def _compare(self) -> None:
        self.dismiss(None)
        self.tui.query_one(TabbedContent).active = "compare"

    def action_close_screen(self) -> None:
        if self._installing:
            if self._cancel_event is not None:
                self._cancel_event.set()
            self._set_status(
                "cancellation requested; waiting for installer cleanup", "yellow"
            )
            return
        self.dismiss(None)

    async def on_unmount(self) -> None:
        await self.cancel_install_and_wait()
