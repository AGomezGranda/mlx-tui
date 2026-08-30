"""Loading of the presets TOML file (~/.config/mlx-tui/presets.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from mlx_tui.config import _clamp, _clamp_int, _coerce_float_strict, config_path


@dataclass(frozen=True)
class Preset:
    name: str
    system: str = ""
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None


def presets_path() -> Path:
    return config_path().parent / "presets.toml"


PRESETS_TEMPLATE = """# mlx-tui presets — flat [[preset]] list, ctrl+n forward, ctrl+o back
# [[preset]]
# name = "default"
# system = "You are helpful."
# temperature = 0.7
# top_p = 1.0
# max_tokens = 1024
"""


def write_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PRESETS_TEMPLATE)


class PresetParseError(Exception):
    """Raised by :func:`parse_presets` when the file is unreadable or invalid."""


def _preset_from_mapping(d: dict[str, object]) -> Preset | None:  # noqa: PLR0912
    name = d.get("name")
    if type(name) is not str:
        return None
    system = d.get("system", "")
    if type(system) is not str:
        return None
    # allow extra keys ignored
    temp: float | None = None
    top_p: float | None = None
    max_tok: int | None = None
    if "temperature" in d:
        temp = _coerce_float_strict(d["temperature"])
        if temp is None:
            return None
    if "top_p" in d:
        top_p = _coerce_float_strict(d["top_p"])
        if top_p is None:
            return None
    if "max_tokens" in d:
        v = d["max_tokens"]
        if type(v) is int:
            max_tok = v
        else:
            return None
    if temp is not None:
        temp = _clamp(temp, 0.0, 2.0)
    if top_p is not None:
        top_p = _clamp(top_p, 0.0, 1.0)
    if max_tok is not None:
        max_tok = _clamp_int(max_tok, 1, 16384)
    return Preset(
        name=name, system=system, temperature=temp, top_p=top_p, max_tokens=max_tok
    )


def parse_presets(path: Path | None = None) -> list[Preset]:
    if path is None:
        path = presets_path()
    try:
        with path.open("rb") as fh:
            data: dict[str, object] = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PresetParseError(f"{path}: {exc}") from exc
    raw = data.get("preset")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PresetParseError(f"{path}: 'preset' must be a list")
    presets: list[Preset] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        preset = _preset_from_mapping(item)
        if preset is not None:
            presets.append(preset)
    return presets


def load_presets(path: Path | None = None) -> list[Preset]:
    try:
        return parse_presets(path)
    except PresetParseError:
        return []
