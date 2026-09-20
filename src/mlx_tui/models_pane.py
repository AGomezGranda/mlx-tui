"""Models tab pane: cache table, load/delete requests, swap progress."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, override

from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, Collapsible, DataTable, Input, Label, Select, Static

from mlx_tui import process, serverctl
from mlx_tui.boot import execute_boot
from mlx_tui.catalog.assessment import (
    AssessmentScenario,
    Compatibility,
    ModelAssessment,
    ModelFacts,
    assess,
    stable_memory_budget_bytes,
)
from mlx_tui.catalog.facts import (
    facts_from_metadata,
    read_cached_config,
    read_cached_index,
)
from mlx_tui.catalog.hardware import local_hardware, runtime_capabilities
from mlx_tui.confirm import ConfirmScreen
from mlx_tui.discover_pane import DiscoverPane
from mlx_tui.model_context import (
    CONTEXT_PRESETS,
    CUSTOM_CONTEXT_LABEL,
    context_preset_for_tokens,
    context_tokens_from_preset,
    validate_custom_context,
)
from mlx_tui.models import (
    CacheNotFound,
    ModelRow,
    delete_repos,
    model_identity_matches,
    resolve_cached_snapshot,
    scan_models,
)
from mlx_tui.operations import OperationKind
from mlx_tui.search import RepoSnapshot
from mlx_tui.status import ServerProbe
from mlx_tui.swap import (
    BootPlan,
    boot_plan_for,
    health_timeout,
    resolve_swap_action,
)
from mlx_tui.table import ModelsTable

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


_COMPACT_WIDTH = 120


class ModelsPane(Vertical):
    """Owns the Installed workspace plus rescan/delete/boot workers."""

    DEFAULT_CSS = """
    ModelsPane { height: 1fr; width: 1fr; }
    #models-workspace { height: 1fr; width: 1fr; layout: horizontal; }
    #models-sidebar {
        width: 30;
        min-width: 30;
        height: 1fr;
        padding: 0 1;
        border-right: solid $panel;
    }
    #models-main {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }
    #models-installed-view { width: 1fr; height: 1fr; }
    #models-discover-pane { width: 1fr; height: 1fr; }
    #models-hardware-panel { height: auto; max-height: 15; }
    #models-hardware-panel > Contents { height: auto; padding: 0 1; }
    .models-section-title { height: 1; text-style: bold; }
    .models-field-label { height: 1; color: $text-muted; }
    .models-field-value { height: 1; }
    #models-discover { width: 1fr; margin: 1 0; }
    #models-context-heading { height: 1; text-style: bold; }
    #models-context-row { width: 1fr; height: 3; }
    #models-context-preset { width: 10; min-width: 10; }
    #models-context { width: 1fr; margin-left: 1; }
    #models-context-help { height: auto; color: $text-muted; }
    #models-context-error { height: auto; color: $error; }
    #swap-progress { height: auto; }
    #models-table { height: 1fr; min-height: 4; }
    #models-details-panel {
        height: 9;
        min-height: 6;
        max-height: 9;
        border-top: solid $panel;
        padding: 0 1;
    }
    #models-details-heading { height: 1; text-style: bold; }
    #models-details { height: 6; max-height: 6; overflow-y: auto; }
    #models-details-more { dock: right; width: 16; height: 2; }
    ModelsPane.compact-layout #models-workspace { layout: vertical; }
    ModelsPane.compact-layout #models-sidebar {
        width: 1fr;
        min-width: 1fr;
        height: auto;
        max-height: 9;
        padding: 0 1;
        border-right: none;
        border-bottom: solid $panel;
    }
    ModelsPane.compact-layout #models-hardware-panel { max-height: 3; }
    ModelsPane.compact-layout #models-hardware-panel > Contents { height: auto; }
    ModelsPane.compact-layout #models-discover { margin: 0; }
    ModelsPane.compact-layout #models-main { min-height: 1fr; }
    ModelsPane.compact-layout #models-details-panel { height: 8; min-height: 6; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rows: list[ModelRow] = []
        self.assessments: dict[str, ModelAssessment] = {}
        self._assessment_revisions: dict[str, str] = {}
        self._assessment_failures: dict[str, str] = {}
        self._facts_cache: dict[tuple[str, str], ModelFacts] = {}
        self._facts_lock = threading.RLock()
        self._assessment_generation = 0
        self._assessment_context: int | None = None
        self._applied_context: int | None = None
        self._details_text = ""
        self._full_details_text = ""
        self._pending_delete_row: ModelRow | None = None
        self._discover_pane: DiscoverPane | None = None

    @property
    def tui(self) -> MlxTuiApp:
        return cast("MlxTuiApp", self.app)

    @property
    def assessment_failures(self) -> dict[str, str]:
        """Per-row metadata/assessment failures retained for the details view."""
        return self._assessment_failures

    @property
    def assessment_revisions(self) -> dict[str, str]:
        """Revision hashes used by the current assessment snapshot."""
        return self._assessment_revisions

    @override
    def compose(self) -> ComposeResult:
        options = tuple((label, label) for label, _tokens in CONTEXT_PRESETS) + (
            (CUSTOM_CONTEXT_LABEL, CUSTOM_CONTEXT_LABEL),
        )
        with Horizontal(id="models-workspace"):
            with Vertical(id="models-sidebar"):
                with Collapsible(
                    title="YOUR MAC", id="models-hardware-panel", collapsed=False
                ):
                    yield Label("Chip", classes="models-field-label")
                    yield Static(
                        "unavailable", id="models-chip", classes="models-field-value"
                    )
                    yield Label("Unified memory", classes="models-field-label")
                    yield Static(
                        "unavailable",
                        id="models-unified-memory",
                        classes="models-field-value",
                    )
                    yield Label("Available now", classes="models-field-label")
                    yield Static(
                        "unavailable",
                        id="models-available-memory",
                        classes="models-field-value",
                    )
                    yield Label("Assessment budget", classes="models-field-label")
                    yield Static(
                        "unavailable",
                        id="models-assessment-budget",
                        classes="models-field-value",
                    )
                    yield Label("Metal working set", classes="models-field-label")
                    yield Static(
                        "unavailable",
                        id="models-working-set",
                        classes="models-field-value",
                    )
                yield Button("Discover on Hugging Face", id="models-discover")
                yield Static("Estimate context", id="models-context-heading")
                with Horizontal(id="models-context-row"):
                    yield Select(options, id="models-context-preset", allow_blank=False)
                    yield Input(
                        placeholder="tokens",
                        id="models-context",
                        type="integer",
                    )
                yield Static("Used for estimates only", id="models-context-help")
                yield Static("", id="models-context-error")
            with Vertical(id="models-main"):
                yield Static("", id="swap-progress")
                with Vertical(id="models-installed-view"):
                    yield Static("Installed models", id="models-installed-heading")
                    yield ModelsTable(id="models-table", cursor_type="row")
                    with Vertical(id="models-details-panel"):
                        yield Static("Selected model", id="models-details-heading")
                        yield Static(
                            "Select a model for assessment details", id="models-details"
                        )
                        yield Button(
                            "More details", id="models-details-more", disabled=True
                        )

    def on_mount(self) -> None:
        configured = self.tui.config.max_ctx
        self._applied_context = configured if configured > 0 else 8_192
        self._set_context_widgets(self._applied_context)
        self._update_layout(self.size.width)
        self._refresh_hardware()

    def on_resize(self, event: events.Resize) -> None:
        self._update_layout(event.size.width)

    def _update_layout(self, width: int) -> None:
        compact = width < _COMPACT_WIDTH
        if self.has_class("compact-layout") != compact:
            self.set_class(compact, "compact-layout")
        try:
            panel = self.query_one("#models-hardware-panel", Collapsible)
        except NoMatches:
            return
        if panel.collapsed != compact:
            panel.collapsed = compact

    def _set_context_widgets(self, tokens: int) -> None:
        label = context_preset_for_tokens(tokens)
        try:
            preset = self.query_one("#models-context-preset", Select)
            context = self.query_one("#models-context", Input)
            if preset.value != label:
                preset.value = label
            if context.value != str(tokens):
                context.value = str(tokens)
        except NoMatches:
            return
        self._set_context_error(None)

    def _set_context_error(self, message: str | None) -> None:
        try:
            error = self.query_one("#models-context-error", Static)
        except NoMatches:
            return
        error.update(message or "")
        error.display = bool(message)

    def _apply_context(self, tokens: int) -> None:
        self._applied_context = tokens
        self._set_context_widgets(tokens)
        self.reassess(context=tokens)

    @property
    def applied_context(self) -> int:
        """The last valid context used by an Installed assessment."""
        return self._applied_context or self.tui.config.max_ctx

    def _refresh_hardware(self) -> None:
        hardware = local_hardware()
        try:
            self.query_one("#models-chip", Static).update(
                hardware.chip or "unavailable"
            )
            self.query_one("#models-unified-memory", Static).update(
                self._format_bytes(hardware.total_bytes)
            )
            self.query_one("#models-available-memory", Static).update(
                self._format_bytes(hardware.available_bytes)
            )
            self.query_one("#models-assessment-budget", Static).update(
                self._format_bytes(stable_memory_budget_bytes(hardware))
            )
            self.query_one("#models-working-set", Static).update(
                self._format_bytes(hardware.recommended_working_set_bytes)
            )
        except NoMatches:
            return
        self._show_selected_details()

    @staticmethod
    def _format_bytes(value: int | None) -> str:
        return f"{value / 2**30:.1f} GiB" if value is not None else "unavailable"

    def rescan(self) -> None:
        context = self._context_tokens()
        self._assessment_generation += 1
        generation = self._assessment_generation
        self._assessment_context = context
        self._rescan(context, generation)

    def reassess(self, *, context: int | None = None) -> None:
        """Recompute cached facts after scenario or runtime changes."""
        if context is None:
            draft = self._draft_context_tokens()
            context = self.applied_context if draft is None else draft
            if context != self.applied_context:
                self._applied_context = context
                self._set_context_widgets(context)
        self._assessment_generation += 1
        generation = self._assessment_generation
        self._assessment_context = context
        self._reassess_cached(list(self.rows), context, generation)

    def _context_tokens(self) -> int:
        return self.applied_context

    def _draft_context_tokens(self) -> int | None:
        try:
            result = validate_custom_context(
                self.query_one("#models-context", Input).value
            )
        except NoMatches:
            return None
        return result.tokens

    def invalidate_facts(self, repo_id: str) -> None:
        with self._facts_lock:
            self._facts_cache = {
                key: facts
                for key, facts in self._facts_cache.items()
                if key[0] != repo_id
            }

    @work(exclusive=True, group="rescan", thread=True)
    def _rescan(self, context: int, generation: int) -> None:
        try:
            rows = scan_models()
            assessments, revisions, failures, runtime_key = self._assess_rows(
                rows, context
            )
        except Exception as exc:
            # Keep the current rows; a failed scan must not blank the table.
            self.tui.call_from_thread(self._rescan_failed, generation, exc)
            return
        # Widget has no call_from_thread in textual 8.2.8 — hop via App.
        self.tui.call_from_thread(
            self._populate,
            rows,
            assessments,
            revisions,
            failures,
            generation,
            context,
            runtime_key,
        )

    @work(exclusive=True, group="rescan", thread=True)
    def _reassess_cached(
        self, rows: list[ModelRow], context: int, generation: int
    ) -> None:
        assessments, revisions, failures, runtime_key = self._assess_rows(
            rows, context, allow_metadata_io=False
        )
        self.tui.call_from_thread(
            self._apply_assessments,
            assessments,
            revisions,
            failures,
            generation,
            context,
            runtime_key,
        )

    def _assess_rows(
        self,
        rows: list[ModelRow],
        context: int,
        *,
        allow_metadata_io: bool = True,
    ) -> tuple[
        dict[str, ModelAssessment],
        dict[str, str],
        dict[str, str],
        tuple[object, ...],
    ]:
        hardware = local_hardware()
        manager = self.tui.managed_runtime
        if (
            self.tui.config.runtime_mode == "managed"
            and manager is not None
            and not manager.install_evidence
        ):
            try:
                manager.inspect()
            except Exception:
                # The assessment remains useful locally; compatibility stays
                # explicitly unverified until a later reinspection succeeds.
                pass
        runtime = runtime_capabilities(
            self.tui.config.runtime_mode,
            managed_verified=bool(manager and manager.install_evidence),
        )
        assessments: dict[str, ModelAssessment] = {}
        revisions: dict[str, str] = {}
        failures: dict[str, str] = {}
        for row in rows:
            if not row.revision_hashes:
                failures[row.repo_id] = "no cached revision is available"
                continue
            revision = row.revision_hashes[-1]
            revisions[row.repo_id] = revision
            try:
                key = (row.repo_id, revision)
                with self._facts_lock:
                    facts = self._facts_cache.get(key)
                if facts is None and not allow_metadata_io:
                    failures[row.repo_id] = "model metadata is not cached"
                    continue
                if facts is None:
                    path = resolve_cached_snapshot(row.repo_id, revision)
                    files = tuple(
                        (str(file.relative_to(path)), file.stat().st_size)
                        for file in path.rglob("*")
                        if file.is_file()
                    )
                    facts = facts_from_metadata(
                        row.repo_id,
                        RepoSnapshot(revision, files),
                        read_cached_config(path),
                        weight_index=read_cached_index(path),
                        files_complete=False,
                    )
                    with self._facts_lock:
                        self._facts_cache[key] = facts
                assessments[row.repo_id] = assess(
                    facts, hardware, runtime, AssessmentScenario(context)
                )
            except Exception as exc:
                failures[row.repo_id] = f"{exc.__class__.__name__}: {exc}"
        runtime_key = self._runtime_key(runtime)
        return assessments, revisions, failures, runtime_key

    def _runtime_key(self, runtime: object) -> tuple[object, ...]:
        capabilities = cast(Any, runtime)
        return (
            capabilities.identity,
            capabilities.verified,
            tuple(sorted(capabilities.architectures)),
            tuple(sorted(capabilities.unsupported_architectures)),
        )

    def _rescan_failed(self, generation: int, exc: Exception) -> None:
        if generation != self._assessment_generation:
            return
        try:
            self.tui.log_error_once("rescan", exc)
        except NoMatches:
            pass

    def _populate(  # noqa: PLR0913, PLR0917
        self,
        rows: list[ModelRow],
        assessments: dict[str, ModelAssessment] | None = None,
        revisions: dict[str, str] | None = None,
        failures: dict[str, str] | None = None,
        generation: int | None = None,
        context: int | None = None,
        runtime_key: tuple[object, ...] | None = None,
    ) -> None:
        if generation is not None and not self._assessment_is_current(
            generation, context, runtime_key
        ):
            return
        self.tui.clear_error("rescan")
        try:
            table = self.query_one("#models-table", ModelsTable)
        except NoMatches:
            # A rescan landing during shutdown has no table left to fill.
            return
        table.set_rows(
            rows,
            selected_model=self.tui.effective_model(),
            assessments=assessments,
        )
        self.rows = rows
        active_keys = {
            (row.repo_id, revision) for row in rows for revision in row.revision_hashes
        }
        with self._facts_lock:
            self._facts_cache = {
                key: facts
                for key, facts in self._facts_cache.items()
                if key in active_keys
            }
        self.assessments = assessments or {}
        self._assessment_revisions = revisions or {}
        self._assessment_failures = failures or {}
        self._show_selected_details()
        self._refresh_hardware()

    def _apply_assessments(  # noqa: PLR0913, PLR0917
        self,
        assessments: dict[str, ModelAssessment],
        revisions: dict[str, str],
        failures: dict[str, str],
        generation: int,
        context: int,
        runtime_key: tuple[object, ...],
    ) -> None:
        if not self._assessment_is_current(generation, context, runtime_key):
            return
        self.assessments = assessments
        self._assessment_revisions = revisions
        self._assessment_failures = failures
        try:
            table = self.query_one("#models-table", ModelsTable)
        except NoMatches:
            return
        table.set_rows(
            self.rows,
            selected_model=self.tui.effective_model(),
            assessments=assessments,
        )
        self._show_selected_details()

    def _assessment_is_current(
        self,
        generation: int,
        context: int | None,
        runtime_key: tuple[object, ...] | None,
    ) -> bool:
        if (
            generation != self._assessment_generation
            or context != self._assessment_context
        ):
            return False
        if runtime_key is None:
            return True
        manager = self.tui.managed_runtime
        current = runtime_capabilities(
            self.tui.config.runtime_mode,
            managed_verified=bool(manager and manager.install_evidence),
        )
        return self._runtime_key(current) == runtime_key

    @on(DataTable.RowHighlighted, "#models-table")
    def _row_highlighted(self) -> None:
        self._show_selected_details()

    @on(Input.Submitted, "#models-context")
    def _context_changed(self, event: Input.Submitted) -> None:
        result = validate_custom_context(event.value)
        if result.error is not None:
            self._set_context_error(result.error)
            return
        assert result.tokens is not None
        self._apply_context(result.tokens)

    @on(Select.Changed, "#models-context-preset")
    def _context_preset_changed(self, event: Select.Changed) -> None:
        label = event.value
        try:
            current = self.query_one("#models-context-preset", Select).value
        except NoMatches:
            return
        # A programmatic value sync can leave an older Changed event queued.
        # Only the value still shown by the control represents a user choice.
        if current != label:
            return
        if label == CUSTOM_CONTEXT_LABEL:
            self._set_context_error(None)
            try:
                self.query_one("#models-context", Input).focus()
            except NoMatches:
                pass
            return
        if not isinstance(label, str):
            return
        tokens = context_tokens_from_preset(label)
        if tokens is not None:
            self._apply_context(tokens)

    @on(Button.Pressed, "#models-discover")
    def _open_discover(self) -> None:
        self.open_discover()

    def open_discover(self) -> None:
        """Replace only the right-side Installed content with a fresh pane."""
        if self._discover_pane is not None:
            self._discover_pane.focus_search()
            return
        installed = self.query_one("#models-installed-view", Vertical)
        installed.display = False
        for selector in (
            "#models-discover",
            "#models-context-heading",
            "#models-context-row",
            "#models-context-help",
            "#models-context-error",
        ):
            self.query_one(selector).display = False
        pane = DiscoverPane(host=self, id="models-discover-pane")
        self._discover_pane = pane
        self.query_one("#models-main", Vertical).mount(pane)

    def discover_return(self, pane: DiscoverPane) -> None:
        if pane is self._discover_pane:
            self._close_discover(pane)

    def discover_complete(
        self, pane: DiscoverPane, outcome: str, repo_id: str | None
    ) -> None:
        if pane is not self._discover_pane:
            return
        if outcome in {"success", "cancelled"}:
            if repo_id is not None:
                self.invalidate_facts(repo_id)
            self.rescan()
            self._close_discover(pane)

    def _close_discover(self, pane: DiscoverPane) -> None:
        if pane is not self._discover_pane:
            return
        pane.teardown()
        self._discover_pane = None
        pane.remove()
        try:
            installed = self.query_one("#models-installed-view", Vertical)
            installed.display = True
            for selector in (
                "#models-discover",
                "#models-context-heading",
                "#models-context-row",
                "#models-context-help",
                "#models-context-error",
            ):
                self.query_one(selector).display = True
            self.query_one("#models-table", ModelsTable).focus()
        except NoMatches:
            return

    @on(Button.Pressed, "#models-details-more")
    def _open_details_preview(self) -> None:
        if not self._full_details_text:
            return
        from mlx_tui.text_screen import TextPreviewScreen  # noqa: PLC0415

        self.tui.push_screen(
            TextPreviewScreen(
                self._full_details_text,
                title="Model details — read only",
            )
        )

    def _show_selected_details(self) -> None:
        try:
            table = self.query_one("#models-table", ModelsTable)
            details = self.query_one("#models-details", Static)
            more = self.query_one("#models-details-more", Button)
        except NoMatches:
            return
        if not 0 <= table.cursor_row < len(self.rows):
            self._details_text = "Select a model for assessment details"
            self._full_details_text = self._details_text
            details.update(self._details_text)
            more.disabled = True
            return
        row = self.rows[table.cursor_row]
        assessment = self.assessments.get(row.repo_id)
        revision = self._assessment_revisions.get(row.repo_id)
        with self._facts_lock:
            facts = self._facts_cache.get((row.repo_id, revision)) if revision else None
        lines = [
            f"Model: {row.repo_id}",
            f"Revision: {revision or 'unresolved'}",
            f"Architecture: {facts.architecture if facts else 'unknown'}",
            "Quantization: "
            + (
                facts.quantization
                if facts is not None and facts.quantization
                else f"{row.quant} name hint"
            ),
            "Weights: "
            + (
                self._format_bytes(facts.weight_bytes)
                if facts is not None
                else "unavailable"
            ),
            f"Cache disk: {self._format_bytes(row.size_on_disk)}",
            f"Estimate context: {self.applied_context:,} tokens",
        ]
        if assessment is None:
            failure = self._assessment_failures.get(row.repo_id, "metadata unavailable")
            lines.extend(
                [
                    f"Compatibility: unavailable — {failure}",
                    "Memory fit: unavailable — assessment unavailable",
                    "Memory estimate: unavailable",
                    "Current available memory: unavailable",
                ]
            )
            self._details_text = "\n".join(lines)
            self._full_details_text = self._details_text
            details.update(self._details_text)
            more.disabled = True
            return
        lines.extend(
            [
                f"Compatibility: {assessment.compatibility.value} — "
                f"{assessment.compatibility_reason}",
                f"Memory fit: {assessment.fit.value} — {assessment.memory_fit_reason}",
                "Memory estimate: " + self._format_bytes(assessment.peak_bytes),
                "Weights component: " + self._format_bytes(assessment.weight_bytes),
                "KV cache component: " + self._format_bytes(assessment.kv_bytes),
                "Runtime allowance: " + self._format_bytes(assessment.allowance_bytes),
                "Stable budget: " + self._format_bytes(assessment.budget_bytes),
            ]
        )
        available = local_hardware().available_bytes
        current_pressure = self._format_bytes(available)
        if assessment.peak_bytes is None:
            pressure = "not comparable without a memory estimate"
        elif assessment.available_now is True:
            pressure = "within current available memory"
        elif assessment.available_now is False:
            pressure = "below the current estimate"
        else:
            pressure = "unavailable"
        lines.append(f"Current available memory: {current_pressure} — {pressure}")
        self._details_text = "\n".join(lines)
        self._full_details_text = "\n".join(
            [
                *lines,
                "",
                "Assumptions:",
                *[f"- {item}" for item in assessment.assumptions],
            ]
        )
        details.update(self._details_text)
        more.disabled = False

    def refresh_markers(self) -> None:
        self._refresh_hardware()
        if not self.rows:
            return
        try:
            table = self.query_one("#models-table", ModelsTable)
        except NoMatches:
            return
        table.refresh_markers(
            self.rows,
            selected_model=self.tui.effective_model(),
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

    def _reject_if_busy(self) -> bool:
        current = self.tui.operations.current
        if current is OperationKind.IDLE:
            return False
        self.tui.log_app(f"{current.value} operation already in progress", "yellow")
        return True

    def request_load_swap(self) -> None:
        table = self.query_one("#models-table", ModelsTable)
        if not self.rows or not 0 <= table.cursor_row < len(self.rows):
            self.tui.log_app("no model selected to load", "dim")
            return
        row = self.rows[table.cursor_row]
        assessment = self.assessments.get(row.repo_id)
        if assessment and assessment.compatibility is Compatibility.DIFFERENT_RUNTIME:
            self.tui.log_app(
                f"cannot load {row.repo_id}: {assessment.compatibility_reason}", "red"
            )
            return
        if assessment is None or assessment.compatibility is Compatibility.UNKNOWN:
            self.tui.log_app(
                f"compatibility for {row.repo_id} is unknown; load may fail", "yellow"
            )
        status_state = self.tui.status_state
        policy = self.tui.config.swap_policy
        has_start = bool(self.tui.config.start_cmd)
        has_stop = bool(self.tui.config.stop_cmd)
        action = resolve_swap_action(status_state, policy, has_start, has_stop)
        if action == "warm":
            self._start_warm(row)
        elif action == "restart":
            self._start_boot(row, stop_first=True)
        elif action == "cold":
            self._start_boot(row, stop_first=False)
        else:
            if status_state == "amber":
                reason = (
                    "endpoint is amber (unexpected service on port) — refusing swap"
                )
            elif policy == "warm":
                reason = (
                    'swap_policy is "warm" but endpoint is not green (requires green)'
                )
            elif policy == "restart":
                reason = 'swap_policy is "restart" but start_cmd/stop_cmd are not both configured'
            else:
                reason = "server unreachable and no start_cmd configured"
            self.tui.log_app(f"cannot load {row.repo_id}: {reason}", "red")

    def _start_warm(self, row: ModelRow) -> None:
        if self._reject_if_busy():
            return
        if not self.tui.operations.try_acquire(OperationKind.LOADING):
            self._reject_if_busy()
            return
        try:
            self.tui.select_model(row.repo_id)
            self.tui.set_operation_ui(True)
            self.tui.log_app(f"requesting {row.repo_id} (in-server generation)…")
            self.run_warm_swap(row)
        except Exception:
            self.tui.operations.release(OperationKind.LOADING)
            self.tui.set_operation_ui(False)
            raise

    def _start_boot(self, row: ModelRow, *, stop_first: bool) -> None:
        if self.tui.operations.current is OperationKind.CHATTING:
            self.tui.cancel_chat_for_swap()
            return
        if self._reject_if_busy():
            return
        if self.tui.config.runtime_mode == "managed":
            self._start_managed(row)
            return
        if not self.tui.operations.try_acquire(OperationKind.RESTARTING):
            self._reject_if_busy()
            return
        try:
            self.tui.select_model(row.repo_id)
            self.tui.set_operation_ui(True)
            self.run_boot(boot_plan_for(row, stop_first=stop_first))
        except Exception:
            self.tui.operations.release(OperationKind.RESTARTING)
            self.tui.set_operation_ui(False)
            raise

    def _start_managed(self, row: ModelRow) -> None:
        if not self.tui.operations.try_acquire(OperationKind.RESTARTING):
            self._reject_if_busy()
            return
        try:
            snapshot = resolve_cached_snapshot(row.repo_id, row.revision_hashes[-1])
            self.tui.select_model(str(snapshot))
            self.tui.set_operation_ui(True)
            self.run_managed_boot(snapshot, row.repo_id)
        except Exception as exc:
            self.tui.operations.release(OperationKind.RESTARTING)
            self.tui.set_operation_ui(False)
            self.tui.log_app(f"managed load failed: {exc}", "red")

    @work(exclusive=True, group="swap", thread=True)
    def run_managed_boot(self, snapshot: Path, model_label: str) -> None:
        def stream(line: str) -> None:
            try:
                self.tui.call_from_thread(self.tui.log_app, f"[managed] {line}")
            except Exception:
                pass

        try:
            probe = self.tui.start_managed(snapshot, on_line=stream)
        except Exception as exc:
            self.tui.call_from_thread(
                self.tui.record_generation_failure,
            )
            self.tui.call_from_thread(
                self.tui.log_app, f"managed load failed: {exc}", "red"
            )
        else:
            self.tui.call_from_thread(
                self.tui.update_server_identity,
                probe,
                self.tui.managed_runtime.identity if self.tui.managed_runtime else None,
            )
            self.tui.call_from_thread(
                self.tui.log_app,
                f"✓ managed server ready for {model_label}; residency unknown",
            )
            self.tui.call_from_thread(self.tui.refresh_models)
            self.tui.call_from_thread(self.tui.reassess_models)
        finally:
            self.tui.call_from_thread(
                self.tui.operations.release, OperationKind.RESTARTING
            )
            self.tui.call_from_thread(self.tui.set_operation_ui, False)

    @work(exclusive=True, group="swap", thread=True)
    def run_warm_swap(self, row: ModelRow) -> None:
        try:
            full_timeout = health_timeout(row.size_on_disk)
            rendered = f"[{self.tui.host}]" if ":" in self.tui.host else self.tui.host
            response_model = serverctl.warm_load(
                f"http://{rendered}:{self.tui.port}/v1/chat/completions",
                row.repo_id,
                timeout_s=full_timeout,
            )
            if response_model != row.repo_id:
                self.tui.call_from_thread(self.tui.record_generation_failure)
                self.tui.call_from_thread(
                    self.tui.log_app,
                    f"request unverified: server reports model {response_model!r}, "
                    f"expected {row.repo_id!r}",
                    "red",
                )
                return
            probe = ServerProbe(state="green", model_id=response_model)
            proc_ident = process.find_server_process(self.tui.host, self.tui.port)
            self.tui.call_from_thread(
                self.tui.update_server_identity, probe, proc_ident
            )
            self.tui.call_from_thread(
                self.tui.log_app,
                f"✓ request succeeded for {row.repo_id}; residency unknown",
            )
            self.tui.call_from_thread(self.tui.refresh_models)
            self.tui.call_from_thread(self.tui._refresh_metrics)
        except Exception as exc:
            self.tui.call_from_thread(self.tui.record_generation_failure)
            detail = f"{exc.__class__.__name__}: {exc}"[:200]
            self.tui.call_from_thread(
                self.tui.log_app, f"request failed: {detail}", "red"
            )
        finally:
            self.tui.call_from_thread(
                self.tui.operations.release, OperationKind.LOADING
            )
            self.tui.call_from_thread(self.tui.set_operation_ui, False)

    @work(exclusive=True, group="swap", thread=True)
    def run_boot(self, plan: BootPlan) -> None:  # noqa: PLR0915
        def stream(line: str) -> None:
            try:
                self.tui.call_from_thread(self.tui.log_app, f"[swap] {line}")
            except Exception:
                pass

        def finish_ui() -> None:
            try:
                self.tui.operations.release(OperationKind.RESTARTING)
            finally:
                try:
                    self.tui.set_operation_ui(False)
                except NoMatches:
                    pass

        try:
            cfg = self.tui.config
            host = self.tui.host
            port = self.tui.port
            if plan.stop_first:
                # Keep the existing pre-stop refresh: the server identity may be
                # stale while the configured stop command is doing its work.
                try:
                    self.tui.call_from_thread(self.tui.refresh_models)
                except Exception:
                    pass
            probe = execute_boot(
                plan,
                cfg,
                host=host,
                port=port,
                on_line=stream,
                on_tick=lambda seconds: self._progress_line(
                    plan.model_id or "server", seconds
                ),
            )
        except Exception as exc:
            try:
                self.tui.call_from_thread(self.tui.record_generation_failure)
            except Exception:
                pass
            detail = f"{exc.__class__.__name__}: {exc}"[:240]
            try:
                self.tui.call_from_thread(self.tui.log_app, detail, "red")
            except Exception:
                pass
        else:
            try:
                proc_ident = process.find_server_process(host, port)
                self.tui.call_from_thread(
                    self.tui.update_server_identity, probe, proc_ident
                )
                self.tui.call_from_thread(self.tui.log_app, plan.success_line)
                self.tui.call_from_thread(self.tui.refresh_models)
                self.tui.call_from_thread(self.tui._refresh_metrics)
            except Exception as exc:
                detail = f"[swap] UI update failed: {exc}"[:240]
                try:
                    self.tui.call_from_thread(self.tui.log_app, detail, "red")
                except Exception:
                    pass
        finally:
            try:
                self.tui.call_from_thread(finish_ui)
            except Exception:
                try:
                    self.tui.operations.release(OperationKind.RESTARTING)
                except Exception:
                    pass

    def request_delete_model(self) -> None:
        if self.tui.operations.is_busy:
            self.tui.log_app("operation already in progress", "yellow")
            return
        table = self.query_one("#models-table", ModelsTable)
        if not self.rows or not 0 <= table.cursor_row < len(self.rows):
            self.tui.log_app("no model selected to delete", "dim")
            return
        row = self.rows[table.cursor_row]
        protected = {
            model
            for model in (
                self.tui.server_identity.selected_model,
                self.tui.server_identity.last_response_model,
            )
            if model is not None
        }
        if any(model_identity_matches(row.repo_id, model) for model in protected):
            self.tui.log_app(
                f"cannot delete {row.repo_id}: selected or last observed; "
                "external use is unknown — select and verify another model first",
                "yellow",
            )
            return
        # The modal blocks interaction, so the selection cannot move before
        # the callback fires; stash the row there for _on_delete_confirmed.
        self._pending_delete_row = row
        self.app.push_screen(
            ConfirmScreen(
                f"delete {row.repo_id} ({row.size_on_disk / 2**30:.1f} GiB)? y/n"
            ),
            self._on_delete_confirmed,
        )

    def _on_delete_confirmed(self, confirmed: bool | None) -> None:
        row = self._pending_delete_row
        self._pending_delete_row = None
        if not confirmed or row is None:
            self.tui.log_app("kept", "dim")
            return
        self.start_delete(row)

    def start_delete(self, row: ModelRow) -> None:
        if not self.tui.operations.try_acquire(OperationKind.DELETING):
            self.tui.log_app("operation already in progress", "yellow")
            return
        try:
            self.tui.set_operation_ui(True)
            self._run_delete(row)
        except Exception:
            self.tui.operations.release(OperationKind.DELETING)
            self.tui.set_operation_ui(False)
            raise

    @work(exclusive=True, group="delete", thread=True)
    def _run_delete(self, row: ModelRow) -> None:
        try:
            # Race: the loaded model may have changed between confirmation and
            # worker execution. Re-check on the UI thread immediately before
            # touching the cache.
            identity = self.tui.call_from_thread(lambda: self.tui.server_identity)
            if any(
                model_identity_matches(row.repo_id, model)
                for model in (identity.selected_model, identity.last_response_model)
            ):
                self.tui.call_from_thread(
                    self.tui.log_app,
                    f"cannot delete {row.repo_id}: now selected or last observed; "
                    "external use is unknown",
                    "yellow",
                )
                return
            freed = delete_repos(row.revision_hashes)
        except (CacheNotFound, OSError) as exc:
            self.tui.call_from_thread(
                self.tui.log_app,
                f"delete failed: {exc.__class__.__name__}: {exc}",
                "red",
            )
        except Exception as exc:
            self.tui.call_from_thread(
                self.tui.log_app,
                f"delete failed: {exc.__class__.__name__}: {exc}"[:240],
                "red",
            )
        else:
            self.tui.call_from_thread(
                self.tui.log_app,
                f"deleted {row.repo_id} — freed {freed / 2**30:.1f} GiB",
            )
            self.tui.call_from_thread(self.invalidate_facts, row.repo_id)
            self.tui.call_from_thread(self.rescan)
        finally:
            self.tui.call_from_thread(
                self.tui.operations.release, OperationKind.DELETING
            )
            self.tui.call_from_thread(self.tui.set_operation_ui, False)
