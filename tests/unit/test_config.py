"""Unit tests for mlx_tui.config against tmp_path TOML files."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from mlx_tui.app import MlxTuiApp
from mlx_tui.app import main as app_main
from mlx_tui.config import (
    AppConfig,
    ConfigParseError,
    _coerce_float_strict,
    config_path,
    load_config,
    parse_config,
)
from mlx_tui.config_io import ConfigDocument, ConfigWriteError, read_config, save_config


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(content)
    return path


@pytest.mark.parametrize("mode", ["attach", "managed"])
@pytest.mark.parametrize(
    ("host", "port"),
    [(None, None), ("cli.test", None), (None, 8080), ("cli.test", 9000)],
)
@pytest.mark.parametrize("override_mode", [None, "attach", "managed"])
def test_resolve_launch_precedence(
    mode: str, host: str | None, port: int | None, override_mode: str | None
) -> None:
    from mlx_tui.config import LaunchOverrides, resolve_launch  # noqa: PLC0415

    saved = AppConfig(host="saved.test", port=9090, runtime_mode=mode)  # type: ignore[arg-type]
    overrides = LaunchOverrides(host, port, override_mode)  # type: ignore[arg-type]
    resolved = resolve_launch(saved, overrides)
    effective_mode = override_mode or mode
    assert resolved.runtime_mode == effective_mode
    assert resolved.host == (
        host or ("127.0.0.1" if effective_mode == "managed" else "saved.test")
    )
    assert resolved.port == (
        port if port is not None else (18080 if effective_mode == "managed" else 9090)
    )
    assert saved == AppConfig(host="saved.test", port=9090, runtime_mode=mode)  # type: ignore[arg-type]


def test_save_new_config_creates_parents_and_template(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "config.toml"

    save_config(read_config(path), {})

    assert path.exists()
    assert path.read_bytes().startswith(b"# mlx-tui config")
    assert b'start_cmd = "mlx_lm.server --port 8080"' in path.read_bytes()


def test_saved_template_parses_as_all_defaults(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "config.toml"

    save_config(read_config(path), {})

    # Every line is commented out, so parsing yields all-default values.
    assert parse_config(path) == AppConfig()


def test_missing_file_yields_all_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path / "absent.toml") == AppConfig()


def test_invalid_utf8_uses_config_recovery(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b'host = "\xff"')
    with pytest.raises(ConfigParseError) as caught:
        parse_config(path)
    assert isinstance(caught.value.__cause__, UnicodeDecodeError)
    assert load_config(path) == AppConfig()


def test_string_keys_round_trip(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            "\n".join(
                [
                    'model = "ornith-ai/Ornith-1.5-9B-MLX-4bit"',
                    'start_cmd = "mlx_lm.server --port 8080"',
                    'stop_cmd = "pkill -f mlx_lm.server"',
                ]
            ),
        )
    )
    assert cfg.model == "ornith-ai/Ornith-1.5-9B-MLX-4bit"
    assert cfg.start_cmd == "mlx_lm.server --port 8080"
    assert cfg.stop_cmd == "pkill -f mlx_lm.server"


def test_host_and_port_round_trip(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, 'host = "0.0.0.0"\nport = 9001\n'))
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9001


def test_optional_request_identity_fields_require_exact_types(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, "seed = 7\nenable_thinking = false\n"))
    assert cfg.seed == 7
    assert cfg.enable_thinking is False

    invalid = load_config(_write(tmp_path, 'seed = true\nenable_thinking = "false"\n'))
    assert invalid.seed is None
    assert invalid.enable_thinking is None


def test_runtime_mode_defaults_to_attach_and_accepts_managed(
    tmp_path: Path,
) -> None:
    assert load_config(_write(tmp_path, 'runtime_mode = "managed"\n')).runtime_mode == (
        "managed"
    )


def test_save_missing_snapshot_does_not_overwrite_late_creation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "config.toml"

    document = read_config(path)
    save_config(
        document, {"runtime_mode": "managed", "host": "127.0.0.1", "port": 18080}
    )
    original = path.read_text()
    assert 'runtime_mode = "managed"' in original
    with pytest.raises(ConfigWriteError, match="Reload"):
        save_config(document, {"runtime_mode": "attach", "host": "0.0.0.0", "port": 9})
    assert path.read_text() == original
    assert load_config(_write(tmp_path, 'runtime_mode = "other"\n')).runtime_mode == (
        "attach"
    )


def test_new_attach_config_can_start_bundled_server(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_config(
        read_config(path),
        {
            "runtime_mode": "attach",
            "host": "127.0.0.1",
            "port": 8081,
            "start_cmd": "mlx_lm.server --port 8081",
        },
    )
    assert load_config(path).start_cmd == "mlx_lm.server --port 8081"


def test_invalid_runtime_mode_does_not_change_attach_settings(tmp_path: Path) -> None:
    cfg = load_config(
        _write(
            tmp_path,
            'runtime_mode = true\nhost = "127.0.0.1"\nport = 9002\nstart_cmd = "server"\n',
        )
    )
    assert cfg.runtime_mode == "attach"
    assert (cfg.host, cfg.port, cfg.start_cmd) == ("127.0.0.1", 9002, "server")


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


@pytest.mark.parametrize("value", ["nan", "inf", "+inf", "-inf"])
@pytest.mark.parametrize("key", ["temperature", "top_p"])
def test_nonfinite_float_degrades_to_absent(
    tmp_path: Path, key: str, value: str
) -> None:
    cfg = load_config(_write(tmp_path, f"{key} = {value}\n"))
    assert getattr(cfg, key) is None


def test_float_coercion_rejects_integer_overflow() -> None:
    assert _coerce_float_strict(10**1000) is None


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


def test_config_path_flag_has_no_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "absent"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setattr(sys, "argv", ["mlx-tui", "--config-path"])

    def unexpected(*_args: object, **_kwargs: object) -> None:
        pytest.fail("--config-path must not read config, construct the app or diagnose")

    monkeypatch.setattr("mlx_tui.app.cli.read_config", unexpected)
    monkeypatch.setattr("mlx_tui.app.cli.MlxTuiApp", unexpected)
    monkeypatch.setattr("mlx_tui.app.cli.write_diagnostics", unexpected)
    app_main()
    assert capsys.readouterr().out == f"{root / 'mlx-tui' / 'config.toml'}\n"
    assert not root.exists()


@pytest.mark.parametrize(
    "flags",
    [
        ["--diagnostics", "report.json"],
        ["--managed"],
        ["--attach"],
        ["--host", "example.test"],
        ["--port", "8080"],
    ],
)
def test_config_path_flag_rejects_other_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
) -> None:
    root = tmp_path / "absent"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    monkeypatch.setattr(sys, "argv", ["mlx-tui", "--config-path", *flags])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as caught:
        app_main()
    assert caught.value.code == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert not root.exists()
    assert not (tmp_path / "report.json").exists()


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


def test_command_shell_default_is_false() -> None:
    assert AppConfig().command_shell is False


def test_command_shell_round_trip(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, "command_shell = true\n"))
    assert cfg.command_shell is True
    cfg2 = load_config(_write(tmp_path, "command_shell = false\n"))
    assert cfg2.command_shell is False


@pytest.mark.parametrize(
    "toml_line",
    [
        'command_shell = "true"',
        "command_shell = 1",
        "command_shell = 123",
    ],
)
def test_command_shell_wrong_type_degrades_to_false(
    tmp_path: Path, toml_line: str
) -> None:
    cfg = load_config(_write(tmp_path, toml_line))
    assert cfg.command_shell is False


def test_saved_template_mentions_command_shell(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "config.toml"

    save_config(read_config(path), {})

    content = path.read_bytes()
    assert b"command_shell" in content
    assert b"MLX_TUI_MODEL" in content
    assert b"enable_thinking" in content
    assert b'runtime_mode = "attach"' in content


def test_replace_preserves_unrelated_app_config_fields() -> None:
    cfg = AppConfig(
        model="org/model",
        host="example.test",
        port=9000,
        start_cmd="start",
        stop_cmd="stop",
        command_shell=True,
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
        command_shell=True,
        temperature=0.2,
        top_p=0.8,
        max_tokens=1024,
        system=None,
        max_ctx=32768,
        swap_policy="restart",
    )


def _stub_main(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], cfg: AppConfig
) -> dict[str, object]:
    seen: dict[str, object] = {}
    monkeypatch.setattr(sys, "argv", argv)
    document = replace(read_config(), config=cfg)

    def fake_read(_path: Path) -> ConfigDocument:
        return document

    monkeypatch.setattr("mlx_tui.app.cli.read_config", fake_read)

    def fake_run(self: MlxTuiApp) -> None:
        seen["app"] = self

    monkeypatch.setattr(MlxTuiApp, "run", fake_run)
    return seen


def test_main_uses_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AppConfig(host="127.0.0.1", port=8080)
    seen = _stub_main(monkeypatch, ["mlx-tui"], cfg)
    app_main()
    app = seen["app"]
    assert isinstance(app, MlxTuiApp)
    assert app.host == "127.0.0.1"
    assert app.port == 8080
    assert app.config == cfg


def test_main_explicit_host_port_win_over_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = AppConfig(host="127.0.0.1", port=8080)
    seen = _stub_main(
        monkeypatch, ["mlx-tui", "--host", "0.0.0.0", "--port", "1234"], cfg
    )
    app_main()
    app = seen["app"]
    assert isinstance(app, MlxTuiApp)
    assert app.host == "0.0.0.0"
    assert app.port == 1234


@pytest.mark.parametrize(
    "flags,expected",
    [
        (["--managed"], ("127.0.0.1", 18080)),
        (["--managed", "--host", "cli.test"], ("cli.test", 18080)),
        (["--managed", "--port", "8080"], ("127.0.0.1", 8080)),
        (["--attach"], ("saved.test", 9090)),
    ],
)
def test_main_reads_once_and_resolves_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: list[str],
    expected: tuple[str, int],
) -> None:
    path = _write(tmp_path, 'host = "saved.test"\nport = 9090\n')
    document = read_config(path)
    seen = _stub_main(monkeypatch, ["mlx-tui", *flags], document.config)
    calls: list[Path] = []

    def read_once(path: Path) -> ConfigDocument:
        calls.append(path)
        return document

    monkeypatch.setattr("mlx_tui.app.cli.read_config", read_once)
    app_main()
    app = seen["app"]
    assert isinstance(app, MlxTuiApp)
    assert (app.host, app.port) == expected
    assert (app.launch_config.host, app.launch_config.port) == expected
    assert app.config_document is document
    assert len(calls) == 1
    assert path.read_bytes() == document.source


def test_app_document_resolves_endpoint_without_constructor_rewrite(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, 'runtime_mode = "managed"\nport = 9090\n')
    app = MlxTuiApp(config_document=read_config(path))
    try:
        assert (app.host, app.port) == ("127.0.0.1", 18080)
        explicit = MlxTuiApp(host="127.0.0.1", port=8080, config=app.config)
        try:
            assert explicit.port == 8080
        finally:
            if explicit.managed_runtime is not None:
                explicit.managed_runtime.close()
    finally:
        if app.managed_runtime is not None:
            app.managed_runtime.close()


def test_main_help_exits_without_launch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _stub_main(monkeypatch, ["mlx-tui", "--help"], AppConfig())
    with pytest.raises(SystemExit) as excinfo:
        app_main()
    assert excinfo.value.code == 0
    assert "app" not in seen
    assert "usage" in capsys.readouterr().out


def test_main_invalid_port_exits_without_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _stub_main(monkeypatch, ["mlx-tui", "--port", "abc"], AppConfig())
    with pytest.raises(SystemExit) as excinfo:
        app_main()
    assert excinfo.value.code == 2
    assert "app" not in seen
