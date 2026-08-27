"""Delete orchestration — confirm + cache removal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mlx_tui.confirm import ConfirmScreen
from mlx_tui.models import CacheNotFound, ModelRow, delete_repos
from mlx_tui.table import ModelsTable

if TYPE_CHECKING:
    from mlx_tui.models_pane import ModelsPane


def request_delete_model(pane: ModelsPane) -> None:
    if pane.tui.swap_busy:
        pane.tui.log_app("swap already in progress", "yellow")
        return
    table = pane.query_one("#models-table", ModelsTable)
    if not pane.rows or not 0 <= table.cursor_row < len(pane.rows):
        pane.tui.log_app("no model selected to delete", "dim")
        return
    row = pane.rows[table.cursor_row]
    # Deleting the model that is currently backing the server leaves the
    # green dot and loaded marker stale (the process keeps the weights in
    # RAM while the files vanish), breaks the next warm-load (effective
    # model still points at the deleted id), and makes the next chat
    # re-download the deleted repo. Block it with a hint.
    # Effective is authoritative: tracked warm-swaps shadow stale cmdline,
    # and when tracked is None it already falls back to cmdline.
    effective = pane.tui.effective_model()
    if effective is not None and row.repo_id == effective:
        pane.tui.log_app(
            f"cannot delete {row.repo_id}: it is currently loaded — swap first",
            "yellow",
        )
        return
    # The modal blocks interaction, so the selection cannot move before
    # the callback fires; stash the row there for _on_delete_confirmed.
    pane._pending_delete_row = row
    pane.app.push_screen(
        ConfirmScreen(f"delete {row.repo_id} ({row.size_on_disk / 2**30:.1f} GB)? y/n"),
        pane._on_delete_confirmed,  # type: ignore[arg-type]
    )


def on_delete_confirmed(pane: ModelsPane, confirmed: bool | None) -> None:
    row = pane._pending_delete_row
    pane._pending_delete_row = None
    if not confirmed or row is None:
        pane.tui.log_app("kept", "dim")
        return
    pane._run_delete(row)


def _run_delete_impl(pane: ModelsPane, row: ModelRow) -> None:
    # Race: the loaded model may have changed between the button press
    # and confirmation (or via an external restart). Re-check here before
    # touching the cache.
    effective_now = pane.tui.effective_model()
    if effective_now is not None and row.repo_id == effective_now:
        pane.tui.call_from_thread(
            pane.tui.log_app,
            f"cannot delete {row.repo_id}: it became active — swap first",
            "yellow",
        )
        return
    try:
        freed = delete_repos(row.revision_hashes)
    except (CacheNotFound, OSError) as exc:
        pane.tui.call_from_thread(
            pane.tui.log_app,
            f"delete failed: {exc.__class__.__name__}: {exc}",
            "red",
        )
        return
    # Defensive: if a warm-loaded model slipped past the guard, clear the
    # tracked marker so the next effective_model() does not keep pointing
    # at a deleted repo (which would re-trigger a download on chat).
    if row.repo_id == pane.tui._tracked_model:
        pane.tui.call_from_thread(pane.tui.set_tracked_model, None)
        pane.tui.call_from_thread(pane.tui.refresh_models)
    pane.tui.call_from_thread(
        pane.tui.log_app, f"deleted {row.repo_id} — freed {freed / 2**30:.1f} GB"
    )
    pane.rescan()
