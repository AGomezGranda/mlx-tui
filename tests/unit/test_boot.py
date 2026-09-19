"""Unit tests for boot command preparation and the verified-health boundary."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mlx_tui import boot
from mlx_tui.config import AppConfig
from mlx_tui.status import ServerProbe
from mlx_tui.swap import BootPlan


@dataclass
class FakeProcess:
    """Small process seam with a stable poll result for boot tests."""

    poll_result: int | None
    pid: int = 4242

    @property
    def returncode(self) -> int | None:
        return self.poll_result

    def poll(self) -> int | None:
        return self.poll_result


def _plan(*, stop_first: bool = False) -> BootPlan:
    return BootPlan(
        model_id="org/model",
        size_on_disk=0,
        stop_first=stop_first,
        success_line="✓ serving",
    )


def test_default_server_command_uses_app_interpreter_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def installed(_name: str) -> object:
        return object()

    monkeypatch.setattr(boot, "find_spec", installed)
    start, _, shell, _, _ = boot.prepare_commands(
        AppConfig(start_cmd="mlx_lm.server --port 8080"), _plan()
    )
    assert start == [
        boot.sys.executable,
        "-m",
        "mlx_lm.server",
        "--port",
        "8080",
        "--model",
        "org/model",
    ]
    assert not shell


def test_custom_server_command_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    def installed(_name: str) -> object:
        return object()

    monkeypatch.setattr(boot, "find_spec", installed)
    start, _, _, _, _ = boot.prepare_commands(
        AppConfig(start_cmd="/opt/server/mlx_lm.server --port 8080"), _plan()
    )
    assert start[0] == "/opt/server/mlx_lm.server"


def test_missing_server_command_has_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("missing")

    monkeypatch.setattr(boot.serverctl, "spawn_with_grace", missing)
    with pytest.raises(RuntimeError, match="server command not found: missing-server"):
        boot.execute_boot(
            _plan(),
            AppConfig(start_cmd="missing-server"),
            host="127.0.0.1",
            port=8080,
            on_line=lambda _line: None,
            on_tick=lambda _seconds: None,
        )


def test_invalid_start_is_rejected_before_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    stops: list[object] = []

    def fake_stop(*args: object, **kwargs: object) -> int:
        stops.append((args, kwargs))
        return 0

    monkeypatch.setattr(boot.serverctl, "run_command", fake_stop)

    with pytest.raises(RuntimeError, match="invalid start_cmd"):
        boot.execute_boot(
            _plan(stop_first=True),
            AppConfig(start_cmd='prog "unterminated', stop_cmd="stop"),
            host="127.0.0.1",
            port=8080,
            on_line=lambda _line: None,
            on_tick=lambda _seconds: None,
        )

    assert stops == []


def test_invalid_stop_is_rejected_before_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    stops: list[object] = []

    def fake_stop(*args: object, **kwargs: object) -> int:
        stops.append((args, kwargs))
        return 0

    monkeypatch.setattr(boot.serverctl, "run_command", fake_stop)

    with pytest.raises(RuntimeError, match="invalid stop_cmd"):
        boot.execute_boot(
            _plan(stop_first=True),
            AppConfig(start_cmd="server", stop_cmd='stop "unterminated'),
            host="127.0.0.1",
            port=8080,
            on_line=lambda _line: None,
            on_tick=lambda _seconds: None,
        )

    assert stops == []


def test_failed_stop_does_not_start_server(monkeypatch: pytest.MonkeyPatch) -> None:
    starts: list[object] = []

    def fake_stop(*args: object, **kwargs: object) -> int:
        return 3

    def fake_start(*args: object, **kwargs: object) -> tuple[FakeProcess, bool]:
        starts.append((args, kwargs))
        raise AssertionError("start must not run after a failed stop")

    monkeypatch.setattr(boot.serverctl, "run_command", fake_stop)
    monkeypatch.setattr(boot.serverctl, "spawn_with_grace", fake_start)

    with pytest.raises(RuntimeError, match="stop_cmd exited 3"):
        boot.execute_boot(
            _plan(stop_first=True),
            AppConfig(start_cmd="server", stop_cmd="stop"),
            host="127.0.0.1",
            port=8080,
            on_line=lambda _line: None,
            on_tick=lambda _seconds: None,
        )

    assert starts == []


def test_early_crash_cleans_owned_process(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProcess(3)
    cleaned: list[object] = []

    def fake_start(*args: object, **kwargs: object) -> tuple[FakeProcess, bool]:
        return proc, False

    monkeypatch.setattr(boot.serverctl, "spawn_with_grace", fake_start)
    monkeypatch.setattr(boot.serverctl, "terminate_failed_process", cleaned.append)

    with pytest.raises(RuntimeError, match="start_cmd exited 3"):
        boot.execute_boot(
            _plan(),
            AppConfig(start_cmd="server"),
            host="127.0.0.1",
            port=8080,
            on_line=lambda _line: None,
            on_tick=lambda _seconds: None,
        )

    assert cleaned == [proc]


def test_health_failure_cleans_owned_process(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProcess(None)
    cleaned: list[object] = []

    def fake_start(*args: object, **kwargs: object) -> tuple[FakeProcess, bool]:
        return proc, True

    def fake_wait(*args: object, **kwargs: object) -> ServerProbe | None:
        return None

    monkeypatch.setattr(boot.serverctl, "spawn_with_grace", fake_start)
    monkeypatch.setattr(boot.serverctl, "wait_healthy", fake_wait)
    monkeypatch.setattr(boot.serverctl, "terminate_failed_process", cleaned.append)

    with pytest.raises(RuntimeError, match="server did not come up"):
        boot.execute_boot(
            _plan(),
            AppConfig(start_cmd="server"),
            host="127.0.0.1",
            port=8080,
            on_line=lambda _line: None,
            on_tick=lambda _seconds: None,
        )

    assert cleaned == [proc]


def test_verified_health_does_not_clean_up(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProcess(None)
    cleaned: list[object] = []
    probe = ServerProbe(state="green", model_id="org/model")

    def fake_start(*args: object, **kwargs: object) -> tuple[FakeProcess, bool]:
        return proc, True

    def fake_wait(*args: object, **kwargs: object) -> ServerProbe:
        return probe

    monkeypatch.setattr(boot.serverctl, "spawn_with_grace", fake_start)
    monkeypatch.setattr(boot.serverctl, "wait_healthy", fake_wait)
    monkeypatch.setattr(boot.serverctl, "terminate_failed_process", cleaned.append)

    result = boot.execute_boot(
        _plan(),
        AppConfig(start_cmd="server"),
        host="127.0.0.1",
        port=8080,
        on_line=lambda _line: None,
        on_tick=lambda _seconds: None,
    )

    assert result == probe
    assert cleaned == []


def test_observational_callbacks_cannot_kill_successful_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = FakeProcess(None)
    cleaned: list[object] = []
    probe = ServerProbe(state="green", model_id="org/model")

    def fake_stop(*args: object, **kwargs: object) -> int:
        kwargs["on_line"]("stopping")  # type: ignore[operator]
        return 0

    def fake_start(*args: object, **kwargs: object) -> tuple[FakeProcess, bool]:
        kwargs["on_line"]("starting")  # type: ignore[operator]
        return proc, True

    def fake_wait(*args: object, **kwargs: object) -> ServerProbe:
        kwargs["on_tick"](1)  # type: ignore[operator]
        return probe

    monkeypatch.setattr(boot.serverctl, "run_command", fake_stop)
    monkeypatch.setattr(boot.serverctl, "spawn_with_grace", fake_start)
    monkeypatch.setattr(boot.serverctl, "wait_healthy", fake_wait)
    monkeypatch.setattr(boot.serverctl, "terminate_failed_process", cleaned.append)

    def fail(_value: object) -> None:
        raise RuntimeError("diagnostic sink failed")

    result = boot.execute_boot(
        _plan(stop_first=True),
        AppConfig(start_cmd="server", stop_cmd="stop"),
        host="127.0.0.1",
        port=8080,
        on_line=fail,
        on_tick=fail,
    )

    assert result == probe
    assert cleaned == []
