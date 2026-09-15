"""Pure presentation builders for the Compare pane.

These helpers format immutable comparison records into literal ``Text`` and
row values for display. They never query widgets, mutate app state, or own
worker lifetimes; the pane remains responsible for Textual composition.
"""

from __future__ import annotations

from rich.text import Text

from mlx_tui.comparison_contracts import (
    TRIAL_REPEATS,
    ComparisonResult,
    TrialResult,
)

_UNKNOWN = "—"
_PROFILE_COUNT = 2
_RANGE_PAIR = 2


def format_seconds(value: float | None) -> str:
    """Format a timing honestly: absent data is unknown, never zero."""
    if value is None:
        return _UNKNOWN
    return f"{value:.2f}s"


def _profile_ids_in_order(result: ComparisonResult) -> tuple[str, str] | None:
    profiles = result.comparison.profiles
    if len(profiles) != _PROFILE_COUNT:
        return None
    return (profiles[0].profile.id, profiles[1].profile.id)


def _profile_name(result: ComparisonResult, profile_id: str) -> str:
    for entry in result.comparison.profiles:
        if entry.profile.id == profile_id:
            return entry.profile.name or entry.profile.id
    return profile_id


def result_header(result: ComparisonResult) -> Text:
    """Render run ID and frozen tested names/order from the result itself."""
    profiles = result.comparison.profiles
    if len(profiles) == _PROFILE_COUNT:
        left = profiles[0].profile.name or profiles[0].profile.id
        right = profiles[1].profile.name or profiles[1].profile.id
        return Text(f"Result: {result.run_id} · {left} then {right}")
    return Text(f"Result: {result.run_id} · unknown profiles")


def _find_trial(
    result: ComparisonResult, profile_id: str, repeat_index: int
) -> TrialResult | None:
    for trial in result.trials:
        if trial.profile_id == profile_id and trial.repeat_index == repeat_index:
            return trial
    return None


def current_attempted_trial(result: ComparisonResult) -> TrialResult | None:
    """Return the active attempted slot, if any, in trial order."""
    for trial in result.trials:
        if trial.state == "attempted":
            return trial
    return None


def _trial_label(repeat_index: int) -> str:
    if repeat_index == 0:
        return "first request"
    return f"repeat {repeat_index}/{TRIAL_REPEATS}"


def progress_text(result: ComparisonResult) -> Text:
    """Describe current profile/first-or-repeat index and completion counts."""
    attempted = sum(trial.state != "not_attempted" for trial in result.trials)
    completed = sum(trial.state == "completed" for trial in result.trials)
    total = len(result.trials) if result.trials else 12
    active = current_attempted_trial(result)
    if active is not None:
        slot = (
            f"{active.profile_id} · {_trial_label(active.repeat_index)} · "
            f"completed {completed}/{total}"
        )
    else:
        slot = f"completed {completed}/{total} · attempted {attempted}/{total}"
    base = f"{result.status} · {slot}"
    if result.error:
        base += f" · error: {result.error}"
    path = result.comparison.result_path
    if path is not None:
        base += f" · checkpoint {path}"
    return Text(base)


def _summary_profiles(result: ComparisonResult) -> dict[str, object]:
    summary = result.summary.get("profiles")
    if isinstance(summary, dict):
        return summary  # type: ignore[return-value]
    return {}


def _passing_count(result: ComparisonResult, profile_id: str) -> int:
    return sum(
        1
        for trial in result.trials
        if trial.profile_id == profile_id
        and trial.repeat_index > 0
        and trial.quality_pass is True
    )


def measurement_rows(  # noqa: PLR0912
    result: ComparisonResult,
) -> list[tuple[Text, Text, Text]]:
    """Return compact A/B rows: first total, passing repeats, median/range."""
    order = _profile_ids_in_order(result)
    if order is None:
        return []
    summaries = _summary_profiles(result)
    rows: list[tuple[Text, Text, Text]] = []

    first_cells: list[str] = []
    for profile_id in order:
        trial = _find_trial(result, profile_id, 0)
        if trial is None or trial.total_s is None:
            # Fall back to the stored summary when the trial row is absent,
            # but never invent a zero for missing data.
            stored: object = None
            profile_summary = summaries.get(profile_id)
            if isinstance(profile_summary, dict):
                first_block = profile_summary.get("first_request")
                if isinstance(first_block, dict):
                    stored = first_block.get("total_s")
            if isinstance(stored, (int, float)) and not isinstance(stored, bool):
                first_cells.append(f"{float(stored):.2f}s")
            else:
                first_cells.append(_UNKNOWN)
        else:
            first_cells.append(format_seconds(trial.total_s))
    rows.append(
        (Text("First-request total"), Text(first_cells[0]), Text(first_cells[1]))
    )

    passing = [_passing_count(result, pid) for pid in order]
    rows.append(
        (
            Text("Passing repeats"),
            Text(f"{passing[0]} / {TRIAL_REPEATS}"),
            Text(f"{passing[1]} / {TRIAL_REPEATS}"),
        )
    )

    median_cells: list[str] = []
    for profile_id in order:
        profile_summary = summaries.get(profile_id)
        median: object = None
        time_range: object = None
        if isinstance(profile_summary, dict):
            repeats = profile_summary.get("repeats")
            if isinstance(repeats, dict):
                median = repeats.get("median_total_s")
                time_range = repeats.get("range_total_s")
        if isinstance(median, (int, float)) and not isinstance(median, bool):
            cell = f"{float(median):.2f}s"
            if (
                isinstance(time_range, list)
                and len(time_range) == _RANGE_PAIR
                and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in time_range
                )
            ):
                low = float(time_range[0])  # type: ignore[arg-type]
                high = float(time_range[1])  # type: ignore[arg-type]
                cell += f" [{low:.2f}–{high:.2f}]"
        else:
            cell = _UNKNOWN
        median_cells.append(cell)
    rows.append(
        (
            Text("Repeat median [range]"),
            Text(median_cells[0]),
            Text(median_cells[1]),
        )
    )
    return rows


