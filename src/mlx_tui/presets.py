"""Loading of the presets TOML file (~/.config/mlx-tui/presets.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from mlx_tui.config import ConfigIssue, _from_mapping, config_path, validate_config


@dataclass(frozen=True)
class Preset:
    name: str
    system: str = ""
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None


def presets_path() -> Path:
    return config_path().parent / "presets.toml"


class PresetParseError(Exception):
    """Raised by :func:`parse_presets` when the file is unreadable or invalid."""


def presets_from_mapping(
    data: dict[str, object],
) -> tuple[tuple[Preset, ...], tuple[ConfigIssue, ...]]:
    raw = data.get("preset", [])
    if not isinstance(raw, list):
        return (), (
            ConfigIssue("preset", "Must be an array of preset tables", "error"),
        )
    presets: list[Preset] = []
    issues: list[ConfigIssue] = []
    allowed = {"name", "system", "temperature", "top_p", "max_tokens"}
    for index, entry in enumerate(raw):
        key = f"preset[{index}]"
        if not isinstance(entry, dict):
            issues.append(ConfigIssue(key, "Must be a preset table", "error"))
            continue
        label = f"{key} ({entry.get('name', 'unnamed')})"
        fields = {k: v for k, v in entry.items() if k in allowed and k != "name"}
        entry_issues = list(validate_config(fields))
        if type(entry.get("name")) is not str or not entry["name"].strip():
            entry_issues.append(
                ConfigIssue("name", "Must be a nonempty string", "error")
            )
        for field in entry:
            if field not in allowed:
                entry_issues.append(
                    ConfigIssue(field, "Unknown key; preserved on save", "warning")
                )
        issues.extend(
            ConfigIssue(
                f"{key}.{issue.key}", f"{label}: {issue.message}", issue.severity
            )
            for issue in entry_issues
        )
        if any(issue.severity == "error" for issue in entry_issues):
            continue
        config = _from_mapping(fields)
        presets.append(
            Preset(
                name=entry["name"],
                system=config.system or "",
                temperature=config.temperature,
                top_p=config.top_p,
                max_tokens=config.max_tokens,
            )
        )
    return tuple(presets), tuple(issues)


def parse_presets(path: Path | None = None) -> list[Preset]:
    if path is None:
        path = presets_path()
    try:
        with path.open("rb") as fh:
            data: dict[str, object] = tomllib.load(fh)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise PresetParseError(f"{path}: {exc}") from exc
    presets, issues = presets_from_mapping(data)
    errors = [
        f"{issue.key}: {issue.message}" for issue in issues if issue.severity == "error"
    ]
    if errors:
        raise PresetParseError(f"{path}: {'; '.join(errors)}")
    return list(presets)


def load_presets(path: Path | None = None) -> list[Preset]:
    try:
        return parse_presets(path)
    except PresetParseError:
        return []
