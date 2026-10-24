"""Configuration reload, presets, and editor integration tests."""

from __future__ import annotations

import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from textual.widgets import Input

from mlx_tui.app import MlxTuiApp
from mlx_tui.app.ui import action_edit_config, apply_chat_defaults
from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.config import AppConfig
from mlx_tui.config_io import read_config
from tests.conftest import AppHarness


async def test_preset_cycle_drives_params_and_system(  # noqa: PLR0915
    harness: AppHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace  # noqa: PLC0415

    from textual.widgets import Input as _Input  # noqa: PLC0415
    from textual.widgets import TabbedContent  # noqa: PLC0415

    from mlx_tui.chat_ui.pane import ChatPane  # noqa: PLC0415
    from mlx_tui.presets import load_presets  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    presets_file = tmp_path / "mlx-tui" / "presets.toml"
    presets_file.parent.mkdir(parents=True, exist_ok=True)
    presets_file.write_text(
        '[[preset]]\nname = "a"\nsystem = "You are A"\ntemperature = 0.2\n'
        '[[preset]]\nname = "b"\nsystem = "You are B"\ntop_p = 0.5\nmax_tokens = 512\n'
    )
    harness.app.presets = load_presets(presets_file)
    harness.app.preset_idx = -1
    harness.app.config = replace(harness.app.config, max_ctx=32768)
    harness.app.query_one(TabbedContent).active = "chat"
    await harness.pilot.pause()
    # direct action kept: pilot.press for ctrl+p was unreliable in textual 8.2.8; ctrl+n forward is now reliable but direct keeps test stable
    harness.app.action_cycle_preset()
    await harness.pilot.pause()
    assert harness.app.config.max_ctx == 32768
    assert harness.app.config.system == "You are A"
    assert harness.app.query_one("#param-temp", _Input).value == "0.2"
    assert any("preset: a" in line for line in harness.app_log_lines())
    harness.app.action_cycle_preset()
    await harness.pilot.pause()
    assert harness.app.config.max_ctx == 32768
    assert harness.app.config.system == "You are B"
    assert harness.app.query_one("#param-top-p", _Input).value == "0.5"
    assert harness.app.query_one("#param-max-tokens", _Input).value == "512"
    assert any("preset: b" in line for line in harness.app_log_lines())
    harness.app.action_cycle_preset_back()
    await harness.pilot.pause()
    assert harness.app.config.max_ctx == 32768
    assert harness.app.config.system == "You are A"

    bindings: dict[str, str] = {}
    for b in harness.app.BINDINGS:
        k = getattr(b, "key", None)
        a = getattr(b, "action", None)
        if k is None and isinstance(b, tuple):
            k, a = b[0], b[1]  # type: ignore[misc]
        if isinstance(k, str) and isinstance(a, str):
            bindings[k] = a
    assert bindings.get("ctrl+n") == "cycle_preset"
    assert bindings.get("ctrl+o") == "cycle_preset_back"
    harness.server.requests.clear()
    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "hi preset"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def reply_done(a: MlxTuiApp) -> bool:
        return len(a.query_one(ChatPane).messages) >= 2

    assert await harness.wait_for(reply_done)
    posts = [r for r in harness.server.requests if "messages" in r]
    assert posts, f"no POST; {harness.server.requests}"
    last = posts[-1]
    msgs = last.get("messages", [])
    assert isinstance(msgs, list) and len(msgs) >= 2
    assert msgs[0] == {"role": "system", "content": "You are A"}


async def test_explicit_apply_clears_removed_system_prompt(
    harness: AppHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import nullcontext  # noqa: PLC0415
    from dataclasses import replace  # noqa: PLC0415

    from mlx_tui.chat_ui.pane import ChatPane  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config_file = tmp_path / "mlx-tui" / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('system = "stale system prompt"\n')
    harness.app.config = replace(harness.app.config, system="stale system prompt")
    pane = harness.app.query_one(ChatPane)
    pane.apply_config_params(harness.app.config)
    monkeypatch.setattr(harness.app, "suspend", nullcontext)

    def fake_editor(_command: list[str], check: bool) -> SimpleNamespace:
        assert check is False
        config_file.write_text(
            f'max_tokens = 256\nmodel = "{harness.server.model_id}"\n'
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_editor)
    harness.app.action_edit_config()
    await harness.pilot.pause()

    assert harness.app.config.system == "stale system prompt"
    assert apply_chat_defaults(harness.app)
    await harness.pilot.pause()
    assert _active_config(harness.app).system is None
    assert harness.app.query_one("#param-max-tokens", Input).value == "256"

    inp = harness.app.query_one("#chat-input", ChatInput)
    inp.text = "without system"
    inp.focus()
    await harness.pilot.press("ctrl+enter")

    def reply_done(_app: MlxTuiApp) -> bool:
        return len(pane.messages) == 2

    assert await harness.wait_for(reply_done)
    posts = [request for request in harness.server.requests if "messages" in request]
    assert posts
    messages = posts[-1]["messages"]
    assert isinstance(messages, list)
    assert not any(message.get("role") == "system" for message in messages)


@contextmanager
def _stub_suspend(entered: list[bool], exited: list[bool]) -> Generator[None]:
    entered.append(True)
    try:
        yield
    finally:
        exited.append(True)


def _active_config(app: MlxTuiApp) -> AppConfig:
    """Fresh-read helper: the checker must not narrow this across mutations."""
    return app.config


def _stub_editor(
    harness: AppHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    returncode: int = 0,
    raise_oserror: bool = False,
) -> tuple[Path, dict[str, object], list[bool], list[bool]]:
    path = tmp_path / "mlx-tui.toml"
    monkeypatch.setattr("mlx_tui.app.ui.config_path", lambda: path)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "fake-editor")
    path.write_text("")
    ran: dict[str, object] = {}
    entered: list[bool] = []
    exited: list[bool] = []

    def fake_run(argv: object, **kwargs: object) -> SimpleNamespace:
        ran["argv"] = argv
        ran["kwargs"] = kwargs
        ran["suspended_during"] = (len(entered), len(exited))
        if raise_oserror:
            raise OSError("no such editor")
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(harness.app, "suspend", lambda: _stub_suspend(entered, exited))
    return path, ran, entered, exited


async def test_edit_config_missing_file_creates_template_and_reloads(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _ran, entered, exited = _stub_editor(harness, monkeypatch, tmp_path)
    path.unlink()
    assert not path.exists()
    action_edit_config(harness.app, create=True)
    await harness.pilot.pause()
    assert path.exists()
    assert "config reloaded" in harness.app_log_lines()
    assert entered == [True] and exited == [True]


async def test_edit_config_editor_argv_with_spaces(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, ran, _entered, _exited = _stub_editor(harness, monkeypatch, tmp_path)
    path.write_text('model = "a/b"\n')
    monkeypatch.setenv("EDITOR", "code --wait --new-window")
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert ran["argv"] == ["code", "--wait", "--new-window", str(path)]
    assert harness.app.config_document is not None
    assert harness.app.config_document.config.model == "a/b"
    assert "config reloaded" in harness.app_log_lines()


async def test_edit_config_suspends_around_editor(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _path, ran, entered, exited = _stub_editor(harness, monkeypatch, tmp_path)
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert entered == [True] and exited == [True]
    assert ran["suspended_during"] == (1, 0)


async def test_edit_config_editor_oserror_keeps_config(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _ran, _entered, _exited = _stub_editor(
        harness, monkeypatch, tmp_path, raise_oserror=True
    )
    path.write_text('model = "a/b"\n')
    harness.app.action_edit_config()
    await harness.pilot.pause()
    lines = harness.app_log_lines()
    assert any("config edit failed" in t and "OSError" in t for t in lines), lines
    assert "config reloaded" not in lines
    assert harness.app.config.model == harness.server.model_id


async def test_edit_config_template_oserror_keeps_config(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, ran, entered, exited = _stub_editor(harness, monkeypatch, tmp_path)
    path.unlink()
    assert not path.exists()

    def fail_template(*_args: object) -> None:
        raise PermissionError("read-only config directory")

    monkeypatch.setattr("mlx_tui.app.ui.save_config", fail_template)
    action_edit_config(harness.app, create=True)
    await harness.pilot.pause()

    lines = harness.app_log_lines()
    assert any("config edit failed" in t and "PermissionError" in t for t in lines), (
        lines
    )
    assert "config reloaded" not in lines
    assert ran == {}
    assert entered == [] and exited == []
    assert harness.app.config.model == harness.server.model_id


async def test_edit_config_malformed_editor_keeps_config(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, ran, entered, exited = _stub_editor(harness, monkeypatch, tmp_path)
    path.write_text('model = "keep/me"\n')
    monkeypatch.setenv("EDITOR", 'unterminated "quote')

    harness.app.action_edit_config()
    await harness.pilot.pause()

    lines = harness.app_log_lines()
    assert any("config edit failed" in t and "ValueError" in t for t in lines), lines
    assert "config reloaded" not in lines
    assert ran == {}
    assert entered == [] and exited == []
    assert harness.app.config.model == harness.server.model_id


async def test_edit_config_empty_editor_keeps_config(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, ran, entered, exited = _stub_editor(harness, monkeypatch, tmp_path)
    path.write_text('model = "keep/me"\n')
    monkeypatch.setenv("EDITOR", "")

    harness.app.action_edit_config()
    await harness.pilot.pause()

    lines = harness.app_log_lines()
    assert any("config edit failed" in t and "EDITOR is empty" in t for t in lines), (
        lines
    )
    assert "config reloaded" not in lines
    assert ran == {}
    assert entered == [] and exited == []
    assert harness.app.config.model == harness.server.model_id


async def test_edit_config_editor_nonzero_keeps_config(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _ran, _entered, _exited = _stub_editor(
        harness, monkeypatch, tmp_path, returncode=3
    )
    path.write_text('model = "a/b"\n')
    harness.app.action_edit_config()
    await harness.pilot.pause()
    lines = harness.app_log_lines()
    assert any("config edit failed" in t and "exited 3" in t for t in lines), lines
    assert "config reloaded" not in lines
    assert harness.app.config.model == harness.server.model_id


async def test_edit_config_malformed_toml_preserves_config(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _ran, _entered, _exited = _stub_editor(harness, monkeypatch, tmp_path)
    path.write_text('model = "keep/me"\n')
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert harness.app.config_document is not None
    assert harness.app.config_document.config.model == "keep/me"
    path.write_text("<<<")
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert harness.app.config_document is not None
    assert harness.app.config_document.config.model == "keep/me"
    assert any("config kept" in t for t in harness.app_log_lines())


async def test_edit_config_cleared_values_apply_and_presets_reset(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _ran, _entered, _exited = _stub_editor(harness, monkeypatch, tmp_path)
    path.write_text('model = "a/b"\nport = 9001\n')
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert harness.app.config_document is not None
    assert harness.app.config_document.config.model == "a/b"
    assert harness.app.config_document is not None
    assert harness.app.config_document.config.port == 9001
    harness.app.presets = []
    harness.app.preset_idx = 3
    path.write_text("port = 9002\n")
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert _active_config(harness.app).model == harness.server.model_id
    assert harness.app.config_document is not None
    assert read_config(path).config.model is None
    assert harness.app.config_document is not None
    assert harness.app.config_document.config.port == 9002
    assert harness.app.preset_idx == -1


async def test_edit_config_host_port_change_notifies_restart(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path, _ran, _entered, _exited = _stub_editor(harness, monkeypatch, tmp_path)
    path.write_text('host = "0.0.0.0"\nport = 9001\n')
    harness.app.action_edit_config()
    await harness.pilot.pause()
    assert harness.app.config_document is not None
    assert harness.app.config_document.config.host == "0.0.0.0"
    assert harness.app.config_document is not None
    assert harness.app.config_document.config.port == 9001
    assert harness.app.host == "127.0.0.1"
    assert any("restart mlx-tui" in t for t in harness.app_log_lines())


async def test_editor_reload_preserves_runtime_ownership(
    harness: AppHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dataclasses import replace  # noqa: PLC0415

    path, _ran, _entered, _exited = _stub_editor(harness, monkeypatch, tmp_path)
    before = replace(
        harness.app.config,
        start_cmd="active-start",
        stop_cmd="active-stop",
        swap_policy="warm",
    )
    harness.app.config = before
    client = harness.app._http
    manager = harness.app.managed_runtime
    path.write_text(
        'runtime_mode = "managed"\nhost = "other.test"\nport = 9000\nstart_cmd = "new-start"\nstop_cmd = "new-stop"\ncommand_shell = true\nswap_policy = "restart"\n[[preset]]\nname = "canonical"\n'
    )
    harness.app.action_edit_config()
    await harness.pilot.pause()
    for key in (
        "runtime_mode",
        "host",
        "port",
        "start_cmd",
        "stop_cmd",
        "command_shell",
        "swap_policy",
    ):
        assert getattr(harness.app.config, key) == getattr(before, key)
    assert harness.app._http is client
    assert harness.app.managed_runtime is manager
    assert harness.app.config_document is not None
    assert harness.app.config_document.config.runtime_mode == "managed"
    assert [p.name for p in harness.app.presets] == ["canonical"]
    assert any("restart mlx-tui" in line for line in harness.app_log_lines())
