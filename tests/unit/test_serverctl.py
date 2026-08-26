"""Unit tests for stop/start commands, warm-load probe, and health waiting."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable

import httpx
import pytest

from mlx_tui import serverctl
from mlx_tui.serverctl import HealthWatch, build_start_command
from tests.unit.test_chat import install_transport

MODELS_URL = "http://stub/v1/models"


@pytest.mark.parametrize(
    ("start_cmd", "model_id", "expected"),
    [
        ("{model} --port 8080", "org/m-4bit", "org/m-4bit --port 8080"),
        ("{model}", None, ""),
        (
            "mlx_lm.server --port 8080",
            "org/m",
            "mlx_lm.server --port 8080 --model org/m",
        ),
        (
            "mlx_lm.server --port 8080",
            "or g/m",
            "mlx_lm.server --port 8080 --model 'or g/m'",
        ),
        ("s --model x", "org/m", "s --model x"),
        ("s", None, "s"),
    ],
)
def test_build_start_command(
    start_cmd: str, model_id: str | None, expected: str
) -> None:
    assert build_start_command(start_cmd, model_id) == expected


def _green_body() -> bytes:
    return b"""{
        "object": "list",
        "data": [{"id": "m", "object": "model", "created": 0}]
    }"""


def test_run_command_streams_merged_output() -> None:
    lines: list[str] = []
    # flush=True: piped stdout is block-buffered and would otherwise land
    # after the unbuffered stderr line, making the merged order unstable.
    cmd = f"{sys.executable} -c \"print('a', flush=True); import sys; print('e', file=sys.stderr)\""

    rc = serverctl.run_command(cmd, on_line=lines.append)

    assert rc == 0
    assert lines == ["a", "e"]


def test_run_command_failing_exit_code_does_not_raise() -> None:
    rc = serverctl.run_command("exit 3", on_line=lambda _line: None)

    assert rc == 3


def test_spawn_command_returns_before_exit_and_streams() -> None:
    lines: list[str] = []
    cmd = (
        f"{sys.executable} -c "
        "\"print('boot', flush=True); import time; time.sleep(10)\""
    )

    start = time.monotonic()
    proc = serverctl.spawn_command(cmd, on_line=lines.append)

    try:
        # A serving start_cmd never exits; spawning must not block on it.
        assert time.monotonic() - start < 5
        deadline = time.monotonic() + 5
        while not lines and time.monotonic() < deadline:
            time.sleep(0.01)
        assert lines == ["boot"]
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()


def test_spawn_with_grace_detects_instant_crash() -> None:
    proc, monitor = serverctl.spawn_with_grace(
        f'{sys.executable} -c "raise SystemExit(3)"',
        on_line=lambda _line: None,
        grace_s=1.0,
        poll_s=0.01,
    )

    assert monitor is False
    assert proc.returncode == 3


def test_spawn_with_grace_monitors_surviving_process() -> None:
    proc, monitor = serverctl.spawn_with_grace(
        f'{sys.executable} -c "import time; time.sleep(5)"',
        on_line=lambda _line: None,
        grace_s=0.05,
        poll_s=0.01,
    )

    try:
        assert monitor is True
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_wait_healthy_bails_when_spawned_command_dies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead start_cmd can never turn green — bail instead of burning time."""
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"data": [{"id": "m"}]}),
    )

    start = time.monotonic()
    ok = serverctl.wait_healthy(
        MODELS_URL,
        HealthWatch(
            target_model="m",
            current_model=lambda: "other",  # never green-matches
            is_running=lambda: False,
        ),
        timeout_s=30,
    )

    assert ok is False
    assert time.monotonic() - start < 5


def test_warm_load_sends_probe_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]},
        )

    install_transport(monkeypatch, handler)

    serverctl.warm_load(
        "http://stub/v1/chat/completions", "repo/a", timeout_s=5
    )

    payload = json.loads(requests[0].read())
    assert payload["model"] == "repo/a"
    assert payload["max_tokens"] == 1
    assert "stream" not in payload


def test_warm_load_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(500, json={"detail": "nope"}),
    )

    with pytest.raises(httpx.HTTPStatusError):
        serverctl.warm_load("http://stub/x", "repo/a", timeout_s=5)


def _not_json(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=b"not-json{")


def _empty_choices(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": []})


def _odd_shape(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"detail": "odd shape"})


@pytest.mark.parametrize(
    "handler",
    [_not_json, _empty_choices, _odd_shape],
)
def test_warm_load_unexpected_body_raises_runtimeerror(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    install_transport(monkeypatch, handler)

    with pytest.raises(RuntimeError):
        serverctl.warm_load("http://stub/x", "repo/a", timeout_s=5)


def test_warm_load_connect_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    install_transport(monkeypatch, boom)

    with pytest.raises(httpx.ConnectError):
        serverctl.warm_load("http://stub/x", "repo/a", timeout_s=5)


def test_wait_healthy_green_first_poll_with_matching_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"data": [{"id": "m"}]}),
    )

    ok = serverctl.wait_healthy(
        MODELS_URL,
        HealthWatch(target_model="m", current_model=lambda: "m"),
        timeout_s=5,
    )

    assert ok is True


def test_wait_healthy_wrong_model_then_right(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"data": [{"id": "m"}]}),
    )
    calls = 0

    def flipper() -> str | None:
        nonlocal calls
        calls += 1
        return "other" if calls <= 2 else "m"

    ok = serverctl.wait_healthy(
        MODELS_URL,
        HealthWatch(target_model="m", current_model=flipper),
        timeout_s=10,
    )

    assert ok is True
    assert calls >= 3


def test_wait_healthy_times_out_when_always_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(502, text="<html>proxy</html>"),
    )
    ticks: list[int] = []
    start = time.monotonic()

    ok = serverctl.wait_healthy(
        MODELS_URL,
        HealthWatch(target_model=None, current_model=lambda: "m"),
        timeout_s=1,
        on_tick=ticks.append,
    )

    assert ok is False
    assert time.monotonic() - start >= 0.9
    assert ticks  # progress ticks were emitted while waiting


def test_wait_healthy_target_none_accepts_any_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(200, json={"data": [{"id": "someone"}]}),
    )

    ok = serverctl.wait_healthy(
        MODELS_URL,
        HealthWatch(target_model=None, current_model=lambda: "never-matches"),
        timeout_s=5,
    )

    assert ok is True
