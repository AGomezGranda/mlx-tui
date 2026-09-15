"""Strict schema-v1 persistence and the choice transaction."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from mlx_tui.comparison_contracts import (
    ComparisonInput,
    ComparisonPersistenceError,
    ComparisonResult,
    ComparisonValidationError,
    DecisionKind,
    JSONValue,
    MemorySample,
    SavedChoice,
    TrialResult,
    _finite_non_negative,
    _now_iso,
)
from mlx_tui.process import ProcessIdentity
from mlx_tui.profiles import (
    CodingProfile,
    ProfileEntry,
    RecommendationEvidence,
    profile_fingerprint,
)


def comparison_dir() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "mlx-tui" / "comparisons"


def choice_path() -> Path:
    return comparison_dir().parent / "choice.json"


def _json_value(value: object) -> JSONValue:
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ComparisonValidationError("JSON values must be finite")
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise ComparisonValidationError(f"unsupported JSON value: {type(value).__name__}")


def _identity_to_json(identity: ProcessIdentity | None) -> JSONValue:
    if identity is None:
        return None
    return {"pid": identity.pid, "create_time": identity.create_time}


def _identity_from_json(value: object) -> ProcessIdentity | None:
    if value is None:
        return None
    if not isinstance(value, dict) or type(value.get("pid")) is not int:
        raise ComparisonValidationError("invalid process identity")
    create_time = value.get("create_time")
    if type(create_time) not in {int, float} or isinstance(create_time, bool):
        raise ComparisonValidationError("invalid process identity create time")
    create_time_number = float(cast(int | float, create_time))
    _finite_non_negative(create_time_number, "process identity create time")
    return ProcessIdentity(pid=value["pid"], create_time=create_time_number)


def _evidence_to_json(evidence: RecommendationEvidence) -> dict[str, JSONValue]:
    return {
        "owner": evidence.owner,
        "source": evidence.source,
        "status": evidence.status,
        "revocation_reason": evidence.revocation_reason,
        "tested_at": evidence.tested_at.isoformat(),
        "expires_at": evidence.expires_at.isoformat(),
        "machine_tier": evidence.machine_tier,
        "task_id": evidence.task_id,
        "check_id": evidence.check_id,
        "prompt_sha256": evidence.prompt_sha256,
        "runtime_freeze": evidence.runtime_freeze,
        "profile_fingerprint": evidence.profile_fingerprint,
        "result_references": _json_value(list(evidence.result_references)),
        "result_hashes": _json_value(list(evidence.result_hashes)),
    }


def _profile_to_json(profile: CodingProfile) -> dict[str, JSONValue]:
    return {
        "id": profile.id,
        "name": profile.name,
        "repo_id": profile.repo_id,
        "revision": profile.revision,
        "quantization": profile.quantization,
        "runtime_commit": profile.runtime_commit,
        "mlx_version": profile.mlx_version,
        "template_sha256": profile.template_sha256,
        "template_assets": _json_value(profile.template_assets),
        "launch_settings": _json_value(profile.launch_settings),
        "system": profile.system,
        "temperature": profile.temperature,
        "top_p": profile.top_p,
        "max_tokens": profile.max_tokens,
        "max_ctx": profile.max_ctx,
        "seed": profile.seed,
        "enable_thinking": profile.enable_thinking,
    }


def _entry_to_json(entry: ProfileEntry) -> dict[str, JSONValue]:
    return {
        "profile": _profile_to_json(entry.profile),
        "evidence": [_evidence_to_json(value) for value in entry.evidence],
    }


def _input_to_json(value: ComparisonInput) -> dict[str, JSONValue]:
    return {
        "endpoint": value.endpoint,
        "profiles": [_entry_to_json(entry) for entry in value.profiles],
        "snapshot_paths": [str(path) for path in value.snapshot_paths],
        "verified_asset_hashes": [
            _json_value(item) for item in value.verified_asset_hashes
        ],
        "runtime_evidence": _json_value(value.runtime_evidence),
        "install_evidence": _json_value(value.install_evidence),
        "launch_evidence": _json_value(value.launch_evidence),
        "provenance": _json_value(value.provenance),
        "process_identity": _identity_to_json(value.process_identity),
        "isolation_evidence": _json_value(value.isolation_evidence),
        "machine_tier": value.machine_tier,
        "operator_conditions": _json_value(value.operator_conditions),
        "profile_order": _json_value(list(value.profile_order))
        if value.profile_order
        else None,
        "task_id": value.task_id,
        "check_id": value.check_id,
        "prompt": value.prompt,
        "result_path": str(value.result_path) if value.result_path else None,
    }


def _sample_to_json(sample: MemorySample) -> dict[str, JSONValue]:
    return {
        "timestamp": sample.timestamp,
        "rss_gib": sample.rss_gib,
        "available_gib": sample.available_gib,
        "scope": sample.scope,
    }


def _trial_to_json(trial: TrialResult) -> dict[str, JSONValue]:
    return {
        "profile_id": trial.profile_id,
        "repeat_index": trial.repeat_index,
        "state": trial.state,
        "error": trial.error,
        "cancelled": trial.cancelled,
        "payload": _json_value(trial.payload),
        "answer": trial.answer,
        "reasoning": trial.reasoning,
        "tool_calls": _json_value(trial.tool_calls),
        "response_model": trial.response_model,
        "finish_reason": trial.finish_reason,
        "stream_complete": trial.stream_complete,
        "quality_pass": trial.quality_pass,
        "quality_reason": trial.quality_reason,
        "prompt_tokens": trial.prompt_tokens,
        "completion_tokens": trial.completion_tokens,
        "prompt_estimated": trial.prompt_estimated,
        "completion_estimated": trial.completion_estimated,
        "cached_prompt_tokens": trial.cached_prompt_tokens,
        "first_output_s": trial.first_output_s,
        "answer_started_s": trial.answer_started_s,
        "total_s": trial.total_s,
        "rss_samples": [_sample_to_json(value) for value in trial.rss_samples],
        "sample_scope": trial.sample_scope,
        "process_identity_before": _identity_to_json(trial.process_identity_before),
        "process_identity_after": _identity_to_json(trial.process_identity_after),
    }


def _result_to_json(value: ComparisonResult) -> dict[str, JSONValue]:
    return {
        "schema_version": 1,
        "run_id": value.run_id,
        "status": value.status,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "error": value.error,
        "input": _input_to_json(value.comparison),
        "profile_fingerprints": [
            entry.profile.fingerprint for entry in value.comparison.profiles
        ],
        "trials": [_trial_to_json(trial) for trial in value.trials],
        "summary": _json_value(value.summary),
    }


def encode_comparison(value: ComparisonResult) -> str:
    """Encode one result with strict JSON number handling."""
    return json.dumps(
        _result_to_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _require_dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ComparisonValidationError(f"{label} must be an object")
    return value


def _required(table: dict[str, Any], key: str, label: str) -> object:
    if key not in table:
        raise ComparisonValidationError(f"{label}.{key} is required")
    return table[key]


def _check_keys(table: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise ComparisonValidationError(
            f"{label} has unknown keys: {', '.join(sorted(unknown))}"
        )


def _string_value(table: dict[str, Any], key: str, label: str) -> str:
    value = _required(table, key, label)
    if not isinstance(value, str):
        raise ComparisonValidationError(f"{label}.{key} must be a string")
    return value


def _profile_from_json(value: object) -> CodingProfile:
    table = _require_dict(value, "profile")
    _check_keys(
        table,
        {
            "id",
            "name",
            "repo_id",
            "revision",
            "quantization",
            "runtime_commit",
            "mlx_version",
            "template_sha256",
            "template_assets",
            "launch_settings",
            "system",
            "temperature",
            "top_p",
            "max_tokens",
            "max_ctx",
            "seed",
            "enable_thinking",
        },
        "profile",
    )
    assets = _require_dict(
        _required(table, "template_assets", "profile"), "profile.template_assets"
    )
    launch = _require_dict(
        _required(table, "launch_settings", "profile"), "profile.launch_settings"
    )

    def number(key: str) -> int | float:
        number_value = _required(table, key, "profile")
        if type(number_value) not in {int, float} or isinstance(number_value, bool):
            raise ComparisonValidationError(f"profile.{key} must be a number")
        return cast(int | float, number_value)

    def integer(key: str) -> int:
        integer_value = number(key)
        if type(integer_value) is not int:
            raise ComparisonValidationError(f"profile.{key} must be an integer")
        return integer_value

    thinking = _required(table, "enable_thinking", "profile")
    if type(thinking) is not bool:
        raise ComparisonValidationError("profile.enable_thinking must be a bool")
    profile = CodingProfile(
        id=_string_value(table, "id", "profile"),
        name=_string_value(table, "name", "profile"),
        repo_id=_string_value(table, "repo_id", "profile"),
        revision=_string_value(table, "revision", "profile"),
        quantization=_string_value(table, "quantization", "profile"),
        runtime_commit=_string_value(table, "runtime_commit", "profile"),
        mlx_version=_string_value(table, "mlx_version", "profile"),
        template_sha256=_string_value(table, "template_sha256", "profile"),
        template_assets={key: str(item) for key, item in assets.items()},
        launch_settings={key: _json_value(item) for key, item in launch.items()},
        system=_string_value(table, "system", "profile"),
        temperature=float(number("temperature")),
        top_p=float(number("top_p")),
        max_tokens=integer("max_tokens"),
        max_ctx=integer("max_ctx"),
        seed=integer("seed"),
        enable_thinking=thinking,
    )
    if profile.fingerprint != profile_fingerprint(profile):
        raise ComparisonValidationError(
            "profile fingerprint could not be reconstructed"
        )
    return profile


def _evidence_from_json(
    value: object, profile: CodingProfile
) -> RecommendationEvidence:
    table = _require_dict(value, "evidence")
    _check_keys(
        table,
        {
            "owner",
            "source",
            "status",
            "revocation_reason",
            "tested_at",
            "expires_at",
            "machine_tier",
            "task_id",
            "check_id",
            "prompt_sha256",
            "runtime_freeze",
            "profile_fingerprint",
            "result_references",
            "result_hashes",
        },
        "evidence",
    )
    try:
        tested_at = datetime.fromisoformat(
            _string_value(table, "tested_at", "evidence")
        )
        expires_at = datetime.fromisoformat(
            _string_value(table, "expires_at", "evidence")
        )
    except ValueError as exc:
        raise ComparisonValidationError("evidence timestamps are invalid") from exc
    if tested_at.tzinfo is None or expires_at.tzinfo is None:
        raise ComparisonValidationError("evidence timestamps need timezones")
    references = _required(table, "result_references", "evidence")
    hashes = _required(table, "result_hashes", "evidence")
    if not isinstance(references, list) or not all(
        isinstance(item, str) for item in references
    ):
        raise ComparisonValidationError("evidence references are invalid")
    if not isinstance(hashes, list) or not all(
        isinstance(item, str) for item in hashes
    ):
        raise ComparisonValidationError("evidence hashes are invalid")
    status = _string_value(table, "status", "evidence")
    if status not in {"candidate", "unqualified", "qualified", "revoked", "withdrawn"}:
        raise ComparisonValidationError("evidence status is invalid")
    reason = table.get("revocation_reason")
    if reason is not None and not isinstance(reason, str):
        raise ComparisonValidationError("evidence revocation reason is invalid")
    return RecommendationEvidence(
        owner=_string_value(table, "owner", "evidence"),
        source=_string_value(table, "source", "evidence"),
        status=status,  # type: ignore[arg-type]
        revocation_reason=reason,
        tested_at=tested_at,
        expires_at=expires_at,
        machine_tier=_string_value(table, "machine_tier", "evidence"),
        task_id=_string_value(table, "task_id", "evidence"),
        check_id=_string_value(table, "check_id", "evidence"),
        prompt_sha256=_string_value(table, "prompt_sha256", "evidence"),
        runtime_freeze=_string_value(table, "runtime_freeze", "evidence"),
        profile_fingerprint=_string_value(table, "profile_fingerprint", "evidence"),
        result_references=tuple(references),
        result_hashes=tuple(hashes),
    )


def _entry_from_json(value: object) -> ProfileEntry:
    table = _require_dict(value, "profile entry")
    _check_keys(table, {"profile", "evidence"}, "profile entry")
    profile = _profile_from_json(_required(table, "profile", "profile entry"))
    raw_evidence = _required(table, "evidence", "profile entry")
    if not isinstance(raw_evidence, list):
        raise ComparisonValidationError("profile entry evidence must be a list")
    return ProfileEntry(
        profile=profile,
        evidence=tuple(_evidence_from_json(item, profile) for item in raw_evidence),
    )


def _input_from_json(value: object) -> ComparisonInput:
    table = _require_dict(value, "input")
    _check_keys(
        table,
        {
            "endpoint",
            "profiles",
            "snapshot_paths",
            "verified_asset_hashes",
            "runtime_evidence",
            "install_evidence",
            "launch_evidence",
            "provenance",
            "process_identity",
            "isolation_evidence",
            "machine_tier",
            "operator_conditions",
            "profile_order",
            "task_id",
            "check_id",
            "prompt",
            "result_path",
        },
        "input",
    )
    raw_profiles = _required(table, "profiles", "input")
    paths = _required(table, "snapshot_paths", "input")
    hashes = _required(table, "verified_asset_hashes", "input")
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
        endpoint=_string_value(table, "endpoint", "input"),
        profiles=tuple(_entry_from_json(item) for item in raw_profiles),
        snapshot_paths=tuple(Path(item) for item in paths),
        verified_asset_hashes=tuple(
            {
                key: str(item)
                for key, item in _require_dict(item, "asset hashes").items()
            }
            for item in hashes
        ),
        runtime_evidence=_require_dict(
            _required(table, "runtime_evidence", "input"), "runtime_evidence"
        ),
        install_evidence=_require_dict(
            _required(table, "install_evidence", "input"), "install_evidence"
        ),
        launch_evidence=_require_dict(
            _required(table, "launch_evidence", "input"), "launch_evidence"
        ),
        provenance=_require_dict(_required(table, "provenance", "input"), "provenance"),
        process_identity=_identity_from_json(table.get("process_identity")),
        isolation_evidence=_require_dict(
            _required(table, "isolation_evidence", "input"), "isolation_evidence"
        ),
        machine_tier=_string_value(table, "machine_tier", "input"),
        operator_conditions=_require_dict(
            _required(table, "operator_conditions", "input"), "operator_conditions"
        ),
        profile_order=tuple(profile_order) if profile_order else None,
        task_id=_string_value(table, "task_id", "input"),
        check_id=_string_value(table, "check_id", "input"),
        prompt=_string_value(table, "prompt", "input"),
        result_path=Path(table["result_path"])
        if isinstance(table.get("result_path"), str)
        else None,
    )


def _sample_from_json(value: object) -> MemorySample:
    table = _require_dict(value, "memory sample")
    _check_keys(
        table, {"timestamp", "rss_gib", "available_gib", "scope"}, "memory sample"
    )
    timestamp = _required(table, "timestamp", "memory sample")
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
        scope=_string_value(table, "scope", "memory sample"),
    )


def _trial_from_json(value: object) -> TrialResult:
    table = _require_dict(value, "trial")
    _check_keys(
        table,
        {
            "profile_id",
            "repeat_index",
            "state",
            "error",
            "cancelled",
            "payload",
            "answer",
            "reasoning",
            "tool_calls",
            "response_model",
            "finish_reason",
            "stream_complete",
            "quality_pass",
            "quality_reason",
            "prompt_tokens",
            "completion_tokens",
            "prompt_estimated",
            "completion_estimated",
            "cached_prompt_tokens",
            "first_output_s",
            "answer_started_s",
            "total_s",
            "rss_samples",
            "sample_scope",
            "process_identity_before",
            "process_identity_after",
        },
        "trial",
    )
    repeat_index = _required(table, "repeat_index", "trial")
    if type(repeat_index) is not int:
        raise ComparisonValidationError("trial repeat index is invalid")
    raw_tools = _required(table, "tool_calls", "trial")
    raw_samples = _required(table, "rss_samples", "trial")
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
    raw_state = _string_value(table, "state", "trial")
    if raw_state not in {"not_attempted", "attempted", "completed"}:
        raise ComparisonValidationError("trial state is invalid")
    payload = _require_dict(_required(table, "payload", "trial"), "trial.payload")
    return TrialResult(
        profile_id=_string_value(table, "profile_id", "trial"),
        repeat_index=repeat_index,
        state=raw_state,  # type: ignore[arg-type]
        error=table.get("error"),
        cancelled=table.get("cancelled") is True,
        payload=payload,
        answer=_string_value(table, "answer", "trial"),
        reasoning=_string_value(table, "reasoning", "trial"),
        tool_calls=tuple(_require_dict(item, "tool call") for item in raw_tools),
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
        sample_scope=_string_value(table, "sample_scope", "trial"),
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
    table = _require_dict(document, "comparison")
    _check_keys(
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
    )
    if table.get("schema_version") != 1:
        raise ComparisonPersistenceError("unsupported comparison schema")
    raw_trials = _required(table, "trials", "comparison")
    if not isinstance(raw_trials, list):
        raise ComparisonValidationError("comparison.trials must be a list")
    status = table.get("status")
    if status not in {"running", "completed", "failed", "cancelled", "interrupted"}:
        raise ComparisonValidationError("comparison status is invalid")
    result = ComparisonResult(
        run_id=_string_value(table, "run_id", "comparison"),
        status=status,  # type: ignore[arg-type]
        comparison=_input_from_json(_required(table, "input", "comparison")),
        trials=tuple(_trial_from_json(item) for item in raw_trials),
        created_at=_string_value(table, "created_at", "comparison"),
        updated_at=_string_value(table, "updated_at", "comparison"),
        summary=_require_dict(_required(table, "summary", "comparison"), "summary"),
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


def _atomic_write(target: Path, encoded: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    except (OSError, ValueError) as exc:
        raise ComparisonPersistenceError(f"could not save {target}") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def save_comparison(value: ComparisonResult, path: Path | None = None) -> Path:
    """Atomically save a checkpoint, leaving the prior file untouched on failure."""
    target = path or value.comparison.result_path
    if target is None:
        target = comparison_dir() / f"{value.run_id}.json"
    target = Path(target)
    _atomic_write(target, encode_comparison(value).encode("utf-8"))
    return target


def load_comparison(path: Path) -> ComparisonResult:
    """Load one result without allowing corrupt files to affect other runs."""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ComparisonPersistenceError(f"could not read comparison {path}") from exc
    return decode_comparison(content)


def _choice_json(value: SavedChoice) -> dict[str, JSONValue]:
    return {
        "schema_version": 1,
        "run_id": value.run_id,
        "result_path": str(value.result_path),
        "decision": value.decision,
        "profile_id": value.profile_id,
        "profile_fingerprint": value.profile_fingerprint,
        "reason": value.reason,
        "created_at": value.created_at,
    }


def save_choice(value: SavedChoice, path: Path | None = None) -> Path:
    target = path or choice_path()
    encoded = json.dumps(
        _choice_json(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    _atomic_write(target, encoded)
    return target


def load_choice(path: Path | None = None) -> SavedChoice:
    target = path or choice_path()
    try:
        document: object = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ComparisonPersistenceError(f"could not read choice {target}") from exc
    table = _require_dict(document, "choice")
    _check_keys(
        table,
        {
            "schema_version",
            "run_id",
            "result_path",
            "decision",
            "profile_id",
            "profile_fingerprint",
            "reason",
            "created_at",
        },
        "choice",
    )
    if table.get("schema_version") != 1:
        raise ComparisonPersistenceError("unsupported choice schema")
    decision = table.get("decision")
    if decision not in {"keep", "retain", "reject"}:
        raise ComparisonValidationError("choice.decision is invalid")
    profile_id = table.get("profile_id")
    fingerprint = table.get("profile_fingerprint")
    if profile_id is not None and not isinstance(profile_id, str):
        raise ComparisonValidationError("choice.profile_id must be a string or null")
    if fingerprint is not None and not isinstance(fingerprint, str):
        raise ComparisonValidationError(
            "choice.profile_fingerprint must be a string or null"
        )
    if decision == "keep" and (not profile_id or not fingerprint):
        raise ComparisonValidationError("a kept choice requires a profile identity")
    if decision != "keep" and (profile_id is not None or fingerprint is not None):
        raise ComparisonValidationError("retain/reject cannot select a profile")
    reason = _string_value(table, "reason", "choice")
    if decision == "reject" and not reason.strip():
        raise ComparisonValidationError("rejecting both profiles requires a reason")
    return SavedChoice(
        run_id=_string_value(table, "run_id", "choice"),
        result_path=Path(_string_value(table, "result_path", "choice")),
        decision=cast(DecisionKind, decision),
        profile_id=profile_id,
        profile_fingerprint=fingerprint,
        reason=reason,
        created_at=_string_value(table, "created_at", "choice"),
    )


def _decision_payload(choice: SavedChoice, status: str) -> dict[str, JSONValue]:
    return {
        "status": status,
        "run_id": choice.run_id,
        "decision": choice.decision,
        "profile_id": choice.profile_id,
        "profile_fingerprint": choice.profile_fingerprint,
        "reason": choice.reason,
    }


def choice_is_committed(value: SavedChoice) -> bool:
    """Fail closed across either choice transaction crash boundary."""
    try:
        result = load_comparison(value.result_path)
    except ComparisonValidationError:
        return False
    decision = result.summary.get("decision")
    return (
        result.run_id == value.run_id
        and isinstance(decision, dict)
        and decision == _decision_payload(value, "finalized")
    )


def commit_choice(
    result: ComparisonResult,
    decision: DecisionKind,
    *,
    profile_id: str | None = None,
    reason: str = "",
    path: Path | None = None,
) -> tuple[ComparisonResult, SavedChoice]:
    """Persist pending run → choice → finalized run, rolling back write failures."""
    if result.status != "completed" or result.comparison.result_path is None:
        raise ComparisonValidationError(
            "only a completed saved comparison is decidable"
        )
    entry = next(
        (item for item in result.comparison.profiles if item.profile.id == profile_id),
        None,
    )
    if decision == "keep" and entry is None:
        raise ComparisonValidationError("kept profile is not part of this comparison")
    if decision != "keep" and profile_id is not None:
        raise ComparisonValidationError("retain/reject cannot select a profile")
    if decision == "reject" and not reason.strip():
        raise ComparisonValidationError("rejecting both profiles requires a reason")
    choice = SavedChoice(
        run_id=result.run_id,
        result_path=result.comparison.result_path,
        decision=decision,
        profile_id=profile_id,
        profile_fingerprint=entry.profile.fingerprint if entry is not None else None,
        reason=reason.strip(),
    )
    target = path or choice_path()
    old_choice = target.read_bytes() if target.exists() else None
    pending = replace(
        result,
        summary={**result.summary, "decision": _decision_payload(choice, "pending")},
        updated_at=_now_iso(),
    )
    save_comparison(pending)
    try:
        save_choice(choice, target)
        finalized = replace(
            pending,
            summary={
                **pending.summary,
                "decision": _decision_payload(choice, "finalized"),
            },
            updated_at=_now_iso(),
        )
        save_comparison(finalized)
    except ComparisonPersistenceError:
        try:
            if old_choice is None:
                target.unlink(missing_ok=True)
            else:
                _atomic_write(target, old_choice)
            save_comparison(result)
        except ComparisonPersistenceError:
            pass
        raise
    return finalized, choice


# Kept as a readable alias for callers that prefer the noun used in the plan.
serialize_comparison = encode_comparison
deserialize_comparison = decode_comparison
