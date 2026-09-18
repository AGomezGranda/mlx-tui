"""Strict pinned-profile catalogue contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mlx_tui.profiles import (
    ProfileEntry,
    ProfileValidationError,
    RecommendationEvidence,
    load_coding_profiles,
    profile_fingerprint,
)


def test_packaged_shortlist_has_two_pinned_candidates() -> None:
    entries = load_coding_profiles()
    assert [entry.profile.id for entry in entries] == [
        "qwen3-1.7b-baseline",
        "qwen3.5-4b-baseline",
    ]
    assert all(len(entry.profile.revision) == 40 for entry in entries)
    assert all(entry.evidence == () for entry in entries)
    now = datetime(2026, 9, 9, tzinfo=UTC)
    assert entries[0].status("local-m4-16gib", now=now) == "unknown"
    assert entries[1].status("local-m4-16gib", now=now) == "unknown"


def test_fingerprint_excludes_display_name_but_includes_launch_and_assets() -> None:
    profile = load_coding_profiles()[0].profile
    assert profile_fingerprint(replace(profile, name="renamed")) == profile.fingerprint
    assert (
        profile_fingerprint(
            replace(profile, launch_settings={**profile.launch_settings, "port": 18081})
        )
        != profile.fingerprint
    )
    assets = {**profile.template_assets, "extra.json": "a" * 64}
    assert (
        profile_fingerprint(replace(profile, template_assets=assets))
        != profile.fingerprint
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('revision = "3b1b1768f8f8cf8351c712464f906e86c2b8269e"', 'revision = "main"'),
        ("temperature = 0.0", "temperature = true"),
    ],
)
def test_catalogue_rejects_unsafe_values(tmp_path: Path, old: str, new: str) -> None:
    source = (
        __import__("importlib.resources", fromlist=["files"])
        .files("mlx_tui")
        .joinpath("coding_profiles.toml")
        .read_text()
    )
    path = tmp_path / "profiles.toml"
    path.write_text(source.replace(old, new, 1))
    with pytest.raises(ProfileValidationError):
        load_coding_profiles(path)


def _synthetic_evidence(
    entry: ProfileEntry,
    *,
    status: str = "qualified",
    tested_at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    expires_at: datetime = datetime(2026, 1, 11, tzinfo=UTC),
    revocation_reason: str | None = None,
) -> RecommendationEvidence:
    return RecommendationEvidence(
        owner="unit",
        source="unit",
        status=status,  # type: ignore[arg-type]
        revocation_reason=revocation_reason,
        tested_at=tested_at,
        expires_at=expires_at,
        machine_tier="local-m4-16gib",
        task_id="coding-check-v1",
        check_id="coding-check-v1",
        prompt_sha256="a" * 64,
        runtime_freeze="frozen",
        profile_fingerprint=entry.profile.fingerprint,
        result_references=("ref",) if status == "qualified" else (),
        result_hashes=("b" * 64,) if status == "qualified" else (),
    )


def test_expiry_is_exact_and_unknown_tiers_fail_closed() -> None:
    entry = load_coding_profiles()[0]
    tested_at = datetime(2026, 1, 1, tzinfo=UTC)
    expires_at = datetime(2026, 1, 11, tzinfo=UTC)
    qualified = ProfileEntry(
        profile=entry.profile,
        evidence=(
            _synthetic_evidence(entry, tested_at=tested_at, expires_at=expires_at),
        ),
    )
    assert qualified.status("other-tier", now=expires_at) == "unknown"
    assert qualified.status("local-m4-16gib", now=expires_at) == "expired"
    assert (
        qualified.status("local-m4-16gib", now=expires_at.replace(microsecond=1))
        == "expired"
    )
    assert qualified.evidence[0].is_current(
        entry.profile,
        machine_tier="local-m4-16gib",
        now=tested_at + timedelta(days=1),
    )


def test_revoked_and_absent_evidence_never_qualify() -> None:
    entry = load_coding_profiles()[0]
    revoked = ProfileEntry(
        profile=entry.profile,
        evidence=(
            _synthetic_evidence(entry, status="revoked", revocation_reason="failed"),
        ),
    )
    assert revoked.status("local-m4-16gib") == "revoked"

    assert (
        ProfileEntry(profile=entry.profile, evidence=()).status("local-m4-16gib")
        == "unknown"
    )


def _catalogue_with_evidence(evidence_toml: str) -> str:
    source = (
        __import__("importlib.resources", fromlist=["files"])
        .files("mlx_tui")
        .joinpath("coding_profiles.toml")
        .read_text()
    )
    head, sep, tail = source.partition("[[profiles]]")
    assert sep
    first, sep2, rest = tail.partition("[[profiles]]")
    assert sep2
    return f"{head}[[profiles]]{first}{evidence_toml}\n[[profiles]]{rest}"


def _evidence_toml(fingerprint: str, *, status: str, reason: str, expires: str) -> str:
    return f"""[[profiles.evidence]]
owner = "unit"
source = "unit"
status = "{status}"
revocation_reason = "{reason}"
tested_at = "2026-01-01T00:00:00Z"
expires_at = "{expires}"
machine_tier = "local-m4-16gib"
task_id = "coding-check-v1"
check_id = "coding-check-v1"
prompt_sha256 = "{"a" * 64}"
runtime_freeze = "frozen"
profile_fingerprint = "{fingerprint}"
result_references = []
result_hashes = []
"""


def test_catalogue_rejects_bad_evidence_timestamp(tmp_path: Path) -> None:
    fingerprint = load_coding_profiles()[0].profile.fingerprint
    path = tmp_path / "bad-timestamp.toml"
    path.write_text(
        _catalogue_with_evidence(
            _evidence_toml(
                fingerprint, status="unqualified", reason="", expires="not-a-date"
            )
        )
    )
    with pytest.raises(ProfileValidationError):
        load_coding_profiles(path)


def test_catalogue_rejects_revocation_without_reason(tmp_path: Path) -> None:
    fingerprint = load_coding_profiles()[0].profile.fingerprint
    path = tmp_path / "bad-revocation.toml"
    path.write_text(
        _catalogue_with_evidence(
            _evidence_toml(
                fingerprint,
                status="revoked",
                reason="",
                expires="2026-01-11T00:00:00Z",
            )
        )
    )
    with pytest.raises(ProfileValidationError):
        load_coding_profiles(path)
