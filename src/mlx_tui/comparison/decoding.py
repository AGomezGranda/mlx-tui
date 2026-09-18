"""Comparison decoding: UI-free strict schema-v1 validation."""

from __future__ import annotations

import json
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path
from typing import cast

from mlx_tui.comparison.contracts import (
    ComparisonInput,
    ComparisonPersistenceError,
    ComparisonResult,
    ComparisonValidationError,
    MemorySample,
    TrialResult,
)
from mlx_tui.comparison.encoding import _identity_from_json, _json_value
from mlx_tui.json_util import check_keys, require_dict, required_field, string_field
from mlx_tui.profiles import CodingProfile, ProfileEntry, RecommendationEvidence


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
    return ComparisonInput(
        endpoint=string_field(
            table, "endpoint", "input", error=ComparisonValidationError
        ),
        profiles=tuple(_entry_from_json(item) for item in raw_profiles),
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
        result_path=Path(table["result_path"])
        if isinstance(table.get("result_path"), str)
        else None,
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
    return TrialResult(
        profile_id=string_field(
            table, "profile_id", "trial", error=ComparisonValidationError
        ),
        repeat_index=repeat_index,
        state=raw_state,  # type: ignore[arg-type]
        error=table.get("error"),
        cancelled=table.get("cancelled") is True,
        payload=payload,
        answer=string_field(table, "answer", "trial", error=ComparisonValidationError),
        reasoning=string_field(
            table, "reasoning", "trial", error=ComparisonValidationError
        ),
        tool_calls=tuple(
            require_dict(item, "tool call", error=ComparisonValidationError)
            for item in raw_tools
        ),
        response_model=table.get("response_model"),
        finish_reason=table.get("finish_reason"),
        stream_complete=table.get("stream_complete") is True,
        quality_pass=table.get("quality_pass"),
        quality_reason=table.get("quality_reason"),
        prompt_tokens=table.get("prompt_tokens"),
        completion_tokens=table.get("completion_tokens"),
        prompt_estimated=table.get("prompt_estimated"),
        completion_estimated=table.get("completion_estimated"),
        cached_prompt_tokens=table.get("cached_prompt_tokens"),
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
    if status not in {"running", "completed", "failed", "cancelled", "interrupted"}:
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
        error=table.get("error"),
    )
    expected_fingerprints = [
        entry.profile.fingerprint for entry in result.comparison.profiles
    ]
    if table.get("profile_fingerprints") != expected_fingerprints:
        raise ComparisonValidationError(
            "saved profile fingerprints do not match profiles"
        )
    if result.status == "running":
        return replace(
            result,
            status="interrupted",
            error=result.error or "comparison was interrupted before completion",
        )
    return result
