"""UI-free JSON validation and atomic file-write helpers."""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

JSONValue = str | int | float | bool | None | dict[str, "JSONValue"] | list["JSONValue"]


def require_dict(
    value: object, label: str, *, error: Callable[[str], Exception]
) -> dict[str, Any]:
    if type(value) is not dict:
        raise error(f"{label} must be an object")
    return value  # type: ignore[return-value]


def required_field(
    table: dict[str, Any], key: str, label: str, *, error: Callable[[str], Exception]
) -> Any:
    if key not in table:
        raise error(f"{label} is missing {key!r}")
    return table[key]


def check_keys(
    table: dict[str, Any],
    allowed: set[str],
    label: str,
    *,
    error: Callable[[str], Exception],
) -> None:
    for key in table:
        if key not in allowed:
            raise error(f"{label} has an unexpected field {key!r}")


def string_field(
    table: dict[str, Any], key: str, label: str, *, error: Callable[[str], Exception]
) -> str:
    value = required_field(table, key, label, error=error)
    if type(value) is not str:
        raise error(f"{label}.{key} must be a string")
    return value


def optional_string_field(
    table: dict[str, Any], key: str, label: str, *, error: Callable[[str], Exception]
) -> str | None:
    value = required_field(table, key, label, error=error)
    if value is not None and type(value) is not str:
        raise error(f"{label}.{key} must be a string or null")
    return value


def bool_field(
    table: dict[str, Any], key: str, label: str, *, error: Callable[[str], Exception]
) -> bool:
    value = required_field(table, key, label, error=error)
    if type(value) is not bool:
        raise error(f"{label}.{key} must be a boolean")
    return value


def json_compatible(
    value: object, label: str, *, error: Callable[[str], Exception]
) -> JSONValue:
    if value is None or type(value) in {str, bool, int}:
        return value  # type: ignore[return-value]
    if type(value) is float:
        if not math.isfinite(value):
            raise error(f"{label} must hold finite JSON values")
        return value
    if type(value) is dict:
        result: dict[str, JSONValue] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise error(f"{label} must hold finite JSON values")
            result[key] = json_compatible(item, label, error=error)
        return result
    if type(value) in {list, tuple}:
        return [json_compatible(item, label, error=error) for item in value]  # type: ignore[union-attr]
    raise error(f"{label} must hold finite JSON values")


def atomic_write_bytes(
    target: Path,
    encoded: bytes,
    *,
    error: Callable[[str], Exception],
    what: str = "",
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(
            dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    except (OSError, ValueError) as exc:
        message = (
            f"could not save {what} {target}" if what else f"could not save {target}"
        )
        raise error(message) from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def sync_dir(
    directory: Path, target: Path, *, error: Callable[[str, Path], Exception]
) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        raise error(
            f"session bytes remain but durability is unconfirmed: {target}", target
        ) from exc
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            raise error(
                f"session bytes remain but durability is unconfirmed: {target}", target
            ) from exc
    finally:
        os.close(fd)
