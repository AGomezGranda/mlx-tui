"""Pinned coding profiles and their reviewed recommendation evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any, Literal

type JSONValue = (
    str | int | float | bool | None | dict[str, "JSONValue"] | list["JSONValue"]
)

type EvidenceStatus = Literal[
    "candidate", "unqualified", "qualified", "revoked", "withdrawn"
]

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_MAX_TEMPERATURE = 2.0
_MAX_TOP_P = 1.0
_MAX_TOKENS = 16384
_ALLOWED_PROFILE_KEYS = {
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
    "evidence",
}
_ALLOWED_EVIDENCE_KEYS = {
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
}


class ProfileValidationError(ValueError):
    """The packaged catalogue is not safe to use as a pinned profile."""


@dataclass(frozen=True)
class CodingProfile:
    id: str
    name: str
    repo_id: str
    revision: str
    quantization: str
    runtime_commit: str
    mlx_version: str
    template_sha256: str
    template_assets: dict[str, str]
    launch_settings: dict[str, JSONValue]
    system: str
    temperature: float
    top_p: float
    max_tokens: int
    max_ctx: int
    seed: int
    enable_thinking: bool

    @property
    def fingerprint(self) -> str:
        return profile_fingerprint(self)


@dataclass(frozen=True)
class RecommendationEvidence:
    owner: str
    source: str
    status: EvidenceStatus
    revocation_reason: str | None
    tested_at: datetime
    expires_at: datetime
    machine_tier: str
    task_id: str
    check_id: str
    prompt_sha256: str
    runtime_freeze: str
    profile_fingerprint: str
    result_references: tuple[str, ...]
    result_hashes: tuple[str, ...]

    def is_current(
        self,
        profile: CodingProfile,
        *,
        machine_tier: str,
        now: datetime | None = None,
    ) -> bool:
        current = _as_utc(now or datetime.now(UTC))
        return (
            self.status == "qualified"
            and self.machine_tier == machine_tier
            and self.profile_fingerprint == profile.fingerprint
            and self.tested_at <= current < self.expires_at
        )


@dataclass(frozen=True)
class ProfileEntry:
    profile: CodingProfile
    evidence: tuple[RecommendationEvidence, ...] = ()

    def status(
        self,
        machine_tier: str,
        *,
        now: datetime | None = None,
    ) -> str:
        """Return a fail-closed display state without discarding dated evidence."""
        current = _as_utc(now or datetime.now(UTC))
        matching = [e for e in self.evidence if e.machine_tier == machine_tier]
        if not matching:
            return "unknown"
        if any(e.status in {"revoked", "withdrawn"} for e in matching):
            return "revoked"
        if any(e.status == "qualified" and e.expires_at <= current for e in matching):
            return "expired"
        if any(
            e.is_current(self.profile, machine_tier=machine_tier, now=current)
            for e in matching
        ):
            return "qualified"
        if any(e.status == "unqualified" for e in matching):
            return "unqualified"
        return "unknown"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ProfileValidationError("timestamps must include a timezone")
    return value.astimezone(UTC)


def _canonical_value(value: JSONValue, *, key: str | None = None) -> JSONValue:
    """Normalize JSON-compatible settings, omitting machine-local path fields."""
    if isinstance(value, dict):
        return {
            k: _canonical_value(v, key=k)
            for k, v in sorted(value.items())
            if k.lower()
            not in {
                "path",
                "model_path",
                "snapshot_path",
                "cache_path",
                "cwd",
                "working_directory",
            }
        }
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ProfileValidationError("launch settings must contain finite numbers")
    if key is not None and key.lower().endswith("_path"):
        return "<machine-local-path>"
    return value


def profile_fingerprint(profile: CodingProfile) -> str:
    """Hash effective profile settings, excluding display and machine-local paths."""
    payload: dict[str, JSONValue] = {
        "id": profile.id,
        "repo_id": profile.repo_id,
        "revision": profile.revision,
        "quantization": profile.quantization,
        "runtime_commit": profile.runtime_commit,
        "mlx_version": profile.mlx_version,
        "template_sha256": profile.template_sha256,
        "template_assets": {
            key: value for key, value in sorted(profile.template_assets.items())
        },
        "launch_settings": profile.launch_settings,
        "system": profile.system,
        "temperature": profile.temperature,
        "top_p": profile.top_p,
        "max_tokens": profile.max_tokens,
        "max_ctx": profile.max_ctx,
        "seed": profile.seed,
        "enable_thinking": profile.enable_thinking,
    }
    encoded = json.dumps(
        _canonical_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fail(message: str) -> ProfileValidationError:
    return ProfileValidationError(message)


def _table(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(f"{label} must be a TOML table")
    return value


def _check_keys(table: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise _fail(f"{label} has unknown keys: {', '.join(sorted(unknown))}")


def _string(table: dict[str, Any], key: str, label: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{label}.{key} must be a non-empty string")
    return value


def _number(table: dict[str, Any], key: str, label: str) -> int | float:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(f"{label}.{key} must be a number, not bool")
    if isinstance(value, float) and not math.isfinite(value):
        raise _fail(f"{label}.{key} must be finite")
    return value


def _integer(table: dict[str, Any], key: str, label: str) -> int:
    value = _number(table, key, label)
    if type(value) is not int:
        raise _fail(f"{label}.{key} must be an integer")
    return value


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise _fail(f"{label} must be a lowercase SHA-256 hash")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise _fail(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _as_utc(parsed)
    except ValueError as exc:
        raise _fail(f"{label} must be an ISO-8601 timestamp with timezone") from exc


def _parse_profile(raw: object, index: int) -> CodingProfile:
    table = _table(raw, f"profiles[{index}]")
    _check_keys(table, _ALLOWED_PROFILE_KEYS, f"profiles[{index}]")
    revision = _string(table, "revision", f"profiles[{index}]")
    if _REVISION_RE.fullmatch(revision) is None:
        raise _fail(f"profiles[{index}].revision must be a full immutable commit")
    template_sha256 = _hash(
        table.get("template_sha256"), f"profiles[{index}].template_sha256"
    )
    runtime_commit = _string(table, "runtime_commit", f"profiles[{index}]")
    if _REVISION_RE.fullmatch(runtime_commit) is None:
        raise _fail(f"profiles[{index}].runtime_commit must be an immutable commit")
    assets_raw = _table(
        table.get("template_assets"), f"profiles[{index}].template_assets"
    )
    assets = {
        key: _hash(value, f"template_assets.{key}") for key, value in assets_raw.items()
    }
    if not assets:
        raise _fail(f"profiles[{index}].template_assets cannot be empty")
    settings = _table(
        table.get("launch_settings"), f"profiles[{index}].launch_settings"
    )
    try:
        json.dumps(_canonical_value(settings))
    except (TypeError, ValueError) as exc:
        raise _fail(
            f"profiles[{index}].launch_settings must be JSON-compatible"
        ) from exc
    temperature = _number(table, "temperature", f"profiles[{index}]")
    top_p = _number(table, "top_p", f"profiles[{index}]")
    max_tokens = _integer(table, "max_tokens", f"profiles[{index}]")
    max_ctx = _integer(table, "max_ctx", f"profiles[{index}]")
    seed = _integer(table, "seed", f"profiles[{index}]")
    if not 0 <= temperature <= _MAX_TEMPERATURE or not 0 <= top_p <= _MAX_TOP_P:
        raise _fail(f"profiles[{index}] sampler values are out of range")
    if not 1 <= max_tokens <= _MAX_TOKENS or max_ctx < 1:
        raise _fail(f"profiles[{index}] token budgets are out of range")
    thinking = table.get("enable_thinking")
    if type(thinking) is not bool:
        raise _fail(f"profiles[{index}].enable_thinking must be a bool")
    system = table.get("system")
    if not isinstance(system, str):
        raise _fail(f"profiles[{index}].system must be a string")
    return CodingProfile(
        id=_string(table, "id", f"profiles[{index}]"),
        name=_string(table, "name", f"profiles[{index}]"),
        repo_id=_string(table, "repo_id", f"profiles[{index}]"),
        revision=revision,
        quantization=_string(table, "quantization", f"profiles[{index}]"),
        runtime_commit=runtime_commit,
        mlx_version=_string(table, "mlx_version", f"profiles[{index}]"),
        template_sha256=template_sha256,
        template_assets=assets,
        launch_settings=settings,
        system=system,
        temperature=float(temperature),
        top_p=float(top_p),
        max_tokens=max_tokens,
        max_ctx=max_ctx,
        seed=seed,
        enable_thinking=thinking,
    )


def _parse_evidence(
    raw: object, profile: CodingProfile, index: int
) -> RecommendationEvidence:
    table = _table(raw, f"profiles[{index}].evidence")
    _check_keys(table, _ALLOWED_EVIDENCE_KEYS, f"profiles[{index}].evidence")
    status = table.get("status")
    if status not in {"candidate", "unqualified", "qualified", "revoked", "withdrawn"}:
        raise _fail(f"profiles[{index}].evidence.status is invalid")
    tested_at = _timestamp(table.get("tested_at"), "evidence.tested_at")
    expires_at = _timestamp(table.get("expires_at"), "evidence.expires_at")
    if expires_at <= tested_at or expires_at > tested_at + timedelta(days=30):
        raise _fail(
            "evidence expiry must be after testing and no more than 30 days later"
        )
    references = table.get("result_references", [])
    hashes = table.get("result_hashes", [])
    if not isinstance(references, list) or not all(
        isinstance(v, str) and v for v in references
    ):
        raise _fail("evidence.result_references must be a list of strings")
    if not isinstance(hashes, list) or not all(
        _HASH_RE.fullmatch(v or "") for v in hashes
    ):
        raise _fail("evidence.result_hashes must contain SHA-256 hashes")
    reason = table.get("revocation_reason", "")
    if not isinstance(reason, str):
        raise _fail("evidence.revocation_reason must be a string")
    if status in {"revoked", "withdrawn"} and not reason.strip():
        raise _fail("revoked evidence requires a reason")
    if status == "qualified" and (not references or not hashes):
        raise _fail("qualified evidence requires result references and hashes")
    fingerprint = _hash(
        table.get("profile_fingerprint"), "evidence.profile_fingerprint"
    )
    if fingerprint != profile.fingerprint:
        raise _fail("evidence.profile_fingerprint does not match the profile")
    return RecommendationEvidence(
        owner=_string(table, "owner", "evidence"),
        source=_string(table, "source", "evidence"),
        status=status,
        revocation_reason=reason or None,
        tested_at=tested_at,
        expires_at=expires_at,
        machine_tier=_string(table, "machine_tier", "evidence"),
        task_id=_string(table, "task_id", "evidence"),
        check_id=_string(table, "check_id", "evidence"),
        prompt_sha256=_hash(table.get("prompt_sha256"), "evidence.prompt_sha256"),
        runtime_freeze=_string(table, "runtime_freeze", "evidence"),
        profile_fingerprint=fingerprint,
        result_references=tuple(references),
        result_hashes=tuple(hashes),
    )


def _read_catalogue(path: Path | None) -> str:
    if path is not None:
        return path.read_text(encoding="utf-8")
    return (
        resources.files("mlx_tui")
        .joinpath("coding_profiles.toml")
        .read_text(encoding="utf-8")
    )


def load_coding_profiles(path: Path | None = None) -> list[ProfileEntry]:
    """Load and strictly validate the packaged pinned-profile catalogue."""
    try:
        document = tomllib.loads(_read_catalogue(path))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileValidationError(
            f"invalid coding profile catalogue: {exc}"
        ) from exc
    _check_keys(document, {"schema_version", "profiles"}, "catalogue")
    if document.get("schema_version") != 1:
        raise ProfileValidationError("unsupported coding profile schema")
    raw_profiles = document.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ProfileValidationError("catalogue.profiles must be a non-empty array")
    entries: list[ProfileEntry] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_profiles):
        table = _table(raw, f"profiles[{index}]")
        profile = _parse_profile(table, index)
        if profile.id in seen:
            raise ProfileValidationError(f"duplicate profile id: {profile.id}")
        seen.add(profile.id)
        raw_evidence = table.get("evidence", [])
        if not isinstance(raw_evidence, list):
            raise ProfileValidationError(f"profiles[{index}].evidence must be an array")
        evidence = tuple(
            _parse_evidence(value, profile, index) for value in raw_evidence
        )
        entries.append(ProfileEntry(profile=profile, evidence=evidence))
    return entries
