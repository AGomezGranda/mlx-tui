"""Comparison store: UI-free checkpoints and choice transaction."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import cast

from mlx_tui.comparison_contracts import (
    ComparisonPersistenceError,
    ComparisonResult,
    ComparisonValidationError,
    DecisionKind,
    JSONValue,
    SavedChoice,
    _now_iso,
)
from mlx_tui.comparison_decoding import decode_comparison
from mlx_tui.comparison_encoding import _json_value, encode_comparison
from mlx_tui.json_util import (
    atomic_write_bytes,
    check_keys,
    require_dict,
    string_field,
)


def comparison_dir() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "mlx-tui" / "comparisons"


def choice_path() -> Path:
    return comparison_dir().parent / "choice.json"


def save_comparison(value: ComparisonResult, path: Path | None = None) -> Path:
    """Atomically save a checkpoint, leaving the prior file untouched on failure."""
    target = path or value.comparison.result_path
    if target is None:
        target = comparison_dir() / f"{value.run_id}.json"
    target = Path(target)
    atomic_write_bytes(
        target,
        encode_comparison(value).encode("utf-8"),
        error=ComparisonPersistenceError,
    )
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
        **cast(dict[str, JSONValue], _json_value(asdict(value))),
    }


def save_choice(value: SavedChoice, path: Path | None = None) -> Path:
    target = path or choice_path()
    encoded = json.dumps(
        _choice_json(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    atomic_write_bytes(target, encoded, error=ComparisonPersistenceError)
    return target


def load_choice(path: Path | None = None) -> SavedChoice:
    target = path or choice_path()
    try:
        document: object = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ComparisonPersistenceError(f"could not read choice {target}") from exc
    table = require_dict(document, "choice", error=ComparisonValidationError)
    check_keys(
        table,
        {f.name for f in fields(SavedChoice)} | {"schema_version"},
        "choice",
        error=ComparisonValidationError,
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
    reason = string_field(table, "reason", "choice", error=ComparisonValidationError)
    if decision == "reject" and not reason.strip():
        raise ComparisonValidationError("rejecting both profiles requires a reason")
    return SavedChoice(
        run_id=string_field(table, "run_id", "choice", error=ComparisonValidationError),
        result_path=Path(
            string_field(
                table, "result_path", "choice", error=ComparisonValidationError
            )
        ),
        decision=cast(DecisionKind, decision),
        profile_id=profile_id,
        profile_fingerprint=fingerprint,
        reason=reason,
        created_at=string_field(
            table, "created_at", "choice", error=ComparisonValidationError
        ),
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
                atomic_write_bytes(target, old_choice, error=ComparisonPersistenceError)
            save_comparison(result)
        except ComparisonPersistenceError:
            pass
        raise
    return finalized, choice
