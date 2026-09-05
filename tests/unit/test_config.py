"""Unit tests for mlx_tui.config against tmp_path TOML files."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mlx_tui.config import (
    AppConfig,
    ConfigParseError,
    config_path,
    load_config,
    parse_config,
    write_template,
)


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(content)
    return path


def test_write_template_creates_parents_and_default_content(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "config.toml"

    write_template(path)

    assert path.exists()
    assert path.read_bytes().startswith(b"# mlx-tui config")
    assert b'start_cmd = "mlx_lm.server --port 8080"' in path.read_bytes()


def test_write_template_parses_as_all_defaults(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "config.toml"

    write_template(path)

    # Every line is commented out, so parsing yields all-default values.
    assert parse_config(path) == AppConfig()


def test_missing_file_yields_all_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path / "absent.toml") == AppConfig()


def test_string_keys_round_trip(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            "\n".join(
                [
                    'model = "ornith-ai/Ornith-1.5-9B-MLX-4bit"',
                    'start_cmd = "mlx_lm.server --port 8080"',
                    'stop_cmd = "pkill -f mlx_lm.server"',
                    'pidfile = "/tmp/mlx.pid"',
                ]
            ),
        )
    )
    assert cfg.model == "ornith-ai/Ornith-1.5-9B-MLX-4bit"
    assert cfg.start_cmd == "mlx_lm.server --port 8080"
    assert cfg.stop_cmd == "pkill -f mlx_lm.server"
    assert cfg.pidfile == "/tmp/mlx.pid"


def test_host_and_port_round_trip(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, 'host = "0.0.0.0"\nport = 9001\n'))
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9001


@pytest.mark.parametrize(
    ("toml_line", "key"),
    [
        ('port = "not-a-number"', "port"),
        ("port = true", "port"),
        ("port = 8080.5", "port"),
        ("model = 123", "model"),
        ("host = [1, 2]", "host"),
    ],
)
def test_wrong_type_degrades_to_default(
    tmp_path: Path, toml_line: str, key: str
) -> None:
    cfg = load_config(_write(tmp_path, toml_line))
    defaults = AppConfig()
    assert getattr(cfg, key) == getattr(defaults, key)


def test_unknown_key_ignored(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, 'bogus = "x"\nport = 9002\n'))
    assert cfg.port == 9002
    assert not hasattr(cfg, "bogus")


def test_malformed_toml_degrades_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"<<<")
    assert load_config(path) == AppConfig()


def test_parse_config_round_trips(tmp_path: Path) -> None:
    cfg = parse_config(_write(tmp_path, 'port = 9003\nstart_cmd = "mlx_lm.server"\n'))
    assert cfg == AppConfig(port=9003, start_cmd="mlx_lm.server")


def test_parse_config_malformed_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigParseError):
        parse_config(_write(tmp_path, "<<<"))


def test_parse_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigParseError):
        parse_config(tmp_path / "absent.toml")


def test_parse_config_wrong_type_degrades_that_key_only(tmp_path: Path) -> None:
    cfg = parse_config(_write(tmp_path, 'port = true\nstart_cmd = "mlx_lm.server"\n'))
    assert cfg.port == 8080
    assert cfg.start_cmd == "mlx_lm.server"


def test_config_path_honours_xdg_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "mlx-tui" / "config.toml"


def test_config_path_falls_back_to_home_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_path() == Path.home() / ".config" / "mlx-tui" / "config.toml"


def test_default_max_ctx_is_8000() -> None:
    assert AppConfig().max_ctx == 8192


def test_max_ctx_default_is_8192() -> None:
    assert AppConfig().max_ctx == 8192


def test_max_ctx_round_trip(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, "max_ctx = 32000\n"))
    assert cfg.max_ctx == 32000


def test_max_context_alias_wins(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, "max_context = 16384\nmax_ctx = 4096\n"))
    assert cfg.max_ctx == 16384


def test_max_ctx_clamps_and_bool_rejected(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, "max_ctx = true\n"))
    assert cfg.max_ctx == 8192
    cfg2 = load_config(_write(tmp_path, "max_ctx = 999999\n"))
    assert cfg2.max_ctx == 131072
    cfg3 = load_config(_write(tmp_path, "max_context = 999999\n"))
    assert cfg3.max_ctx == 131072
    cfg4 = load_config(_write(tmp_path, "max_ctx = 512\n"))
    assert cfg4.max_ctx == 1024


def test_swap_policy_default_is_auto() -> None:
    assert AppConfig().swap_policy == "auto"


@pytest.mark.parametrize("policy", ["auto", "warm", "restart"])
def test_swap_policy_round_trip(tmp_path: Path, policy: str) -> None:
    cfg = load_config(_write(tmp_path, f'swap_policy = "{policy}"\n'))
    assert cfg.swap_policy == policy


@pytest.mark.parametrize(
    "toml_line",
    [
        'swap_policy = "sometimes"',
        'swap_policy = ""',
        "swap_policy = 123",
        "swap_policy = true",
    ],
)
def test_swap_policy_invalid_falls_back_to_auto(tmp_path: Path, toml_line: str) -> None:
    cfg = load_config(_write(tmp_path, toml_line))
    assert cfg.swap_policy == "auto"


def test_replace_preserves_unrelated_app_config_fields() -> None:
    cfg = AppConfig(
        model="org/model",
        host="example.test",
        port=9000,
        start_cmd="start",
        stop_cmd="stop",
        pidfile="/tmp/mlx.pid",
        temperature=0.2,
        top_p=0.8,
        max_tokens=512,
        system="old",
        max_ctx=32768,
        swap_policy="restart",
    )

    updated = replace(cfg, system=None, max_tokens=1024)

    assert updated == AppConfig(
        model="org/model",
        host="example.test",
        port=9000,
        start_cmd="start",
        stop_cmd="stop",
        pidfile="/tmp/mlx.pid",
        temperature=0.2,
        top_p=0.8,
        max_tokens=1024,
        system=None,
        max_ctx=32768,
        swap_policy="restart",
    )
