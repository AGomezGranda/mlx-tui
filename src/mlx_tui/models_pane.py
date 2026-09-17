"""Models tab pane: cache table, load/delete requests, swap progress."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, override

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Static

from mlx_tui import process, serverctl
from mlx_tui.boot import execute_boot
from mlx_tui.confirm import ConfirmScreen
from mlx_tui.models import (
    CacheNotFound,
    ModelRow,
    delete_repos,
    model_identity_matches,
    resolve_cached_snapshot,
    scan_models,
)
from mlx_tui.operations import OperationKind
from mlx_tui.status import ServerProbe
from mlx_tui.swap import (
    BootPlan,
    boot_plan_for,
    health_timeout,
    resolve_swap_action,
)
from mlx_tui.table import ModelsTable

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


class ModelsPane(Vertical):
    """Owns #swap-progress and #models-table plus the rescan/delete/boot workers."""

    DEFAULT_CSS = """
    #swap-progress {
        height: auto;
    }
    """

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
            rows = scan_models()
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
            selected_model=self.tui.effective_model(),
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
            selected_model=self.tui.effective_model(),
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
            if status_state == "amber":
                reason = (
                    "endpoint is amber (unexpected service on port) — refusing swap"
                )
            elif policy == "warm":
                reason = (
                    'swap_policy is "warm" but endpoint is not green (requires green)'
                )
            elif policy == "restart":
                reason = 'swap_policy is "restart" but start_cmd/stop_cmd are not both configured'
            else:
                reason = "server unreachable and no start_cmd configured"
            self.tui.log_app(f"cannot load {row.repo_id}: {reason}", "red")

    def _start_warm(self, row: ModelRow) -> None:
        if self._reject_if_busy():
            return
        if not self.tui.operations.try_acquire(OperationKind.LOADING):
            self._reject_if_busy()
            return
        try:
            self.tui.select_model(row.repo_id)
            self.tui.set_operation_ui(True)
            self.tui.log_app(f"requesting {row.repo_id} (in-server generation)…")
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
        if self.tui.config.runtime_mode == "managed":
            self._start_managed(row)
            return
        if not self.tui.operations.try_acquire(OperationKind.RESTARTING):
            self._reject_if_busy()
            return
        try:
            self.tui.select_model(row.repo_id)
            self.tui.set_operation_ui(True)
            self.run_boot(boot_plan_for(row, stop_first=stop_first))
        except Exception:
            self.tui.operations.release(OperationKind.RESTARTING)
            self.tui.set_operation_ui(False)
            raise

    def _start_managed(self, row: ModelRow) -> None:
        if not self.tui.operations.try_acquire(OperationKind.RESTARTING):
            self._reject_if_busy()
            return
        try:
            snapshot = resolve_cached_snapshot(row.repo_id, row.revision_hashes[-1])
            self.tui.select_model(str(snapshot))
            self.tui.set_operation_ui(True)
            self.run_managed_boot(snapshot, row.repo_id)
        except Exception as exc:
            self.tui.operations.release(OperationKind.RESTARTING)
            self.tui.set_operation_ui(False)
            self.tui.log_app(f"managed load failed: {exc}", "red")

    @work(exclusive=True, group="swap", thread=True)
    def run_managed_boot(self, snapshot: Path, model_label: str) -> None:
        def stream(line: str) -> None:
            try:
                self.tui.call_from_thread(self.tui.log_app, f"[managed] {line}")
            except Exception:
                pass

        try:
            probe = self.tui.start_managed(snapshot, on_line=stream)
        except Exception as exc:
            self.tui.call_from_thread(
                self.tui.record_generation_failure,
            )
            self.tui.call_from_thread(
                self.tui.log_app, f"managed load failed: {exc}", "red"
            )
        else:
            self.tui.call_from_thread(
                self.tui.update_server_identity,
                probe,
                self.tui.managed_runtime.identity if self.tui.managed_runtime else None,
            )
            self.tui.call_from_thread(
                self.tui.log_app,
                f"✓ managed server ready for {model_label}; residency unknown",
            )
            self.tui.call_from_thread(self.tui.refresh_models)
        finally:
            self.tui.call_from_thread(
                self.tui.operations.release, OperationKind.RESTARTING
            )
            self.tui.call_from_thread(self.tui.set_operation_ui, False)

    @work(exclusive=True, group="swap", thread=True)
    def run_warm_swap(self, row: ModelRow) -> None:
        try:
            full_timeout = health_timeout(row.size_on_disk)
            rendered = f"[{self.tui.host}]" if ":" in self.tui.host else self.tui.host
            response_model = serverctl.warm_load(
                f"http://{rendered}:{self.tui.port}/v1/chat/completions",
                row.repo_id,
                timeout_s=full_timeout,
            )
            if response_model != row.repo_id:
                self.tui.call_from_thread(self.tui.record_generation_failure)
                self.tui.call_from_thread(
                    self.tui.log_app,
                    f"request unverified: server reports model {response_model!r}, "
                    f"expected {row.repo_id!r}",
                    "red",
                )
                return
            probe = ServerProbe(state="green", model_id=response_model)
            proc_ident = process.find_server_process(self.tui.host, self.tui.port)
            self.tui.call_from_thread(
                self.tui.update_server_identity, probe, proc_ident
            )
            self.tui.call_from_thread(
                self.tui.log_app,
                f"✓ request succeeded for {row.repo_id}; residency unknown",
            )
            self.tui.call_from_thread(self.tui.refresh_models)
            self.tui.call_from_thread(self.tui._refresh_metrics)
        except Exception as exc:
            self.tui.call_from_thread(self.tui.record_generation_failure)
            detail = f"{exc.__class__.__name__}: {exc}"[:200]
            self.tui.call_from_thread(
                self.tui.log_app, f"request failed: {detail}", "red"
            )
        finally:
            self.tui.call_from_thread(
                self.tui.operations.release, OperationKind.LOADING
            )
            self.tui.call_from_thread(self.tui.set_operation_ui, False)

    @work(exclusive=True, group="swap", thread=True)
    def run_boot(self, plan: BootPlan) -> None:  # noqa: PLR0915
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

        try:
            cfg = self.tui.config
            host = self.tui.host
            port = self.tui.port
            if plan.stop_first:
                # Keep the existing pre-stop refresh: the server identity may be
                # stale while the configured stop command is doing its work.
                try:
                    self.tui.call_from_thread(self.tui.refresh_models)
                except Exception:
                    pass
            probe = execute_boot(
                plan,
                cfg,
                host=host,
                port=port,
                on_line=stream,
                on_tick=lambda seconds: self._progress_line(
                    plan.model_id or "server", seconds
                ),
            )
        except Exception as exc:
            try:
                self.tui.call_from_thread(self.tui.record_generation_failure)
            except Exception:
                pass
            detail = f"{exc.__class__.__name__}: {exc}"[:240]
            try:
                self.tui.call_from_thread(self.tui.log_app, detail, "red")
            except Exception:
                pass
        else:
            try:
                proc_ident = process.find_server_process(host, port)
                self.tui.call_from_thread(
                    self.tui.update_server_identity, probe, proc_ident
                )
                self.tui.call_from_thread(self.tui.log_app, plan.success_line)
                self.tui.call_from_thread(self.tui.refresh_models)
                self.tui.call_from_thread(self.tui._refresh_metrics)
            except Exception as exc:
                detail = f"[swap] UI update failed: {exc}"[:240]
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
        protected = {
            model
            for model in (
                self.tui.server_identity.selected_model,
                self.tui.server_identity.last_response_model,
            )
            if model is not None
        }
        if any(model_identity_matches(row.repo_id, model) for model in protected):
            self.tui.log_app(
                f"cannot delete {row.repo_id}: selected or last observed; "
                "external use is unknown — select and verify another model first",
                "yellow",
            )
            return
        # The modal blocks interaction, so the selection cannot move before
        # the callback fires; stash the row there for _on_delete_confirmed.
        self._pending_delete_row = row
        self.app.push_screen(
            ConfirmScreen(
                f"delete {row.repo_id} ({row.size_on_disk / 2**30:.1f} GiB)? y/n"
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
            identity = self.tui.call_from_thread(lambda: self.tui.server_identity)
            if any(
                model_identity_matches(row.repo_id, model)
                for model in (identity.selected_model, identity.last_response_model)
            ):
                self.tui.call_from_thread(
                    self.tui.log_app,
                    f"cannot delete {row.repo_id}: now selected or last observed; "
                    "external use is unknown",
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
                f"deleted {row.repo_id} — freed {freed / 2**30:.1f} GiB",
            )
            self.tui.call_from_thread(self.rescan)
        finally:
            self.tui.call_from_thread(
                self.tui.operations.release, OperationKind.DELETING
            )
            self.tui.call_from_thread(self.tui.set_operation_ui, False)
