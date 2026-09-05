"""Search query + size fetching — plain functions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from huggingface_hub import HfApi
from textual.css.query import NoMatches
from textual.widgets import DataTable, Input

from mlx_tui.models import quant_label
from mlx_tui.search import (
    filtered_download_size,
    fits_disk,
    free_disk_bytes,
    list_results,
    repo_snapshot,
)

if TYPE_CHECKING:
    from mlx_tui.search_screen import SearchScreen


def on_search_submitted(screen: SearchScreen, event: Input.Submitted) -> None:
    query = event.value.strip()
    if not query:
        screen._set_line("#search-status", "type a search", "dim")
        # Clear stale results so an empty submit doesn't leave old rows.
        screen._repo_ids = []
        screen._sizes.clear()
        screen._revisions.clear()
        try:
            from mlx_tui.search_screen import ResultsTable  # noqa: PLC0415

            screen.query_one("#search-results", ResultsTable).clear()  # type: ignore[attr-defined]
        except NoMatches:
            pass
        return
    screen._run_search(query)
    screen._set_line("#search-status", "searching…", "dim")


def run_search_impl(screen: SearchScreen, query: str) -> None:
    try:
        ids = list_results(HfApi(), query)
    except Exception as exc:
        screen.app.call_from_thread(
            screen._set_line,
            "#search-status",
            f"search failed: {exc.__class__.__name__}",
            "red",
        )
        screen.app.call_from_thread(
            screen.tui.log_app,
            f"search failed: {exc.__class__.__name__}: {exc}"[:300],
            "red",
        )
        return
    screen.app.call_from_thread(populate, screen, ids)


def populate(screen: SearchScreen, ids: list[str]) -> None:
    try:
        from mlx_tui.search_screen import ResultsTable  # noqa: PLC0415

        table = screen.query_one("#search-results", ResultsTable)  # type: ignore[attr-defined]
    except NoMatches:
        return
    screen._repo_ids = ids
    screen._sizes.clear()
    screen._revisions.clear()
    table.clear()
    for rid in ids:
        table.add_row(rid, quant_label(rid), "—", key=rid)
    if not ids:
        screen._set_line("#search-status", "no results", "yellow")
        try:
            screen.query_one("#search-input", Input).focus()
        except NoMatches:
            return
    else:
        screen._set_line("#search-status", f"{len(ids)} results", "dim")
        table.focus()


def on_row_highlighted(screen: SearchScreen, event: DataTable.RowHighlighted) -> None:
    row_key = event.row_key
    # pyrefly may type row_key as unknown; guard accordingly
    repo_id = row_key.value if row_key is not None else None
    if not repo_id:
        return
    # repo_id is str | None; ensure str
    repo_id_str = str(repo_id)
    if repo_id_str in screen._sizes:
        return
    if screen._downloading is not None:
        return
    screen._fetch_size(repo_id_str)


def fetch_size_impl(screen: SearchScreen, repo_id: str) -> None:
    try:
        snapshot = repo_snapshot(HfApi(), repo_id)
        size = filtered_download_size(snapshot.files)
    except Exception:
        return
    glyph_map: dict[bool | None, str] = {True: "✓", False: "⚠", None: "—"}
    glyph = glyph_map[fits_disk(size, free_disk_bytes())]
    screen.app.call_from_thread(
        fill_size_cell, screen, repo_id, size, glyph, snapshot.revision
    )


def fill_size_cell(
    screen: SearchScreen,
    repo_id: str,
    size: int,
    glyph: str,
    revision: str | None = None,
) -> None:
    # Mutate only on the UI thread to avoid a worker/UI dict race.
    screen._sizes[repo_id] = size
    screen._revisions[repo_id] = revision
    try:
        from mlx_tui.search_screen import ResultsTable  # noqa: PLC0415

        table = screen.query_one("#search-results", ResultsTable)  # type: ignore[attr-defined]
    except NoMatches:
        return
    # Import here to avoid circular init: _format_size lives in search_screen/__init__.py
    from mlx_tui.search_screen import _format_size  # noqa: PLC0415

    table.update_cell(repo_id, "download", f"{_format_size(size)} {glyph}")
