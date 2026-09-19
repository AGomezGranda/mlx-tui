"""Unit tests for stop/start commands, warm-load probe, and health waiting."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

import httpx
import psutil
import pytest

from mlx_tui import serverctl
from mlx_tui.process import ProcessIdentity
from mlx_tui.serverctl import build_start_command
from tests.unit.test_chat import install_transport

MODELS_URL = "http://stub/v1/models"


@pytest.mark.parametrize(
    ("start_cmd", "model_id", "expected"),
    [
        ("{model} --port 8080", "org/m-4bit", ["org/m-4bit", "--port", "8080"]),
        (
            "mlx_lm.server --port 8080",
            "org/m",
            ["mlx_lm.server", "--port", "8080", "--model", "org/m"],
        ),
        (
            "mlx_lm.server --port 8080",
            "or g/m",
            ["mlx_lm.server", "--port", "8080", "--model", "or g/m"],
        ),
        (
            "s --model x",
            "org/m",
            ["s", "--model", "org/m"],
        ),
        (
            "s --model=x",
            "org/m",
            ["s", "--model=org/m"],
        ),
        (
            "s --model x --model y",
            "org/m",
            ["s", "--model", "org/m"],
        ),
        (
            "s --model=x --model=y",
            "org/m",
            ["s", "--model=org/m"],
        ),
        (
            "s --model x --model=y",
            "org/m",
            ["s", "--model", "org/m"],
        ),
        (
            "s --model-path p",
            "org/m",
            ["s", "--model-path", "p", "--model", "org/m"],
        ),
        ("s --model x", None, ["s", "--model", "x"]),
        ("s --model=x", None, ["s", "--model=x"]),
        ("s", None, ["s"]),
        (
            "prog --model {model}",
            "org/m",
            ["prog", "--model", "org/m"],
        ),
        (
            "prog --model={model}",
            "org/m",
            ["prog", "--model=org/m"],
        ),
    ],
)
def test_build_start_command(
    start_cmd: str, model_id: str | None, expected: list[str]
) -> None:
    assert build_start_command(start_cmd, model_id) == expected


def test_build_start_command_spaces_stay_literal() -> None:
    assert build_start_command("prog --port 8080", "or g/m; rm") == [
        "prog",
        "--port",
        "8080",
        "--model",
        "or g/m; rm",
    ]
    assert build_start_command("prog {model}", "a; rm -rf") == ["prog", "a; rm -rf"]
    assert build_start_command("prog {model}", "$(rm -rf)") == ["prog", "$(rm -rf)"]


def test_build_start_command_semicolon_is_literal_data() -> None:
    argv = build_start_command("prog --port 8080; rm -rf", "m")
    assert argv[0] == "prog"
    assert "rm" in argv
    # No shell splitting: the semicolon stays attached as argument data.
    assert any(";" in arg for arg in argv)


@pytest.mark.parametrize(
    "start_cmd",
    ["", "   ", "prog --model", "--model"],
)
def test_build_start_command_rejects_empty_and_dangling(start_cmd: str) -> None:
    with pytest.raises(ValueError):
        build_start_command(start_cmd, "org/m")


def test_build_start_command_rejects_dangling_without_target() -> None:
    with pytest.raises(ValueError):
        build_start_command("prog --model", None)


def test_build_start_command_rejects_placeholder_without_target() -> None:
    with pytest.raises(ValueError):
        build_start_command("{model}", None)
    with pytest.raises(ValueError):
        build_start_command("prog {model}", None)


def test_build_start_command_rejects_malformed_quoting() -> None:
    with pytest.raises(ValueError):
        build_start_command('prog "unterminated', "m")


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
    rc = serverctl.run_command(
        [sys.executable, "-c", "raise SystemExit(3)"],
        on_line=lambda _line: None,
    )

    assert rc == 3


def test_run_command_shell_mode_explicit() -> None:
    rc = serverctl.run_command("exit 3", on_line=lambda _line: None, shell=True)

    assert rc == 3


def test_run_command_argv_with_spaces_and_shell_syntax_literal() -> None:
    lines: list[str] = []
    rc = serverctl.run_command(
        [sys.executable, "-c", "print('a; $(echo hi)')"],
        on_line=lines.append,
    )

    assert rc == 0
    assert lines == ["a; $(echo hi)"]


def test_run_command_missing_executable_raises() -> None:
    with pytest.raises((FileNotFoundError, OSError)):
        serverctl.run_command(
            ["definitely-missing-mlx-tui-prog-xyz"],
            on_line=lambda _line: None,
        )


def test_run_command_rejects_empty() -> None:
    with pytest.raises(ValueError):
        serverctl.run_command("", on_line=lambda _line: None)
    with pytest.raises(ValueError):
        serverctl.run_command([], on_line=lambda _line: None)


def test_run_command_shell_env_handoff() -> None:
    lines: list[str] = []
    rc = serverctl.run_command(
        'echo "$MLX_TUI_MODEL"',
        on_line=lines.append,
        shell=True,
        env={"MLX_TUI_MODEL": "org/m", "PATH": "/usr/bin:/bin"},
    )

    assert rc == 0
    assert lines == ["org/m"]


def test_run_command_timeout_kills_process_group() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        serverctl.run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            on_line=lambda _line: None,
            timeout_s=0.2,
        )


def test_run_command_tolerates_callback_failure() -> None:
    def bad(_line: str) -> None:
        raise RuntimeError("callback boom")

    rc = serverctl.run_command(
        [sys.executable, "-c", "print('hi')"],
        on_line=bad,
    )

    assert rc == 0


def test_terminate_failed_process_handles_exited_and_none() -> None:
    serverctl.terminate_failed_process(None)
    proc = serverctl.spawn_command(
        [sys.executable, "-c", "pass"],
        on_line=lambda _line: None,
    )
    proc.wait(timeout=5)
    serverctl.terminate_failed_process(proc)


def test_terminate_failed_process_kills_sleeping_child() -> None:
    proc = serverctl.spawn_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        on_line=lambda _line: None,
    )
    serverctl.terminate_failed_process(proc)
    assert proc.poll() is not None


def test_terminate_owned_process_reaps_only_matching_child() -> None:
    owned = serverctl.spawn_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        on_line=lambda _line: None,
    )
    foreign = serverctl.spawn_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        on_line=lambda _line: None,
    )
    try:
        identity = ProcessIdentity(owned.pid, psutil.Process(owned.pid).create_time())
        serverctl.terminate_owned_process(owned, identity)
        assert owned.poll() is not None
        assert foreign.poll() is None
    finally:
        if foreign.poll() is None:
            foreign.terminate()
        foreign.wait(timeout=5)


def test_terminate_owned_process_escalates_within_owned_session() -> None:
    proc = serverctl.spawn_command(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        ],
        on_line=lambda _line: None,
    )
    try:
        identity = ProcessIdentity(proc.pid, psutil.Process(proc.pid).create_time())
        serverctl.terminate_owned_process(proc, identity)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def test_terminate_owned_process_rejects_recycled_identity() -> None:
    proc = serverctl.spawn_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        on_line=lambda _line: None,
    )
    try:
        wrong = ProcessIdentity(proc.pid, os.times().elapsed)
        with pytest.raises(RuntimeError, match="identity"):
            serverctl.terminate_owned_process(proc, wrong)
        assert proc.poll() is None
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_terminate_failed_process_cleans_term_ignoring_grandchild(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "grandchild.pid"
    grandchild_code = (
        "import signal, subprocess, sys, time; "
        "child=subprocess.Popen([sys.executable, '-c', "
        "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']); "
        f"open({str(pid_file)!r}, 'w').write(str(child.pid)); "
        "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); time.sleep(30)"
    )
    proc = serverctl.spawn_command(
        [sys.executable, "-c", grandchild_code],
        on_line=lambda _line: None,
    )
    try:
        deadline = time.monotonic() + 5.0
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_file.exists()
        grandchild_pid = int(pid_file.read_text())
        serverctl.terminate_failed_process(proc)
        assert proc.poll() is not None
        deadline = time.monotonic() + 5.0
        while psutil.pid_exists(grandchild_pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not psutil.pid_exists(grandchild_pid)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


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


def test_spawned_server_output_survives_launcher_exit(tmp_path: Path) -> None:
    marker = tmp_path / "server-output.txt"
    child_script = (
        "import sys, time; "
        "from pathlib import Path; "
        "time.sleep(0.5); "
        "sys.stderr.write('request log\\n'); sys.stderr.flush(); "
        f"Path({str(marker)!r}).write_text('ok')"
    )
    command = [sys.executable, "-c", child_script]
    launcher_script = (
        "from mlx_tui import serverctl; "
        f"proc = serverctl.spawn_command({command!r}, on_line=lambda _: None); "
        "print(proc.pid, flush=True)"
    )
    launcher = subprocess.run(
        [sys.executable, "-c", launcher_script],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    child_pid = int(launcher.stdout.strip())
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.read_text() == "ok"
    finally:
        with suppress(psutil.NoSuchProcess):
            psutil.Process(child_pid).kill()


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
        lambda request: httpx.Response(200, json={"data": [{"id": "other"}]}),
    )

    start = time.monotonic()
    probe = serverctl.wait_healthy(
        MODELS_URL,
        target_model="m",
        is_running=lambda: False,
        timeout_s=30,
    )

    assert probe is None
    assert time.monotonic() - start < 5


def test_wait_healthy_honors_cancellation_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel = threading.Event()
    cancel.set()

    def unexpected_probe(*_args: object, **_kwargs: object) -> object:
        pytest.fail("cancelled wait performed a probe")

    monkeypatch.setattr(serverctl.httpx.Client, "get", unexpected_probe)

    assert (
        serverctl.wait_healthy(
            MODELS_URL, target_model="m", timeout_s=5, cancel_event=cancel
        )
        is None
    )


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

    result = serverctl.warm_load(
        "http://stub/v1/chat/completions", "repo/a", timeout_s=5
    )

    payload = json.loads(requests[0].read())
    assert payload["model"] == "repo/a"
    assert payload["max_tokens"] == 1
    assert "stream" not in payload
    assert result is None


def test_warm_load_returns_response_model(monkeypatch: pytest.MonkeyPatch) -> None:
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "model": "repo/a",
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            },
        ),
    )

    result = serverctl.warm_load("http://stub/x", "repo/a", timeout_s=5)

    assert result == "repo/a"


def test_warm_load_non_string_model_yields_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "model": 123,
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            },
        ),
    )

    result = serverctl.warm_load("http://stub/x", "repo/a", timeout_s=5)

    assert result is None


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
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        return httpx.Response(200, json={"model": "m", "choices": [{}]})

    install_transport(
        monkeypatch,
        handler,
    )

    probe = serverctl.wait_healthy(
        MODELS_URL,
        target_model="m",
        timeout_s=5,
    )

    assert probe is not None
    assert probe.state == "green"
    assert probe.model_id == "m"
    assert [request.method for request in requests] == ["GET", "GET", "POST"]


def test_wait_healthy_wrong_model_then_right(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal generation_calls
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "other"}]})
        generation_calls += 1
        model = "other" if generation_calls == 1 else "m"
        return httpx.Response(200, json={"model": model, "choices": [{}]})

    install_transport(monkeypatch, handler)

    probe = serverctl.wait_healthy(
        MODELS_URL,
        target_model="m",
        timeout_s=10,
    )

    assert probe is not None
    assert probe.model_id == "m"
    assert generation_calls == 2


def test_wait_healthy_catalog_verifies_target_with_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})
        assert str(request.url) == "http://stub/v1/chat/completions"
        assert json.loads(request.read())["model"] == "b"
        return httpx.Response(200, json={"model": "b", "choices": [{"message": {}}]})

    install_transport(monkeypatch, handler)
    probe = serverctl.wait_healthy(MODELS_URL, target_model="b", timeout_s=5)
    assert probe is not None and probe.model_id == "b"
    assert len(requests) == 3


def test_wait_healthy_times_out_when_always_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(502, text="<html>proxy</html>"),
    )
    ticks: list[int] = []
    start = time.monotonic()

    probe = serverctl.wait_healthy(
        MODELS_URL,
        target_model=None,
        timeout_s=1,
        on_tick=ticks.append,
    )

    assert probe is None
    assert time.monotonic() - start >= 0.9
    assert ticks  # progress ticks were emitted while waiting


def test_wait_healthy_target_none_accepts_any_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"data": [{"id": "someone"}]})

    install_transport(monkeypatch, handler)

    probe = serverctl.wait_healthy(
        MODELS_URL,
        target_model=None,
        timeout_s=5,
    )

    assert probe is not None
    assert probe.state == "green"
    assert probe.model_id is None
    assert probe.available_models == ("someone",)


def test_wait_healthy_wrong_endpoint_model_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "other"}]})
        return httpx.Response(200, json={"model": "other", "choices": [{}]})

    install_transport(monkeypatch, handler)

    probe = serverctl.wait_healthy(
        MODELS_URL,
        target_model="m",
        timeout_s=1,
    )

    assert probe is None


def test_wait_healthy_missing_response_identity_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": ""}]})
        return httpx.Response(200, json={"choices": [{}]})

    install_transport(monkeypatch, handler)

    probe = serverctl.wait_healthy(
        MODELS_URL,
        target_model="m",
        timeout_s=1,
    )

    assert probe is None


def test_wait_healthy_probe_timeout_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    install_transport(monkeypatch, boom)

    start = time.monotonic()
    probe = serverctl.wait_healthy(
        MODELS_URL,
        target_model="m",
        timeout_s=1,
    )

    assert probe is None
    assert time.monotonic() - start >= 0.9
