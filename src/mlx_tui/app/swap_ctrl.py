"""Cold-start / restart orchestration + swap UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.css.query import NoMatches
from textual.widgets import Input, Static

from mlx_tui import process
from mlx_tui.models_pane import ModelsPane
from mlx_tui.swap import BootPlan, SwapState
from mlx_tui.table import ModelsTable

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


def set_swap_ui(app: MlxTuiApp, busy: bool) -> None:
    """Disable/restore table+input around a swap; False clears progress."""
    try:
        app.query_one("#models-table", ModelsTable).disabled = busy
        # A warm swap finishing under a live chat turn must not hand the
        # input back mid-turn: the turn owns it until end_turn.
        live_turn = app.chat_has_live_turn()
        app.query_one("#chat-input", Input).disabled = busy or live_turn
        if not busy:
            app.query_one("#swap-progress", Static).update("")
    except NoMatches:
        return


async def cold_start(app: MlxTuiApp) -> None:
    # Same busy guard as the pane's load action: a second ctrl+s inside a
    # running boot would raise InvalidTransition (starting -> starting)
    # and crash the app. The in-flight flag covers the window before the
    # machine leaves IDLE (the action awaits below).
    if app._cold_start_in_flight or app.swap_busy:
        app.log_app("swap already in progress", "yellow")
        return
    if not app.config.start_cmd:
        app.log_app("set start_cmd in the config to enable cold start", "yellow")
        return
    app._cold_start_in_flight = True
    try:
        # The 2s poll leaves startup windows where status_state still
        # carries its initial "red" against an already-running server;
        # decide on a fresh classification, not the last render.
        state = await app._classify_liveness()
        if state == "green":
            app.status_state = state
            app.log_app("server is already up", "dim")
            return
        # A server process may exist without answering health yet (still
        # loading, or wedged): blind-spawning a second instance cannot
        # bind the port and dies with an OSError traceback. Restart it
        # through the configured commands instead.
        pid = process.find_server_pid(app.config.pidfile)
        if pid is not None:
            restart_config_model(app, pid)
            return
        if state == "amber":
            app.log_app(
                "something else is answering on this port — not starting the server",
                "yellow",
            )
            return
        try:
            pane = app.query_one(ModelsPane)
        except NoMatches:
            return
        app.swap_machine.transition(SwapState.STARTING)
        set_swap_ui(app, True)
        pane.run_boot(
            BootPlan(
                model_id=app.config.model,
                size_on_disk=pane.row_size(app.config.model),
                stop_first=False,
                success_line="✓ server is up",
            )
        )
    finally:
        app._cold_start_in_flight = False


def restart_config_model(app: MlxTuiApp, pid: int) -> None:
    """Restart path for ctrl+s over an existing-but-unhealthy process."""
    try:
        pane = app.query_one(ModelsPane)
    except NoMatches:
        return
    if not (app.config.start_cmd and app.config.stop_cmd):
        app.log_app(
            f"a server process (pid {pid}) is running but not healthy — "
            "set stop_cmd/start_cmd to let mlx-tui restart it",
            "red",
        )
        return
    if app.chat_has_live_turn():
        app.cancel_chat_for_swap()
    app.swap_machine.transition(SwapState.STOPPING)
    set_swap_ui(app, True)
    pane.run_boot(
        BootPlan(
            model_id=app.config.model,
            size_on_disk=pane.row_size(app.config.model),
            stop_first=True,
            success_line="✓ server is up",
        )
    )
