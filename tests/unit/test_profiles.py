"""Strict pinned-profile catalogue contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mlx_tui.profiles import (
    ProfileValidationError,
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
    assert entries[0].status("local-m4-16gib") == "qualified"
    assert entries[1].status("local-m4-16gib") == "unqualified"


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
        ('expires_at = "2026-09-20T18:00:00Z"', 'expires_at = "not-a-date"'),
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


def test_expiry_is_exact_and_unknown_tiers_fail_closed() -> None:
    entry = load_coding_profiles()[0]
    evidence = entry.evidence[0]
    assert entry.status("other-tier", now=evidence.expires_at) == "unknown"
    assert entry.status("local-m4-16gib", now=evidence.expires_at) == "expired"
    assert (
        entry.status("local-m4-16gib", now=evidence.expires_at.replace(microsecond=1))
        == "expired"
    )
    assert evidence.is_current(
        entry.profile,
        machine_tier="local-m4-16gib",
        now=datetime(2026, 9, 9, tzinfo=UTC),
    )


def test_revoked_and_absent_evidence_never_qualify(tmp_path: Path) -> None:
    source = (
        __import__("importlib.resources", fromlist=["files"])
        .files("mlx_tui")
        .joinpath("coding_profiles.toml")
        .read_text()
    )
    revoked = source.replace('status = "qualified"', 'status = "revoked"', 1).replace(
        'revocation_reason = ""', 'revocation_reason = "candidate failed"', 1
    )
    revoked_path = tmp_path / "revoked.toml"
    revoked_path.write_text(revoked)
    assert load_coding_profiles(revoked_path)[0].status("local-m4-16gib") == "revoked"

    no_evidence = source.replace(source[source.index("[[profiles.evidence]]") :], "")
    no_evidence_path = tmp_path / "none.toml"
    no_evidence_path.write_text(no_evidence)
    assert (
        load_coding_profiles(no_evidence_path)[0].status("local-m4-16gib") == "unknown"
    )
