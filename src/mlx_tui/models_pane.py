"""Models tab pane: cache table, load/delete requests, swap progress."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast, override

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Static

from mlx_tui import serverctl
from mlx_tui.confirm import ConfirmScreen
from mlx_tui.models import CacheNotFound, ModelRow, delete_repos, scan_models
from mlx_tui.serverctl import HealthWatch, build_start_command
from mlx_tui.swap import BootPlan, SwapState, health_timeout
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
        rows = scan_models(self.tui.latest_avail_gib)
        # Widget has no call_from_thread in textual 8.2.8 — hop via App.
        self.tui.call_from_thread(self._populate, rows)

    def _populate(self, rows: list[ModelRow]) -> None:
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

    def request_load_swap(self) -> None:
        if self.tui.swap_machine.busy:
            self.tui.log_app("swap already in progress", "yellow")
            return
        table = self.query_one("#models-table", ModelsTable)
        if not self.rows or not 0 <= table.cursor_row < len(self.rows):
            self.tui.log_app("no model selected to load", "dim")
            return
        row = self.rows[table.cursor_row]
        if self.tui.status_state == "green":
            # Warm path: an in-server probe-load owns the wait.
            self.tui.swap_machine.transition(SwapState.WAITING_HEALTH)
            self.tui.set_swap_ui(True)
            self.tui.log_app(f"loading {row.repo_id} (in-server load)…")
            self.run_warm_swap(row)
        elif self.tui.config.start_cmd and self.tui.config.stop_cmd:
            # Restart path: stop/start commands own the wait.
            if self.tui.chat_has_live_turn():
                self.tui.cancel_chat_for_swap()
            self.tui.swap_machine.transition(SwapState.STOPPING)
            self.tui.set_swap_ui(True)
            self.run_boot(self._boot_plan_for(row))
        else:
            self.tui.log_app(
                f"cannot load {row.repo_id}: server unreachable "
                "and no start_cmd configured",
                "red",
            )

    def _boot_plan_for(self, row: ModelRow) -> BootPlan:
        return BootPlan(
            model_id=row.repo_id,
            size_on_disk=row.size_on_disk,
            stop_first=True,
            success_line=f"✓ {row.repo_id} is serving",
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

    @work(exclusive=True, group="swap", thread=True)
    def run_warm_swap(self, row: ModelRow) -> None:
        timeout = health_timeout(row.size_on_disk)
        try:
            serverctl.warm_load(
                f"http://{self.tui.host}:{self.tui.port}/v1/chat/completions",
                row.repo_id,
                timeout_s=timeout,
            )
        except Exception as exc:
            self.tui.swap_machine.reset()
            detail = f"{exc.__class__.__name__}: {exc}"[:200]
            self.tui.call_from_thread(self.tui.log_app, f"load failed: {detail}", "red")
            self.tui.call_from_thread(self.tui.set_swap_ui, False)
            return
        self.tui.call_from_thread(self.tui.set_tracked_model, row.repo_id)
        self.tui.swap_machine.transition(SwapState.IDLE)
        self.tui.call_from_thread(self.tui.log_app, f"✓ {row.repo_id} loaded")
        self.tui.call_from_thread(self.tui.refresh_models)
        self.tui.call_from_thread(self.tui.update_history_strip)
        self.tui.call_from_thread(self.tui.set_swap_ui, False)

    def _fail_swap(self, message: str | None) -> None:
        """FAILED→IDLE reset, optional red log line, then UI release."""
        self.tui.swap_machine.reset()
        if message is not None:
            self.tui.call_from_thread(self.tui.log_app, message, "red")
        self.tui.call_from_thread(self.tui.set_swap_ui, False)

    @work(exclusive=True, group="swap", thread=True)
    def run_boot(self, plan: BootPlan) -> None:
        def stream(line: str) -> None:
            self.tui.call_from_thread(self.tui.log_app, f"[swap] {line}")

        if plan.stop_first:
            if self.tui.config.start_cmd is None or self.tui.config.stop_cmd is None:
                # Config rewritten mid-swap; bail honestly.
                self._fail_swap("[swap] commands vanished from config")
                return
            # Marker hygiene: clear tracked state the moment stop fires.
            self.tui.call_from_thread(self.tui.set_tracked_model, None)
            self.tui.call_from_thread(self.tui.refresh_models)
            stop_rc = serverctl.run_command(self.tui.config.stop_cmd, on_line=stream)
            if stop_rc != 0:
                self._fail_swap(f"[swap] stop_cmd exited {stop_rc}")
                return
            # Graph legality (swap.py): STOPPING may not reach IDLE or
            # WAITING_HEALTH directly; mirrors the STOPPING entry transition
            # in request_load_swap. Restart-only — the cold-start entry
            # already sits in STARTING (starting→starting is illegal).
            self.tui.swap_machine.transition(SwapState.STARTING)
        start_cmd = self.tui.config.start_cmd
        if start_cmd is None:  # config rewritten mid-boot; bail honestly
            self._fail_swap(None)
            return
        start_full = build_start_command(start_cmd, plan.model_id)
        self.tui.call_from_thread(self.tui.log_app, f"[swap] starting: {start_full}")
        proc, monitor = serverctl.spawn_with_grace(
            start_full, on_line=stream, grace_s=2.0, poll_s=0.05
        )
        start_rc = proc.poll()
        if start_rc is not None and start_rc != 0:
            # An instantly-crashing start must not burn the health deadline.
            self._fail_swap(f"[swap] start_cmd exited {start_rc}")
            return
        self.tui.swap_machine.transition(SwapState.WAITING_HEALTH)
        deadline = health_timeout(plan.size_on_disk)

        def tick(seconds: int) -> None:
            self.tui.call_from_thread(
                self._progress_line, plan.model_id or "server", seconds
            )

        ok = serverctl.wait_healthy(
            f"http://{self.tui.host}:{self.tui.port}/v1/models",
            HealthWatch(
                target_model=plan.model_id,
                current_model=self.tui.effective_model,
                is_running=(lambda: proc.poll() is None) if monitor else None,
            ),
            timeout_s=deadline,
            on_tick=tick,
        )
        if ok:
            if plan.model_id:
                self.tui.call_from_thread(self.tui.set_tracked_model, plan.model_id)
            else:
                self.tui.call_from_thread(
                    lambda: self.tui.set_tracked_model(self.tui.effective_model())
                )
            self.tui.swap_machine.transition(SwapState.IDLE)
            self.tui.call_from_thread(self.tui.log_app, plan.success_line)
            self.tui.call_from_thread(self.tui.refresh_models)
            self.tui.call_from_thread(self.tui.update_history_strip)
        elif monitor and proc.poll() is not None:
            self._fail_swap(f"[swap] start_cmd exited {proc.returncode}")
        else:
            timeout_line = (
                f"swap timed out after {int(deadline)}s — check the log pane"
                if plan.stop_first
                else "server did not come up in time — check the log pane"
            )
            self._fail_swap(timeout_line)
        # Unconditional in both originals: the success branch must release the
        # UI too — _fail_swap only covers the failure arms.
        self.tui.call_from_thread(self.tui.set_swap_ui, False)

    def request_delete_model(self) -> None:
        if self.tui.swap_machine.busy:
            self.tui.log_app("swap already in progress", "yellow")
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
        # Effective is authoritative: tracked warm-swaps shadow stale cmdline,
        # and when tracked is None it already falls back to cmdline.
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
        self._run_delete(row)

    @work(exclusive=True, group="delete", thread=True)
    def _run_delete(self, row: ModelRow) -> None:
        # Race: the loaded model may have changed between the button press
        # and confirmation (or via an external restart). Re-check here before
        # touching the cache.
        effective_now = self.tui.effective_model()
        if effective_now is not None and row.repo_id == effective_now:
            self.tui.call_from_thread(
                self.tui.log_app,
                f"cannot delete {row.repo_id}: it became active — swap first",
                "yellow",
            )
            return
        try:
            freed = delete_repos(row.revision_hashes)
        except (CacheNotFound, OSError) as exc:
            self.tui.call_from_thread(
                self.tui.log_app,
                f"delete failed: {exc.__class__.__name__}: {exc}",
                "red",
            )
            return
        # Defensive: if a warm-loaded model slipped past the guard, clear the
        # tracked marker so the next effective_model() does not keep pointing
        # at a deleted repo (which would re-trigger a download on chat).
        if row.repo_id == self.tui._tracked_model:
            self.tui.call_from_thread(self.tui.set_tracked_model, None)
            self.tui.call_from_thread(self.tui.refresh_models)
        self.tui.call_from_thread(
            self.tui.log_app, f"deleted {row.repo_id} — freed {freed / 2**30:.1f} GB"
        )
        self.rescan()
