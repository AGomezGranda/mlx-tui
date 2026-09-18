"""Compare tab pane: pinned coding profile comparison workflow."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast, override

from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import (
    Button,
    Collapsible,
    DataTable,
    Input,
    Label,
    Select,
    Static,
)
from textual.worker import Worker, WorkerError

from mlx_tui.comparison.contracts import (
    _PROFILE_COUNT,
    ComparisonInput,
    ComparisonResult,
    DecisionKind,
    JSONValue,
)
from mlx_tui.config import AppConfig

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp

import mlx_tui.compare.decisions as _compare_decisions
import mlx_tui.compare.render as _compare_render
import mlx_tui.compare.workflow as _compare_workflow

_NARROW_WIDTH = 90


class ComparePane(VerticalScroll):
    """Owns the comparison setup/run/review/choose/reuse workflow."""

    DEFAULT_CSS = """
    ComparePane { height: 1fr; padding: 1 2; }
    ComparePane .section {
        height: auto; padding: 1 2; margin-bottom: 1;
        border: round $panel; background: $surface;
    }
    ComparePane .heading { text-style: bold; color: $accent; margin-bottom: 1; }
    ComparePane .muted { color: $text-muted; height: auto; }
    ComparePane .candidate { width: 1fr; height: auto; padding: 0 1; }
    ComparePane .candidate Label { text-style: bold; }
    ComparePane .candidate Static { height: auto; color: $text-muted; }
    ComparePane .row { height: auto; margin-top: 1; }
    ComparePane .row Button { width: 1fr; min-width: 0; margin-right: 1; }
    ComparePane .row Button:last-child { margin-right: 0; }
    ComparePane Input, ComparePane Select { width: 1fr; }
    ComparePane Static { height: auto; }
    ComparePane Collapsible { height: auto; margin-top: 1; padding: 0; }
    ComparePane Collapsible Input { margin-bottom: 1; }
    #comparison-setup-details { margin-top: 1; }
    #comparison-progress { margin-top: 1; }
    #comparison-result-header, #comparison-latency, #comparison-memory,
    #comparison-choice-help, #comparison-saved { margin-bottom: 1; }
    #comparison-measurements { height: 8; margin-bottom: 1; }
    #comparison-trials { height: 13; }
    #comparison-results { height: auto; }
    #comparison-decision { height: auto; margin-top: 1; }
    ComparePane.narrow { padding: 1; }
    ComparePane.narrow .section { padding: 1; }
    ComparePane.narrow .row { layout: vertical; }
    ComparePane.narrow .row Button { width: 100%; margin: 0 0 1 0; }
    ComparePane.narrow .candidate { width: 100%; margin-bottom: 1; }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._preflight_input: ComparisonInput | None = None
        self._comparison_worker: Worker[None] | None = None
        self._comparison_task: asyncio.Task[ComparisonResult] | None = None
        self._comparison_active = False
        self._run_started = False
        self._cancel_requested = False
        self._selected_trial: tuple[str, int] | None = None

    @property
    def tui(self) -> MlxTuiApp:
        return cast("MlxTuiApp", self.app)

    @override
    def compose(self) -> ComposeResult:  # noqa: PLR0915
        options = [
            (entry.profile.name or entry.profile.id, entry.profile.id)
            for entry in self.tui.profile_entries
        ]
        ids = self.tui.comparison_profile_ids
        with Vertical(id="comparison-controls", classes="section"):
            yield Label("01  Choose profiles", classes="heading")
            yield Static(
                "Compare the same coding task with two pinned profiles. "
                "Each runs once, then repeats five times.",
                classes="muted",
            )
            with Horizontal(classes="row", id="comparison-candidates"):
                for slot, name in enumerate(("a", "b")):
                    with Vertical(classes="candidate"):
                        yield Label(f"Candidate {name.upper()}")
                        yield Select(
                            options,
                            value=ids[slot]
                            if len(ids) == _PROFILE_COUNT
                            else Select.BLANK,
                            prompt=f"Choose candidate {name.upper()}",
                            id=f"comparison-profile-{name}",
                        )
                        yield Static("", id=f"comparison-candidate-{name}-status")
            yield Static("", id="comparison-setup-details", classes="muted")
            with Collapsible(title="Task and exact settings", id="compare-task"):
                yield Static("", id="comparison-task-details")
            with Collapsible(
                title="Test conditions and evidence (optional)", id="compare-evidence"
            ):
                yield Static(
                    "Leave unknown values blank. Missing evidence allows a run, "
                    "but prevents an advantage recommendation.",
                    classes="muted",
                )
                for label, field, hint in (
                    ("Machine tier", "machine-tier", "Machine tier or unknown"),
                    ("Runtime", "runtime", "Runtime version and evidence"),
                    ("Installation", "install", "Install command or source"),
                    ("Server launch", "launch", "Launch command and flags"),
                    ("Test conditions", "conditions", "Other workloads or conditions"),
                    ("Isolation", "isolation", "Restart or eviction evidence"),
                ):
                    yield Label(label)
                    yield Input(placeholder=hint, id=f"comparison-{field}")
                yield Static("", id="comparison-evidence-details")
                yield Static("", id="comparison-details")
            with Horizontal(id="comparison-buttons", classes="row"):
                yield Button("Check readiness", id="comparison-preflight")
                yield Button("Run comparison", variant="primary", id="comparison-run")
                yield Button("Cancel", id="comparison-cancel", disabled=True)
            yield Static(
                "Check readiness to verify the profiles and attached server.",
                id="comparison-progress",
            )
            with Collapsible(title="Need models or a server?", id="compare-recovery"):
                with Horizontal(classes="row"):
                    yield Button("Download models", id="comparison-download")
                    yield Button("Start server", id="comparison-restart")
        with Vertical(classes="section"):
            yield Label("02  Review results", classes="heading")
            yield Static(
                "No comparison yet. Choose two profiles and run a comparison, "
                "or open a saved result below.",
                id="comparison-empty",
                classes="muted",
            )
            with Vertical(id="comparison-results"):
                yield Static("", id="comparison-result-header")
                yield DataTable(id="comparison-measurements", cursor_type="row")
                yield Static(
                    "Times are in seconds. Repeat statistics use passing repeats only.",
                    classes="muted",
                )
                yield Static("", id="comparison-latency")
                yield Static("", id="comparison-memory")
                with Collapsible(
                    title="Individual trials and failures", id="compare-trials-wrap"
                ):
                    yield DataTable(id="comparison-trials", cursor_type="row")
                    yield Static("No trial selected", id="comparison-trial-detail")
                with Vertical(id="comparison-decision"):
                    yield Label("Choose what to keep", classes="heading")
                    yield Static(
                        "Keep A or B saves and applies that tested profile. "
                        "Other decisions leave current settings unchanged. "
                        "Any decision replaces the saved choice.",
                        id="comparison-choice-help",
                        classes="muted",
                    )
                    yield Label("Decision reason (optional)")
                    yield Input(placeholder="Why this choice?", id="comparison-reason")
                    with Horizontal(id="choice-buttons", classes="row"):
                        yield Button("Keep A", id="comparison-keep-a")
                        yield Button("Keep B", id="comparison-keep-b")
                    with Horizontal(classes="row"):
                        yield Button("Keep current settings", id="comparison-retain")
                        yield Button("Reject both", id="comparison-reject")
        with Vertical(classes="section"):
            yield Label("Saved choice", classes="heading")
            yield Static("", id="comparison-saved")
            with Horizontal(classes="row"):
                yield Button("Apply saved profile", id="comparison-use-saved")
                yield Button("Go to Chat", id="comparison-go-chat")
            with Collapsible(title="Open saved result", id="compare-open"):
                yield Label("Result file")
                yield Input(
                    placeholder="~/.../comparison.json", id="comparison-open-path"
                )
                yield Button("Open result", id="comparison-open-result")
                yield Static("", id="comparison-open-status")

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.width < _NARROW_WIDTH, "narrow")

    def on_mount(self) -> None:
        try:
            measurements = self.query_one("#comparison-measurements", DataTable)
            measurements.add_column("Measurement", key="measurement")
            measurements.add_column("Candidate A", key="a")
            measurements.add_column("Candidate B", key="b")
        except NoMatches:
            pass
        try:
            trials = self.query_one("#comparison-trials", DataTable)
            trials.add_column("profile", key="profile")
            trials.add_column("trial", key="trial")
            trials.add_column("state", key="state")
            trials.add_column("quality", key="quality")
            trials.add_column("total", key="total")
        except NoMatches:
            pass
        self.refresh_profile_state()
        result = self.tui.last_comparison
        if result is not None:
            self._render_result(result)
        self._update_controls()

    @property
    def has_live_comparison(self) -> bool:
        return self._comparison_active

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    def abort(self) -> None:
        if not self._comparison_active or self._cancel_requested:
            return
        self._cancel_requested = True
        self._set_text("#comparison-progress", "cancellation requested", "yellow")
        self.tui.refresh_activity()
        self._update_controls()
        if not self._run_started:
            return
        if self._comparison_task is not None:
            self._comparison_task.cancel()

    async def wait_for_cleanup(self) -> None:
        worker = self._comparison_worker
        if worker is None:
            return
        try:
            await worker.wait()
        except WorkerError:
            pass

    def set_comparison_busy(self) -> None:
        # The coordinator owns busy state; readiness, result status, and
        # saved-choice availability decide which controls stay enabled.
        self._update_controls()

    @on(Button.Pressed, "#comparison-cancel")
    def _cancel(self) -> None:
        self.abort()

    def _set_text(self, selector: str, message: str, style: str | None = None) -> None:
        return _compare_render._set_text(self, selector, message, style)

    def _set_rich_text(self, selector: str, text: Text) -> None:
        return _compare_render._set_rich_text(self, selector, text)

    def _is_busy(self) -> bool:
        return _compare_render._is_busy(self)

    def refresh_profile_state(self) -> None:
        return _compare_render.refresh_profile_state(self)

    def _render_task_details(self) -> None:
        return _compare_render._render_task_details(self)

    def _operator_input_value(self, selector: str) -> str:
        return _compare_render._operator_input_value(self, selector)

    def _render_evidence_details(self) -> None:
        return _compare_render._render_evidence_details(self)

    def _render_saved(self) -> None:
        return _compare_render._render_saved(self)

    @staticmethod
    def _result_text(result: ComparisonResult) -> str:
        return _compare_render._result_text(result)

    def _update_keep_labels(self) -> None:
        return _compare_render._update_keep_labels(self)

    def _update_controls(self) -> None:  # noqa: PLR0912, PLR0915
        return _compare_render._update_controls(self)

    @on(Select.Changed, "#comparison-profile-a, #comparison-profile-b")
    def _candidate_changed(self, event: Select.Changed) -> None:
        return _compare_workflow._candidate_changed(self, event)

    @on(
        Input.Changed,
        "#comparison-machine-tier, #comparison-runtime, #comparison-install, "
        "#comparison-launch, #comparison-conditions, #comparison-isolation",
    )
    def _setup_input_changed(self) -> None:
        return _compare_workflow._setup_input_changed(self)

    def _operator_value(self, selector: str) -> dict[str, JSONValue]:
        return _compare_workflow._operator_value(self, selector)

    def _build_comparison_input(self) -> ComparisonInput:
        return _compare_workflow._build_comparison_input(self)

    @on(Button.Pressed, "#comparison-preflight")
    def _preflight(self) -> None:
        return _compare_workflow._preflight(self)

    @on(Button.Pressed, "#comparison-run")
    def _start_comparison(self) -> None:
        return _compare_workflow._start_comparison(self)

    def _clear_result_display(self) -> None:
        return _compare_render._clear_result_display(self)

    def _render_result(self, result: ComparisonResult) -> None:
        return _compare_render._render_result(self, result)

    def _on_progress(self, result: ComparisonResult) -> None:
        return _compare_workflow._on_progress(self, result)

    @work(exclusive=True, group="comparison")
    async def _run_comparison(
        self,
        comparison_input: ComparisonInput,
        previous_config: AppConfig,
        previous_model: str | None,
    ) -> None:
        return await _compare_workflow._run_comparison(
            self, comparison_input, previous_config, previous_model
        )

    @on(Button.Pressed, "#comparison-download")
    def _download(self) -> None:
        return _compare_workflow._download(self)

    @on(Button.Pressed, "#comparison-restart")
    async def _restart(self) -> None:
        return await _compare_workflow._restart(self)

    def _decision_reason(self) -> str:
        return _compare_decisions._decision_reason(self)

    def _commit_decision(self, decision: DecisionKind, slot: int | None = None) -> None:
        return _compare_decisions._commit_decision(self, decision, slot)

    @on(Button.Pressed, "#comparison-keep-a")
    def _keep_a(self) -> None:
        return _compare_decisions._keep_a(self)

    @on(Button.Pressed, "#comparison-keep-b")
    def _keep_b(self) -> None:
        return _compare_decisions._keep_b(self)

    @on(Button.Pressed, "#comparison-retain")
    def _retain(self) -> None:
        return _compare_decisions._retain(self)

    @on(Button.Pressed, "#comparison-reject")
    def _reject(self) -> None:
        return _compare_decisions._reject(self)

    @on(Button.Pressed, "#comparison-use-saved")
    def _use_saved(self) -> None:
        return _compare_decisions._use_saved(self)

    @work(exclusive=True, group="managed-activation", thread=True)
    def _activate_managed_profile(self, profile_id: str) -> None:
        return _compare_decisions._activate_managed_profile(self, profile_id)

    @on(Button.Pressed, "#comparison-go-chat")
    def _go_chat(self) -> None:
        return _compare_decisions._go_chat(self)

    @on(Button.Pressed, "#comparison-open-result")
    def _open_result(self) -> None:
        return _compare_workflow._open_result(self)

    @on(DataTable.RowSelected, "#comparison-trials")
    def _trial_selected(self, event: DataTable.RowSelected) -> None:
        return _compare_workflow._trial_selected(self, event)
