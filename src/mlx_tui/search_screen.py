"""Search modal: find mlx-community models, download one, hand off to Models."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, cast, override

from huggingface_hub import HfApi
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Static

from mlx_tui.models import quant_label
from mlx_tui.search import (
    CancelledDownload,
    download_snapshot,
    filtered_download_size,
    fits_disk,
    free_disk_bytes,
    list_results,
    repo_snapshot,
)

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


class ResultsTable(DataTable[str]):
    """Result rows; enter downloads the selected repo."""

    BINDINGS = [("enter", "start_download", "Download")]

    def action_start_download(self) -> None:
        cast("SearchScreen", self.screen).start_download()


def _format_size(size_bytes: int) -> str:
    """Human size for the table/progress: GB with 1 dp, otherwise MB."""
    if size_bytes >= 2**30:
        return f"{size_bytes / 2**30:.1f} GB"
    return f"{size_bytes / 2**20:.0f} MB"


class SearchScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close_screen", "Close")]

    DEFAULT_CSS = """
    SearchScreen {
        align: center middle;
        #search-box {
            width: 90%;
            max-height: 80%;
            padding: 1 2;
            border: solid $primary;
            background: $surface;
            #search-input { width: 100%; }
            #search-results { height: 12; }
            Static { height: auto; }
        }
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._repo_ids: list[str] = []
        self._sizes: dict[str, int] = {}  # repo_id -> exact download bytes
        self._revisions: dict[str, str | None] = {}  # repo_id -> snapshot revision
        self._downloading: str | None = None
        self._cancel_event: threading.Event | None = None

    @property
    def tui(self) -> MlxTuiApp:  # type: ignore[name-defined]
        return cast("MlxTuiApp", self.app)

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="search-box"):
            yield Input(placeholder="search mlx-community…", id="search-input")
            yield Static("", id="search-status")
            yield ResultsTable(id="search-results", cursor_type="row")
            yield Static("", id="dl-progress")

    def on_mount(self) -> None:
        table = self.query_one("#search-results", ResultsTable)
        for column_key in ("model", "quant", "download"):
            table.add_column(column_key, key=column_key)
        self.query_one("#search-input", Input).focus()

    def on_unmount(self) -> None:
        ev = self._cancel_event
        if ev is not None:
            ev.set()

    def _set_line(self, wid: str, message: str, style: str | None = None) -> None:
        try:
            widget = self.query_one(wid, Static)
        except NoMatches:
            return
        widget.update(Text(message) if style is None else Text(message, style=style))

    @on(Input.Submitted, "#search-input")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            self._set_line("#search-status", "type a search", "dim")
            # Clear stale results so an empty submit doesn't leave old rows.
            self._repo_ids = []
            self._sizes.clear()
            self._revisions.clear()
            try:
                self.query_one("#search-results", ResultsTable).clear()
            except NoMatches:
                pass
            return
        self._run_search(query)
        self._set_line("#search-status", "searching…", "dim")

    @work(exclusive=True, group="hf-search", thread=True)
    def _run_search(self, query: str) -> None:
        try:
            ids = list_results(HfApi(), query)
        except Exception as exc:
            self.app.call_from_thread(
                self._set_line,
                "#search-status",
                f"search failed: {exc.__class__.__name__}",
                "red",
            )
            self.app.call_from_thread(
                self.tui.log_app,
                f"search failed: {exc.__class__.__name__}: {exc}"[:300],
                "red",
            )
            return
        self.app.call_from_thread(self._populate, ids)

    def _populate(self, ids: list[str]) -> None:
        try:
            table = self.query_one("#search-results", ResultsTable)
        except NoMatches:
            return
        self._repo_ids = ids
        self._sizes.clear()
        self._revisions.clear()
        table.clear()
        for rid in ids:
            table.add_row(rid, quant_label(rid), "—", key=rid)
        if not ids:
            self._set_line("#search-status", "no results", "yellow")
            try:
                self.query_one("#search-input", Input).focus()
            except NoMatches:
                return
        else:
            self._set_line("#search-status", f"{len(ids)} results", "dim")
            table.focus()

    @on(DataTable.RowHighlighted, "#search-results")
    def _on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row_key = event.row_key
        repo_id = row_key.value if row_key is not None else None
        if not repo_id:
            return
        if repo_id in self._sizes:
            return
        if self._downloading is not None:
            return
        self._fetch_size(repo_id)

    @work(exclusive=True, group="hf-size", thread=True)
    def _fetch_size(self, repo_id: str) -> None:
        try:
            snapshot = repo_snapshot(HfApi(), repo_id)
            size = filtered_download_size(snapshot.files)
        except Exception as exc:
            self.app.call_from_thread(self._size_failed, repo_id, exc)
            return
        glyph_map: dict[bool | None, str] = {True: "✓", False: "⚠", None: "—"}
        glyph = glyph_map[fits_disk(size, free_disk_bytes())]
        self.app.call_from_thread(
            self._fill_size_cell, repo_id, size, glyph, snapshot.revision
        )

    def _size_failed(self, repo_id: str, exc: Exception) -> None:
        try:
            self.tui.log_error_once(f"size {repo_id}", exc)
        except NoMatches:
            pass

    def _fill_size_cell(
        self, repo_id: str, size: int, glyph: str, revision: str | None = None
    ) -> None:
        # Mutate only on the UI thread to avoid a worker/UI dict race.
        self.tui.clear_error(f"size {repo_id}")
        self._sizes[repo_id] = size
        self._revisions[repo_id] = revision
        try:
            table = self.query_one("#search-results", ResultsTable)
        except NoMatches:
            return
        table.update_cell(repo_id, "download", f"{_format_size(size)} {glyph}")

    def start_download(self) -> None:
        if self._downloading is not None:
            self._set_line(
                "#dl-progress", f"already downloading {self._downloading}", "yellow"
            )
            return
        table = self.query_one("#search-results", ResultsTable)
        if not self._repo_ids or not 0 <= table.cursor_row < len(self._repo_ids):
            self._set_line("#dl-progress", "no result selected", "dim")
            return
        repo_id = self._repo_ids[table.cursor_row]
        size = self._sizes.get(repo_id)
        free = free_disk_bytes()
        if size is not None and fits_disk(size, free) is False:
            # Warn, never block: an actual failure is the ground truth (idea doc).
            self._set_line(
                "#dl-progress",
                f"warning: needs {_format_size(size)},"
                f" only {_format_size(free or 0)} free — downloading anyway",
                "yellow",
            )
        self._downloading = repo_id
        self._cancel_event = threading.Event()
        self.query_one("#search-input", Input).disabled = True
        table.disabled = True
        revision = self._revisions.get(repo_id)
        self._run_download(repo_id, self._cancel_event, revision)

    @work(exclusive=True, group="hf-download", thread=True)
    def _run_download(
        self,
        repo_id: str,
        cancel_event: threading.Event,
        revision: str | None = None,
    ) -> None:
        def on_progress(done: int, expected: int) -> None:
            if cancel_event.is_set():
                return
            self.app.call_from_thread(self._progress_line, repo_id, done, expected)

        try:
            download_snapshot(
                repo_id,
                on_progress=on_progress,
                cancel_event=cancel_event,
                revision=revision,
            )
        except CancelledDownload:
            self.app.call_from_thread(self._finish, "cancelled")
            return
        except Exception as exc:
            detail = f"{exc.__class__.__name__}: {exc}"[:200]
            self.app.call_from_thread(self._finish, "error", detail)
            return
        self.app.call_from_thread(self._finish, "success")

    def _progress_line(self, repo_id: str, done: int, expected: int) -> None:
        ev = self._cancel_event
        if ev is not None and ev.is_set():
            return
        try:
            widget = self.query_one("#dl-progress", Static)
        except NoMatches:
            return
        pct = f" ({done * 100 // expected}%)" if expected else ""
        if expected:
            text = f"downloading {repo_id}… {_format_size(done)}/{_format_size(expected)}{pct}"
        else:
            text = f"downloading {repo_id}… {_format_size(done)}/…{pct}"
        widget.update(Text(text))

    def _finish(self, outcome: str, detail: str | None = None) -> None:
        repo_id = self._downloading
        self._downloading = None
        self._cancel_event = None
        if not self.is_mounted:
            return
        if outcome in ("success", "cancelled"):
            self._rescan_models()
            if repo_id is not None:
                message = (
                    f"download cancelled: {repo_id}"
                    if outcome == "cancelled"
                    else f"✓ downloaded {repo_id}"
                )
                try:
                    self.tui.log_app(
                        message, "yellow" if outcome == "cancelled" else None
                    )
                except NoMatches:
                    pass
            try:
                self.dismiss(None)
            except NoMatches:
                pass
            return
        # error: keep modal usable for retry
        try:
            self.query_one("#search-input", Input).disabled = False
            self.query_one("#search-results", ResultsTable).disabled = False
        except NoMatches:
            return
        if repo_id is not None:
            try:
                self.tui.log_app(f"download failed: {detail}", "red")
            except NoMatches:
                pass
        self._set_line("#dl-progress", f"download failed: {detail}", "red")

    def _rescan_models(self) -> None:
        # Local import: models_pane imports table which lazy-imports this module.
        from mlx_tui.models_pane import ModelsPane  # noqa: PLC0415

        try:
            self.tui.query_one(ModelsPane).rescan()
        except NoMatches:
            return

    def action_close_screen(self) -> None:
        if self._downloading is not None:
            ev = self._cancel_event
            if ev is not None:
                if ev.is_set():
                    return
                ev.set()
            self._set_line("#dl-progress", "cancellation requested", "yellow")
            try:
                self.tui.log_app("cancellation requested", "yellow")
            except NoMatches:
                pass
            return
        try:
            self.dismiss(None)
        except NoMatches:
            pass
