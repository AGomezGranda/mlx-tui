"""Cold-start, restart, command validation, and boot cleanup tests."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable

import pytest

from mlx_tui import serverctl
from mlx_tui.app import MlxTuiApp
from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.config import AppConfig
from mlx_tui.models import ModelRow
from mlx_tui.models_pane import ModelsPane
from mlx_tui.operations import OperationKind
from mlx_tui.status import ServerProbe
from mlx_tui.swap import BootPlan, health_timeout
from mlx_tui.table import ModelsTable
from tests.conftest import AppHarness
from tests.integration.model_helpers import (
    ROW,
    _fake_process_4242,
    _log_text,
    _select_row,
)

_LONG_BOOT_CMD = (
    f"{sys.executable} -c \"print('booting', flush=True); import time; time.sleep(30)\""
)


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
        lambda a: (
            "✓ request succeeded for mlx-community/stub-test" in _log_text(harness)
        )
    ), _log_text(harness)
    lines = _log_text(harness)
    assert "[swap] bye" in lines
    assert "[swap] booting" in lines
    assert harness.app.operations.current is OperationKind.IDLE
    await harness.app._poll()
    assert harness.app.server_identity.last_response_model == ROW.repo_id


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

    assert await harness.wait_for(
        lambda a: "✓ generation verified" in _log_text(harness)
    ), _log_text(harness)
    flat_log = " ".join(_log_text(harness).split())
    assert "[swap] starting:" in flat_log
    assert "--model" in flat_log
    assert harness.app.operations.current is OperationKind.IDLE
    assert harness.app.server_identity.last_response_model == ROW.repo_id
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

    assert await harness.wait_for(
        lambda a: "✓ generation verified" in _log_text(harness)
    ), _log_text(harness)
    lines = _log_text(harness)
    assert "[swap] bye" in lines, lines
    assert "[swap] booting" in lines, lines
    assert procs, "restart path must spawn start_cmd"
    assert harness.app.operations.current is OperationKind.IDLE
    await harness.app._poll()
    assert harness.app.server_identity.last_response_model == ROW.repo_id


async def test_cold_start_with_process_but_no_stop_cmd_guides(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without stop_cmd an existing process can only be reported, not raced."""
    harness = stub_harness
    harness.server.mode = "html"
    await harness.app._poll()
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
            lambda a: "✓ generation verified" in _log_text(harness)
        ), _log_text(harness)
        lines = _log_text(harness)
        assert "[swap] booting" in lines
        assert harness.app.operations.current is OperationKind.IDLE
        assert harness.app.server_identity.last_response_model == ROW.repo_id
        assert not harness.app.query_one("#chat-input", ChatInput).disabled
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
    assert not harness.app.query_one("#chat-input", ChatInput).disabled


async def test_cold_start_reports_mid_boot_death(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A foreground server dying mid-boot ends the wait with its exit code."""
    harness = stub_harness
    monkeypatch.setattr(MlxTuiApp, "_classify_liveness", _always_red)
    harness.server.model_id = "other-model"
    await harness.app._poll()
    harness.server.mode = "error500"
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
        assert not harness.app.query_one("#chat-input", ChatInput).disabled
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
    big_row = ModelRow("mlx-community/big-model", 8 * 2**30, "4bit", ("def456",))
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

    assert await harness.wait_for(
        lambda a: "✓ generation verified" in _log_text(harness)
    ), _log_text(harness)
    assert timeouts == [pytest.approx(health_timeout(8 * 2**30))]


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
    assert not harness.app.query_one("#chat-input", ChatInput).disabled
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
        lambda a: (
            "✓ request succeeded for mlx-community/stub-test" in _log_text(harness)
        )
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
    assert not harness.app.query_one("#chat-input", ChatInput).disabled
    assert not harness.app.query_one("#models-table", ModelsTable).disabled


async def test_verified_boot_ui_failure_does_not_terminate_server(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-health refresh failure cannot reclaim the verified server."""
    harness = stub_harness
    monkeypatch.setattr(MlxTuiApp, "_classify_liveness", _always_red)
    harness.app.config = AppConfig(model=ROW.repo_id, start_cmd="server")

    def fake_execute(  # noqa: PLR0913
        plan: BootPlan,
        config: AppConfig,
        *,
        host: str,
        port: int,
        on_line: Callable[[str], None],
        on_tick: Callable[[int], None],
    ) -> ServerProbe:
        on_line("booting")
        on_tick(1)
        return ServerProbe(state="green", model_id=plan.model_id)

    terminated: list[object] = []

    def sync_call_from_thread(
        callback: Callable[..., object], *args: object, **kwargs: object
    ) -> object:
        if getattr(callback, "__name__", "") == "refresh_models":
            raise RuntimeError("refresh failed")
        return callback(*args, **kwargs)

    monkeypatch.setattr("mlx_tui.models_pane.execute_boot", fake_execute)
    monkeypatch.setattr(serverctl, "terminate_failed_process", terminated.append)
    monkeypatch.setattr(harness.app, "call_from_thread", sync_call_from_thread)

    await harness.pilot.press("ctrl+s")

    assert await harness.wait_for(
        lambda a: "UI update failed: refresh failed" in _log_text(harness)
    ), _log_text(harness)
    assert terminated == []
    assert harness.app.operations.current is OperationKind.IDLE
    assert not harness.app.query_one("#chat-input", ChatInput).disabled
    assert not harness.app.query_one("#models-table", ModelsTable).disabled
