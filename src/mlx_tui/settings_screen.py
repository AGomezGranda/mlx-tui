"""Saved configuration form, shared by onboarding and later edits."""

from __future__ import annotations

import ipaddress
import shlex
import tomllib
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, cast, override

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static, TextArea

import mlx_tui.app.ui as app_ui
from mlx_tui.config import (
    DEFAULT_MAX_CTX,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_P,
    AppConfig,
    ConfigIssue,
    ConfigParseError,
    config_path,
    resolve_launch,
    validate_config,
)
from mlx_tui.config_io import ConfigDocument, ConfigWriteError, read_config, save_config
from mlx_tui.confirm import ConfirmScreen

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp

_INPUTS = {
    "host": ("Attach host", "127.0.0.1"),
    "port": ("Attach port", "8080"),
    "model": ("Default model identifier or path", "Unset: select a model in Models"),
    "temperature": ("Temperature · 0–2", str(DEFAULT_TEMPERATURE)),
    "top_p": ("Top-p · 0–1", str(DEFAULT_TOP_P)),
    "max_tokens": ("Maximum output tokens · 1–16384", str(DEFAULT_MAX_TOKENS)),
    "max_ctx": ("Context budget · 1024–131072", str(DEFAULT_MAX_CTX)),
    "seed": ("Seed", "Unset: provider default"),
}
_INTEGERS = {"port", "max_tokens", "max_ctx", "seed"}


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class SettingsScreen(ModalScreen[ConfigDocument | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    SettingsScreen { align: center middle; }
    #settings-box {
        width: 92%; max-width: 100; height: 92%;
        border: round $primary; background: $surface;
    }
    #settings-scroll { height: 1fr; padding: 0 2; }
    #settings-box Static, #settings-box Label { height: auto; }
    #settings-box .heading { text-style: bold; margin-top: 1; }
    #settings-box .field-error { color: $error; }
    #settings-system { height: 5; }
    #settings-box Horizontal { height: auto; }
    #settings-box Button { min-width: 10; width: 1fr; }
    #settings-actions { height: auto; padding: 0 1; }
    #settings-status { padding: 0 2; max-height: 4; overflow-y: auto; }
    """

    def __init__(
        self,
        document: ConfigDocument | None,
        *,
        error: str | None = None,
        first_run: bool = False,
    ) -> None:
        super().__init__()
        self.document = document
        self.error = error
        self.first_run = first_run
        self.path = document.path if document is not None else config_path()
        self._initial: dict[str, str | bool] = {}

    @property
    def tui(self) -> MlxTuiApp:
        return cast("MlxTuiApp", self.app)

    @override
    def compose(self) -> ComposeResult:
        with Vertical(id="settings-box"):
            with VerticalScroll(id="settings-scroll"):
                yield Static("Settings · saved defaults", classes="heading")
                yield Static(str(self.path), markup=False)
                yield Static("", id="settings-summary", markup=False)
                yield Static("Server", classes="heading")
                yield Label(
                    "Runtime mode"
                    if self.first_run
                    else "Runtime mode (applies at next launch)"
                )
                yield Select(
                    [("Attach", "attach"), ("Managed", "managed")],
                    value="attach",
                    allow_blank=False,
                    id="settings-runtime_mode",
                )
                yield Static(
                    "", id="settings-error-runtime_mode", classes="field-error"
                )
                for key in ("host", "port"):
                    yield from self._input(key)
                yield Checkbox("Enable local server start", id="settings-local-start")
                yield Static("", id="settings-command", markup=False)
                yield Static("", id="settings-endpoint", markup=False)
                yield Static("Chat defaults", classes="heading")
                yield Static("Blank optional fields are unset; hints show fallbacks.")
                yield from self._input("model")
                yield Label("System prompt · blank: no system prompt")
                yield TextArea(id="settings-system")
                yield Static("", id="settings-error-system", classes="field-error")
                for key in ("temperature", "top_p", "max_tokens", "max_ctx", "seed"):
                    yield from self._input(key)
                yield Label("Thinking")
                yield Select(
                    [
                        ("Default (provider default)", "default"),
                        ("On", "on"),
                        ("Off", "off"),
                    ],
                    value="default",
                    allow_blank=False,
                    id="settings-enable_thinking",
                )
                yield Static(
                    "", id="settings-error-enable_thinking", classes="field-error"
                )
                yield Static("Advanced", classes="heading")
                yield Static("", id="settings-presets", markup=False)
                yield Static(
                    "Commands, shell execution, swap policy and presets: edit TOML. "
                    "Editor: $VISUAL, then $EDITOR, then vi. GUI editors must wait "
                    "for closing (for example code --wait)."
                )
                with Horizontal():
                    yield Button("Open TOML", id="settings-open")
                    yield Button("Reload", id="settings-reload")
                    yield Button("Runtime setup", id="settings-runtime")
            yield Static("", id="settings-status", markup=False)
            with Horizontal(id="settings-actions"):
                yield Button("Save", variant="primary", id="settings-save")
                yield Button(
                    "Continue defaults" if self.first_run else "Cancel",
                    id="settings-cancel",
                )
                yield Button("Apply chat defaults", id="settings-apply")

    def _input(self, key: str) -> ComposeResult:
        label, fallback = _INPUTS[key]
        yield Label(label)
        yield Input(placeholder=fallback, id=f"settings-{key}")
        yield Static("", id=f"settings-error-{key}", classes="field-error")

    def on_mount(self) -> None:
        self._populate()
        self.query_one("#settings-runtime_mode", Select).focus()
        self.set_interval(0.25, self._refresh_actions)

    def _populate(self) -> None:
        data: dict[str, object] = (
            tomllib.loads(self.document.source.decode("utf-8"))
            if self.document is not None and self.document.source is not None
            else {}
        )
        for key in _INPUTS:
            value = (
                data.get("max_context", data.get(key))
                if key == "max_ctx"
                else data.get(key)
            )
            if value is None and key in ("host", "port"):
                value = getattr(AppConfig(), key)
            self.query_one(f"#settings-{key}", Input).value = (
                "" if value is None else str(value)
            )
        self.query_one("#settings-system", TextArea).load_text(
            str(data.get("system", ""))
        )
        for key, options, saved_value in (
            (
                "runtime_mode",
                [("Attach", "attach"), ("Managed", "managed")],
                data.get("runtime_mode", "attach"),
            ),
            (
                "enable_thinking",
                [
                    ("Default (provider default)", "default"),
                    ("On", "on"),
                    ("Off", "off"),
                ],
                "on"
                if data.get("enable_thinking") is True
                else "off"
                if data.get("enable_thinking") is False
                else "default",
            ),
        ):
            if self.document and any(
                i.key == key and i.severity == "error" for i in self.document.issues
            ):
                options.append((f"Invalid saved value: {data.get(key)}", "invalid"))
                selected = "invalid"
            else:
                selected = str(saved_value)
            select = self.query_one(f"#settings-{key}", Select)
            select.set_options(options)
            select.value = selected
        custom = (
            self.document is not None and self.document.config.start_cmd is not None
        )
        self.query_one("#settings-local-start", Checkbox).value = (
            self.first_run
            and self.document is not None
            and self.document.source is None
            and not custom
            and self.error is None
        )
        self._initial = self._values()
        self._show_issues(self.document.issues if self.document else ())
        self._status(self.error or "")
        self._refresh_preview()
        self._refresh_actions()

    def _values(self) -> dict[str, str | bool]:
        return {
            **{key: self.query_one(f"#settings-{key}", Input).value for key in _INPUTS},
            "system": self.query_one("#settings-system", TextArea).text,
            "runtime_mode": str(self.query_one("#settings-runtime_mode", Select).value),
            "enable_thinking": str(
                self.query_one("#settings-enable_thinking", Select).value
            ),
            "local_start": self.query_one("#settings-local-start", Checkbox).value,
        }

    @property
    def dirty(self) -> bool:
        return self._values() != self._initial

    def _status(self, message: str) -> None:
        self.query_one("#settings-status", Static).update(message)

    def _show_issues(self, issues: tuple[ConfigIssue, ...]) -> None:
        for field in self.query(".field-error").results(Static):
            field.update("")
            field.display = False
        for issue in issues:
            key = "max_ctx" if issue.key == "max_context" else issue.key
            if issue.severity == "error" and key in (
                *_INPUTS,
                "system",
                "runtime_mode",
                "enable_thinking",
            ):
                field = self.query_one(f"#settings-error-{key}", Static)
                field.update(issue.message)
                field.display = True
        self.query_one("#settings-presets", Static).update(
            f"{len(self.document.presets) if self.document else 0} presets · "
            + (
                "Legacy presets: import on Save"
                if self.document and self.document.legacy_source is not None
                else str(self.path)
            )
            + "\n"
            + "\n".join(f"{i.key}: {i.message}" for i in issues)
        )

    @on(Input.Changed)
    @on(Select.Changed)
    def _field_changed(self) -> None:
        if self._initial:
            self._refresh_preview()

    def _refresh_preview(self) -> None:
        values = self._values()
        managed = values["runtime_mode"] == "managed"
        for key in ("host", "port"):
            self.query_one(f"#settings-{key}", Input).disabled = managed
        custom = (
            self.document is not None and self.document.config.start_cmd is not None
        )
        self.query_one("#settings-local-start", Checkbox).disabled = (
            managed or custom or not _loopback(str(values["host"]))
        )
        self.query_one("#settings-command", Static).update(
            f"Advanced start command: {self.document.config.start_cmd}\n"
            "Endpoint edits preserve this command; its arguments must match."
            if custom and self.document
            else "Local start is available only for literal loopback hosts."
        )
        try:
            preview = resolve_launch(
                replace(
                    AppConfig(),
                    host=str(values["host"]),
                    port=int(str(values["port"])),
                    runtime_mode="managed" if managed else "attach",
                ),
                self.tui.launch_overrides,
            )
            self.query_one("#settings-endpoint", Static).update(
                f"Form preview with CLI overrides: {preview.runtime_mode} {preview.host}:{preview.port}"
            )
        except ValueError:
            self.query_one("#settings-endpoint", Static).update(
                "Enter a valid port to preview the endpoint."
            )
        overrides = self.tui.launch_overrides
        flags: list[str] = []
        if overrides.runtime_mode is not None:
            flags.append(f"--{overrides.runtime_mode}")
        if overrides.host is not None:
            flags.append(f"--host {overrides.host}")
        if overrides.port is not None:
            flags.append(f"--port {overrides.port}")
        active = (
            f"Active: {self.tui.config.runtime_mode} {self.tui.host}:{self.tui.port}"
            if self.tui.runtime_services_started
            else "Save to connect, or continue with defaults for this run."
        )
        saved = (
            resolve_launch(self.document.config, overrides)
            if self.document is not None
            else None
        )
        self.query_one("#settings-summary", Static).update(
            f"{active}\n"
            + (
                f"Saved: {saved.runtime_mode} {saved.host}:{saved.port} (with CLI overrides)\n"
                if saved is not None
                else "Saved: repair the file to resolve settings\n"
            )
            + f"CLI overrides: {', '.join(flags) or 'none'}\n"
            + ("Restart required" if app_ui.runtime_restart_required(self.tui) else "")
        )

    def _refresh_actions(self) -> None:
        self.query_one("#settings-save", Button).disabled = self.document is None
        self.query_one("#settings-apply", Button).disabled = (
            self.document is None or not app_ui.can_apply_chat_defaults(self.tui)
        )
        self.query_one(
            "#settings-runtime", Button
        ).disabled = not self.tui.runtime_services_started

    def _changes(self) -> dict[str, str | int | float | bool | None]:  # noqa: PLR0912, PLR0915
        changes: dict[str, str | int | float | bool | None] = {}
        issues: list[ConfigIssue] = []
        for key, raw in self._values().items():
            if key == "local_start" or raw == self._initial.get(key):
                continue
            value = str(raw)
            if key == "enable_thinking":
                changes[key] = None if value == "default" else value == "on"
            elif not value and key not in ("host", "port", "runtime_mode"):
                changes[key] = None
            elif key in _INTEGERS or key in ("temperature", "top_p"):
                try:
                    changes[key] = int(value) if key in _INTEGERS else float(value)
                except ValueError:
                    issues.append(
                        ConfigIssue(
                            key,
                            "Enter an integer"
                            if key in _INTEGERS
                            else "Enter a number",
                            "error",
                        )
                    )
            else:
                changes[key] = value
        data: dict[str, object] = (
            tomllib.loads(self.document.source.decode("utf-8"))
            if self.document and self.document.source
            else {}
        )
        if "max_ctx" in changes:
            data.pop("max_context", None)
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        issues.extend(validate_config(data))
        self._show_issues(tuple(issues))
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            key = "max_ctx" if errors[0].key == "max_context" else errors[0].key
            if key in (*_INPUTS, "system", "runtime_mode", "enable_thinking"):
                self.query_one(f"#settings-{key}").focus()
            raise ConfigWriteError("; ".join(f"{i.key}: {i.message}" for i in errors))
        checkbox = self.query_one("#settings-local-start", Checkbox)
        if checkbox.value and not checkbox.disabled:
            host = str(self._values()["host"])
            port = str(self._values()["port"])
            changes["start_cmd"] = shlex.join(
                ["mlx_lm.server", "--host", host, "--port", port]
            )
            changes["command_shell"] = False
        return changes

    @on(Button.Pressed, "#settings-save")
    def _save(self) -> None:
        if self.document is None:
            return
        try:
            document = save_config(self.document, self._changes())
        except (ConfigWriteError, ConfigParseError) as exc:
            self._status(str(exc))
            return
        self.document = document
        app_ui.update_config_document(self.tui, document)
        if not self.tui.runtime_services_started:
            self.dismiss(document)
            return
        self._populate()
        self._status("Saved; apply chat defaults here or restart to use them.")

    def _discard_then(self, action: Callable[[], object]) -> None:
        if not self.dirty:
            action()
            return

        def confirmed(result: bool | None) -> None:
            if result:
                action()

        confirm = ConfirmScreen(
            "Discard Settings edits? (yes = discard; no = keep editing)",
            confirm_label="Discard",
        )
        self.app.push_screen(confirm, confirmed)

    @on(Button.Pressed, "#settings-cancel")
    def action_cancel(self) -> None:
        self._discard_then(lambda: self.dismiss(None))

    def _reload(self) -> None:
        try:
            document = read_config(self.path)
        except ConfigParseError as exc:
            self.document = None
            self.error = str(exc)
        else:
            self.document = document
            self.error = None
            app_ui.update_config_document(self.tui, document)
        self._populate()

    @on(Button.Pressed, "#settings-reload")
    def _reload_pressed(self) -> None:
        self._discard_then(self._reload)

    @on(Button.Pressed, "#settings-open")
    def _open_pressed(self) -> None:
        self._discard_then(self._open_toml)

    def _open_toml(self) -> None:
        if not self.path.exists():

            def create(result: bool | None) -> None:
                if result:
                    self._edit(create=True)

            self.app.push_screen(
                ConfirmScreen(
                    "Create the standard config template and open TOML?",
                    confirm_label="Create",
                ),
                create,
            )
        else:
            self._edit()

    def _edit(self, *, create: bool = False) -> None:
        document = app_ui.action_edit_config(self.tui, path=self.path, create=create)
        if document is not None:
            self.document = document
            self.error = None
            self._populate()
        else:
            self._status(
                "Editor/reload failed; repair the file and Reload. See Activity for details."
            )

    @on(Button.Pressed, "#settings-apply")
    def _apply(self) -> None:
        if app_ui.apply_chat_defaults(self.tui):
            self._status("Saved chat defaults applied to subsequent requests.")

    @on(Button.Pressed, "#settings-runtime")
    def _runtime(self) -> None:
        def navigate() -> None:
            self.dismiss(None)
            self.tui.call_after_refresh(self.tui.action_runtime_setup)

        self._discard_then(navigate)
