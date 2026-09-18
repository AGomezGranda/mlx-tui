"""Headless comparison contracts and fail-closed runner tests."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import httpx
import pytest

from mlx_tui.chat import TurnResult
from mlx_tui.comparison import contracts, decoding, encoding, runner, store
from mlx_tui.process import ProcessIdentity
from mlx_tui.profiles import ProfileEntry, RecommendationEvidence, load_coding_profiles
from tests.builders import sse_frames


def test_comparison_modules_own_their_symbols() -> None:
    assert hasattr(contracts, "ComparisonInput")
    assert hasattr(runner, "run_comparison")
    assert hasattr(store, "save_comparison")
    assert not hasattr(contracts, "run_comparison")
    assert not hasattr(contracts, "save_comparison")
    assert hasattr(store, "_decision_payload")
    assert hasattr(runner, "_verify_snapshots")


def test_loopback_parser_accepts_only_exact_supported_endpoint() -> None:
    assert (
        contracts.parse_loopback_url("http://127.0.0.9:18080/v1/chat/completions").url
        == "http://127.0.0.9:18080/v1/chat/completions"
    )
    assert (
        contracts.parse_loopback_url("http://[::1]:18080/v1/chat/completions").host
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
        with pytest.raises(contracts.ComparisonValidationError):
            contracts.parse_loopback_url(url)


def _result_input(tmp_path: Path) -> contracts.ComparisonInput:
    entries = load_coding_profiles()
    snapshots = (tmp_path / "a", tmp_path / "b")
    for snapshot in snapshots:
        snapshot.mkdir()
    identity = ProcessIdentity(os.getpid(), 0.0)
    return contracts.ComparisonInput(
        endpoint="http://127.0.0.1:18080/v1/chat/completions",
        profiles=tuple(entries),
        snapshot_paths=snapshots,
        process_identity=identity,
        result_path=tmp_path / "comparison.json",
    )


def _skip_snapshot_check(_value: contracts.ComparisonInput) -> None:
    return None


def _same_identity(_host: str, _port: int) -> ProcessIdentity:
    return ProcessIdentity(os.getpid(), 0.0)


def test_nested_trial_round_trip_rejects_unknown_fields(tmp_path: Path) -> None:
    identity = ProcessIdentity(123, 4.5)
    trial = contracts.TrialResult(
        "profile-a",
        1,
        state="completed",
        payload={"messages": [{"role": "user", "content": "hello"}]},
        tool_calls=({"index": 0, "arguments": "{}"},),
        rss_samples=(contracts.MemorySample(1.0, 2.0, 3.0),),
        process_identity_before=identity,
        process_identity_after=identity,
    )
    result = contracts.ComparisonResult(
        run_id="nested-trial",
        status="completed",
        comparison=_result_input(tmp_path),
        trials=(trial,),
    )
    encoded = encoding.encode_comparison(result)
    assert decoding.decode_comparison(encoded) == result
    document = json.loads(encoded)
    document["trials"][0]["unknown_field"] = True
    with pytest.raises(contracts.ComparisonValidationError, match="unexpected field"):
        decoding.decode_comparison(document)


def test_json_round_trip_and_reopen_running_as_interrupted(tmp_path: Path) -> None:
    value = _result_input(tmp_path)
    result = contracts.ComparisonResult(
        run_id="run-1",
        status="running",
        comparison=value,
        trials=tuple(
            contracts.TrialResult(entry.profile.id, index)
            for entry in value.profiles
            for index in range(6)
        ),
    )
    path = store.save_comparison(result)
    reopened = store.load_comparison(path)
    assert reopened.status == "interrupted"
    assert len(reopened.trials) == contracts.TRIAL_SLOTS

    path.write_text('{"schema_version": 99}')
    newer_bytes = path.read_bytes()
    with pytest.raises(contracts.ComparisonPersistenceError):
        store.load_comparison(path)
    assert path.read_bytes() == newer_bytes


def test_task_id_default_is_neutral_and_historical_value_round_trips(
    tmp_path: Path,
) -> None:
    value = _result_input(tmp_path)
    assert value.task_id == "coding-check-v1"

    base_entries = load_coding_profiles()
    now = datetime.now(UTC)
    entries = tuple(
        ProfileEntry(
            profile=entry.profile,
            evidence=(
                RecommendationEvidence(
                    owner="unit",
                    source="unit",
                    status="qualified",
                    revocation_reason=None,
                    tested_at=now - timedelta(days=1),
                    expires_at=now + timedelta(days=10),
                    machine_tier="test-tier",
                    task_id="milestone-b-coding-check",
                    check_id="coding-check-v1",
                    prompt_sha256="a" * 64,
                    runtime_freeze="frozen",
                    profile_fingerprint=entry.profile.fingerprint,
                    result_references=("ref",),
                    result_hashes=("b" * 64,),
                ),
            ),
        )
        for entry in base_entries
    )
    historical = replace(value, profiles=entries, task_id="milestone-b-coding-check")
    result = contracts.ComparisonResult(
        run_id="task-id-run",
        status="completed",
        comparison=historical,
        trials=tuple(
            contracts.TrialResult(entry.profile.id, index)
            for entry in entries
            for index in range(6)
        ),
    )
    encoded = encoding.encode_comparison(result)
    decoded = decoding.decode_comparison(encoded)
    assert decoded == result
    assert decoded.comparison.task_id == "milestone-b-coding-check"
    assert decoded.comparison.profiles[0].evidence[0].task_id == (
        "milestone-b-coding-check"
    )


def test_negative_timing_is_rejected() -> None:
    with pytest.raises(contracts.ComparisonValidationError):
        contracts.TrialResult("a", 1, total_s=-0.1)


def _completed_result(tmp_path: Path) -> contracts.ComparisonResult:
    value = _result_input(tmp_path)
    result = contracts.ComparisonResult(
        run_id="choice-run",
        status="completed",
        comparison=value,
        trials=tuple(
            contracts.TrialResult(entry.profile.id, index)
            for entry in value.profiles
            for index in range(6)
        ),
    )
    store.save_comparison(result)
    return result


def test_choice_is_committed_only_after_finalized_run(tmp_path: Path) -> None:
    result = _completed_result(tmp_path)
    profile = result.comparison.profiles[0].profile
    choice = contracts.SavedChoice(
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
        summary={"decision": store._decision_payload(choice, "pending")},
    )
    store.save_comparison(pending)
    store.save_choice(choice, choice_file)
    reopened = store.load_choice(choice_file)
    assert not store.choice_is_committed(reopened)

    finalized = replace(
        result,
        summary={"decision": store._decision_payload(choice, "finalized")},
    )
    store.save_comparison(finalized)
    assert store.choice_is_committed(reopened)


def test_pending_run_without_choice_is_uncommitted(tmp_path: Path) -> None:
    result = _completed_result(tmp_path)
    profile = result.comparison.profiles[0].profile
    choice = contracts.SavedChoice(
        run_id=result.run_id,
        result_path=result.comparison.result_path,  # type: ignore[arg-type]
        decision="keep",
        profile_id=profile.id,
        profile_fingerprint=profile.fingerprint,
        reason="pending",
    )
    store.save_comparison(
        replace(
            result,
            summary={"decision": store._decision_payload(choice, "pending")},
        )
    )

    with pytest.raises(contracts.ComparisonPersistenceError):
        store.load_choice(tmp_path / "missing-choice.json")
    assert not store.choice_is_committed(choice)


def test_commit_choice_round_trip_and_reject_requires_reason(tmp_path: Path) -> None:
    result = _completed_result(tmp_path)
    choice_file = tmp_path / "choice.json"
    profile_id = result.comparison.profiles[1].profile.id

    finalized, choice = store.commit_choice(
        result,
        "keep",
        profile_id=profile_id,
        reason="preferred tradeoff",
        path=choice_file,
    )

    assert finalized.summary["decision"]["status"] == "finalized"  # type: ignore[index]
    assert store.load_choice(choice_file) == choice
    assert store.choice_is_committed(choice)
    with pytest.raises(contracts.ComparisonValidationError):
        store.commit_choice(result, "reject", path=choice_file)

    retained, retained_choice = store.commit_choice(
        result, "retain", reason="keep current baseline", path=choice_file
    )
    assert retained_choice.profile_id is None
    assert store.choice_is_committed(retained_choice)
    assert retained.summary["decision"]["decision"] == "retain"  # type: ignore[index]

    rejected, rejected_choice = store.commit_choice(
        result, "reject", reason="neither passed", path=choice_file
    )
    assert rejected_choice.profile_id is None
    assert store.choice_is_committed(rejected_choice)
    assert rejected.summary["decision"]["decision"] == "reject"  # type: ignore[index]


def test_choice_write_failure_rolls_back_run_and_previous_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _completed_result(tmp_path)
    choice_file = tmp_path / "choice.json"
    choice_file.write_text("previous")

    def fail_choice(_value: contracts.SavedChoice, _path: Path | None = None) -> Path:
        raise contracts.ComparisonPersistenceError("denied")

    monkeypatch.setattr(store, "save_choice", fail_choice)
    with pytest.raises(contracts.ComparisonPersistenceError):
        store.commit_choice(
            result,
            "keep",
            profile_id=result.comparison.profiles[0].profile.id,
            path=choice_file,
        )

    assert choice_file.read_text() == "previous"
    assert (
        "decision"
        not in store.load_comparison(
            result.comparison.result_path  # type: ignore[arg-type]
        ).summary
    )


def test_finalized_run_failure_rolls_back_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _completed_result(tmp_path)
    choice_file = tmp_path / "choice.json"
    choice_file.write_text("previous")
    original = store.save_comparison
    calls = 0

    def fail_final(value: contracts.ComparisonResult, path: Path | None = None) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise contracts.ComparisonPersistenceError("denied")
        return original(value, path)

    monkeypatch.setattr(store, "save_comparison", fail_final)
    with pytest.raises(contracts.ComparisonPersistenceError):
        store.commit_choice(
            result,
            "retain",
            reason="keep current baseline",
            path=choice_file,
        )

    assert choice_file.read_text() == "previous"
    assert (
        "decision"
        not in store.load_comparison(
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
                deltas=[contracts.CODING_CHECK_EXPECTED],
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
    monkeypatch.setattr(runner, "_verify_snapshots", _skip_snapshot_check)
    monkeypatch.setattr(runner.process, "find_server_process", _same_identity)
    result = await runner.run_comparison(value)
    assert result.status == "completed"
    assert len(requests) == contracts.TRIAL_SLOTS
    assert [request["model"] for request in requests[:6]] == [
        str(value.snapshot_paths[0].resolve())
    ] * 6
    assert [request["model"] for request in requests[6:]] == [
        str(value.snapshot_paths[1].resolve())
    ] * 6
    assert all(request["seed"] == 7 for request in requests)
    assert store.load_comparison(value.result_path).status == "completed"  # type: ignore[arg-type]


async def test_transport_failure_stops_without_later_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = _result_input(tmp_path)
    requests = 0

    async def fail(*_args: object, **_kwargs: object) -> TurnResult:
        nonlocal requests
        requests += 1
        raise RuntimeError("load failed")

    monkeypatch.setattr(runner, "stream_turn", fail)
    monkeypatch.setattr(runner, "_verify_snapshots", _skip_snapshot_check)
    monkeypatch.setattr(runner.process, "find_server_process", _same_identity)
    result = await runner.run_comparison(value)
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

    monkeypatch.setattr(runner, "stream_turn", blocked)
    monkeypatch.setattr(runner, "_verify_snapshots", _skip_snapshot_check)
    monkeypatch.setattr(runner.process, "find_server_process", _same_identity)
    task = asyncio.create_task(runner.run_comparison(value))
    assert await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = store.load_comparison(value.result_path)  # type: ignore[arg-type]
    assert saved.status == "cancelled"
    assert saved.trials[0].cancelled is True
    assert all(trial.state == "not_attempted" for trial in saved.trials[1:])
