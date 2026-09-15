"""Status-bar domain logic: liveness classification and rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NamedTuple

GenerationState = Literal["unknown", "succeeded", "failed", "client_cancelled"]


class MemorySnapshot(NamedTuple):
    avail_gib: float
    total_gib: float


@dataclass(frozen=True)
class ServerProbe:
    """One endpoint observation plus optional request-scoped model evidence."""

    state: str
    model_id: str | None
    available_models: tuple[str, ...] = ()
    catalogue_state: str = "unknown"


@dataclass(frozen=True)
class ServerIdentity:
    host: str
    port: int
    selected_model: str | None = None
    last_response_model: str | None = None
    last_success_at: float | None = None
    generation_state: GenerationState = "unknown"
    available_models: tuple[str, ...] = ()
    catalogue_state: str = "unknown"
    pid: int | None = None
    pid_create_time: float | None = None


_HTTP_OK = 200


def probe_from_response(status_code: int, body: object) -> ServerProbe:
    """Pure probe for a completed ``GET /v1/models`` response.

    green ⇔ 200 AND body parses to a dict whose ``data`` is a non-empty
    list; every other complete HTTP response is amber. Catalogue entries are
    availability only and never become response or residency evidence.
    """
    data = body.get("data") if isinstance(body, dict) else None
    if status_code == _HTTP_OK and isinstance(data, list) and len(data) > 0:
        model_ids = [
            mid
            for entry in data
            if isinstance(entry, dict)
            and isinstance((mid := entry.get("id")), str)
            and mid
        ]
        return ServerProbe(
            state="green",
            model_id=None,
            available_models=tuple(model_ids),
            catalogue_state="green",
        )
    return ServerProbe(state="amber", model_id=None, catalogue_state="amber")


def health_state_from_response(status_code: int, body: object) -> str:
    """Classify the pinned runtime's independent ``GET /health`` response."""
    if (
        status_code == _HTTP_OK
        and isinstance(body, dict)
        and body.get("status") == "ok"
    ):
        return "green"
    return "amber"
