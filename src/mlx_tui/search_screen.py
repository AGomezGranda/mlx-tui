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
    repo_files_with_sizes,
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
        self._downloading: str | None = None  # consumed by Phase 3
        self._cancel_event: threading.Event | None = None  # consumed by Phase 3
        self._cancel_logged: bool = False  # dedupe main vs worker cancel log

    @property
    def tui(self) -> MlxTuiApp:
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

    def _status(self, message: str, style: str | None = None) -> None:
        try:
            widget = self.query_one("#search-status", Static)
        except NoMatches:
            return
        text = Text(message) if style is None else Text(message, style=style)
        widget.update(text)

    def _dl_line(self, message: str, style: str | None = None) -> None:
        try:
            widget = self.query_one("#dl-progress", Static)
        except NoMatches:
            return
        text = Text(message) if style is None else Text(message, style=style)
        widget.update(text)

    @on(Input.Submitted, "#search-input")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            self._status("type a search", "dim")
            # Clear stale results so an empty submit doesn't leave old rows.
            self._repo_ids = []
            try:
                self.query_one("#search-results", ResultsTable).clear()
            except NoMatches:
                pass
            return
        self._run_search(query)
        self._status("searching…", "dim")

    @work(exclusive=True, group="hf-search", thread=True)
    def _run_search(self, query: str) -> None:
        try:
            ids = list_results(HfApi(), query)
        except Exception as exc:
            self.app.call_from_thread(
                self._status, f"search failed: {exc.__class__.__name__}", "red"
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
        table.clear()
        for rid in ids:
            table.add_row(rid, quant_label(rid), "—", key=rid)
        if not ids:
            self._status("no results", "yellow")
            try:
                self.query_one("#search-input", Input).focus()
            except NoMatches:
                return
        else:
            self._status(f"{len(ids)} results", "dim")
            table.focus()

    @on(DataTable.RowHighlighted, "#search-results")
    def _on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row_key = event.row_key
        # pyrefly may type row_key as unknown; guard accordingly
        repo_id = row_key.value if row_key is not None else None
        if not repo_id:
            return
        # repo_id is str | None; ensure str
        repo_id_str = str(repo_id)
        if repo_id_str in self._sizes:
            return
        if self._downloading is not None:
            return
        self._fetch_size(repo_id_str)

    @work(exclusive=True, group="hf-size", thread=True)
    def _fetch_size(self, repo_id: str) -> None:
        try:
            size = filtered_download_size(repo_files_with_sizes(HfApi(), repo_id))
        except Exception:
            return
        glyph_map: dict[bool | None, str] = {True: "✓", False: "⚠", None: "—"}
        glyph = glyph_map[fits_disk(size, free_disk_bytes())]
        self.app.call_from_thread(self._fill_size_cell, repo_id, size, glyph)

    def _fill_size_cell(self, repo_id: str, size: int, glyph: str) -> None:
        # Mutate only on the UI thread to avoid a worker/UI dict race.
        self._sizes[repo_id] = size
        try:
            table = self.query_one("#search-results", ResultsTable)
        except NoMatches:
            return
        table.update_cell(repo_id, "download", f"{_format_size(size)} {glyph}")

    def start_download(self) -> None:
        if self._downloading is not None:
            self._dl_line(f"already downloading {self._downloading}", "yellow")
            return
        table = self.query_one("#search-results", ResultsTable)
        if not self._repo_ids or not 0 <= table.cursor_row < len(self._repo_ids):
            self._dl_line("no result selected", "dim")
            return
        repo_id = self._repo_ids[table.cursor_row]
        size = self._sizes.get(repo_id)
        free = free_disk_bytes()
        if size is not None and fits_disk(size, free) is False:
            # Warn, never block: an actual failure is the ground truth (idea doc).
            self._dl_line(
                f"warning: needs {_format_size(size)},"
                f" only {_format_size(free or 0)} free — downloading anyway",
                "yellow",
            )
        self._downloading = repo_id
        self._cancel_event = threading.Event()
        self._cancel_logged = False
        self.query_one("#search-input", Input).disabled = True
        table.disabled = True
        self._run_download(repo_id, self._cancel_event)

    @work(exclusive=True, group="hf-download", thread=True)
    def _run_download(self, repo_id: str, cancel_event: threading.Event) -> None:
        def on_progress(done: int, expected: int) -> None:
            self.app.call_from_thread(self._progress_line, repo_id, done, expected)

        try:
            download_snapshot(
                repo_id, on_progress=on_progress, cancel_event=cancel_event
            )
        except CancelledDownload:
            if not self._cancel_logged:
                self.app.call_from_thread(
                    self.tui.log_app, f"download cancelled: {repo_id}", "yellow"
                )
            self.app.call_from_thread(self._finish_aborted, repo_id)
            return
        except Exception as exc:
            detail = f"{exc.__class__.__name__}: {exc}"[:200]
            self.app.call_from_thread(
                self.tui.log_app, f"download failed: {detail}", "red"
            )
            self.app.call_from_thread(self._finish_error, detail)
            return
        self.app.call_from_thread(self.tui.log_app, f"✓ downloaded {repo_id}")
        self.app.call_from_thread(self._finish_success)

    def _progress_line(self, repo_id: str, done: int, expected: int) -> None:
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

    def _finish_success(self) -> None:
        self._downloading = None
        self._cancel_event = None
        self._cancel_logged = False
        self._rescan_models()
        self.dismiss(None)

    def _finish_error(self, detail: str) -> None:
        self._downloading = None
        self._cancel_event = None
        self._cancel_logged = False
        try:
            self._reset_widgets()
        except NoMatches:
            return
        self._dl_line(f"download failed: {detail}", "red")

    def _finish_aborted(self, repo_id: str) -> None:
        self._downloading = None
        self._cancel_event = None
        self._cancel_logged = False
        try:
            self._reset_widgets()
            self.query_one("#dl-progress", Static).update(Text(""))
        except NoMatches:
            return

    def _reset_widgets(self) -> None:
        self.query_one("#search-input", Input).disabled = False
        self.query_one("#search-results", ResultsTable).disabled = False

    def _rescan_models(self) -> None:
        # Local import: models_pane imports table which lazy-imports this module.
        from mlx_tui.models_pane import ModelsPane  # noqa: PLC0415

        try:
            self.tui.query_one(ModelsPane).rescan()
        except NoMatches:
            return

    def action_close_screen(self) -> None:
        if self._cancel_event is not None:
            # Set flag before the event so the worker's CancelledDownload
            # branch can dedupe — otherwise both threads log.
            self._cancel_logged = True
            self._cancel_event.set()  # the worker aborts at its next chunk
            # Log immediately from the main thread: the worker's
            # call_from_thread log may be lost if the worker is cancelled
            # on unmount (Widget._on_unmount → workers.cancel_node).
            if self._downloading is not None:
                try:
                    self.tui.log_app(
                        f"download cancelled: {self._downloading}", "yellow"
                    )
                except Exception:
                    pass
        self.dismiss(None)
