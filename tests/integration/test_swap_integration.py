"""Warm loading, swap policy, rescan, and identity integration tests."""

from __future__ import annotations

import sys
from collections.abc import Callable

import pytest

from mlx_tui import serverctl
from mlx_tui.app import MlxTuiApp
from mlx_tui.chat_pane import ChatInput
from mlx_tui.config import AppConfig
from mlx_tui.models import ModelRow
from mlx_tui.operations import OperationKind
from mlx_tui.status import ServerProbe
from mlx_tui.swap import health_timeout
from tests.conftest import AppHarness
from tests.integration.model_helpers import (
    ROW,
    _fake_process_4242,
    _log_text,
    _select_row,
    _stub_rows,
)


async def test_warm_swap_happy_path(stub_harness: AppHarness) -> None:
    harness = stub_harness
    harness.server.mode = "probe"
    await harness.app._poll()  # green against the ok-mode GET handler
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: (
            "✓ request succeeded for mlx-community/stub-test" in _log_text(harness)
        )
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE
    assert harness.app.server_identity.last_response_model == "mlx-community/stub-test"
    assert harness.app.server_identity.generation_state == "succeeded"
    assert harness.app.effective_model() == "mlx-community/stub-test"


async def test_warm_swap_catalog_does_not_overwrite_verified_selection(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    catalog = ServerProbe("green", None, ("other-model", ROW.repo_id))

    async def catalog_probe(self: MlxTuiApp) -> ServerProbe:
        return catalog

    def fake_warm(url: str, repo_id: str, *, timeout_s: float) -> str | None:
        assert timeout_s == health_timeout(ROW.size_on_disk)
        return repo_id

    def unexpected_wait(*args: object, **kwargs: object) -> None:
        pytest.fail("a verified completion must not wait for the catalog to change")

    monkeypatch.setattr(MlxTuiApp, "_fetch_probe", catalog_probe)
    monkeypatch.setattr(serverctl, "warm_load", fake_warm)
    monkeypatch.setattr(serverctl, "wait_healthy", unexpected_wait)
    await harness.app._poll()
    assert harness.app.effective_model() is None
    await _select_row(harness)
    await harness.pilot.press("enter")
    assert await harness.wait_for(lambda a: "✓" in _log_text(harness))
    await harness.app._poll()
    assert harness.app.effective_model() == ROW.repo_id
    assert harness.app.operations.current is OperationKind.IDLE

    # A changed process generation invalidates success, not explicit selection.
    monkeypatch.setattr("mlx_tui.process.find_server_process", _fake_process_4242)
    await harness.app._poll()
    assert harness.app.effective_model() == ROW.repo_id
    assert harness.app.server_identity.generation_state == "unknown"


async def test_swap_blocked_while_busy(stub_harness: AppHarness) -> None:
    harness = stub_harness
    await harness.app._poll()
    assert harness.app.operations.try_acquire(OperationKind.LOADING)
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: "loading operation already in progress" in _log_text(harness)
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.LOADING
    harness.app.operations.release(OperationKind.LOADING)


async def test_cannot_swap_when_unreachable_and_unconfigured(
    stub_harness: AppHarness,
) -> None:
    harness = stub_harness
    harness.server.mode = "html"
    await harness.app._poll()
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(lambda a: "cannot load" in _log_text(harness)), (
        _log_text(harness)
    )


async def test_warm_swap_completion_respects_live_turn(
    stub_harness: AppHarness,
) -> None:
    """A live chat owns the lease, so a warm swap cannot start alongside it."""
    harness = stub_harness
    await harness.app._poll()
    await _select_row(harness)

    inp = harness.app.query_one("#chat-input", ChatInput)
    pane = harness.chat_pane()
    assert harness.app.operations.try_acquire(OperationKind.CHATTING)
    pane._turn_active = True
    inp.disabled = True

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: "chatting operation already in progress" in _log_text(harness)
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.CHATTING
    assert inp.disabled

    harness.app.operations.release(OperationKind.CHATTING)
    pane.end_turn()
    assert not inp.disabled


async def test_amber_refuses_swap_even_with_commands(
    stub_harness: AppHarness,
) -> None:
    harness = stub_harness
    harness.server.mode = "html"
    await harness.app._poll()
    assert harness.app.status_state == "amber"
    harness.app.config = AppConfig(
        start_cmd=f"{sys.executable} -c \"print('booting')\"",
        stop_cmd=f"{sys.executable} -c \"print('bye')\"",
    )
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: "cannot load" in _log_text(harness) and "amber" in _log_text(harness),
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE


async def test_warm_policy_rejects_non_green(stub_harness: AppHarness) -> None:
    harness = stub_harness
    await harness.app._poll()
    harness.app.status_state = "red"
    harness.app.config = AppConfig(swap_policy="warm")
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: 'swap_policy is "warm"' in _log_text(harness),
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE


async def test_restart_policy_requires_both_commands(
    stub_harness: AppHarness,
) -> None:
    harness = stub_harness
    await harness.app._poll()
    assert harness.app.status_state == "green"
    harness.app.config = AppConfig(
        swap_policy="restart",
        start_cmd=f"{sys.executable} -c \"print('booting')\"",
    )
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: 'swap_policy is "restart"' in _log_text(harness),
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE


async def test_warm_mismatched_response_model_keeps_old_identity(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    harness.server.mode = "ok"
    harness.server.model_id = "old-model"
    harness.app.select_model("old-model")
    harness.app.record_generation_success("old-model", "old-model")
    await harness.app._poll()
    assert harness.app.server_identity.last_response_model == "old-model"

    def fake_warm(url: str, repo_id: str, *, timeout_s: float) -> str | None:
        return "wrong-model"

    monkeypatch.setattr(serverctl, "warm_load", fake_warm)
    called: list[bool] = []

    def fail_if_called(  # noqa: PLR0913
        url: str,
        *,
        target_model: str | None = None,
        is_running: Callable[[], bool] | None = None,
        timeout_s: float,
        on_tick: Callable[[int], None] | None = None,
    ) -> ServerProbe | None:
        called.append(True)
        return None

    monkeypatch.setattr(serverctl, "wait_healthy", fail_if_called)
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: (
            "request unverified" in _log_text(harness)
            and "wrong-model" in _log_text(harness)
        ),
    ), _log_text(harness)
    assert called == []
    assert harness.app.operations.current is OperationKind.IDLE
    await harness.app._poll()
    assert harness.app.server_identity.last_response_model == "old-model"
    assert harness.app.effective_model() == ROW.repo_id
    assert harness.app.server_identity.generation_state == "failed"


async def test_warm_wrong_endpoint_model_keeps_old_marker(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    harness.server.mode = "probe"
    harness.server.model_id = "old-model"
    harness.app.select_model("old-model")
    harness.app.record_generation_success("old-model", "old-model")
    await harness.app._poll()
    assert harness.app.server_identity.last_response_model == "old-model"

    def fake_warm(url: str, repo_id: str, *, timeout_s: float) -> str | None:
        return None

    def fake_unhealthy(url: str, **kwargs: object) -> ServerProbe | None:
        return None

    monkeypatch.setattr(serverctl, "warm_load", fake_warm)
    monkeypatch.setattr(serverctl, "wait_healthy", fake_unhealthy)
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: (
            "request unverified" in _log_text(harness)
            and ROW.repo_id in _log_text(harness)
        ),
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE
    assert harness.app.server_identity.last_response_model == "old-model"
    assert harness.app.effective_model() == ROW.repo_id
    assert harness.app.server_identity.generation_state == "failed"


async def test_rescan_failure_keeps_rows_and_logs_once(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    await harness.app._poll()
    await _select_row(harness)
    pane = harness.models_pane()

    def failing_scan() -> list[ModelRow]:
        raise RuntimeError("cache exploded")

    def rescan_failed(app: object) -> bool:
        return harness.app._last_errors.get("rescan") == "RuntimeError: cache exploded"

    def rescan_failed_lines() -> list[str]:
        return [
            t
            for t in harness.app_log_lines()
            if "rescan failed" in t and "RuntimeError" in t
        ]

    monkeypatch.setattr("mlx_tui.models_pane.scan_models", failing_scan)
    pane.rescan()
    assert await harness.wait_for(rescan_failed), _log_text(harness)
    assert pane.rows == [ROW]
    assert len(rescan_failed_lines()) == 1

    # A repeated identical failure stays silent.
    pane.rescan()
    assert await harness.wait_for(rescan_failed)
    await harness.pilot.pause()
    assert len(rescan_failed_lines()) == 1

    # A successful rescan clears the source; recurrence becomes visible again.
    monkeypatch.setattr("mlx_tui.models_pane.scan_models", _stub_rows)
    pane.rescan()
    assert await harness.wait_for(lambda a: pane.rows == [ROW])
    monkeypatch.setattr("mlx_tui.models_pane.scan_models", failing_scan)
    pane.rescan()
    assert await harness.wait_for(lambda a: len(rescan_failed_lines()) == 2), _log_text(
        harness
    )
    assert pane.rows == [ROW]
    assert harness.app.operations.current is OperationKind.IDLE


async def test_chat_cleanup_keeps_model_operation_disabled(
    stub_harness: AppHarness,
) -> None:
    harness = stub_harness
    pane = harness.chat_pane()
    inp = pane.query_one("#chat-input", ChatInput)
    pane._turn_active = True
    pane._pending_draft = "recover during model operation"
    inp.disabled = True
    assert harness.app.operations.try_acquire(OperationKind.LOADING)
    focused = harness.app.focused
    pane.end_turn()
    harness.app.set_operation_ui(False)
    assert inp.text == "recover during model operation"
    assert inp.disabled
    assert harness.app.operations.current is OperationKind.LOADING
    assert harness.app.focused is focused
    pane.end_turn()
    assert inp.disabled
    harness.app.operations.release(OperationKind.LOADING)
    harness.app.set_operation_ui(False)
    assert not inp.disabled
