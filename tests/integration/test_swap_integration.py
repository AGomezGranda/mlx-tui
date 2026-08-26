"""Integration tests for the hybrid load/swap orchestration."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from typing import override

import pytest
from textual.widgets import Input, TabbedContent

from mlx_tui.app import MlxTuiApp
from mlx_tui.config import AppConfig
from mlx_tui.models import ModelRow
from mlx_tui.models_pane import ModelsPane
from mlx_tui.process import ServerProcessFinder
from mlx_tui.serverctl import HealthWatch, ServerController
from mlx_tui.swap import SwapState, health_timeout
from mlx_tui.table import ModelsTable
from tests.conftest import AppHarness, StubServer

ROW = ModelRow("mlx-community/stub-test", 1_000_000_000, "4bit", True, ("abc123",))


def _stub_rows(avail_gib: float | None) -> list[ModelRow]:
    return [ROW]


class _StaticFinder(ServerProcessFinder):
    """Process discovery pinned to a fixed answer (or none)."""

    def __init__(self, pid: int | None) -> None:
        super().__init__()
        self._pid = pid

    @override
    def find(self, pidfile: str | None = None) -> int | None:
        return self._pid


@pytest.fixture
async def stub_harness(
    stub_server_factory: Callable[[str], StubServer],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AppHarness]:
    """Harness whose cache always scans to ROW.

    The patch must precede app mount: a mount-time rescan against the real
    HF cache is a thread that exclusive-cancellation cannot stop, and its
    late ``_populate_table`` would otherwise overwrite the stub rows.
    Process discovery is neutralized too — a real mlx server running on the
    dev machine must not flip cold-start into its restart branch.
    """
    monkeypatch.setattr("mlx_tui.models_pane.scan_models", _stub_rows)
    server = stub_server_factory("ok")
    app = MlxTuiApp(host="127.0.0.1", port=int(server.server_address[1]))
    app._process_finder = _StaticFinder(None)
    async with app.run_test() as pilot:
        yield AppHarness(app=app, pilot=pilot, server=server)


async def _select_row(harness: AppHarness) -> None:
    """Switch to the Models tab, wait for the stub row, focus the table."""
    # Activating the already-active tab fires no TabActivated, so kick a
    # rescan explicitly to mirror the user flow.
    harness.app.query_one(TabbedContent).active = "models"
    harness.app.query_one(ModelsPane).rescan()
    assert await harness.wait_for(lambda a: a.query_one(ModelsPane).rows == [ROW])
    # Tab activation does not guarantee keyboard focus lands on the table;
    # focus it explicitly so `enter` hits action_load_swap deterministically.
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
    assert harness.app.swap_machine.state is SwapState.IDLE
    assert harness.app._tracked_model == "mlx-community/stub-test"


async def test_swap_blocked_while_busy(stub_harness: AppHarness) -> None:
    harness = stub_harness
    await harness.app._poll()
    harness.app.swap_machine.transition(SwapState.WAITING_HEALTH)
    await _select_row(harness)

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: "swap already in progress" in _log_text(harness)
    ), _log_text(harness)
    assert harness.app.swap_machine.state is SwapState.WAITING_HEALTH


async def test_restart_swap_streams_and_completes(stub_harness: AppHarness) -> None:
    harness = stub_harness
    # Non-green status forces the restart branch (green would take warm).
    harness.server.mode = "html"
    await harness.app._poll()
    harness.app.config = AppConfig(
        start_cmd=f"{sys.executable} -c \"print('booting')\"",
        stop_cmd=f"{sys.executable} -c \"print('bye')\"",
    )
    harness.app.current_model_supplier = lambda: ROW.repo_id
    await _select_row(harness)

    await harness.pilot.press("enter")
    # The server "comes back" only after dispatch so the health wait succeeds.
    harness.server.mode = "ok"

    assert await harness.wait_for(
        lambda a: "✓ mlx-community/stub-test is serving" in _log_text(harness)
    ), _log_text(harness)
    lines = _log_text(harness)
    assert "[swap] bye" in lines
    assert "[swap] booting" in lines
    assert harness.app.swap_machine.state is SwapState.IDLE
    assert harness.app._tracked_model == ROW.repo_id


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
    # Pin liveness red deterministically: assigning status_state alone would
    # race the 2s poll, which recomputes green against the ok-mode stub.
    monkeypatch.setattr(MlxTuiApp, "_classify_liveness", _always_red)
    await harness.app._poll()
    harness.app.config = AppConfig(
        model="mlx-community/stub-test",
        start_cmd=f"{sys.executable} -c \"print('starting')\"",
    )
    harness.app.current_model_supplier = lambda: ROW.repo_id

    await harness.pilot.press("ctrl+s")

    assert await harness.wait_for(lambda a: "✓ server is up" in _log_text(harness)), (
        _log_text(harness)
    )
    # RichLog wraps long lines, so match against the flattened log.
    flat_log = " ".join(_log_text(harness).split())
    assert "[swap] starting:" in flat_log
    assert "--model" in flat_log
    assert harness.app.swap_machine.state is SwapState.IDLE
    assert harness.app._tracked_model == ROW.repo_id


async def test_double_cold_start_is_guarded(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second ctrl+s inside a running boot must log, not crash the app."""
    harness = stub_harness
    monkeypatch.setattr(MlxTuiApp, "_classify_liveness", _always_red)
    await harness.app._poll()
    # The sleeping start_cmd holds the boot busy so both presses land inside it.
    harness.app.config = AppConfig(start_cmd="sleep 0.5")

    await harness.pilot.press("ctrl+s")
    await harness.pilot.press("ctrl+s")

    assert await harness.wait_for(
        lambda a: "swap already in progress" in _log_text(harness)
    ), _log_text(harness)
    assert harness.app.swap_machine.state is SwapState.STARTING


