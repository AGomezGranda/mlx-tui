"""Loading of the single TOML config file (~/.config/mlx-tui/config.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

SwapPolicy = Literal["auto", "warm", "restart"]


@dataclass(frozen=True)
class AppConfig:
    """Everything the TUI reads from disk; every field degrades to a default."""

    model: str | None = None
    host: str = "127.0.0.1"
    port: int = 8080
    start_cmd: str | None = None
    stop_cmd: str | None = None
    pidfile: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    system: str | None = None
    max_ctx: int = 8192
    swap_policy: SwapPolicy = "auto"


def config_path() -> Path:
    """Config location: ``$XDG_CONFIG_HOME/mlx-tui/config.toml``, else ``~/.config``."""
    root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / "mlx-tui" / "config.toml"


CONFIG_TEMPLATE = """\
# mlx-tui config — model/host/port/start_cmd/stop_cmd/pidfile
# model = "ornith-ai/Ornith-1.5-9B-MLX-4bit"
# start_cmd = "mlx_lm.server --port 8080"
# stop_cmd = "pkill -f mlx_lm.server"
# pidfile = "/tmp/mlx-server.pid"
# temperature = 0.7
# top_p = 1.0
# max_tokens = 1024
# system = "You are a helpful assistant."
# max_ctx = 8192
# max_context = 8192  # alias for max_ctx
# swap_policy = "auto"  # "auto" | "warm" | "restart"
"""


def write_template(path: Path) -> None:
    """Create parent directories and write the commented default config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE)


_KEY_TYPES: dict[str, type] = {
    "model": str,
    "host": str,
    "port": int,
    "start_cmd": str,
    "stop_cmd": str,
    "pidfile": str,
    "temperature": float,
    "top_p": float,
    "max_tokens": int,
    "system": str,
    "max_ctx": int,
}


class ConfigParseError(Exception):
    """Raised by :func:`parse_config` when the file is unreadable or invalid."""


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _clamp_int(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _coerce_float_strict(v: object) -> float | None:
    if type(v) is float:
        return v
    if type(v) is int:
        return float(v)
    return None


def _from_mapping(data: dict[str, object]) -> AppConfig:
    found: dict[str, object] = {}
    for key, expected_type in _KEY_TYPES.items():
        value = data.get(key)
        if type(value) is expected_type:
            found[key] = value
        elif expected_type is float and type(value) is int:
            # TOML may emit 1 not 1.0 for temperature/top_p; coerce int->float but reject bool
            found[key] = float(value)
    host = found.get("host")
    port = found.get("port")
    temp = cast("float | None", found.get("temperature"))
    if temp is not None:
        temp = _clamp(temp, 0.0, 2.0)
    tp = cast("float | None", found.get("top_p"))
    if tp is not None:
        tp = _clamp(tp, 0.0, 1.0)
    mt = cast("int | None", found.get("max_tokens"))
    if mt is not None:
        mt = _clamp_int(mt, 1, 16384)
    raw_mc: int | None
    if type(data.get("max_context")) is int:
        raw_mc = data.get("max_context")  # type: ignore[assignment]
    elif type(data.get("max_ctx")) is int:
        raw_mc = data.get("max_ctx")  # type: ignore[assignment]
    else:
        raw_mc = None
    mc = _clamp_int(raw_mc, 1024, 131072) if raw_mc is not None else None
    raw_policy = data.get("swap_policy")
    if raw_policy in ("auto", "warm", "restart"):
        policy = cast("SwapPolicy", raw_policy)
    else:
        policy = cast("SwapPolicy", "auto")
    return AppConfig(
        model=cast("str | None", found.get("model")),
        host=cast("str", host if host is not None else "127.0.0.1"),
        port=cast("int", port if port is not None else 8080),
        start_cmd=cast("str | None", found.get("start_cmd")),
        stop_cmd=cast("str | None", found.get("stop_cmd")),
        pidfile=cast("str | None", found.get("pidfile")),
        temperature=temp,
        top_p=tp,
        max_tokens=mt,
        system=cast("str | None", found.get("system")),
        max_ctx=mc if mc is not None else 8192,
        swap_policy=policy,
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
    except (OSError, tomllib.TOMLDecodeError) as exc:
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
