"""Versioned local chat-session records: UI-free storage contract.

One JSON snapshot per session under ``$XDG_STATE_HOME/mlx-tui/sessions`` (or
``~/.local/state/mlx-tui/sessions``). Only structured attempts can preserve
unsuccessful output, exact drafts, and request settings without resending
them, so sessions keep ordered typed attempts apart from the successful-pair
projection used for request history.

State/lock transition table (who may write, when ``running`` flips, and
which transitions need a held lock):

- ``temporary``: memory only; never creates files, locks, or checkpoints.
  Leaving requires explicit discard when it holds work.
- ``editable``: the opener holds the per-session advisory lock and may
  checkpoint drafts and attempts. Entered by acquiring ``lock_session``.
- ``locked read-only``: another process holds the lock. This process may
  load and view (``running`` stays ``running``) but must not save, lock,
  or delete.
- ``dirty``: in-memory edits newer than the last acknowledged save.
- ``saving``: serialized atomic replacement in progress (one writer per
  session, guarded by the held lock).
- ``saved``: the directory sync acknowledged durability of the latest edit.
- ``failed-save``: the previous file is preserved; destructive navigation
  stays blocked until Retry save succeeds or the work is explicitly
  discarded. Retry save performs no HTTP request.
- ``interrupted``: a ``running`` attempt whose writer is provably gone.
  ``load_session`` maps ``running`` to ``interrupted`` in memory only after
  acquiring the lock (directly via ``lock_handle`` or by probing that no
  writer remains). Listing never rewrites files.
- ``deleting``: the owner holds the lock and unlinks the session file
  through ``delete_session(..., lock_handle=...)``. Lock files are never
  unlinked while another process may hold them.

Contract: no session reads at app startup; on explicit picker open,
load/validate all candidates off-thread when the picker opens.

Writes rewrite the whole file (``ponytail:`` O(session-size) full-file
rewrite; an append-only format is the upgrade path if measured saves affect
responsiveness). Transcripts are never truncated to fit storage or context.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from hashlib import sha256
from operator import attrgetter
from pathlib import Path
from typing import Any, cast

from mlx_tui.attachments import AttachmentSnapshot

try:
    import fcntl
except ImportError:  # pragma: no cover - session locking is Unix-only
    fcntl = None  # type: ignore[assignment]

JSONValue = str | int | float | bool | None | dict[str, "JSONValue"] | list["JSONValue"]

SCHEMA_VERSION = 2
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


class SessionValidationError(ValueError):
    """A session record, ID, reference, or lock use is invalid."""


class SessionPersistenceError(SessionValidationError):
    """A session checkpoint could not be safely written, read, or removed."""


class SessionLockedError(SessionPersistenceError):
    """Another process holds the session's advisory edit lock."""


class SessionStaleWriteError(SessionPersistenceError):
    """The on-disk revision is newer than the session being saved; kept intact."""


class SessionDurabilityUnconfirmed(SessionPersistenceError):
    """New bytes are on disk but the directory sync failed; durability unconfirmed."""

    def __init__(self, message: str, path: Path) -> None:
        super().__init__(message)
        self.path = path


@dataclass(frozen=True)
class RequestSettings:
    """Next-request settings snapshot; never commands or runtime ownership."""

    model: str | None
    repo_id: str | None
    revision: str | None
    system: str
    temperature: float
    top_p: float
    max_tokens: int
    max_ctx: int
    seed: int | None = None
    enable_thinking: bool | None = None
    profile_id: str | None = None
    profile_fingerprint: str | None = None
    profile_modified: bool = False
    profile_runtime_commit: str | None = None
    profile_runtime_provenance: str | None = None
    profile_template_sha256: str | None = None
    profile_template_provenance: str | None = None
    profile_launch_settings: dict[str, JSONValue] | None = None
    profile_launch_provenance: str | None = None


