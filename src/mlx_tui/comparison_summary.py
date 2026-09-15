"""Summary construction and latency/memory conclusions."""

from __future__ import annotations

import json
from statistics import median
from typing import cast

from mlx_tui.comparison_contracts import (
    _LATENCY_THRESHOLD,
    _PROFILE_COUNT,
    RSS_SCOPE,
    TRIAL_REPEATS,
    ComparisonResult,
    JSONValue,
    TrialResult,
)
from mlx_tui.comparison_persistence import _json_value


def _summary(result: ComparisonResult) -> dict[str, JSONValue]:
    by_profile: dict[str, list[TrialResult]] = {}
    for trial in result.trials:
        by_profile.setdefault(trial.profile_id, []).append(trial)
    profiles: dict[str, JSONValue] = {}
    for profile_id, trials in by_profile.items():
        first = next((trial for trial in trials if trial.repeat_index == 0), None)
        repeats = [
            trial
            for trial in trials
            if trial.repeat_index > 0 and trial.state == "completed"
        ]
        times = [
            trial.total_s
            for trial in repeats
            if trial.quality_pass and trial.total_s is not None
        ]
        cached = [
            trial.cached_prompt_tokens
            for trial in repeats
            if trial.cached_prompt_tokens is not None
        ]
        profiles[profile_id] = {
            "first_request": {
                "status": first.state if first else "not_attempted",
                "total_s": first.total_s if first else None,
                "quality_pass": first.quality_pass if first else None,
                "note": "first request; prior residency unknown",
            },
            "repeats": {
                "attempted": len(repeats),
                "passing": sum(trial.quality_pass is True for trial in repeats),
                "median_total_s": median(times) if times else None,
                "range_total_s": [min(times), max(times)] if times else None,
                "cached_prompt_tokens": _json_value(cached),
            },
        }
    return {
        "task": {
            "id": result.comparison.task_id,
            "check_id": result.comparison.check_id,
            "prompt": result.comparison.prompt,
            "scope": "synthetic coding-check-v1 only; not general coding ability",
        },
        "profiles": profiles,
        "latency": _latency_summary(result, profiles),
        "memory": {
            "status": "inconclusive",
            "reason": "process-wide RSS is not per-profile residency evidence",
            "sample_scope": RSS_SCOPE,
        },
    }


def _has_unknown(value: JSONValue) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "unknown", "unverified"}
    if isinstance(value, dict):
        return any(_has_unknown(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_unknown(item) for item in value)
    return False


def _latency_summary(  # noqa: PLR0911, PLR0912
    result: ComparisonResult, profiles: dict[str, JSONValue]
) -> dict[str, JSONValue]:
    if result.status != "completed" or len(profiles) != _PROFILE_COUNT:
        return {"status": "inconclusive", "reason": "comparison did not complete"}
    if not result.comparison.machine_tier or any(
        entry.status(result.comparison.machine_tier) != "qualified"
        for entry in result.comparison.profiles
    ):
        return {
            "status": "inconclusive",
            "reason": "profile evidence is absent, unqualified, expired, or revoked",
        }
    if any(
        not value or _has_unknown(value)
        for value in (
            result.comparison.runtime_evidence,
            result.comparison.install_evidence,
            result.comparison.launch_evidence,
        )
    ):
        return {
            "status": "inconclusive",
            "reason": "runtime, install, or launch provenance is unknown",
        }
    if (
        not result.comparison.operator_conditions
        or _has_unknown(result.comparison.operator_conditions)
        or result.comparison.operator_conditions.get("suspect") is True
    ):
        return {"status": "inconclusive", "reason": "operator conditions are unknown"}
    repeat_data: list[dict[str, JSONValue]] = []
    for item in profiles.values():
        if isinstance(item, dict) and isinstance(item.get("repeats"), dict):
            repeat_data.append(cast(dict[str, JSONValue], item["repeats"]))
    if len(repeat_data) != _PROFILE_COUNT or any(
        item.get("passing") != TRIAL_REPEATS for item in repeat_data
    ):
        return {
            "status": "inconclusive",
            "reason": "five passing repeats per profile are required",
        }
    cached_sets: list[tuple[JSONValue, ...]] = []
    for item in repeat_data:
        cached = item.get("cached_prompt_tokens", [])
        cached_sets.append(tuple(cached) if isinstance(cached, list) else ())
    if any(cached_sets) and cached_sets[0] != cached_sets[1]:
        return {
            "status": "inconclusive",
            "reason": "materially different cached-token reuse confounds latency",
        }
    passing_trials = [
        trial
        for trial in result.trials
        if trial.repeat_index > 0 and trial.quality_pass is True
    ]
    identities = {
        (identity.pid, identity.create_time)
        for trial in passing_trials
        for identity in (trial.process_identity_before, trial.process_identity_after)
        if identity is not None
    }
    if len(identities) != 1:
        return {
            "status": "inconclusive",
            "reason": "verified process identity is not matched across repeats",
        }
    protocol_fields = (
        "messages",
        "stream",
        "stream_options",
        "max_tokens",
        "temperature",
        "top_p",
        "seed",
        "chat_template_kwargs",
    )
    protocols = [
        tuple(
            json.dumps(trial.payload.get(key), sort_keys=True)
            for key in protocol_fields
        )
        for trial in passing_trials
    ]
    if not protocols or any(protocol != protocols[0] for protocol in protocols[1:]):
        return {
            "status": "inconclusive",
            "reason": "repeat request budgets or protocol are not matched",
        }
    medians = [item.get("median_total_s") for item in repeat_data]
    if not all(type(value) in {int, float} for value in medians):
        return {"status": "inconclusive", "reason": "repeat timings are unavailable"}
    numeric_medians = cast(list[int | float], medians)
    faster, slower = sorted((float(numeric_medians[0]), float(numeric_medians[1])))
    if slower <= 0:
        return {"status": "inconclusive", "reason": "repeat timing is zero"}
    ratio = (slower - faster) / slower
    # ponytail: 5% heuristic; replace only with preregistered statistical evidence.
    return {
        "status": "advantage_possible"
        if ratio >= _LATENCY_THRESHOLD
        else "inconclusive",
        "heuristic": "5% median repeat latency difference; product heuristic, not statistical proof",
        "difference_fraction": ratio,
    }
