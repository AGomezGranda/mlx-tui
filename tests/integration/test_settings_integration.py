"""Mounted Settings workflows use saved defaults independently of active work."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from textual.widgets import Button, Checkbox, Input, Select, Static, TextArea

import mlx_tui.app.ui as app_ui
import mlx_tui.chat_ui.persistence as chat_persistence
from mlx_tui.app import MlxTuiApp
from mlx_tui.attachments import read_attachment
from mlx_tui.chat_ui.widgets import ChatInput
from mlx_tui.config import AppConfig, ConfigParseError, LaunchOverrides
from mlx_tui.config_io import read_config
from mlx_tui.confirm import ConfirmScreen
from mlx_tui.operations import OperationKind
from mlx_tui.params import ParamsPane
from mlx_tui.sessions.store import load_session, session_dir
from tests.conftest import AppHarness


async def test_settings_save_cancel_and_reopen(
    harness: AppHarness, tmp_path: Path
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("# keep this comment\ntemperature = 0.2\n")
    harness.app.config_document = read_config(path)
    harness.app.config = replace(harness.app.config, temperature=0.9)
    before = harness.app.config
    await harness.pilot.press("ctrl+g")
    screen = harness.app.screen
    field = screen.query_one("#settings-temperature", Input)
    assert field.value == "0.2"
    field.focus()
    await harness.pilot.press("ctrl+a", "0", ".", "4")
    await harness.pilot.press("escape", "y")
    assert path.read_text() == "# keep this comment\ntemperature = 0.2\n"
    assert harness.app.config == before

    await harness.pilot.press("ctrl+g")
    screen = harness.app.screen
    screen.query_one("#settings-temperature", Input).value = "0.4"
    screen.query_one("#settings-save", Button).focus()
    await harness.pilot.press("enter")
    assert read_config(path).config.temperature == 0.4
    assert "# keep this comment" in path.read_text()
    assert harness.app.config == before
    await harness.pilot.press("escape", "ctrl+g")
    assert harness.app.screen.query_one("#settings-temperature", Input).value == "0.4"


async def test_settings_failed_save_retains_draft(
    harness: AppHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("temperature = 0.2\n")
    harness.app.config_document = read_config(path)
    before = harness.app.config
    await harness.pilot.press("ctrl+g")
    screen = harness.app.screen
    screen.query_one("#settings-temperature", Input).value = "0.3"

    def fail(*_args: object) -> None:
        from mlx_tui.config_io import ConfigWriteError  # noqa: PLC0415

        raise ConfigWriteError("disk unavailable")

    monkeypatch.setattr("mlx_tui.settings_screen.save_config", fail)
    screen.query_one("#settings-save", Button).press()
    await harness.pilot.pause()
    assert "disk unavailable" in str(screen.query_one("#settings-status").render())
    assert screen.query_one("#settings-temperature", Input).value == "0.3"
    assert path.read_text() == "temperature = 0.2\n"
    assert harness.app.config == before


@pytest.mark.parametrize(
    "choice, port", [("attach", 9042), ("managed", 18080), ("cancel", 8080)]
)
async def test_first_run_uses_saved_endpoint_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, choice: str, port: int
) -> None:
    path = tmp_path / "mlx-tui" / "config.toml"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def no_sampler(_app: MlxTuiApp) -> None:
        pass

    monkeypatch.setattr("mlx_tui.app.polling.start_resource_sampler", no_sampler)
    app = MlxTuiApp(config_document=read_config(path), needs_setup=True)
    starts: list[AppConfig] = []
    start = app._start_runtime_services

    def record(config: AppConfig) -> None:
        starts.append(config)
        start(config)

    monkeypatch.setattr(app, "_start_runtime_services", record)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert starts == []
        assert not hasattr(app, "_http")
        assert app.managed_runtime is None
        screen = app.screen
        if choice == "cancel":
            await pilot.press("escape")
        else:
            screen.query_one("#settings-port", Input).value = "9042"
            screen.query_one("#settings-runtime_mode", Select).value = choice
            screen.query_one("#settings-save", Button).press()
            await pilot.pause()
        assert len(starts) == 1
        assert app.port == port
        assert app.runtime_services_started
        assert (app.managed_runtime is not None) == (choice == "managed")
        assert path.exists() == (choice != "cancel")
        if choice == "attach":
            assert app.config.start_cmd == "mlx_lm.server --host 127.0.0.1 --port 9042"
            assert not app.config.command_shell
        await pilot.press("ctrl+g", "escape")
        assert len(starts) == 1


@pytest.mark.parametrize("navigation", ["escape", "reload", "open"])
async def test_settings_validation_and_dirty_navigation(
    harness: AppHarness, tmp_path: Path, navigation: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("temperature = 0.2\nseed = 7\nenable_thinking = true\n")
    harness.app.config_document = read_config(path)
    await harness.pilot.press("ctrl+g")
    screen = harness.app.screen
    field = screen.query_one("#settings-temperature", Input)
    field.value = "nan"
    screen.query_one("#settings-save", Button).press()
    await harness.pilot.pause()
    assert field.has_focus
    assert screen.query_one("#settings-error-temperature", Static).display
    assert "finite" in str(screen.query_one("#settings-error-temperature").render())
    if navigation == "escape":
        await harness.pilot.press("escape")
    else:
        screen.query_one(f"#settings-{navigation}", Button).press()
        await harness.pilot.pause()
    assert isinstance(harness.app.screen, ConfirmScreen)
    await harness.pilot.press("n")
    assert harness.app.screen is screen
    assert field.value == "nan"
    field.value = "0"
    screen.query_one("#settings-seed", Input).value = ""
    screen.query_one("#settings-enable_thinking", Select).value = "off"
    screen.query_one("#settings-save", Button).press()
    await harness.pilot.pause()
    saved = read_config(path).config
    assert saved.temperature == 0
    assert saved.seed is None
    assert saved.enable_thinking is False


async def test_invalid_startup_can_be_repaired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import nullcontext  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "mlx-tui" / "config.toml"
    path.parent.mkdir()
    path.write_text("<<<")
    with pytest.raises(ConfigParseError) as caught:
        read_config(path)
    app = MlxTuiApp(config_error=str(caught.value))
    monkeypatch.setattr(app, "suspend", nullcontext)
    monkeypatch.setenv("VISUAL", "repair-editor --wait")
    commands: list[list[str]] = []

    def repair(argv: list[str], *, check: bool) -> SimpleNamespace:
        assert not check
        commands.append(argv)
        path.write_text("port = 9043\n")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("mlx_tui.app.ui.subprocess.run", repair)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert not app.runtime_services_started
        assert screen.query_one("#settings-save", Button).disabled
        assert path.read_text() == "<<<"
        screen.query_one("#settings-open", Button).press()
        await pilot.pause()
        assert commands == [["repair-editor", "--wait", str(path)]]
        screen.query_one("#settings-reload", Button).press()
        await pilot.pause()
        assert not screen.query_one("#settings-save", Button).disabled
        screen.query_one("#settings-save", Button).press()
        await pilot.pause()
        assert app.runtime_services_started
        assert app.port == 9043


async def test_runtime_setup_replaces_settings_modal(
    harness: AppHarness, tmp_path: Path
) -> None:
    from mlx_tui.setup_screen import SetupScreen  # noqa: PLC0415

    path = tmp_path / "config.toml"
    path.write_text("")
    harness.app.config_document = read_config(path)
    await harness.pilot.press("ctrl+g")
    harness.app.screen.query_one("#settings-runtime", Button).press()
    await harness.pilot.pause()
    assert isinstance(harness.app.screen, SetupScreen)
    assert len(harness.app.screen_stack) == 2
    harness.app.screen.query_one("#setup-settings", Button).press()
    await harness.pilot.pause()
    assert harness.app.screen.query_one("#settings-save", Button)
    assert len(harness.app.screen_stack) == 2


async def test_local_start_requires_loopback_and_preserves_custom_command(
    harness: AppHarness, tmp_path: Path
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('start_cmd = "custom --port 8080"\ncommand_shell = true\n')
    harness.app.config_document = read_config(path)
    await harness.pilot.press("ctrl+g")
    screen = harness.app.screen
    assert screen.query_one("#settings-local-start", Checkbox).disabled
    screen.query_one("#settings-host", Input).value = "example.test"
    screen.query_one("#settings-port", Input).value = "9001"
    screen.query_one("#settings-save", Button).press()
    await harness.pilot.pause()
    assert read_config(path).config.start_cmd == "custom --port 8080"
    assert read_config(path).config.command_shell
    assert "arguments must match" in str(
        screen.query_one("#settings-command", Static).render()
    )


async def test_deferred_runtime_actions_do_not_contact_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mlx_tui.app import polling  # noqa: PLC0415
    from mlx_tui.chat_ui.pane import ChatPane  # noqa: PLC0415
    from mlx_tui.chat_ui.widgets import ChatInput  # noqa: PLC0415
    from mlx_tui.models_pane import ModelsPane  # noqa: PLC0415
    from tests.integration.model_helpers import ROW  # noqa: PLC0415

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = MlxTuiApp(needs_setup=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.action_cold_start()
        await polling._poll(app)
        chat = app.query_one(ChatPane)
        composer = chat.query_one("#chat-input", ChatInput)
        composer.text = "pending setup"
        chat._send_pressed()
        assert not chat.has_live_turn
        pane = app.query_one(ModelsPane)
        pane._start_warm(ROW)
        assert not app.operations.is_busy
        assert not hasattr(app, "_http")


async def test_apply_uses_saved_request_defaults_and_guards(
    harness: AppHarness, tmp_path: Path
) -> None:
    from mlx_tui.operations import OperationKind  # noqa: PLC0415

    path = tmp_path / "config.toml"
    path.write_text(
        'temperature = 0.1\nsystem = "saved"\nseed = 0\nenable_thinking = false\n'
    )
    harness.app.config_document = read_config(path)
    before = harness.app.config
    chat = harness.chat_pane()
    await harness.pilot.press("ctrl+g")
    button = harness.app.screen.query_one("#settings-apply", Button)
    assert not button.disabled
    assert harness.app.operations.try_acquire(OperationKind.COMPARING)
    await harness.pilot.pause(0.3)
    assert button.disabled
    harness.app.operations.release(OperationKind.COMPARING)
    chat._read_only = True
    await harness.pilot.pause(0.3)
    assert button.disabled
    chat._read_only = False
    await harness.pilot.pause(0.3)
    button.press()
    await harness.pilot.pause()
    assert harness.app.config.temperature == 0.1
    assert harness.app.config.system == "saved"
    assert harness.app.config.model is None
    assert harness.app.server_identity.selected_model is None
    assert harness.app.config.seed == 0
    assert harness.app.config.enable_thinking is False
    assert harness.app.config.port == before.port
    assert not harness.server.requests


async def test_save_and_reload_do_not_change_active_chat(  # noqa: PLR0915
    harness: AppHarness, tmp_path: Path
) -> None:
    app = harness.app
    chat = harness.chat_pane()
    composer = chat.query_one("#chat-input", ChatInput)
    composer.text = "historical request"
    chat._send_pressed()
    assert await harness.wait_for(lambda _app: len(chat.messages) == 2)
    assert await chat_persistence._save_session_now(chat)
    assert chat._session is not None
    attempts = chat._session.attempts
    attachment = tmp_path / "draft.txt"
    attachment.write_text("retained attachment")
    chat._draft_attachments = (read_attachment(attachment),)
    composer.text = "unsent draft"
    await harness.pilot.pause()
    active = app.config
    current = chat.capture_request_settings()
    identity = app.server_identity
    posts = len(harness.server.requests)
    path = tmp_path / "config.toml"
    path.write_text('model = "saved/model"\ntemperature = 0.2\n')
    app.config_document = read_config(path)
    await harness.pilot.press("ctrl+g")
    screen = app.screen
    screen.query_one("#settings-temperature", Input).value = "0.3"
    screen.query_one("#settings-system", TextArea).load_text("saved system")
    screen.query_one("#settings-save", Button).press()
    await harness.pilot.pause()
    path.write_text(
        'model = "next/model"\nsystem = "reloaded system"\ntemperature = 0.4\n'
        "top_p = 0.8\nmax_tokens = 64\nmax_ctx = 16384\nseed = 0\n"
        'enable_thinking = false\nruntime_mode = "managed"\n'
    )
    screen.query_one("#settings-reload", Button).press()
    await harness.pilot.pause()
    document = app.config_document
    assert document is not None
    assert app.config == active
    assert chat.capture_request_settings() == current
    assert app.server_identity == identity
    assert composer.text == "unsent draft"
    assert chat._draft_attachments == (read_attachment(attachment),)
    assert chat._session.attempts == attempts
    assert len(harness.server.requests) == posts

    app.active_profile_id = app.profile_entries[0].profile.id
    app.active_profile_modified = False
    assert app_ui.apply_chat_defaults(app)
    assert app.active_profile_modified
    assert chat._session.settings.profile_modified
    await harness.pilot.pause()
    expected = replace(
        active,
        model="next/model",
        system="reloaded system",
        temperature=0.4,
        top_p=0.8,
        max_tokens=64,
        max_ctx=16384,
        seed=0,
        enable_thinking=False,
    )
    assert app.config == expected
    assert app.effective_model() == "next/model"
    assert chat.query_one(ParamsPane).read_values() == (0.4, 0.8, 64)
    assert app.config_document is document
    assert chat._session.attempts == attempts
    assert composer.text == "unsent draft"
    assert chat._draft_attachments == (read_attachment(attachment),)
    assert len(harness.server.requests) == posts
    assert await chat_persistence._save_session_now(chat)
    stored = load_session(
        session_dir() / f"{chat._session.session_id}.json", chat._session_lock
    )
    assert stored.settings.temperature == 0.4
    assert stored.settings.system == "reloaded system"
    assert stored.settings.profile_modified
    assert stored.attempts == attempts
    assert path.read_bytes() == document.source

    # Clearing saved optional fields resets controls and explicit selection.
    path.write_text("")
    screen.query_one("#settings-reload", Button).press()
    await harness.pilot.pause()
    screen.query_one("#settings-apply", Button).press()
    await harness.pilot.pause()
    assert app.config.model is None
    assert app.effective_model() is None
    assert app.config.system is None
    assert chat.query_one(ParamsPane).read_values() == (0.7, 1.0, 1024)
    assert len(harness.server.requests) == posts


@pytest.mark.parametrize(
    "guard",
    [
        OperationKind.CHATTING,
        OperationKind.COMPARING,
        OperationKind.INSTALLING,
        OperationKind.LOADING,
        OperationKind.RESTARTING,
        "read_only",
        "reconcile",
    ],
)
async def test_apply_defaults_respects_operation_and_session_guards(
    harness: AppHarness, tmp_path: Path, guard: OperationKind | str
) -> None:
    app = harness.app
    chat = harness.chat_pane()
    path = tmp_path / "config.toml"
    path.write_text("temperature = 0.2\n")
    app.config_document = read_config(path)
    before = app.config
    await harness.pilot.press("ctrl+g")
    if isinstance(guard, OperationKind):
        assert app.operations.try_acquire(guard)
    elif guard == "read_only":
        chat._read_only = True
    else:
        chat._pending_reconcile = chat.capture_request_settings()
    try:
        await harness.pilot.pause(0.3)
        assert app.screen.query_one("#settings-apply", Button).disabled
        assert not app_ui.apply_chat_defaults(app)
        app.screen.query_one("#settings-temperature", Input).value = "0.5"
        app.screen.query_one("#settings-save", Button).press()
        await harness.pilot.pause()
        assert read_config(path).config.temperature == 0.5
        assert app.config == before
        assert not harness.server.requests
    finally:
        if isinstance(guard, OperationKind):
            app.operations.release(guard)
        chat._read_only = False
        chat._pending_reconcile = None


@pytest.mark.parametrize("mode", ["attach", "managed"])
async def test_pending_runtime_changes_keep_active_ownership(
    tmp_path: Path, mode: str
) -> None:
    path = tmp_path / "config.toml"
    original = f'runtime_mode = "{mode}"\nhost = "127.0.0.1"\nport = 9081\n'
    path.write_text(original)
    app = MlxTuiApp(config_document=read_config(path))
    async with app.run_test() as pilot:
        active = app.config
        client = app._http
        manager = app.managed_runtime
        launch = app.launch_config
        await pilot.press("ctrl+g")
        screen = app.screen
        screen.query_one("#settings-runtime_mode", Select).value = (
            "managed" if mode == "attach" else "attach"
        )
        screen.query_one("#settings-port", Input).value = "9082"
        screen.query_one("#settings-save", Button).press()
        await pilot.pause()
        screen.query_one("#settings-save", Button).press()
        await pilot.pause()
        assert app_ui.runtime_restart_required(app)
        assert "Restart required" in str(
            screen.query_one("#settings-summary", Static).render()
        )
        assert app.config == active
        assert app._http is client
        assert app.managed_runtime is manager
        assert app.launch_config == launch
        await pilot.press("escape")
        app.log_app("a later unrelated event")
        notice = app.query_one("#config-notice", Static)
        assert notice.display
        assert "Restart required" in str(notice.render())

        # Commands also require restart; Reload never changes ownership.
        path.write_text(original + 'start_cmd = "custom server"\n')
        await pilot.press("ctrl+g")
        screen = app.screen
        screen.query_one("#settings-reload", Button).press()
        await pilot.pause()
        assert app_ui.runtime_restart_required(app)
        path.write_text(original)
        screen.query_one("#settings-reload", Button).press()
        await pilot.pause()
        assert not app_ui.runtime_restart_required(app)
        assert not notice.display
        assert "Restart required" not in str(
            screen.query_one("#settings-summary", Static).render()
        )
        assert app.config == active
        assert app._http is client
        assert app.managed_runtime is manager


async def test_cli_overrides_survive_config_reload(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('host = "saved.test"\nport = 9081\n')
    overrides = LaunchOverrides(host="127.0.0.1", port=8080, runtime_mode="managed")
    app = MlxTuiApp(config_document=read_config(path), launch_overrides=overrides)
    async with app.run_test() as pilot:
        client, manager = app._http, app.managed_runtime
        await pilot.press("ctrl+g")
        screen = app.screen
        path.write_text(
            'runtime_mode = "managed"\nhost = "changed.test"\nport = 9082\n'
        )
        screen.query_one("#settings-reload", Button).press()
        await pilot.pause()
        assert app.launch_overrides is overrides
        assert (app.host, app.port, app.config.runtime_mode) == (
            "127.0.0.1",
            8080,
            "managed",
        )
        assert app._http is client
        assert app.managed_runtime is manager
        assert not app_ui.runtime_restart_required(app)
        summary = str(screen.query_one("#settings-summary", Static).render())
        assert "Active: managed 127.0.0.1:8080" in summary
        assert "Saved: managed 127.0.0.1:8080" in summary
        assert "--managed, --host 127.0.0.1, --port 8080" in summary
        screen.query_one("#settings-runtime_mode", Select).value = "attach"
        screen.query_one("#settings-save", Button).press()
        await pilot.pause()
        assert not app_ui.runtime_restart_required(app)
        assert read_config(path).config == AppConfig(host="changed.test", port=9082)
        assert b"127.0.0.1" not in path.read_bytes()
        assert b"8080" not in path.read_bytes()


async def test_settings_repairs_invalid_select_fields(
    harness: AppHarness, tmp_path: Path
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('runtime_mode = "typo"\nenable_thinking = "yes"\n')
    harness.app.config_document = read_config(path)
    await harness.pilot.press("ctrl+g")
    screen = harness.app.screen
    screen.query_one("#settings-save", Button).press()
    await harness.pilot.pause()
    assert screen.query_one("#settings-runtime_mode", Select).has_focus
    screen.query_one("#settings-runtime_mode", Select).value = "attach"
    screen.query_one("#settings-enable_thinking", Select).value = "default"
    screen.query_one("#settings-save", Button).press()
    await harness.pilot.pause()
    assert not any(i.severity == "error" for i in read_config(path).issues)


async def test_missing_toml_creation_requires_confirmation(
    harness: AppHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import nullcontext  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415

    path = tmp_path / "missing" / "config.toml"
    harness.app.config_document = read_config(path)
    monkeypatch.setattr(harness.app, "suspend", nullcontext)
    calls: list[list[str]] = []

    def editor(argv: list[str], *, check: bool) -> SimpleNamespace:
        assert not check
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("mlx_tui.app.ui.subprocess.run", editor)
    await harness.pilot.press("ctrl+g")
    assert not path.exists()
    screen = harness.app.screen
    screen.query_one("#settings-open", Button).press()
    await harness.pilot.pause()
    assert isinstance(harness.app.screen, ConfirmScreen)
    await harness.pilot.press("n")
    assert not path.exists()
    screen.query_one("#settings-open", Button).press()
    await harness.pilot.pause()
    await harness.pilot.press("y")
    assert path.exists()
    assert "# mlx-tui config" in path.read_text()
    assert calls and calls[0][-1] == str(path)
