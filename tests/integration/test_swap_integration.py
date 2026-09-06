"""Integration tests for the hybrid load/swap orchestration."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from textual.widgets import Input, TabbedContent

from mlx_tui import serverctl
from mlx_tui.app import MlxTuiApp
from mlx_tui.app.operations import OperationKind
from mlx_tui.config import AppConfig
from mlx_tui.confirm import ConfirmScreen
from mlx_tui.models import CacheNotFound, ModelRow
from mlx_tui.models_pane import ModelsPane
from mlx_tui.process import ProcessIdentity
from mlx_tui.serverctl import WarmLoadResult
from mlx_tui.status import ServerProbe
from mlx_tui.swap import health_timeout, resolve_swap_action
from mlx_tui.table import ModelsTable
from tests.conftest import AppHarness, StubServer

ROW = ModelRow("mlx-community/stub-test", 1_000_000_000, "4bit", True, ("abc123",))


def _stub_rows(avail_gib: float | None) -> list[ModelRow]:
    return [ROW]


def _no_process(
    host: str, port: int, pidfile: str | None = None
) -> ProcessIdentity | None:
    return None


def _fake_process_4242(
    host: str, port: int, pidfile: str | None = None
) -> ProcessIdentity | None:
    return ProcessIdentity(4242, 111.0)


@pytest.fixture
async def stub_harness(
    stub_server_factory: Callable[[str], StubServer],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AppHarness]:
    """Harness whose cache always scans to ROW."""
    monkeypatch.setattr("mlx_tui.models_pane.scan_models", _stub_rows)
    monkeypatch.setattr(
        "mlx_tui.models_pane.process.find_server_process",
        _no_process,
    )
    monkeypatch.setattr(
        "mlx_tui.app.process.find_server_process",
        _no_process,
    )
    monkeypatch.setattr(
        "mlx_tui.process.find_server_process",
        _no_process,
    )
    server = stub_server_factory("ok")
    app = MlxTuiApp(host="127.0.0.1", port=server.server_address[1])
    async with app.run_test() as pilot:
        yield AppHarness(app=app, pilot=pilot, server=server)


async def _select_row(harness: AppHarness) -> None:
    """Switch to the Models tab, wait for the stub row, focus the table."""
    harness.app.query_one(TabbedContent).active = "models"
    harness.app.query_one(ModelsPane).rescan()
    assert await harness.wait_for(lambda a: a.query_one(ModelsPane).rows == [ROW])
    harness.app.query_one("#models-table", ModelsTable).focus()


def _log_text(harness: AppHarness) -> str:
    return "\n".join(harness.app_log_lines())


async def test_warm_swap_happy_path(stub_harness: AppHarness) -> None:
    harness = stub_harness
    harness.server.mode = "probe"
    await harness.app._poll()  # green against the ok-mode GET handler
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: "✓ mlx-community/stub-test loaded" in _log_text(harness)
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE
    assert harness.app.server_identity.model_id == "mlx-community/stub-test"
    assert harness.app.effective_model() == "mlx-community/stub-test"


async def test_warm_swap_catalog_does_not_overwrite_verified_selection(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    catalog = ServerProbe("green", None, ("other-model", ROW.repo_id))

    async def catalog_probe(self: MlxTuiApp) -> ServerProbe:
        return catalog

    def fake_warm(url: str, repo_id: str, *, timeout_s: float) -> WarmLoadResult:
        assert timeout_s == health_timeout(ROW.size_on_disk)
        return WarmLoadResult(response_model=repo_id)

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

    # A changed process generation invalidates the last verified selection.
    monkeypatch.setattr("mlx_tui.process.find_server_process", _fake_process_4242)
    await harness.app._poll()
    assert harness.app.effective_model() is None


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


async def test_restart_swap_streams_and_completes(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    harness.server.mode = "ok"
    harness.server.model_id = ROW.repo_id
    await harness.app._poll()
    harness.app.config = AppConfig(
        start_cmd=f"{sys.executable} -c \"print('booting')\"",
        stop_cmd=f"{sys.executable} -c \"print('bye')\"",
    )
    await _select_row(harness)

    await harness.pilot.press("enter")
    harness.server.mode = "ok"

    assert await harness.wait_for(
        lambda a: "✓ mlx-community/stub-test is serving" in _log_text(harness)
    ), _log_text(harness)
    lines = _log_text(harness)
    assert "[swap] bye" in lines
    assert "[swap] booting" in lines
    assert harness.app.operations.current is OperationKind.IDLE
    await harness.app._poll()
    assert harness.app.server_identity.model_id == ROW.repo_id


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


async def _always_red(self: MlxTuiApp) -> str:
    return "red"


async def test_cold_start_boots_server(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    monkeypatch.setattr(MlxTuiApp, "_classify_liveness", _always_red)
    await harness.app._poll()
    harness.app.config = AppConfig(
        model="mlx-community/stub-test",
        start_cmd=f"{sys.executable} -c \"print('starting')\"",
    )
    monkeypatch.setattr(harness.app, "effective_model", lambda: ROW.repo_id)

    await harness.pilot.press("ctrl+s")

    assert await harness.wait_for(lambda a: "✓ server is up" in _log_text(harness)), (
        _log_text(harness)
    )
    flat_log = " ".join(_log_text(harness).split())
    assert "[swap] starting:" in flat_log
    assert "--model" in flat_log
    assert harness.app.operations.current is OperationKind.IDLE
    assert harness.app.server_identity.model_id == ROW.repo_id
    assert harness.app.effective_model() == ROW.repo_id


async def test_double_cold_start_is_guarded(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second ctrl+s inside a running boot must log, not crash the app."""
    harness = stub_harness
    monkeypatch.setattr(MlxTuiApp, "_classify_liveness", _always_red)
    await harness.app._poll()
    harness.app.config = AppConfig(start_cmd="sleep 0.5")

    await harness.pilot.press("ctrl+s")
    await harness.pilot.press("ctrl+s")

    assert await harness.wait_for(
        lambda a: "operation already in progress" in _log_text(harness)
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.RESTARTING


async def test_cold_start_when_server_already_up_never_spawns(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ctrl+s during the stale startup-red window must not spawn a duplicate."""
    harness = stub_harness
    procs: list[subprocess.Popen[str]] = []
    orig_spawn = serverctl.spawn_command

    def recording_spawn(cmd, *, on_line, shell=False, env=None):  # type: ignore[no-untyped-def]
        proc = orig_spawn(cmd, on_line=on_line, shell=shell, env=env)
        procs.append(proc)
        return proc

    monkeypatch.setattr(serverctl, "spawn_command", recording_spawn)
    harness.app.config = AppConfig(model=ROW.repo_id, start_cmd="true")

    await harness.pilot.press("ctrl+s")

    assert await harness.wait_for(
        lambda a: "server is already up" in _log_text(harness)
    ), _log_text(harness)
    assert procs == []
    assert harness.app.operations.current is OperationKind.IDLE


async def test_cold_start_with_unhealthy_process_restarts_it(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing mlx process holding the port is stopped first, not raced."""
    harness = stub_harness
    harness.server.mode = "html"
    await harness.app._poll()
    monkeypatch.setattr(
        "mlx_tui.app.process.find_server_process",
        _fake_process_4242,
    )
    monkeypatch.setattr(
        "mlx_tui.models_pane.process.find_server_process",
        _fake_process_4242,
    )
    monkeypatch.setattr(
        "mlx_tui.process.find_server_process",
        _fake_process_4242,
    )
    harness.app.config = AppConfig(
        model=ROW.repo_id,
        start_cmd=f"{sys.executable} -c \"print('booting')\"",
        stop_cmd=f"{sys.executable} -c \"print('bye')\"",
    )
    monkeypatch.setattr(harness.app, "effective_model", lambda: ROW.repo_id)
    procs: list[subprocess.Popen[str]] = []
    orig_spawn = serverctl.spawn_command

    def recording_spawn(cmd, *, on_line, shell=False, env=None):  # type: ignore[no-untyped-def]
        proc = orig_spawn(cmd, on_line=on_line, shell=shell, env=env)
        procs.append(proc)
        return proc

    monkeypatch.setattr(serverctl, "spawn_command", recording_spawn)

    await harness.pilot.press("ctrl+s")
    harness.server.mode = "ok"

    assert await harness.wait_for(lambda a: "✓ server is up" in _log_text(harness)), (
        _log_text(harness)
    )
    lines = _log_text(harness)
    assert "[swap] bye" in lines, lines
    assert "[swap] booting" in lines, lines
    assert procs, "restart path must spawn start_cmd"
    assert harness.app.operations.current is OperationKind.IDLE
    await harness.app._poll()
    assert harness.app.server_identity.model_id == ROW.repo_id


async def test_cold_start_with_process_but_no_stop_cmd_guides(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without stop_cmd an existing process can only be reported, not raced."""
    harness = stub_harness
    harness.server.mode = "html"
    await harness.app._poll()
    monkeypatch.setattr(
        "mlx_tui.app.process.find_server_process",
        _fake_process_4242,
    )
    monkeypatch.setattr(
        "mlx_tui.models_pane.process.find_server_process",
        _fake_process_4242,
    )
    monkeypatch.setattr(
        "mlx_tui.process.find_server_process",
        _fake_process_4242,
    )
    harness.app.config = AppConfig(start_cmd="true")
    procs: list[subprocess.Popen[str]] = []
    orig_spawn = serverctl.spawn_command

    def recording_spawn(cmd, *, on_line, shell=False, env=None):  # type: ignore[no-untyped-def]
        proc = orig_spawn(cmd, on_line=on_line, shell=shell, env=env)
        procs.append(proc)
        return proc

    monkeypatch.setattr(serverctl, "spawn_command", recording_spawn)

    await harness.pilot.press("ctrl+s")

    assert await harness.wait_for(
        lambda a: "running but not healthy" in _log_text(harness)
    ), _log_text(harness)
    assert procs == []
    assert harness.app.operations.current is OperationKind.IDLE


_LONG_BOOT_CMD = (
    f"{sys.executable} -c \"print('booting', flush=True); import time; time.sleep(30)\""
)


async def test_cold_start_long_running_boot_keeps_ui_live(
    stub_harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A serving start_cmd never exits; the boot must finish regardless."""
    harness = stub_harness
    monkeypatch.setattr(MlxTuiApp, "_classify_liveness", _always_red)
    await harness.app._poll()
    harness.app.config = AppConfig(
        model=ROW.repo_id,
        start_cmd=_LONG_BOOT_CMD,
    )
    monkeypatch.setattr(harness.app, "effective_model", lambda: ROW.repo_id)
    procs: list[subprocess.Popen[str]] = []
    orig_spawn = serverctl.spawn_command

    def recording_spawn(cmd, *, on_line, shell=False, env=None):  # type: ignore[no-untyped-def]
        proc = orig_spawn(cmd, on_line=on_line, shell=shell, env=env)
        procs.append(proc)
        return proc

    monkeypatch.setattr(serverctl, "spawn_command", recording_spawn)

    try:
        await harness.pilot.press("ctrl+s")

        assert await harness.wait_for(
            lambda a: "✓ server is up" in _log_text(harness)
        ), _log_text(harness)
        lines = _log_text(harness)
        assert "[swap] booting" in lines
        assert harness.app.operations.current is OperationKind.IDLE
        assert harness.app.server_identity.model_id == ROW.repo_id
        assert not harness.app.query_one("#chat-input", Input).disabled
        assert not harness.app.query_one("#models-table", ModelsTable).disabled
    finally:
        for proc in procs:
            proc.terminate()
            proc.wait(timeout=5)


async def test_cold_start_instant_crash_fails_fast(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    monkeypatch.setattr(MlxTuiApp, "_classify_liveness", _always_red)
    await harness.app._poll()
    harness.app.config = AppConfig(
        start_cmd=f"{sys.executable} -c 'raise SystemExit(3)'"
    )

    await harness.pilot.press("ctrl+s")

    assert await harness.wait_for(
        lambda a: "[swap] start_cmd exited 3" in _log_text(harness)
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE
    assert not harness.app.query_one("#chat-input", Input).disabled


async def test_cold_start_reports_mid_boot_death(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A foreground server dying mid-boot ends the wait with its exit code."""
    harness = stub_harness
    monkeypatch.setattr(MlxTuiApp, "_classify_liveness", _always_red)
    harness.server.model_id = "other-model"
    await harness.app._poll()
    harness.app.config = AppConfig(
        model=ROW.repo_id,
        start_cmd=(
            f'{sys.executable} -c "import time; time.sleep(3); raise SystemExit(1)"'
        ),
    )
    procs: list[subprocess.Popen[str]] = []
    orig_spawn = serverctl.spawn_command

    def recording_spawn(cmd, *, on_line, shell=False, env=None):  # type: ignore[no-untyped-def]
        proc = orig_spawn(cmd, on_line=on_line, shell=shell, env=env)
        procs.append(proc)
        return proc

    monkeypatch.setattr(serverctl, "spawn_command", recording_spawn)

    try:
        await harness.pilot.press("ctrl+s")

        assert await harness.wait_for(
            lambda a: "[swap] start_cmd exited 1" in _log_text(harness),
            attempts=500,
        ), _log_text(harness)
        assert harness.app.operations.current is OperationKind.IDLE
        assert not harness.app.query_one("#chat-input", Input).disabled
    finally:
        for proc in procs:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass


async def test_cold_start_timeout_scales_with_model_size(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold boot reuses the scanned row's size for its health deadline."""
    harness = stub_harness
    monkeypatch.setattr(MlxTuiApp, "_classify_liveness", _always_red)
    await harness.app._poll()
    big_row = ModelRow("mlx-community/big-model", 8 * 2**30, "4bit", False, ("def456",))
    harness.app.query_one(ModelsPane)._populate([ROW, big_row])
    harness.app.config = AppConfig(model=big_row.repo_id, start_cmd="true")
    timeouts: list[float] = []

    def fake_wait(  # noqa: PLR0913
        url: str,
        *,
        target_model: str | None = None,
        is_running: Callable[[], bool] | None = None,
        timeout_s: float,
        on_tick: Callable[[int], None] | None = None,
    ) -> ServerProbe | None:
        timeouts.append(timeout_s)
        return ServerProbe(state="green", model_id=target_model)

    def fake_spawn(cmd, *, on_line, shell=False, env=None):  # type: ignore[no-untyped-def]
        return subprocess.Popen([sys.executable, "-c", "pass"])

    monkeypatch.setattr(serverctl, "wait_healthy", fake_wait)
    monkeypatch.setattr(serverctl, "spawn_command", fake_spawn)

    await harness.pilot.press("ctrl+s")

    assert await harness.wait_for(lambda a: "✓ server is up" in _log_text(harness)), (
        _log_text(harness)
    )
    assert timeouts == [pytest.approx(health_timeout(8 * 2**30))]


async def test_warm_swap_completion_respects_live_turn(
    stub_harness: AppHarness,
) -> None:
    """A live chat owns the lease, so a warm swap cannot start alongside it."""
    harness = stub_harness
    await harness.app._poll()
    await _select_row(harness)

    inp = harness.app.query_one("#chat-input", Input)
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


@pytest.mark.parametrize(
    ("status", "policy", "has_start", "has_stop", "expected"),
    [
        ("amber", "auto", True, True, "refuse"),
        ("amber", "warm", False, False, "refuse"),
        ("amber", "restart", True, True, "refuse"),
        ("green", "auto", True, True, "restart"),
        ("green", "auto", False, False, "warm"),
        ("green", "auto", True, False, "warm"),
        ("green", "warm", False, False, "warm"),
        ("green", "warm", True, True, "warm"),
        ("green", "restart", True, True, "restart"),
        ("green", "restart", False, False, "refuse"),
        ("green", "restart", True, False, "refuse"),
        ("red", "auto", True, True, "restart"),
        ("red", "auto", True, False, "cold"),
        ("red", "auto", False, False, "refuse"),
        ("red", "warm", False, False, "refuse"),
        ("red", "restart", True, True, "restart"),
        ("red", "restart", True, False, "refuse"),
    ],
)
def test_resolve_swap_action_branches(
    status: str, policy: str, has_start: bool, has_stop: bool, expected: str
) -> None:
    assert resolve_swap_action(status, policy, has_start, has_stop) == expected


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
    await harness.app._poll()
    assert harness.app.server_identity.model_id == "old-model"

    def fake_warm(url: str, repo_id: str, *, timeout_s: float) -> WarmLoadResult:
        return WarmLoadResult(response_model="wrong-model")

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
            "load failed" in _log_text(harness) and "wrong-model" in _log_text(harness)
        ),
    ), _log_text(harness)
    assert called == []
    assert harness.app.operations.current is OperationKind.IDLE
    await harness.app._poll()
    assert harness.app.server_identity.model_id == "old-model"
    assert harness.app.server_identity.model_id != "wrong-model"


async def test_warm_wrong_endpoint_model_keeps_old_marker(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    harness.server.mode = "probe"
    harness.server.model_id = "old-model"
    await harness.app._poll()
    assert harness.app.server_identity.model_id == "old-model"

    def fake_warm(url: str, repo_id: str, *, timeout_s: float) -> WarmLoadResult:
        return WarmLoadResult(response_model=None)

    def fake_unhealthy(url: str, **kwargs: object) -> ServerProbe | None:
        return None

    monkeypatch.setattr(serverctl, "warm_load", fake_warm)
    monkeypatch.setattr(serverctl, "wait_healthy", fake_unhealthy)
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: (
            "load failed" in _log_text(harness) and ROW.repo_id in _log_text(harness)
        ),
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE
    assert harness.app.server_identity.model_id == "old-model"
    assert harness.app.effective_model() == "old-model"


async def test_invalid_start_config_runs_no_stop(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed start_cmd is rejected before stop_cmd ever runs."""
    harness = stub_harness
    harness.server.mode = "ok"
    harness.server.model_id = ROW.repo_id
    await harness.app._poll()
    harness.app.config = AppConfig(
        start_cmd='prog "unterminated',
        stop_cmd=f"{sys.executable} -c \"print('bye')\"",
    )
    stops: list[str] = []
    orig_run = serverctl.run_command

    def recording_run(cmd, *, on_line, timeout_s=30.0, shell=False, env=None):  # type: ignore[no-untyped-def]
        stops.append(str(cmd))
        return orig_run(cmd, on_line=on_line, timeout_s=timeout_s, shell=shell, env=env)

    monkeypatch.setattr(serverctl, "run_command", recording_run)
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: "invalid start_cmd" in _log_text(harness)
    ), _log_text(harness)
    assert stops == []
    assert "[swap] bye" not in _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE
    assert not harness.app.query_one("#chat-input", Input).disabled
    assert not harness.app.query_one("#models-table", ModelsTable).disabled


async def test_shell_mode_rejects_brace_placeholder(
    stub_harness: AppHarness,
) -> None:
    harness = stub_harness
    harness.server.mode = "ok"
    await harness.app._poll()
    harness.app.config = AppConfig(
        start_cmd="mlx_lm.server --model {model} --port 8080",
        stop_cmd=f"{sys.executable} -c \"print('bye')\"",
        command_shell=True,
    )
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(lambda a: "MLX_TUI_MODEL" in _log_text(harness)), (
        _log_text(harness)
    )
    assert "[swap] bye" not in _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE


async def test_shell_mode_requires_env_reference(
    stub_harness: AppHarness,
) -> None:
    harness = stub_harness
    harness.server.mode = "ok"
    await harness.app._poll()
    harness.app.config = AppConfig(
        start_cmd="mlx_lm.server --port 8080",
        stop_cmd=f"{sys.executable} -c \"print('bye')\"",
        command_shell=True,
    )
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: 'must reference "$MLX_TUI_MODEL"' in _log_text(harness)
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE


async def test_shell_mode_restart_completes(
    stub_harness: AppHarness,
) -> None:
    harness = stub_harness
    harness.server.mode = "ok"
    harness.server.model_id = ROW.repo_id
    await harness.app._poll()
    harness.app.config = AppConfig(
        start_cmd=f'{sys.executable} -c "print(\'$MLX_TUI_MODEL\')" --model "$MLX_TUI_MODEL"',
        stop_cmd=f"{sys.executable} -c \"print('bye')\"",
        command_shell=True,
    )
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: "✓ mlx-community/stub-test is serving" in _log_text(harness)
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE


async def test_failed_boot_terminates_proc_and_restores_controls(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Health failure kills the owned proc; UI is usable afterwards."""
    harness = stub_harness
    harness.server.mode = "ok"
    harness.server.model_id = "other-model"
    await harness.app._poll()
    harness.app.config = AppConfig(
        start_cmd=(
            f"{sys.executable} -c "
            "\"import time; print('booting', flush=True); time.sleep(30)\""
        ),
        stop_cmd=f"{sys.executable} -c \"print('bye')\"",
    )

    def fake_unhealthy(url: str, **kwargs: object) -> ServerProbe | None:
        return None

    monkeypatch.setattr(serverctl, "wait_healthy", fake_unhealthy)
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: "swap timed out" in _log_text(harness),
        attempts=500,
    ), _log_text(harness)
    assert harness.app.operations.current is OperationKind.IDLE
    assert not harness.app.query_one("#chat-input", Input).disabled
    assert not harness.app.query_one("#models-table", ModelsTable).disabled


async def test_rescan_failure_keeps_rows_and_logs_once(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    await harness.app._poll()
    await _select_row(harness)
    pane = harness.models_pane()

    def failing_scan(avail_gib: float | None) -> list[ModelRow]:
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


async def _press_delete_and_wait_confirm(harness: AppHarness) -> None:
    await harness.pilot.press("d")
    assert await harness.wait_for(lambda a: isinstance(a.screen, ConfirmScreen)), (
        f"confirm never opened; screen={harness.app.screen!r}"
    )


def _controls_restored(harness: AppHarness) -> bool:
    return (
        harness.app.operations.current is OperationKind.IDLE
        and not harness.app.query_one("#chat-input", Input).disabled
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


async def test_delete_refuses_active_model(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = stub_harness
    await harness.app._poll()
    assert harness.app.effective_model() == ROW.repo_id
    await _select_row(harness)
    called = False

    def fake_delete(hashes: tuple[str, ...]) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("mlx_tui.models_pane.delete_repos", fake_delete)
    await harness.pilot.press("d")
    assert await harness.wait_for(lambda a: "currently loaded" in _log_text(harness)), (
        _log_text(harness)
    )
    assert not isinstance(harness.app.screen, ConfirmScreen)
    assert not called
    assert _controls_restored(harness)


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
    # The loaded model changes between confirmation and worker execution.
    monkeypatch.setattr(harness.app, "effective_model", lambda: ROW.repo_id)
    await harness.pilot.press("y")
    assert await harness.wait_for(lambda a: "became active" in _log_text(harness)), (
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
