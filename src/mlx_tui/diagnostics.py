"""Private, local-only support diagnostics with no user-content collection."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
from datetime import UTC, datetime
from importlib import resources
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

import psutil

from mlx_tui import managed
from mlx_tui.config import ConfigParseError, config_path, parse_config

_MAX_MARKER_BYTES = 1024 * 1024
_VERSION = re.compile(r"^(?=.*[0-9])[0-9A-Za-z][0-9A-Za-z.!+~_-]{0,63}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DEPENDENCIES = ("httpx", "huggingface-hub", "psutil", "textual", "rich")
_RUNTIME_PACKAGES = ("mlx", "mlx-metal", "mlx-lm")


class DiagnosticsError(Exception):
    """A fixed, user-actionable diagnostics export failure."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(f"diagnostics export failed: {category}")


def _safe_version(value: object) -> str | None:
    return value if isinstance(value, str) and _VERSION.fullmatch(value) else None


def _safe_hash(value: object, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _package_info(name: str) -> dict[str, object]:
    try:
        value = package_version(name)
    except PackageNotFoundError:
        return {"status": "unavailable", "version": None}
    except (TypeError, ValueError):
        return {"status": "unavailable", "version": None}
    safe = _safe_version(value)
    if safe is None:
        return {"status": "invalid", "version": None}
    return {"status": "available", "version": safe}


def _packaged_runtime() -> dict[str, object]:
    hashes: dict[str, str | None] = {}
    try:
        for name in (managed.RUNTIME_RESOURCE, managed.BUILD_CONSTRAINTS_RESOURCE):
            content = resources.files("mlx_tui").joinpath(name).read_bytes()
            hashes[name] = hashlib.sha256(content).hexdigest()
    except (ModuleNotFoundError, OSError):
        return {
            "status": "unavailable",
            "runtime_commit": None,
            "python_version": None,
            "uv_version": None,
            "mlx_version": None,
            "mlx_metal_version": None,
            "mlx_lm_version": None,
            "resource_hashes": hashes,
        }
    return {
        "status": "available",
        "runtime_commit": managed.RUNTIME_COMMIT,
        "python_version": managed.PYTHON_VERSION,
        "uv_version": managed.UV_VERSION,
        "mlx_version": managed.MLX_VERSION,
        "mlx_metal_version": managed.MLX_METAL_VERSION,
        "mlx_lm_version": managed.MLX_LM_VERSION,
        "resource_hashes": hashes,
    }


def _config_info() -> dict[str, object]:
    path = config_path()
    if not path.exists():
        return {"present": False, "status": "missing", "runtime_mode": None}
    try:
        config = parse_config(path)
    except ConfigParseError:
        return {"present": True, "status": "malformed", "runtime_mode": None}
    return {
        "present": True,
        "status": "valid",
        "runtime_mode": config.runtime_mode,
    }


def _marker_info() -> dict[str, object]:  # noqa: PLR0911, PLR0912
    marker = managed.runtime_root() / managed.COMPLETION_MARKER
    packages: dict[str, str | None] = {name: None for name in _RUNTIME_PACKAGES}
    resource_hashes: dict[str, str | None] = {
        managed.RUNTIME_RESOURCE: None,
        managed.BUILD_CONSTRAINTS_RESOURCE: None,
    }
    unknown: dict[str, object] = {
        "runtime_commit": None,
        "python_version": None,
        "uv_version": None,
        "packages": packages,
        "resource_hashes": resource_hashes,
        "freeze_sha256": None,
    }
    try:
        info = marker.lstat()
    except FileNotFoundError:
        return {
            "status": "missing",
            "verification": "recorded, not verified now",
            **unknown,
        }
    except OSError:
        return {
            "status": "unavailable",
            "verification": "recorded, not verified now",
            **unknown,
        }
    if stat.S_ISLNK(info.st_mode):
        return {
            "status": "symlink",
            "verification": "recorded, not verified now",
            **unknown,
        }
    if not stat.S_ISREG(info.st_mode):
        return {
            "status": "invalid_type",
            "verification": "recorded, not verified now",
            **unknown,
        }
    if info.st_size > _MAX_MARKER_BYTES:
        return {
            "status": "too_large",
            "verification": "recorded, not verified now",
            **unknown,
        }
    try:
        with marker.open("rb") as file:
            raw = file.read(_MAX_MARKER_BYTES + 1)
    except OSError:
        return {
            "status": "unavailable",
            "verification": "recorded, not verified now",
            **unknown,
        }
    if len(raw) > _MAX_MARKER_BYTES:
        return {
            "status": "too_large",
            "verification": "recorded, not verified now",
            **unknown,
        }
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "status": "corrupt",
            "verification": "recorded, not verified now",
            **unknown,
        }
    if not isinstance(value, dict):
        return {
            "status": "corrupt",
            "verification": "recorded, not verified now",
            **unknown,
        }

    invalid = False
    commit = _safe_hash(value.get("runtime_commit"), _HEX40)
    if value.get("runtime_commit") is not None and commit is None:
        invalid = True
    python_version = _safe_version(value.get("python_version"))
    if value.get("python_version") is not None and python_version is None:
        invalid = True
    uv_version = _safe_version(value.get("uv_version"))
    if value.get("uv_version") is not None and uv_version is None:
        invalid = True
    freeze_sha256: str | None = None
    inspection = value.get("inspection")
    if isinstance(inspection, dict):
        raw_packages = inspection.get("packages")
        if isinstance(raw_packages, dict):
            for name in _RUNTIME_PACKAGES:
                package = _safe_version(raw_packages.get(name))
                if raw_packages.get(name) is not None and package is None:
                    invalid = True
                packages[name] = package
        raw_hashes = inspection.get("resource_hashes")
        if isinstance(raw_hashes, dict):
            for name in resource_hashes:
                value_hash = _safe_hash(raw_hashes.get(name), _HEX64)
                if raw_hashes.get(name) is not None and value_hash is None:
                    invalid = True
                resource_hashes[name] = value_hash
        freeze_sha256 = _safe_hash(inspection.get("freeze_sha256"), _HEX64)
        if inspection.get("freeze_sha256") is not None and freeze_sha256 is None:
            invalid = True
    return {
        "status": "invalid_values" if invalid else "recorded",
        "verification": "recorded, not verified now",
        "runtime_commit": commit,
        "python_version": python_version,
        "uv_version": uv_version,
        "packages": packages,
        "resource_hashes": resource_hashes,
        "freeze_sha256": freeze_sha256,
    }


