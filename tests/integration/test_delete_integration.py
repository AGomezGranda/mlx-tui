"""Model deletion confirmation, race, and error integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mlx_tui.chat_pane import ChatInput
from mlx_tui.confirm import ConfirmScreen
from mlx_tui.models import CacheNotFound, ModelRow
from mlx_tui.operations import OperationKind
from mlx_tui.table import ModelsTable
from tests.conftest import AppHarness
from tests.integration.model_helpers import ROW, _log_text, _select_row, _stub_rows


async def _press_delete_and_wait_confirm(harness: AppHarness) -> None:
    await harness.pilot.press("d")
    assert await harness.wait_for(lambda a: isinstance(a.screen, ConfirmScreen)), (
        f"confirm never opened; screen={harness.app.screen!r}"
    )


def _controls_restored(harness: AppHarness) -> bool:
    return (
        harness.app.operations.current is OperationKind.IDLE
        and not harness.app.query_one("#chat-input", ChatInput).disabled
        and not harness.app.query_one("#models-table", ModelsTable).disabled
    )


async def test_delete_confirm_yes_deletes_exact_hashes_and_rescans(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    await _select_row(harness)
    seen: list[tuple[str, ...]] = []
    scans = {"count": 0}
    orig_scan = _stub_rows

    def counting_scan(avail_gib: float | None) -> list[ModelRow]:
        scans["count"] += 1
        return orig_scan(avail_gib)

    def fake_delete(hashes: tuple[str, ...]) -> int:
        seen.append(hashes)
        return 2_000_000_000

    monkeypatch.setattr("mlx_tui.models_pane.scan_models", counting_scan)
    monkeypatch.setattr("mlx_tui.models_pane.delete_repos", fake_delete)
    scans_before = scans["count"]
    await _press_delete_and_wait_confirm(harness)
    await harness.pilot.press("y")
    assert await harness.wait_for(
        lambda a: "deleted mlx-community/stub-test" in _log_text(harness)
    ), _log_text(harness)
    assert seen == [ROW.revision_hashes] == [("abc123",)]
    assert await harness.wait_for(lambda a: scans["count"] > scans_before)
    assert await harness.wait_for(lambda a: _controls_restored(harness))


async def test_delete_confirm_no_keeps_without_touching_cache(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    await _select_row(harness)
    called = False

    def fake_delete(hashes: tuple[str, ...]) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("mlx_tui.models_pane.delete_repos", fake_delete)
    await _press_delete_and_wait_confirm(harness)
    await harness.pilot.press("n")
    assert await harness.wait_for(lambda a: "kept" in _log_text(harness))
    assert await harness.wait_for(lambda a: not isinstance(a.screen, ConfirmScreen))
    assert not called
    assert _controls_restored(harness)


async def test_delete_escape_keeps_without_touching_cache(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    await _select_row(harness)
    called = False

    def fake_delete(hashes: tuple[str, ...]) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("mlx_tui.models_pane.delete_repos", fake_delete)
    await _press_delete_and_wait_confirm(harness)
    await harness.pilot.press("escape")
    assert await harness.wait_for(lambda a: "kept" in _log_text(harness))
    assert await harness.wait_for(lambda a: not isinstance(a.screen, ConfirmScreen))
    assert not called
    assert _controls_restored(harness)


async def test_delete_no_selection_keeps(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    await _select_row(harness)
    called = False

    def fake_delete(hashes: tuple[str, ...]) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("mlx_tui.models_pane.delete_repos", fake_delete)
    harness.models_pane().rows = []
    harness.models_pane().request_delete_model()
    await harness.pilot.pause()
    assert "no model selected to delete" in _log_text(harness)
    assert not isinstance(harness.app.screen, ConfirmScreen)
    assert not called
    assert _controls_restored(harness)


async def test_delete_refuses_selected_model(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    await harness.app._poll()
    harness.app.select_model(ROW.repo_id)
    assert harness.app.effective_model() == ROW.repo_id
    await _select_row(harness)
    called = False

    def fake_delete(hashes: tuple[str, ...]) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("mlx_tui.models_pane.delete_repos", fake_delete)
    await harness.pilot.press("d")
    assert await harness.wait_for(
        lambda a: "selected or last observed" in _log_text(harness)
    ), _log_text(harness)
    assert not isinstance(harness.app.screen, ConfirmScreen)
    assert not called
    assert _controls_restored(harness)


async def test_delete_refuses_last_observed_model(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    await _select_row(harness)
    harness.app.select_model(ROW.repo_id)
    harness.app.record_generation_success(ROW.repo_id, ROW.repo_id)
    harness.app.select_model("another/model")
    called = False

    def fake_delete(hashes: tuple[str, ...]) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("mlx_tui.models_pane.delete_repos", fake_delete)
    await harness.pilot.press("d")
    assert await harness.wait_for(
        lambda a: "selected or last observed" in _log_text(harness)
    )
    assert not isinstance(harness.app.screen, ConfirmScreen)
    assert not called


async def test_delete_race_became_active_after_confirm(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    await _select_row(harness)
    called = False

    def fake_delete(hashes: tuple[str, ...]) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("mlx_tui.models_pane.delete_repos", fake_delete)
    await _press_delete_and_wait_confirm(harness)
    # Selection changes between confirmation and worker execution.
    harness.app.select_model(ROW.repo_id)
    await harness.pilot.press("y")
    assert await harness.wait_for(lambda a: "now selected" in _log_text(harness)), (
        _log_text(harness)
    )
    assert not called
    assert await harness.wait_for(lambda a: _controls_restored(harness))


@pytest.mark.parametrize(
    ("exc", "name"),
    [
        (CacheNotFound("gone", Path("/gone")), "CacheNotFound"),
        (OSError("denied"), "OSError"),
        (RuntimeError("weird"), "RuntimeError"),
    ],
)
async def test_delete_failures_report_and_restore(
    stub_harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    name: str,
) -> None:
    harness = stub_harness
    await _select_row(harness)
    attempted: list[tuple[str, ...]] = []

    def fake_delete(hashes: tuple[str, ...]) -> int:
        attempted.append(hashes)
        raise exc

    monkeypatch.setattr("mlx_tui.models_pane.delete_repos", fake_delete)
    await _press_delete_and_wait_confirm(harness)
    await harness.pilot.press("y")
    assert await harness.wait_for(
        lambda a: f"delete failed: {name}" in _log_text(harness)
    ), _log_text(harness)
    assert attempted == [ROW.revision_hashes]
    assert await harness.wait_for(lambda a: _controls_restored(harness))
