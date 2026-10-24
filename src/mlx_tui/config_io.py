"""Validated configuration snapshots and comment-preserving writes."""

from __future__ import annotations

import fcntl
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomlkit

from mlx_tui.config import (
    CONFIG_TEMPLATE,
    AppConfig,
    ConfigIssue,
    ConfigParseError,
    _from_mapping,
    config_path,
    validate_config,
)
from mlx_tui.json_util import atomic_write_bytes
from mlx_tui.presets import Preset, presets_from_mapping


class ConfigWriteError(Exception):
    """Configuration could not be saved; the prior snapshot remains active."""


@dataclass(frozen=True)
class ConfigDocument:
    path: Path
    source: bytes | None
    config: AppConfig
    presets: tuple[Preset, ...]
    issues: tuple[ConfigIssue, ...]
    legacy_source: bytes | None
    _target: Path


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _mapping(source: bytes, path: Path) -> dict[str, object]:
    try:
        return tomllib.loads(source.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigParseError(f"{path}: {exc}") from exc


def read_config(path: Path | None = None) -> ConfigDocument:
    path = path if path is not None else config_path()
    try:
        target = path.resolve()
        source = _read_bytes(target)
        data: dict[str, object] = _mapping(source, path) if source is not None else {}
        legacy_source = None
        preset_data: dict[str, object] = data
        issues = list(validate_config(data))
        if "preset" not in data:
            legacy_path = path.parent / "presets.toml"
            legacy_source = _read_bytes(legacy_path)
            if legacy_source is not None:
                try:
                    preset_data = _mapping(legacy_source, legacy_path)
                except ConfigParseError as exc:
                    issues.append(ConfigIssue("preset", str(exc), "error"))
                    preset_data = {}
                issues.append(
                    ConfigIssue("preset", "Legacy presets: import on Save", "warning")
                )
        presets, preset_issues = presets_from_mapping(preset_data)
        return ConfigDocument(
            path,
            source,
            _from_mapping(data),
            presets,
            (*issues, *preset_issues),
            legacy_source,
            target,
        )
    except (OSError, RuntimeError) as exc:
        raise ConfigParseError(f"{path}: {exc}") from exc


def save_config(
    document: ConfigDocument, changes: dict[str, str | int | float | bool | None]
) -> ConfigDocument:
    source = (
        document.source.decode("utf-8")
        if document.source is not None
        else CONFIG_TEMPLATE
    )
    doc = tomlkit.parse(source)
    if "max_ctx" in changes:
        doc.pop("max_context", None)
    for key, value in changes.items():
        if value is None:
            doc.pop(key, None)
        else:
            doc[key] = value
    importing = "preset" not in doc and document.legacy_source is not None
    if importing:
        assert document.legacy_source is not None
        legacy_path = document.path.parent / "presets.toml"
        try:
            legacy_data = _mapping(document.legacy_source, legacy_path)
        except ConfigParseError as exc:
            raise ConfigWriteError(str(exc)) from exc
        _, legacy_issues = presets_from_mapping(legacy_data)
        _require_valid(legacy_issues)
        legacy_doc = tomlkit.parse(document.legacy_source.decode("utf-8"))
        # Keep the legacy array's formatting, comments, names, and order.
        doc["preset"] = legacy_doc.get("preset", [])
        for key, item in legacy_doc.body:
            if key is None:
                doc.add(item)
    encoded = tomlkit.dumps(doc).encode("utf-8")
    data = _mapping(encoded, document.path)
    _, preset_issues = presets_from_mapping(data)
    _require_valid((*validate_config(data), *preset_issues))
    try:
        _check_unchanged(document)
        target = document._target
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_name(target.name + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "wb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ConfigWriteError(
                    f"Config lock is busy: {lock_path}; retry Save"
                ) from exc
            _check_unchanged(document)
            if importing and document.source is not None:
                backup = document.path.with_name(
                    document.path.name + ".pre-unification.bak"
                )
                _backup(backup, document.source)
            atomic_write_bytes(target, encoded, error=ConfigWriteError, what="config")
    except (OSError, RuntimeError) as exc:
        raise ConfigWriteError(f"Could not save {document.path}: {exc}") from exc
    return read_config(document.path)


def _require_valid(issues: tuple[ConfigIssue, ...]) -> None:
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise ConfigWriteError("; ".join(f"{i.key}: {i.message}" for i in errors))


def _check_unchanged(document: ConfigDocument) -> None:
    if (
        document.path.resolve() != document._target
        or _read_bytes(document._target) != document.source
    ):
        raise ConfigWriteError("Config changed on disk; Reload before saving")
    data: dict[str, object] = (
        _mapping(document.source, document.path) if document.source is not None else {}
    )
    if "preset" not in data:
        if _read_bytes(document.path.parent / "presets.toml") != document.legacy_source:
            raise ConfigWriteError(
                "Legacy presets changed on disk; Reload before saving"
            )


def _backup(path: Path, source: bytes) -> None:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(source)
            file.flush()
            os.fsync(file.fileno())
    except OSError:
        path.unlink(missing_ok=True)
        raise
