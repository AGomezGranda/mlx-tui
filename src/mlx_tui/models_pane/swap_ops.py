"""Swap orchestration — warm vs boot."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mlx_tui import serverctl
from mlx_tui.models import ModelRow
from mlx_tui.serverctl import build_start_command
from mlx_tui.swap import BootPlan, SwapState, health_timeout
from mlx_tui.table import ModelsTable

if TYPE_CHECKING:
    from mlx_tui.models_pane import ModelsPane


def request_load_swap(pane: ModelsPane) -> None:
    if pane.tui.swap_busy:
        pane.tui.log_app("swap already in progress", "yellow")
        return
    table = pane.query_one("#models-table", ModelsTable)
    if not pane.rows or not 0 <= table.cursor_row < len(pane.rows):
        pane.tui.log_app("no model selected to load", "dim")
        return
    row = pane.rows[table.cursor_row]
    if pane.tui.status_state == "green":
        # Warm path: an in-server probe-load owns the wait.
        pane.tui.swap_machine.transition(SwapState.WAITING_HEALTH)
        pane.tui.set_swap_ui(True)
        pane.tui.log_app(f"loading {row.repo_id} (in-server load)…")
        pane.run_warm_swap(row)
    elif pane.tui.config.start_cmd and pane.tui.config.stop_cmd:
        # Restart path: stop/start commands own the wait.
        if pane.tui.chat_has_live_turn():
            pane.tui.cancel_chat_for_swap()
        pane.tui.swap_machine.transition(SwapState.STOPPING)
        pane.tui.set_swap_ui(True)
        pane.run_boot(boot_plan_for(row))
    else:
        pane.tui.log_app(
            f"cannot load {row.repo_id}: server unreachable "
            "and no start_cmd configured",
            "red",
        )


def boot_plan_for(row: ModelRow) -> BootPlan:
    return BootPlan(
        model_id=row.repo_id,
        size_on_disk=row.size_on_disk,
        stop_first=True,
        success_line=f"✓ {row.repo_id} is serving",
    )


def run_warm_swap_impl(pane: ModelsPane, row: ModelRow) -> None:
    timeout = health_timeout(row.size_on_disk)
    try:
        serverctl.warm_load(
            f"http://{pane.tui.host}:{pane.tui.port}/v1/chat/completions",
            row.repo_id,
            timeout_s=timeout,
        )
    except Exception as exc:
        pane.tui.swap_machine.reset()
        detail = f"{exc.__class__.__name__}: {exc}"[:200]
        pane.tui.call_from_thread(pane.tui.log_app, f"load failed: {detail}", "red")
        pane.tui.call_from_thread(pane.tui.set_swap_ui, False)
        return
    pane.tui.call_from_thread(pane.tui.set_tracked_model, row.repo_id)
    pane.tui.swap_machine.transition(SwapState.IDLE)
    pane.tui.call_from_thread(pane.tui.log_app, f"✓ {row.repo_id} loaded")
    pane.tui.call_from_thread(pane.tui.refresh_models)
    pane.tui.call_from_thread(pane.tui._refresh_metrics)
    pane.tui.call_from_thread(pane.tui.set_swap_ui, False)


def fail_swap(pane: ModelsPane, message: str | None) -> None:
    """FAILED→IDLE reset, optional red log line, then UI release."""
    pane.tui.swap_machine.reset()
    if message is not None:
        pane.tui.call_from_thread(pane.tui.log_app, message, "red")
    pane.tui.call_from_thread(pane.tui.set_swap_ui, False)


def run_boot_impl(pane: ModelsPane, plan: BootPlan) -> None:
    def stream(line: str) -> None:
        pane.tui.call_from_thread(pane.tui.log_app, f"[swap] {line}")

    if plan.stop_first:
        if pane.tui.config.start_cmd is None or pane.tui.config.stop_cmd is None:
            # Config rewritten mid-swap; bail honestly.
            fail_swap(pane, "[swap] commands vanished from config")
            return
        # Marker hygiene: clear tracked state the moment stop fires.
        pane.tui.call_from_thread(pane.tui.set_tracked_model, None)
        pane.tui.call_from_thread(pane.tui.refresh_models)
        stop_rc = serverctl.run_command(pane.tui.config.stop_cmd, on_line=stream)
        if stop_rc != 0:
            fail_swap(pane, f"[swap] stop_cmd exited {stop_rc}")
            return
        # Graph legality (swap.py): STOPPING may not reach IDLE or
        # WAITING_HEALTH directly; mirrors the STOPPING entry transition
        # in request_load_swap. Restart-only — the cold-start entry
        # already sits in STARTING (starting→starting is illegal).
        pane.tui.swap_machine.transition(SwapState.STARTING)
    start_cmd = pane.tui.config.start_cmd
    if start_cmd is None:  # config rewritten mid-boot; bail honestly
        fail_swap(pane, None)
        return
    start_full = build_start_command(start_cmd, plan.model_id)
    pane.tui.call_from_thread(pane.tui.log_app, f"[swap] starting: {start_full}")
    proc, monitor = serverctl.spawn_with_grace(
        start_full, on_line=stream, grace_s=2.0, poll_s=0.05
    )
    start_rc = proc.poll()
    if start_rc is not None and start_rc != 0:
        # An instantly-crashing start must not burn the health deadline.
        fail_swap(pane, f"[swap] start_cmd exited {start_rc}")
        return
    pane.tui.swap_machine.transition(SwapState.WAITING_HEALTH)
    deadline = health_timeout(plan.size_on_disk)

    def tick(seconds: int) -> None:
        from mlx_tui.models_pane import table_ops  # noqa: PLC0415

        pane.tui.call_from_thread(
            table_ops.progress_line, pane, plan.model_id or "server", seconds
        )

    ok = serverctl.wait_healthy(
        f"http://{pane.tui.host}:{pane.tui.port}/v1/models",
        target_model=plan.model_id,
        current_model=pane.tui.effective_model,
        is_running=(lambda: proc.poll() is None) if monitor else None,
        timeout_s=deadline,
        on_tick=tick,
    )
    if ok:
        if plan.model_id:
            pane.tui.call_from_thread(pane.tui.set_tracked_model, plan.model_id)
        else:
            pane.tui.call_from_thread(
                lambda: pane.tui.set_tracked_model(pane.tui.effective_model())
            )
        pane.tui.swap_machine.transition(SwapState.IDLE)
        pane.tui.call_from_thread(pane.tui.log_app, plan.success_line)
        pane.tui.call_from_thread(pane.tui.refresh_models)
        pane.tui.call_from_thread(pane.tui._refresh_metrics)
    elif monitor and proc.poll() is not None:
        fail_swap(pane, f"[swap] start_cmd exited {proc.returncode}")
    else:
        timeout_line = (
            f"swap timed out after {int(deadline)}s — check the log pane"
            if plan.stop_first
            else "server did not come up in time — check the log pane"
        )
        fail_swap(pane, timeout_line)
    # Unconditional in both originals: the success branch must release the
    # UI too — _fail_swap only covers the failure arms.
    pane.tui.call_from_thread(pane.tui.set_swap_ui, False)