def collect_diagnostics() -> dict[str, object]:
    """Collect an allowlisted local snapshot without startup or network work."""
    tui = _package_info("mlx-tui")
    dependencies = {name: _package_info(name) for name in _DEPENDENCIES}
    try:
        total_ram = psutil.virtual_memory().total
        ram_status = "available"
    except (AttributeError, OSError, ValueError):
        total_ram = None
        ram_status = "unavailable"
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tui_version": tui["version"],
        "tui_version_status": tui["status"],
        "python_version": platform.python_version(),
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "total_ram_bytes": total_ram,
        "total_ram_status": ram_status,
        "dependencies": dependencies,
        "expected_runtime": _packaged_runtime(),
        "recorded_installation": _marker_info(),
        "config": _config_info(),
    }


def write_diagnostics(path: Path) -> None:
    """Write one private JSON snapshot, refusing overwrite and symlinks."""
    payload = collect_diagnostics()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DiagnosticsError("parent_unavailable") from exc
    if path.is_symlink():
        raise DiagnosticsError("symlink_target")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise DiagnosticsError("output_exists") from exc
    except OSError as exc:
        raise DiagnosticsError("output_unavailable") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
    except Exception as exc:
        try:
            path.unlink()
        except OSError:
            pass
        raise DiagnosticsError("write_failed") from exc