@dataclass(frozen=True)
class SessionTurn:
    """One typed attempt; presentation is derived from these raw facts on reopen."""

    turn_id: str
    created_at: str
    original_draft: str
    sent_content: str
    settings: RequestSettings
    attachments: tuple[AttachmentSnapshot, ...] = ()
    included_turn_ids: tuple[str, ...] = ()
    estimated_input: int | None = None
    reserved_output: int | None = None
    excluded_messages: int | None = None
    answer: str = ""
    reasoning: str = ""
    tool_calls: tuple[dict[str, JSONValue], ...] = ()
    response_model: str | None = None
    finish_reason: str | None = None
    skipped_frames: int = 0
    stream_complete: bool = False
    outcome: str = "running"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    first_output_s: float | None = None
    answer_started_s: float | None = None
    total_s: float | None = None
    error_category: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class ChatSession:
    """One durable conversation: next-request settings, draft, ordered attempts."""

    session_id: str
    created_at: str
    updated_at: str
    settings: RequestSettings
    draft: str = ""
    attachments: tuple[AttachmentSnapshot, ...] = ()
    attempts: tuple[SessionTurn, ...] = ()
    schema_version: int = SCHEMA_VERSION


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def new_session(settings: RequestSettings, draft: str = "") -> ChatSession:
    """Start an unsaved session; the caller owns first-save timestamps."""
    now = _now_iso()
    return ChatSession(
        session_id=str(uuid.uuid4()),
        created_at=now,
        updated_at=now,
        settings=settings,
        draft=draft,
    )


def _required(table: dict[str, Any], key: str, label: str) -> Any:
    if key not in table:
        raise SessionValidationError(f"{label} is missing {key!r}")
    return table[key]


def _check_keys(table: dict[str, Any], allowed: set[str], label: str) -> None:
    for key in table:
        if key not in allowed:
            raise SessionValidationError(f"{label} has an unexpected field {key!r}")


def _string_value(table: dict[str, Any], key: str, label: str) -> str:
    value = _required(table, key, label)
    if type(value) is not str:
        raise SessionValidationError(f"{label}.{key} must be a string")
    return value


def _optional_string(table: dict[str, Any], key: str, label: str) -> str | None:
    value = _required(table, key, label)
    if value is not None and type(value) is not str:
        raise SessionValidationError(f"{label}.{key} must be a string or null")
    return value


def _bool_value(table: dict[str, Any], key: str, label: str) -> bool:
    value = _required(table, key, label)
    if type(value) is not bool:
        raise SessionValidationError(f"{label}.{key} must be a boolean")
    return value


def _optional_bool(table: dict[str, Any], key: str, label: str) -> bool | None:
    value = _required(table, key, label)
    if value is not None and type(value) is not bool:
        raise SessionValidationError(f"{label}.{key} must be a boolean or null")
    return value


def _count_value(
    table: dict[str, Any], key: str, label: str, minimum: int = 0
) -> int | None:
    value = _required(table, key, label)
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
    value = _required(table, key, label)
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
    value = _string_value(table, key, label)
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise SessionValidationError(f"{label}.{key} is not an ISO timestamp") from exc
    return value


def _json_compatible(value: object, label: str) -> JSONValue:
    if value is None or type(value) in {str, bool, int}:
        return value  # type: ignore[return-value]
    if type(value) is float:
        if not math.isfinite(value):
            raise SessionValidationError(f"{label} must hold finite JSON values")
        return value
    if type(value) is dict:
        result: dict[str, JSONValue] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise SessionValidationError(f"{label} must hold finite JSON values")
            result[key] = _json_compatible(item, label)
        return result
    if type(value) in {list, tuple}:
        return [_json_compatible(item, label) for item in value]  # type: ignore[union-attr]
    raise SessionValidationError(f"{label} must hold finite JSON values")