async def test_cold_start_when_server_already_up_never_spawns(
    stub_harness: AppHarness,
) -> None:
    """ctrl+s during the stale startup-red window must not spawn a duplicate."""
    harness = stub_harness
    # No _poll: status_state still carries its initial "red" while the stub
    # server is in fact up — exactly the enter-the-app-and-start flow.
    recorder = _SpawnRecorder()
    harness.app.server_ctl = recorder
    harness.app.config = AppConfig(model=ROW.repo_id, start_cmd="true")

    await harness.pilot.press("ctrl+s")

    assert await harness.wait_for(
        lambda a: "server is already up" in _log_text(harness)
    ), _log_text(harness)
    assert recorder.procs == []
    assert harness.app.swap_machine.state is SwapState.IDLE


async def test_cold_start_with_unhealthy_process_restarts_it(
    stub_harness: AppHarness,
) -> None:
    """An existing mlx process holding the port is stopped first, not raced."""
    harness = stub_harness
    harness.server.mode = "html"
    await harness.app._poll()
    harness.app._process_finder = _StaticFinder(4242)
    harness.app.config = AppConfig(
        model=ROW.repo_id,
        start_cmd=f"{sys.executable} -c \"print('booting')\"",
        stop_cmd=f"{sys.executable} -c \"print('bye')\"",
    )
    harness.app.current_model_supplier = lambda: ROW.repo_id
    ctl = _SpawnRecorder()
    harness.app.server_ctl = ctl

    await harness.pilot.press("ctrl+s")
    # The server "comes back" only after dispatch so the health wait succeeds.
    harness.server.mode = "ok"

    assert await harness.wait_for(lambda a: "✓ server is up" in _log_text(harness)), (
        _log_text(harness)
    )
    lines = _log_text(harness)
    assert "[swap] bye" in lines, lines
    assert "[swap] booting" in lines, lines
    assert ctl.procs, "restart path must spawn start_cmd"
    assert harness.app.swap_machine.state is SwapState.IDLE
    assert harness.app._tracked_model == ROW.repo_id


async def test_cold_start_with_process_but_no_stop_cmd_guides(
    stub_harness: AppHarness,
) -> None:
    """Without stop_cmd an existing process can only be reported, not raced."""
    harness = stub_harness
    harness.server.mode = "html"
    await harness.app._poll()
    harness.app._process_finder = _StaticFinder(4242)
    harness.app.config = AppConfig(start_cmd="true")
    recorder = _SpawnRecorder()
    harness.app.server_ctl = recorder

    await harness.pilot.press("ctrl+s")

    assert await harness.wait_for(
        lambda a: "running but not healthy" in _log_text(harness)
    ), _log_text(harness)
    assert recorder.procs == []
    assert harness.app.swap_machine.state is SwapState.IDLE


class _SpawnRecorder(ServerController):
    """Records spawned processes so tests can reap them."""

    def __init__(self) -> None:
        super().__init__()
        self.procs: list[subprocess.Popen[str]] = []

    @override
    def spawn_command(
        self, cmd: str, *, on_line: Callable[[str], None]
    ) -> subprocess.Popen[str]:
        proc = super().spawn_command(cmd, on_line=on_line)
        self.procs.append(proc)
        return proc


_LONG_BOOT_CMD = (
    f"{sys.executable} -c \"print('booting', flush=True); import time; time.sleep(30)\""
)


