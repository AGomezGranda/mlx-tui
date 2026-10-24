"""Loading of the single TOML config file (~/.config/mlx-tui/config.toml)."""

from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

SwapPolicy = Literal["auto", "warm", "restart"]
RuntimeMode = Literal["attach", "managed"]

DEFAULT_TEMPERATURE = 0.7
DEFAULT_TOP_P = 1.0
DEFAULT_MAX_TOKENS = 1024
DEFAULT_MAX_CTX = 8192
TEMPERATURE_RANGE = (0.0, 2.0)
TOP_P_RANGE = (0.0, 1.0)
MAX_TOKENS_RANGE = (1, 16384)
MAX_CTX_RANGE = (1024, 131072)


@dataclass(frozen=True)
class AppConfig:
    """Everything the TUI reads from disk; every field degrades to a default."""

    model: str | None = None
    host: str = "127.0.0.1"
    port: int = 8080
    start_cmd: str | None = None
    stop_cmd: str | None = None
    command_shell: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    seed: int | None = None
    enable_thinking: bool | None = None
    system: str | None = None
    max_ctx: int = DEFAULT_MAX_CTX
    swap_policy: SwapPolicy = "auto"
    runtime_mode: RuntimeMode = "attach"


@dataclass(frozen=True)
class ConfigIssue:
    key: str
    message: str
    severity: Literal["error", "warning"]


@dataclass(frozen=True)
class LaunchOverrides:
    host: str | None = None
    port: int | None = None
    runtime_mode: RuntimeMode | None = None


def resolve_launch(config: AppConfig, overrides: LaunchOverrides) -> AppConfig:
    mode = overrides.runtime_mode or config.runtime_mode
    host, port = (
        ("127.0.0.1", 18080) if mode == "managed" else (config.host, config.port)
    )
    return replace(
        config,
        runtime_mode=mode,
        host=overrides.host if overrides.host is not None else host,
        port=overrides.port if overrides.port is not None else port,
    )


def config_path() -> Path:
    """Config location: ``$XDG_CONFIG_HOME/mlx-tui/config.toml``, else ``~/.config``."""
    root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / "mlx-tui" / "config.toml"


CONFIG_TEMPLATE = """\
# mlx-tui config — model/host/port/runtime_mode/start_cmd/stop_cmd
# runtime_mode = "attach"  # "attach" (default) | "managed"
# host = "127.0.0.1"  # attach endpoint; managed defaults to 127.0.0.1:18080
# port = 8080
# model = "ornith-ai/Ornith-1.5-9B-MLX-4bit"
# start_cmd = "mlx_lm.server --port 8080"
# stop_cmd = "pkill -f mlx_lm.server"
# command_shell = false  # true: run start_cmd/stop_cmd via shell with $MLX_TUI_MODEL
# shell example: command_shell = true
# start_cmd = "mlx_lm.server --model \\"$MLX_TUI_MODEL\\" --port 8080"
# temperature = 0.7
# top_p = 1.0
# max_tokens = 1024
# seed = 7
# enable_thinking = false
# system = "You are a helpful assistant."
# max_ctx = 8192
# max_context = 8192  # alias for max_ctx
# swap_policy = "auto"  # "auto" | "warm" | "restart"
# Presets in this file take precedence over legacy presets.toml.
# preset = []  # explicitly disable all presets
# [[preset]]
# name = "Coding"
# system = "You are a coding assistant."
# temperature = 0.2
"""


_KEY_TYPES: dict[str, type] = {
    "model": str,
    "host": str,
    "port": int,
    "start_cmd": str,
    "stop_cmd": str,
    "command_shell": bool,
    "temperature": float,
    "top_p": float,
    "max_tokens": int,
    "seed": int,
    "enable_thinking": bool,
    "system": str,
    "max_ctx": int,
}

_RANGES = {
    "port": (1, 65535),
    "temperature": TEMPERATURE_RANGE,
    "top_p": TOP_P_RANGE,
    "max_tokens": MAX_TOKENS_RANGE,
    "max_ctx": MAX_CTX_RANGE,
    "max_context": MAX_CTX_RANGE,
}
_ENUMS = {
    "runtime_mode": ("attach", "managed"),
    "swap_policy": ("auto", "warm", "restart"),
}


