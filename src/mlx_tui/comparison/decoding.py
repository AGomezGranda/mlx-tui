"""Comparison decoding: UI-free strict schema-v1 validation."""

from __future__ import annotations

import json
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path
from typing import cast

from mlx_tui.comparison.contracts import (
    TRIAL_REPEATS,
    ComparisonInput,
    ComparisonPersistenceError,
    ComparisonResult,
    ComparisonValidationError,
    MemorySample,
    TrialResult,
)
from mlx_tui.comparison.encoding import _identity_from_json, _json_value
from mlx_tui.json_util import (
    bool_field,
    check_keys,
    optional_string_field,
    require_dict,
    required_field,
    string_field,
)
from mlx_tui.profiles import CodingProfile, ProfileEntry, RecommendationEvidence

_PROFILE_COUNT = 2


def _nullable_scalar[T](
    table: dict[str, object], key: str, expected: type[T]
) -> T | None:
    value = required_field(table, key, "trial", error=ComparisonValidationError)
    if value is not None and type(value) is not expected:
        raise ComparisonValidationError(
            f"trial.{key} must be {expected.__name__} or null"
        )
    return value


def _profile_from_json(value: object) -> CodingProfile:
    table = require_dict(value, "profile", error=ComparisonValidationError)
    check_keys(
        table,
        {f.name for f in fields(CodingProfile)},
        "profile",
        error=ComparisonValidationError,
    )
    assets = require_dict(
        required_field(
            table, "template_assets", "profile", error=ComparisonValidationError
        ),
        "profile.template_assets",
        error=ComparisonValidationError,
    )
    launch = require_dict(
        required_field(
            table, "launch_settings", "profile", error=ComparisonValidationError
        ),
        "profile.launch_settings",
        error=ComparisonValidationError,
    )

    def number(key: str) -> int | float:
        number_value = required_field(
            table, key, "profile", error=ComparisonValidationError
        )
        if type(number_value) not in {int, float} or isinstance(number_value, bool):
            raise ComparisonValidationError(f"profile.{key} must be a number")
        return cast(int | float, number_value)

    def integer(key: str) -> int:
        integer_value = number(key)
        if type(integer_value) is not int:
            raise ComparisonValidationError(f"profile.{key} must be an integer")
        return integer_value

    thinking = required_field(
        table, "enable_thinking", "profile", error=ComparisonValidationError
    )
    if type(thinking) is not bool:
        raise ComparisonValidationError("profile.enable_thinking must be a bool")
    profile = CodingProfile(
        id=string_field(table, "id", "profile", error=ComparisonValidationError),
        name=string_field(table, "name", "profile", error=ComparisonValidationError),
        repo_id=string_field(
            table, "repo_id", "profile", error=ComparisonValidationError
        ),
        revision=string_field(
            table, "revision", "profile", error=ComparisonValidationError
        ),
        quantization=string_field(
            table, "quantization", "profile", error=ComparisonValidationError
        ),
        runtime_commit=string_field(
            table, "runtime_commit", "profile", error=ComparisonValidationError
        ),
        mlx_version=string_field(
            table, "mlx_version", "profile", error=ComparisonValidationError
        ),
        template_sha256=string_field(
            table, "template_sha256", "profile", error=ComparisonValidationError
        ),
        template_assets={key: str(item) for key, item in assets.items()},
        launch_settings={key: _json_value(item) for key, item in launch.items()},
        system=string_field(
            table, "system", "profile", error=ComparisonValidationError
        ),
        temperature=float(number("temperature")),
        top_p=float(number("top_p")),
        max_tokens=integer("max_tokens"),
        max_ctx=integer("max_ctx"),
        seed=integer("seed"),
        enable_thinking=thinking,
    )
    return profile


