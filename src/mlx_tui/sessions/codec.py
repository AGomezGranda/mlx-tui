"""Session codec: UI-free validation and JSON encode/decode."""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, fields
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from mlx_tui.attachments import AttachmentSnapshot
from mlx_tui.json_util import (
    JSONValue,
    bool_field,
    check_keys,
    json_compatible,
    optional_string_field,
    required_field,
    string_field,
)
from mlx_tui.sessions.errors import SessionPersistenceError, SessionValidationError
from mlx_tui.sessions.models import (
    SCHEMA_VERSION,
    ChatSession,
    RequestSettings,
    SessionTurn,
)

SUPPORTED_SCHEMA_VERSIONS = frozenset({1, SCHEMA_VERSION})
SHA256_HEX_LENGTH = 64
OUTCOMES = frozenset(
    {
        "running",
        "success",
        "length_capped",
        "tool_only",
        "empty",
        "incomplete",
        "failed",
        "cancelled",
        "interrupted",
    }
)
ERROR_CATEGORIES = frozenset(
    {"transport", "context", "setup", "identity", "cancelled", "server", "stream"}
)
PROVENANCE = frozenset({"requested", "verified"})
MAX_AGGREGATE_BYTES = 1024 * 1024


def _optional_bool(table: dict[str, Any], key: str, label: str) -> bool | None:
    value = required_field(table, key, label, error=SessionValidationError)
    if value is not None and type(value) is not bool:
        raise SessionValidationError(f"{label}.{key} must be a boolean or null")
    return value


def _count_value(
    table: dict[str, Any], key: str, label: str, minimum: int = 0
) -> int | None:
    value = required_field(table, key, label, error=SessionValidationError)
    if value is None:
        return None
    if type(value) is not int or isinstance(value, bool) or value < minimum:
        raise SessionValidationError(f"{label}.{key} must be an int >= {minimum}")
    return value


def _required_count(table: dict[str, Any], key: str, label: str, minimum: int) -> int:
    value = _count_value(table, key, label, minimum)
    if value is None:
        raise SessionValidationError(f"{label}.{key} must be an int >= {minimum}")
    return value


