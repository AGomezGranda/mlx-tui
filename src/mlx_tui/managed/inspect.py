"""Managed runtime inspection and ownership markers."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, cast

from mlx_tui.comparison_contracts import JSONValue
from mlx_tui.managed.paths import (
    BUILD_CONSTRAINTS_RESOURCE,
    MIN_FREE_BYTES,
    MLX_LM_VERSION,
    MLX_METAL_VERSION,
    MLX_VERSION,
    OWNER_MARKER,
    PYTHON_VERSION,
    QUALIFIED_MACOS_VERSION,
    RUNTIME_COMMIT,
    RUNTIME_RESOURCE,
    UV_VERSION,
    ManagedRuntimeError,
    _command_output,
    _freeze_requirements,
    _python_path,
    _resource_bytes,
    _resource_hash,
    _sanitized_environment,
    _uv_executable,
)
from mlx_tui.profiles import load_coding_profiles

try:
    import fcntl
except ImportError:  # pragma: no cover - managed mode is macOS-only
    fcntl = None  # type: ignore[assignment]


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
