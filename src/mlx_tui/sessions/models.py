"""Session dataclasses: UI-free next-request settings and attempts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from mlx_tui.attachments import AttachmentSnapshot
from mlx_tui.json_util import JSONValue

SCHEMA_VERSION = 2


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
