"""Focused checks for the app-owned managed runtime boundary."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

import mlx_tui.managed.inspect as managed_inspect
import mlx_tui.managed.install as managed_install
import mlx_tui.managed.runtime as managed_runtime
from mlx_tui import serverctl
from mlx_tui.comparison_contracts import JSONValue
from mlx_tui.managed.paths import (
    COMPLETION_MARKER,
    MLX_LM_VERSION,
    MLX_VERSION,
    OWNER_MARKER,
    PYTHON_VERSION,
    RUNTIME_COMMIT,
    RUNTIME_DIRNAME,
    RUNTIME_RESOURCE,
    InstallCancelled,
    ManagedRuntimeError,
    _freeze_requirements,
    _resource_bytes,
    _sanitized_environment,
    runtime_root,
)
from mlx_tui.status import ServerProbe


def _fake_uv(env: dict[str, str]) -> str:
    return "/uv"


def _fake_preflight(path: Path) -> tuple[str, dict[str, str]]:
    return "/uv", {"PATH": "/bin"}


def _fake_inspection(path: Path) -> dict[str, JSONValue]:
    return {"schema_version": 1}


def _linux() -> str:
    return "Linux"


def _noop_validate() -> None:
    return


def _inspection_payload(*, mlx_version: str = MLX_VERSION) -> dict[str, object]:
    packages: dict[str, str] = {}
    for name, (version, _) in _freeze_requirements(
        _resource_bytes(RUNTIME_RESOURCE).decode()
    ).items():
        packages[name] = version or MLX_LM_VERSION
    packages["mlx"] = mlx_version
    return {
        "python_version": PYTHON_VERSION,
        "packages": packages,
        "direct_urls": {"mlx-lm": {"vcs_info": {"commit_id": RUNTIME_COMMIT}}},
        "server_entry_point": True,
    }


def test_runtime_root_uses_versioned_xdg_data_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert runtime_root() == tmp_path / "mlx-tui" / "runtimes" / "74e7cf9-py3131"


def test_sanitized_environment_drops_redirectors_but_keeps_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", "/foreign/venv")
    monkeypatch.setenv("PYTHONPATH", "/foreign/path")
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.example")
    env = _sanitized_environment()
    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert env["HTTPS_PROXY"] == "https://proxy.example"


def test_index_override_is_explicitly_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UV_INDEX_URL", "https://mirror.example/simple")
    with pytest.raises(ManagedRuntimeError, match="index override"):
        _sanitized_environment()


def test_preflight_rejects_wrong_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(managed_inspect.platform, "system", _linux)
    with pytest.raises(ManagedRuntimeError, match="Darwin arm64"):
        managed_inspect._preflight(tmp_path / "runtime")


def test_inspect_runtime_rejects_version_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runtime"
    (root / "bin").mkdir(parents=True)
    python = root / "bin" / "python"
    python.touch()
    monkeypatch.setattr(managed_inspect, "_uv_executable", _fake_uv)
    monkeypatch.setattr(managed_inspect, "_validate_profiles", _noop_validate)
    payload = _inspection_payload(mlx_version="0.32.1")

    def command_output(argv: list[str], env: dict[str, str]) -> str:
        return json.dumps(payload) if argv[0] == str(python) else "mlx==0.32.2\n"

    monkeypatch.setattr(
        managed_inspect,
        "_command_output",
        command_output,
    )
    with pytest.raises(ManagedRuntimeError, match="expected 0.32.2"):
        managed_inspect.inspect_runtime(root)


def test_inspect_runtime_rejects_direct_url_commit_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runtime"
    (root / "bin").mkdir(parents=True)
    python = root / "bin" / "python"
    python.touch()
    monkeypatch.setattr(managed_inspect, "_uv_executable", _fake_uv)
    monkeypatch.setattr(managed_inspect, "_validate_profiles", _noop_validate)
    payload = _inspection_payload()
    payload["direct_urls"] = {"mlx-lm": {"vcs_info": {"commit_id": "wrong"}}}

    def command_output(argv: list[str], env: dict[str, str]) -> str:
        return json.dumps(payload) if argv[0] == str(python) else "freeze\n"

    monkeypatch.setattr(
        managed_inspect,
        "_command_output",
        command_output,
    )
    with pytest.raises(ManagedRuntimeError, match="direct URL commit"):
        managed_inspect.inspect_runtime(root)


def test_install_targets_final_interpreter_and_writes_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data" / "mlx-tui" / "runtimes" / RUNTIME_DIRNAME

    def fake_runtime_root() -> Path:
        return root

    monkeypatch.setattr(managed_install, "runtime_root", fake_runtime_root)
    monkeypatch.setattr(managed_install, "_preflight", _fake_preflight)
    monkeypatch.setattr(managed_install, "inspect_runtime", _fake_inspection)
    commands: list[list[str]] = []

    def fake_run(
        argv: list[str],
        *,
        env: dict[str, str],
        on_line: Callable[[str], None],
        cancel_event: threading.Event,
    ) -> None:
        commands.append(argv)
        (root / "bin").mkdir(exist_ok=True)
        (root / "bin" / "python").touch()

    monkeypatch.setattr(managed_install, "_run_install_command", fake_run)
    result = managed_install.install_runtime(
        on_line=lambda line: None, cancel_event=threading.Event()
    )
    assert result == root
    assert commands[0][0:6] == [
        "/uv",
        "--no-config",
        "venv",
        "--allow-existing",
        "--python",
        PYTHON_VERSION,
    ]
    assert commands[1][0:5] == [
        "/uv",
        "--no-config",
        "pip",
        "sync",
        "--python",
    ]
    assert str(root / "bin" / "python") in commands[1]
    assert (root / COMPLETION_MARKER).exists()


def test_cancelled_install_leaves_incomplete_owned_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data" / "mlx-tui" / "runtimes" / RUNTIME_DIRNAME
    monkeypatch.setattr(managed_install, "runtime_root", lambda: root)
    monkeypatch.setattr(managed_install, "_preflight", _fake_preflight)

    def cancel(
        argv: list[str],
        *,
        env: dict[str, str],
        on_line: Callable[[str], None],
        cancel_event: threading.Event,
    ) -> None:
        raise InstallCancelled("cancelled")

    monkeypatch.setattr(managed_install, "_run_install_command", cancel)
    with pytest.raises(InstallCancelled):
        managed_install.install_runtime(
            on_line=lambda line: None, cancel_event=threading.Event()
        )
    assert (root / OWNER_MARKER).exists()
    assert not (root / COMPLETION_MARKER).exists()


def test_repair_refuses_unowned_existing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data" / "mlx-tui" / "runtimes" / RUNTIME_DIRNAME
    root.mkdir(parents=True)
    (root / "foreign.txt").write_text("keep")
    monkeypatch.setattr(managed_install, "runtime_root", lambda: root)
    monkeypatch.setattr(managed_install, "_preflight", _fake_preflight)
    with pytest.raises(ManagedRuntimeError, match="not app-owned"):
        managed_install.install_runtime(
            on_line=lambda line: None, cancel_event=threading.Event()
        )


def test_install_lock_reports_busy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "runtime"
    assert managed_inspect.fcntl is not None
    flock_module = managed_inspect.fcntl
    real_flock = flock_module.flock

    def busy(fd: int, operation: int) -> None:
        if operation & flock_module.LOCK_NB:
            raise BlockingIOError
        real_flock(fd, operation)

    monkeypatch.setattr(flock_module, "flock", busy)
    with pytest.raises(ManagedRuntimeError, match="already busy"):
        with managed_inspect._runtime_lock(root):
            pass


def test_managed_runtime_retains_and_closes_its_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data" / "mlx-tui" / "runtimes" / RUNTIME_DIRNAME
    root.mkdir(parents=True)
    (root / OWNER_MARKER).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runtime_root": str(root),
                "runtime_commit": RUNTIME_COMMIT,
            }
        )
    )
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    def fake_inspect(_path: Path) -> dict[str, JSONValue]:
        return {"ok": True}

    def fake_verify(_path: Path) -> None:
        return

    def fake_port(_self: managed_runtime.ManagedRuntime) -> bool:
        return False

    def fake_listener(_self: managed_runtime.ManagedRuntime) -> bool:
        return True

    def fake_spawn(
        _argv: list[str], *, on_line: Callable[[str], None], env: dict[str, str]
    ) -> subprocess.Popen[str]:
        del on_line, env
        return child

    def fake_wait(*_args: object, **_kwargs: object) -> ServerProbe:
        return ServerProbe(state="green", model_id=str(snapshot))

    monkeypatch.setattr(managed_runtime, "inspect_runtime", fake_inspect)
    monkeypatch.setattr(managed_runtime, "verify_cached_assets", fake_verify)
    monkeypatch.setattr(managed_runtime.ManagedRuntime, "_port_is_occupied", fake_port)
    monkeypatch.setattr(
        managed_runtime.ManagedRuntime, "_listener_matches_child", fake_listener
    )
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True
    )
    monkeypatch.setattr(
        serverctl,
        "spawn_command",
        fake_spawn,
    )
    monkeypatch.setattr(
        serverctl,
        "wait_healthy",
        fake_wait,
    )
    runtime = managed_runtime.ManagedRuntime(root, port=18080)
    try:
        probe = runtime.start(snapshot, on_line=lambda _line: None)
        assert probe.state == "green"
        assert runtime.process is child
        assert runtime.identity is not None
        runtime.close()
        assert child.poll() is not None
        assert runtime.process is None
        runtime.close()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_failed_managed_start_releases_runtime_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data" / "mlx-tui" / "runtimes" / RUNTIME_DIRNAME
    root.mkdir(parents=True)
    (root / OWNER_MARKER).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runtime_root": str(root),
                "runtime_commit": RUNTIME_COMMIT,
            }
        )
    )
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    def fake_inspect(_path: Path) -> dict[str, JSONValue]:
        return {"ok": True}

    def fake_verify(_path: Path) -> None:
        return

    def fake_port(_self: managed_runtime.ManagedRuntime) -> bool:
        return False

    def fake_listener(_self: managed_runtime.ManagedRuntime) -> bool:
        return True

    def fake_spawn(
        _argv: list[str], *, on_line: Callable[[str], None], env: dict[str, str]
    ) -> subprocess.Popen[str]:
        del on_line, env
        return child

    monkeypatch.setattr(managed_runtime, "inspect_runtime", fake_inspect)
    monkeypatch.setattr(managed_runtime, "verify_cached_assets", fake_verify)
    monkeypatch.setattr(managed_runtime.ManagedRuntime, "_port_is_occupied", fake_port)
    monkeypatch.setattr(
        managed_runtime.ManagedRuntime, "_listener_matches_child", fake_listener
    )
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True
    )
    monkeypatch.setattr(
        serverctl,
        "spawn_command",
        fake_spawn,
    )

    def failed_wait(*_args, **_kwargs):
        raise RuntimeError("health failed")

    monkeypatch.setattr(serverctl, "wait_healthy", failed_wait)
    runtime = managed_runtime.ManagedRuntime(root)
    try:
        with pytest.raises(RuntimeError, match="health failed"):
            runtime.start(snapshot, on_line=lambda _line: None)
        assert child.poll() is not None
        with managed_inspect._runtime_lock(root):
            pass
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def _owned_root(tmp_path: Path) -> Path:
    root = tmp_path / "data" / "mlx-tui" / "runtimes" / RUNTIME_DIRNAME
    root.mkdir(parents=True)
    (root / OWNER_MARKER).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runtime_root": str(root),
                "runtime_commit": RUNTIME_COMMIT,
            }
        )
    )
    return root


def test_managed_repeat_activation_requires_fresh_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _owned_root(tmp_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    def fake_inspect(_path: Path) -> dict[str, JSONValue]:
        return {"ok": True}

    def fake_verify(_path: Path) -> None:
        return None

    def fake_port(_self: managed_runtime.ManagedRuntime) -> bool:
        return False

    def fake_listener(_self: managed_runtime.ManagedRuntime) -> bool:
        return True

    monkeypatch.setattr(managed_runtime, "inspect_runtime", fake_inspect)
    monkeypatch.setattr(managed_runtime, "verify_cached_assets", fake_verify)
    monkeypatch.setattr(managed_runtime.ManagedRuntime, "_port_is_occupied", fake_port)
    monkeypatch.setattr(
        managed_runtime.ManagedRuntime, "_listener_matches_child", fake_listener
    )
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True
    )
    spawns: list[list[str]] = []
    waits: list[str] = []

    def fake_spawn(
        argv: list[str], *, on_line: Callable[[str], None], env: dict[str, str]
    ) -> subprocess.Popen[str]:
        del on_line, env
        spawns.append(argv)
        return child

    def fake_wait(url: str, **_kwargs: object) -> ServerProbe:
        waits.append(url)
        return ServerProbe(state="green", model_id=str(snapshot))

    monkeypatch.setattr(serverctl, "spawn_command", fake_spawn)
    monkeypatch.setattr(serverctl, "wait_healthy", fake_wait)
    runtime = managed_runtime.ManagedRuntime(root, port=18080)
    try:
        first = runtime.start(snapshot, on_line=lambda _line: None)
        assert first.state == "green"
        second = runtime.start(snapshot, on_line=lambda _line: None)
        assert second.state == "green"
        assert runtime.process is child
        assert child.poll() is None
        assert len(spawns) == 1
        assert len(waits) == 2
        assert waits[0].endswith("/v1/models")
    finally:
        runtime.close()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_managed_repeat_activation_failure_preserves_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _owned_root(tmp_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    def fake_inspect(_path: Path) -> dict[str, JSONValue]:
        return {"ok": True}

    def fake_verify(_path: Path) -> None:
        return None

    def fake_port(_self: managed_runtime.ManagedRuntime) -> bool:
        return False

    def fake_listener(_self: managed_runtime.ManagedRuntime) -> bool:
        return True

    monkeypatch.setattr(managed_runtime, "inspect_runtime", fake_inspect)
    monkeypatch.setattr(managed_runtime, "verify_cached_assets", fake_verify)
    monkeypatch.setattr(managed_runtime.ManagedRuntime, "_port_is_occupied", fake_port)
    monkeypatch.setattr(
        managed_runtime.ManagedRuntime, "_listener_matches_child", fake_listener
    )
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True
    )
    spawns = 0

    def fake_spawn(
        _argv: list[str], *, on_line: Callable[[str], None], env: dict[str, str]
    ) -> subprocess.Popen[str]:
        nonlocal spawns
        del on_line, env
        spawns += 1
        return child

    calls = 0

    def fake_wait(*_args: object, **_kwargs: object) -> ServerProbe | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ServerProbe(state="green", model_id=str(snapshot))
        return None

    monkeypatch.setattr(serverctl, "spawn_command", fake_spawn)
    monkeypatch.setattr(serverctl, "wait_healthy", fake_wait)
    runtime = managed_runtime.ManagedRuntime(root, port=18080)
    try:
        runtime.start(snapshot, on_line=lambda _line: None)
        with pytest.raises(ManagedRuntimeError, match="did not re-verify"):
            runtime.start(snapshot, on_line=lambda _line: None)
        assert runtime.process is child
        assert child.poll() is None
        assert spawns == 1
        assert calls == 2
        # Retry still possible; close remains safe.
        runtime.close()
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_managed_repeat_activation_cancelled_preserves_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _owned_root(tmp_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    def fake_inspect(_path: Path) -> dict[str, JSONValue]:
        return {"ok": True}

    def fake_verify(_path: Path) -> None:
        return None

    def fake_port(_self: managed_runtime.ManagedRuntime) -> bool:
        return False

    def fake_listener(_self: managed_runtime.ManagedRuntime) -> bool:
        return True

    monkeypatch.setattr(managed_runtime, "inspect_runtime", fake_inspect)
    monkeypatch.setattr(managed_runtime, "verify_cached_assets", fake_verify)
    monkeypatch.setattr(managed_runtime.ManagedRuntime, "_port_is_occupied", fake_port)
    monkeypatch.setattr(
        managed_runtime.ManagedRuntime, "_listener_matches_child", fake_listener
    )
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True
    )
    runtime = managed_runtime.ManagedRuntime(root, port=18080)

    def fake_spawn(
        _argv: list[str], *, on_line: Callable[[str], None], env: dict[str, str]
    ) -> subprocess.Popen[str]:
        del on_line, env
        return child

    calls = 0

    def fake_wait(*_args: object, **_kwargs: object) -> ServerProbe | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ServerProbe(state="green", model_id=str(snapshot))
        runtime.cancel_event.set()
        return None

    monkeypatch.setattr(serverctl, "spawn_command", fake_spawn)
    monkeypatch.setattr(serverctl, "wait_healthy", fake_wait)
    try:
        runtime.start(snapshot, on_line=lambda _line: None)
        with pytest.raises(ManagedRuntimeError, match="cancelled"):
            runtime.start(snapshot, on_line=lambda _line: None)
        assert runtime.process is child
        assert child.poll() is None
    finally:
        runtime.close()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_managed_repeat_activation_listener_mismatch_cannot_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _owned_root(tmp_path)
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()

    def fake_inspect(_path: Path) -> dict[str, JSONValue]:
        return {"ok": True}

    def fake_verify(_path: Path) -> None:
        return None

    def fake_port(_self: managed_runtime.ManagedRuntime) -> bool:
        return False

    monkeypatch.setattr(managed_runtime, "inspect_runtime", fake_inspect)
    monkeypatch.setattr(managed_runtime, "verify_cached_assets", fake_verify)
    monkeypatch.setattr(managed_runtime.ManagedRuntime, "_port_is_occupied", fake_port)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], text=True
    )
    runtime = managed_runtime.ManagedRuntime(root, port=18080)

    def fake_spawn(
        _argv: list[str], *, on_line: Callable[[str], None], env: dict[str, str]
    ) -> subprocess.Popen[str]:
        del on_line, env
        return child

    # First start: post-check True. Second start: pre-check True, post-check False.
    listener_calls = 0

    def fake_listener(_self: managed_runtime.ManagedRuntime) -> bool:
        nonlocal listener_calls
        listener_calls += 1
        # Calls: 1 = first start post-check (True),
        # 2 = second start pre-check (True), 3 = second start post-check (False).
        return listener_calls != 3

    def fake_wait(*_args: object, **_kwargs: object) -> ServerProbe:
        return ServerProbe(state="green", model_id=str(snapshot))

    monkeypatch.setattr(
        managed_runtime.ManagedRuntime, "_listener_matches_child", fake_listener
    )
    monkeypatch.setattr(serverctl, "spawn_command", fake_spawn)
    monkeypatch.setattr(serverctl, "wait_healthy", fake_wait)
    try:
        runtime.start(snapshot, on_line=lambda _line: None)
        with pytest.raises(ManagedRuntimeError, match="did not re-verify"):
            runtime.start(snapshot, on_line=lambda _line: None)
        assert runtime.process is child
        assert child.poll() is None
    finally:
        runtime.close()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
