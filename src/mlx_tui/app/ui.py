"""App activity/error/config/preset UI helpers (app-parameterized)."""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import replace
from typing import Any

from rich.text import Text
from textual.css.query import NoMatches
from textual.widgets import Collapsible, RichLog, TabbedContent

from mlx_tui.chat_ui.pane import ChatPane
from mlx_tui.compare.pane import ComparePane
from mlx_tui.config import (
    ConfigParseError,
    config_path,
    parse_config,
    write_template,
)
from mlx_tui.models_pane import ModelsPane
from mlx_tui.operations import OperationKind
from mlx_tui.params import ParamsPane
from mlx_tui.presets import Preset, load_presets


def _apply_preset(app: Any, preset: Preset) -> None:
    try:
        pane = app.query_one(ChatPane)
        params = pane.query_one(ParamsPane)
    except NoMatches:
        return
    params.apply_values(preset.temperature, preset.top_p, preset.max_tokens)
    # sync config snapshot
    temp, top_p, max_tok = params.read_values()
    app.config = replace(
        app.config,
        temperature=temp,
        top_p=top_p,
        max_tokens=max_tok,
        system=preset.system.strip() or None,
    )
    app.mark_active_profile_modified()
    params.apply_config(app.config)
    app.log_app(f"preset: {preset.name}", "dim")
    pane.mark_next_request_dirty()


def _cycle_preset(app: Any, step: int) -> None:
    if not app.presets:
        app.log_app("no presets — create ~/.config/mlx-tui/presets.toml", "yellow")
        return
    app.preset_idx = (app.preset_idx + step) % len(app.presets)
    app._apply_preset(app.presets[app.preset_idx])


def action_cycle_preset(app: Any) -> None:
    app._cycle_preset(1)


def action_cycle_preset_back(app: Any) -> None:
    app._cycle_preset(-1)


def _on_tab_activated(app: Any, event: TabbedContent.TabActivated) -> None:
    pane = app._chat_pane_or_none()
    if pane is not None:
        pane._follow_from = None
        pane._restore_composer_focus = False
    if event.tabbed_content.active == "models":
        app.query_one(ModelsPane).rescan()
    elif event.tabbed_content.active == "metrics":
        app._refresh_metrics()


def action_toggle_activity(app: Any) -> None:
    activity = app.query_one("#activity", Collapsible)
    activity.collapsed = not activity.collapsed


def _focusable(widget: Any) -> bool:
    return bool(
        widget is not None
        and widget.is_mounted
        and widget.visible
        and widget.display
        and widget.can_focus
        and not getattr(widget, "disabled", False)
    )


def _focus_chat_after_layout(app: Any, previous_focus: Any) -> None:
    if _focusable(previous_focus):
        previous_focus.focus()
        return
    for selector in ("#chat-input", "#chat-transcript"):
        try:
            widget = app.query_one(selector)
        except NoMatches:
            continue
        if _focusable(widget):
            widget.focus()
            return


def _restore_zen_view_after_layout(app: Any, previous_tab: str, focus: Any) -> None:
    if _focusable(focus):
        focus.focus()
    elif previous_tab == "chat":
        _focus_chat_after_layout(app, None)


def action_toggle_zen(app: Any) -> None:
    """Toggle the chat presentation while keeping its widgets mounted."""
    if not app.is_mounted or len(app.screen_stack) != 1:
        return
    tabs = app.query_one(TabbedContent)
    base_screen = app.screen_stack[0]
    if app.zen_mode:
        previous_tab = app._zen_previous_tab or "chat"
        previous_focus = app._zen_previous_focus
        app.zen_mode = False
        base_screen.set_class(False, "zen")
        tabs.active = previous_tab
        app._zen_previous_tab = None
        app._zen_previous_focus = None
        chat = app._chat_pane_or_none()
        if chat is not None:
            chat._refresh_composer_hint()
            chat.refresh_zen_info()
        app.call_after_refresh(
            lambda: _restore_zen_view_after_layout(app, previous_tab, previous_focus)
        )
        return

    app._zen_previous_tab = tabs.active
    app._zen_previous_focus = app.focused
    chat_focus = app.focused if tabs.active == "chat" else None
    app.zen_mode = True
    base_screen.set_class(True, "zen")
    tabs.active = "chat"
    chat = app._chat_pane_or_none()
    if chat is not None:
        chat._refresh_composer_hint()
        chat.refresh_zen_info()
    app.call_after_refresh(lambda: _focus_chat_after_layout(app, chat_focus))


def _activity_toggled(app: Any, event: Collapsible.Toggled) -> None:
    activity = event.collapsible
    if not activity.collapsed:
        app._unseen_notice = None
    elif app.focused is activity.query_one(RichLog):
        activity.query_one("CollapsibleTitle").focus()
    activity.query_one(RichLog).can_focus = not activity.collapsed
    app.refresh_activity()