def _evidence_from_json(
    value: object, profile: CodingProfile
) -> RecommendationEvidence:
    table = require_dict(value, "evidence", error=ComparisonValidationError)
    check_keys(
        table,
        {f.name for f in fields(RecommendationEvidence)},
        "evidence",
        error=ComparisonValidationError,
    )
    try:
        tested_at = datetime.fromisoformat(
            string_field(
                table, "tested_at", "evidence", error=ComparisonValidationError
            )
        )
        expires_at = datetime.fromisoformat(
            string_field(
                table, "expires_at", "evidence", error=ComparisonValidationError
            )
        )
    except ValueError as exc:
        raise ComparisonValidationError("evidence timestamps are invalid") from exc
    if tested_at.tzinfo is None or expires_at.tzinfo is None:
        raise ComparisonValidationError("evidence timestamps need timezones")
    references = required_field(
        table, "result_references", "evidence", error=ComparisonValidationError
    )
    hashes = required_field(
        table, "result_hashes", "evidence", error=ComparisonValidationError
    )
    if not isinstance(references, list) or not all(
        isinstance(item, str) for item in references
    ):
        raise ComparisonValidationError("evidence references are invalid")
    if not isinstance(hashes, list) or not all(
        isinstance(item, str) for item in hashes
    ):
        raise ComparisonValidationError("evidence hashes are invalid")
    status = string_field(table, "status", "evidence", error=ComparisonValidationError)
    if status not in {"candidate", "unqualified", "qualified", "revoked", "withdrawn"}:
        raise ComparisonValidationError("evidence status is invalid")
    reason = table.get("revocation_reason")
    if reason is not None and not isinstance(reason, str):
        raise ComparisonValidationError("evidence revocation reason is invalid")
    return RecommendationEvidence(
        owner=string_field(table, "owner", "evidence", error=ComparisonValidationError),
        source=string_field(
            table, "source", "evidence", error=ComparisonValidationError
        ),
        status=status,  # type: ignore[arg-type]
        revocation_reason=reason,
        tested_at=tested_at,
        expires_at=expires_at,
        machine_tier=string_field(
            table, "machine_tier", "evidence", error=ComparisonValidationError
        ),
        task_id=string_field(
            table, "task_id", "evidence", error=ComparisonValidationError
        ),
        check_id=string_field(
            table, "check_id", "evidence", error=ComparisonValidationError
        ),
        prompt_sha256=string_field(
            table, "prompt_sha256", "evidence", error=ComparisonValidationError
        ),
        runtime_freeze=string_field(
            table, "runtime_freeze", "evidence", error=ComparisonValidationError
        ),
        profile_fingerprint=string_field(
            table, "profile_fingerprint", "evidence", error=ComparisonValidationError
        ),
        result_references=tuple(references),
        result_hashes=tuple(hashes),
    )


def _entry_from_json(value: object) -> ProfileEntry:
    table = require_dict(value, "profile entry", error=ComparisonValidationError)
    check_keys(
        table, {"profile", "evidence"}, "profile entry", error=ComparisonValidationError
    )
    profile = _profile_from_json(
        required_field(
            table, "profile", "profile entry", error=ComparisonValidationError
        )
    )
    raw_evidence = required_field(
        table, "evidence", "profile entry", error=ComparisonValidationError
    )
    if not isinstance(raw_evidence, list):
        raise ComparisonValidationError("profile entry evidence must be a list")
    return ProfileEntry(
        profile=profile,
        evidence=tuple(_evidence_from_json(item, profile) for item in raw_evidence),
    )


def _input_from_json(value: object) -> ComparisonInput:
    table = require_dict(value, "input", error=ComparisonValidationError)
    check_keys(
        table,
        {f.name for f in fields(ComparisonInput)},
        "input",
        error=ComparisonValidationError,
    )
    raw_profiles = required_field(
        table, "profiles", "input", error=ComparisonValidationError
    )
    paths = required_field(
        table, "snapshot_paths", "input", error=ComparisonValidationError
    )
    hashes = required_field(
        table, "verified_asset_hashes", "input", error=ComparisonValidationError
    )
    if (
        not isinstance(raw_profiles, list)
        or not isinstance(paths, list)
        or not isinstance(hashes, list)
    ):
        raise ComparisonValidationError(
            "input profiles, paths, and hashes must be lists"
        )
    profile_order = table.get("profile_order")
    if profile_order is not None and (
        not isinstance(profile_order, list)
        or not all(isinstance(item, str) for item in profile_order)
    ):
        raise ComparisonValidationError("input.profile_order is invalid")
    if not all(isinstance(item, str) for item in paths):
        raise ComparisonValidationError("input snapshot paths must be strings")
    profiles = tuple(_entry_from_json(item) for item in raw_profiles)
    ids = tuple(entry.profile.id for entry in profiles)
    if len(profiles) != _PROFILE_COUNT or len(set(ids)) != _PROFILE_COUNT:
        raise ComparisonValidationError("input requires two distinct profiles")
    if len(paths) != len(profiles) or len(hashes) not in {0, len(profiles)}:
        raise ComparisonValidationError(
            "input paths and hashes must align with profiles"
        )
    if profile_order is not None and tuple(profile_order) != ids:
        raise ComparisonValidationError("input.profile_order must match profiles")
    result_path = optional_string_field(
        table, "result_path", "input", error=ComparisonValidationError
    )
    return ComparisonInput(
        endpoint=string_field(
            table, "endpoint", "input", error=ComparisonValidationError
        ),
        profiles=profiles,
        snapshot_paths=tuple(Path(item) for item in paths),
        verified_asset_hashes=tuple(
            {
                key: str(item)
                for key, item in require_dict(
                    item, "asset hashes", error=ComparisonValidationError
                ).items()
            }
            for item in hashes
        ),
        runtime_evidence=require_dict(
            required_field(
                table, "runtime_evidence", "input", error=ComparisonValidationError
            ),
            "runtime_evidence",
            error=ComparisonValidationError,
        ),
        install_evidence=require_dict(
            required_field(
                table, "install_evidence", "input", error=ComparisonValidationError
            ),
            "install_evidence",
            error=ComparisonValidationError,
        ),
        launch_evidence=require_dict(
            required_field(
                table, "launch_evidence", "input", error=ComparisonValidationError
            ),
            "launch_evidence",
            error=ComparisonValidationError,
        ),
        provenance=require_dict(
            required_field(
                table, "provenance", "input", error=ComparisonValidationError
            ),
            "provenance",
            error=ComparisonValidationError,
        ),
        process_identity=_identity_from_json(table.get("process_identity")),
        isolation_evidence=require_dict(
            required_field(
                table, "isolation_evidence", "input", error=ComparisonValidationError
            ),
            "isolation_evidence",
            error=ComparisonValidationError,
        ),
        machine_tier=string_field(
            table, "machine_tier", "input", error=ComparisonValidationError
        ),
        operator_conditions=require_dict(
            required_field(
                table, "operator_conditions", "input", error=ComparisonValidationError
            ),
            "operator_conditions",
            error=ComparisonValidationError,
        ),
        profile_order=tuple(profile_order) if profile_order else None,
        task_id=string_field(
            table, "task_id", "input", error=ComparisonValidationError
        ),
        check_id=string_field(
            table, "check_id", "input", error=ComparisonValidationError
        ),
        prompt=string_field(table, "prompt", "input", error=ComparisonValidationError),
        result_path=Path(result_path) if result_path is not None else None,
    )


