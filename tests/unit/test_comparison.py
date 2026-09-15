"""Headless comparison contracts and fail-closed runner tests."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from functools import partial
from pathlib import Path

import httpx
import pytest

from mlx_tui import comparison, comparison_persistence, comparison_runner
from mlx_tui.chat import TurnResult
from mlx_tui.process import ProcessIdentity
from mlx_tui.profiles import load_coding_profiles
from tests.builders import sse_frames


def test_facade_reexports_production_and_qualification_api() -> None:
    facade_names = [
        "ComparisonInput",
        "ComparisonResult",
        "SavedChoice",
        "ComparisonPersistenceError",
        "ComparisonValidationError",
        "DecisionKind",
        "JSONValue",
        "choice_is_committed",
        "choice_path",
        "commit_choice",
        "comparison_dir",
        "load_choice",
        "load_comparison",
        "parse_loopback_url",
        "run_comparison",
        "verify_profile_snapshot",
    ]
    for name in facade_names:
        assert hasattr(comparison, name), f"facade missing {name}"
    # Private helpers must live in their defining modules, not the facade.
    assert not hasattr(comparison, "_decision_payload")
    assert not hasattr(comparison, "_verify_snapshots")
    assert hasattr(comparison_persistence, "_decision_payload")
    assert hasattr(comparison_runner, "_verify_snapshots")


def test_loopback_parser_accepts_only_exact_supported_endpoint() -> None:
    assert (
        comparison.parse_loopback_url("http://127.0.0.9:18080/v1/chat/completions").url
        == "http://127.0.0.9:18080/v1/chat/completions"
    )
    assert (
        comparison.parse_loopback_url("http://[::1]:18080/v1/chat/completions").host
        == "::1"
    )
    for url in (
        "http://127.0.0.1/v1/chat/completions",
        "http://localhost:18080/v1/chat/completions",
        "http://127.0.0.1.evil:18080/v1/chat/completions",
        "http://user:pass@127.0.0.1:18080/v1/chat/completions",
        "http://127.0.0.1:18080/v1/chat/completions?x=1",
        "http://127.0.0.1:18080/v1/chat/completions#x",
        "http://127.0.0.1:18080/health",
        "https://127.0.0.1:18080/v1/chat/completions",
    ):
        with pytest.raises(comparison.ComparisonValidationError):
            comparison.parse_loopback_url(url)


def _result_input(tmp_path: Path) -> comparison.ComparisonInput:
    entries = load_coding_profiles()
    snapshots = (tmp_path / "a", tmp_path / "b")
    for snapshot in snapshots:
        snapshot.mkdir()
    identity = ProcessIdentity(os.getpid(), 0.0)
    return comparison.ComparisonInput(
        endpoint="http://127.0.0.1:18080/v1/chat/completions",
        profiles=tuple(entries),
        snapshot_paths=snapshots,
        process_identity=identity,
        result_path=tmp_path / "comparison.json",
    )


def _skip_snapshot_check(_value: comparison.ComparisonInput) -> None:
    return None


def _same_identity(_host: str, _port: int) -> ProcessIdentity:
    return ProcessIdentity(os.getpid(), 0.0)


def test_json_round_trip_and_reopen_running_as_interrupted(tmp_path: Path) -> None:
    value = _result_input(tmp_path)
    result = comparison.ComparisonResult(
        run_id="run-1",
        status="running",
        comparison=value,
        trials=tuple(
            comparison.TrialResult(entry.profile.id, index)
            for entry in value.profiles
            for index in range(6)
        ),
    )
    path = comparison.save_comparison(result)
    reopened = comparison.load_comparison(path)
    assert reopened.status == "interrupted"
    assert len(reopened.trials) == comparison.TRIAL_SLOTS

    path.write_text('{"schema_version": 99}')
    newer_bytes = path.read_bytes()
    with pytest.raises(comparison.ComparisonPersistenceError):
        comparison.load_comparison(path)
    assert path.read_bytes() == newer_bytes


def test_negative_timing_is_rejected() -> None:
    with pytest.raises(comparison.ComparisonValidationError):
        comparison.TrialResult("a", 1, total_s=-0.1)


def _completed_result(tmp_path: Path) -> comparison.ComparisonResult:
    value = _result_input(tmp_path)
    result = comparison.ComparisonResult(
        run_id="choice-run",
        status="completed",
        comparison=value,
        trials=tuple(
            comparison.TrialResult(entry.profile.id, index)
            for entry in value.profiles
            for index in range(6)
        ),
    )
    comparison.save_comparison(result)
    return result


def test_choice_is_committed_only_after_finalized_run(tmp_path: Path) -> None:
    result = _completed_result(tmp_path)
    profile = result.comparison.profiles[0].profile
    choice = comparison.SavedChoice(
        run_id=result.run_id,
        result_path=result.comparison.result_path,  # type: ignore[arg-type]
        decision="keep",
        profile_id=profile.id,
        profile_fingerprint=profile.fingerprint,
        reason="lower latency for this check",
    )
    choice_file = tmp_path / "choice.json"
    pending = replace(
        result,
        summary={
            "decision": comparison_persistence._decision_payload(choice, "pending")
        },
    )
    comparison.save_comparison(pending)
    comparison.save_choice(choice, choice_file)
    reopened = comparison.load_choice(choice_file)
    assert not comparison.choice_is_committed(reopened)

    finalized = replace(
        result,
        summary={
            "decision": comparison_persistence._decision_payload(choice, "finalized")
        },
    )
    comparison.save_comparison(finalized)
    assert comparison.choice_is_committed(reopened)


def test_pending_run_without_choice_is_uncommitted(tmp_path: Path) -> None:
    result = _completed_result(tmp_path)
    profile = result.comparison.profiles[0].profile
    choice = comparison.SavedChoice(
        run_id=result.run_id,
        result_path=result.comparison.result_path,  # type: ignore[arg-type]
        decision="keep",
        profile_id=profile.id,
        profile_fingerprint=profile.fingerprint,
        reason="pending",
    )
    comparison.save_comparison(
        replace(
            result,
            summary={
                "decision": comparison_persistence._decision_payload(choice, "pending")
            },
        )
    )

    with pytest.raises(comparison.ComparisonPersistenceError):
        comparison.load_choice(tmp_path / "missing-choice.json")
    assert not comparison.choice_is_committed(choice)


def test_commit_choice_round_trip_and_reject_requires_reason(tmp_path: Path) -> None:
    result = _completed_result(tmp_path)
    choice_file = tmp_path / "choice.json"
    profile_id = result.comparison.profiles[1].profile.id

    finalized, choice = comparison.commit_choice(
        result,
        "keep",
        profile_id=profile_id,
        reason="preferred tradeoff",
        path=choice_file,
    )

    assert finalized.summary["decision"]["status"] == "finalized"  # type: ignore[index]
    assert comparison.load_choice(choice_file) == choice
    assert comparison.choice_is_committed(choice)
    with pytest.raises(comparison.ComparisonValidationError):
        comparison.commit_choice(result, "reject", path=choice_file)

    retained, retained_choice = comparison.commit_choice(
        result, "retain", reason="keep current baseline", path=choice_file
    )
    assert retained_choice.profile_id is None
    assert comparison.choice_is_committed(retained_choice)
    assert retained.summary["decision"]["decision"] == "retain"  # type: ignore[index]

    rejected, rejected_choice = comparison.commit_choice(
        result, "reject", reason="neither passed", path=choice_file
    )
    assert rejected_choice.profile_id is None
    assert comparison.choice_is_committed(rejected_choice)
    assert rejected.summary["decision"]["decision"] == "reject"  # type: ignore[index]


def test_choice_write_failure_rolls_back_run_and_previous_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _completed_result(tmp_path)
    choice_file = tmp_path / "choice.json"
    choice_file.write_text("previous")

    def fail_choice(_value: comparison.SavedChoice, _path: Path | None = None) -> Path:
        raise comparison.ComparisonPersistenceError("denied")

    monkeypatch.setattr(comparison_persistence, "save_choice", fail_choice)
    with pytest.raises(comparison.ComparisonPersistenceError):
        comparison.commit_choice(
            result,
            "keep",
            profile_id=result.comparison.profiles[0].profile.id,
            path=choice_file,
        )

    assert choice_file.read_text() == "previous"
    assert (
        "decision"
        not in comparison.load_comparison(
            result.comparison.result_path  # type: ignore[arg-type]
        ).summary
    )


def test_finalized_run_failure_rolls_back_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _completed_result(tmp_path)
    choice_file = tmp_path / "choice.json"
    choice_file.write_text("previous")
    original = comparison_persistence.save_comparison
    calls = 0

    def fail_final(
        value: comparison.ComparisonResult, path: Path | None = None
    ) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise comparison.ComparisonPersistenceError("denied")
        return original(value, path)

    monkeypatch.setattr(comparison_persistence, "save_comparison", fail_final)
    with pytest.raises(comparison.ComparisonPersistenceError):
        comparison.commit_choice(
            result,
            "retain",
            reason="keep current baseline",
            path=choice_file,
        )

    assert choice_file.read_text() == "previous"
    assert (
        "decision"
        not in comparison.load_comparison(
            result.comparison.result_path  # type: ignore[arg-type]
        ).summary
    )


def _install_stream_stub(
    monkeypatch: pytest.MonkeyPatch,
    requests: list[dict[str, object]],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        model = body["model"]
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=sse_frames(
                deltas=[comparison.CODING_CHECK_EXPECTED],
                usage=(10, 5),
                model=model,
            ),
        )

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport)
    )


async def test_runner_orders_twelve_requests_and_persists_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _result_input(tmp_path)
    requests: list[dict[str, object]] = []
    _install_stream_stub(monkeypatch, requests)
    monkeypatch.setattr(comparison_runner, "_verify_snapshots", _skip_snapshot_check)
    monkeypatch.setattr(
        comparison_runner.process, "find_server_process", _same_identity
    )
    result = await comparison.run_comparison(value)
    assert result.status == "completed"
    assert len(requests) == comparison.TRIAL_SLOTS
    assert [request["model"] for request in requests[:6]] == [
        str(value.snapshot_paths[0].resolve())
    ] * 6
    assert [request["model"] for request in requests[6:]] == [
        str(value.snapshot_paths[1].resolve())
    ] * 6
    assert all(request["seed"] == 7 for request in requests)
    assert comparison.load_comparison(value.result_path).status == "completed"  # type: ignore[arg-type]


async def test_transport_failure_stops_without_later_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _result_input(tmp_path)
    requests = 0

    async def fail(*_args: object, **_kwargs: object) -> TurnResult:
        nonlocal requests
        requests += 1
        raise RuntimeError("load failed")

    monkeypatch.setattr(comparison_runner, "stream_turn", fail)
    monkeypatch.setattr(comparison_runner, "_verify_snapshots", _skip_snapshot_check)
    monkeypatch.setattr(
        comparison_runner.process, "find_server_process", _same_identity
    )
    result = await comparison.run_comparison(value)
    assert result.status == "failed"
    assert requests == 1
    assert sum(trial.state == "not_attempted" for trial in result.trials) == 11


async def test_cancellation_rethrows_and_checkpoints_cancelled_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _result_input(tmp_path)
    entered = asyncio.Event()

    async def blocked(*_args: object, **_kwargs: object) -> TurnResult:
        entered.set()
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    monkeypatch.setattr(comparison_runner, "stream_turn", blocked)
    monkeypatch.setattr(comparison_runner, "_verify_snapshots", _skip_snapshot_check)
    monkeypatch.setattr(
        comparison_runner.process, "find_server_process", _same_identity
    )
    task = asyncio.create_task(comparison.run_comparison(value))
    assert await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = comparison.load_comparison(value.result_path)  # type: ignore[arg-type]
    assert saved.status == "cancelled"
    assert saved.trials[0].cancelled is True
    assert all(trial.state == "not_attempted" for trial in saved.trials[1:])
