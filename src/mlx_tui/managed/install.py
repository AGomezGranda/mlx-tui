"""Managed runtime installation into the app-owned path."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from mlx_tui import serverctl
from mlx_tui.managed.inspect import (
    _atomic_json,
    _preflight,
    _prepare_runtime,
    _runtime_lock,
    _validate_profiles,
    inspect_runtime,
)
from mlx_tui.managed.paths import (
    BUILD_CONSTRAINTS_RESOURCE,
    COMPLETION_MARKER,
    PYTHON_VERSION,
    RUNTIME_COMMIT,
    RUNTIME_RESOURCE,
    UV_VERSION,
    InstallCancelled,
    ManagedRuntimeError,
    _python_path,
    runtime_root,
)


def _run_install_command(
    argv: list[str],
    *,
    env: dict[str, str],
    on_line: Callable[[str], None],
    cancel_event: threading.Event,
) -> None:
    try:
        proc = subprocess.Popen(
            argv,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        raise ManagedRuntimeError(f"could not run {argv[0]}: {exc}") from exc
    lines: list[str] = []

    def pump() -> None:
        if proc.stdout is None:
            return
        try:
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\n")
                if line:
                    lines.append(line)
                    with suppress(Exception):
                        on_line(line)
        finally:
            with suppress(Exception):
                proc.stdout.close()

    reader = threading.Thread(target=pump, daemon=True, name="managed-install-output")
    reader.start()
    try:
        while proc.poll() is None:
            if cancel_event.is_set():
                serverctl.terminate_failed_process(proc)
                raise InstallCancelled("managed runtime installation cancelled")
            with suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=0.1)
            time.sleep(0.01)
        reader.join(timeout=1.0)
    finally:
        if proc.poll() is None:
            serverctl.terminate_failed_process(proc)
        reader.join(timeout=1.0)
    if cancel_event.is_set():
        raise InstallCancelled("managed runtime installation cancelled")
    if proc.returncode != 0:
        detail = lines[-1] if lines else f"exit status {proc.returncode}"
        raise ManagedRuntimeError(f"managed runtime installation failed: {detail}")


def install_runtime(
    *, on_line: Callable[[str], None], cancel_event: threading.Event
) -> Path:
    """Install and verify the pinned runtime at its final app-owned path."""
    root = runtime_root()
    uv, env = _preflight(root)
    _validate_profiles()
    root.parent.mkdir(parents=True, exist_ok=True)
    with _runtime_lock(root):
        _prepare_runtime(root)
        completion = root / COMPLETION_MARKER
        if completion.exists():
            with suppress(ManagedRuntimeError):
                inspect_runtime(root)
                return root
            if completion.is_symlink():
                raise ManagedRuntimeError(
                    f"refusing symlinked completion marker: {completion}"
                )
            completion.unlink()
        if cancel_event.is_set():
            raise InstallCancelled("managed runtime installation cancelled")
        with (
            resources.as_file(
                resources.files("mlx_tui").joinpath(RUNTIME_RESOURCE)
            ) as freeze,
            resources.as_file(
                resources.files("mlx_tui").joinpath(BUILD_CONSTRAINTS_RESOURCE)
            ) as constraints,
        ):
            with suppress(Exception):
                on_line(f"Installing Python {PYTHON_VERSION} runtime")
            _run_install_command(
                [
                    uv,
                    "--no-config",
                    "venv",
                    "--allow-existing",
                    "--python",
                    PYTHON_VERSION,
                    str(root),
                ],
                env=env,
                on_line=on_line,
                cancel_event=cancel_event,
            )
            _run_install_command(
                [
                    uv,
                    "--no-config",
                    "pip",
                    "sync",
                    "--python",
                    str(_python_path(root)),
                    "--strict",
                    "--build-constraints",
                    str(constraints),
                    str(freeze),
                ],
                env=env,
                on_line=on_line,
                cancel_event=cancel_event,
            )
        inspection = inspect_runtime(root)
        _atomic_json(
            completion,
            {
                "schema_version": 1,
                "runtime_root": str(root),
                "runtime_commit": RUNTIME_COMMIT,
                "python_version": PYTHON_VERSION,
                "uv_version": UV_VERSION,
                "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "inspection": inspection,
            },
        )
    return root