def validate_config(data: dict[str, object]) -> tuple[ConfigIssue, ...]:
    """Report invalid fields without changing or clamping their values."""
    issues: list[ConfigIssue] = []
    types = {**_KEY_TYPES, "max_context": int, "runtime_mode": str, "swap_policy": str}
    for key, value in data.items():
        if key == "preset":
            continue  # The shared preset parser owns entry validation.
        expected = types.get(key)
        if expected is None:
            issues.append(ConfigIssue(key, "Unknown key; preserved on save", "warning"))
            continue
        if (expected is float and _coerce_float_strict(value) is None) or (
            expected is not float and type(value) is not expected
        ):
            issues.append(
                ConfigIssue(key, f"Must be a finite {expected.__name__}", "error")
            )
            continue
        if key in _RANGES:
            lo, hi = _RANGES[key]
            number = cast("int | float", value)
            if not lo <= number <= hi:
                issues.append(
                    ConfigIssue(key, f"Must be between {lo} and {hi}", "error")
                )
        if key in _ENUMS and value not in _ENUMS[key]:
            issues.append(
                ConfigIssue(key, f"Must be one of {', '.join(_ENUMS[key])}", "error")
            )
        if key == "host" and not cast("str", value).strip():
            issues.append(ConfigIssue(key, "Host must not be empty", "error"))
        if key == "max_context":
            issues.append(
                ConfigIssue(
                    key,
                    "Alias takes precedence over max_ctx; editing context writes max_ctx",
                    "warning",
                )
            )
    return tuple(issues)


class ConfigParseError(Exception):
    """Raised by :func:`parse_config` when the file is unreadable or invalid."""


def _clamp[T: (int, float)](v: T, lo: T, hi: T) -> T:
    return max(lo, min(hi, v))


def _coerce_float_strict(v: object) -> float | None:
    if type(v) is float:
        return v if math.isfinite(v) else None
    if type(v) is int:
        try:
            coerced = float(v)
        except OverflowError:
            return None
        return coerced if math.isfinite(coerced) else None
    return None


def _from_mapping(data: dict[str, object]) -> AppConfig:
    found: dict[str, object] = {}
    for key, expected_type in _KEY_TYPES.items():
        value = data.get(key)
        if expected_type is float:
            coerced = _coerce_float_strict(value)
            if coerced is not None:
                found[key] = coerced
        elif type(value) is expected_type:
            found[key] = value
    host = found.get("host")
    port = found.get("port")
    temp = cast("float | None", found.get("temperature"))
    if temp is not None:
        temp = _clamp(temp, *TEMPERATURE_RANGE)
    tp = cast("float | None", found.get("top_p"))
    if tp is not None:
        tp = _clamp(tp, *TOP_P_RANGE)
    mt = cast("int | None", found.get("max_tokens"))
    if mt is not None:
        mt = _clamp(mt, *MAX_TOKENS_RANGE)
    raw_mc: int | None
    if type(data.get("max_context")) is int:
        raw_mc = data.get("max_context")  # type: ignore[assignment]
    elif type(data.get("max_ctx")) is int:
        raw_mc = data.get("max_ctx")  # type: ignore[assignment]
    else:
        raw_mc = None
    mc = _clamp(raw_mc, *MAX_CTX_RANGE) if raw_mc is not None else None
    raw_policy = data.get("swap_policy")
    if raw_policy in ("auto", "warm", "restart"):
        policy = cast("SwapPolicy", raw_policy)
    else:
        policy = cast("SwapPolicy", "auto")
    raw_mode = data.get("runtime_mode")
    mode = cast(
        "RuntimeMode", raw_mode if raw_mode in ("attach", "managed") else "attach"
    )
    return AppConfig(
        model=cast("str | None", found.get("model")),
        host=cast("str", host if host is not None else "127.0.0.1"),
        port=cast("int", port if port is not None else 8080),
        start_cmd=cast("str | None", found.get("start_cmd")),
        stop_cmd=cast("str | None", found.get("stop_cmd")),
        command_shell=found.get("command_shell") is True,
        temperature=temp,
        top_p=tp,
        max_tokens=mt,
        seed=cast("int | None", found.get("seed")),
        enable_thinking=cast("bool | None", found.get("enable_thinking")),
        system=cast("str | None", found.get("system")),
        max_ctx=mc if mc is not None else DEFAULT_MAX_CTX,
        swap_policy=policy,
        runtime_mode=mode,
    )


def parse_config(path: Path | None = None) -> AppConfig:
    """Read the config file strictly; unreadable/invalid TOML raises.

    A known key is accepted iff ``type(value)`` matches exactly, so a TOML
    bool can never sneak into ``port`` (``type(True)`` is ``bool``, not
    ``int``). Rejected, absent, and unknown keys fall back to defaults.
    """
    if path is None:
        path = config_path()
    try:
        with path.open("rb") as fh:
            data: dict[str, object] = tomllib.load(fh)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigParseError(f"{path}: {exc}") from exc
    return _from_mapping(data)


def load_config(path: Path | None = None) -> AppConfig:
    """Read the config file, degrading any bad content to defaults.

    A missing file or parse error yields the all-defaults config — never a
    crash. Callers that must distinguish a broken file from an empty one
    should use :func:`parse_config` instead.
    """
    try:
        return parse_config(path)
    except ConfigParseError:
        return AppConfig()