def _finite_value(table: dict[str, Any], key: str, label: str) -> float | None:
    value = required_field(table, key, label, error=SessionValidationError)
    if value is None:
        return None
    if (
        type(value) not in {int, float}
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise SessionValidationError(f"{label}.{key} must be a finite number or null")
    return float(value)


def _session_uuid(value: object, label: str) -> str:
    if type(value) is not str:
        raise SessionValidationError(f"{label} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise SessionValidationError(f"{label} is not a valid UUID") from exc
    if str(parsed) != value:
        raise SessionValidationError(f"{label} is not a canonical UUID")
    return value


def _timestamp_value(table: dict[str, Any], key: str, label: str) -> str:
    value = string_field(table, key, label, error=SessionValidationError)
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise SessionValidationError(f"{label}.{key} is not an ISO timestamp") from exc
    return value


def _provenance_value(table: dict[str, Any], key: str, label: str) -> str | None:
    value = optional_string_field(table, key, label, error=SessionValidationError)
    if value is not None and value not in PROVENANCE:
        raise SessionValidationError(
            f"{label}.{key} must be requested, verified, or null"
        )
    return value


_ATTACHMENT_KEYS = {f.name for f in fields(AttachmentSnapshot)}


def _validate_attachment(snapshot: AttachmentSnapshot) -> AttachmentSnapshot:
    if not isinstance(snapshot, AttachmentSnapshot):
        raise SessionValidationError("attachment must be an AttachmentSnapshot")
    if not isinstance(snapshot.selected_path, Path) or not str(snapshot.selected_path):
        raise SessionValidationError("attachment.selected_path must be a path")
    if (
        not isinstance(snapshot.resolved_path, Path)
        or not snapshot.resolved_path.is_absolute()
    ):
        raise SessionValidationError("attachment.resolved_path must be absolute")
    if (
        type(snapshot.byte_length) is not int
        or snapshot.byte_length < 0
        or snapshot.byte_length > 256 * 1024
    ):
        raise SessionValidationError("attachment.byte_length is out of range")
    if (
        type(snapshot.sha256) is not str
        or len(snapshot.sha256) != SHA256_HEX_LENGTH
        or any(char not in "0123456789abcdef" for char in snapshot.sha256)
    ):
        raise SessionValidationError("attachment.sha256 must be a lowercase SHA-256")
    if type(snapshot.content) is not str or "\x00" in snapshot.content:
        raise SessionValidationError("attachment.content must be UTF-8 text")
    raw = snapshot.content.encode("utf-8")
    if len(raw) != snapshot.byte_length:
        raise SessionValidationError("attachment.byte_length does not match content")
    if len(raw) > 256 * 1024:
        raise SessionValidationError("attachment content exceeds 256 KiB")
    if sha256(raw).hexdigest() != snapshot.sha256:
        raise SessionValidationError("attachment.sha256 does not match content")
    return snapshot


def _attachment_from_json(value: object) -> AttachmentSnapshot:
    if type(value) is not dict:
        raise SessionValidationError("attachment must be an object")
    table: dict[str, Any] = value  # type: ignore[assignment]
    check_keys(table, _ATTACHMENT_KEYS, "attachment", error=SessionValidationError)
    selected = string_field(
        table, "selected_path", "attachment", error=SessionValidationError
    )
    resolved = string_field(
        table, "resolved_path", "attachment", error=SessionValidationError
    )
    byte_length = required_field(
        table, "byte_length", "attachment", error=SessionValidationError
    )
    if type(byte_length) is not int or isinstance(byte_length, bool):
        raise SessionValidationError("attachment.byte_length must be an integer")
    snapshot = AttachmentSnapshot(
        selected_path=Path(selected),
        resolved_path=Path(resolved),
        byte_length=byte_length,
        sha256=string_field(
            table, "sha256", "attachment", error=SessionValidationError
        ),
        content=string_field(
            table, "content", "attachment", error=SessionValidationError
        ),
    )
    return _validate_attachment(snapshot)


def _attachments_from_json(value: object, label: str) -> tuple[AttachmentSnapshot, ...]:
    if type(value) is not list:
        raise SessionValidationError(f"{label} must be a list")
    try:
        snapshots = tuple(_attachment_from_json(item) for item in value)
    except SessionValidationError as exc:
        raise SessionValidationError(f"{label} contains an invalid attachment") from exc
    return snapshots


def _attachments_to_json(
    snapshots: tuple[AttachmentSnapshot, ...],
) -> list[JSONValue]:
    result: list[JSONValue] = []
    for item in snapshots:
        snapshot = _validate_attachment(item)
        result.append(
            {
                "selected_path": str(snapshot.selected_path),
                "resolved_path": str(snapshot.resolved_path),
                "byte_length": snapshot.byte_length,
                "sha256": snapshot.sha256,
                "content": snapshot.content,
            }
        )
    return result


_REQUEST_KEYS = {f.name for f in fields(RequestSettings)}


def _settings_from_json(table: object) -> RequestSettings:
    if type(table) is not dict:
        raise SessionValidationError("session settings must be an object")
    mapping: dict[str, Any] = table  # type: ignore[assignment]
    check_keys(mapping, _REQUEST_KEYS, "settings", error=SessionValidationError)
    temperature = required_field(
        mapping, "temperature", "settings", error=SessionValidationError
    )
    if (
        type(temperature) not in {int, float}
        or isinstance(temperature, bool)
        or not math.isfinite(temperature)
        or temperature < 0
    ):
        raise SessionValidationError("settings.temperature must be finite and >= 0")
    top_p = required_field(mapping, "top_p", "settings", error=SessionValidationError)
    if (
        type(top_p) not in {int, float}
        or isinstance(top_p, bool)
        or not math.isfinite(top_p)
        or not 0 < top_p <= 1
    ):
        raise SessionValidationError("settings.top_p must be finite within (0, 1]")
    launch = required_field(
        mapping, "profile_launch_settings", "settings", error=SessionValidationError
    )
    launch_settings: dict[str, JSONValue] | None = None
    if launch is not None:
        if type(launch) is not dict:
            raise SessionValidationError("settings.profile_launch_settings invalid")
        launch_settings = {
            key: json_compatible(
                item, "settings.profile_launch_settings", error=SessionValidationError
            )
            for key, item in launch.items()
        }
    runtime_commit = optional_string_field(
        mapping, "profile_runtime_commit", "settings", error=SessionValidationError
    )
    runtime_provenance = _provenance_value(
        mapping, "profile_runtime_provenance", "settings"
    )
    template_sha = optional_string_field(
        mapping, "profile_template_sha256", "settings", error=SessionValidationError
    )
    template_provenance = _provenance_value(
        mapping, "profile_template_provenance", "settings"
    )
    launch_provenance = _provenance_value(
        mapping, "profile_launch_provenance", "settings"
    )
    for known, provenance, name in (
        (runtime_commit, runtime_provenance, "profile_runtime"),
        (template_sha, template_provenance, "profile_template"),
        (launch_settings, launch_provenance, "profile_launch"),
    ):
        if (known is None) != (provenance is None):
            raise SessionValidationError(
                f"settings.{name} value and provenance must agree on null"
            )
    return RequestSettings(
        model=optional_string_field(
            mapping, "model", "settings", error=SessionValidationError
        ),
        repo_id=optional_string_field(
            mapping, "repo_id", "settings", error=SessionValidationError
        ),
        revision=optional_string_field(
            mapping, "revision", "settings", error=SessionValidationError
        ),
        system=string_field(
            mapping, "system", "settings", error=SessionValidationError
        ),
        temperature=float(temperature),
        top_p=float(top_p),
        max_tokens=_required_count(mapping, "max_tokens", "settings", 1),
        max_ctx=_required_count(mapping, "max_ctx", "settings", 1),
        seed=_count_value(mapping, "seed", "settings"),
        enable_thinking=_optional_bool(mapping, "enable_thinking", "settings"),
        profile_id=optional_string_field(
            mapping, "profile_id", "settings", error=SessionValidationError
        ),
        profile_fingerprint=optional_string_field(
            mapping, "profile_fingerprint", "settings", error=SessionValidationError
        ),
        profile_modified=bool_field(
            mapping, "profile_modified", "settings", error=SessionValidationError
        ),
        profile_runtime_commit=runtime_commit,
        profile_runtime_provenance=runtime_provenance,
        profile_template_sha256=template_sha,
        profile_template_provenance=template_provenance,
        profile_launch_settings=launch_settings,
        profile_launch_provenance=launch_provenance,
    )


_TURN_KEYS = {f.name for f in fields(SessionTurn)} - {"attachments"}

_TURN_KEYS_V2 = _TURN_KEYS | {"attachments"}


def _tool_calls_from_json(raw: object) -> tuple[dict[str, JSONValue], ...]:
    if type(raw) is not list:
        raise SessionValidationError("attempt.tool_calls must be a list")
    tool_calls: list[dict[str, JSONValue]] = []
    for item in raw:
        if type(item) is not dict:
            raise SessionValidationError("attempt.tool_calls entries must be objects")
        checked = json_compatible(
            item, "attempt.tool_calls", error=SessionValidationError
        )
        if type(checked) is not dict:  # pragma: no cover - guarded above
            raise SessionValidationError("attempt.tool_calls entries must be objects")
        tool_calls.append(checked)
    return tuple(tool_calls)


def _check_turn_consistency(
    outcome: str,
    answer: str,
    tool_calls: tuple[dict[str, JSONValue], ...],
    sent_content: str,
    error_category: str | None,
) -> None:
    if outcome not in OUTCOMES:
        raise SessionValidationError("attempt.outcome is invalid")
    if outcome == "success" and answer == "" and not tool_calls:
        raise SessionValidationError("successful attempts need an answer or tool calls")
    if sent_content == "":
        raise SessionValidationError("attempts need sent user content")
    if error_category is not None and error_category not in ERROR_CATEGORIES:
        raise SessionValidationError("attempt.error_category is invalid")


def _turn_from_json(table: object, version: int) -> SessionTurn:
    if type(table) is not dict:
        raise SessionValidationError("session attempt must be an object")
    mapping: dict[str, Any] = table  # type: ignore[assignment]
    check_keys(
        mapping,
        _TURN_KEYS_V2 if version == SCHEMA_VERSION else _TURN_KEYS,
        "attempt",
        error=SessionValidationError,
    )
    raw_included = required_field(
        mapping, "included_turn_ids", "attempt", error=SessionValidationError
    )
    if type(raw_included) is not list:
        raise SessionValidationError("attempt.included_turn_ids must be a list")
    included = tuple(
        _session_uuid(item, "attempt.included_turn_ids entry") for item in raw_included
    )
    tool_calls = _tool_calls_from_json(
        required_field(mapping, "tool_calls", "attempt", error=SessionValidationError)
    )
    outcome = string_field(mapping, "outcome", "attempt", error=SessionValidationError)
    answer = string_field(mapping, "answer", "attempt", error=SessionValidationError)
    sent_content = string_field(
        mapping, "sent_content", "attempt", error=SessionValidationError
    )
    error_category = optional_string_field(
        mapping, "error_category", "attempt", error=SessionValidationError
    )
    _check_turn_consistency(outcome, answer, tool_calls, sent_content, error_category)
    attachments = (
        _attachments_from_json(
            required_field(
                mapping, "attachments", "attempt", error=SessionValidationError
            ),
            "attempt.attachments",
        )
        if version == SCHEMA_VERSION
        else ()
    )
    for key in ("first_output_s", "answer_started_s", "total_s"):
        duration = _finite_value(mapping, key, "attempt")
        if duration is not None and duration < 0:
            raise SessionValidationError(f"attempt.{key} must be non-negative or null")
    return SessionTurn(
        turn_id=_session_uuid(
            required_field(mapping, "turn_id", "attempt", error=SessionValidationError),
            "attempt.turn_id",
        ),
        created_at=_timestamp_value(mapping, "created_at", "attempt"),
        original_draft=string_field(
            mapping, "original_draft", "attempt", error=SessionValidationError
        ),
        sent_content=sent_content,
        settings=_settings_from_json(
            required_field(mapping, "settings", "attempt", error=SessionValidationError)
        ),
        attachments=attachments,
        included_turn_ids=included,
        estimated_input=_count_value(mapping, "estimated_input", "attempt"),
        reserved_output=_count_value(mapping, "reserved_output", "attempt"),
        excluded_messages=_count_value(mapping, "excluded_messages", "attempt"),
        answer=answer,
        reasoning=string_field(
            mapping, "reasoning", "attempt", error=SessionValidationError
        ),
        tool_calls=tuple(tool_calls),
        response_model=optional_string_field(
            mapping, "response_model", "attempt", error=SessionValidationError
        ),
        finish_reason=optional_string_field(
            mapping, "finish_reason", "attempt", error=SessionValidationError
        ),
        skipped_frames=_required_count(mapping, "skipped_frames", "attempt", 0),
        stream_complete=bool_field(
            mapping, "stream_complete", "attempt", error=SessionValidationError
        ),
        outcome=outcome,
        prompt_tokens=_count_value(mapping, "prompt_tokens", "attempt"),
        completion_tokens=_count_value(mapping, "completion_tokens", "attempt"),
        total_tokens=_count_value(mapping, "total_tokens", "attempt"),
        cached_prompt_tokens=_count_value(mapping, "cached_prompt_tokens", "attempt"),
        first_output_s=_finite_value(mapping, "first_output_s", "attempt"),
        answer_started_s=_finite_value(mapping, "answer_started_s", "attempt"),
        total_s=_finite_value(mapping, "total_s", "attempt"),
        error_category=error_category,
        error_detail=optional_string_field(
            mapping, "error_detail", "attempt", error=SessionValidationError
        ),
    )


_SESSION_KEYS = {f.name for f in fields(ChatSession)} - {"attachments"}

_SESSION_KEYS_V2 = _SESSION_KEYS | {"attachments"}


def _attempt_aggregate_bytes(turn: SessionTurn) -> int:
    parts = [
        turn.original_draft,
        turn.sent_content,
        turn.answer,
        turn.reasoning,
        turn.error_detail or "",
    ]
    size = sum(len(part.encode("utf-8")) for part in parts)
    size += len(
        json.dumps(
            list(turn.tool_calls),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=False,
        ).encode("utf-8")
    )
    size += sum(len(snapshot.content.encode("utf-8")) for snapshot in turn.attachments)
    return size


def _check_attempt_references(attempts: tuple[SessionTurn, ...]) -> None:
    seen: set[str] = set()
    for turn in attempts:
        if turn.turn_id in seen:
            raise SessionValidationError("session attempt IDs must be unique")
        seen.add(turn.turn_id)
    by_id = {turn.turn_id: index for index, turn in enumerate(attempts)}
    for index, turn in enumerate(attempts):
        if len(set(turn.included_turn_ids)) != len(turn.included_turn_ids):
            raise SessionValidationError("included turn references must be unique")
        for reference in turn.included_turn_ids:
            position = by_id.get(reference)
            if position is None or position >= index:
                raise SessionValidationError(
                    "included turns must reference prior attempts only"
                )
            if attempts[position].outcome != "success":
                raise SessionValidationError(
                    "included turns must reference successful attempts"
                )


def _session_from_json(document: object, expected_id: str | None = None) -> ChatSession:
    if type(document) is not dict:
        raise SessionValidationError("session must be a JSON object")
    table: dict[str, Any] = document  # type: ignore[assignment]
    version = required_field(
        table, "schema_version", "session", error=SessionValidationError
    )
    if type(version) is not int or isinstance(version, bool):
        raise SessionValidationError("session.schema_version must be an integer")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SessionPersistenceError(f"unsupported session schema: {version}")
    check_keys(
        table,
        _SESSION_KEYS_V2 if version == SCHEMA_VERSION else _SESSION_KEYS,
        "session",
        error=SessionValidationError,
    )
    session_id = _session_uuid(
        required_field(table, "session_id", "session", error=SessionValidationError),
        "session_id",
    )
    if expected_id is not None and session_id != expected_id:
        raise SessionValidationError("session filename does not match session_id")
    raw_attempts = required_field(
        table, "attempts", "session", error=SessionValidationError
    )
    if type(raw_attempts) is not list:
        raise SessionValidationError("session.attempts must be a list")
    attempts = tuple(_turn_from_json(item, version) for item in raw_attempts)
    _check_attempt_references(attempts)
    draft = string_field(table, "draft", "session", error=SessionValidationError)
    attachments = (
        _attachments_from_json(
            required_field(
                table, "attachments", "session", error=SessionValidationError
            ),
            "session.attachments",
        )
        if version == SCHEMA_VERSION
        else ()
    )
    draft_size = len(draft.encode("utf-8")) + sum(
        len(snapshot.content.encode("utf-8")) for snapshot in attachments
    )
    if draft_size > MAX_AGGREGATE_BYTES:
        raise SessionValidationError("session draft exceeds the 1 MiB aggregate bound")
    for turn in attempts:
        if _attempt_aggregate_bytes(turn) > MAX_AGGREGATE_BYTES:
            raise SessionValidationError(
                f"session attempt {turn.turn_id} exceeds the 1 MiB aggregate bound"
            )
    return ChatSession(
        session_id=session_id,
        created_at=_timestamp_value(table, "created_at", "session"),
        updated_at=_timestamp_value(table, "updated_at", "session"),
        settings=_settings_from_json(
            required_field(table, "settings", "session", error=SessionValidationError)
        ),
        draft=draft,
        attachments=attachments,
        attempts=attempts,
        schema_version=SCHEMA_VERSION,
    )


def _settings_to_json(settings: RequestSettings) -> dict[str, JSONValue]:
    return cast(dict[str, JSONValue], asdict(settings))


def _turn_to_json(turn: SessionTurn) -> dict[str, JSONValue]:
    return {
        **asdict(turn),
        "attachments": _attachments_to_json(turn.attachments),
        "included_turn_ids": cast(JSONValue, list(turn.included_turn_ids)),
        "tool_calls": cast(JSONValue, list(turn.tool_calls)),
    }


def _session_to_json(session: ChatSession) -> dict[str, JSONValue]:
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session.session_id,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "settings": _settings_to_json(session.settings),
        "draft": session.draft,
        "attachments": _attachments_to_json(session.attachments),
        "attempts": [_turn_to_json(turn) for turn in session.attempts],
    }


def encode_session(session: ChatSession) -> str:
    """Render canonical JSON after re-validating the in-memory record."""
    validated = _session_from_json(
        _session_to_json(session), expected_id=session.session_id
    )
    try:
        return json.dumps(
            _session_to_json(validated),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        raise SessionValidationError("session holds non-JSON values") from exc


def decode_session(value: str, expected_id: str | None = None) -> ChatSession:
    """Strictly decode schema-v1/v2 JSON without touching running-state mapping."""
    try:
        document: object = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise SessionPersistenceError("session file is corrupt") from exc
    return _session_from_json(document, expected_id=expected_id)
