"""Owned pinned MLX-LM child lifecycle."""

from __future__ import annotations

import socket
import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import psutil

from mlx_tui import process, serverctl
from mlx_tui.comparison.contracts import JSONValue
from mlx_tui.managed.inspect import _check_owner, inspect_runtime
from mlx_tui.managed.paths import (
    ManagedRuntimeError,
    _python_path,
    _sanitized_environment,
    runtime_root,
)
from mlx_tui.models import verify_cached_assets
from mlx_tui.process import ProcessIdentity
from mlx_tui.status import ServerProbe

try:
    import fcntl
except ImportError:  # pragma: no cover - managed mode is macOS-only
    fcntl = None  # type: ignore[assignment]


class ManagedRuntime:
    """Own one pinned MLX-LM child for the lifetime of the app."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 18080,
    ) -> None:
        self.root = Path(root) if root is not None else runtime_root()
        self.host = host
        self.port = port
        self.process: subprocess.Popen[str] | None = None
        self.identity: ProcessIdentity | None = None
        self.current_model: Path | None = None
        self.previous_model: Path | None = None
        self.argv: list[str] = []
        self.environment: dict[str, str] = {}
        self.install_evidence: dict[str, JSONValue] = {}
        self.cancel_event = threading.Event()
        self._lock = threading.RLock()
        self._runtime_handle: Any | None = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def shutting_down(self) -> bool:
        return self.cancel_event.is_set()

    def _acquire_runtime_lock(self) -> None:
        if self._runtime_handle is not None:
            return
        if fcntl is None:
            raise ManagedRuntimeError(
                "managed runtime locking is unsupported on this OS"
            )
        self.root.parent.mkdir(parents=True, exist_ok=True)
        handle = self.root.parent.joinpath(".install.lock").open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise ManagedRuntimeError(
                "managed runtime installation is already busy"
            ) from exc
        self._runtime_handle = handle

    def _release_runtime_lock(self) -> None:
        handle = self._runtime_handle
        self._runtime_handle = None
        if handle is None:
            return
        if fcntl is not None:
            with suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def _child_is_running(self) -> bool:
        child = self.process
        return child is not None and child.poll() is None

    def _listener_matches_child(self) -> bool:
        if self.identity is None:
            return False
        found = process.find_server_process(self.host, self.port)
        return found is not None and (
            found.pid == self.identity.pid
            and found.create_time == self.identity.create_time
        )

    def listener_matches_child(self) -> bool:
        with self._lock:
            return self._listener_matches_child()

    def child_is_running(self) -> bool:
        with self._lock:
            return self._child_is_running()

    def _port_is_occupied(self) -> bool:
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.1)
            return sock.connect_ex((self.host, self.port)) == 0

    def _register_child(self, child: subprocess.Popen[str]) -> None:
        try:
            identity = ProcessIdentity(
                child.pid, psutil.Process(child.pid).create_time()
            )
        except (psutil.Error, OSError) as exc:
            serverctl.terminate_failed_process(child)
            raise ManagedRuntimeError(
                "could not record the managed child identity"
            ) from exc
        self.process = child
        self.identity = identity

    def _cleanup_child(self) -> None:
        child, identity = self.process, self.identity
        if child is None or identity is None:
            return
        serverctl.terminate_owned_process(child, identity)
        self.process = None
        self.identity = None

    def _models_url(self) -> str:
        rendered = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{rendered}:{self.port}/v1/models"

    def _probe_same_target(self, target: Path) -> ServerProbe:
        # Fresh request-scoped evidence; failure preserves child ownership.
        probe = serverctl.wait_healthy(
            self._models_url(),
            target_model=str(target),
            is_running=self._child_is_running,
            timeout_s=60.0,
            cancel_event=self.cancel_event,
        )
        if self._closed or self.cancel_event.is_set():
            raise ManagedRuntimeError("managed startup cancelled")
        if probe is None or not self._listener_matches_child():
            raise ManagedRuntimeError(
                "managed server did not re-verify the owned target"
            )
        return probe

    def start(self, model: Path, *, on_line: Callable[[str], None]) -> ServerProbe:
        """Start and verify a pinned snapshot, retaining the actual child."""
        target = Path(model)
        with self._lock:
            if self._closed:
                raise ManagedRuntimeError("managed runtime is closed")
            self.cancel_event.clear()
            _check_owner(self.root)
            self.install_evidence = inspect_runtime(self.root)
            try:
                verify_cached_assets(target)
            except ValueError as exc:
                raise ManagedRuntimeError(str(exc)) from exc
            if self._port_is_occupied() and not self._listener_matches_child():
                raise ManagedRuntimeError(
                    f"managed port {self.port} is occupied by an unknown process"
                )
            self._acquire_runtime_lock()
            if (
                self.process is not None
                and self.current_model == target
                and self._listener_matches_child()
            ):
                # Explicit re-activation may reload the target; treat as activation.
                return self._probe_same_target(target)
            try:
                if self.process is not None:
                    self.previous_model = self.current_model
                    self.current_model = None
                    self._cleanup_child()
                python = _python_path(self.root)
                workdir = self.root / "work"
                workdir.mkdir(exist_ok=True)
                env = _sanitized_environment()
                env.update(
                    {
                        "HF_HUB_OFFLINE": "1",
                        "HF_DATASETS_OFFLINE": "1",
                        "PYTHONNOUSERSITE": "1",
                    }
                )
                argv = [
                    str(python),
                    "-m",
                    "mlx_lm.server",
                    "--model",
                    str(target),
                    "--host",
                    self.host,
                    "--port",
                    str(self.port),
                    "--log-level",
                    "INFO",
                ]
                child = serverctl.spawn_command(argv, on_line=on_line, env=env)
                self.argv = argv
                self.environment = env
                self._register_child(child)
                probe = serverctl.wait_healthy(
                    self._models_url(),
                    target_model=str(target),
                    is_running=self._child_is_running,
                    timeout_s=60.0,
                    cancel_event=self.cancel_event,
                )
                if self._closed or self.cancel_event.is_set():
                    raise ManagedRuntimeError("managed startup cancelled")
                if probe is None or not self._listener_matches_child():
                    raise ManagedRuntimeError(
                        "managed server did not become healthy with the owned identity"
                    )
                self.current_model = target
                return probe
            except Exception:
                try:
                    self._cleanup_child()
                finally:
                    if self.process is None:
                        self._release_runtime_lock()
                raise

    def stop(self) -> None:
        with self._lock:
            self.cancel_event.set()
            self.current_model = None
            self._cleanup_child()
            self._release_runtime_lock()

    def close(self) -> None:
        # Set this before waiting for start(): a concurrent start observes it
        # before accepting health and cleans its child on the way out.
        self._closed = True
        self.cancel_event.set()
        with self._lock:
            self._cleanup_child()
            self._release_runtime_lock()
