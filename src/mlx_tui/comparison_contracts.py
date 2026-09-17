"""Immutable comparison contracts: types, constants, and input validation."""

from __future__ import annotations

import ipaddress
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from mlx_tui.process import ProcessIdentity
from mlx_tui.profiles import ProfileEntry

JSONValue = str | int | float | bool | None | dict[str, "JSONValue"] | list["JSONValue"]
ComparisonStatus = Literal["running", "completed", "failed", "cancelled", "interrupted"]
TrialState = Literal["not_attempted", "attempted", "completed"]
DecisionKind = Literal["keep", "retain", "reject"]

CODING_CHECK_PROMPT = (
    "Synthetic coding check v1. Return exactly this one-line Python function and "
    "nothing else: def answer(): return 42"
)
CODING_CHECK_EXPECTED = "def answer(): return 42"
TRIAL_REPEATS = 5
TRIAL_SLOTS = 2 * (TRIAL_REPEATS + 1)
RSS_SCOPE = "server process RSS while requested"
_IPV4 = 4
_IPV6 = 6
_MAX_PORT = 65535
_PROFILE_COUNT = 2
_LATENCY_THRESHOLD = 0.05


class ComparisonValidationError(ValueError):
    """The comparison input or saved result is invalid."""


class ComparisonPersistenceError(ComparisonValidationError):
    """A comparison checkpoint could not be safely written or read."""


class ComparisonIdentityError(ComparisonValidationError):
    """The endpoint response or process identity was not verified."""


@dataclass(frozen=True)
class LoopbackEndpoint:
    """A supported chat-completions URL with an exact loopback identity."""

    url: str
    host: str
    port: int


def parse_loopback_url(url: str) -> LoopbackEndpoint:
    """Accept only an explicit-IP loopback chat-completions endpoint."""
    if not isinstance(url, str) or not url:
        raise ComparisonValidationError("endpoint must be a URL")
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.username is not None:
        raise ComparisonValidationError("endpoint must use unauthenticated http")
    if parsed.password is not None or parsed.query or parsed.fragment:
        raise ComparisonValidationError(
            "endpoint cannot contain credentials, query, or fragment"
        )
    if parsed.path != "/v1/chat/completions" or parsed.netloc == "":
        raise ComparisonValidationError("endpoint path must be /v1/chat/completions")
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as exc:
        raise ComparisonValidationError(
            "endpoint must contain a valid explicit port"
        ) from exc
    if port is None or not 1 <= port <= _MAX_PORT:
        raise ComparisonValidationError("endpoint must contain a valid explicit port")
    if not (
        address.version == _IPV4 and address in ipaddress.ip_network("127.0.0.0/8")
    ) and address != ipaddress.IPv6Address("::1"):
        raise ComparisonValidationError("endpoint host must be a loopback IP literal")
    host = str(address)
    rendered_host = f"[{host}]" if address.version == _IPV6 else host
    return LoopbackEndpoint(
        url=f"http://{rendered_host}:{port}/v1/chat/completions",
        host=host,
        port=port,
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class SavedChoice:
    """A local decision; committed only when its finalized run agrees."""

    run_id: str
    result_path: Path
    decision: DecisionKind
    profile_id: str | None
    profile_fingerprint: str | None
    reason: str
    created_at: str = field(default_factory=_now_iso)


def _finite_non_negative(value: float | None, label: str) -> None:
    if value is not None and (not math.isfinite(value) or value < 0):
        raise ComparisonValidationError(f"{label} must be finite and non-negative")


@dataclass(frozen=True)
class MemorySample:
    timestamp: float
    rss_gib: float | None
    available_gib: float | None
    scope: str = RSS_SCOPE

    def __post_init__(self) -> None:
        _finite_non_negative(self.timestamp, "memory sample timestamp")
        _finite_non_negative(self.rss_gib, "memory sample RSS")
        _finite_non_negative(self.available_gib, "memory sample available memory")


@dataclass(frozen=True)
class ComparisonInput:
    endpoint: str
    profiles: tuple[ProfileEntry, ...]
    snapshot_paths: tuple[Path, ...]
    verified_asset_hashes: tuple[dict[str, str], ...] = ()
    runtime_evidence: dict[str, JSONValue] = field(default_factory=dict)
    install_evidence: dict[str, JSONValue] = field(default_factory=dict)
    launch_evidence: dict[str, JSONValue] = field(default_factory=dict)
    provenance: dict[str, JSONValue] = field(default_factory=dict)
    process_identity: ProcessIdentity | None = None
    isolation_evidence: dict[str, JSONValue] = field(default_factory=dict)
    machine_tier: str = ""
    operator_conditions: dict[str, JSONValue] = field(default_factory=dict)
    profile_order: tuple[str, str] | None = None
    task_id: str = "milestone-b-coding-check"
    check_id: str = "coding-check-v1"
    prompt: str = CODING_CHECK_PROMPT
    result_path: Path | None = None

    def __post_init__(self) -> None:
        entries: tuple[ProfileEntry, ...] = tuple(
            value if isinstance(value, ProfileEntry) else ProfileEntry(value)
            for value in self.profiles
        )
        object.__setattr__(self, "profiles", entries)
        object.__setattr__(
            self, "snapshot_paths", tuple(Path(p) for p in self.snapshot_paths)
        )
        object.__setattr__(
            self,
            "verified_asset_hashes",
            tuple(dict(values) for values in self.verified_asset_hashes),
        )


@dataclass(frozen=True)
class TrialResult:
    profile_id: str
    repeat_index: int
    state: TrialState = "not_attempted"
    error: str | None = None
    cancelled: bool = False
    payload: dict[str, JSONValue] = field(default_factory=dict)
    answer: str = ""
    reasoning: str = ""
    tool_calls: tuple[dict[str, JSONValue], ...] = ()
    response_model: str | None = None
    finish_reason: str | None = None
    stream_complete: bool = False
    quality_pass: bool | None = None
    quality_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    prompt_estimated: bool | None = None
    completion_estimated: bool | None = None
    cached_prompt_tokens: int | None = None
    first_output_s: float | None = None
    answer_started_s: float | None = None
    total_s: float | None = None
    rss_samples: tuple[MemorySample, ...] = ()
    sample_scope: str = RSS_SCOPE
    process_identity_before: ProcessIdentity | None = None
    process_identity_after: ProcessIdentity | None = None

    def __post_init__(self) -> None:
        if self.repeat_index < 0 or self.repeat_index > TRIAL_REPEATS:
            raise ComparisonValidationError("trial repeat index is out of range")
        for name in ("first_output_s", "answer_started_s", "total_s"):
            _finite_non_negative(getattr(self, name), f"trial {name}")
        if self.state == "not_attempted" and any(
            value is not None
            for value in (self.first_output_s, self.answer_started_s, self.total_s)
        ):
            raise ComparisonValidationError("not-attempted trials cannot have timings")


@dataclass(frozen=True)
class ComparisonResult:
    run_id: str
    status: ComparisonStatus
    comparison: ComparisonInput
    trials: tuple[TrialResult, ...]
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    summary: dict[str, JSONValue] = field(default_factory=dict)
    error: str | None = None
