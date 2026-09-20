"""App operation/managed-runtime helpers (app-parameterized)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from textual.css.query import NoMatches
from textual.widgets import Button, Static

from mlx_tui import process
from mlx_tui.managed.runtime import ManagedRuntime
from mlx_tui.models import resolve_cached_snapshot
from mlx_tui.models_pane import ModelsPane
from mlx_tui.operations import OperationKind
from mlx_tui.status import ServerProbe
from mlx_tui.swap import BootPlan
from mlx_tui.table import ModelsTable


def set_operation_ui(app: Any, busy: bool) -> None:
    """Disable or restore controls for a model lifecycle operation."""
    from mlx_tui.chat_ui.widgets import ChatInput  # noqa: PLC0415

    app.refresh_activity()
    try:
        app.query_one("#models-table", ModelsTable).disabled = busy
        app.query_one("#chat-input", ChatInput).disabled = (
            busy or app.operations.is_busy
        )
        try:
            app.query_one("#btn-send", Button).disabled = busy or app.operations.is_busy
        except NoMatches:
            pass
        if not busy:
            app.query_one("#swap-progress", Static).update("")
    except NoMatches:
        pass
    try:
        from mlx_tui.compare.pane import ComparePane  # noqa: PLC0415

        app.query_one(ComparePane).set_comparison_busy()
    except NoMatches:
        pass


async def action_cold_start(app: Any) -> None:  # noqa: PLR0911
    """Check the endpoint, then own a complete cold-start worker lifetime."""
    if app.config.runtime_mode == "managed":
        app._action_managed_start()
        return
    if not app.config.start_cmd:
        app.log_app("set start_cmd in the config to enable cold start", "yellow")
        return
    if app.operations.current is OperationKind.CHATTING:
        app.cancel_chat_for_swap()
        return
    if not app.operations.try_acquire(OperationKind.RESTARTING):
        app.log_app("operation already in progress", "yellow")
        return

    worker_started = False
    try:
        # The 2s poll leaves startup windows where status_state still carries
        # its initial red against an already-running server.
        live_state = await app._classify_liveness()
        if live_state == "green":
            app.status_state = live_state
            app.log_app("server is already up", "dim")
            return

        # A process may exist without answering health yet. Restart it through
        # configured commands instead of racing a second instance onto the port.
        proc_ident = process.find_server_process(app.host, app.port)
        if proc_ident is not None:
            worker_started = app._restart_config_model(proc_ident.pid)
            return
        if live_state == "amber":
            app.log_app(
                "something else is answering on this port — not starting the server",
                "yellow",
            )
            return
        try:
            pane = app.query_one(ModelsPane)
        except NoMatches:
            return
        app.set_operation_ui(True)
        if app.config.model is not None:
            app.select_model(app.config.model)
        pane.run_boot(
            BootPlan(
                model_id=app.config.model,
                size_on_disk=pane.row_size(app.config.model),
                stop_first=False,
                success_line="✓ generation verified; residency unknown",
            )
        )
        worker_started = True
    finally:
        if not worker_started:
            app.operations.release(OperationKind.RESTARTING)
            app.set_operation_ui(False)


def ensure_managed_runtime(app: Any) -> ManagedRuntime:
    if app.managed_runtime is None or app.managed_runtime.closed:
        app.managed_runtime = ManagedRuntime(host=app.host, port=app.port)
    return app.managed_runtime


def reassess_models(app: Any) -> None:
    """Recompute model assessments after an explicit runtime change."""
    try:
        pane = app.query_one(ModelsPane)
        pane.reassess(context=pane.applied_context)
    except NoMatches:
        return


def set_runtime_mode(app: Any, mode: str) -> None:
    if mode not in ("attach", "managed"):
        mode = "attach"
    from dataclasses import replace  # noqa: PLC0415

    app.config = replace(app.config, runtime_mode=mode)  # type: ignore[arg-type]
    if mode == "managed":
        app.ensure_managed_runtime()
    reassess_models(app)


def _managed_target(app: Any) -> object:
    model = app.config.model
    if not model:
        return None
    path = Path(model).expanduser()
    if path.is_dir():
        return path
    for entry in app.profile_entries:
        if entry.profile.repo_id == model:
            try:
                return resolve_cached_snapshot(model, entry.profile.revision)
            except (OSError, ValueError):
                return None
    return None


def _action_managed_start(app: Any) -> None:
    target = app._managed_target()
    if not isinstance(target, Path):
        app.log_app(
            "managed setup needs a verified cached model; open Setup to install/download it",
            "yellow",
        )
        return
    try:
        pane = app.query_one(ModelsPane)
    except NoMatches:
        return
    if app.operations.current is OperationKind.CHATTING:
        app.cancel_chat_for_swap()
        return
    if not app.operations.try_acquire(OperationKind.RESTARTING):
        app.log_app("operation already in progress", "yellow")
        return
    app.select_model(str(target))
    app.set_operation_ui(True)
    pane.run_managed_boot(target, str(target))


def start_managed(
    app: Any, model: Path, *, on_line: Callable[[str], None]
) -> ServerProbe:
    return app.ensure_managed_runtime().start(model, on_line=on_line)


def _restart_config_model(app: Any, pid: int) -> bool:
    """Launch the restart worker for an existing unhealthy server process."""
    try:
        pane = app.query_one(ModelsPane)
    except NoMatches:
        return False
    if app.config.swap_policy == "warm":
        app.log_app(
            'cannot restart: swap_policy is "warm" (requires green, no restarts)',
            "red",
        )
        return False
    if not (app.config.start_cmd and app.config.stop_cmd):
        app.log_app(
            f"a server process (pid {pid}) is running but not healthy — "
            "set stop_cmd/start_cmd to let mlx-tui restart it",
            "red",
        )
        return False
    if app.operations.current is OperationKind.CHATTING:
        app.cancel_chat_for_swap()
        return False
    acquired_here = False
    if app.operations.current is not OperationKind.RESTARTING:
        if not app.operations.try_acquire(OperationKind.RESTARTING):
            app.log_app("operation already in progress", "yellow")
            return False
        acquired_here = True
    try:
        app.set_operation_ui(True)
        if app.config.model is not None:
            app.select_model(app.config.model)
        pane.run_boot(
            BootPlan(
                model_id=app.config.model,
                size_on_disk=pane.row_size(app.config.model),
                stop_first=True,
                success_line="✓ generation verified; residency unknown",
            )
        )
    except Exception:
        if acquired_here:
            app.operations.release(OperationKind.RESTARTING)
            app.set_operation_ui(False)
        raise
    return True


def _chat_pane_or_none(app: Any) -> Any | None:
    from mlx_tui.chat_ui.pane import ChatPane  # noqa: PLC0415

    try:
        return app.query_one(ChatPane)
    except NoMatches:
        return None


def cancel_chat_for_swap(app: Any) -> None:
    pane = app._chat_pane_or_none()
    if pane is not None:
        pane.abort()
        app.log_app("cancellation requested — retry after cleanup", "yellow")


def action_cancel_chat(app: Any) -> None:
    try:
        from mlx_tui.discover_pane import DiscoverPane  # noqa: PLC0415

        discover = app.query_one(DiscoverPane)
    except NoMatches:
        discover = None
    if discover is not None and (discover.visible or discover._downloading is not None):
        discover.request_close()
        return
    if app.operations.current is OperationKind.COMPARING:
        try:
            from mlx_tui.compare.pane import ComparePane  # noqa: PLC0415

            app.query_one(ComparePane).abort()
        except NoMatches:
            pass
        return
    pane = app._chat_pane_or_none()
    if pane is not None:
        pane.abort()
