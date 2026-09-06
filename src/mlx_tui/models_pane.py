"""Models tab pane: cache table, load/delete requests, swap progress."""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import TYPE_CHECKING, Any, cast, override

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Static

from mlx_tui import process, serverctl
from mlx_tui.app.operations import OperationKind
from mlx_tui.confirm import ConfirmScreen
from mlx_tui.models import (
    CacheNotFound,
    ModelRow,
    delete_repos,
    scan_models,
)
from mlx_tui.serverctl import build_start_command
from mlx_tui.status import ServerProbe
from mlx_tui.swap import (
    BootPlan,
    _refuse_reason,
    boot_plan_for,
    health_timeout,
    resolve_swap_action,
)
from mlx_tui.table import ModelsTable

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


class ModelsPane(Vertical):
    """Owns #swap-progress and #models-table plus the rescan/delete/boot workers."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rows: list[ModelRow] = []
        self._pending_delete_row: ModelRow | None = None

    @property
    def tui(self) -> MlxTuiApp:
        return cast("MlxTuiApp", self.app)

    @override
    def compose(self) -> ComposeResult:
        yield Static("", id="swap-progress")
        yield ModelsTable(id="models-table", cursor_type="row")

    def rescan(self) -> None:
        self._rescan()

    @work(exclusive=True, group="rescan", thread=True)
    def _rescan(self) -> None:
        try:
            rows = scan_models(self.tui.latest_avail_gib)
        except Exception as exc:
            # Keep the current rows; a failed scan must not blank the table.
            self.tui.call_from_thread(self._rescan_failed, exc)
            return
        # Widget has no call_from_thread in textual 8.2.8 — hop via App.
        self.tui.call_from_thread(self._populate, rows)

    def _rescan_failed(self, exc: Exception) -> None:
        try:
            self.tui.log_error_once("rescan", exc)
        except NoMatches:
            pass

    def _populate(self, rows: list[ModelRow]) -> None:
        self.tui.clear_error("rescan")
        try:
            table = self.query_one("#models-table", ModelsTable)
        except NoMatches:
            # A rescan landing during shutdown has no table left to fill.
            return
        table.set_rows(
            rows,
            effective_model=self.tui.effective_model(),
            avail_gib=self.tui.latest_avail_gib,
        )
        self.rows = rows

    def refresh_markers(self) -> None:
        if not self.rows:
            return
        try:
            table = self.query_one("#models-table", ModelsTable)
        except NoMatches:
            return
        table.refresh_markers(
            self.rows,
            effective_model=self.tui.effective_model(),
            avail_gib=self.tui.latest_avail_gib,
        )

    def row_size(self, repo_id: str | None) -> int:
        """Scanned size_on_disk for repo_id; 0 when absent or unknown."""
        if repo_id is None:
            return 0
        return next(
            (row.size_on_disk for row in self.rows if row.repo_id == repo_id), 0
        )

    def _progress_line(self, repo_id: str, seconds: int) -> None:
        try:
            self.query_one("#swap-progress", Static).update(
                f"waiting for {repo_id}… {seconds}s"
            )
        except NoMatches:
            return

    def _reject_if_busy(self) -> bool:
        current = self.tui.operations.current
        if current is OperationKind.IDLE:
            return False
        self.tui.log_app(f"{current.value} operation already in progress", "yellow")
        return True

    def request_load_swap(self) -> None:
        table = self.query_one("#models-table", ModelsTable)
        if not self.rows or not 0 <= table.cursor_row < len(self.rows):
            self.tui.log_app("no model selected to load", "dim")
            return
        row = self.rows[table.cursor_row]
        status_state = self.tui.status_state
        policy = self.tui.config.swap_policy
        has_start = bool(self.tui.config.start_cmd)
        has_stop = bool(self.tui.config.stop_cmd)
        action = resolve_swap_action(status_state, policy, has_start, has_stop)
        if action == "warm":
            self._start_warm(row)
        elif action == "restart":
            self._start_boot(row, stop_first=True)
        elif action == "cold":
            self._start_boot(row, stop_first=False)
        else:
            reason = _refuse_reason(status_state, policy, has_start, has_stop)
            self.tui.log_app(f"cannot load {row.repo_id}: {reason}", "red")

    def _start_warm(self, row: ModelRow) -> None:
        if self._reject_if_busy():
            return
        if not self.tui.operations.try_acquire(OperationKind.LOADING):
            self._reject_if_busy()
            return
        try:
            self.tui.set_operation_ui(True)
            self.tui.log_app(f"loading {row.repo_id} (in-server load)…")
            self.run_warm_swap(row)
        except Exception:
            self.tui.operations.release(OperationKind.LOADING)
            self.tui.set_operation_ui(False)
            raise

    def _start_boot(self, row: ModelRow, *, stop_first: bool) -> None:
        if self.tui.operations.current is OperationKind.CHATTING:
            self.tui.cancel_chat_for_swap()
            return
        if self._reject_if_busy():
            return
        if not self.tui.operations.try_acquire(OperationKind.RESTARTING):
            self._reject_if_busy()
            return
        try:
            self.tui.set_operation_ui(True)
            self.run_boot(boot_plan_for(row, stop_first=stop_first))
        except Exception:
            self.tui.operations.release(OperationKind.RESTARTING)
            self.tui.set_operation_ui(False)
            raise

    @work(exclusive=True, group="swap", thread=True)
    def run_warm_swap(self, row: ModelRow) -> None:
        try:
            full_timeout = health_timeout(row.size_on_disk)
            result = serverctl.warm_load(
                f"http://{self.tui.host}:{self.tui.port}/v1/chat/completions",
                row.repo_id,
                timeout_s=full_timeout,
            )
            if (
                result.response_model is not None
                and result.response_model != row.repo_id
            ):
                self.tui.call_from_thread(
                    self.tui.log_app,
                    f"load failed: server reports model {result.response_model!r}, "
                    f"expected {row.repo_id!r}",
                    "red",
                )
                return
            probe = (
                ServerProbe(state="green", model_id=result.response_model)
                if result.response_model == row.repo_id
                else serverctl.wait_healthy(
                    f"http://{self.tui.host}:{self.tui.port}/v1/models",
                    target_model=row.repo_id,
                    timeout_s=full_timeout,
                )
            )
            if probe is None:
                self.tui.call_from_thread(
                    self.tui.log_app,
                    f"load failed: endpoint did not report {row.repo_id}",
                    "red",
                )
                return
            proc_ident = process.find_server_process(
                self.tui.host, self.tui.port, self.tui.config.pidfile
            )
            self.tui.call_from_thread(
                self.tui.update_server_identity, probe, proc_ident
            )
            self.tui.call_from_thread(self.tui.log_app, f"✓ {row.repo_id} loaded")
            self.tui.call_from_thread(self.tui.refresh_models)
            self.tui.call_from_thread(self.tui._refresh_metrics)
        except Exception as exc:
            detail = f"{exc.__class__.__name__}: {exc}"[:200]
            self.tui.call_from_thread(self.tui.log_app, f"load failed: {detail}", "red")
        finally:
            self.tui.call_from_thread(
                self.tui.operations.release, OperationKind.LOADING
            )
            self.tui.call_from_thread(self.tui.set_operation_ui, False)

    @work(exclusive=True, group="swap", thread=True)
    def run_boot(self, plan: BootPlan) -> None:  # noqa: PLR0912,PLR0915
        def stream(line: str) -> None:
            try:
                self.tui.call_from_thread(self.tui.log_app, f"[swap] {line}")
            except Exception:
                pass

        def finish_ui() -> None:
            try:
                self.tui.operations.release(OperationKind.RESTARTING)
            finally:
                try:
                    self.tui.set_operation_ui(False)
                except NoMatches:
                    pass

        proc: subprocess.Popen[str] | None = None
        monitor = False
        try:
            cfg = self.tui.config
            host = self.tui.host
            port = self.tui.port
            pidfile = cfg.pidfile
            target = plan.model_id
            shell = cfg.command_shell
            start_raw = cfg.start_cmd
            stop_raw = cfg.stop_cmd
            if start_raw is None:
                raise RuntimeError("[swap] start_cmd vanished from config")
            if plan.stop_first and stop_raw is None:
                raise RuntimeError("[swap] stop_cmd vanished from config")
            env: dict[str, str] | None = None
            if shell:
                if "{model}" in start_raw or (
                    plan.stop_first and stop_raw is not None and "{model}" in stop_raw
                ):
                    raise RuntimeError(
                        "[swap] start/stop_cmd uses {model}; shell mode requires "
                        '"$MLX_TUI_MODEL" (e.g., --model "$MLX_TUI_MODEL")'
                    )
                if not start_raw.strip():
                    raise RuntimeError("[swap] invalid start_cmd: empty command")
                if plan.stop_first and stop_raw is not None and not stop_raw.strip():
                    raise RuntimeError("[swap] invalid stop_cmd: empty command")
                if target is not None:
                    # Shell contract: --model "$MLX_TUI_MODEL". The reference is
                    # required but never treated as proof; endpoint verification
                    # remains the final check. Do not rewrite shell syntax.
                    if "MLX_TUI_MODEL" not in start_raw:
                        raise RuntimeError(
                            '[swap] shell start_cmd must reference "$MLX_TUI_MODEL" '
                            '(e.g., --model "$MLX_TUI_MODEL")'
                        )
                    env = {**os.environ, "MLX_TUI_MODEL": target}
                start_cmd_run: str | list[str] = start_raw
                stop_cmd_run: str | list[str] | None = stop_raw
                start_display = start_raw
            else:
                try:
                    start_argv = build_start_command(start_raw, target)
                except ValueError as exc:
                    raise RuntimeError(f"[swap] invalid start_cmd: {exc}") from exc
                stop_argv: list[str] | None = None
                if plan.stop_first:
                    assert stop_raw is not None
                    try:
                        parsed_stop = shlex.split(stop_raw)
                    except ValueError as exc:
                        raise RuntimeError(f"[swap] invalid stop_cmd: {exc}") from exc
                    if not parsed_stop:
                        raise RuntimeError("[swap] invalid stop_cmd: empty command")
                    if any("{model}" in arg for arg in parsed_stop):
                        if target is None:
                            raise RuntimeError(
                                "[swap] stop_cmd contains {model} but no model target"
                            )
                        parsed_stop = [
                            arg.replace("{model}", target) for arg in parsed_stop
                        ]
                    stop_argv = parsed_stop
                start_cmd_run = start_argv
                stop_cmd_run = stop_argv
                start_display = shlex.join(start_argv)
            if plan.stop_first:
                assert stop_cmd_run is not None
                try:
                    self.tui.call_from_thread(self.tui.refresh_models)
                except Exception:
                    pass
                try:
                    stop_rc = serverctl.run_command(
                        stop_cmd_run, on_line=stream, shell=shell, env=env
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("[swap] stop_cmd timed out") from exc
                except ValueError as exc:
                    raise RuntimeError(f"[swap] invalid stop_cmd: {exc}") from exc
                if stop_rc != 0:
                    raise RuntimeError(f"[swap] stop_cmd exited {stop_rc}")

            try:
                self.tui.call_from_thread(
                    self.tui.log_app, f"[swap] starting: {start_display}"
                )
            except Exception:
                pass
            try:
                proc, monitor = serverctl.spawn_with_grace(
                    start_cmd_run,
                    on_line=stream,
                    grace_s=2.0,
                    poll_s=0.05,
                    shell=shell,
                    env=env,
                )
            except ValueError as exc:
                raise RuntimeError(f"[swap] invalid start_cmd: {exc}") from exc
            start_rc = proc.poll()
            if start_rc is not None and start_rc != 0:
                raise RuntimeError(f"[swap] start_cmd exited {start_rc}")

            deadline = health_timeout(plan.size_on_disk)

            def tick(seconds: int) -> None:
                try:
                    self.tui.call_from_thread(
                        self._progress_line, plan.model_id or "server", seconds
                    )
                except Exception:
                    pass

            ok = serverctl.wait_healthy(
                f"http://{host}:{port}/v1/models",
                target_model=target,
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

            proc_ident = process.find_server_process(host, port, pidfile)
            try:
                self.tui.call_from_thread(
                    self.tui.update_server_identity, ok, proc_ident
                )
                self.tui.call_from_thread(self.tui.log_app, plan.success_line)
                self.tui.call_from_thread(self.tui.refresh_models)
                self.tui.call_from_thread(self.tui._refresh_metrics)
            except Exception as exc:
                raise RuntimeError(f"[swap] UI update failed: {exc}") from exc
        except Exception as exc:
            try:
                serverctl._terminate_failed_process(proc)
            except Exception:
                pass
            detail = f"{exc.__class__.__name__}: {exc}"[:240]
            try:
                self.tui.call_from_thread(self.tui.log_app, detail, "red")
            except Exception:
                pass
        finally:
            try:
                self.tui.call_from_thread(finish_ui)
            except Exception:
                try:
                    self.tui.operations.release(OperationKind.RESTARTING)
                except Exception:
                    pass

    def request_delete_model(self) -> None:
        if self.tui.operations.is_busy:
            self.tui.log_app("operation already in progress", "yellow")
            return
        table = self.query_one("#models-table", ModelsTable)
        if not self.rows or not 0 <= table.cursor_row < len(self.rows):
            self.tui.log_app("no model selected to delete", "dim")
            return
        row = self.rows[table.cursor_row]
        # Deleting the model that is currently backing the server leaves the
        # green dot and loaded marker stale (the process keeps the weights in
        # RAM while the files vanish), breaks the next warm-load (effective
        # model still points at the deleted id), and makes the next chat
        # re-download the deleted repo. Block it with a hint.
        effective = self.tui.effective_model()
        if effective is not None and row.repo_id == effective:
            self.tui.log_app(
                f"cannot delete {row.repo_id}: it is currently loaded — swap first",
                "yellow",
            )
            return
        # The modal blocks interaction, so the selection cannot move before
        # the callback fires; stash the row there for _on_delete_confirmed.
        self._pending_delete_row = row
        self.app.push_screen(
            ConfirmScreen(
                f"delete {row.repo_id} ({row.size_on_disk / 2**30:.1f} GB)? y/n"
            ),
            self._on_delete_confirmed,
        )

    def _on_delete_confirmed(self, confirmed: bool | None) -> None:
        row = self._pending_delete_row
        self._pending_delete_row = None
        if not confirmed or row is None:
            self.tui.log_app("kept", "dim")
            return
        self.start_delete(row)

    def start_delete(self, row: ModelRow) -> None:
        if not self.tui.operations.try_acquire(OperationKind.DELETING):
            self.tui.log_app("operation already in progress", "yellow")
            return
        try:
            self.tui.set_operation_ui(True)
            self._run_delete(row)
        except Exception:
            self.tui.operations.release(OperationKind.DELETING)
            self.tui.set_operation_ui(False)
            raise

    @work(exclusive=True, group="delete", thread=True)
    def _run_delete(self, row: ModelRow) -> None:
        try:
            # Race: the loaded model may have changed between confirmation and
            # worker execution. Re-check on the UI thread immediately before
            # touching the cache.
            effective_now = self.tui.call_from_thread(self.tui.effective_model)
            if effective_now is not None and row.repo_id == effective_now:
                self.tui.call_from_thread(
                    self.tui.log_app,
                    f"cannot delete {row.repo_id}: it became active — swap first",
                    "yellow",
                )
                return
            freed = delete_repos(row.revision_hashes)
        except (CacheNotFound, OSError) as exc:
            self.tui.call_from_thread(
                self.tui.log_app,
                f"delete failed: {exc.__class__.__name__}: {exc}",
                "red",
            )
        except Exception as exc:
            self.tui.call_from_thread(
                self.tui.log_app,
                f"delete failed: {exc.__class__.__name__}: {exc}"[:240],
                "red",
            )
        else:
            self.tui.call_from_thread(
                self.tui.log_app,
                f"deleted {row.repo_id} — freed {freed / 2**30:.1f} GB",
            )
            self.tui.call_from_thread(self.rescan)
        finally:
            self.tui.call_from_thread(
                self.tui.operations.release, OperationKind.DELETING
            )
            self.tui.call_from_thread(self.tui.set_operation_ui, False)