async def test_cold_start_long_running_boot_keeps_ui_live(
    stub_harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A serving start_cmd never exits; the boot must finish regardless."""
    harness = stub_harness
    # Pin liveness red deterministically so ctrl+s takes the cold-start path;
    # wait_healthy itself talks to the ok-mode stub and turns green.
    monkeypatch.setattr(MlxTuiApp, "_classify_liveness", _always_red)
    await harness.app._poll()
    harness.app.config = AppConfig(
        model=ROW.repo_id,
        start_cmd=_LONG_BOOT_CMD,
    )
    harness.app.current_model_supplier = lambda: ROW.repo_id
    ctl = _SpawnRecorder()
    harness.app.server_ctl = ctl

    try:
        await harness.pilot.press("ctrl+s")

        assert await harness.wait_for(
            lambda a: "✓ server is up" in _log_text(harness)
        ), _log_text(harness)
        lines = _log_text(harness)
        # Output streamed from the still-running command.
        assert "[swap] booting" in lines
        assert harness.app.swap_machine.state is SwapState.IDLE
        assert harness.app._tracked_model == ROW.repo_id
        # The swap must hand the UI back once health is confirmed.
        assert not harness.app.query_one("#chat-input", Input).disabled
        assert not harness.app.query_one("#models-table", ModelsTable).disabled
    finally:
        for proc in ctl.procs:
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
    assert harness.app.swap_machine.state is SwapState.IDLE
    assert not harness.app.query_one("#chat-input", Input).disabled


async def test_cold_start_reports_mid_boot_death(
    stub_harness: AppHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A foreground server dying mid-boot ends the wait with its exit code."""
    harness = stub_harness
    monkeypatch.setattr(MlxTuiApp, "_classify_liveness", _always_red)
    await harness.app._poll()
    harness.app.config = AppConfig(
        model=ROW.repo_id,
        start_cmd=(
            f'{sys.executable} -c "import time; time.sleep(3); raise SystemExit(1)"'
        ),
    )
    # The model never matches, so only the death check can end the wait.
    harness.app.current_model_supplier = lambda: None
    ctl = _SpawnRecorder()
    harness.app.server_ctl = ctl

    try:
        await harness.pilot.press("ctrl+s")

        assert await harness.wait_for(
            lambda a: "[swap] start_cmd exited 1" in _log_text(harness),
            attempts=500,
        ), _log_text(harness)
        assert harness.app.swap_machine.state is SwapState.IDLE
        assert not harness.app.query_one("#chat-input", Input).disabled
    finally:
        for proc in ctl.procs:
            proc.terminate()
            proc.wait(timeout=5)


class _RecordingController(ServerController):
    """Stands in for ServerController, recording the health deadline."""

    def __init__(self) -> None:
        super().__init__()
        self.timeouts: list[float] = []

    @override
    def spawn_command(
        self, cmd: str, *, on_line: Callable[[str], None]
    ) -> subprocess.Popen[str]:
        # A finished no-op process: rc 0, already detached.
        return subprocess.Popen([sys.executable, "-c", "pass"])

    @override
    def wait_healthy(
        self,
        url: str,
        watch: HealthWatch,
        *,
        timeout_s: float,
        on_tick: Callable[[int], None] | None = None,
    ) -> bool:
        self.timeouts.append(timeout_s)
        return True


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
    ctl = _RecordingController()
    harness.app.server_ctl = ctl

    await harness.pilot.press("ctrl+s")

    assert await harness.wait_for(lambda a: "✓ server is up" in _log_text(harness)), (
        _log_text(harness)
    )
    assert ctl.timeouts == [pytest.approx(health_timeout(8 * 2**30))]


async def test_warm_swap_completion_respects_live_turn(
    stub_harness: AppHarness,
) -> None:
    """A warm swap finishing mid-turn must not hand the input back early."""
    harness = stub_harness
    harness.server.mode = "probe"
    await harness.app._poll()
    await _select_row(harness)

    # Simulate a live chat turn: stream open, input owned by the turn.
    inp = harness.app.query_one("#chat-input", Input)
    harness.chat_pane()._chat.active_response = object()  # type: ignore[assignment]

    await harness.pilot.press("enter")

    assert await harness.wait_for(
        lambda a: "✓ mlx-community/stub-test loaded" in _log_text(harness)
    ), _log_text(harness)
    assert inp.disabled, "warm swap completion re-enabled the input mid-turn"

    # The turn's own teardown (after the swap is done) restores the input.
    harness.chat_pane()._chat.active_response = None
    harness.chat_pane().end_turn()
    assert not inp.disabled
