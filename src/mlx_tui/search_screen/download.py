"""Download orchestration — start, progress, finish, rescan."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from textual.css.query import NoMatches
from textual.widgets import Input, Static

from mlx_tui.search import (
    CancelledDownload,
    download_snapshot,
    fits_disk,
    free_disk_bytes,
)

if TYPE_CHECKING:
    from mlx_tui.search_screen import SearchScreen


def start_download(screen: SearchScreen) -> None:
    if screen._downloading is not None:
        screen._set_line(
            "#dl-progress", f"already downloading {screen._downloading}", "yellow"
        )
        return
    from mlx_tui.search_screen import ResultsTable  # noqa: PLC0415

    table = screen.query_one("#search-results", ResultsTable)
    if not screen._repo_ids or not 0 <= table.cursor_row < len(screen._repo_ids):
        screen._set_line("#dl-progress", "no result selected", "dim")
        return
    repo_id = screen._repo_ids[table.cursor_row]
    size = screen._sizes.get(repo_id)
    free = free_disk_bytes()
    if size is not None and fits_disk(size, free) is False:
        # Warn, never block: an actual failure is the ground truth (idea doc).
        from mlx_tui.search_screen import _format_size  # noqa: PLC0415

        screen._set_line(
            "#dl-progress",
            f"warning: needs {_format_size(size)},"
            f" only {_format_size(free or 0)} free — downloading anyway",
            "yellow",
        )
    screen._downloading = repo_id
    screen._cancel_event = threading.Event()
    screen._cancel_logged = False
    screen.query_one("#search-input", Input).disabled = True
    table.disabled = True
    screen._run_download(repo_id, screen._cancel_event)


def run_download_impl(
    screen: SearchScreen, repo_id: str, cancel_event: threading.Event
) -> None:
    def on_progress(done: int, expected: int) -> None:
        screen.app.call_from_thread(progress_line, screen, repo_id, done, expected)

    try:
        download_snapshot(repo_id, on_progress=on_progress, cancel_event=cancel_event)
    except CancelledDownload:
        if not screen._cancel_logged:
            screen.app.call_from_thread(
                screen.tui.log_app, f"download cancelled: {repo_id}", "yellow"
            )
        screen.app.call_from_thread(finish, screen, "aborted")
        return
    except Exception as exc:
        detail = f"{exc.__class__.__name__}: {exc}"[:200]
        screen.app.call_from_thread(
            screen.tui.log_app, f"download failed: {detail}", "red"
        )
        screen.app.call_from_thread(finish, screen, "error", detail)
        return
    screen.app.call_from_thread(screen.tui.log_app, f"✓ downloaded {repo_id}")
    screen.app.call_from_thread(finish, screen, "success")


def progress_line(screen: SearchScreen, repo_id: str, done: int, expected: int) -> None:
    try:
        widget = screen.query_one("#dl-progress", Static)
    except NoMatches:
        return
    from rich.text import Text  # noqa: PLC0415

    from mlx_tui.search_screen import _format_size  # noqa: PLC0415

    pct = f" ({done * 100 // expected}%)" if expected else ""
    if expected:
        text = (
            f"downloading {repo_id}… {_format_size(done)}/{_format_size(expected)}{pct}"
        )
    else:
        text = f"downloading {repo_id}… {_format_size(done)}/…{pct}"
    widget.update(Text(text))


def finish(screen: SearchScreen, outcome: str, detail: str | None = None) -> None:
    screen._downloading = None
    screen._cancel_event = None
    screen._cancel_logged = False
    if outcome == "success":
        rescan_models(screen)
        screen.dismiss(None)
        return
    # error / aborted share widget reset
    from mlx_tui.search_screen import ResultsTable  # noqa: PLC0415

    try:
        screen.query_one("#search-input", Input).disabled = False
        screen.query_one("#search-results", ResultsTable).disabled = False
    except NoMatches:
        return
    if outcome == "error":
        screen._set_line("#dl-progress", f"download failed: {detail}", "red")
    else:  # aborted
        try:
            from rich.text import Text  # noqa: PLC0415

            screen.query_one("#dl-progress", Static).update(Text(""))
        except NoMatches:
            pass


def rescan_models(screen: SearchScreen) -> None:
    # Local import: models_pane imports table which lazy-imports this module.
    from mlx_tui.models_pane import ModelsPane  # noqa: PLC0415

    try:
        screen.tui.query_one(ModelsPane).rescan()
    except NoMatches:
        return


def close_screen(screen: SearchScreen) -> None:
    if screen._cancel_event is not None:
        # Set flag before the event so the worker's CancelledDownload
        # branch can dedupe — otherwise both threads log.
        screen._cancel_logged = True
        screen._cancel_event.set()  # the worker aborts at its next chunk
        # Log immediately from the main thread: the worker's
        # call_from_thread log may be lost if the worker is cancelled
        # on unmount (Widget._on_unmount → workers.cancel_node).
        if screen._downloading is not None:
            try:
                screen.tui.log_app(
                    f"download cancelled: {screen._downloading}", "yellow"
                )
            except Exception:
                pass
    screen.dismiss(None)