def _provenance_value(table: dict[str, Any], key: str, label: str) -> str | None:
    value = _optional_string(table, key, label)
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
    _check_keys(table, _ATTACHMENT_KEYS, "attachment")
    selected = _string_value(table, "selected_path", "attachment")
    resolved = _string_value(table, "resolved_path", "attachment")
    byte_length = _required(table, "byte_length", "attachment")
    if type(byte_length) is not int or isinstance(byte_length, bool):
        raise SessionValidationError("attachment.byte_length must be an integer")
    snapshot = AttachmentSnapshot(
        selected_path=Path(selected),
        resolved_path=Path(resolved),
        byte_length=byte_length,
        sha256=_string_value(table, "sha256", "attachment"),
        content=_string_value(table, "content", "attachment"),
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
    _check_keys(mapping, _REQUEST_KEYS, "settings")
    temperature = _required(mapping, "temperature", "settings")
    if (
        type(temperature) not in {int, float}
        or isinstance(temperature, bool)
        or not math.isfinite(temperature)
        or temperature < 0
    ):
        raise SessionValidationError("settings.temperature must be finite and >= 0")
    top_p = _required(mapping, "top_p", "settings")
    if (
        type(top_p) not in {int, float}
        or isinstance(top_p, bool)
        or not math.isfinite(top_p)
        or not 0 < top_p <= 1
    ):
        raise SessionValidationError("settings.top_p must be finite within (0, 1]")
    launch = _required(mapping, "profile_launch_settings", "settings")
    launch_settings: dict[str, JSONValue] | None = None
    if launch is not None:
        if type(launch) is not dict:
            raise SessionValidationError("settings.profile_launch_settings invalid")
        launch_settings = {
            key: _json_compatible(item, "settings.profile_launch_settings")
            for key, item in launch.items()
        }
    runtime_commit = _optional_string(mapping, "profile_runtime_commit", "settings")
    runtime_provenance = _provenance_value(
        mapping, "profile_runtime_provenance", "settings"
    )
    template_sha = _optional_string(mapping, "profile_template_sha256", "settings")
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
        model=_optional_string(mapping, "model", "settings"),
        repo_id=_optional_string(mapping, "repo_id", "settings"),
        revision=_optional_string(mapping, "revision", "settings"),
        system=_string_value(mapping, "system", "settings"),
        temperature=float(temperature),
        top_p=float(top_p),
        max_tokens=_required_count(mapping, "max_tokens", "settings", 1),
        max_ctx=_required_count(mapping, "max_ctx", "settings", 1),
        seed=_count_value(mapping, "seed", "settings"),
        enable_thinking=_optional_bool(mapping, "enable_thinking", "settings"),
        profile_id=_optional_string(mapping, "profile_id", "settings"),
        profile_fingerprint=_optional_string(
            mapping, "profile_fingerprint", "settings"
        ),
        profile_modified=_bool_value(mapping, "profile_modified", "settings"),
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
        checked = _json_compatible(item, "attempt.tool_calls")
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
    _check_keys(
        mapping,
        _TURN_KEYS_V2 if version == SCHEMA_VERSION else _TURN_KEYS,
        "attempt",
    )
    raw_included = _required(mapping, "included_turn_ids", "attempt")
    if type(raw_included) is not list:
        raise SessionValidationError("attempt.included_turn_ids must be a list")
    included = tuple(
        _session_uuid(item, "attempt.included_turn_ids entry") for item in raw_included
    )
    tool_calls = _tool_calls_from_json(_required(mapping, "tool_calls", "attempt"))
    outcome = _string_value(mapping, "outcome", "attempt")
    answer = _string_value(mapping, "answer", "attempt")
    sent_content = _string_value(mapping, "sent_content", "attempt")
    error_category = _optional_string(mapping, "error_category", "attempt")
    _check_turn_consistency(outcome, answer, tool_calls, sent_content, error_category)
    attachments = (
        _attachments_from_json(
            _required(mapping, "attachments", "attempt"), "attempt.attachments"
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
            _required(mapping, "turn_id", "attempt"), "attempt.turn_id"
        ),
        created_at=_timestamp_value(mapping, "created_at", "attempt"),
        original_draft=_string_value(mapping, "original_draft", "attempt"),
        sent_content=sent_content,
        settings=_settings_from_json(_required(mapping, "settings", "attempt")),
        attachments=attachments,
        included_turn_ids=included,
        estimated_input=_count_value(mapping, "estimated_input", "attempt"),
        reserved_output=_count_value(mapping, "reserved_output", "attempt"),
        excluded_messages=_count_value(mapping, "excluded_messages", "attempt"),
        answer=answer,
        reasoning=_string_value(mapping, "reasoning", "attempt"),
        tool_calls=tuple(tool_calls),
        response_model=_optional_string(mapping, "response_model", "attempt"),
        finish_reason=_optional_string(mapping, "finish_reason", "attempt"),
        skipped_frames=_required_count(mapping, "skipped_frames", "attempt", 0),
        stream_complete=_bool_value(mapping, "stream_complete", "attempt"),
        outcome=outcome,
        prompt_tokens=_count_value(mapping, "prompt_tokens", "attempt"),
        completion_tokens=_count_value(mapping, "completion_tokens", "attempt"),
        total_tokens=_count_value(mapping, "total_tokens", "attempt"),
        cached_prompt_tokens=_count_value(mapping, "cached_prompt_tokens", "attempt"),
        first_output_s=_finite_value(mapping, "first_output_s", "attempt"),
        answer_started_s=_finite_value(mapping, "answer_started_s", "attempt"),
        total_s=_finite_value(mapping, "total_s", "attempt"),
        error_category=error_category,
        error_detail=_optional_string(mapping, "error_detail", "attempt"),
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
    version = _required(table, "schema_version", "session")
    if type(version) is not int or isinstance(version, bool):
        raise SessionValidationError("session.schema_version must be an integer")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SessionPersistenceError(f"unsupported session schema: {version}")
    _check_keys(
        table,
        _SESSION_KEYS_V2 if version == SCHEMA_VERSION else _SESSION_KEYS,
        "session",
    )
    session_id = _session_uuid(_required(table, "session_id", "session"), "session_id")
    if expected_id is not None and session_id != expected_id:
        raise SessionValidationError("session filename does not match session_id")
    raw_attempts = _required(table, "attempts", "session")
    if type(raw_attempts) is not list:
        raise SessionValidationError("session.attempts must be a list")
    attempts = tuple(_turn_from_json(item, version) for item in raw_attempts)
    _check_attempt_references(attempts)
    draft = _string_value(table, "draft", "session")
    attachments = (
        _attachments_from_json(
            _required(table, "attachments", "session"), "session.attachments"
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
        settings=_settings_from_json(_required(table, "settings", "session")),
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


def session_dir() -> Path:
    """Return the sessions directory without creating or reading anything."""
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "mlx-tui" / "sessions"


def _refuse_symlinked_root(directory: Path) -> None:
    data_root = directory.parents[1]
    for path in (data_root, data_root / "mlx-tui", directory):
        if path.exists() and path.is_symlink():
            raise SessionPersistenceError(f"refusing symlinked sessions path: {path}")


def _checked_dir(create: bool) -> Path:
    directory = session_dir()
    _refuse_symlinked_root(directory)
    if create:
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise SessionPersistenceError(
                f"could not create sessions directory: {directory}"
            ) from exc
        if directory.is_symlink() or not directory.is_dir():
            raise SessionPersistenceError(
                f"refusing unexpected sessions path: {directory}"
            )
    return directory


def _session_path(directory: Path, session_id: str) -> Path:
    canonical = _session_uuid(session_id, "session_id")
    return directory / f"{canonical}.json"


def _lock_path(directory: Path, session_id: str) -> Path:
    canonical = _session_uuid(session_id, "session_id")
    target = directory / f"{canonical}.lock"
    if target.exists() and target.is_symlink():
        raise SessionPersistenceError(f"refusing symlinked session lock: {target}")
    return target


def _parse_updated_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _regular_session_file(target: Path) -> Path:
    if target.is_symlink() or not target.is_file():
        raise SessionValidationError(f"refusing non-regular session file: {target}")
    return target


@dataclass
class SessionLock:
    """A held exclusive advisory lock for one session; released on close."""

    session_id: str
    path: Path
    fd: int = field(repr=False, compare=False)
    held: bool = field(default=True, compare=False)

    def release(self) -> None:
        """Release the advisory lock exactly once; closing is idempotent."""
        if not self.held:
            return
        if fcntl is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.held = False


@contextmanager
def lock_session(session_id: str) -> Generator[SessionLock]:
    """Hold a nonblocking exclusive lock on a stable content-free sibling file.

    A locked session can be viewed read-only elsewhere; only the holder may
    save or delete. Lock files are never unlinked. The same process must not
    open a second descriptor for a session it already holds (a second
    ``flock`` through a separately opened descriptor blocks even in the same
    process on macOS); pass the yielded handle to ``delete_session`` instead.
    """
    if fcntl is None:
        raise SessionPersistenceError("session locking is unsupported on this OS")
    directory = _checked_dir(create=True)
    target = _lock_path(directory, session_id)
    try:
        fd = os.open(target, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise SessionPersistenceError(f"cannot open session lock: {target}") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            raise SessionLockedError(
                f"session is locked by another process: {session_id}"
            ) from exc
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise SessionPersistenceError(f"cannot lock session: {session_id}") from exc
    handle = SessionLock(
        session_id=_session_uuid(session_id, "session_id"), path=target, fd=fd
    )
    try:
        yield handle
    finally:
        handle.release()


def _locked_by_other(lock_target: Path) -> bool:
    if fcntl is None:
        raise SessionPersistenceError("session locking is unsupported on this OS")
    try:
        fd = os.open(lock_target, os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SessionPersistenceError(
            f"could not probe session lock: {lock_target}"
        ) from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        os.close(fd)


def _atomic_write(target: Path, encoded: bytes) -> None:
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
        raise SessionPersistenceError(f"could not save session {target}") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _sync_directory(directory: Path, target: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        raise SessionDurabilityUnconfirmed(
            f"session bytes remain but durability is unconfirmed: {target}", target
        ) from exc
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            raise SessionDurabilityUnconfirmed(
                f"session bytes remain but durability is unconfirmed: {target}", target
            ) from exc
    finally:
        os.close(fd)


def save_session(session: ChatSession) -> Path:
    """Validate, then atomically replace the session snapshot.

    A stale in-memory revision (older ``updated_at`` than the file on disk)
    is rejected without touching the file. Post-replacement directory-sync
    failures raise ``SessionDurabilityUnconfirmed``: the new bytes remain on
    disk but durability is unconfirmed.
    """
    encoded = encode_session(session).encode("utf-8")
    directory = _checked_dir(create=True)
    target = _session_path(directory, session.session_id)
    if target.exists():
        _regular_session_file(target)
        try:
            existing = decode_session(
                target.read_text(encoding="utf-8"),
                expected_id=session.session_id,
            )
        except SessionValidationError:
            existing = None
        except (OSError, UnicodeError) as exc:
            raise SessionPersistenceError(
                f"could not read existing session {target}"
            ) from exc
        if existing is not None and _parse_updated_at(
            existing.updated_at
        ) > _parse_updated_at(session.updated_at):
            raise SessionStaleWriteError(
                "session file holds a newer revision; refusing stale save"
            )
    _atomic_write(target, encoded)
    _sync_directory(directory, target)
    return target


def load_session(path: Path, lock_handle: SessionLock | None = None) -> ChatSession:
    """Load and strictly validate one session without rewriting it.

    A ``running`` attempt is preserved while another process owns the edit
    lock; it maps to ``interrupted`` in memory only after the caller proves
    no writer remains, either by passing its held ``lock_handle`` or by a
    successful lock probe. Corrupt and newer-schema files stay intact and
    raise independently.
    """
    target = Path(path)
    if target.is_symlink():
        raise SessionValidationError(f"refusing non-regular session file: {target}")
    if not target.exists():
        raise SessionPersistenceError(f"unknown session file: {target}")
    _regular_session_file(target)
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SessionPersistenceError(f"could not read session {target}") from exc
    session = decode_session(content, expected_id=target.stem)
    if not any(turn.outcome == "running" for turn in session.attempts):
        return session
    if lock_handle is not None:
        if not lock_handle.held or lock_handle.session_id != session.session_id:
            raise SessionValidationError("session lock is not held for this session")
        locked_by_other = False
    else:
        locked_by_other = _locked_by_other(
            _lock_path(target.parent, session.session_id)
        )
    if locked_by_other:
        return session
    return ChatSession(
        session_id=session.session_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        settings=session.settings,
        draft=session.draft,
        attempts=tuple(
            replace(
                turn,
                outcome="interrupted" if turn.outcome == "running" else turn.outcome,
            )
            for turn in session.attempts
        ),
    )


def list_sessions() -> list[Path]:
    """Return candidate session files by name; contents stay unread until opened."""
    directory = session_dir()
    if not directory.exists():
        return []
    _refuse_symlinked_root(directory)
    if not directory.is_dir():
        raise SessionPersistenceError(f"refusing unexpected sessions path: {directory}")
    return sorted(directory.glob("*.json"), key=attrgetter("name"))


def delete_session(session_id: str, lock_handle: SessionLock | None = None) -> None:
    """Unlink one session file, consuming the owner's held lock when given.

    Without a handle, deletion is refused while another process holds the
    lock. Lock files are never unlinked.
    """
    directory = _checked_dir(create=False)
    target = _session_path(directory, session_id)
    if lock_handle is not None:
        if not lock_handle.held or lock_handle.session_id != target.stem:
            raise SessionValidationError("session lock is not held for this session")
    elif target.with_suffix(".lock").exists() and _locked_by_other(
        _lock_path(directory, session_id)
    ):
        raise SessionLockedError(f"session is locked by another process: {session_id}")
    if target.is_symlink():
        raise SessionValidationError(f"refusing non-regular session file: {target}")
    if not target.exists():
        raise SessionPersistenceError(f"unknown session: {session_id}")
    _regular_session_file(target)
    try:
        target.unlink()
    except FileNotFoundError as exc:
        raise SessionPersistenceError(f"unknown session: {session_id}") from exc
    except OSError as exc:
        raise SessionPersistenceError(f"could not delete session {target}") from exc


def successful_turns(session: ChatSession) -> tuple[SessionTurn, ...]:
    """Return successful attempts in order; partial/error attempts never qualify."""
    return tuple(turn for turn in session.attempts if turn.outcome == "success")


def request_messages(session: ChatSession) -> list[dict[str, str]]:
    """Derive request-window messages only from successful user/assistant pairs."""
    messages: list[dict[str, str]] = []
    for turn in successful_turns(session):
        messages.append({"role": "user", "content": turn.sent_content})
        messages.append({"role": "assistant", "content": turn.answer})
    return messages


def session_label(session: ChatSession, excerpt_chars: int = 40) -> str:
    """Derive a picker label from the first prompt excerpt and the update date."""
    excerpt = ""
    for turn in session.attempts:
        if turn.sent_content:
            excerpt = turn.sent_content
            break
    if not excerpt:
        excerpt = session.draft
    first_line = excerpt.strip().splitlines()[0] if excerpt.strip() else "Empty session"
    if len(first_line) > excerpt_chars:
        first_line = first_line[:excerpt_chars] + "…"
    return f"{first_line} · {session.updated_at[:10]}"
