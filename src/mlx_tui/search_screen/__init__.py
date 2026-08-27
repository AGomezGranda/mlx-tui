"""Search modal: find mlx-community models, download one, hand off to Models."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, cast, override

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Static

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
        self._downloading: str | None = None
        self._cancel_event: threading.Event | None = None
        self._cancel_logged: bool = False

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

    def _set_line(self, wid: str, message: str, style: str | None = None) -> None:
        try:
            widget = self.query_one(wid, Static)
        except NoMatches:
            return
        widget.update(Text(message) if style is None else Text(message, style=style))

    @on(Input.Submitted, "#search-input")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        from . import query  # noqa: PLC0415

        return query.on_search_submitted(self, event)  # type: ignore[arg-type]

    @work(exclusive=True, group="hf-search", thread=True)
    def _run_search(self, query: str) -> None:
        from . import query as q  # noqa: PLC0415

        return q.run_search_impl(self, query)  # type: ignore[arg-type]

    def _populate(self, ids: list[str]) -> None:
        from . import query  # noqa: PLC0415

        return query.populate(self, ids)  # type: ignore[arg-type]

    @on(DataTable.RowHighlighted, "#search-results")
    def _on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        from . import query  # noqa: PLC0415

        return query.on_row_highlighted(self, event)  # type: ignore[arg-type]

    @work(exclusive=True, group="hf-size", thread=True)
    def _fetch_size(self, repo_id: str) -> None:
        from . import query  # noqa: PLC0415

        return query.fetch_size_impl(self, repo_id)  # type: ignore[arg-type]

    def _fill_size_cell(self, repo_id: str, size: int, glyph: str) -> None:
        from . import query  # noqa: PLC0415

        return query.fill_size_cell(self, repo_id, size, glyph)  # type: ignore[arg-type]

    def start_download(self) -> None:
        from . import download  # noqa: PLC0415

        return download.start_download(self)  # type: ignore[arg-type]

    @work(exclusive=True, group="hf-download", thread=True)
    def _run_download(self, repo_id: str, cancel_event: threading.Event) -> None:
        from . import download  # noqa: PLC0415

        return download.run_download_impl(self, repo_id, cancel_event)  # type: ignore[arg-type]

    def _progress_line(self, repo_id: str, done: int, expected: int) -> None:
        from . import download  # noqa: PLC0415

        return download.progress_line(self, repo_id, done, expected)  # type: ignore[arg-type]

    def _finish(self, outcome: str, detail: str | None = None) -> None:
        from . import download  # noqa: PLC0415

        return download.finish(self, outcome, detail)  # type: ignore[arg-type]

    def _rescan_models(self) -> None:
        from . import download  # noqa: PLC0415

        return download.rescan_models(self)  # type: ignore[arg-type]

    def action_close_screen(self) -> None:
        from . import download  # noqa: PLC0415

        return download.close_screen(self)  # type: ignore[arg-type]
