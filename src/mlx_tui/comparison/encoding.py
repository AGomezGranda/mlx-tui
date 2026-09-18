"""Comparison encoding: UI-free JSON rendering."""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import cast

from mlx_tui.comparison.contracts import (
    ComparisonInput,
    ComparisonResult,
    ComparisonValidationError,
    JSONValue,
    TrialResult,
    _finite_non_negative,
)
from mlx_tui.process import ProcessIdentity
from mlx_tui.profiles import CodingProfile, ProfileEntry, RecommendationEvidence


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
    return cast(dict[str, JSONValue], _json_value(asdict(profile)))


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


def _trial_to_json(trial: TrialResult) -> dict[str, JSONValue]:
    return cast(dict[str, JSONValue], _json_value(asdict(trial)))


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
