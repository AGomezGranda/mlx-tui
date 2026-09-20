"""Modal host for the reusable Hugging Face discovery pane."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast, override

from huggingface_hub import hf_hub_download
from textual.app import ComposeResult
from textual.screen import ModalScreen

from mlx_tui.discover_pane import (
    _INITIAL_LIMIT,
    DiscoverHost,
    DiscoverPane,
    ResultsTable,
    _format_size,
)
from mlx_tui.search import (
    CancelledDownload,
    HubApi,
    RepoSnapshot,
    download_snapshot,
    fits_disk,
    free_disk_bytes,
    list_results,
    repo_snapshot,
)

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


class SearchScreen(ModalScreen[None]):
    """Keep the legacy modal entry point around the shared discovery widget."""

    BINDINGS = [("escape", "close_screen", "Close")]

    DEFAULT_CSS = """
    SearchScreen { align: center middle; }
    SearchScreen > DiscoverPane {
        width: 100%;
        height: 100%;
        border: solid $primary;
    }
    """

    def __init__(self, pinned_candidates: tuple[tuple[str, str], ...] = ()) -> None:
        super().__init__()
        self.discover = DiscoverPane(
            pinned_candidates,
            host=self,
            show_chrome=True,
        )

    @property
    def tui(self) -> MlxTuiApp:  # type: ignore[name-defined]
        return cast("MlxTuiApp", self.app)

    @override
    def compose(self) -> ComposeResult:
        yield self.discover

    def on_mount(self) -> None:
        self.discover.focus_search()

    def on_unmount(self) -> None:
        self.discover.teardown()

    def action_close_screen(self) -> None:
        self.discover.request_close()

    def discover_return(self, pane: DiscoverPane) -> None:
        if pane is self.discover:
            self.dismiss(None)

    def discover_complete(
        self, pane: DiscoverPane, outcome: str, repo_id: str | None
    ) -> None:
        if pane is not self.discover:
            return
        if outcome in {"success", "cancelled"}:
            self._rescan_models(repo_id)
            self.dismiss(None)

    # These forwarding hooks preserve the existing test seams for modal
    # callers while the actual state and workers live in DiscoverPane.
    def discover_list_results(
        self,
        api: HubApi,
        query: str,
        *,
        limit: int = 50,
        author: str | None = None,
    ) -> list[str]:
        if limit == _INITIAL_LIMIT and author is None:
            return list_results(api, query)
        return list_results(api, query, limit=limit, author=author)

    def discover_repo_snapshot(
        self, api: HubApi, repo_id: str, *, revision: str | None = None
    ) -> RepoSnapshot:
        if revision is None:
            return repo_snapshot(api, repo_id)
        return repo_snapshot(api, repo_id, revision=revision)

    def discover_free_disk_bytes(self) -> int | None:
        return free_disk_bytes()

    def discover_hf_hub_download(
        self, repo_id: str, filename: str, *, revision: str
    ) -> str:
        return hf_hub_download(repo_id, filename, revision=revision)

    def discover_download_snapshot(
        self,
        repo_id: str,
        *,
        on_progress: Callable[[int, int], None],
        cancel_event: Any,
        revision: str | None,
    ) -> None:
        download_snapshot(
            repo_id,
            on_progress=on_progress,
            cancel_event=cancel_event,
            revision=revision,
        )

    def discover_schedule_metadata(self, _pane: DiscoverPane, repo_id: str) -> None:
        self._schedule_metadata(repo_id)

    def _schedule_metadata(self, repo_id: str) -> None:
        self.discover._schedule_metadata_impl(repo_id)

    def _rescan_models(self, repo_id: str | None = None) -> None:
        from mlx_tui.models_pane import ModelsPane  # noqa: PLC0415

        try:
            models = self.tui.query_one(ModelsPane)
            if repo_id is not None:
                models.invalidate_facts(repo_id)
            models.rescan()
        except Exception:
            return

    # Compatibility forwarding for existing callers/tests. New code should
    # query the DiscoverPane directly through its host.
    @property
    def _repo_ids(self) -> list[str]:
        return self.discover._repo_ids

    @property
    def _sizes(self) -> dict[str, int]:
        return self.discover._sizes

    @property
    def _revisions(self) -> dict[str, str | None]:
        return self.discover._revisions

    @property
    def _search_generation(self) -> int:
        return self.discover._search_generation

    @property
    def _downloading(self) -> str | None:
        return self.discover._downloading

    @_downloading.setter
    def _downloading(self, value: str | None) -> None:
        self.discover._downloading = value

    @property
    def _cancel_event(self) -> Any:
        return self.discover._cancel_event

    @_cancel_event.setter
    def _cancel_event(self, value: Any) -> None:
        self.discover._cancel_event = value

    def _populate(self, generation: int, ids: list[str], cached: bool = False) -> None:
        self.discover._populate(generation, ids, cached)

    def _fill_size_cell(  # noqa: PLR0913, PLR0917
        self,
        generation: int,
        repo_id: str,
        size: int | None,
        glyph: str,
        revision: str | None = None,
        facts: Any = None,
    ) -> None:
        self.discover._fill_size_cell(generation, repo_id, size, glyph, revision, facts)

    def _fetch_size(
        self, repo_id: str, generation: int, revision: str | None = None
    ) -> None:
        self.discover._fetch_size(repo_id, generation, revision)

    def _show_details(self, repo_id: str) -> None:
        self.discover._show_details(repo_id)

    def _is_current(self, generation: int) -> bool:
        return self.discover._is_current(generation)


__all__ = [
    "CancelledDownload",
    "DiscoverHost",
    "DiscoverPane",
    "RepoSnapshot",
    "ResultsTable",
    "SearchScreen",
    "_format_size",
    "fits_disk",
    "free_disk_bytes",
]
