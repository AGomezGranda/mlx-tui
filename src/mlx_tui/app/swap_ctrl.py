"""Cold-start / restart orchestration + operation UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.css.query import NoMatches
from textual.widgets import Input, Static

from mlx_tui import process
from mlx_tui.app.operations import OperationKind
from mlx_tui.models_pane import ModelsPane
from mlx_tui.swap import BootPlan
from mlx_tui.table import ModelsTable

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


def set_operation_ui(app: MlxTuiApp, busy: bool) -> None:
    """Disable or restore controls for a model lifecycle operation."""
    try:
        app.query_one("#models-table", ModelsTable).disabled = busy
        app.query_one("#chat-input", Input).disabled = (
            busy or app.operations.current is OperationKind.CHATTING
        )
        if not busy:
            app.query_one("#swap-progress", Static).update("")
    except NoMatches:
        return


async def cold_start(app: MlxTuiApp) -> None:  # noqa: PLR0911
    """Check the endpoint, then own a complete cold-start worker lifetime."""
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
        proc_ident = process.find_server_process(app.host, app.port, app.config.pidfile)
        if proc_ident is not None:
            worker_started = restart_config_model(app, proc_ident.pid)
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
        set_operation_ui(app, True)
        pane.run_boot(
            BootPlan(
                model_id=app.config.model,
                size_on_disk=pane.row_size(app.config.model),
                stop_first=False,
                success_line="✓ server is up",
            )
        )
        worker_started = True
    finally:
        if not worker_started:
            app.operations.release(OperationKind.RESTARTING)
            set_operation_ui(app, False)


def restart_config_model(app: MlxTuiApp, pid: int) -> bool:
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
        set_operation_ui(app, True)
        pane.run_boot(
            BootPlan(
                model_id=app.config.model,
                size_on_disk=pane.row_size(app.config.model),
                stop_first=True,
                success_line="✓ server is up",
            )
        )
    except Exception:
        if acquired_here:
            app.operations.release(OperationKind.RESTARTING)
            set_operation_ui(app, False)
        raise
    return True