def refresh_activity(app: Any) -> None:
    """Present existing operation/log state without owning its lifetime."""
    if not app.is_mounted:
        return
    try:
        activity = app.query_one("#activity", Collapsible)
    except NoMatches:
        return
    kind = app.operations.current
    title = "Activity" if kind is OperationKind.IDLE else f"{kind.value} · Activity"
    event = app._unseen_notice or app._latest_event
    if event:
        title += " · " + " ".join(event.split())
    summary = Text(title)
    summary.truncate(max(1, app.size.width - 4), overflow="ellipsis")
    activity.title = summary.plain
    app.refresh_bindings()
    try:
        app.query_one(ComparePane)._update_controls()
    except NoMatches:
        pass


def log_app(app: Any, message: str, style: str | None = None) -> None:
    text = Text(message) if style is None else Text(message, style=style)
    app.query_one("#app-log", RichLog).write(text)
    app._latest_event = message
    activity = app.query_one("#activity", Collapsible)
    if (activity.collapsed or app.zen_mode) and style in ("red", "yellow"):
        label = "Last error" if style == "red" else "Last warning"
        app._unseen_notice = f"{label}: {message}"
    if app.zen_mode and style in ("red", "yellow"):
        app.notify(
            message,
            title="Last error" if style == "red" else "Last warning",
            severity="error" if style == "red" else "warning",
            markup=False,
        )
        pane = app._chat_pane_or_none()
        if pane is not None:
            pane.refresh_zen_info()
    app.refresh_activity()


def check_action(  # noqa: PLR0911
    app: Any, action: str, parameters: tuple[object, ...]
) -> bool | None:
    if action == "toggle_zen":
        return len(app.screen_stack) == 1
    if action == "toggle_activity" and app.zen_mode:
        return False
    if action == "cancel_chat":
        try:
            from mlx_tui.discover_pane import DiscoverPane  # noqa: PLC0415

            discover = app.query_one(DiscoverPane)
        except NoMatches:
            discover = None
        if discover is not None and (
            discover.visible or discover._downloading is not None
        ):
            return True
        if app.operations.current is OperationKind.COMPARING:
            try:
                pane = app.query_one(ComparePane)
            except NoMatches:
                return False
            return pane.has_live_comparison and not pane.cancel_requested
        pane = app._chat_pane_or_none()
        return (
            len(app.screen_stack) == 1
            and pane is not None
            and pane.has_live_turn
            and not pane._cancel_requested
        )
    return True


def log_error_once(app: Any, source: str, exc: Exception) -> None:
    """Log one bounded diagnostic per distinct failure for a source.

    Identical repeats stay silent until recovery; a changed error logs
    immediately. Callers clear the source via clear_error() on success so
    a recurrence becomes visible again.
    """
    key = f"{exc.__class__.__name__}: {exc}"
    if app._last_errors.get(source) == key:
        return
    app._last_errors[source] = key
    app.log_app(f"{source} failed: {key}"[:300], "red")


def clear_error(app: Any, source: str) -> None:
    """Forget a source's last failure after a successful fetch."""
    app._last_errors.pop(source, None)


def action_edit_config(app: Any) -> None:
    path = config_path()
    before = (app.config.host, app.config.port)
    editor = os.environ.get("EDITOR", "vi")
    try:
        editor_argv = shlex.split(editor)
    except ValueError as exc:
        app.log_app(f"config edit failed: {exc.__class__.__name__}: {exc}"[:200], "red")
        return
    if not editor_argv:
        app.log_app("config edit failed: EDITOR is empty", "red")
        return
    try:
        if not path.exists():
            write_template(path)
        with app.suspend():
            proc = subprocess.run([*editor_argv, str(path)], check=False)
    except OSError as exc:
        app.log_app(f"config edit failed: {exc.__class__.__name__}: {exc}"[:200], "red")
        return
    if proc.returncode != 0:
        app.log_app(f"config edit failed: editor exited {proc.returncode}", "red")
        return
    try:
        new_config = parse_config(path)
    except ConfigParseError as exc:
        # A typo must not silently wipe the session's commands/model;
        # keep the previous config instead of adopting all-defaults.
        app.log_app(f"config kept — parse failed: {exc}", "red")
        return
    app.config = new_config
    app.mark_active_profile_modified()
    if new_config.model is not None and new_config.model != app.effective_model():
        app.select_model(new_config.model)
    app.log_app("config reloaded")
    try:
        pane = app.query_one(ChatPane)
        pane.apply_config_params(app.config)
        pane.refresh_context_bar()
        pane.mark_next_request_dirty()
    except NoMatches:
        pass
    app.presets = load_presets()
    app.preset_idx = -1
    if (app.config.host, app.config.port) != before:
        app.log_app("restart mlx-tui to apply host/port", "yellow")
