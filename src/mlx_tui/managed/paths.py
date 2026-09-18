"""Managed runtime paths, pins, and sanitized process helpers."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from importlib import resources
from pathlib import Path

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


def _python_path(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