def latency_text(result: ComparisonResult) -> Text:
    """Render the stored latency conclusion and explanation literally."""
    latency = result.summary.get("latency")
    if not isinstance(latency, dict):
        return Text("Latency: unknown · no stored conclusion")
    status = latency.get("status", "unknown")
    reason = latency.get("reason")
    heuristic = latency.get("heuristic")
    fraction = latency.get("difference_fraction")
    parts = f"Latency: {status}"
    if isinstance(fraction, (int, float)) and not isinstance(fraction, bool):
        parts += f" · difference {float(fraction):.3f}"
    if isinstance(reason, str) and reason:
        parts += f" · {reason}"
    if isinstance(heuristic, str) and heuristic:
        parts += f" · {heuristic}"
    return Text(parts)


def memory_text(result: ComparisonResult) -> Text:
    """Render memory as inconclusive with its sampled-RSS scope."""
    memory = result.summary.get("memory")
    if not isinstance(memory, dict):
        return Text("Memory: inconclusive · process RSS is not per-profile residency")
    status = memory.get("status", "inconclusive")
    reason = memory.get("reason")
    parts = f"Memory: {status} · process RSS is not per-profile residency"
    if isinstance(reason, str) and reason:
        parts += f" · {reason}"
    return Text(parts)


def _quality_label(trial: TrialResult) -> str:
    if trial.quality_pass is True:
        return "pass"
    if trial.quality_pass is False:
        return "fail"
    return _UNKNOWN


def trial_table_rows(
    result: ComparisonResult,
) -> list[tuple[tuple[str, int], tuple[Text, Text, Text, Text, Text]]]:
    """Return trial rows keyed by (profile_id, repeat_index).

    Cells are profile, first/repeat index, state, quality, and timings.
    """
    rows: list[tuple[tuple[str, int], tuple[Text, Text, Text, Text, Text]]] = []
    for trial in result.trials:
        label = "first" if trial.repeat_index == 0 else f"repeat {trial.repeat_index}"
        cells = (
            Text(trial.profile_id),
            Text(label),
            Text(trial.state),
            Text(_quality_label(trial)),
            Text(format_seconds(trial.total_s)),
        )
        rows.append(((trial.profile_id, trial.repeat_index), cells))
    return rows


def _rss_range_text(trial: TrialResult) -> str:
    values = [
        sample.rss_gib for sample in trial.rss_samples if sample.rss_gib is not None
    ]
    count = len(trial.rss_samples)
    if not values:
        return f"sampled process RSS unknown (n={count})"
    low = min(values)
    high = max(values)
    return f"sampled process RSS {low:.2f}–{high:.2f} GiB (n={count})"


def trial_detail_text(
    result: ComparisonResult, profile_id: str, repeat_index: int
) -> Text:
    """Render error/quality reason, answer/reasoning, identity, tokens, RSS."""
    trial = _find_trial(result, profile_id, repeat_index)
    if trial is None:
        return Text("No trial selected")
    lines = [
        f"{trial.profile_id} · {_trial_label(repeat_index)} · {trial.state}",
        f"quality: {_quality_label(trial)}"
        + (f" · {trial.quality_reason}" if trial.quality_reason else ""),
    ]
    if trial.error:
        lines.append(f"error: {trial.error}")
    if trial.response_model:
        lines.append(f"response model: {trial.response_model}")
    if trial.finish_reason:
        lines.append(f"finish reason: {trial.finish_reason}")
    cached = (
        _UNKNOWN
        if trial.cached_prompt_tokens is None
        else str(trial.cached_prompt_tokens)
    )
    prompt = _UNKNOWN if trial.prompt_tokens is None else str(trial.prompt_tokens)
    if trial.prompt_estimated:
        prompt += " (estimated)"
    completion = (
        _UNKNOWN if trial.completion_tokens is None else str(trial.completion_tokens)
    )
    if trial.completion_estimated:
        completion += " (estimated)"
    lines.append(f"cached prompt tokens: {cached}")
    lines.append(f"prompt tokens: {prompt} · completion tokens: {completion}")
    lines.append(
        f"first output: {format_seconds(trial.first_output_s)} · "
        f"answer started: {format_seconds(trial.answer_started_s)} · "
        f"total: {format_seconds(trial.total_s)}"
    )
    lines.append(_rss_range_text(trial))
    if trial.answer:
        lines.append(f"answer: {trial.answer[:2000]}")
    if trial.reasoning:
        lines.append(f"reasoning: {trial.reasoning[:2000]}")
    return Text("\n".join(lines))


def keep_button_labels(result: ComparisonResult | None) -> tuple[str, str]:
    """Return frozen Keep labels bound to the displayed result's profiles."""
    if result is None or len(result.comparison.profiles) != _PROFILE_COUNT:
        return ("Keep A", "Keep B")
    left = (
        result.comparison.profiles[0].profile.name
        or result.comparison.profiles[0].profile.id
    )
    right = (
        result.comparison.profiles[1].profile.name
        or result.comparison.profiles[1].profile.id
    )
    return (f"Keep A: {left}", f"Keep B: {right}")
