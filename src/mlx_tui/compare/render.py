"""Compare result/profile rendering helpers (pane-parameterized)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual.css.query import NoMatches
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Select,
    Static,
)

from mlx_tui.comparison import (
    CODING_CHECK_EXPECTED,
    CODING_CHECK_PROMPT,
    ComparisonResult,
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
from mlx_tui.models import resolve_cached_snapshot

if TYPE_CHECKING:
    pass

_PROFILE_COUNT = 2


def _set_text(pane: Any, selector: str, message: str, style: str | None = None) -> None:
    try:
        widget = pane.query_one(selector, Static)
    except NoMatches:
        return
    widget.update(Text(message) if style is None else Text(message, style=style))


def _set_rich_text(pane: Any, selector: str, text: Text) -> None:
    try:
        widget = pane.query_one(selector, Static)
    except NoMatches:
        return
    widget.update(text)


def _is_busy(pane: Any) -> bool:
    return pane._comparison_active or pane.tui.operations.is_busy


def refresh_profile_state(pane: Any) -> None:
    ids = pane.tui.comparison_profile_ids
    try:
        if len(ids) == _PROFILE_COUNT:
            pane.query_one("#comparison-profile-a", Select).value = ids[0]
            pane.query_one("#comparison-profile-b", Select).value = ids[1]
    except NoMatches:
        pass
    entries = pane.tui.selected_comparison_profiles()
    try:
        tier_raw = pane.query_one("#comparison-machine-tier", Input).value.strip()
    except NoMatches:
        tier_raw = ""
    tier = tier_raw or "unknown"
    catalogue_error = pane.tui.profile_error
    if catalogue_error:
        pane._set_text(
            "#comparison-setup-details",
            f"catalogue unavailable: {catalogue_error}",
            "red",
        )
        pane._set_text("#comparison-details", "", None)
    elif not entries:
        pane._set_text(
            "#comparison-setup-details",
            "no pinned profiles available",
            "yellow",
        )
        pane._set_text("#comparison-details", "", None)
    else:
        lines: list[str] = []
        for slot, entry in enumerate(entries):
            profile = entry.profile
            try:
                snapshot = resolve_cached_snapshot(profile.repo_id, profile.revision)
                cache = f"cached {snapshot}"
            except Exception as exc:
                cache = f"cache missing: {exc.__class__.__name__}"
            name = "a" if slot == 0 else "b"
            pane._set_text(
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
            lines.append(f"Order: {entries[0].profile.id} then {entries[1].profile.id}")
        details = "\n".join(lines)
        pane._set_text(
            "#comparison-setup-details",
            "Runs in order: A → B · fixed coding-check-v1",
        )
        pane._set_text("#comparison-details", details)
    pane._render_task_details()
    pane._render_evidence_details()
    pane._render_saved()
    pane._update_keep_labels()
    pane._update_controls()


def _render_task_details(pane: Any) -> None:
    entries = pane.tui.selected_comparison_profiles()
    if not entries:
        pane._set_text("#comparison-task-details", "no profiles selected")
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
    pane._set_text("#comparison-task-details", "\n".join(lines))


def _operator_input_value(pane: Any, selector: str) -> str:
    try:
        return pane.query_one(selector, Input).value.strip()
    except NoMatches:
        return ""


def _render_evidence_details(pane: Any) -> None:
    runtime = pane._operator_input_value("#comparison-runtime") or "unknown"
    install = pane._operator_input_value("#comparison-install") or "unknown"
    launch = pane._operator_input_value("#comparison-launch") or "unknown"
    conditions = pane._operator_input_value("#comparison-conditions") or "unknown"
    isolation = pane._operator_input_value("#comparison-isolation") or "unknown"
    try:
        tier = pane._operator_input_value("#comparison-machine-tier") or "unknown"
    except NoMatches:
        tier = "unknown"
    lines = [
        f"machine tier: {tier or 'unknown'}",
        f"runtime: {runtime} · install: {install} · launch: {launch}",
        f"conditions: {conditions} · isolation: {isolation}",
        "operator text is a declaration, not verification; unknown "
        "provenance allows a run but blocks an advantage recommendation",
    ]
    pane._set_text("#comparison-evidence-details", "\n".join(lines))


def _render_saved(pane: Any) -> None:
    if pane.tui.saved_choice_error is not None:
        text = f"saved choice unavailable: {pane.tui.saved_choice_error}"
    elif pane.tui.saved_choice is not None:
        choice = pane.tui.saved_choice
        text = (
            f"saved {choice.decision}: {choice.profile_id or 'no profile'} · "
            f"run {choice.run_id}"
        )
    else:
        text = "saved choice: none"
    if pane.tui.active_profile_id is not None:
        suffix = " (modified)" if pane.tui.active_profile_modified else ""
        text += f"\nactive: {pane.tui.active_profile_id}{suffix}"
    else:
        text += "\nactive: none"
    result = pane.tui.last_comparison
    if result is not None:
        text += f"\nresult: {pane._result_text(result)}"
    pane._set_text("#comparison-saved", text)


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


def _update_keep_labels(pane: Any) -> None:
    keep_a, keep_b = keep_button_labels(pane.tui.last_comparison)
    try:
        pane.query_one("#comparison-keep-a", Button).label = keep_a
    except NoMatches:
        pass
    try:
        pane.query_one("#comparison-keep-b", Button).label = keep_b
    except NoMatches:
        pass


def _update_controls(pane: Any) -> None:  # noqa: PLR0912, PLR0915
    if not pane.is_mounted:
        return
    busy = pane._is_busy()
    catalogue_broken = pane.tui.profile_error is not None
    readiness = pane._preflight_input is not None and not busy
    result = pane.tui.last_comparison
    decidable = result is not None and result.status == "completed" and not busy
    pane.query_one("#comparison-results").display = result is not None
    pane.query_one("#comparison-empty").display = result is None
    pane.query_one("#comparison-decision").display = (
        result is not None and result.status == "completed"
    )
    saved = pane.tui.saved_choice
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
                pane.query_one(selector, Input | Select).disabled = busy  # type: ignore[type-abstract]
            except NoMatches:
                pass
    except Exception:
        pass
    # Select widgets need explicit handling because Input|Select union
    # above may miss them on some Textual versions.
    for selector in ("#comparison-profile-a", "#comparison-profile-b"):
        try:
            pane.query_one(selector, Select).disabled = busy
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
            pane.query_one(selector, Input).disabled = busy
        except NoMatches:
            pass
    try:
        preflight = pane.query_one("#comparison-preflight", Button)
        preflight.disabled = busy or catalogue_broken
    except NoMatches:
        pass
    try:
        run = pane.query_one("#comparison-run", Button)
        run.disabled = (not readiness) or catalogue_broken
    except NoMatches:
        pass
    try:
        cancel = pane.query_one("#comparison-cancel", Button)
        cancel.disabled = not (pane._comparison_active and not pane._cancel_requested)
    except NoMatches:
        pass
    for selector in ("#comparison-download", "#comparison-restart"):
        try:
            pane.query_one(selector, Button).disabled = busy
        except NoMatches:
            pass
    for selector in (
        "#comparison-keep-a",
        "#comparison-keep-b",
        "#comparison-retain",
        "#comparison-reject",
    ):
        try:
            pane.query_one(selector, Button).disabled = not decidable
        except NoMatches:
            pass
    try:
        pane.query_one("#comparison-reason", Input).disabled = busy
    except NoMatches:
        pass
    try:
        pane.query_one("#comparison-use-saved", Button).disabled = not applicable
    except NoMatches:
        pass
    try:
        pane.query_one("#comparison-go-chat", Button).disabled = False
    except NoMatches:
        pass
    try:
        pane.query_one("#comparison-open-path", Input).disabled = busy
    except NoMatches:
        pass
    try:
        pane.query_one("#comparison-open-result", Button).disabled = busy
    except NoMatches:
        pass


def _clear_result_display(pane: Any) -> None:
    pane._set_text("#comparison-result-header", "")
    pane._set_text("#comparison-latency", "")
    pane._set_text("#comparison-memory", "")
    pane._set_text("#comparison-trial-detail", "No trial selected")
    try:
        pane.query_one("#comparison-measurements", DataTable).clear()
    except NoMatches:
        pass
    try:
        pane.query_one("#comparison-trials", DataTable).clear()
    except NoMatches:
        pass


def _render_result(pane: Any, result: ComparisonResult) -> None:
    pane._set_rich_text("#comparison-result-header", result_header(result))
    try:
        table = pane.query_one("#comparison-measurements", DataTable)
        table.clear()
        for measurement, left, right in measurement_rows(result):
            table.add_row(measurement.plain, left.plain, right.plain, height=2)
    except NoMatches:
        pass
    pane._set_rich_text("#comparison-latency", latency_text(result))
    pane._set_rich_text("#comparison-memory", memory_text(result))
    try:
        trials = pane.query_one("#comparison-trials", DataTable)
        trials.clear()
        for key, cells in trial_table_rows(result):
            profile, label, state, quality, total = (cell.plain for cell in cells)
            row_key = f"{key[0]}:{key[1]}"
            trials.add_row(profile, label, state, quality, total, key=row_key)
    except NoMatches:
        pass
    if pane._selected_trial is not None:
        profile_id, repeat_index = pane._selected_trial
        pane._set_rich_text(
            "#comparison-trial-detail",
            trial_detail_text(result, profile_id, repeat_index),
        )
    else:
        failures = [t for t in result.trials if t.error or t.quality_pass is False]
        if failures:
            first = failures[0]
            pane._set_rich_text(
                "#comparison-trial-detail",
                trial_detail_text(result, first.profile_id, first.repeat_index),
            )
        else:
            pane._set_text("#comparison-trial-detail", "No trial selected")
    pane._set_rich_text("#comparison-progress", progress_text(result))
    pane._update_keep_labels()
    pane._render_saved()
    pane._update_controls()