def _sample_from_json(value: object) -> MemorySample:
    table = require_dict(value, "memory sample", error=ComparisonValidationError)
    check_keys(
        table,
        {"timestamp", "rss_gib", "available_gib", "scope"},
        "memory sample",
        error=ComparisonValidationError,
    )
    timestamp = required_field(
        table, "timestamp", "memory sample", error=ComparisonValidationError
    )
    if type(timestamp) not in {int, float} or isinstance(timestamp, bool):
        raise ComparisonValidationError("memory sample timestamp is invalid")
    rss = table.get("rss_gib")
    available = table.get("available_gib")
    for item, label in ((rss, "rss_gib"), (available, "available_gib")):
        if item is not None and (
            type(item) not in {int, float} or isinstance(item, bool)
        ):
            raise ComparisonValidationError(f"memory sample {label} is invalid")
    timestamp_number = float(cast(int | float, timestamp))
    return MemorySample(
        timestamp=timestamp_number,
        rss_gib=float(rss) if rss is not None else None,
        available_gib=float(available) if available is not None else None,
        scope=string_field(
            table, "scope", "memory sample", error=ComparisonValidationError
        ),
    )


def _trial_from_json(value: object) -> TrialResult:
    table = require_dict(value, "trial", error=ComparisonValidationError)
    check_keys(
        table,
        {f.name for f in fields(TrialResult)},
        "trial",
        error=ComparisonValidationError,
    )
    repeat_index = required_field(
        table, "repeat_index", "trial", error=ComparisonValidationError
    )
    if type(repeat_index) is not int:
        raise ComparisonValidationError("trial repeat index is invalid")
    raw_tools = required_field(
        table, "tool_calls", "trial", error=ComparisonValidationError
    )
    raw_samples = required_field(
        table, "rss_samples", "trial", error=ComparisonValidationError
    )
    if not isinstance(raw_tools, list) or not isinstance(raw_samples, list):
        raise ComparisonValidationError("trial tool calls and samples must be lists")
    timings: dict[str, float | None] = {}
    for key in ("first_output_s", "answer_started_s", "total_s"):
        item = table.get(key)
        if item is not None and (
            type(item) not in {int, float} or isinstance(item, bool)
        ):
            raise ComparisonValidationError(f"trial {key} is invalid")
        timings[key] = float(item) if item is not None else None
    raw_state = string_field(table, "state", "trial", error=ComparisonValidationError)
    if raw_state not in {"not_attempted", "attempted", "completed"}:
        raise ComparisonValidationError("trial state is invalid")
    payload = require_dict(
        required_field(table, "payload", "trial", error=ComparisonValidationError),
        "trial.payload",
        error=ComparisonValidationError,
    )
    counts = {
        key: _nullable_scalar(table, key, int)
        for key in ("prompt_tokens", "completion_tokens", "cached_prompt_tokens")
    }
    if any(value is not None and value < 0 for value in counts.values()):
        raise ComparisonValidationError("trial token counts must be non-negative")
    return TrialResult(
        profile_id=string_field(
            table, "profile_id", "trial", error=ComparisonValidationError
        ),
        repeat_index=repeat_index,
        state=raw_state,  # type: ignore[arg-type]
        error=optional_string_field(
            table, "error", "trial", error=ComparisonValidationError
        ),
        cancelled=bool_field(
            table, "cancelled", "trial", error=ComparisonValidationError
        ),
        payload=payload,
        answer=string_field(table, "answer", "trial", error=ComparisonValidationError),
        reasoning=string_field(
            table, "reasoning", "trial", error=ComparisonValidationError
        ),
        tool_calls=tuple(
            require_dict(item, "tool call", error=ComparisonValidationError)
            for item in raw_tools
        ),
        response_model=optional_string_field(
            table, "response_model", "trial", error=ComparisonValidationError
        ),
        finish_reason=optional_string_field(
            table, "finish_reason", "trial", error=ComparisonValidationError
        ),
        stream_complete=bool_field(
            table, "stream_complete", "trial", error=ComparisonValidationError
        ),
        quality_pass=_nullable_scalar(table, "quality_pass", bool),
        quality_reason=optional_string_field(
            table, "quality_reason", "trial", error=ComparisonValidationError
        ),
        prompt_tokens=counts["prompt_tokens"],
        completion_tokens=counts["completion_tokens"],
        prompt_estimated=_nullable_scalar(table, "prompt_estimated", bool),
        completion_estimated=_nullable_scalar(table, "completion_estimated", bool),
        cached_prompt_tokens=counts["cached_prompt_tokens"],
        **timings,
        rss_samples=tuple(_sample_from_json(item) for item in raw_samples),
        sample_scope=string_field(
            table, "sample_scope", "trial", error=ComparisonValidationError
        ),
        process_identity_before=_identity_from_json(
            table.get("process_identity_before")
        ),
        process_identity_after=_identity_from_json(table.get("process_identity_after")),
    )


