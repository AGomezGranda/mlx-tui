"""Pinned, app-owned MLX-LM runtime installation and inspection."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, cast

import psutil

try:
    import fcntl
except ImportError:  # pragma: no cover - managed mode is macOS-only
    fcntl = None  # type: ignore[assignment]

from mlx_tui import process, serverctl
from mlx_tui.comparison_contracts import JSONValue
from mlx_tui.models import verify_cached_assets
from mlx_tui.process import ProcessIdentity
from mlx_tui.profiles import load_coding_profiles
from mlx_tui.status import ServerProbe

RUNTIME_COMMIT = "74e7cf931e84ef7c2f63e875adf414e20decc1c5"
RUNTIME_DIRNAME = "74e7cf9-py3131"
PYTHON_VERSION = "3.13.1"
UV_VERSION = "0.12.7"
MLX_VERSION = "0.32.2"
MLX_METAL_VERSION = "0.32.2"
MLX_LM_VERSION = "0.32.0"
QUALIFIED_MACOS_VERSION = "26.6.2"
MIN_FREE_BYTES = 2 * 2**30
RUNTIME_RESOURCE = "managed-runtime.txt"
BUILD_CONSTRAINTS_RESOURCE = "managed-build-constraints.txt"
OWNER_MARKER = ".mlx-tui-owner.json"
COMPLETION_MARKER = ".mlx-tui-runtime.json"

_INDEX_ENVIRONMENT = {
    "UV_DEFAULT_INDEX",
    "UV_EXTRA_INDEX_URL",
    "UV_FIND_LINKS",
    "UV_INDEX",
    "UV_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_FIND_LINKS",
    "PIP_INDEX_URL",
}
_SANITIZED_ENVIRONMENT = {
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONUSERBASE",
    "PIP_CONFIG_FILE",
    "PIP_PREFIX",
    "PIP_REQUIRE_VIRTUALENV",
    "PIP_TARGET",
    "PIP_USER",
    "UV_CONFIG_FILE",
    "UV_PROJECT",
    "UV_PYTHON",
    "UV_SYSTEM_PYTHON",
    "UV_WORKING_DIR",
    "VIRTUAL_ENV",
}


class ManagedRuntimeError(ValueError):
    """The managed runtime is unavailable or failed identity verification."""


class InstallCancelled(ManagedRuntimeError):
    """The caller cancelled installation before it completed."""


def runtime_root() -> Path:
    """Return the final, versioned app-owned runtime path."""
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "mlx-tui" / "runtimes" / RUNTIME_DIRNAME


def _resource_bytes(name: str) -> bytes:
    return resources.files("mlx_tui").joinpath(name).read_bytes()


def _resource_hash(name: str) -> str:
    return hashlib.sha256(_resource_bytes(name)).hexdigest()


def _normal_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _freeze_requirements(text: str) -> dict[str, tuple[str | None, str | None]]:
    requirements: dict[str, tuple[str | None, str | None]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if " @ " in line:
            name, url = line.split(" @ ", 1)
            requirements[_normal_name(name)] = (None, url)
            continue
        name, separator, version = line.partition("==")
        if separator:
            requirements[_normal_name(name)] = (version, None)
    return requirements


def _sanitized_environment() -> dict[str, str]:
    env = os.environ.copy()
    unsupported = sorted(key for key in _INDEX_ENVIRONMENT if env.get(key))
    if unsupported:
        names = ", ".join(unsupported)
        raise ManagedRuntimeError(
            f"unsupported package index override ({names}); remove it before managed setup"
        )
    for key in _SANITIZED_ENVIRONMENT:
        env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _command_output(argv: list[str], env: dict[str, str]) -> str:
    try:
        completed = subprocess.run(
            argv,
            env=env,
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        raise ManagedRuntimeError(f"could not run {argv[0]}: {exc}") from exc
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        detail = output.strip() or f"exit status {completed.returncode}"
        raise ManagedRuntimeError(f"{argv[0]} failed: {detail}")
    return output


def _uv_executable(env: dict[str, str]) -> str:
    uv = shutil.which("uv", path=env.get("PATH"))
    if uv is None:
        raise ManagedRuntimeError("uv 0.12.7 is required; install uv and retry")
    return uv


def _preflight(root: Path) -> tuple[str, dict[str, str]]:
    if platform.system() != "Darwin" or platform.machine().lower() not in {
        "arm64",
        "aarch64",
    }:
        raise ManagedRuntimeError("managed setup is untested; requires Darwin arm64")
    macos_version = platform.mac_ver()[0]
    if macos_version != QUALIFIED_MACOS_VERSION:
        raise ManagedRuntimeError(
            f"macOS {macos_version or 'unknown'} is untested; qualified version is "
            f"{QUALIFIED_MACOS_VERSION}"
        )
    if not root.is_absolute():
        raise ManagedRuntimeError("managed runtime path must be absolute")
    env = _sanitized_environment()
    uv = _uv_executable(env)
    uv_output = _command_output([uv, "--no-config", "--version"], env)
    match = re.search(r"\buv\s+(\d+\.\d+\.\d+)", uv_output)
    if match is None or match.group(1) != UV_VERSION:
        found = match.group(1) if match else "unknown"
        raise ManagedRuntimeError(f"uv {UV_VERSION} is required; found {found}")
    _command_output(
        [uv, "--no-config", "--no-python-downloads", "python", "find", PYTHON_VERSION],
        env,
    )
    if shutil.which("git", path=env.get("PATH")) is None:
        raise ManagedRuntimeError(
            "Git is required for the pinned MLX-LM runtime; install Git and retry"
        )
    data_root = root.parents[2]
    for path in (data_root, data_root / "mlx-tui", root.parent, root):
        if path.exists() and path.is_symlink():
            raise ManagedRuntimeError(f"refusing symlinked managed path: {path}")
    existing = root
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if not existing.is_dir() or not os.access(existing, os.W_OK):
        raise ManagedRuntimeError(f"managed data directory is not writable: {existing}")
    if shutil.disk_usage(existing).free < MIN_FREE_BYTES:
        raise ManagedRuntimeError("at least 2 GiB of free disk space is required")
    return uv, env


def _python_path(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


_INSPECT_SCRIPT = r"""
import json
import re
import sys
from importlib import metadata
from pathlib import Path

