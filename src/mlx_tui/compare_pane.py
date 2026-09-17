"""Compare tab pane: pinned coding profile comparison workflow."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from pathlib import Path
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
    TabbedContent,
)
from textual.worker import Worker, WorkerError

from mlx_tui import process
from mlx_tui.comparison import (
    CODING_CHECK_EXPECTED,
    CODING_CHECK_PROMPT,
    ComparisonInput,
    ComparisonPersistenceError,
    ComparisonResult,
    ComparisonValidationError,
    DecisionKind,
    JSONValue,
    commit_choice,
    comparison_dir,
    load_comparison,
    parse_loopback_url,
    run_comparison,
    verify_profile_snapshot,
)
from mlx_tui.comparison_presenter import (
    keep_button_labels,
    latency_text,
    measurement_rows,
    memory_text,
    progress_text,
    result_header,
    trial_detail_text,
    trial_table_rows,
)
from mlx_tui.config import AppConfig
from mlx_tui.models import resolve_cached_snapshot
from mlx_tui.operations import OperationKind
from mlx_tui.params import ParamsPane
from mlx_tui.search_screen import SearchScreen

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp

_PROFILE_COUNT = 2
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

    def _set_text(self, selector: str, message: str, style: str | None = None) -> None:
        try:
            widget = self.query_one(selector, Static)
        except NoMatches:
            return
        widget.update(Text(message) if style is None else Text(message, style=style))

    def _set_rich_text(self, selector: str, text: Text) -> None:
        try:
            widget = self.query_one(selector, Static)
        except NoMatches:
            return
        widget.update(text)

    def _is_busy(self) -> bool:
        return self._comparison_active or self.tui.operations.is_busy

    def refresh_profile_state(self) -> None:
        ids = self.tui.comparison_profile_ids
        try:
            if len(ids) == _PROFILE_COUNT:
                self.query_one("#comparison-profile-a", Select).value = ids[0]
                self.query_one("#comparison-profile-b", Select).value = ids[1]
        except NoMatches:
            pass
        entries = self.tui.selected_comparison_profiles()
        try:
            tier_raw = self.query_one("#comparison-machine-tier", Input).value.strip()
        except NoMatches:
            tier_raw = ""
        tier = tier_raw or "unknown"
        catalogue_error = self.tui.profile_error
        if catalogue_error:
            self._set_text(
                "#comparison-setup-details",
                f"catalogue unavailable: {catalogue_error}",
                "red",
            )
            self._set_text("#comparison-details", "", None)
        elif not entries:
            self._set_text(
                "#comparison-setup-details",
                "no pinned profiles available",
                "yellow",
            )
            self._set_text("#comparison-details", "", None)
        else:
            lines: list[str] = []
            for slot, entry in enumerate(entries):
                profile = entry.profile
                try:
                    snapshot = resolve_cached_snapshot(
                        profile.repo_id, profile.revision
                    )
                    cache = f"cached {snapshot}"
                except Exception as exc:
                    cache = f"cache missing: {exc.__class__.__name__}"
                name = "a" if slot == 0 else "b"
                self._set_text(
                    f"#comparison-candidate-{name}-status",
                    f"{profile.repo_id}\n"
                    f"{'Cached locally' if cache.startswith('cached') else 'Download needed'}"
                    f" · evidence {entry.status(tier)}",
                )
                lines.append(
                    f"{profile.name} · {profile.id} · rev {profile.revision[:12]} · "
                    f"{cache} · evidence {entry.status(tier)}"
                )
            if len(entries) == _PROFILE_COUNT:
                lines.append(
                    f"Order: {entries[0].profile.id} then {entries[1].profile.id}"
                )
            details = "\n".join(lines)
            self._set_text(
                "#comparison-setup-details",
                "Runs in order: A → B · fixed coding-check-v1",
            )
            self._set_text("#comparison-details", details)
        self._render_task_details()
        self._render_evidence_details()
        self._render_saved()
        self._update_keep_labels()
        self._update_controls()

    def _render_task_details(self) -> None:
        entries = self.tui.selected_comparison_profiles()
        if not entries:
            self._set_text("#comparison-task-details", "no profiles selected")
            return
        first = entries[0].profile
        lines = [
            f"task {first.id}: {CODING_CHECK_PROMPT}",
            f"expected: {CODING_CHECK_EXPECTED}",
            "generated code is not executed; reproduction only, not general ability",
            f"max_tokens {first.max_tokens} · temp {first.temperature} · "
            f"top_p {first.top_p} · seed {first.seed} · "
            f"thinking {first.enable_thinking}",
            "comparison profiles are not editable here",
        ]
        self._set_text("#comparison-task-details", "\n".join(lines))

    def _operator_input_value(self, selector: str) -> str:
        try:
            return self.query_one(selector, Input).value.strip()
        except NoMatches:
            return ""

    def _render_evidence_details(self) -> None:
        runtime = self._operator_input_value("#comparison-runtime") or "unknown"
        install = self._operator_input_value("#comparison-install") or "unknown"
        launch = self._operator_input_value("#comparison-launch") or "unknown"
        conditions = self._operator_input_value("#comparison-conditions") or "unknown"
        isolation = self._operator_input_value("#comparison-isolation") or "unknown"
        try:
            tier = self._operator_input_value("#comparison-machine-tier") or "unknown"
        except NoMatches:
            tier = "unknown"
        lines = [
            f"machine tier: {tier or 'unknown'}",
            f"runtime: {runtime} · install: {install} · launch: {launch}",
            f"conditions: {conditions} · isolation: {isolation}",
            "operator text is a declaration, not verification; unknown "
            "provenance allows a run but blocks an advantage recommendation",
        ]
        self._set_text("#comparison-evidence-details", "\n".join(lines))

    def _render_saved(self) -> None:
        if self.tui.saved_choice_error is not None:
            text = f"saved choice unavailable: {self.tui.saved_choice_error}"
        elif self.tui.saved_choice is not None:
            choice = self.tui.saved_choice
            text = (
                f"saved {choice.decision}: {choice.profile_id or 'no profile'} · "
                f"run {choice.run_id}"
            )
        else:
            text = "saved choice: none"
        if self.tui.active_profile_id is not None:
            suffix = " (modified)" if self.tui.active_profile_modified else ""
            text += f"\nactive: {self.tui.active_profile_id}{suffix}"
        else:
            text += "\nactive: none"
        result = self.tui.last_comparison
        if result is not None:
            text += f"\nresult: {self._result_text(result)}"
        self._set_text("#comparison-saved", text)

    @staticmethod
    def _result_text(result: ComparisonResult) -> str:
        completed = sum(trial.state == "completed" for trial in result.trials)
        attempted = sum(trial.state != "not_attempted" for trial in result.trials)
        failures = [trial.error for trial in result.trials if trial.error]
        text = f"{result.status} · attempted {attempted}/12 · completed {completed}/12"
        latency = result.summary.get("latency")
        memory = result.summary.get("memory")
        if isinstance(latency, dict):
            text += f" · latency {latency.get('status', 'unknown')}"
            if latency.get("reason"):
                text += f" ({latency['reason']})"
        if isinstance(memory, dict):
            text += f" · memory {memory.get('status', 'unknown')}"
        if failures:
            text += f" · failure: {failures[0]}"
        return text

    def _update_keep_labels(self) -> None:
        keep_a, keep_b = keep_button_labels(self.tui.last_comparison)
        try:
            self.query_one("#comparison-keep-a", Button).label = keep_a
        except NoMatches:
            pass
        try:
            self.query_one("#comparison-keep-b", Button).label = keep_b
        except NoMatches:
            pass

    def _update_controls(self) -> None:  # noqa: PLR0912, PLR0915
        if not self.is_mounted:
            return
        busy = self._is_busy()
        catalogue_broken = self.tui.profile_error is not None
        readiness = self._preflight_input is not None and not busy
        result = self.tui.last_comparison
        decidable = result is not None and result.status == "completed" and not busy
        self.query_one("#comparison-results").display = result is not None
        self.query_one("#comparison-empty").display = result is None
        self.query_one("#comparison-decision").display = (
            result is not None and result.status == "completed"
        )
        saved = self.tui.saved_choice
        applicable = (
            saved is not None
            and saved.decision == "keep"
            and saved.profile_id is not None
            and not busy
        )
        try:
            for selector in (
                "#comparison-profile-a",
                "#comparison-profile-b",
                "#comparison-machine-tier",
                "#comparison-runtime",
                "#comparison-install",
                "#comparison-launch",
                "#comparison-conditions",
                "#comparison-isolation",
            ):
                try:
                    self.query_one(selector, Input | Select).disabled = busy  # type: ignore[type-abstract]
                except NoMatches:
                    pass
        except Exception:
            pass
        # Select widgets need explicit handling because Input|Select union
        # above may miss them on some Textual versions.
        for selector in ("#comparison-profile-a", "#comparison-profile-b"):
            try:
                self.query_one(selector, Select).disabled = busy
            except NoMatches:
                pass
        for selector in (
            "#comparison-machine-tier",
            "#comparison-runtime",
            "#comparison-install",
            "#comparison-launch",
            "#comparison-conditions",
            "#comparison-isolation",
        ):
            try:
                self.query_one(selector, Input).disabled = busy
            except NoMatches:
                pass
        try:
            preflight = self.query_one("#comparison-preflight", Button)
            preflight.disabled = busy or catalogue_broken
        except NoMatches:
            pass
        try:
            run = self.query_one("#comparison-run", Button)
            run.disabled = (not readiness) or catalogue_broken
        except NoMatches:
            pass
        try:
            cancel = self.query_one("#comparison-cancel", Button)
            cancel.disabled = not (
                self._comparison_active and not self._cancel_requested
            )
        except NoMatches:
            pass
        for selector in ("#comparison-download", "#comparison-restart"):
            try:
                self.query_one(selector, Button).disabled = busy
            except NoMatches:
                pass
        for selector in (
            "#comparison-keep-a",
            "#comparison-keep-b",
            "#comparison-retain",
            "#comparison-reject",
        ):
            try:
                self.query_one(selector, Button).disabled = not decidable
            except NoMatches:
                pass
        try:
            self.query_one("#comparison-reason", Input).disabled = busy
        except NoMatches:
            pass
        try:
            self.query_one("#comparison-use-saved", Button).disabled = not applicable
        except NoMatches:
            pass
        try:
            self.query_one("#comparison-go-chat", Button).disabled = False
        except NoMatches:
            pass
        try:
            self.query_one("#comparison-open-path", Input).disabled = busy
        except NoMatches:
            pass
        try:
            self.query_one("#comparison-open-result", Button).disabled = busy
        except NoMatches:
            pass

    @on(Select.Changed, "#comparison-profile-a, #comparison-profile-b")
    def _candidate_changed(self, event: Select.Changed) -> None:
        if not isinstance(event.value, str):
            return
        slot = 0 if event.select.id == "comparison-profile-a" else 1
        self._preflight_input = None
        self.tui.update_comparison_candidate(slot, event.value)
        self._update_controls()

    @on(
        Input.Changed,
        "#comparison-machine-tier, #comparison-runtime, #comparison-install, "
        "#comparison-launch, #comparison-conditions, #comparison-isolation",
    )
    def _setup_input_changed(self) -> None:
        self._preflight_input = None
        self.refresh_profile_state()

    def _operator_value(self, selector: str) -> dict[str, JSONValue]:
        value = self.query_one(selector, Input).value.strip()
        return {"operator": value or "unknown"}

    def _build_comparison_input(self) -> ComparisonInput:
        entries = self.tui.selected_comparison_profiles()
        if (
            len(entries) != _PROFILE_COUNT
            or entries[0].profile.id == entries[1].profile.id
        ):
            raise ComparisonValidationError("select two distinct coding profiles")
        host = self.tui.host
        rendered_host = f"[{host}]" if ":" in host else host
        raw_endpoint = f"http://{rendered_host}:{self.tui.port}/v1/chat/completions"
        endpoint = parse_loopback_url(raw_endpoint).url
        snapshots = tuple(
            resolve_cached_snapshot(entry.profile.repo_id, entry.profile.revision)
            for entry in entries
        )
        hashes = tuple(
            verify_profile_snapshot(entry, snapshot)
            for entry, snapshot in zip(entries, snapshots, strict=True)
        )
        manager = self.tui.managed_runtime
        if self.tui.config.runtime_mode == "managed":
            if (
                manager is None
                or manager.identity is None
                or not manager.child_is_running()
                or not manager.listener_matches_child()
            ):
                raise ComparisonValidationError(
                    "managed server ownership is not verified"
                )
            identity = manager.identity
            runtime_evidence = manager.install_evidence
            install_evidence: dict[str, JSONValue] = {
                "runtime_root": str(manager.root),
                "completion_marker": str(manager.root / ".mlx-tui-runtime.json"),
            }
            launch_evidence: dict[str, JSONValue] = {
                "owned": True,
                "argv": cast(list[JSONValue], list(manager.argv)),
                "host": manager.host,
                "port": manager.port,
            }
            provenance: dict[str, JSONValue] = {
                "source": "managed runtime inspection",
                "offline_hub": manager.environment.get("HF_HUB_OFFLINE") == "1",
                "python_no_user_site": manager.environment.get("PYTHONNOUSERSITE")
                == "1",
            }
            isolation_evidence: dict[str, JSONValue] = {
                "ownership": "TUI-retained child"
            }
        else:
            identity = process.find_server_process(self.tui.host, self.tui.port)
            if identity is None:
                raise ComparisonValidationError(
                    "attached server process identity is unavailable"
                )
            runtime_evidence = self._operator_value("#comparison-runtime")
            install_evidence = self._operator_value("#comparison-install")
            launch_evidence = self._operator_value("#comparison-launch")
            provenance = {"source": "operator preflight"}
            isolation_evidence = self._operator_value("#comparison-isolation")
        try:
            tier = self.query_one("#comparison-machine-tier", Input).value.strip()
        except NoMatches:
            tier = ""
        return ComparisonInput(
            endpoint=endpoint,
            profiles=entries,
            snapshot_paths=snapshots,
            verified_asset_hashes=hashes,
            runtime_evidence=runtime_evidence,
            install_evidence=install_evidence,
            launch_evidence=launch_evidence,
            provenance=provenance,
            process_identity=identity,
            isolation_evidence=isolation_evidence,
            machine_tier=tier or "unknown",
            operator_conditions=self._operator_value("#comparison-conditions"),
            profile_order=(entries[0].profile.id, entries[1].profile.id),
            result_path=comparison_dir() / f"{uuid.uuid4()}.json",
        )

    @on(Button.Pressed, "#comparison-preflight")
    def _preflight(self) -> None:
        if self._is_busy():
            self._set_text(
                "#comparison-progress",
                "comparison or another operation is already running",
                "yellow",
            )
            return
        catalogue_error = self.tui.profile_error
        if catalogue_error is not None:
            self._set_text(
                "#comparison-progress",
                f"catalogue unavailable: {catalogue_error}",
                "red",
            )
            return
        try:
            self._preflight_input = self._build_comparison_input()
        except (OSError, ValueError) as exc:
            self._preflight_input = None
            self._set_text("#comparison-progress", f"readiness failed: {exc}", "red")
            self._update_controls()
            return
        self._set_text(
            "#comparison-progress",
            (
                "ready · managed child identity retained; "
                "readiness does not prove residency"
                if self.tui.config.runtime_mode == "managed"
                else "ready · attached server remains operator-managed; "
                "readiness is not proof the runtime applied all settings"
            ),
        )
        self._update_controls()

    @on(Button.Pressed, "#comparison-run")
    def _start_comparison(self) -> None:
        if self._is_busy():
            self._set_text(
                "#comparison-progress",
                "comparison or another operation is already running",
                "yellow",
            )
            return
        if self._preflight_input is None:
            self._set_text(
                "#comparison-progress", "run Check readiness first", "yellow"
            )
            return
        if not self.tui.operations.try_acquire(OperationKind.COMPARING):
            self.tui.log_app("operation already in progress", "yellow")
            return
        try:
            comparison_input = self._build_comparison_input()
            params = self.tui.query_one(ParamsPane).read_values()
            previous_config = replace(
                self.tui.config,
                temperature=params[0],
                top_p=params[1],
                max_tokens=params[2],
            )
            previous_model = self.tui.effective_model()
            self._cancel_requested = False
            self._run_started = False
            self._comparison_active = True
            self._selected_trial = None
            self._clear_result_display()
            if self.query_one("#comparison-run", Button).has_focus:
                self.focus()
            self.tui.set_operation_ui(True)
            self._update_controls()
            self._comparison_worker = self._run_comparison(
                comparison_input, previous_config, previous_model
            )
        except Exception as exc:
            self._comparison_active = False
            self.tui.operations.release(OperationKind.COMPARING)
            self.tui.set_operation_ui(False)
            self._set_text("#comparison-progress", f"readiness changed: {exc}", "red")
            self._update_controls()

    def _clear_result_display(self) -> None:
        self._set_text("#comparison-result-header", "")
        self._set_text("#comparison-latency", "")
        self._set_text("#comparison-memory", "")
        self._set_text("#comparison-trial-detail", "No trial selected")
        try:
            self.query_one("#comparison-measurements", DataTable).clear()
        except NoMatches:
            pass
        try:
            self.query_one("#comparison-trials", DataTable).clear()
        except NoMatches:
            pass

    def _render_result(self, result: ComparisonResult) -> None:
        self._set_rich_text("#comparison-result-header", result_header(result))
        try:
            table = self.query_one("#comparison-measurements", DataTable)
            table.clear()
            for measurement, left, right in measurement_rows(result):
                table.add_row(measurement.plain, left.plain, right.plain, height=2)
        except NoMatches:
            pass
        self._set_rich_text("#comparison-latency", latency_text(result))
        self._set_rich_text("#comparison-memory", memory_text(result))
        try:
            trials = self.query_one("#comparison-trials", DataTable)
            trials.clear()
            for key, cells in trial_table_rows(result):
                profile, label, state, quality, total = (cell.plain for cell in cells)
                row_key = f"{key[0]}:{key[1]}"
                trials.add_row(profile, label, state, quality, total, key=row_key)
        except NoMatches:
            pass
        if self._selected_trial is not None:
            profile_id, repeat_index = self._selected_trial
            self._set_rich_text(
                "#comparison-trial-detail",
                trial_detail_text(result, profile_id, repeat_index),
            )
        else:
            failures = [t for t in result.trials if t.error or t.quality_pass is False]
            if failures:
                first = failures[0]
                self._set_rich_text(
                    "#comparison-trial-detail",
                    trial_detail_text(result, first.profile_id, first.repeat_index),
                )
            else:
                self._set_text("#comparison-trial-detail", "No trial selected")
        self._set_rich_text("#comparison-progress", progress_text(result))
        self._update_keep_labels()
        self._render_saved()
        self._update_controls()

    def _on_progress(self, result: ComparisonResult) -> None:
        self.tui.last_comparison = result
        self._render_result(result)

    @work(exclusive=True, group="comparison")
    async def _run_comparison(
        self,
        comparison_input: ComparisonInput,
        previous_config: AppConfig,
        previous_model: str | None,
    ) -> None:
        self._run_started = True
        try:
            task = asyncio.create_task(
                run_comparison(comparison_input, on_progress=self._on_progress)
            )
            self._comparison_task = task
            await asyncio.sleep(0)
            if self._cancel_requested:
                task.cancel()
            result = await task
            self.tui.last_comparison = result
            self._on_progress(result)
            if result.status == "failed":
                self.tui.log_app(f"comparison failed: {result.error}", "red")
        except asyncio.CancelledError:
            try:
                self.tui.last_comparison = load_comparison(
                    comparison_input.result_path  # type: ignore[arg-type]
                )
                if self.tui.last_comparison is not None:
                    self._render_result(self.tui.last_comparison)
            except ComparisonPersistenceError:
                pass
            self._set_text(
                "#comparison-progress",
                "cancelled · partial checkpoint retained; engine state unknown",
                "yellow",
            )
        except Exception as exc:
            self._set_text(
                "#comparison-progress",
                f"comparison failed: {exc.__class__.__name__}: {exc}",
                "red",
            )
            self.tui.log_app(f"comparison failed: {exc}", "red")
        finally:
            if self._cancel_requested and self.tui.managed_runtime is not None:
                try:
                    await asyncio.to_thread(self.tui.managed_runtime.stop)
                except Exception as exc:
                    self.tui.log_app(f"managed shutdown failed: {exc}", "red")
            self._comparison_task = None
            self._comparison_worker = None
            self._run_started = False
            self._comparison_active = False
            self._preflight_input = None
            self.tui.restore_request_state(previous_config, previous_model)
            self.tui.operations.release(OperationKind.COMPARING)
            self.tui.set_operation_ui(False)
            await self.tui._poll()
            self.refresh_profile_state()
            if self.tui.last_comparison is not None:
                self._render_result(self.tui.last_comparison)
            self._update_controls()

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

    @on(Button.Pressed, "#comparison-download")
    def _download(self) -> None:
        if self._is_busy():
            self._set_text(
                "#comparison-progress",
                "download unavailable while another operation runs",
                "yellow",
            )
            return
        self._preflight_input = None
        candidates = tuple(
            (entry.profile.repo_id, entry.profile.revision)
            for entry in self.tui.selected_comparison_profiles()
        )
        self._update_controls()
        self.app.push_screen(SearchScreen(candidates))

    @on(Button.Pressed, "#comparison-restart")
    async def _restart(self) -> None:
        if self._is_busy():
            self._set_text(
                "#comparison-progress",
                "server start unavailable while another operation runs",
                "yellow",
            )
            return
        self._preflight_input = None
        self._update_controls()
        await self.tui.action_cold_start()
        self._update_controls()

    def _decision_reason(self) -> str:
        try:
            return self.query_one("#comparison-reason", Input).value
        except NoMatches:
            return ""

    def _commit_decision(self, decision: DecisionKind, slot: int | None = None) -> None:
        if self._is_busy():
            self._set_text(
                "#comparison-saved",
                "decision unavailable while another operation runs",
                "yellow",
            )
            return
        result = self.tui.last_comparison
        if result is None or result.status != "completed":
            self._set_text(
                "#comparison-saved", "no completed comparison to decide", "yellow"
            )
            return
        profile_id = (
            result.comparison.profiles[slot].profile.id if slot is not None else None
        )
        try:
            finalized, choice = commit_choice(
                result,
                decision,
                profile_id=profile_id,
                reason=self._decision_reason(),
            )
        except (OSError, ValueError) as exc:
            self._set_text("#comparison-saved", f"choice not saved: {exc}", "red")
            return
        self.tui.last_comparison = finalized
        self.tui.saved_choice = choice
        self.tui.saved_choice_error = None
        managed_activation = False
        if decision == "keep" and profile_id is not None:
            try:
                self.tui.apply_coding_profile(
                    profile_id, expected_fingerprint=choice.profile_fingerprint
                )
            except (OSError, ValueError) as exc:
                self._render_result(finalized)
                self._set_text(
                    "#comparison-saved",
                    f"Choice saved; profile not applied: {exc}",
                    "red",
                )
                return
            if self.tui.config.runtime_mode == "managed":
                managed_activation = True
                self._activate_managed_profile(profile_id)
        self._render_result(finalized)
        if managed_activation:
            self._set_text(
                "#comparison-saved", "choice saved; activating managed profile"
            )

    @on(Button.Pressed, "#comparison-keep-a")
    def _keep_a(self) -> None:
        self._commit_decision("keep", 0)

    @on(Button.Pressed, "#comparison-keep-b")
    def _keep_b(self) -> None:
        self._commit_decision("keep", 1)

    @on(Button.Pressed, "#comparison-retain")
    def _retain(self) -> None:
        self._commit_decision("retain")

    @on(Button.Pressed, "#comparison-reject")
    def _reject(self) -> None:
        self._commit_decision("reject")

    @on(Button.Pressed, "#comparison-use-saved")
    def _use_saved(self) -> None:
        if self._is_busy():
            self._set_text(
                "#comparison-saved",
                "saved profile unavailable while another operation runs",
                "yellow",
            )
            return
        choice = self.tui.saved_choice
        if choice is None or choice.decision != "keep" or choice.profile_id is None:
            self._set_text(
                "#comparison-saved", "saved choice has no profile to apply", "yellow"
            )
            return
        try:
            self.tui.apply_coding_profile(
                choice.profile_id, expected_fingerprint=choice.profile_fingerprint
            )
        except (OSError, ValueError) as exc:
            self._set_text(
                "#comparison-saved", f"saved profile not applied: {exc}", "red"
            )
            return
        if self.tui.config.runtime_mode == "managed":
            self._activate_managed_profile(choice.profile_id)
        self.refresh_profile_state()

    @work(exclusive=True, group="managed-activation", thread=True)
    def _activate_managed_profile(self, profile_id: str) -> None:
        if not self.tui.operations.try_acquire(OperationKind.RESTARTING):
            self.app.call_from_thread(
                self._set_text,
                "#comparison-saved",
                "Choice saved; activation blocked by another operation",
                "yellow",
            )
            return
        try:
            model = self.tui.config.model
            if model is None:
                raise ValueError(f"managed profile {profile_id} has no snapshot")
            self.tui.start_managed(
                Path(model),
                on_line=lambda line: self.app.call_from_thread(
                    self.tui.log_app, f"[managed] {line}"
                ),
            )
        except Exception as exc:
            self.app.call_from_thread(
                self._set_text,
                "#comparison-saved",
                f"Choice saved; activation failed: {exc}",
                "red",
            )
        else:
            self.app.call_from_thread(
                self._set_text,
                "#comparison-saved",
                "choice saved; managed profile ready",
            )
        finally:
            self.app.call_from_thread(
                self.tui.operations.release, OperationKind.RESTARTING
            )
            self.app.call_from_thread(self.tui.set_operation_ui, False)

    @on(Button.Pressed, "#comparison-go-chat")
    def _go_chat(self) -> None:
        from mlx_tui.chat_pane import ChatInput  # noqa: PLC0415

        try:
            tabs = self.tui.query_one(TabbedContent)
            tabs.active = "chat"
        except NoMatches:
            return
        try:
            composer = self.tui.query_one("#chat-input", ChatInput)
            composer.focus()
        except NoMatches:
            pass

    @on(Button.Pressed, "#comparison-open-result")
    def _open_result(self) -> None:
        if self._is_busy():
            self._set_text(
                "#comparison-open-status",
                "result opening unavailable while another operation runs",
                "yellow",
            )
            return
        try:
            raw = self.query_one("#comparison-open-path", Input).value.strip()
        except NoMatches:
            return
        if not raw:
            self._set_text(
                "#comparison-open-status", "enter a result path to open", "yellow"
            )
            return
        opened_path = Path(raw).expanduser()
        try:
            loaded = load_comparison(opened_path)
        except (OSError, ValueError) as exc:
            self._set_text(
                "#comparison-open-status",
                f"could not open result: {exc}",
                "red",
            )
            return
        authoritative = replace(
            loaded,
            comparison=replace(loaded.comparison, result_path=opened_path),
        )
        self.tui.last_comparison = authoritative
        self._selected_trial = None
        self._render_result(authoritative)
        self._set_text(
            "#comparison-open-status",
            f"opened {loaded.status} result {loaded.run_id}; "
            "saved choice, setup, and active settings unchanged",
        )

    @on(DataTable.RowSelected, "#comparison-trials")
    def _trial_selected(self, event: DataTable.RowSelected) -> None:
        raw = event.row_key.value
        if raw is None:
            return
        key = raw
        if ":" not in key:
            return
        profile_id, _, repeat_text = key.rpartition(":")
        try:
            repeat_index = int(repeat_text)
        except ValueError:
            return
        if not profile_id:
            return
        self._selected_trial = (profile_id, repeat_index)
        result = self.tui.last_comparison
        if result is None:
            return
        self._set_rich_text(
            "#comparison-trial-detail",
            trial_detail_text(result, profile_id, repeat_index),
        )
