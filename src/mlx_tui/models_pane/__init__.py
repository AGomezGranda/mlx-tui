"""Models tab pane: cache table, load/delete requests, swap progress."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast, override

from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from mlx_tui.models import ModelRow
from mlx_tui.swap import BootPlan
from mlx_tui.table import ModelsTable

from . import delete, swap_ops, table_ops

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
        return table_ops._rescan_impl(self)  # type: ignore[arg-type]

    def _populate(self, rows: list[ModelRow]) -> None:
        return table_ops.populate(self, rows)  # type: ignore[arg-type]

    def refresh_markers(self) -> None:
        return table_ops.refresh_markers(self)  # type: ignore[arg-type]

    def request_load_swap(self) -> None:
        return swap_ops.request_load_swap(self)  # type: ignore[arg-type]

    def _boot_plan_for(self, row: ModelRow) -> BootPlan:
        return swap_ops.boot_plan_for(row)

    def row_size(self, repo_id: str | None) -> int:
        """Scanned size_on_disk for repo_id; 0 when absent or unknown."""
        if repo_id is None:
            return 0
        return next(
            (row.size_on_disk for row in self.rows if row.repo_id == repo_id), 0
        )

    def _progress_line(self, repo_id: str, seconds: int) -> None:
        return table_ops.progress_line(self, repo_id, seconds)  # type: ignore[arg-type]

    @work(exclusive=True, group="swap", thread=True)
    def run_warm_swap(self, row: ModelRow) -> None:
        return swap_ops.run_warm_swap_impl(self, row)  # type: ignore[arg-type]

    def _fail_swap(self, message: str | None) -> None:
        return swap_ops.fail_swap(self, message)  # type: ignore[arg-type]

    @work(exclusive=True, group="swap", thread=True)
    def run_boot(self, plan: BootPlan) -> None:
        return swap_ops.run_boot_impl(self, plan)  # type: ignore[arg-type]

    def request_delete_model(self) -> None:
        return delete.request_delete_model(self)  # type: ignore[arg-type]

    def _on_delete_confirmed(self, confirmed: bool | None) -> None:
        return delete.on_delete_confirmed(self, confirmed)  # type: ignore[arg-type]

    def start_delete(self, row: ModelRow) -> None:
        return delete.start_delete(self, row)  # type: ignore[arg-type]

    @work(exclusive=True, group="delete", thread=True)
    def _run_delete(self, row: ModelRow) -> None:
        return delete._run_delete_impl(self, row)  # type: ignore[arg-type]