def decode_comparison(value: str | dict[str, object]) -> ComparisonResult:
    """Strictly decode schema-v1 JSON, marking a reopened running run interrupted."""
    try:
        document: object = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError) as exc:
        raise ComparisonPersistenceError("comparison JSON is corrupt") from exc
    table = require_dict(document, "comparison", error=ComparisonValidationError)
    check_keys(
        table,
        {
            "schema_version",
            "run_id",
            "status",
            "created_at",
            "updated_at",
            "error",
            "input",
            "profile_fingerprints",
            "trials",
            "summary",
        },
        "comparison",
        error=ComparisonValidationError,
    )
    if table.get("schema_version") != 1:
        raise ComparisonPersistenceError("unsupported comparison schema")
    raw_trials = required_field(
        table, "trials", "comparison", error=ComparisonValidationError
    )
    if not isinstance(raw_trials, list):
        raise ComparisonValidationError("comparison.trials must be a list")
    status = table.get("status")
    if status not in ("running", "completed", "failed", "cancelled", "interrupted"):
        raise ComparisonValidationError("comparison status is invalid")
    result = ComparisonResult(
        run_id=string_field(
            table, "run_id", "comparison", error=ComparisonValidationError
        ),
        status=status,  # type: ignore[arg-type]
        comparison=_input_from_json(
            required_field(
                table, "input", "comparison", error=ComparisonValidationError
            )
        ),
        trials=tuple(_trial_from_json(item) for item in raw_trials),
        created_at=string_field(
            table, "created_at", "comparison", error=ComparisonValidationError
        ),
        updated_at=string_field(
            table, "updated_at", "comparison", error=ComparisonValidationError
        ),
        summary=require_dict(
            required_field(
                table, "summary", "comparison", error=ComparisonValidationError
            ),
            "summary",
            error=ComparisonValidationError,
        ),
        error=optional_string_field(
            table, "error", "comparison", error=ComparisonValidationError
        ),
    )
    expected_fingerprints = [
        entry.profile.fingerprint for entry in result.comparison.profiles
    ]
    if table.get("profile_fingerprints") != expected_fingerprints:
        raise ComparisonValidationError(
            "saved profile fingerprints do not match profiles"
        )
    expected_keys = {
        (entry.profile.id, index)
        for entry in result.comparison.profiles
        for index in range(TRIAL_REPEATS + 1)
    }
    keys = [(trial.profile_id, trial.repeat_index) for trial in result.trials]
    if len(keys) != len(set(keys)) or not set(keys) <= expected_keys:
        raise ComparisonValidationError(
            "comparison trial keys are invalid or duplicated"
        )
    if result.status == "completed" and (
        set(keys) != expected_keys
        or any(trial.state != "completed" for trial in result.trials)
    ):
        raise ComparisonValidationError(
            "completed comparison requires every completed trial"
        )
    if result.status == "running":
        return replace(
            result,
            status="interrupted",
            error=result.error or "comparison was interrupted before completion",
        )
    return result
