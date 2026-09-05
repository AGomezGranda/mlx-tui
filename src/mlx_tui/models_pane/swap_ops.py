"""Warm-load and cold-start orchestration."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Literal

from mlx_tui import serverctl
from mlx_tui.app.operations import OperationKind
from mlx_tui.models import ModelRow
from mlx_tui.serverctl import build_start_command
from mlx_tui.swap import BootPlan, health_timeout
from mlx_tui.table import ModelsTable

if TYPE_CHECKING:
    from mlx_tui.models_pane import ModelsPane


def _reject_if_busy(pane: ModelsPane) -> bool:
    current = pane.tui.operations.current
    if current is OperationKind.IDLE:
        return False
    pane.tui.log_app(f"{current.value} operation already in progress", "yellow")
    return True


SwapAction = Literal["warm", "restart", "cold", "refuse"]


def resolve_swap_action(
    status_state: str,
    swap_policy: str,
    has_start: bool,
    has_stop: bool,
) -> SwapAction:
    """Deterministic swap policy.

    Priority: amber refuses (proxy squatting); explicit "warm" requires green;
    explicit "restart" requires both commands; "auto" restarts when both
    commands exist, otherwise warms when green and cold-starts when red with
    a start_cmd.
    """
    if swap_policy not in ("auto", "warm", "restart"):
        swap_policy = "auto"
    if status_state == "amber":
        return "refuse"
    if swap_policy == "warm":
        return "warm" if status_state == "green" else "refuse"
    if swap_policy == "restart":
        return "restart" if (has_start and has_stop) else "refuse"
    # swap_policy == "auto"
    if has_start and has_stop:
        return "restart"
    if status_state == "green":
        return "warm"
    return "cold" if (status_state == "red" and has_start) else "refuse"


def _refuse_reason(
    status_state: str,
    swap_policy: str,
    has_start: bool,
    has_stop: bool,
) -> str:
    if status_state == "amber":
        return "endpoint is amber (unexpected service on port) — refusing swap"
    if swap_policy == "warm":
        return 'swap_policy is "warm" but endpoint is not green (requires green)'
    if swap_policy == "restart":
        return 'swap_policy is "restart" but start_cmd/stop_cmd are not both configured'
    return "server unreachable and no start_cmd configured"


def _start_warm(pane: ModelsPane, row: ModelRow) -> None:
    if _reject_if_busy(pane):
        return
    if not pane.tui.operations.try_acquire(OperationKind.LOADING):
        _reject_if_busy(pane)
        return
    try:
        pane.tui.set_operation_ui(True)
        pane.tui.log_app(f"loading {row.repo_id} (in-server load)…")
        pane.run_warm_swap(row)
    except Exception:
        pane.tui.operations.release(OperationKind.LOADING)
        pane.tui.set_operation_ui(False)
        raise


def _start_boot(pane: ModelsPane, row: ModelRow, *, stop_first: bool) -> None:
    if pane.tui.operations.current is OperationKind.CHATTING:
        pane.tui.cancel_chat_for_swap()
        return
    if _reject_if_busy(pane):
        return
    if not pane.tui.operations.try_acquire(OperationKind.RESTARTING):
        _reject_if_busy(pane)
        return
    try:
        pane.tui.set_operation_ui(True)
        pane.run_boot(boot_plan_for(row, stop_first=stop_first))
    except Exception:
        pane.tui.operations.release(OperationKind.RESTARTING)
        pane.tui.set_operation_ui(False)
        raise


def request_load_swap(pane: ModelsPane) -> None:
    table = pane.query_one("#models-table", ModelsTable)
    if not pane.rows or not 0 <= table.cursor_row < len(pane.rows):
        pane.tui.log_app("no model selected to load", "dim")
        return
    row = pane.rows[table.cursor_row]
    status_state = pane.tui.status_state
    policy = pane.tui.config.swap_policy
    has_start = bool(pane.tui.config.start_cmd)
    has_stop = bool(pane.tui.config.stop_cmd)
    action = resolve_swap_action(status_state, policy, has_start, has_stop)
    if action == "warm":
        _start_warm(pane, row)
    elif action == "restart":
        _start_boot(pane, row, stop_first=True)
    elif action == "cold":
        _start_boot(pane, row, stop_first=False)
    else:
        reason = _refuse_reason(status_state, policy, has_start, has_stop)
        pane.tui.log_app(f"cannot load {row.repo_id}: {reason}", "red")


def boot_plan_for(row: ModelRow, *, stop_first: bool = True) -> BootPlan:
    return BootPlan(
        model_id=row.repo_id,
        size_on_disk=row.size_on_disk,
        stop_first=stop_first,
        success_line=f"✓ {row.repo_id} is serving",
    )


def run_warm_swap_impl(pane: ModelsPane, row: ModelRow) -> None:
    try:
        full_timeout = health_timeout(row.size_on_disk)
        result = serverctl.warm_load(
            f"http://{pane.tui.host}:{pane.tui.port}/v1/chat/completions",
            row.repo_id,
            timeout_s=10.0,
        )
        if result.response_model is not None and result.response_model != row.repo_id:
            pane.tui.call_from_thread(
                pane.tui.log_app,
                f"load failed: server reports model {result.response_model!r}, "
                f"expected {row.repo_id!r}",
                "red",
            )
            return
        probe = serverctl.wait_healthy(
            f"http://{pane.tui.host}:{pane.tui.port}/v1/models",
            target_model=row.repo_id,
            timeout_s=full_timeout,
        )
        if probe is None:
            pane.tui.call_from_thread(
                pane.tui.log_app,
                f"load failed: endpoint did not report {row.repo_id}",
                "red",
            )
            return
        from mlx_tui import process as process_mod  # noqa: PLC0415
        from mlx_tui.app.state import update_server_identity  # noqa: PLC0415

        proc_ident = process_mod.find_server_process(
            pane.tui.host, pane.tui.port, pane.tui.config.pidfile
        )
        pane.tui.call_from_thread(update_server_identity, pane.tui, probe, proc_ident)
        pane.tui.call_from_thread(pane.tui.log_app, f"✓ {row.repo_id} loaded")
        pane.tui.call_from_thread(pane.tui.refresh_models)
        pane.tui.call_from_thread(pane.tui._refresh_metrics)
    except Exception as exc:
        detail = f"{exc.__class__.__name__}: {exc}"[:200]
        pane.tui.call_from_thread(pane.tui.log_app, f"load failed: {detail}", "red")
    finally:
        pane.tui.call_from_thread(pane.tui.operations.release, OperationKind.LOADING)
        pane.tui.call_from_thread(pane.tui.set_operation_ui, False)


def fail_swap(pane: ModelsPane, message: str | None) -> None:
    """Release the current model-operation lease and restore its controls."""
    if message is not None:
        pane.tui.call_from_thread(pane.tui.log_app, message, "red")
    kind = pane.tui.operations.current
    if kind in (OperationKind.LOADING, OperationKind.RESTARTING):
        pane.tui.call_from_thread(pane.tui.operations.release, kind)
        pane.tui.call_from_thread(pane.tui.set_operation_ui, False)


def _terminate_failed_process(proc: subprocess.Popen[str] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            return


def run_boot_impl(pane: ModelsPane, plan: BootPlan) -> None:
    def stream(line: str) -> None:
        pane.tui.call_from_thread(pane.tui.log_app, f"[swap] {line}")

    proc: subprocess.Popen[str] | None = None
    monitor = False
    try:
        if plan.stop_first:
            if pane.tui.config.start_cmd is None or pane.tui.config.stop_cmd is None:
                raise RuntimeError("[swap] commands vanished from config")
            pane.tui.call_from_thread(pane.tui.refresh_models)
            try:
                stop_rc = serverctl.run_command(
                    pane.tui.config.stop_cmd, on_line=stream
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("[swap] stop_cmd timed out") from exc
            if stop_rc != 0:
                raise RuntimeError(f"[swap] stop_cmd exited {stop_rc}")

        start_cmd = pane.tui.config.start_cmd
        if start_cmd is None:
            raise RuntimeError("[swap] start_cmd vanished from config")
        start_full = build_start_command(start_cmd, plan.model_id)
        pane.tui.call_from_thread(pane.tui.log_app, f"[swap] starting: {start_full}")
        proc, monitor = serverctl.spawn_with_grace(
            start_full, on_line=stream, grace_s=2.0, poll_s=0.05
        )
        start_rc = proc.poll()
        if start_rc is not None and start_rc != 0:
            raise RuntimeError(f"[swap] start_cmd exited {start_rc}")

        deadline = health_timeout(plan.size_on_disk)

        def tick(seconds: int) -> None:
            from mlx_tui.models_pane import table_ops  # noqa: PLC0415

            pane.tui.call_from_thread(
                table_ops.progress_line, pane, plan.model_id or "server", seconds
            )

        ok = serverctl.wait_healthy(
            f"http://{pane.tui.host}:{pane.tui.port}/v1/models",
            target_model=plan.model_id,
            is_running=(lambda: proc is not None and proc.poll() is None)
            if monitor
            else None,
            timeout_s=deadline,
            on_tick=tick,
        )
        if ok is None:
            if monitor and proc is not None and proc.poll() is not None:
                raise RuntimeError(f"[swap] start_cmd exited {proc.returncode}")
            message = (
                f"swap timed out after {int(deadline)}s — check the log pane"
                if plan.stop_first
                else "server did not come up in time — check the log pane"
            )
            raise RuntimeError(message)

        from mlx_tui import process as process_mod  # noqa: PLC0415
        from mlx_tui.app.state import update_server_identity  # noqa: PLC0415

        proc_ident = process_mod.find_server_process(
            pane.tui.host, pane.tui.port, pane.tui.config.pidfile
        )
        pane.tui.call_from_thread(update_server_identity, pane.tui, ok, proc_ident)
        pane.tui.call_from_thread(pane.tui.log_app, plan.success_line)
        pane.tui.call_from_thread(pane.tui.refresh_models)
        pane.tui.call_from_thread(pane.tui._refresh_metrics)
    except Exception as exc:
        _terminate_failed_process(proc)
        detail = f"{exc.__class__.__name__}: {exc}"[:240]
        pane.tui.call_from_thread(pane.tui.log_app, detail, "red")
    finally:
        pane.tui.call_from_thread(pane.tui.operations.release, OperationKind.RESTARTING)
        pane.tui.call_from_thread(pane.tui.set_operation_ui, False)
