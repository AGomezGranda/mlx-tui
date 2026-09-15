"""Sequential twelve-slot comparison runner."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal, cast

import psutil

from mlx_tui import process
from mlx_tui.chat import TurnResult, stream_turn
from mlx_tui.comparison_contracts import (
    _PROFILE_COUNT,
    CODING_CHECK_EXPECTED,
    CODING_CHECK_PROMPT,
    TRIAL_REPEATS,
    ComparisonIdentityError,
    ComparisonInput,
    ComparisonPersistenceError,
    ComparisonResult,
    ComparisonValidationError,
    JSONValue,
    MemorySample,
    TrialResult,
    _now_iso,
    parse_loopback_url,
)
from mlx_tui.comparison_persistence import (
    _json_value,
    comparison_dir,
    save_comparison,
)
from mlx_tui.comparison_summary import _summary
from mlx_tui.history.tokens import prepare_context
from mlx_tui.models import verify_cached_assets
from mlx_tui.process import ProcessIdentity
from mlx_tui.profiles import CodingProfile, ProfileEntry


def coding_payload(
    profile: ProfileEntry | CodingProfile,
    snapshot_path: Path | str,
    *,
    prompt: str = CODING_CHECK_PROMPT,
) -> dict[str, JSONValue]:
    """Build the exact synthetic request without mutating chat history."""
    coding = profile.profile if isinstance(profile, ProfileEntry) else profile
    window = prepare_context(
        [{"role": "user", "content": prompt}],
        coding.system or None,
        coding.max_ctx,
        coding.max_tokens,
    )
    payload: dict[str, JSONValue] = {
        "messages": [dict(message) for message in window.messages],
        "stream": True,
        "stream_options": {"include_usage": True},
        "model": str(Path(snapshot_path).resolve()),
        "max_tokens": coding.max_tokens,
        "temperature": coding.temperature,
        "top_p": coding.top_p,
        "seed": coding.seed,
        "chat_template_kwargs": {"enable_thinking": coding.enable_thinking},
    }
    return payload


def assert_coding_check_v1(result: TurnResult, expected_model: str) -> None:
    """Reject anything except the exact, complete, identity-matched answer."""
    if result.response_model != expected_model:
        raise ComparisonIdentityError(
            "response model identity is missing or mismatched"
        )
    if result.tool_calls:
        raise ComparisonValidationError("coding-check-v1 returned a tool call")
    if not result.stream_complete or result.finish_reason != "stop":
        raise ComparisonValidationError("coding-check-v1 stream is incomplete")
    if result.full_text.strip() != CODING_CHECK_EXPECTED:
        raise ComparisonValidationError("coding-check-v1 answer did not match exactly")


def coding_check_v1(result: TurnResult, expected_model: str) -> bool:
    """Pure boolean form of :func:`assert_coding_check_v1`."""
    try:
        assert_coding_check_v1(result, expected_model)
    except ComparisonValidationError:
        return False
    return True


def _hash_file(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise ComparisonValidationError(f"missing profile asset: {path}") from exc


def _verify_snapshots(value: ComparisonInput) -> None:
    if (
        len(value.profiles) != _PROFILE_COUNT
        or len(value.snapshot_paths) != _PROFILE_COUNT
    ):
        raise ComparisonValidationError(
            "comparison requires exactly two profiles and snapshots"
        )
    if len(value.verified_asset_hashes) not in {0, _PROFILE_COUNT}:
        raise ComparisonValidationError("asset hashes must cover both profiles")
    for index, entry in enumerate(value.profiles):
        snapshot = value.snapshot_paths[index]
        supplied: dict[str, str] = (
            value.verified_asset_hashes[index] if value.verified_asset_hashes else {}
        )
        verify_profile_snapshot(entry, snapshot, supplied)


def verify_profile_snapshot(
    entry: ProfileEntry,
    snapshot: Path,
    supplied: dict[str, str] | None = None,
) -> dict[str, str]:
    """Verify one cached snapshot and return its observed immutable asset hashes."""
    if not snapshot.is_dir():
        raise ComparisonValidationError(f"snapshot is not a directory: {snapshot}")
    try:
        verify_cached_assets(snapshot, entry.profile.template_assets)
    except ValueError as exc:
        raise ComparisonValidationError(str(exc)) from exc
    observed: dict[str, str] = {}
    for name, expected in entry.profile.template_assets.items():
        asset = snapshot / name
        actual = _hash_file(asset)
        if actual != expected or (supplied and supplied.get(name) != actual):
            raise ComparisonValidationError(f"profile asset hash mismatch: {asset}")
        observed[name] = actual
    return observed


def _sample_process(
    identity: ProcessIdentity,
    stop: threading.Event,
    samples: list[MemorySample],
) -> None:
    """Sample cheap values in a worker; never discover a process here."""
    while True:
        try:
            rss = psutil.Process(identity.pid).memory_info().rss / 2**30
            available = psutil.virtual_memory().available / 2**30
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
            OSError,
        ):
            rss = None
            available = None
        samples.append(
            MemorySample(timestamp=time.time(), rss_gib=rss, available_gib=available)
        )
        if stop.wait(0.05):
            return


async def _stop_sampler(stop: threading.Event, sampler: asyncio.Task[None]) -> None:
    stop.set()
    try:
        await asyncio.shield(sampler)
    except asyncio.CancelledError:
        stop.set()
        try:
            await asyncio.shield(sampler)
        except asyncio.CancelledError:
            pass


def _response_trial(  # noqa: PLR0913, PLR0917
    trial: TrialResult,
    response: TurnResult,
    samples: list[MemorySample],
    before: ProcessIdentity,
    after: ProcessIdentity,
    expected_model: str,
) -> TrialResult:
    quality_pass = coding_check_v1(response, expected_model)
    quality_reason: str | None = None
    if not quality_pass:
        if response.tool_calls:
            quality_reason = "tool call returned"
        elif not response.stream_complete:
            quality_reason = "incomplete stream"
        elif response.full_text.strip() != CODING_CHECK_EXPECTED:
            quality_reason = "answer mismatch"
    accounting = response.accounting
    return replace(
        trial,
        state="completed",
        answer=response.full_text,
        reasoning=response.reasoning_text,
        tool_calls=tuple(
            cast(dict[str, JSONValue], _json_value(item))
            for item in response.tool_calls
        ),
        response_model=response.response_model,
        finish_reason=response.finish_reason,
        stream_complete=response.stream_complete,
        quality_pass=quality_pass,
        quality_reason=quality_reason,
        prompt_tokens=accounting.prompt_tokens,
        completion_tokens=accounting.completion_tokens,
        prompt_estimated=accounting.prompt_estimated,
        completion_estimated=accounting.completion_estimated,
        cached_prompt_tokens=response.cached_prompt_tokens,
        first_output_s=response.first_output_s,
        answer_started_s=response.answer_started_s,
        total_s=response.total_s,
        rss_samples=tuple(samples),
        process_identity_before=before,
        process_identity_after=after,
    )


def _replace_trial(
    result: ComparisonResult, index: int, trial: TrialResult
) -> ComparisonResult:
    trials = list(result.trials)
    trials[index] = trial
    return replace(result, trials=tuple(trials), updated_at=_now_iso())


def _terminal(
    result: ComparisonResult,
    status: Literal["failed", "cancelled"],
    error: str,
) -> ComparisonResult:
    return replace(result, status=status, error=error, updated_at=_now_iso())


def _save_or_keep(result: ComparisonResult) -> bool:
    try:
        save_comparison(result)
    except ComparisonPersistenceError:
        return False
    return True


async def run_comparison(  # noqa: PLR0911, PLR0912, PLR0915
    comparison: ComparisonInput,
    *,
    on_progress: Callable[[ComparisonResult], None] = lambda _result: None,
) -> ComparisonResult:
    """Run twelve ordered slots, checkpointing before every notification/request."""
    endpoint = parse_loopback_url(comparison.endpoint)
    _verify_snapshots(comparison)
    if len({entry.profile.id for entry in comparison.profiles}) != _PROFILE_COUNT:
        raise ComparisonValidationError("comparison profiles must have distinct IDs")
    if comparison.profile_order is not None and comparison.profile_order != tuple(
        entry.profile.id for entry in comparison.profiles
    ):
        raise ComparisonValidationError("profile order does not match input profiles")
    result_path = comparison.result_path or comparison_dir() / f"{uuid.uuid4()}.json"
    comparison = replace(
        comparison,
        endpoint=endpoint.url,
        result_path=result_path,
        profile_order=(
            comparison.profiles[0].profile.id,
            comparison.profiles[1].profile.id,
        ),
    )
    run_id = str(uuid.uuid4())
    trials = tuple(
        TrialResult(profile_id=entry.profile.id, repeat_index=repeat_index)
        for entry in comparison.profiles
        for repeat_index in range(TRIAL_REPEATS + 1)
    )
    result = ComparisonResult(
        run_id=run_id, status="running", comparison=comparison, trials=trials
    )
    if not _save_or_keep(result):
        raise ComparisonPersistenceError("could not save initial running comparison")
    try:
        on_progress(result)
    except Exception as exc:
        result = _terminal(result, "failed", f"progress callback failed: {exc}")
        _save_or_keep(result)
        return result

    for trial_index, slot in enumerate(result.trials):
        entry_index = 0 if slot.profile_id == comparison.profiles[0].profile.id else 1
        profile = comparison.profiles[entry_index]
        snapshot = comparison.snapshot_paths[entry_index]
        expected_model = str(snapshot.resolve())
        current = slot
        try:
            before = process.find_server_process(endpoint.host, endpoint.port)
            if comparison.launch_evidence.get("owned") is True:
                if before != comparison.process_identity:
                    raise ComparisonIdentityError(
                        "managed server process identity is not the retained child"
                    )
            elif before is None:
                before = comparison.process_identity
            if before is None:
                raise ComparisonIdentityError("server process identity is unavailable")
            payload = coding_payload(profile, snapshot, prompt=comparison.prompt)
            prompt_estimate = prepare_context(
                [{"role": "user", "content": comparison.prompt}],
                profile.profile.system or None,
                profile.profile.max_ctx,
                profile.profile.max_tokens,
            ).input_tokens
            current = replace(
                slot,
                state="attempted",
                payload=payload,
                process_identity_before=before,
            )
            result = _replace_trial(result, trial_index, current)
            if not _save_or_keep(result):
                return _terminal(result, "failed", "checkpoint failed before request")
            on_progress(result)
            samples: list[MemorySample] = []
            stop = threading.Event()
            sampler = asyncio.create_task(
                asyncio.to_thread(_sample_process, before, stop, samples)
            )
            try:
                response = await stream_turn(
                    endpoint.url,
                    cast(dict[str, object], payload),
                    prompt_estimate=prompt_estimate,
                    on_flush=lambda _text: None,
                )
            finally:
                await _stop_sampler(stop, sampler)
            after = process.find_server_process(endpoint.host, endpoint.port)
            if comparison.launch_evidence.get("owned") is True:
                if after != comparison.process_identity:
                    raise ComparisonIdentityError(
                        "managed server process identity changed during trial"
                    )
            elif after is None:
                after = comparison.process_identity
            if after is None or after != before:
                raise ComparisonIdentityError(
                    "server process identity changed during trial"
                )
            if response.response_model != expected_model:
                raise ComparisonIdentityError(
                    "response model identity is missing or mismatched"
                )
            current = _response_trial(
                current, response, samples, before, after, expected_model
            )
        except asyncio.CancelledError:
            current = replace(current, error="comparison cancelled", cancelled=True)
            result = _replace_trial(result, trial_index, current)
            result = _terminal(result, "cancelled", "comparison cancelled")
            _save_or_keep(result)
            raise
        except ComparisonIdentityError as exc:
            current = replace(current, error=str(exc))
            result = _replace_trial(result, trial_index, current)
            result = _terminal(result, "failed", str(exc))
            _save_or_keep(result)
            return result
        except Exception as exc:
            current = replace(current, error=f"{type(exc).__name__}: {exc}")
            result = _replace_trial(result, trial_index, current)
            result = _terminal(result, "failed", str(exc))
            _save_or_keep(result)
            return result
        result = _replace_trial(result, trial_index, current)
        result = replace(result, updated_at=_now_iso())
        if not _save_or_keep(result):
            return _terminal(result, "failed", "checkpoint failed after request")
        try:
            on_progress(result)
        except Exception as exc:
            result = _terminal(result, "failed", f"progress callback failed: {exc}")
            _save_or_keep(result)
            return result

    completed = replace(result, status="completed", updated_at=_now_iso())
    result = replace(completed, summary=_summary(completed))
    if not _save_or_keep(result):
        return _terminal(result, "failed", "checkpoint failed at completion")
    try:
        on_progress(result)
    except Exception as exc:
        result = _terminal(result, "failed", f"progress callback failed: {exc}")
        _save_or_keep(result)
    return result