def normal(name):
    return re.sub(r"[-_.]+", "-", name).lower()

packages = {}
for dist in metadata.distributions():
    name = dist.metadata.get("Name")
    if name:
        packages[normal(name)] = dist.version
direct_urls = {}
for name in ("mlx-lm", "mlx", "mlx-metal"):
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        continue
    direct = Path(dist._path) / "direct_url.json"
    if direct.is_file():
        direct_urls[normal(name)] = json.loads(direct.read_text())
entry_point = any(
    entry.name == "mlx_lm.server" and entry.value == "mlx_lm.server:main"
    for entry in metadata.entry_points(group="console_scripts")
)
print(json.dumps({
    "python_version": ".".join(str(part) for part in sys.version_info[:3]),
    "packages": packages,
    "direct_urls": direct_urls,
    "server_entry_point": entry_point,
}))
"""


def _validate_profiles() -> None:
    for entry in load_coding_profiles():
        if entry.profile.runtime_commit != RUNTIME_COMMIT:
            raise ManagedRuntimeError(
                f"profile {entry.profile.id} has a different MLX-LM commit"
            )
        if entry.profile.mlx_version != MLX_VERSION:
            raise ManagedRuntimeError(
                f"profile {entry.profile.id} has a different MLX version"
            )


def inspect_runtime(path: Path) -> dict[str, JSONValue]:  # noqa: PLR0912
    """Inspect a runtime with its own interpreter and fail closed on mismatch."""
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        raise ManagedRuntimeError(f"managed runtime directory is missing: {root}")
    python = _python_path(root)
    if not python.is_file():
        raise ManagedRuntimeError(f"managed runtime interpreter is missing: {python}")
    env = _sanitized_environment()
    uv = _uv_executable(env)
    inspected = json.loads(
        _command_output([str(python), "-I", "-c", _INSPECT_SCRIPT], env)
    )
    if not isinstance(inspected, dict):
        raise ManagedRuntimeError("runtime inspection returned invalid JSON")
    packages = inspected.get("packages")
    direct_urls = inspected.get("direct_urls")
    python_version = inspected.get("python_version")
    if not isinstance(packages, dict) or not isinstance(direct_urls, dict):
        raise ManagedRuntimeError("runtime inspection omitted package metadata")
    if python_version != PYTHON_VERSION:
        raise ManagedRuntimeError(
            f"managed runtime requires Python {PYTHON_VERSION}; found {python_version}"
        )
    freeze = _command_output(
        [uv, "--no-config", "pip", "freeze", "--python", str(python)], env
    )
    requirements = _freeze_requirements(_resource_bytes(RUNTIME_RESOURCE).decode())
    for name, (version, source) in requirements.items():
        actual = packages.get(name)
        if not isinstance(actual, str):
            raise ManagedRuntimeError(f"managed runtime is missing {name}")
        if version is not None and actual != version:
            raise ManagedRuntimeError(
                f"managed runtime has {name} {actual}; expected {version}"
            )
        if source is not None and name == "mlx-lm":
            direct = direct_urls.get(name)
            commit = (
                direct.get("vcs_info", {}).get("commit_id")
                if isinstance(direct, dict)
                else None
            )
            if commit != RUNTIME_COMMIT:
                raise ManagedRuntimeError(
                    "MLX-LM direct URL commit does not match the pin"
                )
    expected = {
        "mlx": MLX_VERSION,
        "mlx-metal": MLX_METAL_VERSION,
        "mlx-lm": MLX_LM_VERSION,
    }
    for name, version in expected.items():
        if packages.get(name) != version:
            raise ManagedRuntimeError(f"{name} version does not match the managed pin")
    if inspected.get("server_entry_point") is not True:
        raise ManagedRuntimeError("mlx_lm.server console entry point is missing")
    _validate_profiles()
    return {
        "schema_version": 1,
        "runtime_root": str(root),
        "python": str(python),
        "python_version": PYTHON_VERSION,
        "uv_version": UV_VERSION,
        "mlx_version": MLX_VERSION,
        "mlx_metal_version": MLX_METAL_VERSION,
        "mlx_lm_version": MLX_LM_VERSION,
        "runtime_commit": RUNTIME_COMMIT,
        "server_entry_point": "mlx_lm.server:main",
        "packages": cast(dict[str, JSONValue], packages),
        "direct_urls": cast(dict[str, JSONValue], direct_urls),
        "freeze": freeze,
        "freeze_sha256": hashlib.sha256(freeze.encode()).hexdigest(),
        "resource_hashes": {
            RUNTIME_RESOURCE: _resource_hash(RUNTIME_RESOURCE),
            BUILD_CONSTRAINTS_RESOURCE: _resource_hash(BUILD_CONSTRAINTS_RESOURCE),
        },
        "provenance": {
            "resolver": f"uv {UV_VERSION}",
            "python": PYTHON_VERSION,
            "runtime_commit": RUNTIME_COMMIT,
        },
    }


def _atomic_json(path: Path, value: dict[str, JSONValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _read_marker(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _check_owner(root: Path) -> None:
    marker = _read_marker(root / OWNER_MARKER)
    if marker is None or marker.get("schema_version") != 1:
        raise ManagedRuntimeError(f"runtime is not app-owned: {root}")
    if (
        marker.get("runtime_root") != str(root)
        or marker.get("runtime_commit") != RUNTIME_COMMIT
    ):
        raise ManagedRuntimeError(f"runtime ownership marker does not match: {root}")


def _prepare_runtime(root: Path) -> None:
    if root.exists():
        if root.is_symlink() or not root.is_dir():
            raise ManagedRuntimeError(f"refusing unexpected runtime path: {root}")
        _check_owner(root)
        return
    root.mkdir(parents=True)
    _atomic_json(
        root / OWNER_MARKER,
        {
            "schema_version": 1,
            "runtime_root": str(root),
            "runtime_commit": RUNTIME_COMMIT,
            "python_version": PYTHON_VERSION,
        },
    )


@contextmanager
def _runtime_lock(root: Path):
    if fcntl is None:
        raise ManagedRuntimeError("managed runtime locking is unsupported on this OS")
    lock_path = root.parent / ".install.lock"
    try:
        handle = lock_path.open("a+")
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot open managed install lock: {exc}") from exc
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ManagedRuntimeError(
                "managed runtime installation is already busy"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


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
