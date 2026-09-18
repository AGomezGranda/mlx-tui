"""Compare-tab integration tests (extracted from Metrics coverage)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from textual.widgets import (
    Button,
    Collapsible,
    DataTable,
    Input,
    Select,
    Static,
    TabbedContent,
)
from textual.widgets._data_table import Coordinate

from mlx_tui.app import MlxTuiApp
from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.compare.pane import ComparePane
from mlx_tui.comparison import contracts as comparison
from mlx_tui.comparison.store import (
    choice_is_committed,
    load_comparison,
    save_comparison,
)
from mlx_tui.comparison.summary import _summary
from mlx_tui.config import AppConfig
from mlx_tui.operations import OperationKind
from mlx_tui.process import ProcessIdentity
from mlx_tui.status import ServerProbe
from tests.conftest import AppHarness


def _comparison_result(
    value: comparison.ComparisonInput, status: comparison.ComparisonStatus
) -> comparison.ComparisonResult:
    result = comparison.ComparisonResult(
        run_id="ui-comparison-run",
        status=status,
        comparison=value,
        trials=tuple(
            comparison.TrialResult(
                entry.profile.id,
                index,
                state="completed" if status == "completed" else "not_attempted",
                quality_pass=True if status == "completed" else None,
            )
            for entry in value.profiles
            for index in range(6)
        ),
        summary={"scope": "coding-check-v1 only"} if status == "completed" else {},
    )
    save_comparison(result)
    return result


def _stub_comparison_preflight(
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshots = {
        entry.profile.repo_id: tmp_path / entry.profile.id
        for entry in harness.app.profile_entries
    }
    for snapshot in snapshots.values():
        snapshot.mkdir()

    def resolve(repo_id: str, _revision: str) -> Path:
        return snapshots[repo_id]

    def verify(*_args: object, **_kwargs: object) -> dict[str, str]:
        return {}

    identity = ProcessIdentity(os.getpid(), 1.0)

    def same_identity(_host: str, _port: int) -> ProcessIdentity:
        return identity

    monkeypatch.setattr("mlx_tui.compare.workflow.resolve_cached_snapshot", resolve)
    monkeypatch.setattr("mlx_tui.app.state.resolve_cached_snapshot", resolve)
    monkeypatch.setattr("mlx_tui.compare.workflow.verify_profile_snapshot", verify)
    monkeypatch.setattr("mlx_tui.app.state.verify_profile_snapshot", verify)
    monkeypatch.setattr(
        "mlx_tui.compare.workflow.process.find_server_process",
        same_identity,
    )


async def test_compare_keep_and_next_chat_preserve_state_and_exact_settings(
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _stub_comparison_preflight(harness, monkeypatch, tmp_path)

    async def complete(
        value: comparison.ComparisonInput,
        *,
        on_progress: Callable[[comparison.ComparisonResult], None],
    ) -> comparison.ComparisonResult:
        result = _comparison_result(value, "completed")
        on_progress(result)
        return result

    monkeypatch.setattr("mlx_tui.compare.workflow.run_comparison", complete)
    pane = harness.app.query_one(ComparePane)
    original_messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "answer"},
    ]
    harness.chat_pane().messages = list(original_messages)
    draft = harness.app.query_one("#chat-input", ChatInput)
    draft.text = "draft stays"

    pane._preflight()
    pane._start_comparison()
    assert await harness.wait_for(
        lambda app: (
            app.operations.current is OperationKind.IDLE
            and app.last_comparison is not None
        )
    )
    assert harness.chat_pane().messages == original_messages
    assert draft.text == "draft stays"

    pane._keep_a()
    profile = harness.app.last_comparison.comparison.profiles[0].profile  # type: ignore[union-attr]
    assert harness.app.active_profile_id == profile.id
    assert harness.app.config.seed == profile.seed
    assert harness.app.config.enable_thinking == profile.enable_thinking
    assert harness.app.saved_choice is not None
    assert choice_is_committed(harness.app.saved_choice)

    reopened = MlxTuiApp(
        host=harness.app.host,
        port=harness.app.port,
        config=AppConfig(model="unchanged"),
    )
    assert reopened.saved_choice == harness.app.saved_choice
    assert reopened.active_profile_id is None
    assert reopened.config.model == "unchanged"

    draft.focus()
    await harness.pilot.press("ctrl+enter")
    assert await harness.wait_for(lambda app: len(app.history.all_records()) == 1)
    request = harness.server.requests[-1]
    assert request["seed"] == profile.seed
    assert request["chat_template_kwargs"] == {
        "enable_thinking": profile.enable_thinking
    }
    assert request["temperature"] == profile.temperature
    assert request["top_p"] == profile.top_p
    assert request["max_tokens"] == profile.max_tokens
    harness.app.query_one("#param-temp", Input).value = "0.25"
    await harness.pilot.pause()
    assert harness.app.active_profile_modified


async def test_comparison_owns_pair_and_suppresses_poll_until_one_cleanup_refresh(
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _stub_comparison_preflight(harness, monkeypatch, tmp_path)
    pane = harness.app.query_one(ComparePane)
    ids = harness.app.comparison_profile_ids
    pane.query_one("#comparison-profile-a", Select).value = ids[1]
    await harness.pilot.pause()
    assert harness.app.comparison_profile_ids == (ids[1], ids[0])

    entered = asyncio.Event()
    finish = asyncio.Event()

    async def blocked(
        value: comparison.ComparisonInput,
        *,
        on_progress: Callable[[comparison.ComparisonResult], None],
    ) -> comparison.ComparisonResult:
        entered.set()
        await finish.wait()
        result = _comparison_result(value, "completed")
        on_progress(result)
        return result

    polls = 0

    async def fetch() -> ServerProbe:
        nonlocal polls
        polls += 1
        return ServerProbe(state="green", model_id=None)

    monkeypatch.setattr("mlx_tui.compare.workflow.run_comparison", blocked)
    monkeypatch.setattr(harness.app, "_fetch_probe", fetch)
    pane._preflight()
    pane._start_comparison()
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert harness.app.operations.current is OperationKind.COMPARING
    assert harness.app.query_one("#chat-input", ChatInput).disabled
    assert harness.app.query_one("#models-table").disabled
    await harness.app._poll()
    assert polls == 0
    finish.set()
    assert await harness.wait_for(
        lambda app: app.operations.current is OperationKind.IDLE
    )
    assert polls == 1


async def test_escape_cancels_comparison_after_checkpoint(
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _stub_comparison_preflight(harness, monkeypatch, tmp_path)
    pane = harness.app.query_one(ComparePane)
    entered = asyncio.Event()

    async def blocked(
        value: comparison.ComparisonInput,
        *,
        on_progress: Callable[[comparison.ComparisonResult], None],
    ) -> comparison.ComparisonResult:
        running = _comparison_result(value, "running")
        on_progress(running)
        entered.set()
        try:
            await asyncio.sleep(30)
            raise AssertionError("unreachable")
        except asyncio.CancelledError:
            cancelled = replace(running, status="cancelled", error="cancelled")
            save_comparison(cancelled)
            raise

    monkeypatch.setattr("mlx_tui.compare.workflow.run_comparison", blocked)
    pane._preflight()
    pane._start_comparison()
    await asyncio.wait_for(entered.wait(), timeout=2)
    await harness.pilot.press("escape")
    assert await harness.wait_for(
        lambda app: app.operations.current is OperationKind.IDLE
    )
    assert harness.app.last_comparison is not None
    assert harness.app.last_comparison.status == "cancelled"


def _populated_completed_result(
    harness: AppHarness, tmp_path: Path
) -> comparison.ComparisonResult:
    pane = harness.app.query_one(ComparePane)
    assert pane._preflight_input is not None
    base_input = pane._preflight_input
    entries = base_input.profiles
    trials: list[comparison.TrialResult] = []
    for profile_index, entry in enumerate(entries):
        median = 1.0 if profile_index == 0 else 2.0
        for repeat_index in range(6):
            failed = profile_index == 1 and repeat_index == 3
            trials.append(
                comparison.TrialResult(
                    profile_id=entry.profile.id,
                    repeat_index=repeat_index,
                    state="completed",
                    error="answer mismatch" if failed else None,
                    payload={
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": True,
                        "stream_options": {"include_usage": True},
                        "max_tokens": 32,
                        "temperature": 0.0,
                        "top_p": 1.0,
                        "seed": 7,
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                    answer="" if failed else "def answer(): return 42",
                    quality_pass=not failed,
                    quality_reason="answer mismatch" if failed else None,
                    total_s=median,
                    first_output_s=0.1,
                    answer_started_s=0.2,
                    cached_prompt_tokens=10,
                    response_model=str(base_input.snapshot_paths[profile_index]),
                    process_identity_before=base_input.process_identity,
                    process_identity_after=base_input.process_identity,
                )
            )
    provisional = comparison.ComparisonResult(
        run_id="populated-run",
        status="completed",
        comparison=base_input,
        trials=tuple(trials),
    )
    summary = _summary(provisional)
    result = replace(provisional, summary=summary)
    save_comparison(result)
    return result


async def test_populated_result_renders_measurements_and_failures(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _stub_comparison_preflight(harness, monkeypatch, tmp_path)
    pane = harness.app.query_one(ComparePane)
    pane._preflight()
    result = _populated_completed_result(harness, tmp_path)
    harness.app.last_comparison = result
    pane._render_result(result)
    await harness.pilot.pause()

    header = pane.query_one("#comparison-result-header", Static)
    header_text = str(header.render())
    assert "populated-run" in header_text
    assert result.comparison.profiles[0].profile.name in header_text

    measurements = pane.query_one("#comparison-measurements", DataTable)
    assert measurements.row_count == 3
    first_a = measurements.get_cell_at(Coordinate(0, 1))
    first_b = measurements.get_cell_at(Coordinate(0, 2))
    assert "1.00s" in str(first_a)
    assert "2.00s" in str(first_b)
    passing_a = measurements.get_cell_at(Coordinate(1, 1))
    passing_b = measurements.get_cell_at(Coordinate(1, 2))
    assert "5 / 5" in str(passing_a)
    assert "4 / 5" in str(passing_b)

    trials = pane.query_one("#comparison-trials", DataTable)
    assert trials.row_count == 12
    qualities = [str(trials.get_cell_at(Coordinate(row, 3))) for row in range(12)]
    assert "fail" in qualities

    detail = pane.query_one("#comparison-trial-detail", Static)
    assert "answer mismatch" in str(detail.render())

    keep_a = pane.query_one("#comparison-keep-a", Button)
    assert result.comparison.profiles[0].profile.name in str(keep_a.label)


async def test_changed_setup_cannot_relabel_prior_result(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _stub_comparison_preflight(harness, monkeypatch, tmp_path)
    pane = harness.app.query_one(ComparePane)
    pane._preflight()
    result = _populated_completed_result(harness, tmp_path)
    harness.app.last_comparison = result
    pane._render_result(result)
    await harness.pilot.pause()
    before = str(pane.query_one("#comparison-result-header", Static).render())
    keep_before = str(pane.query_one("#comparison-keep-a", Button).label)

    ids = harness.app.comparison_profile_ids
    harness.app.update_comparison_candidate(0, ids[1])
    await harness.pilot.pause()

    after = str(pane.query_one("#comparison-result-header", Static).render())
    assert after == before
    assert str(pane.query_one("#comparison-keep-a", Button).label) == keep_before
    assert result.comparison.profiles[0].profile.name in after
    assert result.comparison.profiles[1].profile.name in after


async def test_readiness_invalidation_excludes_reason(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _stub_comparison_preflight(harness, monkeypatch, tmp_path)
    pane = harness.app.query_one(ComparePane)
    pane._preflight()
    assert pane._preflight_input is not None

    pane.query_one("#comparison-reason", Input).value = "still deciding"
    await harness.pilot.pause()
    assert pane._preflight_input is not None

    pane.query_one("#comparison-machine-tier", Input).value = "other-tier"
    pane._setup_input_changed()
    assert pane._preflight_input is None


async def test_busy_guards_block_decisions_and_open(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _stub_comparison_preflight(harness, monkeypatch, tmp_path)
    pane = harness.app.query_one(ComparePane)
    pane._preflight()
    result = _populated_completed_result(harness, tmp_path)
    harness.app.last_comparison = result
    pane._render_result(result)
    await harness.pilot.pause()

    entered = asyncio.Event()
    finish = asyncio.Event()

    async def blocked(
        value: comparison.ComparisonInput,
        *,
        on_progress: Callable[[comparison.ComparisonResult], None],
    ) -> comparison.ComparisonResult:
        entered.set()
        await finish.wait()
        completed = _comparison_result(value, "completed")
        on_progress(completed)
        return completed

    monkeypatch.setattr("mlx_tui.compare.workflow.run_comparison", blocked)
    pane._preflight()
    pane._start_comparison()
    await asyncio.wait_for(entered.wait(), timeout=2)
    await harness.pilot.pause()

    assert pane.query_one("#comparison-keep-a", Button).disabled
    assert pane.query_one("#comparison-use-saved", Button).disabled
    assert pane.query_one("#comparison-open-result", Button).disabled
    assert not pane.query_one("#comparison-cancel", Button).disabled

    saved_before = harness.app.saved_choice
    pane._commit_decision("keep", 0)
    assert harness.app.saved_choice == saved_before
    pane.query_one("#comparison-open-path", Input).value = "/tmp/nope.json"
    # Direct open during busy must not replace the displayed result.
    # run_comparison progress may have replaced it with a running record;
    # the guard still must leave saved choice untouched.
    pane._open_result()
    assert harness.app.saved_choice == saved_before

    finish.set()
    assert await harness.wait_for(
        lambda app: app.operations.current is OperationKind.IDLE
    )

    # Chat lease also blocks decisions even when Compare itself is idle.
    assert harness.app.operations.try_acquire(OperationKind.CHATTING)
    harness.app.refresh_activity()
    await harness.pilot.pause()
    assert pane.query_one("#comparison-keep-a", Button).disabled
    assert pane.query_one("#comparison-open-result", Button).disabled
    pane._commit_decision("keep", 0)
    assert harness.app.saved_choice == saved_before
    harness.app.operations.release(OperationKind.CHATTING)
    harness.app.refresh_activity()
    await harness.pilot.pause()


async def test_save_then_failed_apply_is_truthful(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _stub_comparison_preflight(harness, monkeypatch, tmp_path)
    pane = harness.app.query_one(ComparePane)
    pane._preflight()
    result = _populated_completed_result(harness, tmp_path)
    harness.app.last_comparison = result
    pane._render_result(result)
    pane.query_one("#comparison-reason", Input).value = "prefer A"
    await harness.pilot.pause()

    def fail_apply(_profile_id: str, **_kwargs: object) -> None:
        raise ValueError("snapshot vanished")

    monkeypatch.setattr(harness.app, "apply_coding_profile", fail_apply)
    pane._keep_a()

    assert harness.app.saved_choice is not None
    assert harness.app.saved_choice.decision == "keep"
    assert choice_is_committed(harness.app.saved_choice)
    saved_text = str(pane.query_one("#comparison-saved", Static).render())
    assert "Choice saved; profile not applied" in saved_text
    assert harness.app.active_profile_id is None


async def test_open_completed_partial_and_corrupt_preserves_state(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _stub_comparison_preflight(harness, monkeypatch, tmp_path)
    pane = harness.app.query_one(ComparePane)
    pane._preflight()
    completed = _populated_completed_result(harness, tmp_path)
    completed_path = Path(str(completed.comparison.result_path))
    harness.app.last_comparison = completed
    harness.app.saved_choice = None
    harness.app.active_profile_id = None
    setup_before = harness.app.comparison_profile_ids
    pane._render_result(completed)
    await harness.pilot.pause()

    partial_input = pane._preflight_input
    assert partial_input is not None
    partial = comparison.ComparisonResult(
        run_id="partial-run",
        status="failed",
        comparison=partial_input,
        trials=tuple(
            comparison.TrialResult(entry.profile.id, index)
            for entry in partial_input.profiles
            for index in range(6)
        ),
        error="boom",
    )
    partial_path = tmp_path / "partial.json"
    save_comparison(partial, partial_path)
    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text("{not json")

    pane.query_one("#comparison-open-path", Input).value = str(partial_path)
    pane._open_result()
    await harness.pilot.pause()
    assert harness.app.last_comparison is not None
    assert harness.app.last_comparison.run_id == "partial-run"
    assert harness.app.comparison_profile_ids == setup_before
    assert harness.app.saved_choice is None
    assert harness.app.active_profile_id is None
    assert pane.query_one("#comparison-keep-a", Button).disabled

    pane.query_one("#comparison-open-path", Input).value = str(corrupt_path)
    pane._open_result()
    await harness.pilot.pause()
    assert harness.app.last_comparison is not None
    assert harness.app.last_comparison.run_id == "partial-run"
    assert "could not open" in str(
        pane.query_one("#comparison-open-status", Static).render()
    )

    pane.query_one("#comparison-open-path", Input).value = str(completed_path)
    pane._open_result()
    await harness.pilot.pause()
    assert harness.app.last_comparison is not None
    assert harness.app.last_comparison.run_id == "populated-run"
    assert not pane.query_one("#comparison-keep-a", Button).disabled


async def test_copied_result_decides_copy_not_original(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _stub_comparison_preflight(harness, monkeypatch, tmp_path)
    pane = harness.app.query_one(ComparePane)
    pane._preflight()
    result = _populated_completed_result(harness, tmp_path)
    original_path = Path(str(result.comparison.result_path))
    assert original_path.exists()  # noqa: ASYNC240

    copied_path = tmp_path / "copied.json"
    copied_path.write_text(original_path.read_text())  # noqa: ASYNC240
    pane.query_one("#comparison-open-path", Input).value = str(copied_path)
    pane._open_result()
    await harness.pilot.pause()
    opened = harness.app.last_comparison
    assert opened is not None
    assert Path(str(opened.comparison.result_path)) == copied_path

    pane.query_one("#comparison-reason", Input).value = "keep copied A"
    pane._keep_a()
    assert harness.app.saved_choice is not None
    assert Path(str(harness.app.saved_choice.result_path)) == copied_path

    copied = load_comparison(copied_path)
    assert isinstance(copied.summary.get("decision"), dict)
    original = load_comparison(original_path)
    assert "decision" not in original.summary


async def test_comparison_layout_adapts_and_reveals_results(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_comparison_preflight(harness, monkeypatch, tmp_path)
    pane = harness.app.query_one(ComparePane)
    harness.app.query_one(TabbedContent).active = "compare"
    await harness.pilot.resize_terminal(120, 50)
    await harness.pilot.pause()
    left = pane.query_one("#comparison-profile-a", Select)
    right = pane.query_one("#comparison-profile-b", Select)
    assert left.region.y == right.region.y
    assert left.region.right <= right.region.x
    assert pane.query_one("#compare-evidence", Collapsible).collapsed
    assert not pane.query_one("#comparison-results").display
    assert pane.query_one("#comparison-empty").display
    run = pane.query_one("#comparison-run", Button)
    assert run.region.bottom <= pane.region.bottom

    await harness.pilot.resize_terminal(70, 40)
    await harness.pilot.pause()
    assert left.region.bottom <= right.region.y
    assert pane.virtual_size.width <= pane.size.width
    pane.query_one("#compare-evidence", Collapsible).collapsed = False
    await harness.pilot.pause()
    field = pane.query_one("#comparison-runtime", Input)
    field.scroll_visible(animate=False)
    await harness.pilot.pause()
    assert field.region.width > 30
    assert field.region.right <= pane.region.right

    pane._preflight()
    result = _populated_completed_result(harness, tmp_path)
    harness.app.last_comparison = result
    pane._render_result(result)
    await harness.pilot.pause()
    assert pane.query_one("#comparison-results").display
    assert not pane.query_one("#comparison-empty").display
    assert pane.query_one("#comparison-decision").display
    harness.app.last_comparison = replace(result, status="failed")
    pane._render_result(harness.app.last_comparison)
    assert not pane.query_one("#comparison-decision").display
