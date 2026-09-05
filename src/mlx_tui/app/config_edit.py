"""Config edit suspend + reload."""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import TYPE_CHECKING

from textual.css.query import NoMatches

from mlx_tui.chat_pane import ChatPane
from mlx_tui.config import ConfigParseError, config_path, parse_config, write_template
from mlx_tui.history.tokens import ContextLimitError, prepare_context
from mlx_tui.presets import load_presets

if TYPE_CHECKING:
    from mlx_tui.app import MlxTuiApp


def edit_config(app: MlxTuiApp) -> None:
    path = config_path()
    if not path.exists():
        write_template(path)
    before = (app.config.host, app.config.port)
    editor = os.environ.get("EDITOR", "vi")
    with app.suspend():
        subprocess.run([*shlex.split(editor), str(path)], check=False)
    try:
        app.config = parse_config(path)
    except ConfigParseError as exc:
        # A typo must not silently wipe the session's commands/model;
        # keep the previous config instead of adopting all-defaults.
        app.log_app(f"config kept — parse failed: {exc}", "red")
        return
    app.log_app("config reloaded")
    try:
        pane = app.query_one(ChatPane)
        pane.apply_config_params(app.config)
        _refresh_context_bar(pane, app.config.max_ctx)
    except NoMatches:
        pass
    app.presets = load_presets()
    app.preset_idx = -1
    if (app.config.host, app.config.port) != before:
        app.log_app("restart mlx-tui to apply host/port", "yellow")


def _refresh_context_bar(pane: ChatPane, max_ctx: int) -> None:
    _, _, max_tokens = pane._parse_params()
    try:
        window = prepare_context(
            pane.messages, pane.tui.config.system, max_ctx, max_tokens
        )
    except ContextLimitError:
        pane.update_ctx_bar(max_ctx)
    else:
        pane.update_ctx_bar(window.reserved_tokens)
