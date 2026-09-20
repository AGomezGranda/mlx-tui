"""Reusable Hugging Face discovery workspace.

The pane owns search, inspection, and download state.  Hosts only decide what
to do when the user returns or when a download has completed; this keeps an
embedded Models view independent from modal setup and comparison flows.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast, override

from huggingface_hub import HfApi, hf_hub_download
from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, DataTable, Input, Select, Static

from mlx_tui.catalog.assessment import (
    AssessmentScenario,
    ModelAssessment,
    ModelFacts,
    assess,
    recommendation_group,
)
from mlx_tui.catalog.facts import (
    facts_from_metadata,
    read_cached_config,
    read_cached_index,
)
from mlx_tui.catalog.hardware import local_hardware, runtime_capabilities
from mlx_tui.catalog.recommendations import (
    rank_candidates,
    suggested_evidence,
    task_evidence,
)
from mlx_tui.models import (
    quant_label,
    resolve_cached_snapshot,
    verify_cached_assets,
)
from mlx_tui.operations import OperationKind
from mlx_tui.search import (
    CancelledDownload,
    HubApi,
    RepoSnapshot,
    download_snapshot,
    exact_repo_id,
    filtered_download_size,
    fits_disk,
    free_disk_bytes,
    list_results,
    repo_snapshot,
)

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


_INITIAL_LIMIT = 50
_NOT_FOUND = 404
_RATE_LIMITED = 429
_COMPACT_WIDTH = 100
_COMPACT_HEIGHT = 28


class DiscoverHost(Protocol):
    """Small lifecycle contract implemented by modal and inline hosts."""

    def discover_return(self, pane: DiscoverPane) -> None: ...

    def discover_complete(
        self, pane: DiscoverPane, outcome: str, repo_id: str | None
    ) -> None: ...


class ResultsTable(DataTable[str]):
    """Result rows; enter asks the owning discovery pane to download."""

    BINDINGS = [
        ("enter", "start_download", "Download"),
        ("escape", "close_discover", "Return"),
    ]

    class DownloadRequested(Message):
        """Posted instead of assuming that the table is owned by a screen."""

    def action_start_download(self) -> None:
        self.post_message(self.DownloadRequested())

    def action_close_discover(self) -> None:
        self.post_message(self.CloseRequested())

    class CloseRequested(Message):
        """Posted so the owning pane handles Return/cancellation."""


def _format_size(size_bytes: int) -> str:
    """Human size for the table/progress using binary units."""
    if size_bytes >= 2**30:
        return f"{size_bytes / 2**30:.1f} GiB"
    return f"{size_bytes / 2**20:.0f} MiB"


class DiscoverPane(Vertical):
    """Search, inspect, and download models for any discovery host."""

    BINDINGS = [("escape", "close_discover", "Return")]

    DEFAULT_CSS = """
    DiscoverPane { height: 1fr; width: 1fr; }
    #search-box {
        width: 100%;
        height: 1fr;
        padding: 1 2;
        background: $surface;
        overflow-y: auto;
        #discover-search-row { height: 3; }
        #search-input { width: 2fr; }
        #discover-publisher { width: 1fr; }
        #discover-actions { height: 3; }
        #discover-controls { height: 3; }
        #discover-task, #discover-context, #discover-scope { width: 1fr; }
        #search-results { height: 1fr; min-height: 4; }
        #discover-details { max-height: 8; overflow-y: auto; }
        Static { height: auto; }
    }
    DiscoverPane.embedded #search-box { padding: 0 1; }
    DiscoverPane.compact #search-box { padding: 0 1; }
    DiscoverPane.compact #search-box #discover-actions,
    DiscoverPane.compact #search-box #discover-search-row,
    DiscoverPane.compact #search-box #discover-controls { height: 2; }
    DiscoverPane.compact #search-box Button,
    DiscoverPane.compact #search-box Input,
    DiscoverPane.compact #search-box Select { height: 2; min-height: 2; }
    DiscoverPane.compact #discover-details { height: 4; max-height: 4; }
    """

    def __init__(
        self,
        pinned_candidates: tuple[tuple[str, str], ...] = (),
        *,
        host: DiscoverHost,
        show_chrome: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._host = host
        self._show_chrome = show_chrome
        self._pinned_revisions = dict(pinned_candidates)
        self._repo_ids: list[str] = list(self._pinned_revisions)
        self._all_repo_ids: list[str] = list(self._repo_ids)
        self._sizes: dict[str, int] = {}
        self._revisions: dict[str, str | None] = {}
        self._facts: dict[str, ModelFacts] = {}
        self._assessments: dict[str, ModelAssessment] = {}
        self._inspected: set[str] = set()
        self._metadata_pending: set[str] = set()
        self._metadata_slots = threading.Semaphore(3)
        self._search_generation = 0
        self._query = ""
        self._publisher = ""
        self._limit = _INITIAL_LIMIT
        self._downloading: str | None = None
        self._cancel_event: threading.Event | None = None
        self._download_lease_owned = False
        self._return_notified = False

        if show_chrome:
            self.remove_class("embedded")
        else:
            self.add_class("embedded")

    @property
    def tui(self) -> MlxTuiApp:  # type: ignore[name-defined]
        return cast("MlxTuiApp", self.app)

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="search-box"):
            if self._show_chrome:
                yield Static("Models  /  Discover", id="discover-title")
                yield Static("Local Mac: checking…", id="discover-hardware")
            with Horizontal(id="discover-actions"):
                yield Button("← Installed", id="discover-installed")
                yield Button("Load more", id="discover-more")
                yield Button("Refresh", id="discover-refresh")
                yield Button("Inspect suggestion", id="discover-suggested-model")
            with Horizontal(id="discover-search-row"):
                yield Input(
                    placeholder="Search or paste a Hugging Face model URL…",
                    id="search-input",
                )
                yield Input(placeholder="Publisher filter", id="discover-publisher")
            with Horizontal(id="discover-controls"):
                yield Select(
                    [
                        ("Chat", "chat"),
                        ("Coding", "coding"),
                        ("Reasoning", "reasoning"),
                    ],
                    value="chat",
                    id="discover-task",
                )
                yield Input(value=str(self.tui.config.max_ctx), id="discover-context")
                yield Select(
                    [
                        ("All results", "all"),
                        ("Recommended", "recommended"),
                        ("Comfortable", "comfortable"),
                        ("Tight", "tight"),
                        ("Unsupported", "unsupported"),
                        ("Unknown", "unknown"),
                    ],
                    value="all",
                    id="discover-scope",
                )
            yield Static("", id="discover-suggestion")
            yield Static("", id="search-status")
            yield ResultsTable(id="search-results", cursor_type="row")
            yield Static(
                "Select a model for revision, compatibility, and memory details",
                id="discover-details",
            )
            yield Static("", id="dl-progress")

    def on_mount(self) -> None:
        self._update_compact(self.size.width, self.size.height)
        if self._show_chrome:
            hardware = local_hardware()
            total = (
                f"{hardware.total_bytes / 2**30:.1f} GiB"
                if hardware.total_bytes is not None
                else "unknown"
            )
            available = (
                f"{hardware.available_bytes / 2**30:.1f} GiB"
                if hardware.available_bytes is not None
                else "unknown"
            )
            self.query_one("#discover-hardware", Static).update(
                f"{hardware.chip or 'Unknown chip'} · {total} unified · "
                f"{available} available now"
            )
        self._show_suggestion()
        table = self.query_one("#search-results", ResultsTable)
        for column_key in (
            "model",
            "compatibility",
            "memory fit",
            "download",
            "quant",
        ):
            table.add_column(column_key, key=column_key)
        if self._repo_ids:
            self._populate(self._search_generation, self._repo_ids)
        self.query_one("#search-input", Input).focus()

    def on_resize(self, event: events.Resize) -> None:
        self._update_compact(event.size.width, event.size.height)

    def _update_compact(self, width: int, height: int) -> None:
        self.set_class(
            width < _COMPACT_WIDTH or height < _COMPACT_HEIGHT,
            "compact",
        )

    def teardown(self) -> None:
        """Invalidate callbacks and cancel a worker owned by this pane."""
        self._search_generation += 1
        event = self._cancel_event
        if event is not None:
            event.set()
        if self._download_lease_owned:
            self.tui.operations.release(OperationKind.DOWNLOADING)
            self._download_lease_owned = False

    def on_unmount(self) -> None:
        self.teardown()

    def _is_current(self, generation: int) -> bool:
        return self.is_mounted and generation == self._search_generation

    def _set_line(self, wid: str, message: str, style: str | None = None) -> None:
        try:
            widget = self.query_one(wid, Static)
        except NoMatches:
            return
        widget.update(Text(message) if style is None else Text(message, style=style))

    def _clear_details(self) -> None:
        self._set_line(
            "#discover-details",
            "Select a model for revision, compatibility, and memory details",
        )

    def _selected_repo_id(self) -> str | None:
        try:
            table = self.query_one("#search-results", ResultsTable)
        except NoMatches:
            return None
        if not 0 <= table.cursor_row < len(self._repo_ids):
            return None
        return self._repo_ids[table.cursor_row]

    def focus_search(self) -> None:
        try:
            self.query_one("#search-input", Input).focus()
        except NoMatches:
            return

    def request_close(self) -> None:
        """Return while idle, or request download cancellation while busy."""
        if self._downloading is not None:
            self._request_cancellation()
            return
        self._search_generation += 1
        if self._return_notified:
            return
        self._return_notified = True
        self._host.discover_return(self)

    def _request_cancellation(self) -> None:
        event = self._cancel_event
        if event is None or event.is_set():
            return
        event.set()
        self._set_line("#dl-progress", "cancellation requested", "yellow")
        try:
            self.tui.log_app("cancellation requested", "yellow")
        except NoMatches:
            pass

    def action_close_discover(self) -> None:
        self.request_close()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            event.stop()
            self.request_close()

    @on(Button.Pressed, "#discover-installed")
    def _return_to_installed(self) -> None:
        self.request_close()

    @on(ResultsTable.DownloadRequested)
    def _download_requested(self) -> None:
        self.start_download()

    @on(ResultsTable.CloseRequested)
    def _close_requested(self) -> None:
        self.request_close()

    @on(Input.Submitted, "#search-input")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        self._publisher = self.query_one("#discover-publisher", Input).value.strip()
        self._submit_search(event.value.strip())

    @on(Input.Submitted, "#discover-publisher")
    def _on_publisher_submitted(self, event: Input.Submitted) -> None:
        self._publisher = event.value.strip()
        self._submit_search(self.query_one("#search-input", Input).value.strip())

    def _submit_search(self, query: str) -> None:
        self._search_generation += 1
        generation = self._search_generation
        self._query = query
        self._limit = _INITIAL_LIMIT
        if not query and not self._publisher:
            self._set_line("#search-status", "type a search", "dim")
            self._repo_ids = []
            self._all_repo_ids = []
            self._sizes.clear()
            self._revisions.clear()
            self._facts.clear()
            self._assessments.clear()
            self._inspected.clear()
            self._metadata_pending.clear()
            try:
                self.query_one("#search-results", ResultsTable).clear()
            except NoMatches:
                pass
            self._clear_details()
            return
        self._run_search(query, generation)
        self._set_line("#search-status", "searching…", "dim")

    @on(Button.Pressed, "#discover-more")
    def _load_more(self) -> None:
        self._limit += _INITIAL_LIMIT
        self._search_generation += 1
        self._run_search(self._query, self._search_generation)
        self._set_line("#search-status", "loading more popular MLX models…", "dim")

    @on(Button.Pressed, "#discover-refresh")
    def _refresh_results(self) -> None:
        key = (self._query, self._publisher, self._limit)
        self.tui.model_search_cache.invalidate(key)
        self._search_generation += 1
        self._run_search(self._query, self._search_generation)
        self._set_line("#search-status", "refreshing Hub results…", "dim")

    def _show_suggestion(self) -> None:
        task = str(self.query_one("#discover-task", Select).value)
        suggestions = suggested_evidence(task)
        if suggestions:
            item = suggestions[0]
            message = (
                f"{task.title()} suggestion to inspect: {item.repo_id} "
                f"({item.scope} purpose evidence; fit and runtime support pending)"
            )
        else:
            message = f"No reviewed {task} suggestion; search all MLX candidates"
        self._set_line("#discover-suggestion", message)

    @on(Button.Pressed, "#discover-suggested-model")
    def _inspect_suggestion(self) -> None:
        task = str(self.query_one("#discover-task", Select).value)
        suggestions = suggested_evidence(task)
        if not suggestions:
            return
        item = suggestions[0]
        self._pinned_revisions[item.repo_id] = item.revision
        self._search_generation += 1
        self._populate(self._search_generation, [item.repo_id])

    def _call_list_results(self, api: HubApi, query: str) -> list[str]:
        hook = getattr(self._host, "discover_list_results", None)
        if hook is not None:
            if self._limit != _INITIAL_LIMIT or self._publisher:
                return hook(
                    api,
                    query,
                    limit=self._limit,
                    author=self._publisher or None,
                )
            return hook(api, query)
        if self._limit != _INITIAL_LIMIT or self._publisher:
            return list_results(
                api, query, limit=self._limit, author=self._publisher or None
            )
        return list_results(api, query)

    @work(exclusive=True, group="hf-search", thread=True)
    def _run_search(self, query: str, generation: int) -> None:
        key = (query, self._publisher, self._limit)
        cached = None if exact_repo_id(query) else self.tui.model_search_cache.get(key)
        if cached is not None:
            self.app.call_from_thread(self._populate, generation, cached, True)
            return
        try:
            ids = self._call_list_results(HfApi(), query)
        except Exception as exc:
            self.app.call_from_thread(self._search_failed, generation, exc)
            return
        if generation == self._search_generation and exact_repo_id(query) is None:
            self.tui.model_search_cache.put(key, ids)
        self.app.call_from_thread(self._populate, generation, ids)

    def _search_failed(self, generation: int, exc: Exception) -> None:
        if not self._is_current(generation):
            return
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status in {401, 403}:
            message = "Hub access denied; sign in or request repository access"
        elif status == _NOT_FOUND:
            message = "Repository not found"
        elif status == _RATE_LIMITED:
            message = "Hub rate limit reached; retry later"
        else:
            message = f"Hub unavailable: {exc.__class__.__name__}"
        self._set_line("#search-status", message, "red")
        self.tui.log_app(f"search failed: {exc.__class__.__name__}: {exc}"[:300], "red")

    def _populate(self, generation: int, ids: list[str], cached: bool = False) -> None:
        if not self._is_current(generation):
            return
        try:
            table = self.query_one("#search-results", ResultsTable)
        except NoMatches:
            return
        self._repo_ids = ids
        self._all_repo_ids = ids
        self._sizes.clear()
        self._facts.clear()
        self._assessments.clear()
        self._inspected.clear()
        self._metadata_pending.clear()
        self._revisions = {
            repo_id: self._pinned_revisions[repo_id]
            for repo_id in ids
            if repo_id in self._pinned_revisions
        }
        table.clear()
        for rid in ids:
            table.add_row(rid, "Unknown", "Unknown", "—", quant_label(rid), key=rid)
        if not ids:
            self._set_line("#search-status", "no results", "yellow")
            self._clear_details()
            try:
                self.query_one("#search-input", Input).focus()
            except NoMatches:
                return
        else:
            source = (
                " · cached Hub results; Refresh for latest"
                if cached
                else " · Hub results by downloads"
            )
            self._set_line("#search-status", f"{len(ids)} results{source}", "dim")
            table.focus()
            for repo_id in ids[:3]:
                self._schedule_metadata(repo_id)
        if self.query_one("#discover-scope", Select).value != "all":
            self._filter_results()

    @on(Select.Changed, "#discover-scope")
    def _filter_results(self) -> None:
        if not self.is_mounted:
            return
        scope = str(self.query_one("#discover-scope", Select).value)
        visible = (
            self._all_repo_ids
            if scope == "all"
            else [rid for rid in self._all_repo_ids if self._matches_scope(rid, scope)]
        )
        if scope in {"recommended", "tight"}:
            task = str(self.query_one("#discover-task", Select).value)
            candidates = [
                (
                    rid,
                    self._assessments[rid],
                    task_evidence(rid, self._facts[rid].revision, task)
                    if rid in self._facts
                    else None,
                    len(self._all_repo_ids) - index,
                )
                for index, rid in enumerate(self._all_repo_ids)
                if rid in visible and rid in self._assessments
            ]
            visible = [item[0] for item in rank_candidates(candidates)]
        table = self.query_one("#search-results", ResultsTable)
        selected = (
            table.get_row_at(table.cursor_row)[0]
            if 0 <= table.cursor_row < table.row_count
            else None
        )
        self._repo_ids = visible
        table.clear()
        for rid in visible:
            result = self._assessments.get(rid)
            size = self._sizes.get(rid)
            table.add_row(
                rid,
                result.compatibility.value if result else "Unknown",
                result.fit.value if result else "Unknown",
                _format_size(size) if size is not None else "—",
                quant_label(rid),
                key=rid,
            )
        if selected is not None and selected in visible:
            table.move_cursor(row=visible.index(selected))
        else:
            self._clear_details()
        self._set_line(
            "#search-status",
            f"{len(visible)} shown of {len(self._all_repo_ids)} results",
        )

    def _matches_scope(self, repo_id: str, scope: str) -> bool:
        result = self._assessments.get(repo_id)
        if result is None:
            return scope == "unknown"
        if scope == "recommended":
            task = str(self.query_one("#discover-task", Select).value)
            facts = self._facts.get(repo_id)
            evidence = task_evidence(repo_id, facts.revision, task) if facts else None
            return recommendation_group(result, evidence is not None) == "Recommended"
        if scope == "comfortable":
            return result.fit.value == "Comfortable estimate"
        if scope == "tight":
            return result.fit.value == "Tight estimate"
        if scope == "unsupported":
            return result.compatibility.value == "Requires different runtime"
        return result.compatibility.value == "Unknown" or result.fit.value == "Unknown"

    @on(DataTable.RowHighlighted, "#search-results")
    def _on_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row_key = event.row_key
        repo_id = row_key.value if row_key is not None else None
        if not repo_id:
            return
        self._show_details(repo_id)
        if repo_id in self._inspected or repo_id in self._metadata_pending:
            return
        if self._downloading is not None:
            return
        self._schedule_metadata(repo_id)

    def _schedule_metadata(self, repo_id: str) -> None:
        hook = getattr(self._host, "discover_schedule_metadata", None)
        if hook is not None:
            hook(self, repo_id)
            return
        self._schedule_metadata_impl(repo_id)

    def _schedule_metadata_impl(self, repo_id: str) -> None:
        if repo_id in self._inspected or repo_id in self._metadata_pending:
            return
        self._metadata_pending.add(repo_id)
        self._fetch_size(
            repo_id, self._search_generation, self._pinned_revisions.get(repo_id)
        )

    def _call_repo_snapshot(
        self, api: HubApi, repo_id: str, revision: str | None
    ) -> RepoSnapshot:
        hook = getattr(self._host, "discover_repo_snapshot", None)
        if hook is not None:
            if revision is None:
                return hook(api, repo_id)
            return hook(api, repo_id, revision=revision)
        if revision is None:
            return repo_snapshot(api, repo_id)
        return repo_snapshot(api, repo_id, revision=revision)

    def _call_free_disk_bytes(self) -> int | None:
        hook = getattr(self._host, "discover_free_disk_bytes", None)
        return hook() if hook is not None else free_disk_bytes()

    def _call_hf_download(self, repo_id: str, filename: str, revision: str) -> str:
        hook = getattr(self._host, "discover_hf_hub_download", None)
        if hook is not None:
            return hook(repo_id, filename, revision=revision)
        return hf_hub_download(repo_id, filename, revision=revision)

    @work(group="hf-size", thread=True)
    def _fetch_size(
        self, repo_id: str, generation: int, revision: str | None = None
    ) -> None:
        if revision is None:
            revision = self._pinned_revisions.get(repo_id)
        with self._metadata_slots:
            self._fetch_size_now(repo_id, generation, revision)

    def _fetch_size_now(
        self, repo_id: str, generation: int, revision: str | None
    ) -> None:
        snapshot = None
        try:
            cached = self._verified_pinned_snapshot(repo_id, revision)
            if cached is not None:
                files = tuple(
                    (str(path.relative_to(cached)), path.stat().st_size)
                    for path in cached.rglob("*")
                    if path.is_file()
                )
                snapshot = RepoSnapshot(revision, files)
                size = None
                resolved_revision = revision
            elif revision is None:
                snapshot = self._call_repo_snapshot(HfApi(), repo_id, None)
                size = (
                    filtered_download_size(snapshot.files) if snapshot.files else None
                )
                resolved_revision = snapshot.revision
            else:
                snapshot = self._call_repo_snapshot(HfApi(), repo_id, revision)
                size = (
                    filtered_download_size(snapshot.files) if snapshot.files else None
                )
                resolved_revision = snapshot.revision
        except Exception as exc:
            self.app.call_from_thread(self._size_failed, generation, repo_id, exc)
            return
        facts = (
            facts_from_metadata(
                repo_id,
                snapshot,
                read_cached_config(cached),
                weight_index=read_cached_index(cached),
                files_complete=False,
            )
            if cached is not None and snapshot is not None
            else None
        )
        if (
            cached is None
            and resolved_revision is not None
            and len(resolved_revision) == 40  # noqa: PLR2004
        ):
            try:
                config_path = self._call_hf_download(
                    repo_id, "config.json", resolved_revision
                )
                config = json.loads(Path(config_path).read_text(encoding="utf-8"))
                if snapshot is not None:
                    index = None
                    if any(
                        name == "model.safetensors.index.json"
                        for name, _ in snapshot.files
                    ):
                        index_path = self._call_hf_download(
                            repo_id,
                            "model.safetensors.index.json",
                            resolved_revision,
                        )
                        index = read_cached_index(Path(index_path).parent)
                    facts = facts_from_metadata(
                        repo_id, snapshot, config, weight_index=index
                    )
            except Exception:
                pass
        glyph_map: dict[bool | None, str] = {True: "✓", False: "⚠", None: "—"}
        glyph = (
            glyph_map[fits_disk(size, self._call_free_disk_bytes())]
            if size is not None
            else "—"
        )
        self.app.call_from_thread(
            self._fill_size_cell,
            generation,
            repo_id,
            size,
            glyph,
            resolved_revision,
            facts,
        )

    def _verified_pinned_snapshot(
        self, repo_id: str, revision: str | None
    ) -> Path | None:
        if revision is None:
            return None
        entry = next(
            (
                entry
                for entry in self.tui.profile_entries
                if entry.profile.repo_id == repo_id
                and entry.profile.revision == revision
            ),
            None,
        )
        if entry is None:
            return None
        try:
            snapshot = resolve_cached_snapshot(repo_id, revision)
            verify_cached_assets(snapshot, entry.profile.template_assets)
        except (OSError, ValueError):
            return None
        return snapshot

    def _size_failed(self, generation: int, repo_id: str, exc: Exception) -> None:
        if not self._is_current(generation) or repo_id not in self._all_repo_ids:
            return
        self._metadata_pending.discard(repo_id)
        try:
            self.tui.log_error_once(f"size {repo_id}", exc)
        except NoMatches:
            pass

    def _fill_size_cell(  # noqa: PLR0913, PLR0917
        self,
        generation: int,
        repo_id: str,
        size: int | None,
        glyph: str,
        revision: str | None = None,
        facts: ModelFacts | None = None,
    ) -> None:
        if not self._is_current(generation) or repo_id not in self._all_repo_ids:
            return
        self._metadata_pending.discard(repo_id)
        self.tui.clear_error(f"size {repo_id}")
        if size is not None:
            self._sizes[repo_id] = size
        pinned_revision = self._pinned_revisions.get(repo_id)
        if pinned_revision is not None:
            revision = pinned_revision
        self._revisions[repo_id] = revision
        self._inspected.add(repo_id)
        if facts is not None:
            self._facts[repo_id] = facts
        if repo_id not in self._repo_ids:
            self._assessment_changed()
            return
        try:
            table = self.query_one("#search-results", ResultsTable)
        except NoMatches:
            return
        table.update_cell(
            repo_id,
            "download",
            f"{_format_size(size)} {glyph}" if size is not None else "unknown",
        )
        if self._selected_repo_id() == repo_id:
            self._show_details(repo_id)
        result = self._assessments.get(repo_id)
        if result is not None:
            table.update_cell(repo_id, "compatibility", result.compatibility.value)
            table.update_cell(repo_id, "memory fit", result.fit.value)
        if self.query_one("#discover-scope", Select).value != "all":
            self._filter_results()

    def _show_details(self, repo_id: str) -> None:
        facts = self._facts.get(repo_id)
        revision = self._revisions.get(repo_id)
        if facts is None:
            message = (
                f"{repo_id}@{revision or 'unresolved'} · compatibility and memory "
                "unknown; metadata unavailable"
            )
        else:
            try:
                context = int(self.query_one("#discover-context", Input).value)
            except ValueError:
                context = 0
            manager = self.tui.managed_runtime
            result = assess(
                facts,
                local_hardware(),
                runtime_capabilities(
                    self.tui.config.runtime_mode,
                    managed_verified=bool(manager and manager.install_evidence),
                ),
                AssessmentScenario(context),
            )
            self._assessments[repo_id] = result
            task = self.query_one("#discover-task", Select).value
            evidence = task_evidence(repo_id, facts.revision, str(task))
            group = recommendation_group(result, evidence is not None)
            evidence_label = (
                f"{evidence.explanation} Source: {evidence.source} "
                f"(reviewed {evidence.reviewed})"
                if evidence
                else f"{task} suitability unverified"
            )
            message = (
                f"{repo_id}@{facts.revision or 'unresolved'} · "
                f"{result.compatibility.value}: {result.compatibility_reason}\n"
                f"{result.fit.value} at {context} tokens "
                f"({result.memory_fit_reason}; {group}; {evidence_label}). "
                f"Weights: {_format_size(facts.weight_bytes) if facts.weight_bytes is not None else 'unknown'}; "
                f"download: {_format_size(facts.download_bytes) if facts.download_bytes is not None else 'unknown'}. "
                f"Additional download: {_format_size(facts.additional_download_bytes) if facts.additional_download_bytes is not None else 'unknown'}. "
                f"Architecture: {facts.architecture or 'unknown'}; quantization: "
                f"{facts.quantization or quant_label(repo_id) + ' name hint'}. "
                "Local Mac estimate; one sequence, prefill may exceed allowance."
            )
            if result.available_now is False:
                message += " Available memory is currently below the estimate."
        self._set_line("#discover-details", message)

    @on(Input.Changed, "#discover-context")
    @on(Select.Changed, "#discover-task")
    def _assessment_changed(self) -> None:
        self._show_suggestion()
        try:
            context = int(self.query_one("#discover-context", Input).value)
        except ValueError:
            context = 0
        manager = self.tui.managed_runtime
        runtime = runtime_capabilities(
            self.tui.config.runtime_mode,
            managed_verified=bool(manager and manager.install_evidence),
        )
        hardware = local_hardware()
        for repo_id, facts in self._facts.items():
            self._assessments[repo_id] = assess(
                facts, hardware, runtime, AssessmentScenario(context)
            )
        table = self.query_one("#search-results", ResultsTable)
        if 0 <= table.cursor_row < len(self._repo_ids):
            repo_id = self._repo_ids[table.cursor_row]
            self._show_details(repo_id)
        else:
            self._clear_details()
        for repo_id in self._repo_ids:
            result = self._assessments.get(repo_id)
            if result is not None:
                table.update_cell(repo_id, "memory fit", result.fit.value)
        if self.query_one("#discover-scope", Select).value != "all":
            self._filter_results()

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
        revision = self._revisions.get(repo_id)
        if revision is None:
            self._set_line(
                "#dl-progress",
                "Inspecting revision; retry download when its SHA is available",
                "yellow",
            )
            self._fetch_size(repo_id, self._search_generation)
            return
        if not self.tui.operations.try_acquire(OperationKind.DOWNLOADING):
            self._set_line(
                "#dl-progress", "another operation is already running", "yellow"
            )
            return
        self._download_lease_owned = True
        size = self._sizes.get(repo_id)
        free = self._call_free_disk_bytes()
        if size is not None and fits_disk(size, free) is False:
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
        self._run_download(repo_id, self._cancel_event, revision)

    def _call_download_snapshot(
        self,
        repo_id: str,
        *,
        on_progress: Callable[[int, int], None],
        cancel_event: threading.Event,
        revision: str | None,
    ) -> None:
        hook = getattr(self._host, "discover_download_snapshot", None)
        if hook is not None:
            hook(
                repo_id,
                on_progress=on_progress,
                cancel_event=cancel_event,
                revision=revision,
            )
            return
        download_snapshot(
            repo_id,
            on_progress=on_progress,
            cancel_event=cancel_event,
            revision=revision,
        )

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
            self._call_download_snapshot(
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
        event = self._cancel_event
        if event is not None and event.is_set():
            return
        try:
            widget = self.query_one("#dl-progress", Static)
        except NoMatches:
            return
        pct = f" ({done * 100 // expected}%)" if expected else ""
        if expected:
            text = (
                f"downloading {repo_id}… {_format_size(done)}/"
                f"{_format_size(expected)}{pct}"
            )
        else:
            text = f"downloading {repo_id}… {_format_size(done)}/…{pct}"
        widget.update(Text(text))

    def _finish(self, outcome: str, detail: str | None = None) -> None:
        if self._download_lease_owned:
            self.tui.operations.release(OperationKind.DOWNLOADING)
            self._download_lease_owned = False
        repo_id = self._downloading
        self._downloading = None
        self._cancel_event = None
        if not self.is_mounted:
            return
        if outcome in ("success", "cancelled"):
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
            self._host.discover_complete(self, outcome, repo_id)
            return
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
