"""Focused latency arithmetic regression for the moved summary module."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mlx_tui.comparison.contracts import (
    ComparisonInput,
    ComparisonResult,
    JSONValue,
    TrialResult,
)
from mlx_tui.comparison.summary import _summary
from mlx_tui.process import ProcessIdentity
from mlx_tui.profiles import (
    ProfileEntry,
    RecommendationEvidence,
    load_coding_profiles,
)

_TIER = "test-tier"
_IDENTITY = ProcessIdentity(os.getpid(), 1.0)
_PAYLOAD: dict[str, JSONValue] = {
    "messages": [{"role": "user", "content": "hi"}],
    "stream": True,
    "stream_options": {"include_usage": True},
    "max_tokens": 32,
    "temperature": 0.0,
    "top_p": 1.0,
    "seed": 7,
    "chat_template_kwargs": {"enable_thinking": False},
}


def _qualified_entry(base: ProfileEntry) -> ProfileEntry:
    now = datetime.now(UTC)
    evidence = RecommendationEvidence(
        owner="unit",
        source="unit",
        status="qualified",
        revocation_reason=None,
        tested_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=10),
        machine_tier=_TIER,
        task_id="coding-check-v1",
        check_id="coding-check-v1",
        prompt_sha256="a" * 64,
        runtime_freeze="frozen",
        profile_fingerprint=base.profile.fingerprint,
        result_references=("ref",),
        result_hashes=("b" * 64,),
    )
    return ProfileEntry(profile=base.profile, evidence=(evidence,))


def _eligible_result(  # noqa: PLR0913
    tmp_path: Path,
    median_a: float,
    median_b: float,
    *,
    machine_tier: str = _TIER,
    runtime_unknown: bool = False,
    use_catalogue_entries: bool = False,
) -> tuple[ComparisonInput, ComparisonResult]:
    base_entries = load_coding_profiles()
    if use_catalogue_entries:
        entries = tuple(base_entries)
    else:
        entries = (_qualified_entry(base_entries[0]), _qualified_entry(base_entries[1]))
    snapshots = (tmp_path / "a", tmp_path / "b")
    for snapshot in snapshots:
        snapshot.mkdir(exist_ok=True)
    runtime: dict[str, JSONValue] = (
        {"operator": "unknown"} if runtime_unknown else {"operator": "lab-notes"}
    )
    comparison_input = ComparisonInput(
        endpoint="http://127.0.0.1:18080/v1/chat/completions",
        profiles=entries,
        snapshot_paths=snapshots,
        runtime_evidence=runtime,
        install_evidence={"operator": "lab-notes"},
        launch_evidence={"operator": "lab-notes"},
        provenance={"source": "unit"},
        process_identity=_IDENTITY,
        isolation_evidence={"operator": "lab-notes"},
        machine_tier=machine_tier,
        operator_conditions={"operator": "calm"},
        profile_order=(entries[0].profile.id, entries[1].profile.id),
        result_path=tmp_path / "comparison.json",
    )
    medians = (median_a, median_b)
    trials: list[TrialResult] = []
    for profile_index, entry in enumerate(entries):
        median = medians[profile_index]
        for repeat_index in range(6):
            trials.append(
                TrialResult(
                    profile_id=entry.profile.id,
                    repeat_index=repeat_index,
                    state="completed",
                    payload=dict(_PAYLOAD),
                    quality_pass=True,
                    total_s=median,
                    first_output_s=0.1,
                    answer_started_s=0.2,
                    cached_prompt_tokens=10,
                    process_identity_before=_IDENTITY,
                    process_identity_after=_IDENTITY,
                )
            )
    result = ComparisonResult(
        run_id="latency-run",
        status="completed",
        comparison=comparison_input,
        trials=tuple(trials),
    )
    return comparison_input, result


def _latency(result: ComparisonResult) -> dict[str, object]:
    summary = _summary(result)
    latency = summary["latency"]
    assert isinstance(latency, dict)
    return latency  # type: ignore[return-value]


@pytest.mark.parametrize(("median_a", "median_b"), [(1.0, 2.0), (2.0, 1.0)])
def test_opposite_orders_give_half_difference_and_advantage(
    tmp_path: Path, median_a: float, median_b: float
) -> None:
    _, result = _eligible_result(tmp_path, median_a, median_b)
    latency = _latency(result)
    assert latency["status"] == "advantage_possible"
    assert latency["difference_fraction"] == pytest.approx(0.5)


def test_equal_positive_medians_give_zero_inconclusive(tmp_path: Path) -> None:
    _, result = _eligible_result(tmp_path, 1.5, 1.5)
    latency = _latency(result)
    assert latency["status"] == "inconclusive"
    assert latency["difference_fraction"] == pytest.approx(0.0)


def test_two_zeros_remain_inconclusive(tmp_path: Path) -> None:
    _, result = _eligible_result(tmp_path, 0.0, 0.0)
    latency = _latency(result)
    assert latency["status"] == "inconclusive"
    assert latency["reason"] == "repeat timing is zero"


def test_sub_five_percent_difference_stays_inconclusive(tmp_path: Path) -> None:
    _, result = _eligible_result(tmp_path, 1.0, 1.04)
    latency = _latency(result)
    assert latency["status"] == "inconclusive"
    fraction = latency["difference_fraction"]
    assert isinstance(fraction, float)
    assert fraction < 0.05


def test_unqualified_evidence_blocks_advantage_despite_large_gap(
    tmp_path: Path,
) -> None:
    _, result = _eligible_result(tmp_path, 1.0, 2.0, use_catalogue_entries=True)
    latency = _latency(result)
    assert latency["status"] == "inconclusive"
    assert "evidence" in str(latency.get("reason", ""))


def test_unknown_runtime_provenance_blocks_advantage(tmp_path: Path) -> None:
    _, result = _eligible_result(tmp_path, 1.0, 2.0, runtime_unknown=True)
    latency = _latency(result)
    assert latency["status"] == "inconclusive"
    assert "provenance" in str(latency.get("reason", ""))
